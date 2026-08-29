import subprocess
import socket
import json
import os
import time
import sys
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv
from datetime import date

load_dotenv()

# ─── Configuration Originale Radio ────────────────────────────────────────
ICECAST_URL = "https://icecast-vps-ovh.airvs.fr/stream11"
ALSA_DEVICE = "usb_card"
SOCKET_PATH = "/tmp/mpv_radio_socket"

# ─── Configuration Sortie pour RadioDJ ─────────────────────────────────
DOSSIER_RESEAU = "/home/sebastien/radio_bridge"
# Fichier lu par RadioDJ (Vidé chaque jour)
PLAYLIST_ACTIVE_PATH = os.path.join(DOSSIER_RESEAU, "playlist_live_azuracast.m3u")

# ─── Configuration Base de données ─────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "192.168.1.39"),
    "port":     3306,
    "user":     os.getenv("DB_USER", "sebastien"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME", "radiodb"),
    "charset":  "utf8mb4",
    "collation": "utf8mb4_general_ci", 
    "use_pure": True,  # <-- LE BOUCLIER FINAL : Force le respect des ordres
    "connection_timeout": 5 
}

# ─── Variables globales ────────────────────────────────────────────────
db_connection = None
ARCHIVE_PATH = None # Sera défini au démarrage

# ─── Logique SQL Résiliante ───────────────────────────────────────────
def ensure_db_connected():
    """Vérifie si la connexion est active, sinon tente de se connecter."""
    global db_connection
    
    # Si on a une connexion, on vérifie qu'elle n'est pas tombée
    if db_connection is not None:
        try:
            db_connection.ping(reconnect=True, attempts=1)
            return True
        except Error:
            db_connection = None # La connexion est morte, on la réinitialise

    # Tentative de nouvelle connexion
    try:
        db_connection = mysql.connector.connect(**DB_CONFIG)
        print("[DB] Connexion à la base SQL établie.", flush=True)
        return True
    except Error as e:  # <-- ATTENTION AU "as e" ICI
        print(f"[DB] IMPOSSIBLE DE SE CONNECTER. Raison exacte : {e}", flush=True)
        return False
    
def get_windows_path_from_db(title: str):
    global db_connection  # <--- AJOUT TRÈS IMPORTANT ICI
    if not title: return None
    if not ensure_db_connected(): return None
    
    parts = title.split(" - ", 1)
    artist = parts[0].strip() if len(parts) > 0 else ""
    song = parts[1].strip() if len(parts) > 1 else ""

    try:
        cursor = db_connection.cursor()
        query = "SELECT path FROM songs WHERE artist LIKE %s AND title LIKE %s LIMIT 1"
        cursor.execute(query, (f"%{artist}%", f"%{song}%"))
        result = cursor.fetchone()
        cursor.close()
        return result[0] if result else None
    except Error as e:
        print(f"[DB] Erreur lors de la requête, réinitialisation... ({e})", flush=True)
        db_connection = None
        return None
    
# ─── NOUVEAU : Logique de gestion des fichiers (Actif + Archive) ──────
def manage_daily_playlist():
    global ARCHIVE_PATH
    os.makedirs(DOSSIER_RESEAU, exist_ok=True)
    
    # 1. Créer le fichier d'archive du jour (s'il n'existe pas)
    today_str = date.today().strftime("%Y-%m-%d")
    ARCHIVE_PATH = os.path.join(DOSSIER_RESEAU, f"archive_{today_str}.m3u")
    
    if not os.path.exists(ARCHIVE_PATH):
        try:
            with open(ARCHIVE_PATH, 'w', encoding='utf-8') as f:
                f.write("#EXTM3U\n")
            print(f"[HISTORIQUE] Nouvelle archive créée : archive_{today_str}.m3u", flush=True)
        except IOError as e:
            print(f"[ERREUR] Création archive : {e}", flush=True)

    # 2. Gérer le fichier actif pour RadioDJ (qu'on vide chaque jour)
    reset_active = False
    if not os.path.exists(PLAYLIST_ACTIVE_PATH):
        reset_active = True
    else:
        mod_time = os.path.getmtime(PLAYLIST_ACTIVE_PATH)
        if date.fromtimestamp(mod_time) != date.today():
            reset_active = True

    if reset_active:
        try:
            with open(PLAYLIST_ACTIVE_PATH, 'w', encoding='utf-8') as f:
                f.write("#EXTM3U\n")
            print("[PLAYLIST] Fichier actif quotidien réinitialisé pour RadioDJ.", flush=True)
        except IOError as e:
            print(f"[ERREUR] Réinitialisation playlist active : {e}", flush=True)

