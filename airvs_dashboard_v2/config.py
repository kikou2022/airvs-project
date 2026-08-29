"""config.py — Constantes partagees du dashboard AIRVS.

Extraites de app.py pour eviter les imports circulaires lors de la
migration vers Flask Blueprints (Phase 1.1).

Ce fichier est importe par app.py et par les blueprints.
Il ne contient aucune logique Flask (pas de `app`, pas de `@app.route`).
"""

import os
import sys
import hashlib
import subprocess as sp
from dotenv import load_dotenv

# Charger les variables d'environnement depuis le fichier .env
load_dotenv()


# ═══════════════════════════════════════════════════════════════════════
# Chemins de base
# ═══════════════════════════════════════════════════════════════════════

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(BASE_DIR, "logs", "sync_log.txt")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
CONFIG_JSON_PATH = os.path.join(BASE_DIR, "config.json")
FLUX_CONFIG_PATH = os.path.join(BASE_DIR, "flux_radio.json")

# Creation des dossiers de travail (idempotent)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════
# Serveur Flask
# ═══════════════════════════════════════════════════════════════════════

HOST = '0.0.0.0'
PORT = 5000


# ═══════════════════════════════════════════════════════════════════════
# Authentification
# ═══════════════════════════════════════════════════════════════════════

MOT_DE_PASSE_HASH = hashlib.sha256(
    os.getenv('MOT_DE_PASSE_AIRVS', '').encode()
).hexdigest()

API_TOKEN = os.getenv('API_TOKEN', 'changeme_default_token_please_override')


# ═══════════════════════════════════════════════════════════════════════
# Reseau local (machine Debian + bridge)
# ═══════════════════════════════════════════════════════════════════════

DEBIAN_3_IP = '192.168.1.30'
DEBIAN_3_SSH_USER = 'sebastien'


# ═══════════════════════════════════════════════════════════════════════
# Anti-flash fenetre console sous Windows
# ═══════════════════════════════════════════════════════════════════════
# Sur Windows, chaque appel a subprocess.run(["ssh", ...]) ouvre
# brievement une fenetre console noire. CREATE_NO_WINDOW (0x08000000)
# masque ces fenetres. Sur Linux/Mac, on passe 0 (valeur neutre).

