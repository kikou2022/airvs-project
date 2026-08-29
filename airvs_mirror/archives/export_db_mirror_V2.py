#!/usr/bin/env python3
import json
import subprocess
import pymysql
import os
import re
import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from datetime import datetime

# La même configuration que ton worker_ubuntu.py
DB_CONFIG = {
    "host": "192.168.1.39",
    "port": 3306,
    "user": "sebastien",
    "password": "idylle@SL",
    "database": "radiodb",
    "charset": "utf8mb4"
}

# Configuration des destinations
DESTINATIONS = {
    "debian12": {
        "host": "sebastien@192.168.1.30", 
        "path": "/var/www/html/airvs_mirror/db.json"
    },
    "vps": {
        "host": "ubuntu@54.37.38.117",       
        "path": "/var/www/html/airvs_mirror/db.json"
    }
}

FICHIER_TEMPORAIRE = "/tmp/airvs_db_export.json"

# ======================================================================
# ########## NOUVEAU : CONFIGURATION NIVEAU 2 (AZURACAST) #############
# ======================================================================
AZURACAST_LOCAL_PATH = "/home/sebastien/music_azuracast6_7/"
MOT_CLE_AZURA = "IN_AZURACAST"
# ======================================================================

def normaliser_nom_fichier_azura(nom_fichier):
    """Transforme un nom de fichier sale en chaîne propre"""
    nom = os.path.splitext(nom_fichier)[0].lower()
    nom = re.sub(r'^[0-9]+[\s\-_]+', '', nom)
    nom = re.sub(r'[_\s]+-[_\s]+', ' - ', nom)
    nom = nom.replace('_', ' ')
    nom = re.sub(r'\s+', ' ', nom).strip()
    return nom

def lire_tags_audio_azura(chemin_complet):
    """Lit les tags ID3/Vorbis d'un fichier et renvoie 'artiste - titre' en minuscules"""
    try:
        # MP3
        if chemin_complet.lower().endswith('.mp3'):
            audio = EasyID3(chemin_complet)
            artist = str(audio.get('artist', [''])[0]).strip().lower()
            title = str(audio.get('title', [''])[0]).strip().lower()
        # FLAC
        elif chemin_complet.lower().endswith('.flac'):
            audio = FLAC(chemin_complet)
            artist = str(audio.get('artist', [''])[0]).strip().lower()
            title = str(audio.get('title', [''])[0]).strip().lower()
        else:
            return None
            
        if artist and title:
            return f"{artist} - {title}"
        return None
    except Exception:
        return None

