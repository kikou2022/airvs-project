import subprocess
import socket
import json
import os
import time
import sys

# Configuration
ICECAST_URL = "https://icecast-vps-ovh.airvs.fr/stream11"
ALSA_DEVICE = "usb_card"
SOCKET_PATH = "/tmp/mpv_radio_socket"  # Retour dans /tmp, accessible par systemd

def main():
    # On nettoie le socket s'il existe déjà (au cas où mpv aurait planté brutalement)
    if os.path.exists(SOCKET_PATH):
        try:
            os.remove(SOCKET_PATH)
        except OSError:
            print("Attention : Le socket existant est bloqué, on le force...", flush=True)
            time.sleep(1)
            os.remove(SOCKET_PATH)

    # Lancement de mpv en arrière-plan
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

    # On attend jusqu'à 15 secondes que le fichier socket soit créé
    socket_ready = False
    for i in range(15):
        if process.poll() is not None:
            print("Erreur : mpv s'est arrêté au démarrage (problème réseau ou son).", flush=True)
            sys.exit(1)
        if os.path.exists(SOCKET_PATH):
            socket_ready = True
            break
        time.sleep(1)

    # Si mpv n'arrive pas à créer le socket, le son joue quand même, on ne plante pas
    if not socket_ready:
        print("Info : Détection des titres désactivée. Le son joue normalement.", flush=True)
        process.wait() 
        sys.exit(0)

    print("Lecture du flux en cours (Titres activés)...", flush=True)

    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(SOCKET_PATH)

        # On demande à mpv de prévenir quand le "media-title" change (plus fiable pour les flux Icecast)
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
                                # On filtre pour ne pas afficher l'URL du flux au démarrage
                                if title and "http" not in title and title != "":
                                    print(f"Titre : {title}", flush=True)
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

if __name__ == "__main__":
    main()
