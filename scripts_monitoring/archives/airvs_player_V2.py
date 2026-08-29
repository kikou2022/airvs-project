import subprocess
import socket
import json
import os
import time
import sys
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv

load_dotenv()

# ─── Configuration Originale Radio ────────────────────────────────────────
ICECAST_URL = "https://icecast-vps-ovh.airvs.fr/stream11"
ALSA_DEVICE = "usb_card"
SOCKET_PATH = "/tmp/mpv_radio_socket"

# ─── NOUVEAU : Configuration Sortie pour RadioDJ ─────────────────────────
# Mettez ici un chemin qui est SUR un partage Samba de la Debian
DOSSIER_RESEAU = "/home/sebastien/radio_bridge"
PLAYLIST_PATH = os.path.join(DOSSIER_RESEAU, "playlist_live_azuracast.m3u")

# ─── NOUVEAU : Configuration Base de données ─────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "192.168.1.39"),
    "port":     3306,
    "user":     os.getenv("DB_USER", "sebastien"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME", "radiodb"),
    "charset":  "utf8mb4",
}

# ─── NOUVEAU : Logique SQL ───────────────────────────────────────────────
db_connection = None

def connect_to_db():
    global db_connection
    try:
        db_connection = mysql.connector.connect(**DB_CONFIG)
    except Error as e:
        print(f"[DB] Erreur connexion : {e}", flush=True)

def get_windows_path_from_db(title: str):
    if not db_connection or not db_connection.is_connected(): return None
    if not title: return None
    
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
        print(f"[DB] Erreur SQL : {e}", flush=True)
        connect_to_db()
        return None

def add_to_playlist(windows_path: str):
    if not windows_path: return
    os.makedirs(DOSSIER_RESEAU, exist_ok=True)
    try:
        # Anti-doublon : on ne l'écrit pas si c'est le dernier de la liste
        if os.path.exists(PLAYLIST_PATH):
            with open(PLAYLIST_PATH, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                last_line = ""
                for line in reversed(lines):
                    if line.strip(): last_line = line.strip(); break
                if last_line == windows_path: return 

        with open(PLAYLIST_PATH, 'a', encoding='utf-8') as f:
            f.write(windows_path + '\n')
        print(f"  -> [RADIODJ] {windows_path}", flush=True)
    except IOError as e:
        print(f"  -> [ERREUR] Écriture : {e}", flush=True)

# ─── Votre Code Original (Légèrement modifié dans la boucle) ─────────────
def main():
    # Connexion SQL silencieuse au démarrage
    connect_to_db()

    if os.path.exists(SOCKET_PATH):
        try:
            os.remove(SOCKET_PATH)
        except OSError:
            print("Attention : Le socket existant est bloqué, on le force...", flush=True)
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
            print("Erreur : mpv s'est arrêté au démarrage (problème réseau ou son).", flush=True)
            sys.exit(1)
        if os.path.exists(SOCKET_PATH):
            socket_ready = True
            break
        time.sleep(1)

    if not socket_ready:
        print("Info : Détection des titres désactivée. Le son joue normalement.", flush=True)
        process.wait() 
        sys.exit(0)

    print("Lecture du flux en cours (Titres activés + Pont RadioDJ)...", flush=True)

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
                        if not line:
                            continue
                        msg = json.loads(line)
                        
                        if msg.get("event") == "property-change":
                            if msg.get("name") == "media-title":
                                title = msg.get("data")
                                if title and "http" not in title and title != "":
                                    print(f"Titre : {title}", flush=True)
                                    
                                    # === NOUVEAU : Appel SQL ===
                                    windows_path = get_windows_path_from_db(title)
                                    if windows_path:
                                        add_to_playlist(windows_path)
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