def verifier_azuracast(db):
    print(f"[{datetime.now()}] [NIVEAU 2] Analyse du dossier AzuraCast local...")
    
    if not os.path.exists(AZURACAST_LOCAL_PATH):
        print(f"[{datetime.now()}] [NIVEAU 2] ERREUR : Dossier {AZURACAST_LOCAL_PATH} introuvable.")
        return

    # ---------------------------------------------------------
    # PASSE 1 : Comparaison par le NOM DU FICHIER (Ultra rapide)
    # ---------------------------------------------------------
    fichiers_azura_par_nom = set()
    fichiers_restants = [] # Ceux qui n'ont pas matché par le nom

    for dossier_racine, sous_dossiers, fichiers in os.walk(AZURACAST_LOCAL_PATH):
        for f in fichiers:
            if f.lower().endswith(('.mp3', '.flac', '.ogg', '.wav', '.m4a')):
                chemin_complet = os.path.join(dossier_racine, f)
                nom_propre = normaliser_nom_fichier_azura(f)
                fichiers_azura_par_nom.add(nom_propre)
                fichiers_restants.append(chemin_complet)
                
    print(f"[{datetime.now()}] [NIVEAU 2] Passe 1 : {len(fichiers_azura_par_nom)} fichiers analysés par nom.")

    # ---------------------------------------------------------
    # PASSE 2 : Comparaison par les TAGS METADATA (Pour les restants)
    # ---------------------------------------------------------
    fichiers_azura_par_tags = set()
    print(f"[{datetime.now()}] [NIVEAU 2] Passe 2 : Lecture des tags ID3 pour {len(fichiers_restants)} fichiers restants (cela peut prendre ~30 secondes)...")
    
    compteur = 0
    for chemin in fichiers_restants:
        compteur += 1
        if compteur % 500 == 0:
            print(f"[{datetime.now()}] [NIVEAU 2] ... Analyse des tags : {compteur}/{len(fichiers_restants)}")
            
        tags_propres = lire_tags_audio_azura(chemin)
        if tags_propres:
            fichiers_azura_par_tags.add(tags_propres)

    print(f"[{datetime.now()}] [NIVEAU 2] Passe 2 terminée. {len(fichiers_azura_par_tags)} correspondances trouvées via tags.")

    # Fusionner les deux passes (Set mathématique : l'union des deux ensembles)
    tout_azura = fichiers_azura_par_nom.union(fichiers_azura_par_tags)

    # ---------------------------------------------------------
    # COMPARAISON AVEC LA BASE RADIODJ
    # ---------------------------------------------------------
    cursor = db.cursor()
    cursor.execute("SELECT ID, artist, title, path, comments FROM songs WHERE song_type = 0")
    titres = cursor.fetchall()

    mises_a_jour = []
    titres_trouves = 0

    for t in titres:
        id_radio = t[0]
        artist = str(t[1] or "").strip().lower()
        title = str(t[2] or "").strip().lower()
        current_comment = str(t[4] or "")

        if not artist or not title: continue

        chaine_radiodj = re.sub(r'\s+', ' ', f"{artist} - {title}")

        # On vérifie si la chaîne RadioDJ est dans l'une des deux passes
        if chaine_radiodj in tout_azura:
            nouveau_comment = MOT_CLE_AZURA
            titres_trouves += 1
        else:
            nouveau_comment = ""

        if current_comment != nouveau_comment:
            mises_a_jour.append((nouveau_comment, id_radio))

    # Mise à jour SQL
    if mises_a_jour:
        cursor.executemany("UPDATE songs SET comments = %s WHERE ID = %s", mises_a_jour)
        db.commit()
        print(f"[{datetime.now()}] [NIVEAU 2] Base RadioDJ mise à jour : {len(mises_a_jour)} statuts modifiés.")
    else:
        print(f"[{datetime.now()}] [NIVEAU 2] Aucun changement dans la base RadioDJ.")
        
    print(f"[{datetime.now()}] [NIVEAU 2] Statistique : {titres_trouves} titres de RadioDJ sont présents dans AzuraCast.")
    cursor.close()


def extraire_donnees():
    print(f"[{datetime.now()}] Connexion à la base Windows...")
    try:
        db = pymysql.connect(**DB_CONFIG)
        
        # --- APPEL DE LA NOUVELLE FONCTION AVANT L'EXTRACTION ---
        verifier_azuracast(db)
        # --------------------------------------------------------

        cursor = db.cursor()
        # On ajoute 'comments' (index 6) pour l'afficher dans le Miroir !
        cursor.execute("SELECT ID, artist, title, year, path, date_added, comments FROM songs WHERE song_type = 0")
        titres = cursor.fetchall()
        cursor.close()
        db.close()

        # Conversion en liste de dictionnaires
        data = [
            {
                "id": t[0], 
                "artist": t[1], 
                "title": t[2], 
                "year": t[3], 
                "path": t[4],
                "date_added": str(t[5]) if t[5] else "",
                "comments": t[6] if t[6] else "" # NOUVEAU
            }
            for t in titres
        ]
        
        # Écriture du fichier JSON local
        with open(FICHIER_TEMPORAIRE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
            
        print(f"[{datetime.now()}] {len(data)} titres exportés dans {FICHIER_TEMPORAIRE}.")
        return True
        
    except Exception as e:
        print(f"[{datetime.now()}] ERREUR BDD : {e}")
        return False

def envoyer_vers_destination(nom, config):
    print(f"[{datetime.now()}] Envoi vers {nom} ({config['host']})...")
    try:
        cmd = ["scp", "-q", FICHIER_TEMPORAIRE, f"{config['host']}:{config['path']}"]
        subprocess.run(cmd, check=True, timeout=30)
        print(f"[{datetime.now()}] Succès vers {nom}.")
        return True
    except subprocess.TimeoutExpired:
        print(f"[{datetime.now()}] ÉCHEC vers {nom} : Timeout (Le serveur est-il allumé/accessible ?)")
        return False
    except Exception as e:
        print(f"[{datetime.now()}] ÉCHEC vers {nom} : {e}")
        return False

if __name__ == "__main__":
    print("--- DÉBUT DE LA SYNCHRONISATION MIROIR ---")
    
    if extraire_donnees():
        for nom, config in DESTINATIONS.items():
            envoyer_vers_destination(nom, config)
    
    print("--- FIN DE LA TÂCHE ---\n")