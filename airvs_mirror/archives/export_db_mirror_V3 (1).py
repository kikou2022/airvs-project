#!/usr/bin/env python3
import json
import subprocess
import pymysql
import requests
import re
import os
from datetime import datetime

# ==========================================
# CONFIGURATION BASE DE DONNÉES
# ==========================================
DB_CONFIG = {
    "host": "192.168.1.39",
    "port": 3306,
    "user": "sebastien",
    "password": "idylle@SL",
    "database": "radiodb",
    "charset": "utf8mb4"
}

# ==========================================
# CONFIGURATION DESTINATIONS MIROIRS
# ==========================================
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

# ==========================================
# CONFIGURATION NIVEAU 2 (FLUX TENDU AZURACAST)
# ==========================================
AZURACAST_LOCAL_PATH = "/home/sebastien/music_azuracast6_7/"
MOT_CLE_AZURA = "IN_AZURACAST" # Le mot-clé exact qui sera écrit dans RadioDJ

AZURA_API_URL = "https://azuracast.2026.airvs.fr/api/station/7/files"
AZURA_API_KEY = "5e256834da4da4cf:1cadd3936115612949a795cd5a48e12d"

# ==========================================
# FONCTIONS DE NETTOYAGE ET DE COMPARAISON
# ==========================================

def normaliser_nom_fichier_azura(nom_fichier):
    """Transforme un nom de fichier sale en chaîne propre pour comparer avec RadioDJ"""
    nom = os.path.splitext(nom_fichier)[0].lower()
    # Supprime les préfixes numériques (ex: 001__, 01 - )
    nom = re.sub(r'^[0-9]+[\s\-_]+', '', nom)
    # Uniformise les séparateurs (ex: _-_ ou - deviennent " - ")
    nom = re.sub(r'[_\s]+-[_\s]+', ' - ', nom)
    # Remplace les underscore restants par des espaces
    nom = nom.replace('_', ' ')
    # Supprime les espaces multiples
    nom = re.sub(r'\s+', ' ', nom).strip()
    return nom

def verifier_azuracast(db):
    """Compare la base RadioDJ avec AzuraCast (via Noms + API)"""
    print(f"[{datetime.now()}] [NIVEAU 2] Analyse du flux tendu AzuraCast...")
    
    # ---------------------------------------------------------
    # PASSE 1 : Comparaison par le NOM DU FICHIER (Local, ultra rapide)
    # ---------------------------------------------------------
    fichiers_azura_par_nom = set()
    if os.path.exists(AZURACAST_LOCAL_PATH):
        for dossier_racine, sous_dossiers, fichiers in os.walk(AZURACAST_LOCAL_PATH):
            for f in fichiers:
                if f.lower().endswith(('.mp3', '.flac', '.ogg', '.wav', '.m4a')):
                    fichiers_azura_par_nom.add(normaliser_nom_fichier_azura(f))
                    
    print(f"[{datetime.now()}] [NIVEAU 2] Passe 1 (Noms locaux) : {len(fichiers_azura_par_nom)} fichiers analysés.")

    # ---------------------------------------------------------
    # PASSE 2 : Comparaison via l'API AZURACAST (Réseau, fulgurant)
    # ---------------------------------------------------------
    fichiers_azura_par_api = set()
    print(f"[{datetime.now()}] [NIVEAU 2] Passe 2 (API) : Interrogation de l'API en cours...")
    
    try:
        headers = {"Authorization": f"Bearer {AZURA_API_KEY}"}
        page = 1
        has_more = True
        
        while has_more:
            # On paginate par blocs de 100 (standard et sûr pour le serveur)
            params = {"limit": 100, "page": page} 
            reponse = requests.get(AZURA_API_URL, headers=headers, params=params, timeout=60)
            
            if reponse.status_code == 200:
                    data = reponse.json()
                    
                    # SÉCURITÉ : L'API peut renvoyer une liste directe ou un dictionnaire avec "rows"
                    if isinstance(data, list):
                        rows = data
                    else:
                        rows = data.get("rows", [])
                    
                    if not rows:
                        has_more = False # Plus de résultats, on arrête la boucle
                    else:
                        for morceau in rows:
                            artist = str(morceau.get("artist", "") or "").strip().lower()
                            title = str(morceau.get("title", "") or "").strip().lower()
                            
                            if artist and title:
                                chaine_api = re.sub(r'\s+', ' ', f"{artist} - {title}")
                                fichiers_azura_par_api.add(chaine_api)
                                
                        # Gestion de la pagination
                        if isinstance(data, dict) and data.get("has_more", False):
                            page += 1
                        else:
                            has_more = False # Si c'était une liste simple, on a tout eu d'un coup
            else:
                print(f"[{datetime.now()}] [NIVEAU 2] Passe 2 (API) : Erreur HTTP {reponse.status_code}. Abandon de l'API.")
                has_more = False
                
        print(f"[{datetime.now()}] [NIVEAU 2] Passe 2 (API) : {len(fichiers_azura_par_api)} correspondances trouvées via API.")
            
    except requests.exceptions.RequestException as e:
        print(f"[{datetime.now()}] [NIVEAU 2] Passe 2 (API) : Impossible de joindre l'API ({e}). On ignore cette passe.")

    # ---------------------------------------------------------
    # FUSION ET MISE À JOUR SQL
    # ---------------------------------------------------------
    # On rassemble les résultats des deux passes (mathématiques ensemblistes)
    tout_azura = fichiers_azura_par_nom.union(fichiers_azura_par_api)

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

        # Le titre est-il dans le Set fusionné ?
        if chaine_radiodj in tout_azura:
            nouveau_comment = MOT_CLE_AZURA
            titres_trouves += 1
        else:
            nouveau_comment = "" # On vide le champ s'il a été retiré d'AzuraCast

        if current_comment != nouveau_comment:
            mises_a_jour.append((nouveau_comment, id_radio))

    if mises_a_jour:
        cursor.executemany("UPDATE songs SET comments = %s WHERE ID = %s", mises_a_jour)
        db.commit()
        print(f"[{datetime.now()}] [NIVEAU 2] Base RadioDJ mise à jour : {len(mises_a_jour)} statuts modifiés.")
    else:
        print(f"[{datetime.now()}] [NIVEAU 2] Aucun changement dans la base RadioDJ.")
        
    print(f"[{datetime.now()}] [NIVEAU 2] Statistique : {titres_trouves} titres de RadioDJ sont présents dans AzuraCast.")
    cursor.close()


# ==========================================
# FONCTIONS D'EXTRACTION ET DE TRANSFERT
# ==========================================

def extraire_donnees():
    print(f"[{datetime.now()}] Connexion à la base Windows...")
    try:
        db = pymysql.connect(**DB_CONFIG)
        
        # On lance la vérification AzuraCast AVANT d'exporter
        verifier_azuracast(db)
        
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
                "comments": t[6] if t[6] else ""
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

# ==========================================
# EXECUTION PRINCIPALE
# ==========================================
if __name__ == "__main__":
    print("--- DÉBUT DE LA SYNCHRONISATION MIROIR (V3 - API) ---")
    
    if extraire_donnees():
        for nom, config in DESTINATIONS.items():
            envoyer_vers_destination(nom, config)
    
    print("--- FIN DE LA TÂCHE ---\n")