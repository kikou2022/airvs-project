#!/usr/bin/env python3
import json
import subprocess
import pymysql
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

def extraire_donnees():
    print(f"[{datetime.now()}] Connexion à la base Windows...")
    try:
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor()
        
        # --- MODIFICATION ICI : Ajout de date_added ---
        cursor.execute("SELECT ID, artist, title, year, path, date_added FROM songs WHERE song_type = 0")
        titres = cursor.fetchall()
        cursor.close()
        db.close()

        # --- MODIFICATION ICI : Conversion avec gestion du format date ---
        data = [
            {
                "id": t[0], 
                "artist": t[1], 
                "title": t[2], 
                "year": t[3], 
                "path": t[4],
                # On convertit la date en chaîne de caractères (string) pour le JSON
                # Si la date est vide (None), on met une chaîne vide
                "date_added": str(t[5]) if t[5] else "" 
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