def add_to_playlist(windows_path: str):
    if not windows_path or not ARCHIVE_PATH: return
    
    # On vérifie le doublon uniquement sur le fichier actif (le plus récent)
    last_line = ""
    if os.path.exists(PLAYLIST_ACTIVE_PATH):
        with open(PLAYLIST_ACTIVE_PATH, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            for line in reversed(lines):
                if line.strip() and not line.strip().startswith("#"): 
                    last_line = line.strip()
                    break
            if last_line == windows_path: 
                return # Déjà présent, on ignore

    try:
        # ÉCRITURE 1 : Dans le fichier actif (pour RadioDJ)
        with open(PLAYLIST_ACTIVE_PATH, 'a', encoding='utf-8') as f:
            f.write(windows_path + '\n')
            
        # ÉCRITURE 2 : Dans l'archive du jour (pour l'historique)
        with open(ARCHIVE_PATH, 'a', encoding='utf-8') as f:
            f.write(windows_path + '\n')
            
        print(f"  -> [RADIODJ] Ajouté : {os.path.basename(windows_path)}", flush=True)
    except IOError as e:
        print(f"  -> [ERREUR] Écriture disque : {e}", flush=True)

# ─── Boucle Principale ────────────────────────────────────────────────
def main():
    manage_daily_playlist()

    if os.path.exists(SOCKET_PATH):
        try:
            os.remove(SOCKET_PATH)
        except OSError:
            time.sleep(1)
            os.remove(SOCKET_PATH)

    cmd = [
        "mpv",
        "--no-video",
        "--really-quiet",
        "--ao=alsa",
        f"--audio-device=alsa/{ALSA_DEVICE}",
        f"--input-ipc-server={SOCKET_PATH}",
        ICECAST_URL
    ]
    
    process = subprocess.Popen(cmd)

    socket_ready = False
    for i in range(15):
        if process.poll() is not None:
            print("Erreur : mpv s'est arrêté au démarrage.", flush=True)
            sys.exit(1)
        if os.path.exists(SOCKET_PATH):
            socket_ready = True
            break
        time.sleep(1)

    if not socket_ready:
        print("Info : Détection des titres désactivée.", flush=True)
        process.wait() 
        sys.exit(0)

    print("Lecture du flux en cours (Mode Résilient + Historique)...", flush=True)

    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(SOCKET_PATH)

        request = {"command": ["observe_property", 1, "media-title"]}
        client.sendall((json.dumps(request) + "\n").encode())

        while True:
            if process.poll() is not None:
                print("Fin du flux.", flush=True)
                break

            client.settimeout(1.0)
            try:
                data = client.recv(4096).decode()
                if data:
                    for line in data.strip().split('\n'):
                        if not line: continue
                        msg = json.loads(line)
                        
                        if msg.get("event") == "property-change":
                            if msg.get("name") == "media-title":
                                title = msg.get("data")
                                if title and " - " in title and "http" not in title:
                                    print(f"Titre : {title}", flush=True)
                                    
                                    windows_path = get_windows_path_from_db(title)
                                    if windows_path:
                                        add_to_playlist(windows_path)
                                    else:
                                        if db_connection is None:
                                            print("  -> En attente de la base Windows...", flush=True)
                                        else:
                                            print("  -> Non trouvé dans la base SQL", flush=True)
                                        
            except socket.timeout:
                continue
            except json.JSONDecodeError:
                pass
    except Exception as e:
        print(f"Erreur canal de communication : {e}", flush=True)
        process.wait()
    finally:
        process.terminate()
        try:
            client.close()
        except:
            pass
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
        if db_connection and db_connection.is_connected(): 
            db_connection.close()

if __name__ == "__main__":
    main()