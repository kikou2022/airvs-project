#!/usr/bin/env python3
import subprocess
import socket
import json
import os
import time
import re
import sys
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv
from datetime import date

load_dotenv()

# ─── Configuration Sortie pour RadioDJ ─────────────────────────────────
DOSSIER_RESEAU = "/home/sebastien/radio_bridge"
PLAYLIST_ACTIVE_PATH = os.path.join(DOSSIER_RESEAU, "playlist_live_azuracast.m3u")
NB_TITRES_FLUX_ACTIF = 10

# ─── Configuration Flux radio (depuis flux_radio.json) ────────────────
FLUX_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flux_radio.json")
ALSA_DEVICE = "usb_card"
SOCKET_PATH = "/tmp/mpv_radio_socket"

def _lire_flux_config():
    """Lit le fichier flux_radio.json et retourne l'URL du flux actif."""
    config_defaut = {
        "flux_actif": "stream11",
        "flux_disponibles": [
            {"id": "stream11", "nom": "AIRVS Principal", "url": "https://icecast-vps-ovh.airvs.fr/stream11", "codec": "AAC HE-AAC v2", "bitrate": 48, "detail": "libfdk_aac / aac_he_v2 / 44100Hz / Stéréo"}
        ]
    }
    try:
        if os.path.exists(FLUX_CONFIG_PATH):
            with open(FLUX_CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
            flux_id = config.get("flux_actif", "")
            for flux in config.get("flux_disponibles", []):
                if flux.get("id") == flux_id:
                    print(f"[CONFIG] Flux actif : {flux.get('nom', flux_id)} ({flux.get('url', '')})", flush=True)
                    return flux.get("url", "")
            # Si le flux_actif n'est pas trouvé dans la liste, utiliser le premier
            if config.get("flux_disponibles"):
                premier = config["flux_disponibles"][0]
                print(f"[CONFIG] Flux actif non trouvé, utilisation du premier : {premier.get('nom', '')} ({premier.get('url', '')})", flush=True)
                return premier.get("url", "")
        else:
            print(f"[CONFIG] Fichier {FLUX_CONFIG_PATH} non trouvé, utilisation de la config par défaut.", flush=True)
    except Exception as e:
        print(f"[CONFIG] Erreur lecture config : {e}. Utilisation de la config par défaut.", flush=True)

    # Fallback : URL par défaut
    return config_defaut["flux_disponibles"][0]["url"]

# ─── Configuration Base de données ─────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "192.168.1.39"),
    "port":     3306,
    "user":     os.getenv("DB_USER", "sebastien"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME", "radiodb"),
    "charset":  "utf8mb4",
    "collation": "utf8mb4_general_ci", 
    "use_pure": True,  
    "connection_timeout": 10, # Temps max pour établir la connexion physique
    "read_timeout": 10      # NOUVEAU : Temps max pour exécuter une requête (Anti-blocage si Windows est lent)
}

db_connection = None
ARCHIVE_PATH = None
historique_actif = []

def ensure_db_connected():
    global db_connection
    if db_connection is not None:
        try:
            db_connection.ping(reconnect=True, attempts=1)
            return True
        except Error:
            db_connection = None 

    try:
        db_connection = mysql.connector.connect(**DB_CONFIG)
        print("[DB] Connexion à la base SQL établie.", flush=True)
        return True
    except Error as e:
        print(f"[DB] IMPOSSIBLE DE SE CONNECTER. Raison : {e}", flush=True)
        return False
    
def get_windows_path_from_db(title: str):
    global db_connection
    if not title: return None
    
    # NOUVEAU : Si la base est injoignable, on ne bloque pas le script, on attend 5 sec
    if not ensure_db_connected():
        return None
    
    parts = title.split(" - ", 1)
    raw_artist = parts[0].strip() if len(parts) > 0 else ""
    song = parts[1].strip() if len(parts) > 1 else ""

    separateurs = r'[;,/&]|feat\.?|ft\.?|vs\.?'
    artistes_propres = re.split(separateurs, raw_artist, flags=re.IGNORECASE)
    artist = artistes_propres[0].strip() 
    
    artist = re.sub(r'\s+', ' ', artist)
    song = re.sub(r'\s+', ' ', song)

    try:
        cursor = db_connection.cursor()
        query = "SELECT path FROM songs WHERE artist LIKE %s AND title LIKE %s LIMIT 1"
        # Note : le timeout SQL est déjà géré via read_timeout=10 dans DB_CONFIG
        cursor.execute(query, (f"%{artist}%", f"%{song}%"))
        result = cursor.fetchone()
        cursor.close()
        
        if result:
            return result[0]
        else:
            cursor = db_connection.cursor()
            query_title_only = "SELECT path FROM songs WHERE title LIKE %s LIMIT 1"
            cursor.execute(query_title_only, (f"%{song}%",))
            result_title = cursor.fetchone()
            cursor.close()
            return result_title[0] if result_title else None
            
    except Error as e:
        print(f"[DB] Erreur requête (Timeout probable, Windows se réveille ?) : {e}", flush=True)
        # NOUVEAU : Si on a eu un timeout, on force la déconnexion pour que le prochain tour retente une connexion propre
        try:
            if db_connection:
                db_connection.close()
        except:
            pass
        db_connection = None
        return None
            
def manage_daily_playlist():
    """Initialise ou bascule l'archive journalière.
    
    Appelée au démarrage, puis à chaque détection de changement de jour
    dans la boucle principale. Crée le fichier archive_{today}.m3u si
    besoin et réinitialise l'historique actif (flux live).
    """
    global ARCHIVE_PATH
    global historique_actif
    historique_actif = []
    
    os.makedirs(DOSSIER_RESEAU, exist_ok=True)
    
    today_str = date.today().strftime("%Y-%m-%d")
    new_archive_path = os.path.join(DOSSIER_RESEAU, f"archive_{today_str}.m3u")
    
    # Détection d'un changement de jour : on logue la bascule
    if ARCHIVE_PATH and new_archive_path != ARCHIVE_PATH:
        print(f"[HISTORIQUE] Bascule vers le nouveau jour : {today_str}", flush=True)
    
    ARCHIVE_PATH = new_archive_path
    
    if not os.path.exists(ARCHIVE_PATH):
        try:
            with open(ARCHIVE_PATH, 'w', encoding='utf-8') as f:
                f.write("#EXTM3U\n")
            print(f"[HISTORIQUE] Nouvelle archive créée : archive_{today_str}.m3u", flush=True)
        except IOError as e:
            print(f"[ERREUR] Création archive : {e}", flush=True)

    if not os.path.exists(PLAYLIST_ACTIVE_PATH):
        try:
            with open(PLAYLIST_ACTIVE_PATH, 'w', encoding='utf-8') as f:
                f.write("#EXTM3U\n")
        except IOError as e:
            print(f"[ERREUR] Création playlist active : {e}", flush=True)


def add_to_playlist(windows_path: str):
    global historique_actif
    if not windows_path or not ARCHIVE_PATH: return
    
    temp_path = PLAYLIST_ACTIVE_PATH + ".tmp"
    try:
        with open(ARCHIVE_PATH, 'a', encoding='utf-8') as f:
            f.write(windows_path + '\n')
            
        if windows_path not in historique_actif:
            historique_actif.append(windows_path)
            
            if len(historique_actif) > NB_TITRES_FLUX_ACTIF:
                historique_actif = historique_actif[-NB_TITRES_FLUX_ACTIF:]
            
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write("#EXTM3U\n")
                for chemin in historique_actif:
                    f.write(chemin + '\n')
            
            os.replace(temp_path, PLAYLIST_ACTIVE_PATH)
            print(f"  -> [RADIODJ] Mise à jour du flux direct : {os.path.basename(windows_path)}", flush=True)
            
    except IOError as e:
        print(f"  -> [ERREUR] Écriture disque : {e}", flush=True)
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass

# ─── Boucle Principale ────────────────────────────────────────────────
def main():
    # Lire le flux actif depuis la configuration JSON
    icecast_url = _lire_flux_config()
    if not icecast_url:
        print("[ERREUR] Aucune URL de flux trouvée dans la configuration. Arrêt.", flush=True)
        sys.exit(1)

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
        icecast_url
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

            # ── Vérification changement de jour (midnight rollover) ──
            # ARCHIVE_PATH est fixé au démarrage. Si on passe minuit,
            # il faut créer le nouveau fichier archive_{new_day}.m3u
            # et réinitialiser l'historique actif.
            current_date_str = date.today().strftime("%Y-%m-%d")
            if ARCHIVE_PATH is None or current_date_str not in ARCHIVE_PATH:
                manage_daily_playlist()

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
                                    
                                    # NOUVEAU : Si le chemin est vide, Windows n'est pas prêt. On fait une pause discrète.
                                    if not windows_path:
                                        print("  -> En attente de la base Windows...", flush=True)
                                        time.sleep(5) # On attend 5 secondes au lieu de spamer la DB
                                        continue
                                    
                                    add_to_playlist(windows_path)
                                        
            except socket.timeout:
                continue
            except json.JSONDecodeError:
                pass
    except Exception as e:
        print(f"Erreur canal de communication : {e}", flush=True)
        process.wait()
    finally:
        # NOUVEAU : kill() au lieu de terminate() pour garantir la libération des ressources si le script plante violemment
        if process.poll() is None:
            process.kill()
            
        try:
            client.close()
        except:
            pass
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
        if db_connection and db_connection.is_connected(): 
            db_connection.close()


# ════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE DU SCRIPT
# ════════════════════════════════════════════════════════════════════
# Sans ce bloc, le script Python définit toutes les fonctions puis sort
# immédiatement avec le code 0, sans jamais exécuter la boucle principale.
if __name__ == "__main__":
    main()