_SUBPROCESS_CREATIONFLAGS = (
    getattr(sp, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
)


# ═══════════════════════════════════════════════════════════════════════
# Flux radio
# ═══════════════════════════════════════════════════════════════════════

FLUX_BRIDGE_PATH = "/home/sebastien/radio_bridge"  # Chemin sur Debian 3 (acces via SSH)


# ═══════════════════════════════════════════════════════════════════════
# Grille editoriale
# ═══════════════════════════════════════════════════════════════════════

JOURS_MAP = {
    1: 'lundi', 2: 'mardi', 3: 'mercredi', 4: 'jeudi',
    5: 'vendredi', 6: 'samedi', 7: 'dimanche',
}

JOURS_LABELS = {
    1: 'Lundi', 2: 'Mardi', 3: 'Mercredi', 4: 'Jeudi',
    5: 'Vendredi', 6: 'Samedi', 7: 'Dimanche',
}

JOURS_LABELS_COURTS = {
    1: 'Lun', 2: 'Mar', 3: 'Mer', 4: 'Jeu',
    5: 'Ven', 6: 'Sam', 7: 'Dim',
}

PALETTE_AIRVS = [
    '#1DB954',  # vert Spotify
    '#E22165',  # rose
    '#F39C12',  # orange
    '#3498DB',  # bleu
    '#9B59B6',  # violet
    '#1ABC9C',  # turquoise
    '#E74C3C',  # rouge
    '#34495E',  # gris-bleu fonce
    '#F1C40F',  # jaune
    '#16A085',  # vert fonce
]


def _ge_to_min(t):
    """Convertit 'HH:MM' en minutes depuis minuit."""
    h, m = map(int, t.split(':'))
    return h * 60 + m


def _ge_times_overlap(s1, e1, s2, e2):
    """Detecte si deux intervalles horaires se chevauchent.
    Gere les blocs traversant minuit (fin < debut -> +24h sur la fin).
    """
    s1m, e1m = _ge_to_min(s1), _ge_to_min(e1)
    s2m, e2m = _ge_to_min(s2), _ge_to_min(e2)
    if e1m <= s1m:
        e1m += 24 * 60
    if e2m <= s2m:
        e2m += 24 * 60
    return not (e1m <= s2m or e2m <= s1m)


# ═══════════════════════════════════════════════════════════════════════
# Piges
# ═══════════════════════════════════════════════════════════════════════

PIGES_BASE_PATH = "/var/www/pige"


# ═══════════════════════════════════════════════════════════════════════
# Push AzuraCast
# ═══════════════════════════════════════════════════════════════════════

PUSH_LIMIT_HARDCAP = 500


# ═══════════════════════════════════════════════════════════════════════
# Tiroirs de reception MP3 (Inbox)
# ═══════════════════════════════════════════════════════════════════════

INBOX_FOLDERS = {
    "inbox_1": {
        "path": r"E:\projet_radio\Musique\en_cours",
        "id_subcat": 244,
        "id_genre": 452
    },
    "inbox_2": {
        "path": r"D:\projet_radio\Musique\en_cours",
        "id_subcat": 244,
        "id_genre": 452
    },
    "inbox_local": {
        "path": UPLOAD_FOLDER,
        "id_subcat": 244,
        "id_genre": 452
    }
}


# ═══════════════════════════════════════════════════════════════════════
# AzuraCast API
# ═══════════════════════════════════════════════════════════════════════

AZURA_API_URL = os.getenv('AZURA_API_URL', 'https://azuracast.2026.airvs.fr/api/station/7/files')
AZURA_API_KEY = os.getenv('AZURA_API_KEY', '5e256834da4da4cf:1cadd3936115612949a795cd5a48e12d')
AZURA_MEDIA_BASE = os.getenv('AZURA_MEDIA_BASE', '/home/sebastien/music_azuracast6_7')


# ═══════════════════════════════════════════════════════════════════════
# Piges (artwork)
# ═══════════════════════════════════════════════════════════════════════

PIGES_ARTWORK_WINDOWS_DIR = os.getenv('PIGES_ARTWORK_WINDOWS_DIR', r"U:\airvs_artwork")


# ═══════════════════════════════════════════════════════════════════════
# Worker
# ═══════════════════════════════════════════════════════════════════════

WORKER_VERSION_ATTENDUE = os.getenv('WORKER_VERSION_ATTENDUE', '2026.08.29-A')


# ═══════════════════════════════════════════════════════════════════════
# Bulk Schedule (AzuraCast)
# ═══════════════════════════════════════════════════════════════════════

BULK_SCHEDULE_MAX_PLAYLISTS = 50
BULK_SCHEDULE_POLL_TIMEOUT = 180  # secondes max pour attendre le worker


# ═══════════════════════════════════════════════════════════════════════
# Validation des suspects (E1)
# ═══════════════════════════════════════════════════════════════════════

# Dossier contenant les fichiers suspects (sur Debian, monte U:\)
# Convention : sous-dossier 'suspects' du dossier d'import.
SUSPECTS_DIR = os.getenv('SUSPECTS_DIR', r'U:\Musique\import\suspects')

# Base du dossier FolderSync sur E:\ (disque local Windows)
# Chemin Windows reel (physique sur disque).
# Le worker Ubuntu voit le meme dossier via SSHFS en /mnt/projet_radio3/Musique
# car DOSSIERS_A_IGNORER={"projet_radio"} dans windows_vers_linux() absorbe
# la difference entre E:\projet_radio\Musique et E:\Musique.
# RadioDJ surveille ce dossier pour importer les nouveaux fichiers.
FOLDERSYNC_WINDOWS_BASE = os.getenv('FOLDERSYNC_WINDOWS_BASE', r'E:\projet_radio\Musique')

# Chemin vers mp3gain.exe sur le dashboard Windows.
# Si 'mp3gain' (defaut), il sera cherche dans le PATH systeme.
# Sinon, specifier le chemin complet, ex: r'C:\Outils\mp3gain\mp3gain.exe'
MP3GAIN_PATH = os.getenv('MP3GAIN_PATH', 'mp3gain')
