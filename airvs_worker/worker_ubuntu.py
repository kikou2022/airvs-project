#!/usr/bin/env python3
# PATCH 31/08/2026 ~17h00 — Fix sync différentielle dossiers AzuraCast (plus de vidage)
# ═══════════════════════════════════════════════════════════════════
# WORKER_VERSION : à incrémenter à chaque modification significative.
# Le worker loggue cette version en base au démarrage, ce qui permet
# au dashboard de vérifier que la bonne version est déployée sur Ubuntu.
# Historique :
#   2026.06.23-A — ajout IMPORT_SHEETS_COULEUR (Phase 3.3 Google Sheets)
#                 + fix SFTP (paramètre 'destination' manquant)
#   2026.06.24-B — Mode Pool de titres validés (VALIDATION_POOL_SHEETS) :
#                 • Tables airvs_pool_titres + airvs_pool_meta + airvs_pool_auto_validation
#                 • Auto-détection multi-format (vince / laurent / mary)
#                 • Accès Google Drive natif via gspread (pas de fichiers locaux)
#                 • Réutilisation du moteur de matching flou de executer_prefill_sheets
#                 • Dépendance : validation_pool_multiformat.py (même dossier)
# ═══════════════════════════════════════════════════════════════════
WORKER_VERSION = "2026.08.29-A"  # + airvs_animateurs: table dédiée soumissions VPS (genre, video_url, commentaire, vps_id)

import time
import datetime
import json
import shutil
import os
import subprocess
import pymysql
import gspread
import requests
import shlex
import unicodedata
import re
import logging
import ftplib
import traceback
from io import BytesIO
from google.oauth2.service_account import Credentials
from pathlib import Path, PureWindowsPath
from datetime import datetime, timedelta

# Module import de masse (pipeline dédoublonnage intégré au worker)
# Doit être déployé dans le même dossier que ce worker.
try:
    from import_masse import executer_import_masse
    IMPORT_MASSE_MODULE_DISPONIBLE = True
except ImportError as _e_import:
    IMPORT_MASSE_MODULE_DISPONIBLE = False
    print(f"[WARN] Module import_masse.py introuvable ({_e_import}). Tâche IMPORT_MASSE désactivée.")

try:
    from azuracast_api import (
        AzuraCastAPI, AzuraCastAPIError,
        get_default_api, get_api_for_station, lister_stations_config,
        generer_m3u_pour_azuracast, get_api_for_azuracast2
    )
    AZURA_API_MODULE_DISPONIBLE = True
except ImportError:
    AZURA_API_MODULE_DISPONIBLE = False
    print("[WARN] Module azuracast_api.py introuvable. Tâches playlist désactivées.")

# Module multi-format pour le mode Pool de titres validés.
# Doit être déployé dans le même dossier que ce worker.
# Si absent, le mode Pool est désactivé mais le reste du worker continue de fonctionner.
try:
    from validation_pool_multiformat import (
        detecter_format as pool_detecter_format,
        extraire_titres_depuis_valeurs as pool_extraire_titres,
        extraire_titres_classeur_gspread as pool_extraire_classeur,
        PROFIL_VINCE, PROFIL_LAURENT, PROFIL_MARY,
    )
    POOL_MODULE_DISPONIBLE = True
except ImportError as _e_pool:
    POOL_MODULE_DISPONIBLE = False
    print(f"[WARN] Module validation_pool_multiformat.py introuvable ({_e_pool}). "
          f"Mode Pool désactivé. Déployez ce fichier dans le même dossier que worker_ubuntu.py.")

DB_CONFIG = {
    "host": "192.168.1.30",
    "port": 3306,
    "user": "airvs_user",
    "password": "idylle@SL2026!",
    "database": "airvs_dashboard",
    "charset": "utf8mb4"
}

MOUNT_MAP = {
    "D:\\": "/mnt/projet_radio2",
    "E:\\": "/mnt/projet_radio3",
    "M:\\": "/mnt/musique_172_go",
    "L:\\": "/mnt/musique_47_go",
    "N:\\": "/media/sebastien/musique_debian",
    "U:\\": "/mnt/stockage_160go/",
    "Z:\\": "/mnt/externe",
}
DOSSIERS_A_IGNORER = {"projet_radio"}

CONFIG_JSON = "config.json"
AZURA_SSHFS_ROOT = "/home/sebastien/music_azuracast6_7"
AZURA_SSH_HOST = "sebastien@azuracast.2026.airvs.fr"
AZURA_SSH_PORT = "2022"
CREDENTIALS_JSON = "credentials.json"
SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
# Constantes historiques (station 7). Conservées comme fallback si config.json absent.
# NE PLUS UTILISER DIRECTEMENT — passer par _url_key_for_station(station_id).
AZURA_API_URL = "https://azuracast.2026.airvs.fr/api/station/7/files"
AZURA_API_KEY = "5e256834da4da4cf:1cadd3936115612949a795cd5a48e12d"
_DEFAULT_STATION_ID = 7

PURGE_RETENTION_JOURS = 7

# ── Colonnes optionnelles de la table songs (RadioDJ) ──
# Les noms de colonnes varient selon la version de RadioDJ.
# Le worker les détecte au démarrage via INFORMATION_SCHEMA (comme le backend Flask)
# et les ajoute dynamiquement au SELECT du mode Sheets et Pool.
_COLONNES_OPTIONNELLES_CANDIDATS = {
    'sm_weight':  ['sm_weight', 'weight', 'song_weight', 'rotation_weight'],
    'sm_rating':  ['sm_rating', 'rating', 'song_rating'],
    'play_count': ['play_count', 'count_played', 'played_count'],
    'date_played':['date_played', 'last_played', 'date_last_played'],
    'date_added': ['date_added'],
}
_colonnes_optionnelles_cache = None  # peuplé au 1er appel


def _detecter_colonnes_optionnelles_worker():
    """Détecte les colonnes optionnelles de la table songs via INFORMATION_SCHEMA.
    Même logique que _detecter_colonnes_optionnelles() dans blueprints/azuracast.py,
    adaptée à pymysql.

    Retourne un dict {cle_logique: nom_sql_trouve_ou_None}.
    Résultat mis en cache pour toute la durée de vie du worker.
    """
    global _colonnes_optionnelles_cache
    if _colonnes_optionnelles_cache is not None:
        return _colonnes_optionnelles_cache

    resultat = {k: None for k in _COLONNES_OPTIONNELLES_CANDIDATS}
    try:
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'songs'"
        )
        colonnes_reelles = {row['COLUMN_NAME'] for row in cursor.fetchall()}
        cursor.close()
        db.close()

        for cle_logique, candidats in _COLONNES_OPTIONNELLES_CANDIDATS.items():
            for c in candidats:
                if c in colonnes_reelles:
                    resultat[cle_logique] = c
                    break
    except Exception as e:
        print(f"[WARN] Détection colonnes optionnelles songs échouée : {e}")

    _colonnes_optionnelles_cache = resultat
    detectees = {k: v for k, v in resultat.items() if v}
    print(f"[COLONNES] Optionnelles détectées : {detectees}")
    return resultat


def _construire_select_optionnel_worker():
    """Construit les fragments SQL pour les colonnes optionnelles détectées.
    Retourne (select_parts: list[str], disponibles: dict).
    """
    cols = _detecter_colonnes_optionnelles_worker()
    select_parts = []
    for cle_logique, nom_sql in cols.items():
        if nom_sql:
            select_parts.append(f"s.{nom_sql} AS {cle_logique}")
    return select_parts, {k: v for k, v in cols.items() if v}

# ── Évolution 3 : Stats agrégées (audience) — VPS OVH 1 ──
# Le worker interroge en SSH le dashboard agrégateur (airvs-stats-aggregator)
# qui écoute en local sur 127.0.0.1:8050 sur le VPS OVH 1.
VPS_OVH1_SSH_HOST = "ubuntu@54.37.38.117"
VPS_OVH1_SSH_PORT = "22"

# ── Évolution 1 : Piges → Podcasts — VPS OVH 1 ──
# Dossier où pige.airvs.fr dépose les enregistrements horaires (MP3).
PIGES_BASE_PATH = "/var/www/pige"

# Cache des instances AzuraCastAPI par station_id (une instance par station)
_azura_api_instances = {}


def _charger_azura_config_worker():
    """Lit la section 'azuracast' de config.json (côté worker).
    Retourne {} si absent/corrompu."""
    try:
        if not os.path.exists(CONFIG_JSON):
            return {}
        with open(CONFIG_JSON, 'r', encoding='utf-8') as f:
            config = json.load(f)
        azura = config.get("azuracast") if isinstance(config, dict) else None
        return azura if isinstance(azura, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"[CONFIG] Erreur lecture {CONFIG_JSON} : {e}")
        return {}


def _url_key_for_station(station_id):
    """Construit l'URL de l'endpoint /files et récupère la clé API pour la station donnée.
    Lit config.json pour base_url et api_key (communs à toutes les stations d'une installation).
    Fallback sur les constantes AZURA_API_URL / AZURA_API_KEY si config absent.

    Returns:
        (api_url, api_key) — api_url format: 'https://azuracast.../api/station/{id}/files'
    """
    azura = _charger_azura_config_worker()
    base_url = azura.get("base_url", "https://azuracast.2026.airvs.fr")
    api_key = azura.get("api_key", AZURA_API_KEY)
    # base_url dans config.json ne contient PAS /api (on l'ajoute)
    if not base_url.endswith("/api"):
        base_url = base_url.rstrip("/") + "/api"
    api_url = f"{base_url}/station/{int(station_id)}/files"
    return api_url, api_key


def get_azura_api(station_id=None):
    """Retourne une instance AzuraCastAPI pour la station demandée.

    Args:
        station_id: ID de la station (ex: 6 ou 7). Si None, utilise la station par défaut
                    (première de config.json, ou 7 en fallback).

    Returns:
        AzuraCastAPI instance, ou None si le module azuracast_api n'est pas disponible.
    """
    if not AZURA_API_MODULE_DISPONIBLE:
        return None
    if station_id is None:
        station_id = _DEFAULT_STATION_ID
    else:
        station_id = int(station_id)
    # Cache par station_id
    if station_id not in _azura_api_instances:
        _azura_api_instances[station_id] = get_api_for_station(station_id)
    return _azura_api_instances[station_id]

def purger_anciennes_taches():
    """Supprime les tâches terminées datant de plus de PURGE_RETENTION_JOURS jours."""
    try:
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor()
        seuil = datetime.now() - timedelta(days=PURGE_RETENTION_JOURS)
        cursor.execute(
            "DELETE FROM taches_planifiees WHERE statut = 'termine' AND date_fin < %s",
            (seuil,)
        )
        nb_supprimees = cursor.rowcount
        if nb_supprimees > 0:
            db.commit()
            print(f"[PURGE] {nb_supprimees} tâche(s) ancienne(s) supprimée(s).")
        cursor.close()
        db.close()
    except Exception as e:
        print(f"[PURGE] Erreur : {e}")


def windows_vers_linux(chemin_windows: str) -> Path:
    p = PureWindowsPath(chemin_windows)
    lecteur = p.drive + "\\"
    if lecteur not in MOUNT_MAP:
        raise ValueError(f"Lecteur inconnu '{lecteur}'")
    mount = MOUNT_MAP[lecteur]
    parties_sans_lecteur = p.parts[1:]
    if parties_sans_lecteur and parties_sans_lecteur[0] in DOSSIERS_A_IGNORER:
        parties_sans_lecteur = parties_sans_lecteur[1:]
    return Path(mount).joinpath(*parties_sans_lecteur)


def resoudre_chemin_insensible(src_path: Path) -> Path:
    """
    Résout un chemin en ignorant la casse sur Linux.
    Si le chemin exact n'existe pas, tente une résolution composant par composant
    en ignorant la casse (utile pour les extensions .MP3 vs .mp3, etc.).
    Retourne le chemin résolu ou le chemin original si aucune correspondance n'est trouvée.
    """
    if src_path.exists():
        return src_path

    # Résolution composant par composant en ignorant la casse
    current = src_path.anchor  # ex: '/'
    for part in src_path.relative_to(src_path.anchor).parts:
        candidate = Path(current) / part
        if candidate.exists():
            current = str(candidate)
            continue
        # Chercher une correspondance insensible à la casse dans le répertoire parent
        parent = Path(current)
        if not parent.is_dir():
            return src_path  # On ne peut pas aller plus loin
        part_lower = part.lower()
        found = False
        try:
            for entry in os.scandir(parent):
                if entry.name.lower() == part_lower:
                    current = str(Path(current) / entry.name)
                    found = True
                    break
        except OSError:
            return src_path
        if not found:
            return src_path  # Aucune correspondance

    resolved = Path(current)
    if resolved.exists():
        return resolved
    return src_path


def logger(tache_id, message, raw_append=False):
    """Ajoute une ligne au log_resultat de la tâche.

    Par défaut, tronque à MAX_LOG_APPEND (50 Ko) pour protéger la colonne TEXT.
    Si raw_append=True, écrit le message tel quel sans troncature
    (utile pour les payloads JSON qui sont concaténés puis parsés par le frontend).
    """
    try:
        if not raw_append:
            # Tronquer le message pour éviter Data too long (colonne TEXT ~65 Ko,
            # on se garde une marge de 50 Ko pour le CONCAT accumulé)
            MAX_LOG_APPEND = 50000  # 50 Ko par appel
            if len(message) > MAX_LOG_APPEND:
                message = message[:MAX_LOG_APPEND] + '\n... [TRONQUÉ]'
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor()
        query = "UPDATE taches_planifiees SET log_resultat = CONCAT(IFNULL(log_resultat, ''), %s) WHERE id = %s"
        cursor.execute(query, (message + "\n", tache_id))
        db.commit()
        cursor.close()
        db.close()
    except pymysql.Error.OperationalError as e:
        # Lock wait timeout ou connexion perdue → ne pas retry pour éviter la cascade
        print(f"Erreur logger (SQL {e[0]}): {e}")
    except Exception as e:
        print(f"Erreur logger: {e}")


def creer_arborescence_sftp(chemin_relatif):
    """Crée chaque niveau de dossier via SFTP (un appel par niveau, ignore les existants)."""
    parties = [p for p in chemin_relatif.split('/') if p]
    courant = ""
    for partie in parties:
        courant = courant + "/" + partie if courant else partie
        cmd = [
            "sftp", "-b", "-", "-P", AZURA_SSH_PORT,
            "-o", "BatchMode=yes", AZURA_SSH_HOST
        ]
        subprocess.run(
            cmd, input=f"mkdir {shlex.quote(courant)}\n",
            capture_output=True, text=True, timeout=15
        )
        # Le mkdir peut échouer si le dossier existe déjà — on ignore


def copier_vers_azuracast_sftp(fichier_source, chemin_distant_complet):
    """Envoie un fichier vers AzuraCast via SFTP (chemin relatif depuis la racine SFTPGo)."""
    try:
        chemin_distant_complet = chemin_distant_complet.replace('\\', '/')

        if not chemin_distant_complet.startswith(AZURA_SSHFS_ROOT):
            return False, "Le chemin ne pointe pas vers AzuraCast."

        # SFTPGo : la racine virtuelle SFTP = AZURA_SSHFS_ROOT
        # On supprime ce préfixe pour obtenir un chemin relatif
        chemin_relatif = chemin_distant_complet[len(AZURA_SSHFS_ROOT):].lstrip('/')

        commandes_sftp = (
            f"put {shlex.quote(str(fichier_source))} "
            f"{shlex.quote(chemin_relatif)}\n"
        )

        cmd_sftp = [
            "sftp", "-b", "-", "-P", AZURA_SSH_PORT,
            "-o", "BatchMode=yes", AZURA_SSH_HOST
        ]

        resultat = subprocess.run(
            cmd_sftp, input=commandes_sftp,
            capture_output=True, text=True, timeout=120
        )

        if resultat.returncode != 0:
            erreur = resultat.stderr.strip() if resultat.stderr else "Erreur inconnue SFTP"
            return False, erreur

        return True, "SFTP OK"

    except Exception as e:
        return False, str(e)


def resoudre_sheets_id(alias_ou_id: str) -> str:
    try:
        if os.path.exists(CONFIG_JSON):
            with open(CONFIG_JSON, "r", encoding="utf-8") as f:
                config = json.load(f)
            if alias_ou_id in config.get("sheets", {}):
                return config["sheets"][alias_ou_id]
    except Exception as e:
        print(f"Attention : Erreur de lecture du config.json -> {e}")
    return alias_ou_id


def obtenir_titres_deja_azuracast(station_id=None):
    """Interroge l'API AzuraCast pour avoir un Set de tous les 'artiste - titre' présents
    sur la station donnée.

    Args:
        station_id: ID de la station (ex: 6 ou 7). Si None, utilise la station par défaut (7).
    """
    if station_id is None:
        station_id = _DEFAULT_STATION_ID
    try:
        api_url, api_key = _url_key_for_station(station_id)
        headers = {"Authorization": f"Bearer {api_key}"}
        params = {"limit": 10000}
        reponse = requests.get(api_url, headers=headers, params=params, timeout=60)

        if reponse.status_code != 200:
            return set()

        data = reponse.json()
        rows = data if isinstance(data, list) else data.get("rows", [])

        titres_existants = set()
        for morceau in rows:
            artist = str(morceau.get("artist", "") or "").strip()
            title = str(morceau.get("title", "") or "").strip()
            if artist and title:
                chaine_complete = f"{artist} - {title}"
                titres_existants.add(normaliser_pour_recherche(chaine_complete))
        return titres_existants
    except Exception:
        return set()


def normaliser_pour_recherche(texte):
    """
    Normalise un texte pour comparaison floue :
    - minuscules, sans accents
    - remplace & et + par des espaces
    - remplace les mots de liaison 'et', 'and', 'x' par des espaces
      (harmonise 'Angus & Julia Stone' ≈ 'Angus et Julia Stone' ≈ 'Angus + Julia Stone')
    - supprime ponctuation et caractères spéciaux
    - collapse les espaces multiples
    """
    if not texte:
        return ""
    texte = str(texte).strip().lower()
    # Suppression des accents (NFD + strip combining marks)
    texte = ''.join(
        c for c in unicodedata.normalize('NFD', texte)
        if unicodedata.category(c) != 'Mn'
    )
    # Remplacement des caractères spéciaux par des espaces
    for caractere in "()[]&'+-":
        texte = texte.replace(caractere, " ")
    # Harmonisation des mots de liaison (et/and/x → espace)
    # On utilise des limites de mots pour ne pas modifier l'intérieur d'un mot
    texte = re.sub(r'\b(?:et|and|x)\b', ' ', texte, flags=re.IGNORECASE)
    # Collapse espaces
    return re.sub(r'\s+', ' ', texte).strip()


def _normaliser_manquant(texte):
    """Normalisation legere pour dedup airvs_manquants.
    Strip, collapse espaces, NFC unicode. Conserve la casse et les
    accents pour l'affichage, mais assure la coherence pour l'unique index.
    """
    if not texte:
        return ''
    texte = str(texte).strip()
    texte = unicodedata.normalize('NFC', texte)
    texte = re.sub(r'\s+', ' ', texte)
    return texte


def extraire_principal(texte):
    """
    Extrait la partie principale d'un nom d'artiste ou de titre :
    - "Towa Bird feat. Kathleen Hanna" → "Towa Bird"
    - "All Gone (feat. Kathleen Hanna)" → "All Gone"
    - "Abba" → "Abba"
    Conserve les parenthèses non-feat (cover, remix, radio edit...)
    """
    if not texte:
        return texte
    # Retirer le contenu entre parenthèses s'il contient feat/ft
    texte = re.sub(r'\s*\([^)]*(?:feat\.?|ft\.?|featuring)[^)]*\)\s*', ' ', texte, flags=re.IGNORECASE).strip()
    # Couper avant feat./ft./featuring en fin de chaîne
    texte = re.split(r'\s+(?:feat\.?|ft\.?|featuring)\s+', texte, flags=re.IGNORECASE)[0].strip()
    return texte


def extraire_nu(texte):
    """
    Extrait le noyau nu d'un artiste ou titre : supprime TOUT contenu
    entre parenthèses ET les feat./ft./featuring.
    Plus agressif qu'extraire_principal, utilisé quand la correspondance
    échoue avec les parenthèses conservées.

    Exemples :
    - "Trio SR9 feat. Camille"             → "Trio SR9"
    - "Happy (cover Pharrell Williams)"    → "Happy"
    - "Happy (ft Camille)"                 → "Happy"
    - "All Gone (feat. Kathleen Hanna)"    → "All Gone"
    - "Song Title (Radio Edit)"            → "Song Title"
    - "Towa Bird feat. Kathleen Hanna"     → "Towa Bird"
    """
    if not texte:
        return texte
    # Retirer TOUT le contenu entre parenthèses
    texte = re.sub(r'\s*\([^)]*\)\s*', ' ', texte).strip()
    # Couper avant feat./ft./featuring
    texte = re.split(r'\s+(?:feat\.?|ft\.?|featuring)\s+', texte, flags=re.IGNORECASE)[0].strip()
    return texte


# ── PRÉ-REMPLISSAGE STRUCTURAL ──
def executer_prefill_sheets(tache_id, parametres):
    sheets_alias_ou_id = parametres.get('sheets_id', '')
    onglet = parametres.get('onglet', '')
    semaine = parametres.get('semaine', '')
    cat_id = parametres.get('category_id')
    subcat_id = parametres.get('subcategory_id')

    logger(tache_id, f"☁ DÉBUT PRÉ-REMPLISSAGE (Onglet: {onglet}, Cat: {cat_id}, Sous-Cat: {subcat_id})")

    # ── Auto-migration : s'assurer que log_resultat est MEDIUMTEXT (16 Mo)
    #    Le type TEXT (65 Ko) est insuffisant pour les Sheets volumineux :
    #    le JSON payload + les logs intermédiaires dépassent la limite,
    #    ce qui fait échouer silencieusement l'écriture des marqueurs JSON.
    try:
        _mig_db = pymysql.connect(**DB_CONFIG)
        _mig_cur = _mig_db.cursor()
        _mig_cur.execute(
            "SELECT DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'taches_planifiees' "
            "AND COLUMN_NAME = 'log_resultat'")
        _row = _mig_cur.fetchone()
        if _row and _row[0].upper() != 'MEDIUMTEXT':
            _mig_cur.execute("ALTER TABLE taches_planifiees MODIFY COLUMN log_resultat MEDIUMTEXT")
            logger(tache_id, "📊 log_resultat migré TEXT → MEDIUMTEXT")
        elif not _row:
            logger(tache_id, "⚠ Colonne log_resultat introuvable")
        _mig_db.commit()
        _mig_cur.close()
        _mig_db.close()
        logger(tache_id, "📊 Colonne log_resultat vérifiée (MEDIUMTEXT)")
    except Exception as _mig_e:
        logger(tache_id, f"⚠ Migration colonne échouée (non bloquant) : {_mig_e}")

    try:
        sheets_id = resoudre_sheets_id(sheets_alias_ou_id)
        creds = Credentials.from_service_account_file(CREDENTIALS_JSON, scopes=SHEETS_SCOPES)
        client = gspread.authorize(creds)
        feuille = client.open_by_key(sheets_id).worksheet(onglet)
        lignes = feuille.get_all_records()
    except Exception as e:
        logger(tache_id, f"ERREUR FATALE Connexion Google Sheets : {e}")
        return

    # Diagnostic : afficher les en-têtes détectés
    try:
        _diagnostic_and_search(tache_id, parametres, lignes, onglet, semaine,
                                    cat_id, subcat_id)
    except Exception as _main_exc:
        # ── Filet de sécurité ultime ──
        # Logger l'exception complète, avec fallback si logger() est cassé.
        _tb = traceback.format_exc()
        _err_msg = f"!!! EXCEPTION dans prefill:\n{_tb}"
        print(f"[Tâche {tache_id}] {_err_msg}")
        try:
            logger(tache_id, _err_msg)
        except Exception:
            # logger() elle-même échoue → écriture directe dans la BDD
            try:
                _fb_db = pymysql.connect(**DB_CONFIG)
                _fb_cur = _fb_db.cursor()
                _fb_cur.execute(
                    "UPDATE taches_planifiees SET log_resultat = CONCAT(IFNULL(log_resultat,''), %s) WHERE id = %s",
                    ("\n" + _err_msg + "\n", tache_id))
                _fb_db.commit()
                _fb_cur.close()
                _fb_db.close()
            except Exception as _fb_e:
                print(f"[Tâche {tache_id}] Impossible d'écrire l'erreur dans le log: {_fb_e}")
        raise  # Propager pour que le caller marque la tâche 'erreur'


def _diagnostic_and_search(tache_id, parametres, lignes, onglet, semaine,
                             cat_id, subcat_id):
    """Partie diagnostic + recherche de executer_prefill_sheets.
    Extraite pour pouvoir envelopper dans un try/except avec traceback complet."""
    if lignes:
        colonnes = list(lignes[0].keys())
        logger(tache_id, f"📋 Colonnes détectées dans le Sheet ({len(colonnes)} colonnes) : {colonnes}")
        # Aperçu concis de la 1ère ligne (sans le dict complet pour limiter le log)
        _premier = lignes[0]
        _art_preview = str(_premier.get('ARTISTE', '') or _premier.get('ARTISTE ', '') or '')[:50]
        _tit_preview = str(_premier.get('TITRE 1', '') or _premier.get('TITRE 1 ', '') or '')[:50]
        logger(tache_id, f"📋 Aperçu ligne 1 : ARTISTE='{_art_preview}', TITRE='{_tit_preview}'")
        # Vérifier la présence des colonnes attendues
        colonnes_attendues = ["SEMAINE", "ARTISTE", "TITRE 1", "TITRE 2"]
        manquantes = [c for c in colonnes_attendues if c not in colonnes and c + " " not in colonnes]
        if manquantes:
            logger(tache_id, f"⚠ Colonnes attendues mais non trouvées : {manquantes}")
            # Suggestions : chercher des colonnes proches
            for col_attendue in manquantes:
                suggestions = [c for c in colonnes if col_attendue.replace(" ", "").upper() in c.replace(" ", "").upper() or c.replace(" ", "").upper() in col_attendue.replace(" ", "").upper()]
                if suggestions:
                    logger(tache_id, f"  → Colonnes proches trouvées pour '{col_attendue}' : {suggestions}")
        logger(tache_id, f"📋 {len(lignes)} ligne(s) de données dans l'onglet '{onglet}'")
    else:
        logger(tache_id, "⚠ Aucune ligne de données trouvée dans l'onglet (Sheet vide ou sans en-têtes)")
        return

    recherches = []
    lignes_filtrees_semaine = 0
    lignes_sans_artiste = 0
    lignes_sans_titre = 0
    for ligne in lignes:
        semaine_brute = str(
            ligne.get("SEMAINE") or ligne.get("SEMAINE ") or ""
        ).strip().replace(".0", "")
        if semaine and semaine_brute != semaine.strip():
            lignes_filtrees_semaine += 1
            continue
        artist = str(ligne.get("ARTISTE", "")).strip()
        titre1 = str(ligne.get("TITRE 1", "")).strip()
        titre2 = str(ligne.get("TITRE 2", "")).strip()
        if not artist:
            lignes_sans_artiste += 1
            continue
        if not titre1 and not titre2:
            lignes_sans_titre += 1
            continue
        # ── Évolution 1a : extraire ANNÉE et ALBUM (informationnels uniquement,
        #    non utilisés dans la comparaison SQL). Tolérant sur les variations
        #    de casse/espaces des en-têtes ("ANNÉE" / "ANNEE" / "Année").
        annee_raw = ""
        for k in ligne.keys():
            if k and k.strip().upper() in ("ANNÉE", "ANNEE", "YEAR"):
                annee_raw = str(ligne.get(k, "") or "").strip()
                break
        # Convertir ".0" en entier si numérique (Google Sheets remonte parfois 1985.0)
        try:
            if annee_raw:
                annee_int = int(float(annee_raw))
                annee_raw = str(annee_int)
        except (ValueError, TypeError):
            pass  # Garder la valeur brute si non numérique

        album_raw = ""
        for k in ligne.keys():
            if k and k.strip().upper() in ("ALBUM", "ALBUMS"):
                album_raw = str(ligne.get(k, "") or "").strip()
                break

        if artist and titre1:
            recherches.append((artist, titre1, annee_raw, album_raw))
        if artist and titre2:
            recherches.append((artist, titre2, annee_raw, album_raw))

    # Résumé du diagnostic
    logger(tache_id, f"📋 Diagnostic parsing : {len(recherches)} titre(s) extrait(s) sur {len(lignes)} ligne(s)")
    if lignes_filtrees_semaine > 0:
        logger(tache_id, f"   → {lignes_filtrees_semaine} ligne(s) ignorée(s) par filtre SEMAINE='{semaine}'")
    if lignes_sans_artiste > 0:
        logger(tache_id, f"   → {lignes_sans_artiste} ligne(s) sans ARTISTE")
    if lignes_sans_titre > 0:
        logger(tache_id, f"   → {lignes_sans_titre} ligne(s) sans TITRE 1 ni TITRE 2")

    logger(tache_id, f"{len(recherches)} titre(s) trouvés dans le Sheet. Requête structurelle SQL en cours...")

    db = pymysql.connect(**DB_CONFIG)
    cursor = db.cursor(pymysql.cursors.DictCursor)

    # ── Colonnes optionnelles DB (détection dynamique) ──
    opt_select_parts = []
    opt_disponibles = {}
    opt_select_str = ""
    try:
        opt_select_parts, opt_disponibles = _construire_select_optionnel_worker()
        opt_select_str = (", " + ", ".join(opt_select_parts)) if opt_select_parts else ""
        if opt_disponibles:
            logger(tache_id, f"📊 Colonnes optionnelles DB détectées : {opt_disponibles}")
    except Exception as e:
        logger(tache_id, f"⚠ Détection colonnes optionnelles échouée (non bloquant) : {e}")

    resultats_trouves = []
    non_trouves = []
    inversions_detectees = []  # Suivi des inversions artiste/titre pour le récapitulatif
    _total_recherches = len(recherches)
    _progress_step = max(1, _total_recherches // 10)  # Progression tous les 10%

    for _idx, (artist, title, sheet_annee, sheet_album) in enumerate(recherches):
        # Indicateur de progression tous les 10%
        if _progress_step > 0 and (_idx + 1) % _progress_step == 0:
            logger(tache_id, f"  ⏳ Progression : {_idx + 1}/{_total_recherches} ({100 * (_idx + 1) // _total_recherches}%)")
        sql_conditions = ["s.song_type = 0"]
        sql_params = []

        if subcat_id:
            sql_conditions.append("s.id_subcat = %s")
            sql_params.append(subcat_id)
        elif cat_id:
            sql_conditions.append("s.id_subcat IN (SELECT ID FROM subcategory WHERE parentid = %s)")
            sql_params.append(cat_id)

        base_query = f"""
            SELECT s.ID, s.path, s.artist, s.title, s.year, s.duration, s.comments{opt_select_str}
            FROM songs s
            WHERE {' AND '.join(sql_conditions)}
        """

        resultat = None
        methode = ""

        # ── Passe 1 : Recherche stricte (LIKE exact sur artiste et titre complets) ──
        query1 = base_query + " AND s.artist LIKE %s AND s.title LIKE %s LIMIT 1"
        params1 = sql_params + [f"%{artist}%", f"%{title}%"]
        cursor.execute(query1, tuple(params1))
        resultat = cursor.fetchone()
        if resultat:
            methode = "strict"

        # ── Passe 2 : Recherche avec artiste/titre principal (sans feat.) ──
        if not resultat:
            artist_principal = extraire_principal(artist)
            title_principal = extraire_principal(title)
            if artist_principal != artist or title_principal != title:
                query2 = base_query + " AND s.artist LIKE %s AND s.title LIKE %s LIMIT 1"
                params2 = sql_params + [f"%{artist_principal}%", f"%{title_principal}%"]
                cursor.execute(query2, tuple(params2))
                resultat = cursor.fetchone()
                if resultat:
                    methode = f"principal (artiste='{artist_principal}', titre='{title_principal}')"

        # ── Passe 2b : Recherche « nu » (sans feat. ni aucune parenthèse) ──
        # Gère les cas où le contenu entre parenthèses diffère totalement
        # entre le Sheet et RadioDJ : ex (cover X) vs (ft Y)
        if not resultat:
            artist_nu = extraire_nu(artist)
            title_nu = extraire_nu(title)
            if (artist_nu != extraire_principal(artist) or title_nu != extraire_principal(title)):
                if artist_nu and title_nu:
                    query2b = base_query + " AND s.artist LIKE %s AND s.title LIKE %s LIMIT 1"
                    params2b = sql_params + [f"%{artist_nu}%", f"%{title_nu}%"]
                    cursor.execute(query2b, tuple(params2b))
                    resultat = cursor.fetchone()
                    if resultat:
                        methode = f"nu (artiste='{artist_nu}', titre='{title_nu}')"

        # ── Passe 3 : Recherche normalisée (sans accents, minuscules, et/and/&, caractères spéciaux) ──
        if not resultat:
            artist_norm = normaliser_pour_recherche(artist)
            title_norm = normaliser_pour_recherche(title)
            artist_principal_norm = normaliser_pour_recherche(extraire_principal(artist))
            title_principal_norm = normaliser_pour_recherche(extraire_principal(title))
            artist_nu_norm = normaliser_pour_recherche(extraire_nu(artist))
            title_nu_norm = normaliser_pour_recherche(extraire_nu(title))
            query3 = base_query + " AND LOWER(s.artist) LIKE %s AND LOWER(s.title) LIKE %s LIMIT 1"
            # Essayer par ordre de spécificité décroissante : nu normalisé → principal normalisé → complet normalisé
            for a_search, t_search in [(artist_nu_norm, title_nu_norm), (artist_principal_norm, title_principal_norm), (artist_norm, title_norm)]:
                if a_search and t_search:
                    params3 = sql_params + [f"%{a_search}%", f"%{t_search}%"]
                    cursor.execute(query3, tuple(params3))
                    resultat = cursor.fetchone()
                    if resultat:
                        methode = f"normalisé (artiste='{a_search}', titre='{t_search}')"
                        break

        # ── Passe 4 : Recherche par mots-clés extraits (mots significatifs de l'artiste + titre) ──
        # Gère les cas résiduels où la normalisation ne suffit pas,
        # ex: différences d'ordre des mots, variantes orthographiques multiples
        if not resultat:
            artist_mots = set(m for m in normaliser_pour_recherche(extraire_nu(artist)).split() if len(m) >= 3)
            title_mots = set(m for m in normaliser_pour_recherche(extraire_nu(title)).split() if len(m) >= 3)
            # Il faut au moins 2 mots significatifs au total pour éviter les faux positifs
            if len(artist_mots) + len(title_mots) >= 2:
                # On cherche avec le mot le plus discriminant (le plus long) de l'artiste et du titre
                artist_best = max(artist_mots, key=len) if artist_mots else ""
                title_best = max(title_mots, key=len) if title_mots else ""
                if artist_best and title_best:
                    query4 = base_query + " AND LOWER(s.artist) LIKE %s AND LOWER(s.title) LIKE %s LIMIT 5"
                    params4 = sql_params + [f"%{artist_best}%", f"%{title_best}%"]
                    cursor.execute(query4, tuple(params4))
                    candidats = cursor.fetchall()
                    # Parmi les candidats, chercher celui qui a le plus de mots en commun
                    if candidats:
                        meilleur_score = 0
                        meilleur_resultat = None
                        for cand in candidats:
                            cand_artiste_mots = set(m for m in normaliser_pour_recherche(extraire_nu(cand['artist'])).split() if len(m) >= 3)
                            cand_titre_mots = set(m for m in normaliser_pour_recherche(extraire_nu(cand['title'])).split() if len(m) >= 3)
                            score = len(artist_mots & cand_artiste_mots) + len(title_mots & cand_titre_mots)
                            if score > meilleur_score and score >= 2:
                                meilleur_score = score
                                meilleur_resultat = cand
                        if meilleur_resultat:
                            resultat = meilleur_resultat
                            methode = f"mots-cles (artiste='{artist_best}', titre='{title_best}', score={meilleur_score})"

        # ── Passe 5 : Inversion artiste/titre ──
        # Gère les cas où les colonnes ARTISTE et TITRE sont interverties dans le Sheet.
        # Exemple : Sheet = ARTISTE:"Une Femme avec une Femme", TITRE:"Mecano"
        #           RadioDJ = artist:"Mecano", title:"Une Femme avec une Femme"
        if not resultat:
            artist_inv = title    # Le "titre" du Sheet devient l'artiste cherché
            title_inv = artist    # L'"artiste" du Sheet devient le titre cherché
            # On rejoue les passes 1 à 3 avec les valeurs inversées
            # Passe 5a : Stricte inversée
            query5a = base_query + " AND s.artist LIKE %s AND s.title LIKE %s LIMIT 1"
            params5a = sql_params + [f"%{artist_inv}%", f"%{title_inv}%"]
            cursor.execute(query5a, tuple(params5a))
            resultat = cursor.fetchone()
            if resultat:
                methode = f"inversé-strict (artiste='{artist_inv}', titre='{title_inv}')"
            else:
                # Passe 5b : Principal inversé
                artist_inv_principal = extraire_principal(artist_inv)
                title_inv_principal = extraire_principal(title_inv)
                if artist_inv_principal != artist_inv or title_inv_principal != title_inv:
                    query5b = base_query + " AND s.artist LIKE %s AND s.title LIKE %s LIMIT 1"
                    params5b = sql_params + [f"%{artist_inv_principal}%", f"%{title_inv_principal}%"]
                    cursor.execute(query5b, tuple(params5b))
                    resultat = cursor.fetchone()
                    if resultat:
                        methode = f"inversé-principal (artiste='{artist_inv_principal}', titre='{title_inv_principal}')"
            if not resultat:
                # Passe 5c : Normalisé inversé
                artist_inv_norm = normaliser_pour_recherche(artist_inv)
                title_inv_norm = normaliser_pour_recherche(title_inv)
                artist_inv_nu_norm = normaliser_pour_recherche(extraire_nu(artist_inv))
                title_inv_nu_norm = normaliser_pour_recherche(extraire_nu(title_inv))
                query5c = base_query + " AND LOWER(s.artist) LIKE %s AND LOWER(s.title) LIKE %s LIMIT 1"
                for a_search, t_search in [(artist_inv_nu_norm, title_inv_nu_norm), (artist_inv_norm, title_inv_norm)]:
                    if a_search and t_search:
                        params5c = sql_params + [f"%{a_search}%", f"%{t_search}%"]
                        cursor.execute(query5c, tuple(params5c))
                        resultat = cursor.fetchone()
                        if resultat:
                            methode = f"inversé-normalisé (artiste='{a_search}', titre='{t_search}')"
                            break
            if resultat:
                inversions_detectees.append({
                    'sheet_artiste': artist,
                    'sheet_titre': title,
                    'db_artiste': resultat['artist'],
                    'db_titre': resultat['title']
                })
                logger(tache_id, f"  ⚠ INVERSION artiste/titre détectée : Sheet='{artist} - {title}' → DB='{resultat['artist']} - {resultat['title']}'")

        if resultat:
            # ── Évolution 1a : attacher les métadonnées Sheet (ANNÉE, ALBUM)
            #    au résultat DB, pour affichage informatif côté frontend.
            #    Ces valeurs ne sont PAS utilisées pour la comparaison SQL.
            resultat['_sheet_annee'] = sheet_annee
            resultat['_sheet_album'] = sheet_album
            resultats_trouves.append(resultat)
            if methode != "strict":
                # Log concis : méthode tronquée + artiste/titre (max 80 car. total)
                _meth_court = methode.split('(')[0][:30]
                logger(tache_id, f"  🔍 {_meth_court} : {resultat['artist'][:35]} - {resultat['title'][:35]}")
        else:
            # ── Évolution 1c : formater les manquants en dicts (et non plus
            #    en chaînes "artiste - titre") pour pouvoir afficher ANNÉE/ALBUM
            #    et copier au format "artiste titre" (sans séparateur).
            non_trouves.append({
                'artist': artist,
                'title': title,
                'annee': sheet_annee,
                'album': sheet_album,
                'display': f"{artist} {title}".strip()
            })

    cursor.close()
    db.close()

    logger(tache_id, f"Analyse terminée : {len(resultats_trouves)} trouvés, {len(non_trouves)} manquants.")

    # ── Récapitulatif des inversions artiste/titre détectées ──
    if inversions_detectees:
        logger(tache_id, f"━━━ RÉCAPITULATIF INVERSIONS ARTISTE/TITRE ({len(inversions_detectees)} détectée(s)) ━━━")
        logger(tache_id, "  Ces lignes du Sheet ont les colonnes ARTISTE et TITRE 1 interverties.")
        logger(tache_id, "  Pensez à les corriger dans le Google Sheet :")
        for inv in inversions_detectees:
            logger(tache_id, f"  • Sheet : ARTISTE='{inv['sheet_artiste']}' / TITRE='{inv['sheet_titre']}'")
            logger(tache_id, f"    → DB  : artist='{inv['db_artiste']}' / title='{inv['db_titre']}'")
        logger(tache_id, "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    json_payload = json.dumps({"found": resultats_trouves, "not_found": non_trouves}, ensure_ascii=False, default=str)
    # Marqueurs sur des lignes séparées.
    # Le JSON est écrit sans troncature (raw_append=True) car parsé par le frontend.
    # Filet de sécurité : vérifier que les marqueurs ont bien été écrits.
    logger(tache_id, "===PREFILL_JSON_START===")
    logger(tache_id, json_payload, raw_append=True)
    logger(tache_id, "===PREFILL_JSON_END===")

    # ── Vérification post-écriture des marqueurs JSON ──
    try:
        _verif_db = pymysql.connect(**DB_CONFIG)
        _verif_cur = _verif_db.cursor()
        _verif_cur.execute(
            "SELECT log_resultat FROM taches_planifiees WHERE id = %s", (tache_id,))
        _verif_row = _verif_cur.fetchone()
        _verif_cur.close()
        _verif_db.close()
        _log_content = _verif_row[0] if _verif_row and _verif_row[0] else ""
        if "===PREFILL_JSON_END===" not in _log_content:
            # Les marqueurs n'ont pas été écrits (colonne trop petite ou erreur).
            # Tenter une écriture directe (sans CONCAT) dans une nouvelle connexion.
            logger(tache_id, "⚠ Marqueurs JSON non détectés après écriture. Tentative d'écriture directe...")
            _direct_db = pymysql.connect(**DB_CONFIG)
            _direct_cur = _direct_db.cursor()
            _direct_cur.execute(
                "UPDATE taches_planifiees SET log_resultat = %s WHERE id = %s",
                ("\n===PREFILL_JSON_START===\n" + json_payload + "\n===PREFILL_JSON_END===\n", tache_id))
            _direct_db.commit()
            _direct_cur.close()
            _direct_db.close()
            logger(tache_id, "✅ Marqueurs JSON écrits via écriture directe (contenu remplacé)")
    except Exception as _verif_e:
        print(f"Erreur vérification marqueurs JSON: {_verif_e}")

    # Log des manquants sur disque
    if non_trouves:
        log_dir = "logs_prefill"
        os.makedirs(log_dir, exist_ok=True)

        alias_propre = re.sub(r'[^\w\-_.]', '_', str(parametres.get('sheets_id', 'inconnu')))
        onglet_propre = re.sub(r'[^\w\-_.]', '_', str(parametres.get('onglet', 'inconnu')))
        semaine_str = str(parametres.get('semaine', '')).strip()
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

        if semaine_str:
            nom_fichier_log = f"prefill_{alias_propre}_{onglet_propre}_S{semaine_str}_{timestamp_str}.log"
        else:
            nom_fichier_log = f"prefill_{alias_propre}_{onglet_propre}_{timestamp_str}.log"

        chemin_log = os.path.join(log_dir, nom_fichier_log)

        try:
            with open(chemin_log, "w", encoding="utf-8") as f:
                f.write(f"Rapport de pré-remplissage AIRVS - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Sheet Alias : {parametres.get('sheets_id')}\n")
                f.write(f"Onglet      : {parametres.get('onglet')}\n")
                if semaine_str:
                    f.write(f"Semaine     : {semaine_str}\n")
                f.write(f"Total lues  : {len(recherches)}\n")
                f.write(f"Manquants   : {len(non_trouves)}\n")
                f.write("=" * 50 + "\n")
                # ── Évolution 1c : format "artiste titre" sur une ligne
                #    (sans séparateur " - ") + ANNÉE et ALBUM entre crochets si présents
                for item in non_trouves:
                    ligne_log = item['display']
                    extras = []
                    if item.get('annee'):
                        extras.append(item['annee'])
                    if item.get('album'):
                        extras.append(item['album'])
                    if extras:
                        ligne_log += "  [" + " | ".join(extras) + "]"
                    f.write(ligne_log + "\n")

            logger(tache_id, f"Log des manquants sauvegardé : {nom_fichier_log}")
        except Exception as e:
            logger(tache_id, f"Erreur lors de la création du log : {e}")

    # ── Persister les manquants dans airvs_manquants (étape A) ──
    if non_trouves:
        try:
            _manq_db = pymysql.connect(**DB_CONFIG)
            _manq_cur = _manq_db.cursor()
            _manq_alias = str(parametres.get('sheets_id', '')).strip() or None
            _manq_onglet = str(parametres.get('onglet', '')).strip() or None
            _manq_semaine = str(parametres.get('semaine', '')).strip() or None
            nb_manq_ins = 0
            for item in non_trouves:
                try:
                    _manq_cur.execute(
                        "INSERT IGNORE INTO airvs_manquants "
                        "(artiste, titre, annee, album, source, sheet_alias, onglet, semaine) "
                        "VALUES (%s, %s, %s, %s, 'prefill_sheets', %s, %s, %s)",
                        (
                            _normaliser_manquant((item.get('artist') or ''))[:500],
                            _normaliser_manquant((item.get('title') or ''))[:500],
                            (item.get('annee') or None),
                            (item.get('album') or None),
                            _manq_alias,
                            _manq_onglet,
                            _manq_semaine,
                        )
                    )
                    if _manq_cur.rowcount > 0:
                        nb_manq_ins += 1
                except Exception:
                    pass  # ne pas casser le flux pour un manquant
            _manq_db.commit()
            _manq_cur.close()
            _manq_db.close()
            logger(tache_id, f"Manquants persistés : {nb_manq_ins} nouveau(x) dans airvs_manquants")
        except Exception as _manq_e:
            logger(tache_id, f"⚠ Persistance manquants échouée (non bloquant) : {_manq_e}")
    else:
        logger(tache_id, "Aucun titre manquant, log non généré.")

    logger(tache_id, "=== TERMINÉ PREFILL ===")


# ────────────────────────────────────────────────────────────────────────────
# Mode Pool de titres validés (2026.06.24-B)
# ────────────────────────────────────────────────────────────────────────────

def _match_titre_songs(cursor, artist, title, sheet_annee=None, sheet_album=None,
                        cat_id=None, subcat_id=None):
    """
    Moteur de matching flou partagé entre le mode Push (executer_prefill_sheets)
    et le mode Pool (executer_validation_pool_sheets).

    Réutilise intégralement les 5 passes du mode Push :
      1. Strict (LIKE %artiste% AND LIKE %titre%)
      2. Principal (sans feat.)
      2b. Nu (sans feat. ni parenthèses)
      3. Normalisé (sans accents, minuscules)
      4. Mots-clés (score >= 2)
      5. Inversion artiste/titre (passes 5a/5b/5c)

    Retourne (resultat_dict_ou_None, methode_str).
    Le dict resultat contient les colonnes songs + _sheet_annee + _sheet_album.
    """
    sql_conditions = ["s.song_type = 0"]
    sql_params = []

    if subcat_id:
        sql_conditions.append("s.id_subcat = %s")
        sql_params.append(subcat_id)
    elif cat_id:
        sql_conditions.append("s.id_subcat IN (SELECT ID FROM subcategory WHERE parentid = %s)")
        sql_params.append(cat_id)

    # ── Colonnes optionnelles DB (détection dynamique, cache global) ──
    opt_select_parts = []
    opt_select_str = ""
    try:
        opt_select_parts, _ = _construire_select_optionnel_worker()
        opt_select_str = (", " + ", ".join(opt_select_parts)) if opt_select_parts else ""
    except Exception:
        pass  # non bloquant : le SELECT de base fonctionne sans ces colonnes

    base_query = (
        "SELECT s.ID, s.path, s.artist, s.title, s.year, s.duration, s.comments"
        + opt_select_str + " "
        "FROM songs s WHERE " + " AND ".join(sql_conditions)
    )

    resultat = None
    methode = ""

    # ── Passe 1 : stricte ──
    # ORDER BY CHAR_LENGTH préfère les titres courts/exacts
    query1 = base_query + " AND s.artist LIKE %s AND s.title LIKE %s ORDER BY CHAR_LENGTH(s.title) ASC LIMIT 1"
    params1 = sql_params + [f"%{artist}%", f"%{title}%"]
    cursor.execute(query1, tuple(params1))
    resultat = cursor.fetchone()
    if resultat:
        methode = "strict"

    # ── Passe 2 : principal (sans feat.) ──
    if not resultat:
        artist_principal = extraire_principal(artist)
        title_principal = extraire_principal(title)
        if artist_principal != artist or title_principal != title:
            query2 = base_query + " AND s.artist LIKE %s AND s.title LIKE %s ORDER BY CHAR_LENGTH(s.title) ASC LIMIT 1"
            params2 = sql_params + [f"%{artist_principal}%", f"%{title_principal}%"]
            cursor.execute(query2, tuple(params2))
            resultat = cursor.fetchone()
            if resultat:
                methode = f"principal (artiste='{artist_principal}', titre='{title_principal}')"

    # ── Passe 2b : nu (sans feat. ni parenthèses) ──
    if not resultat:
        artist_nu = extraire_nu(artist)
        title_nu = extraire_nu(title)
        if (artist_nu != extraire_principal(artist) or title_nu != extraire_principal(title)):
            if artist_nu and title_nu:
                query2b = base_query + " AND s.artist LIKE %s AND s.title LIKE %s ORDER BY CHAR_LENGTH(s.title) ASC LIMIT 1"
                params2b = sql_params + [f"%{artist_nu}%", f"%{title_nu}%"]
                cursor.execute(query2b, tuple(params2b))
                resultat = cursor.fetchone()
                if resultat:
                    methode = f"nu (artiste='{artist_nu}', titre='{title_nu}')"

    # ── Passe 3 : normalisé ──
    if not resultat:
        artist_norm = normaliser_pour_recherche(artist)
        title_norm = normaliser_pour_recherche(title)
        artist_principal_norm = normaliser_pour_recherche(extraire_principal(artist))
        title_principal_norm = normaliser_pour_recherche(extraire_principal(title))
        artist_nu_norm = normaliser_pour_recherche(extraire_nu(artist))
        title_nu_norm = normaliser_pour_recherche(extraire_nu(title))
        query3 = base_query + " AND LOWER(s.artist) LIKE %s AND LOWER(s.title) LIKE %s ORDER BY CHAR_LENGTH(s.title) ASC LIMIT 1"
        for a_search, t_search in [
            (artist_nu_norm, title_nu_norm),
            (artist_principal_norm, title_principal_norm),
            (artist_norm, title_norm),
        ]:
            if a_search and t_search:
                params3 = sql_params + [f"%{a_search}%", f"%{t_search}%"]
                cursor.execute(query3, tuple(params3))
                resultat = cursor.fetchone()
                if resultat:
                    methode = f"normalisé (artiste='{a_search}', titre='{t_search}')"
                    break

    # ── Passe 4 : mots-clés ──
    if not resultat:
        artist_mots = set(m for m in normaliser_pour_recherche(extraire_nu(artist)).split() if len(m) >= 3)
        title_mots = set(m for m in normaliser_pour_recherche(extraire_nu(title)).split() if len(m) >= 3)
        if len(artist_mots) + len(title_mots) >= 2:
            artist_best = max(artist_mots, key=len) if artist_mots else ""
            title_best = max(title_mots, key=len) if title_mots else ""
            if artist_best and title_best:
                query4 = base_query + " AND LOWER(s.artist) LIKE %s AND LOWER(s.title) LIKE %s LIMIT 5"
                params4 = sql_params + [f"%{artist_best}%", f"%{title_best}%"]
                cursor.execute(query4, tuple(params4))
                candidats = cursor.fetchall()
                if candidats:
                    meilleur_score = 0
                    meilleur_resultat = None
                    for cand in candidats:
                        cand_artiste_mots = set(m for m in normaliser_pour_recherche(extraire_nu(cand['artist'])).split() if len(m) >= 3)
                        cand_titre_mots = set(m for m in normaliser_pour_recherche(extraire_nu(cand['title'])).split() if len(m) >= 3)
                        score = len(artist_mots & cand_artiste_mots) + len(title_mots & cand_titre_mots)
                        if score > meilleur_score and score >= 2:
                            meilleur_score = score
                            meilleur_resultat = cand
                    if meilleur_resultat:
                        resultat = meilleur_resultat
                        methode = f"mots-cles (artiste='{artist_best}', titre='{title_best}', score={meilleur_score})"

    # ── Passe 5 : inversion artiste/titre ──
    if not resultat:
        artist_inv = title
        title_inv = artist
        query5a = base_query + " AND s.artist LIKE %s AND s.title LIKE %s ORDER BY CHAR_LENGTH(s.title) ASC LIMIT 1"
        params5a = sql_params + [f"%{artist_inv}%", f"%{title_inv}%"]
        cursor.execute(query5a, tuple(params5a))
        resultat = cursor.fetchone()
        if resultat:
            methode = f"inversé-strict (artiste='{artist_inv}', titre='{title_inv}')"
        else:
            artist_inv_principal = extraire_principal(artist_inv)
            title_inv_principal = extraire_principal(title_inv)
            if artist_inv_principal != artist_inv or title_inv_principal != title_inv:
                query5b = base_query + " AND s.artist LIKE %s AND s.title LIKE %s ORDER BY CHAR_LENGTH(s.title) ASC LIMIT 1"
                params5b = sql_params + [f"%{artist_inv_principal}%", f"%{title_inv_principal}%"]
                cursor.execute(query5b, tuple(params5b))
                resultat = cursor.fetchone()
                if resultat:
                    methode = f"inversé-principal (artiste='{artist_inv_principal}', titre='{title_inv_principal}')"
        if not resultat:
            artist_inv_norm = normaliser_pour_recherche(artist_inv)
            title_inv_norm = normaliser_pour_recherche(title_inv)
            artist_inv_nu_norm = normaliser_pour_recherche(extraire_nu(artist_inv))
            title_inv_nu_norm = normaliser_pour_recherche(extraire_nu(title_inv))
            query5c = base_query + " AND LOWER(s.artist) LIKE %s AND LOWER(s.title) LIKE %s ORDER BY CHAR_LENGTH(s.title) ASC LIMIT 1"
            for a_search, t_search in [(artist_inv_nu_norm, title_inv_nu_norm), (artist_inv_norm, title_inv_norm)]:
                if a_search and t_search:
                    params5c = sql_params + [f"%{a_search}%", f"%{t_search}%"]
                    cursor.execute(query5c, tuple(params5c))
                    resultat = cursor.fetchone()
                    if resultat:
                        methode = f"inversé-normalisé (artiste='{a_search}', titre='{t_search}')"
                        break

    if resultat:
        resultat['_sheet_annee'] = sheet_annee or ""
        resultat['_sheet_album'] = sheet_album or ""
    return resultat, methode


def executer_validation_pool_sheets(tache_id, parametres):
    """
    Tâche worker : valide les titres d'une Google Sheet tierce contre songs
    et persiste les résultats dans airvs_pool_titres.

    Paramètres attendus dans tache['parametres'] :
      - sheets_id (alias ou ID brut, résolu via resoudre_sheets_id)
      - onglet (nom de feuille, ou 'TOUS' pour parcourir toutes les feuilles)
      - semaine_filter (optionnel, ex "23")
      - dry_run (optionnel, booléen)

    Utilise :
      - gspread (credentials.json) pour Google Drive
      - validation_pool_multiformat pour l'auto-détection + extraction multi-format
      - _match_titre_songs (moteur de matching flou partagé avec le mode Push)
    """
    if not POOL_MODULE_DISPONIBLE:
        logger(tache_id, "ERREUR FATALE : Module validation_pool_multiformat.py manquant. "
                          "Déployez ce fichier dans le même dossier que worker_ubuntu.py.")
        logger(tache_id, "=== TERMINÉ VALIDATION POOL (ERREUR MODULE) ===")
        return

    sheets_alias_ou_id = parametres.get('sheets_id', '')
    onglet = parametres.get('onglet', 'TOUS')
    semaine_filter = parametres.get('semaine_filter') or parametres.get('semaine', '')
    dry_run = bool(parametres.get('dry_run', False))

    logger(tache_id, "════════════════════════════════════════════")
    logger(tache_id, f"🔄 VALIDATION POOL — Sheet: {sheets_alias_ou_id}")
    logger(tache_id, f"  Onglet         : {onglet}")
    logger(tache_id, f"  Semaine filter : {semaine_filter or '(toutes)'}")
    logger(tache_id, f"  Mode           : {'DRY-RUN (parse seulement)' if dry_run else 'ÉCRITURE'}")
    logger(tache_id, "════════════════════════════════════════════")

    if not sheets_alias_ou_id:
        logger(tache_id, "ERREUR: sheets_id (alias ou ID brut) requis")
        logger(tache_id, "=== TERMINÉ VALIDATION POOL (ERREUR PARAM) ===")
        return

    # 1. Connexion Google Drive via gspread
    sheets_id = resoudre_sheets_id(sheets_alias_ou_id)
    logger(tache_id, f"  Sheet ID résolu : {sheets_id}")

    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_JSON, scopes=SHEETS_SCOPES)
        client = gspread.authorize(creds)
    except Exception as e:
        logger(tache_id, f"ERREUR FATALE Credentials/Google Drive : {e}")
        logger(tache_id, "=== TERMINÉ VALIDATION POOL (ERREUR CREDENTIALS) ===")
        return

    # 2. Extraction multi-format via le module dédié
    feuilles_a_parcourir = None
    if onglet and onglet.upper() != 'TOUS':
        feuilles_a_parcourir = [onglet]

    def _log_extraction(msg):
        logger(tache_id, msg)

    titres_tous = pool_extraire_classeur(
        gc_client=client,
        sheet_id=sheets_id,
        feuilles_a_parcourir=feuilles_a_parcourir,
        semaine_filtrer=semaine_filter or None,
        log_callback=_log_extraction,
    )

    if not titres_tous:
        logger(tache_id, "⚠ Aucun titre extrait — Sheet vide ou format non reconnu")
        logger(tache_id, "=== TERMINÉ VALIDATION POOL (VIDE) ===")
        return

    # Construire l'id_pool
    id_pool = f"{sheets_alias_ou_id}/{onglet}/{semaine_filter or 'ALL'}"
    logger(tache_id, f"✅ {len(titres_tous)} titre(s) extrait(s) — id_pool={id_pool}")

    # Déduction du profil majoritaire à partir de l'alias
    alias_lower = sheets_alias_ou_id.lower()
    if 'vince' in alias_lower:
        source_majoritaire = PROFIL_VINCE
    elif 'laurent' in alias_lower:
        source_majoritaire = PROFIL_LAURENT
    elif 'mary' in alias_lower:
        source_majoritaire = PROFIL_MARY
    else:
        source_majoritaire = 'inconnu'
    logger(tache_id, f"  Profil détecté  : {source_majoritaire}")

    # 3. Validation contre songs (moteur de matching flou)
    db = pymysql.connect(**DB_CONFIG)
    cursor = db.cursor(pymysql.cursors.DictCursor)

    resultats_trouves = []
    non_trouves = []
    inversions_detectees = []

    total_a_traiter = len(titres_tous)
    t0_matching = time.time()
    logger(tache_id, f"🔍 Démarrage matching contre songs ({total_a_traiter} titre(s) à valider)")

    for idx, t in enumerate(titres_tous, 1):
        resultat, methode = _match_titre_songs(
            cursor, t.artiste, t.titre,
            sheet_annee=str(t.annee) if t.annee else None,
            sheet_album=t.album,
        )
        if resultat:
            resultat['_source'] = source_majoritaire
            resultat['_feuille'] = t.feuille
            resultat['_ligne'] = t.ligne
            resultat['_semaine'] = t.semaine
            resultat['_jour'] = t.jour
            # 2026-06-25-C : conserver aussi l'artiste/titre saisis dans la
            # sheet (t.artiste/t.titre) pour alimenter correctement les
            # colonnes artiste_saisi/titre_saisi lors de l'INSERT. Avant ce
            # fix, on utilisait par erreur r['artist'] (DB match) à la place.
            resultat['_sheet_artiste'] = t.artiste
            resultat['_sheet_titre'] = t.titre
            resultats_trouves.append(resultat)
            if 'inversé' in methode:
                inversions_detectees.append({
                    'sheet_artiste': t.artiste,
                    'sheet_titre': t.titre,
                    'db_artiste': resultat['artist'],
                    'db_titre': resultat['title']
                })
                logger(tache_id, f"  ⚠ INVERSION : Sheet='{t.artiste} - {t.titre}' → DB='{resultat['artist']} - {resultat['title']}'")
            elif methode != "strict":
                logger(tache_id, f"  🔍 Trouvé via {methode} : {resultat['artist']} - {resultat['title']}")
        else:
            non_trouves.append({
                'artist': t.artiste,
                'title': t.titre,
                'annee': str(t.annee) if t.annee else '',
                'album': t.album or '',
                'feuille': t.feuille,
                'ligne': t.ligne,
                'display': f"{t.artiste} {t.titre}".strip()
            })

        # Progression tous les 20 titres (ou le dernier)
        if idx % 20 == 0 or idx == total_a_traiter:
            elapsed_match = time.time() - t0_matching
            avg_per_titre = elapsed_match / idx if idx > 0 else 0
            pct = (idx / total_a_traiter) * 100 if total_a_traiter > 0 else 100
            reste = (total_a_traiter - idx) * avg_per_titre
            logger(tache_id, f"  📈 Progression : {idx}/{total_a_traiter} ({pct:.0f}%) — "
                              f"{len(resultats_trouves)} OK / {len(non_trouves)} KO — "
                              f"moy {avg_per_titre:.2f}s/titre — reste ~{reste:.0f}s")

    elapsed_match_total = time.time() - t0_matching
    logger(tache_id, f"✅ Matching terminé en {elapsed_match_total:.1f}s "
                      f"(moy {elapsed_match_total/total_a_traiter:.2f}s/titre)")
    logger(tache_id, f"📊 Validation : {len(resultats_trouves)} validé(s), {len(non_trouves)} rejeté(s)")

    # 4. Persistance dans airvs_pool_titres (sauf en dry-run)
    inserts = 0
    if not dry_run:
        # Purge préalable du pool existant pour cet id_pool
        cursor.execute("DELETE FROM airvs_pool_titres WHERE id_pool = %s", (id_pool,))
        logger(tache_id, f"🗑 Pool existant purgé : {cursor.rowcount} ancienne(s) entrée(s)")

        for r in resultats_trouves:
            # 2026-06-25-C :
            #   - artiste_saisi/titre_saisi ← sheet (t.artiste/t.titre)
            #     stockés dans r['_sheet_artiste']/_sheet_titre
            #   - artist/title/year/album ← DB match (r['artist']/r['title']/r['year']/r['comments'])
            #     pour compat dashboard qui fait JOIN songs ou lit directement ces colonnes
            sheet_artiste = r.get('_sheet_artiste') or r.get('artist') or ''
            sheet_titre = r.get('_sheet_titre') or r.get('title') or ''
            song_id_val = r.get('ID')
            if song_id_val is None:
                # Defensive — _match_titre_songs devrait toujours renvoyer ID
                # non-NULL pour un match valide, mais par sécurité on logge
                logger(tache_id, f"  ⚠ Match sans ID (artist='{r.get('artist')}', title='{r.get('title')}') — song_id forcé à 0")
                song_id_val = 0
            cursor.execute("""
                INSERT INTO airvs_pool_titres
                  (id_pool, sheet_alias, source, feuille, ligne, semaine, jour,
                   song_id, artist, title, year, album,
                   artiste_saisi, titre_saisi, annee_saisie, album_saisi,
                   statut, score_match, date_validation)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'valide',100,NOW())
            """, (
                id_pool, sheets_alias_ou_id, source_majoritaire,
                r.get('_feuille'), r.get('_ligne'), r.get('_semaine'), r.get('_jour'),
                song_id_val,
                r.get('artist'), r.get('title'),
                str(r.get('year') or '') or None,
                r.get('comments') or None,
                sheet_artiste, sheet_titre,
                r.get('_sheet_annee') or None, r.get('_sheet_album') or None,
            ))
            inserts += cursor.rowcount

        for nf in non_trouves:
            # 2026-06-25-C : song_id=0 (au lieu de None) pour cohérence avec
            # le legacy worker et éviter tout souci même si la colonne retourne
            # à NOT NULL à l'avenir. Le schéma actuel accepte NULL/0.
            cursor.execute("""
                INSERT INTO airvs_pool_titres
                  (id_pool, sheet_alias, source, feuille, ligne, semaine, jour,
                   song_id, artiste_saisi, titre_saisi, annee_saisie, album_saisi,
                   statut, score_match, motif_rejet, date_validation)
                VALUES (%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,'rejete',0,'Aucun match dans songs',NOW())
            """, (
                id_pool, sheets_alias_ou_id, source_majoritaire,
                nf.get('feuille'), nf.get('ligne'), None, None,
                nf['artist'], nf['title'],
                nf.get('annee') or None, nf.get('album') or None,
            ))
            inserts += cursor.rowcount

        # ── B1.3 : Persister les manquants du pool dans airvs_manquants (flux 2) ──
        nb_manq_pool = 0
        for nf in non_trouves:
            try:
                cursor.execute(
                    "INSERT IGNORE INTO airvs_manquants "
                    "(artiste, titre, annee, album, source, sheet_alias, onglet, semaine, origine) "
                    "VALUES (%s, %s, %s, %s, 'prefill_sheets', %s, %s, %s, 'pool_validation')",
                    (
                        _normaliser_manquant((nf.get('artist') or ''))[:500],
                        _normaliser_manquant((nf.get('title') or ''))[:500],
                        nf.get('annee') or None,
                        nf.get('album') or None,
                        sheets_alias_ou_id,
                        nf.get('feuille') or onglet,
                        semaine_filter or None,
                    )
                )
                if cursor.rowcount > 0:
                    nb_manq_pool += 1
            except Exception:
                pass  # non bloquant
        if nb_manq_pool > 0:
            logger(tache_id, f"Manquants pool persistés : {nb_manq_pool} nouveau(x) dans airvs_manquants")

        db.commit()
        logger(tache_id, f"💾 {inserts} entrée(s) insérée(s) dans airvs_pool_titres")
    else:
        logger(tache_id, "🔍 DRY-RUN : aucune insertion en base")

    cursor.close()
    db.close()

    # 5. Récapitulatif des inversions (pour correction humaine)
    if inversions_detectees:
        logger(tache_id, f"━━━ RÉCAPITULATIF INVERSIONS ({len(inversions_detectees)} détectée(s)) ━━━")
        for inv in inversions_detectees:
            logger(tache_id, f"  • Sheet : ARTISTE='{inv['sheet_artiste']}' / TITRE='{inv['sheet_titre']}'")
            logger(tache_id, f"    → DB  : artist='{inv['db_artiste']}' / title='{inv['db_titre']}'")
        logger(tache_id, "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # 6. Rapport manquants sur disque (réutilisation du format Push)
    if non_trouves:
        log_dir = "logs_prefill"
        os.makedirs(log_dir, exist_ok=True)
        alias_propre = re.sub(r'[^\w\-_.]', '_', str(sheets_alias_ou_id))
        onglet_propre = re.sub(r'[^\w\-_.]', '_', str(onglet))
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        nom_fichier_log = f"pool_{alias_propre}_{onglet_propre}_{timestamp_str}.log"
        chemin_log = os.path.join(log_dir, nom_fichier_log)
        try:
            with open(chemin_log, "w", encoding="utf-8") as f:
                f.write(f"Rapport Validation Pool AIRVS - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Sheet Alias : {sheets_alias_ou_id}\n")
                f.write(f"Onglet      : {onglet}\n")
                f.write(f"Profil      : {source_majoritaire}\n")
                f.write(f"Total extraits : {len(titres_tous)}\n")
                f.write(f"Validés     : {len(resultats_trouves)}\n")
                f.write(f"Manquants   : {len(non_trouves)}\n")
                f.write("=" * 50 + "\n")
                for item in non_trouves:
                    ligne_log = item['display']
                    extras = []
                    if item.get('annee'):
                        extras.append(item['annee'])
                    if item.get('album'):
                        extras.append(item['album'])
                    if extras:
                        ligne_log += "  [" + " | ".join(extras) + "]"
                    if item.get('feuille'):
                        ligne_log += f"  (feuille={item['feuille']}, ligne={item.get('ligne', '?')})"
                    f.write(ligne_log + "\n")
            logger(tache_id, f"Log des manquants sauvegardé : {nom_fichier_log}")
        except Exception as e:
            logger(tache_id, f"Erreur lors de la création du log : {e}")
    else:
        logger(tache_id, "Aucun titre manquant, log non généré.")

    # 7. Payload JSON pour le dashboard (structure similaire au Push)
    json_payload = json.dumps({
        "found": [{"ID": r['ID'], "artist": r['artist'], "title": r['title'],
                    "year": r.get('year'), "_sheet_annee": r.get('_sheet_annee'),
                    "_sheet_album": r.get('_sheet_album')} for r in resultats_trouves],
        "not_found": non_trouves,
        "id_pool": id_pool,
        "source": source_majoritaire,
    }, ensure_ascii=False, default=str)
    logger(tache_id, "===POOL_JSON_START===")
    logger(tache_id, json_payload, raw_append=True)
    logger(tache_id, "===POOL_JSON_END===")

    logger(tache_id, "=== TERMINÉ VALIDATION POOL ===")


def executer_copie_sheets(tache_id, parametres):
    logger(tache_id, "L'outil structural via le Dashboard est désormais recommandé.")
    logger(tache_id, "=== TERMINÉ GOOGLE SHEETS ===")


def executer_import_sheets_couleur(tache_id, parametres):
    """Phase 3.3 — Importe des créneaux de programmation avancée depuis un Google Sheet.

    Lit un onglet Sheets contenant une ligne par créneau, parse les colonnes, et
    insère/maj dans airvs_grille_couleur (UPSERT sur heure+jour+station+type+valeur).

    Format attendu (voir docstring côté app.py / endpoint /import_sheets) :
      HEURE | JOUR | STATION | TYPE | VALEUR | NB | MODE | PLAYLIST_IDS |
      NOM_TEMPLATE | DOSSIER | ANTI_REP | PLAGE_DEBUT | PLAGE_FIN | DRY_RUN | ACTIF | COMMENTAIRE

    Si dry_run_import=True : parse et logge seulement, n'écrit pas en base.
    """
    sheets_alias_ou_id = parametres.get('sheets_id', '')
    onglet = parametres.get('onglet', '')
    dry_run_import = bool(parametres.get('dry_run_import', False))
    demande_par = parametres.get('demande_par', 'inconnu')

    logger(tache_id, "════════════════════════════════════════════")
    logger(tache_id, f"📥 IMPORT SHEETS → Programmation avancée (demandé par {demande_par})")
    logger(tache_id, f"  Sheet : {sheets_alias_ou_id}")
    logger(tache_id, f"  Onglet: {onglet}")
    logger(tache_id, f"  Mode  : {'DRY-RUN (parse seulement)' if dry_run_import else 'ÉCRITURE'}")
    logger(tache_id, "════════════════════════════════════════════")

    if not sheets_alias_ou_id or not onglet:
        logger(tache_id, "ERREUR: sheets_id et onglet requis")
        logger(tache_id, "=== TERMINÉ IMPORT SHEETS (ERREUR) ===")
        return

    # ── 1. Connexion Google Sheets ──
    try:
        sheets_id = resoudre_sheets_id(sheets_alias_ou_id)
        creds = Credentials.from_service_account_file(CREDENTIALS_JSON, scopes=SHEETS_SCOPES)
        client = gspread.authorize(creds)
        feuille = client.open_by_key(sheets_id).worksheet(onglet)
        lignes = feuille.get_all_records()
    except Exception as e:
        logger(tache_id, f"ERREUR FATALE Connexion Google Sheets : {e}")
        logger(tache_id, "=== TERMINÉ IMPORT SHEETS (ERREUR) ===")
        return

    if not lignes:
        logger(tache_id, "⚠ Aucune ligne dans l'onglet (Sheet vide ou sans en-têtes)")
        logger(tache_id, "=== TERMINÉ IMPORT SHEETS (VIDE) ===")
        return

    # ── 2. Diagnostic colonnes ──
    colonnes = list(lignes[0].keys())
    logger(tache_id, f"📋 {len(lignes)} ligne(s) — {len(colonnes)} colonne(s) : {colonnes}")

    # Helper : récupérer une valeur de colonne en tolérant casse/espaces
    def get_col(row, *candidates):
        for k in row.keys():
            if k and k.strip().upper() in [c.upper() for c in candidates]:
                return row.get(k, '')
        return ''

    # ── 3. Parse + UPSERT ──
    nb_crees, nb_maj, nb_erreurs, nb_skip = 0, 0, 0, 0
    db = pymysql.connect(**DB_CONFIG)
    cursor = db.cursor(pymysql.cursors.DictCursor)

    for idx, ligne in enumerate(lignes, start=2):  # start=2 car ligne 1 = en-têtes
        try:
            heure_raw = str(get_col(ligne, 'HEURE')).strip()
            if not heure_raw:
                nb_skip += 1
                continue
            # Normaliser HH:MM → HH:MM:SS
            if ':' in heure_raw and heure_raw.count(':') == 1:
                heure = heure_raw + ':00'
            else:
                heure = heure_raw

            jour_raw = str(get_col(ligne, 'JOUR')).strip() or '0'
            try:
                jour = int(float(jour_raw))
            except (ValueError, TypeError):
                jour = 0
            if not (0 <= jour <= 7):
                jour = 0

            station_raw = str(get_col(ligne, 'STATION')).strip() or '7'
            try:
                station_id = int(float(station_raw))
            except (ValueError, TypeError):
                station_id = 7

            type_couleur = str(get_col(ligne, 'TYPE')).strip().lower()
            if type_couleur not in ('artist', 'genre', 'subcategory', 'category', 'keyword', 'titre'):
                logger(tache_id, f"  L{idx}: TYPE invalide '{type_couleur}' — skip")
                nb_erreurs += 1
                continue

            valeur_couleur = str(get_col(ligne, 'VALEUR')).strip()
            if type_couleur != 'titre' and not valeur_couleur:
                logger(tache_id, f"  L{idx}: VALEUR vide pour type={type_couleur} — skip")
                nb_erreurs += 1
                continue

            nb_raw = str(get_col(ligne, 'NB')).strip() or '20'
            try:
                nb_titres = int(float(nb_raw))
            except (ValueError, TypeError):
                nb_titres = 20

            mode = str(get_col(ligne, 'MODE')).strip().lower() or 'ajouter_existantes'
            if mode not in ('ajouter_existantes', 'creer_nouvelle', 'remplacer'):
                mode = 'ajouter_existantes'

            # Playlist IDs : accepter "[12,15]" ou "12,15" ou "12;15" ou "12"
            pl_ids_raw = str(get_col(ligne, 'PLAYLIST_IDS')).strip()
            playlist_ids_json = None
            if pl_ids_raw:
                pl_clean = pl_ids_raw.strip('[]').replace(';', ',').replace(' ', '')
                try:
                    pl_list = [int(float(x)) for x in pl_clean.split(',') if x]
                    playlist_ids_json = json.dumps(pl_list) if pl_list else None
                except ValueError:
                    logger(tache_id, f"  L{idx}: PLAYLIST_IDS invalide '{pl_ids_raw}' — ignoré")
            if mode in ('ajouter_existantes', 'remplacer') and not playlist_ids_json:
                logger(tache_id, f"  L{idx}: ⚠ MODE={mode} sans PLAYLIST_IDS — sera sans effet")

            nom_template = str(get_col(ligne, 'NOM_TEMPLATE')).strip() or None
            if mode == 'creer_nouvelle' and not nom_template:
                logger(tache_id, f"  L{idx}: ⚠ MODE=creer_nouvelle sans NOM_TEMPLATE — utilisera défaut")
                nom_template = None

            dossier_cible = str(get_col(ligne, 'DOSSIER')).strip() or 'imports_push'

            anti_raw = str(get_col(ligne, 'ANTI_REP')).strip() or '7'
            try:
                anti_rep = int(float(anti_raw))
            except (ValueError, TypeError):
                anti_rep = 7

            plage_deb_raw = str(get_col(ligne, 'PLAGE_DEBUT')).strip() or '06:00'
            plage_fin_raw = str(get_col(ligne, 'PLAGE_FIN')).strip() or '23:00'
            plage_h_debut = plage_deb_raw + ':00' if plage_deb_raw.count(':') == 1 else plage_deb_raw
            plage_h_fin = plage_fin_raw + ':00' if plage_fin_raw.count(':') == 1 else plage_fin_raw

            dry_raw = str(get_col(ligne, 'DRY_RUN')).strip() or '1'
            try:
                dry_run = int(float(dry_raw))
            except (ValueError, TypeError):
                dry_run = 1
            dry_run = 1 if dry_run != 0 else 0

            actif_raw = str(get_col(ligne, 'ACTIF')).strip() or '1'
            try:
                actif = int(float(actif_raw))
            except (ValueError, TypeError):
                actif = 1
            actif = 1 if actif != 0 else 0

            commentaire = str(get_col(ligne, 'COMMENTAIRE')).strip() or None

            if dry_run_import:
                # Dry-run : juste log
                logger(tache_id, f"  L{idx}: {heure[:5]} J{jour} S{station_id} {type_couleur}='{valeur_couleur[:30]}' "
                                 f"nb={nb_titres} mode={mode} → SERAIT CRÉÉ/MÀJ")
                nb_crees += 1  # en dry-run on compte tout comme "serait créé"
                continue

            # UPSERT : chercher par (heure, jour, station, type, valeur)
            cursor.execute(
                "SELECT id FROM airvs_grille_couleur "
                "WHERE heure = %s AND jour_semaine = %s AND station_id = %s "
                "AND type_couleur = %s AND valeur_couleur = %s",
                (heure, jour, station_id, type_couleur, valeur_couleur)
            )
            existing = cursor.fetchone()
            if existing:
                # Mise à jour
                existing_id = existing['id']
                cursor.execute(
                    "UPDATE airvs_grille_couleur SET "
                    "nb_titres=%s, mode_playlist=%s, playlist_ids=%s, "
                    "nom_nouvelle_playlist=%s, dossier_cible=%s, "
                    "anti_repetition_jours=%s, plage_h_debut=%s, plage_h_fin=%s, "
                    "dry_run=%s, actif=%s, commentaire=%s "
                    "WHERE id=%s",
                    (nb_titres, mode, playlist_ids_json, nom_template, dossier_cible,
                     anti_rep, plage_h_debut, plage_h_fin, dry_run, actif, commentaire,
                     existing_id)
                )
                logger(tache_id, f"  L{idx}: ✓ MÀJ créneau #{existing_id} ({heure[:5]} {type_couleur}='{valeur_couleur[:30]}')")
                nb_maj += 1
            else:
                # Création
                cursor.execute(
                    "INSERT INTO airvs_grille_couleur "
                    "(jour_semaine, heure, station_id, type_couleur, valeur_couleur, "
                    " nb_titres, mode_playlist, playlist_ids, nom_nouvelle_playlist, "
                    " dossier_cible, anti_repetition_jours, plage_h_debut, plage_h_fin, "
                    " dry_run, actif, commentaire) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (jour, heure, station_id, type_couleur, valeur_couleur,
                     nb_titres, mode, playlist_ids_json, nom_template,
                     dossier_cible, anti_rep, plage_h_debut, plage_h_fin,
                     dry_run, actif, commentaire)
                )
                new_id = cursor.lastrowid
                logger(tache_id, f"  L{idx}: ✓ CRÉÉ créneau #{new_id} ({heure[:5]} {type_couleur}='{valeur_couleur[:30]}')")
                nb_crees += 1

        except Exception as e:
            logger(tache_id, f"  L{idx}: ✗ Erreur parse/écriture : {e}")
            nb_erreurs += 1

    if not dry_run_import:
        db.commit()
    cursor.close()
    db.close()

    logger(tache_id, f"── Bilan import ──")
    logger(tache_id, f"  Créés/MÀJ : {nb_crees + nb_maj} ({nb_crees} nouveaux, {nb_maj} maj)")
    logger(tache_id, f"  Erreurs   : {nb_erreurs}")
    logger(tache_id, f"  Skippés   : {nb_skip} (lignes vides)")
    logger(tache_id, "=== TERMINÉ IMPORT SHEETS ===")


# ═══════════════════════════════════════════════════════════════════════
# VALIDATION DES SUSPECTS (E1) : Rejet — suppression fichier
# ═══════════════════════════════════════════════════════════════════════

# IP et user SSH du serveur Debian (stockage physique des fichiers suspects)
DEBIAN_SSH_HOST = "192.168.1.30"
DEBIAN_SSH_USER = "sebastien"


def executer_suspect_reject(tache_id, parametres):
    """Supprime un fichier suspect sur le stockage Debian.

    Le chemin (filepath) est fourni en format Linux SSHFS
    (ex: /mnt/stockage_160go/Musique/import/suspects/titre.mp3).

    Stratégie :
      1. Suppression directe via le mount SSHFS (rapide, pas de latence SSH)
      2. Si le mount est inaccessible, fallback SSH vers Debian
    """
    filepath = parametres.get('filepath', '')
    original_filename = parametres.get('original_filename', '')

    if not filepath:
        logger(tache_id, "ERREUR : parametre 'filepath' manquant.")
        raise RuntimeError("Parametre 'filepath' manquant")

    logger(tache_id, f"Suppression suspect : {original_filename or filepath}")

    # ── 1. Tentative directe via SSHFS ──
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            logger(tache_id, f"Fichier supprime directement (SSHFS) : {filepath}")
            logger(tache_id, "=== TERMINÉ SUSPECT_REJECT ===")
            return
        else:
            logger(tache_id, f"Fichier absent du mount SSHFS : {filepath}")
            logger(tache_id, "Tentative via SSH vers Debian...")
    except PermissionError as e:
        logger(tache_id, f"Permission refusee sur SSHFS : {e}")
        logger(tache_id, "Tentative via SSH vers Debian...")
    except OSError as e:
        logger(tache_id, f"Erreur OS sur SSHFS : {e}")
        logger(tache_id, "Tentative via SSH vers Debian...")

    # ── 2. Fallback SSH vers Debian ──
    # Le filepath est deja en format Linux, on l'utilise tel quel.
    safe_path = shlex.quote(filepath)
    ssh_cmd = [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
        f"{DEBIAN_SSH_USER}@{DEBIAN_SSH_HOST}",
        f"rm -f {safe_path}"
    ]
    try:
        result = subprocess.run(
            ssh_cmd, capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            logger(tache_id, f"Fichier supprime via SSH : {filepath}")
            logger(tache_id, "=== TERMINÉ SUSPECT_REJECT ===")
        else:
            stderr = (result.stderr or "").strip()
            logger(tache_id, f"ERREUR SSH (code {result.returncode}) : {stderr}")
            raise RuntimeError(f"SSH rm a echoue (code {result.returncode}): {stderr}")
    except subprocess.TimeoutExpired:
        logger(tache_id, "ERREUR : SSH timeout (15s)")
        raise RuntimeError("SSH vers Debian timeout")
    except Exception as e:
        logger(tache_id, f"ERREUR SSH : {e}")
        raise


def executer_copie(tache_id, parametres):
    destination = parametres.get('destination')
    fichiers = parametres.get('fichiers', [])
    dossier_nom = parametres.get('dossier_nom', '')
    ajouter_sous_dossier_date = parametres.get('ajouter_sous_dossier_date', True)
    # station_id (multi-station). Fallback sur station 7 pour les tâches anciennes.
    station_id = parametres.get('station_id', _DEFAULT_STATION_ID)
    try:
        station_id = int(station_id)
    except (ValueError, TypeError):
        station_id = _DEFAULT_STATION_ID

    # Garde-fou : si 'destination' est absent/None, on n'essaie surtout pas
    # de faire os.path.join(None, dossier_nom) qui lèverait un TypeError obscur
    # ("expected str, bytes or os.PathLike object, not NoneType").
    if not destination:
        logger(tache_id, "ERREUR: paramètre 'destination' manquant dans la tâche COPIE. "
                         "Abandon (aucun fichier copié).")
        logger(tache_id, f"=== TERMINÉ === 0 copié(s), 0 exclu(s), 0 erreur(s). ===")
        return

    if not dossier_nom and ajouter_sous_dossier_date:
        dossier_nom = datetime.now().strftime("%Y-%m-%d_Copie")
    if dossier_nom:
        destination_finale = os.path.join(destination, dossier_nom)
    else:
        destination_finale = destination

    logger(tache_id, f"Dossier cible final : {destination_finale} (station {station_id})")

    if not destination_finale or not fichiers:
        logger(tache_id, "ERREUR: Paramètres manquants.")
        return

    copies, erreurs, exclus = 0, 0, 0

    if destination.startswith(AZURA_SSHFS_ROOT):
        logger(tache_id, f"Interrogation API AzuraCast (station {station_id}) pour vérifier les doublons globaux...")
        titres_deja_azura = obtenir_titres_deja_azuracast(station_id)
        logger(tache_id, f"{len(titres_deja_azura)} titres référencés dans AzuraCast (station {station_id}).")

        # Création de l'arborescence via SFTP (un niveau à la fois)
        chemin_relatif = destination_finale[len(AZURA_SSHFS_ROOT):].lstrip('/')
        if chemin_relatif:
            creer_arborescence_sftp(chemin_relatif)
        logger(tache_id, f"Dossier distant préparé : {chemin_relatif}")

        for fich in fichiers:
            # ── Vérification d'annulation push ──
            if check_push_cancel(tache_id):
                logger(tache_id, f"=== ANNULÉ === {copies} copié(s), {exclus} exclu(s), {erreurs} erreur(s) ===")
                return
            chemin_windows = fich.get('path', '').replace("/", "\\")
            artiste, titre = fich.get('artist', '?'), fich.get('title', '?')
            details = f"{artiste} - {titre}"
            try:
                src_path = windows_vers_linux(chemin_windows)
            except ValueError as e:
                logger(tache_id, f"[ERREUR TRADUCTION] {details} -> {e}"); erreurs += 1; continue
            if not src_path.exists():
                # Tentative de résolution insensible à la casse (ex: .MP3 vs .mp3)
                src_path_resolved = resoudre_chemin_insensible(src_path)
                if not src_path_resolved.exists():
                    logger(tache_id, f"[ABSENT SUR DISQUE] {details} -> {src_path}"); erreurs += 1; continue
                else:
                    logger(tache_id, f"[CHEMIN RÉSOLU] {details} -> {src_path_resolved}")
                    src_path = src_path_resolved
            clean_name = normaliser_pour_recherche(f"{artiste.lower()} - {titre.lower()}")
            if clean_name in titres_deja_azura:
                logger(tache_id, f"[DÉJÀ DANS AZURACAST] {details} -> Copie annulée."); exclus += 1; continue
            dst_path_str = destination_finale + "/" + src_path.name
            succes, message = copier_vers_azuracast_sftp(src_path, dst_path_str)
            if succes:
                logger(tache_id, f"[OK SFTP] {details} -> {src_path.name}"); copies += 1
            else:
                logger(tache_id, f"[ÉCHEC SFTP] {details} -> {message}"); erreurs += 1
    else:
        try:
            os.makedirs(destination_finale, exist_ok=True)
        except Exception as e:
            logger(tache_id, f"ERREUR FATALE: Impossible de créer le dossier {destination_finale} ({e})")
            return

        for fich in fichiers:
            # ── Vérification d'annulation push ──
            if check_push_cancel(tache_id):
                logger(tache_id, f"=== ANNULÉ === {copies} copié(s), {exclus} exclu(s), {erreurs} erreur(s) ===")
                return
            chemin_windows = fich.get('path', '').replace("/", "\\")
            details = f"{fich.get('artist', '?')} - {fich.get('title', '?')}"

            try:
                src_path = windows_vers_linux(chemin_windows)
            except ValueError as e:
                logger(tache_id, f"[ERREUR TRADUCTION] {details} -> {e}")
                erreurs += 1
                continue

            if not src_path.exists():
                # Tentative de résolution insensible à la casse (ex: .MP3 vs .mp3)
                src_path_resolved = resoudre_chemin_insensible(src_path)
                if not src_path_resolved.exists():
                    logger(tache_id, f"[ABSENT SUR DISQUE] {details} -> {src_path}")
                    erreurs += 1
                    continue
                else:
                    logger(tache_id, f"[CHEMIN RÉSOLU] {details} -> {src_path_resolved}")
                    src_path = src_path_resolved

            try:
                dst_path = Path(destination_finale) / src_path.name
                if dst_path.exists():
                    stem, suffix = dst_path.stem, dst_path.suffix
                    compteur = 1
                    while True:
                        nouveau_nom = dst_path.with_name(f"{stem}_{compteur}{suffix}")
                        if not nouveau_nom.exists():
                            dst_path = nouveau_nom
                            break
                        compteur += 1

                shutil.copy2(src_path, dst_path)
                logger(tache_id, f"[OK] {details} -> {dst_path.name}")
                copies += 1
            except Exception as e:
                logger(tache_id, f"[ÉCHEC COPIE] {details} -> {e}")
                erreurs += 1

    logger(tache_id, f"=== TERMINÉ === {copies} copié(s), {exclus} exclu(s) doublon AzuraCast, {erreurs} erreur(s). ===")


# ── Annulation de push en cours ──
def check_push_cancel(tache_id):
    """Vérifie si un flag PUSH_CANCEL existe pour cette tâche de push.
    Si oui, le consomme (passe à 'annule') et retourne True.
    Ouvre sa propre connexion DB (le worker n'a pas de connexion globale permanente)."""
    try:
        _db = pymysql.connect(**DB_CONFIG)
        chk = _db.cursor(pymysql.cursors.DictCursor)
        chk.execute(
            "SELECT id FROM taches_planifiees "
            "WHERE type_action = 'PUSH_CANCEL' "
            "AND statut = 'en_attente' "
            "AND JSON_EXTRACT(parametres, '$.push_task_id') = %s "
            "ORDER BY id DESC LIMIT 1",
            (tache_id,)
        )
        row = chk.fetchone()
        chk.close()
        if row:
            clr = _db.cursor()
            clr.execute(
                "UPDATE taches_planifiees SET statut = 'annule' WHERE id = %s",
                (row['id'],)
            )
            _db.commit()
            clr.close()
            _db.close()
            return True
        _db.close()
    except Exception:
        pass  # Ne pas interrompre le push pour une vérification échouée
    return False


# ══════════════════════════════════════════════════════
# TÂCHES AZURACAST API : PLAYLISTS
# ══════════════════════════════════════════════════════

def _extraire_donnees_playlists(raw_playlists):
    """
    Transforme les données brutes de l'API AzuraCast en données de cache.
    Utilise les champs réels de l'API : 'num_songs' et 'total_length'.
    L'API AzuraCast (GET /playlists) retourne notamment :
      - id, name, type, source, order, is_enabled
      - num_songs (nombre de titres)
      - total_length (durée totale en secondes)
    """
    resultats = []
    for p in raw_playlists:
        nb_tracks = int(p.get('num_songs', 0) or 0)
        total_duration = float(p.get('total_length', 0) or 0)
        resultats.append({
            'azura_id': p.get('id'),
            'name': p.get('name', ''),
            'type': p.get('type', 'default'),
            'source': p.get('source', 'songs'),
            'order': p.get('order', 'shuffle'),
            'is_enabled': 1 if p.get('is_enabled', False) else 0,
            'nb_tracks': nb_tracks,
            'total_duration': total_duration,
        })
    return resultats


def _assurer_cache_playlists_schema(default_station_id=None):
    """S'assure que la table azuracast_playlists_cache existe AVEC la colonne station_id
    (multi-station) et une UNIQUE KEY sur (azura_id, station_id).
    
    Sans cette colonne, l'API /api/azuracast/playlists ne filtre pas par station,
    toutes les playlists reviennent à chaque appel, et le badge "Station" du
    frontend affiche "?".
    
    Args:
        default_station_id: ID de station par défaut à utiliser pour les lignes
                            existantes lors de la migration (NULL → _DEFAULT_STATION_ID).
    """
    if default_station_id is None:
        default_station_id = _DEFAULT_STATION_ID
    try:
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor()
        
        # 1. Créer la table si elle n'existe pas du tout (avec colonne station_id)
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS azuracast_playlists_cache (
                id INT AUTO_INCREMENT PRIMARY KEY,
                azura_id INT NOT NULL,
                name VARCHAR(255) NOT NULL,
                type VARCHAR(50),
                source VARCHAR(50),
                `order` VARCHAR(20),
                is_enabled TINYINT(1) DEFAULT 0,
                nb_tracks INT DEFAULT 0,
                total_duration INT DEFAULT 0,
                date_sync DATETIME DEFAULT CURRENT_TIMESTAMP,
                station_id TINYINT NOT NULL DEFAULT %s,
                UNIQUE KEY uq_azura_station (azura_id, station_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
            (default_station_id,)
        )
        
        # 2. Détecter si la table préexistait SANS la colonne station_id (migration)
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'azuracast_playlists_cache' "
            "AND column_name = 'station_id'"
        )
        col_existe = cursor.fetchone()
        has_station_col = (
            (isinstance(col_existe, dict) and col_existe.get('COUNT(*)', 0) > 0) or
            (isinstance(col_existe, (list, tuple)) and col_existe[0] > 0)
        )
        
        if not has_station_col:
            # Migration douce : ajouter la colonne station_id
            try:
                cursor.execute(
                    f"ALTER TABLE azuracast_playlists_cache "
                    f"ADD COLUMN station_id TINYINT NOT NULL DEFAULT {int(default_station_id)}"
                )
                print(f"[MIGRATION] Colonne 'station_id' ajoutée à azuracast_playlists_cache "
                      f"(default={default_station_id}). Les anciennes lignes sont associées "
                      f"à la station {default_station_id}.")
            except Exception as e:
                print(f"[MIGRATION] Impossible d'ajouter 'station_id' à azuracast_playlists_cache : {e}")
        
        # 3. Ajouter l'UNIQUE KEY si elle n'existe pas (permet l'UPSERT via INSERT ... ON DUPLICATE KEY)
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = 'azuracast_playlists_cache' "
                "AND index_name = 'uq_azura_station'"
            )
            idx_existe = cursor.fetchone()
            has_idx = (
                (isinstance(idx_existe, dict) and idx_existe.get('COUNT(*)', 0) > 0) or
                (isinstance(idx_existe, (list, tuple)) and idx_existe[0] > 0)
            )
            if not has_idx:
                cursor.execute(
                    "ALTER TABLE azuracast_playlists_cache "
                    "ADD UNIQUE KEY uq_azura_station (azura_id, station_id)"
                )
                print("[MIGRATION] UNIQUE KEY uq_azura_station ajoutée à azuracast_playlists_cache.")
        except Exception as e:
            # Pas bloquant : si l'index ne peut pas être ajouté (doublons existants ?), on continue
            print(f"[MIGRATION] Impossible d'ajouter l'UNIQUE KEY uq_azura_station : {e}")
        
        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        print(f"[MIGRATION] Erreur _assurer_cache_playlists_schema : {e}")


def _inserer_cache_playlists_sql(cache_data, station_id=None):
    """Insère les données de cache dans la table SQL azuracast_playlists_cache.
    Si station_id est fourni, ne supprime que les lignes de cette station (multi-station).
    Sinon, fallback : vide toute la table (compatibilité descendante)."""
    if station_id is None:
        station_id = _DEFAULT_STATION_ID
    db = pymysql.connect(**DB_CONFIG)
    cursor = db.cursor()
    # Détecter si la colonne station_id existe (migration multi-station)
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'azuracast_playlists_cache' "
            "AND column_name = 'station_id'"
        )
        col_existe = cursor.fetchone()
        has_station_col = (
            (isinstance(col_existe, dict) and col_existe.get('COUNT(*)', 0) > 0) or
            (isinstance(col_existe, (list, tuple)) and col_existe[0] > 0)
        )
    except Exception:
        has_station_col = False

    if has_station_col:
        cursor.execute(
            "DELETE FROM azuracast_playlists_cache WHERE station_id = %s",
            (station_id,)
        )
        for pl in cache_data:
            cursor.execute(
                """INSERT INTO azuracast_playlists_cache
                   (azura_id, name, type, source, `order`, is_enabled, nb_tracks, total_duration, date_sync, station_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)""",
                (pl['azura_id'], pl['name'], pl['type'], pl['source'], pl['order'],
                 pl['is_enabled'], pl['nb_tracks'], pl['total_duration'], station_id)
            )
    else:
        # Compatibilité descendante : ancienne table sans colonne station_id
        cursor.execute("DELETE FROM azuracast_playlists_cache")
        for pl in cache_data:
            cursor.execute(
                """INSERT INTO azuracast_playlists_cache
                   (azura_id, name, type, source, `order`, is_enabled, nb_tracks, total_duration, date_sync)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
                (pl['azura_id'], pl['name'], pl['type'], pl['source'], pl['order'],
                 pl['is_enabled'], pl['nb_tracks'], pl['total_duration'])
            )
    db.commit()
    cursor.close()
    db.close()


def _construire_dictionnaire_chemins_azuracast(station_id=None):
    """Interroge l'API AzuraCast pour construire un dictionnaire :
    normalisé('artiste - titre') → chemin_réel_dans_azuracast.
    Permet de résoudre le chemin des fichiers déjà présents pour les ajouter à une playlist.

    Args:
        station_id: ID de la station (ex: 6 ou 7). Si None, utilise la station par défaut (7).
    """
    if station_id is None:
        station_id = _DEFAULT_STATION_ID
    try:
        api_url, api_key = _url_key_for_station(station_id)
        headers = {"Authorization": f"Bearer {api_key}"}
        params = {"limit": 10000}
        resp = requests.get(api_url, headers=headers, params=params, timeout=60)
        if resp.status_code != 200:
            return {}

        data = resp.json()
        rows = data if isinstance(data, list) else data.get("rows", [])

        dictionnaire = {}
        for morceau in rows:
            if not isinstance(morceau, dict):
                continue
            artist = str(morceau.get("artist", "") or "").strip()
            title = str(morceau.get("title", "") or "").strip()
            path = str(morceau.get("path", "") or "").strip()
            if artist and title and path:
                cle = normaliser_pour_recherche(f"{artist} - {title}")
                dictionnaire[cle] = path
        return dictionnaire
    except Exception:
        return {}



# ═══════════════════════════════════════════════════════════════
# SYNC GRILLE WEB — Pousse grille.json vers l'hébergement airvs.fr
# ═══════════════════════════════════════════════════════════════
def executer_sync_grille_web(tache_id, parametres):
    """Sync la grille éditoriale vers le site airvs.fr via FTP.

    Deux modes selon les parametres :
      - Mode HTTP  (dev)  : tire grille.json depuis le dashboard dev (token)
      - Mode Fichier     : lit un fichier grille.json local (fallback)

    Puis pousse via FTP vers l'hebergement web.

    Parametres attendus dans tache['parametres'] :
      - dashboard_url    : URL du dashboard dev (ex: http://192.168.1.30:5050)
      - api_token        : Token API du dashboard dev
      - local_file       : (optionnel) Chemin local du grille.json en fallback
      - ftp_host         : Hote FTP de l'hebergement web
      - ftp_user         : Utilisateur FTP
      - ftp_pass         : Mot de passe FTP
      - ftp_remote_path  : Chemin distant (ex: /www/airvs.fr/)
    """
    dashboard_url = parametres.get('dashboard_url', '').rstrip('/')
    api_token = parametres.get('api_token', '')
    local_file = parametres.get('local_file', '')
    ftp_host = parametres.get('ftp_host', '')
    ftp_user = parametres.get('ftp_user', '')
    ftp_pass = parametres.get('ftp_pass', '')
    ftp_remote_path = parametres.get('ftp_remote_path', '/')

    logger(tache_id, "=== SYNC GRILLE WEB ===")

    # ── Etape 1 : obtenir le JSON de la grille ──
    grille_content = None
    source_desc = ""

    # Mode 1 : HTTP depuis le dashboard dev
    if dashboard_url and api_token:
        url = f"{dashboard_url}/api/grille_editoriale/grille.json?token={api_token}"
        logger(tache_id, f"Tirage HTTP depuis : {dashboard_url}/api/grille_editoriale/grille.json")
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                grille_content = resp.text
                source_desc = f"HTTP ({dashboard_url})"
                logger(tache_id, f"  OK - {len(grille_content)} octets recus")
            else:
                logger(tache_id, f"  Echec HTTP : status {resp.status_code}")
        except requests.RequestException as e:
            logger(tache_id, f"  Echec HTTP : {e}")

    # Mode 2 : fichier local (fallback ou mode fichier direct)
    if not grille_content and local_file:
        logger(tache_id, f"Tentative lecture fichier local : {local_file}")
        try:
            local_resolved = windows_vers_linux(local_file)
            with open(local_resolved, 'r', encoding='utf-8') as f:
                grille_content = f.read()
            source_desc = f"fichier ({local_file})"
            logger(tache_id, f"  OK - {len(grille_content)} octets lus")
        except Exception as e:
            logger(tache_id, f"  Echec lecture fichier : {e}")

    if not grille_content:
        logger(tache_id, "ECHEC : impossible d'obtenir la grille (ni HTTP, ni fichier)")
        raise RuntimeError("Impossible d'obtenir la grille - verifiez dashboard_url/api_token ou local_file")

    # Verifier que c'est du JSON valide
    try:
        data = json.loads(grille_content)
        # Format 1 (route /api/grille_editoriale/data du dashboard) :
        #   {"data": {"schedule": {"lundi": {"blocks": [...]}, ...}, "meta": {"label": "..."}}}
        # Format 2 (route /api/grille_editoriale/grille.json pour le worker,
        #           fonction _ge_generer_grille_dict) :
        #   {"version": "...", "jours": {"1": [...blocs...], ...}, "meta": {"nb_blocs": N}}
        # On supporte les deux formats pour la validation.
        if 'data' in data and 'schedule' in data.get('data', {}):
            # Format 1 (dashboard /data)
            nb_blocs = sum(len(d.get('blocks', [])) for d in data['data']['schedule'].values())
            meta_label = data['data'].get('meta', {}).get('label', '?')
        elif 'schedule' in data:
            # Format 1 variante sans enrobage data
            nb_blocs = sum(len(d.get('blocks', [])) for d in data['schedule'].values())
            meta_label = data.get('meta', {}).get('label', '?')
        elif 'jours' in data:
            # Format 2 (worker /grille.json)
            nb_blocs = sum(len(blocs) for blocs in data['jours'].values())
            meta_label = data.get('version', '?')
        else:
            nb_blocs = 0
            meta_label = '?'
        logger(tache_id, f"Grille valide - version '{meta_label}', {nb_blocs} bloc(s)")
    except json.JSONDecodeError as e:
        logger(tache_id, f"ECHEC : JSON invalide - {e}")
        raise RuntimeError(f"grille.json invalide : {e}")

    # ── Etape 2 : envoi FTP vers l'hebergement ──
    # OVH utilise FTP sur TLS explicite (port 21 + STARTTLS) — ftplib.FTP_TLS
    # gère ce protocole nativement. On garde ftplib.FTP (plain) en fallback
    # au cas où l'hébergement ne supporte pas TLS.
    if not ftp_host or not ftp_user:
        logger(tache_id, "ECHEC : ftp_host et ftp_user sont requis dans les parametres")
        raise RuntimeError("ftp_host et ftp_user requis")

    logger(tache_id, f"Connexion FTP : {ftp_user}@{ftp_host}")

    # Tentative 1 : FTP sur TLS explicite (protocole OVH par défaut)
    ftp = None
    connexion_mode = None
    try:
        ftp = ftplib.FTP_TLS(timeout=30)
        ftp.connect(ftp_host, 21)
        ftp.login(ftp_user, ftp_pass)
        ftp.prot_p()  # Active la protection des données sur le canal chiffré (obligatoire après login en FTP-TLS)
        connexion_mode = "FTP-TLS explicite"
        logger(tache_id, f"  Connexion FTP-TLS établie (mode sécurisé)")
    except ftplib.all_errors as e_tls:
        logger(tache_id, f"  FTP-TLS échoué : {e_tls}")
        logger(tache_id, f"  Tentative fallback : FTP plain (non chiffré)")
        # Tentative 2 : FTP plain (fallback)
        try:
            ftp = ftplib.FTP(timeout=30)
            ftp.connect(ftp_host, 21)
            ftp.login(ftp_user, ftp_pass)
            connexion_mode = "FTP plain"
            logger(tache_id, f"  Connexion FTP plain établie (mode non sécurisé)")
        except ftplib.all_errors as e_plain:
            logger(tache_id, f"  FTP plain échoué : {e_plain}")
            raise RuntimeError(f"Erreur FTP : TLS={e_tls} / plain={e_plain}")

    try:
        # Naviguer vers le dossier distant (creer si necessaire)
        ftp_remote_path = ftp_remote_path.strip('/')
        if ftp_remote_path:
            for part in ftp_remote_path.split('/'):
                try:
                    ftp.cwd(part)
                except ftplib.error_perm:
                    ftp.mkd(part)
                    ftp.cwd(part)

        # Stocker en memoire et uploader
        ftp.storbinary(
            'STOR grille.json',
            BytesIO(grille_content.encode('utf-8'))
        )
        ftp.quit()

        logger(tache_id, f"grille.json publie avec succes sur {ftp_host} (source : {source_desc}, mode : {connexion_mode})")
        logger(tache_id, "=== SYNC GRILLE WEB TERMINE ===")

    except ftplib.all_errors as e:
        logger(tache_id, f"ECHEC FTP : {e}")
        raise RuntimeError(f"Erreur FTP : {e}")


# ═══════════════════════════════════════════════════════════════════════
# ═══ BULK SCHEDULE AZURACAST ══════════════════════════════════════════
# Modifie en masse la planification de plusieurs playlists AzuraCast.
# 3 modes : insert_event, shift, json_override.
# 3 sous-modes : list (récupérer playlists), preview (dry-run), apply.
# ═══════════════════════════════════════════════════════════════════════

# Configuration AzuraCast API (déduite depuis les variables d'environnement)
def _bulk_get_azura_config():
    """Retourne (base_url, api_key) pour l'API AzuraCast.
    Réutilise la configuration existante du worker (config.json + constantes)."""
    try:
        base_url, api_key = _url_key_for_station(1)  # station 1 pour récupérer base_url
        # _url_key_for_station retourne /api/station/X/files — on veut /api
        import re as _re
        m = _re.match(r'^(https?://[^/]+)/api/', base_url)
        if m:
            return m.group(1) + '/api', api_key
        if '/api' in base_url:
            return base_url.split('/api')[0] + '/api', api_key
        return base_url.rstrip('/'), api_key
    except Exception:
        # Fallback sur les constantes
        base_url = AZURA_API_URL
        m = __import__('re').match(r'^(https?://[^/]+)/api/', base_url)
        if m:
            return m.group(1) + '/api', AZURA_API_KEY
        return base_url, AZURA_API_KEY


def _bulk_azura_request(method, path, json_payload=None, timeout=30):
    """Appel HTTP vers l'API AzuraCast via la bibliothèque requests (comme le reste du worker).
    Utilise X-API-Key (format attendu par AzuraCast v4 pour les endpoints /playlists)."""
    base_url, api_key = _bulk_get_azura_config()
    url = base_url + path

    headers = {
        'X-API-Key': api_key,
        'Authorization': f'Bearer {api_key}',  # fallback pour certains endpoints
        'Accept': 'application/json',
    }
    kwargs = {'headers': headers, 'timeout': timeout}
    if json_payload is not None:
        kwargs['json'] = json_payload

    try:
        resp = requests.request(method, url, **kwargs)
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, resp.text
    except requests.RequestException as e:
        return 0, str(e)


def _bulk_list_playlists(station_id):
    status, data = _bulk_azura_request('GET', f'/station/{station_id}/playlists')
    if status != 200:
        raise RuntimeError(f"GET playlists station {station_id} -> HTTP {status}: {data}")
    return data if isinstance(data, list) else []


def _bulk_get_playlist(station_id, playlist_id):
    status, data = _bulk_azura_request('GET', f'/station/{station_id}/playlists/{playlist_id}')
    if status != 200:
        raise RuntimeError(f"GET playlist {playlist_id} -> HTTP {status}: {data}")
    return data if isinstance(data, dict) else {}


def _bulk_update_playlist(station_id, playlist_id, payload):
    """Met à jour une playlist via PUT.
    URL: /station/{id}/playlist/{pid} (playlist SINGULIER, d'après azuracast_api.py)."""
    status, data = _bulk_azura_request(
        'PUT', f'/station/{station_id}/playlist/{playlist_id}', json_payload=payload
    )
    if status not in (200, 201, 204):
        raise RuntimeError(f"PUT playlist {playlist_id} -> HTTP {status}: {data}")


def _bulk_parse_time_to_minutes(t):
    """Convertit un temps en minutes depuis minuit.
    Gère 3 formats :
    - Entier AzuraCast : 600 → 06:00 → 360 min, 1100 → 11:00 → 660 min
    - String 'HH:MM' : '06:00' → 360 min
    - String 'HH:MM:SS' : '06:00:00' → 360 min
    """
    if isinstance(t, (int, float)):
        # Format AzuraCast : entier HHMM (ex: 600 = 06:00, 1100 = 11:00, 1330 = 13:30)
        t_int = int(t)
        hours = t_int // 100
        minutes = t_int % 100
        return hours * 60 + minutes
    # Format string
    parts = str(t).split(':')
    if len(parts) >= 2:
        return int(parts[0]) * 60 + int(parts[1])
    return 0


def _bulk_minutes_to_azura_int(minutes):
    """Convertit des minutes depuis minuit vers le format entier AzuraCast (HHMM).
    Ex: 360 → 600, 660 → 1100, 840 → 1400
    """
    hours = minutes // 60
    mins = minutes % 60
    return hours * 100 + mins


def _bulk_build_payload(current, schedule_cfg=None):
    """Construit le payload PUT en mode WHITELIST — n'envoie que les champs
    autorisés en écriture par AzuraCast. Évite les HTTP 500 causés par les
    champs read-only (station_id, podcasts, etc.)."""

    WRITABLE = {
        'name', 'is_enabled', 'playback_mode', 'weight',
        'include_in_requests', 'include_in_automation',
        'avoid_duplicates', 'duplicate_prevention_time',
        'schedule_items',
    }

    payload = {k: v for k, v in current.items() if k in WRITABLE}

    if schedule_cfg:
        clean = {k: v for k, v in schedule_cfg.items() if not str(k).startswith('_')}
        if 'schedule_items' in clean:
            payload['schedule_items'] = [
                {k: v for k, v in item.items() if not str(k).startswith('_')}
                for item in clean['schedule_items']
            ]
        for k, v in clean.items():
            if k != 'schedule_items' and k in WRITABLE:
                payload[k] = v
    return payload


def _bulk_summarize_change(before, after_payload):
    diffs = []
    interesting = {
        'name', 'is_enabled', 'playback_mode', 'weight',
        'include_in_requests', 'include_in_automation',
        'avoid_duplicates', 'duplicate_prevention_time', 'schedule_items',
    }
    all_keys = set(before.keys()) | set(after_payload.keys())
    for key in sorted(all_keys & interesting):
        old = before.get(key, '<absent>')
        new = after_payload.get(key, '<absent>')
        if old != new:
            diffs.append({'field': key, 'before': old, 'after': new})
    return diffs


def _bulk_is_item_active_today(item, target_date=None):
    """Vérifie si un schedule_item est actif à la date donnée (ou aujourd'hui).
    AzuraCast utilise start_date et end_date (format YYYY-MM-DD) pour limiter
    la période d'activité d'un schedule_item. Si les deux sont None, l'item
    est actif en permanence.
    """
    if target_date is None:
        target_date = datetime.now().strftime('%Y-%m-%d')

    start_date = item.get('start_date')
    end_date = item.get('end_date')

    # Pas de restriction de période → actif
    if not start_date and not end_date:
        return True

    # Vérifier start_date
    if start_date and target_date < start_date:
        return False

    # Vérifier end_date
    if end_date and target_date > end_date:
        return False

    return True


def _bulk_compute_insert_event(playlists, insert_start, insert_duration_min, insert_playlist_id, insert_day=None):
    """Calcule les modifications pour insérer un événement.

    Quand insert_day est spécifié, les schedule_items multi-jours sont scindés :
    - L'item original garde tous ses jours SAUF insert_day (horaires inchangés)
    - Un ou deux nouveaux items sont créés pour insert_day uniquement,
      avec les horaires découpés autour de l'événement

    Exemple : événement jeudi 19:00-20:00, item days=[1,2,4,5] 18:00-22:00
      → Item 1 : days=[1,2,5] 18:00-22:00 (inchangé pour lun/mar/ven)
      → Item 2 : days=[4] 18:00-19:00 (jeudi avant événement)
      → Item 3 : days=[4] 20:00-22:00 (jeudi après événement)
    """
    insert_end = insert_start + insert_duration_min
    results = []
    for pl in playlists:
        if pl.get('id') == insert_playlist_id:
            continue
        current_items = pl.get('schedule_items', []) or []
        new_items = []
        changed = False
        for item in current_items:
            # Filtrer par période (start_date / end_date)
            if not _bulk_is_item_active_today(item):
                new_items.append(dict(item))
                continue

            item_start = _bulk_parse_time_to_minutes(item.get('start_time', 0))
            item_end = _bulk_parse_time_to_minutes(item.get('end_time', 0))
            if item_end <= item_start:
                item_end += 24 * 60

            # Vérifier le chevauchement horaire
            if item_start >= insert_end or item_end <= insert_start:
                # Pas de chevauchement → item inchangé
                new_items.append(dict(item))
                continue

            # Il y a chevauchement horaire
            item_days = item.get('days', [])

            if insert_day is not None and item_days and insert_day in item_days and len(item_days) > 1:
                # CAS SPÉCIAL : item multi-jours incluant insert_day
                # → scinder en deux : autres jours (inchangé) + insert_day (découpé)
                changed = True

                # 1. Item pour les autres jours (horaires inchacts)
                other_days = [d for d in item_days if d != insert_day]
                if other_days:
                    item_other = dict(item)
                    item_other['days'] = other_days
                    if 'id' in item_other:
                        del item_other['id']
                    new_items.append(item_other)

                # 2. Items pour insert_day uniquement (découpés)
                # Partie avant l'événement
                if item_start < insert_start:
                    part1 = dict(item)
                    part1['days'] = [insert_day]
                    part1['start_time'] = _bulk_minutes_to_azura_int(item_start % (24 * 60))
                    part1['end_time'] = _bulk_minutes_to_azura_int(insert_start % (24 * 60))
                    if 'id' in part1:
                        del part1['id']
                    new_items.append(part1)

                # Partie après l'événement
                if item_end > insert_end:
                    part2 = dict(item)
                    part2['days'] = [insert_day]
                    part2['start_time'] = _bulk_minutes_to_azura_int(insert_end % (24 * 60))
                    part2['end_time'] = _bulk_minutes_to_azura_int(item_end % (24 * 60))
                    if 'id' in part2:
                        del part2['id']
                    new_items.append(part2)

            elif insert_day is not None and item_days and insert_day not in item_days:
                # L'item ne joue pas le jour de l'insertion → inchangé
                new_items.append(dict(item))

            elif insert_day is not None and not item_days:
                # L'item joue tous les jours (days vide) mais insert_day est spécifié
                # → scinder : tous les jours sauf insert_day (inchangé) + insert_day (découpé)
                changed = True

                # 1. Item pour tous les jours sauf insert_day
                # (AzuraCast ne supporte pas "tous les jours sauf X" avec days vide,
                # donc on liste explicitement les 6 autres jours)
                all_days = [1, 2, 3, 4, 5, 6, 7]
                other_days = [d for d in all_days if d != insert_day]
                item_other = dict(item)
                item_other['days'] = other_days
                if 'id' in item_other:
                    del item_other['id']
                new_items.append(item_other)

                # 2. Items pour insert_day uniquement (découpés)
                if item_start < insert_start:
                    part1 = dict(item)
                    part1['days'] = [insert_day]
                    part1['start_time'] = _bulk_minutes_to_azura_int(item_start % (24 * 60))
                    part1['end_time'] = _bulk_minutes_to_azura_int(insert_start % (24 * 60))
                    if 'id' in part1:
                        del part1['id']
                    new_items.append(part1)

                if item_end > insert_end:
                    part2 = dict(item)
                    part2['days'] = [insert_day]
                    part2['start_time'] = _bulk_minutes_to_azura_int(insert_end % (24 * 60))
                    part2['end_time'] = _bulk_minutes_to_azura_int(item_end % (24 * 60))
                    if 'id' in part2:
                        del part2['id']
                    new_items.append(part2)

            else:
                # insert_day non spécifié, ou item mono-jour correspondant à insert_day
                # → découpage simple (comportement précédent)
                changed = True

                if item_start < insert_start:
                    part1 = dict(item)
                    part1['start_time'] = _bulk_minutes_to_azura_int(item_start % (24 * 60))
                    part1['end_time'] = _bulk_minutes_to_azura_int(insert_start % (24 * 60))
                    if 'id' in part1:
                        del part1['id']
                    new_items.append(part1)

                if item_end > insert_end:
                    part2 = dict(item)
                    part2['start_time'] = _bulk_minutes_to_azura_int(insert_end % (24 * 60))
                    part2['end_time'] = _bulk_minutes_to_azura_int(item_end % (24 * 60))
                    if 'id' in part2:
                        del part2['id']
                    new_items.append(part2)

        if changed:
            diffs = _bulk_summarize_change(pl, {'schedule_items': new_items})
            results.append((pl, new_items, diffs))
    return results


def _bulk_compute_shift(playlists, delta_minutes):
    """Décale toutes les schedule_items de delta_minutes.
    Les schedule_items d'AzuraCast utilisent start_time/end_time au format entier HHMM.
    """
    results = []
    for pl in playlists:
        current_items = pl.get('schedule_items', []) or []
        new_items = []
        changed = False
        for item in current_items:
            item_start = _bulk_parse_time_to_minutes(item.get('start_time', 0))
            item_end = _bulk_parse_time_to_minutes(item.get('end_time', 0))
            if item_end <= item_start:
                item_end += 24 * 60
            new_start = (item_start + delta_minutes) % (24 * 60)
            new_end = (item_end + delta_minutes) % (24 * 60)
            new_item = dict(item)
            new_item['start_time'] = _bulk_minutes_to_azura_int(new_start)
            new_item['end_time'] = _bulk_minutes_to_azura_int(new_end)
            new_items.append(new_item)
            if new_item['start_time'] != item.get('start_time') or new_item['end_time'] != item.get('end_time'):
                changed = True
        if changed:
            diffs = _bulk_summarize_change(pl, {'schedule_items': new_items})
            results.append((pl, new_items, diffs))
    return results


def _bulk_backup_state(station_id, playlists):
    """Sauvegarde l'état des playlists dans un fichier JSON horodaté."""
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups', 'bulk_schedule')
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    path = os.path.join(backup_dir, f'backup_station{station_id}_{ts}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({
            'station_id': station_id,
            'backed_up_at': ts,
            'playlists': playlists,
        }, f, ensure_ascii=False, indent=2, default=str)
    return path


def _bulk_store_result(tache_id, result_dict):
    """Stocke le résultat JSON dans la colonne 'log_resultat' de taches_planifiees.
    Utilise un UPDATE direct (pas CONCAT) pour remplacer le contenu par le JSON seul,
    précédé d'un marqueur pour que le dashboard puisse le retrouver."""
    try:
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor()
        result_json = json.dumps(result_dict, ensure_ascii=False, default=str)
        # Marqueur + JSON — le dashboard cherche ce marqueur
        marker = "__BULK_RESULT__:"
        cursor.execute(
            "UPDATE taches_planifiees SET log_resultat = CONCAT(log_resultat, %s) WHERE id = %s",
            (marker + result_json + "\n", tache_id)
        )
        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        print(f"[Bulk schedule] Erreur stockage résultat: {e}")


def executer_bulk_schedule(tache_id, parametres):
    """Exécute une tâche BULK_SCHEDULE.

    Sous-modes :
    - list : récupère les playlists d'une station
    - preview : calcule les modifications (dry-run)
    - apply : applique les modifications (avec backup)
    """
    sub_mode = parametres.get('sub_mode', '')
    station_id = parametres.get('station_id')

    if not sub_mode or not station_id:
        raise RuntimeError("sub_mode et station_id requis")

    logger(tache_id, f"=== BULK SCHEDULE (sub_mode={sub_mode}, station={station_id}) ===")

    # ── Sous-mode LIST : récupérer les playlists ──
    if sub_mode == 'list':
        all_playlists = _bulk_list_playlists(station_id)
        detailed = []
        for pl in all_playlists:
            try:
                d = _bulk_get_playlist(station_id, pl['id'])
                detailed.append({
                    'id': d.get('id'),
                    'name': d.get('name', '?'),
                    'is_enabled': d.get('is_enabled', False),
                    'schedule_items': d.get('schedule_items', []) or [],
                    'playback_mode': d.get('playback_mode', ''),
                    'weight': d.get('weight', 0),
                })
            except Exception as e:
                logger(tache_id, f"  Erreur détail playlist {pl.get('id')}: {e}")

        result = {'status': 'ok', 'playlists': detailed}
        logger(tache_id, f"  {len(detailed)} playlist(s) récupérée(s)")
        logger(tache_id, "__BULK_RESULT__:" + json.dumps(result, ensure_ascii=False, default=str))
        return

    # ── Sous-mode PREVIEW : calculer les modifications ──
    if sub_mode == 'preview':
        mode = parametres.get('mode', '')
        all_playlists_raw = _bulk_list_playlists(station_id)
        logger(tache_id, f"  _bulk_list_playlists returned {len(all_playlists_raw)} playlist(s)")

        # La liste GET /playlists retourne déjà les schedule_items complets.
        # On filtre les playlists désactivées (is_enabled=false) car elles ne
        # jouent pas à l'antenne et ne doivent pas être décalées.
        detailed = []
        for pl in all_playlists_raw:
            if not pl.get('is_enabled', False):
                continue  # Playlist désactivée — on l'ignore
            if pl.get('schedule_items') is not None:
                detailed.append(pl)
            else:
                try:
                    d = _bulk_get_playlist(station_id, pl['id'])
                    detailed.append(d)
                except Exception:
                    pass
        logger(tache_id, f"  detailed: {len(detailed)} playlist(s) avec schedule_items")

        planned = []

        if mode == 'insert_event':
            insert_cfg = parametres.get('insert', {})
            insert_playlist_id = insert_cfg.get('playlist_id')
            start_time = insert_cfg.get('start_time', '')
            duration_min = int(insert_cfg.get('duration_minutes', 0))
            insert_start = _bulk_parse_time_to_minutes(start_time)

            # Debug: log what we received
            logger(tache_id, f"  insert_playlist_id={insert_playlist_id} start={start_time} dur={duration_min}")

            results = _bulk_compute_insert_event(detailed, insert_start, duration_min, insert_playlist_id, insert_cfg.get('day'))
            for pl, new_items, diffs in results:
                # Résumé compact : ne pas renvoyer tout le schedule_items
                # (trop volumineux pour log_resultat). L'apply recalculera.
                planned.append({
                    'playlist_id': pl['id'],
                    'name': pl.get('name', '?'),
                    'changes': len(diffs),
                    'old_items': len(pl.get('schedule_items', []) or []),
                    'new_items_count': len(new_items),
                })

            insert_pl = next((p for p in detailed if p.get('id') == insert_playlist_id), None)
            insert_info = None
            if insert_pl:
                end_time_min = (insert_start + duration_min) % (24 * 60)
                insert_info = {
                    'playlist_id': insert_playlist_id,
                    'name': insert_pl.get('name', '?'),
                    'start_time': _bulk_minutes_to_azura_int(insert_start),
                    'end_time': _bulk_minutes_to_azura_int(end_time_min),
                }

            result = {
                'status': 'ok', 'mode': mode,
                'planned_updates': planned,
                'insert_playlist': insert_info,
                'total_affected': len(planned),
            }

        elif mode == 'shift':
            shift_cfg = parametres.get('shift', {})
            playlist_ids = shift_cfg.get('playlist_ids', [])
            delta_min = int(shift_cfg.get('delta_minutes', 0))

            shift_playlists = [p for p in detailed if p.get('id') in playlist_ids]
            results = _bulk_compute_shift(shift_playlists, delta_min)
            for pl, new_items, diffs in results:
                planned.append({
                    'playlist_id': pl['id'],
                    'name': pl.get('name', '?'),
                    'changes': len(diffs),
                    'old_items': len(pl.get('schedule_items', []) or []),
                    'new_items_count': len(new_items),
                })

            result = {
                'status': 'ok', 'mode': mode,
                'planned_updates': planned,
                'total_affected': len(planned),
            }

        elif mode == 'json_override':
            jo_cfg = parametres.get('json_override', {})
            playlist_ids = jo_cfg.get('playlist_ids', [])
            schedule_config = jo_cfg.get('schedule_config', {})

            for pid in playlist_ids:
                current = next((p for p in detailed if p.get('id') == pid), None)
                if not current:
                    try:
                        current = _bulk_get_playlist(station_id, pid)
                    except Exception:
                        continue
                payload = _bulk_build_payload(current, schedule_config)
                diffs = _bulk_summarize_change(current, payload)
                if diffs:
                    planned.append({
                        'playlist_id': pid,
                        'name': current.get('name', '?'),
                        'changes': len(diffs),
                    })

            result = {
                'status': 'ok', 'mode': mode,
                'planned_updates': planned,
                'total_affected': len(planned),
            }
        else:
            raise RuntimeError(f"Mode inconnu: {mode}")

        logger(tache_id, f"  {len(planned)} playlist(s) affectée(s)")
        # Stocker le résultat JSON dans log_resultat
        logger(tache_id, "__BULK_RESULT__:" + json.dumps(result, ensure_ascii=False, default=str))
        return

    # ── Sous-mode APPLY : appliquer les modifications ──
    if sub_mode == 'apply':
        mode = parametres.get('mode', '')

        # Recalculer les modifications localement (le preview ne renvoie qu'un résumé)
        all_playlists_raw = _bulk_list_playlists(station_id)
        detailed = []
        for pl in all_playlists_raw:
            if not pl.get('is_enabled', False):
                continue
            if pl.get('schedule_items') is not None:
                detailed.append(pl)
            else:
                try:
                    detailed.append(_bulk_get_playlist(station_id, pl['id']))
                except Exception:
                    pass

        updates = []
        insert_playlist = None
        selected_ids = parametres.get('selected_playlist_ids', None)

        if mode == 'insert_event':
            insert_cfg = parametres.get('insert', {})
            insert_playlist_id = insert_cfg.get('playlist_id')
            start_time = insert_cfg.get('start_time', '')
            duration_min = int(insert_cfg.get('duration_minutes', 0))
            insert_start = _bulk_parse_time_to_minutes(start_time)

            results = _bulk_compute_insert_event(detailed, insert_start, duration_min, insert_playlist_id, insert_cfg.get('day'))
            for pl, new_items, diffs in results:
                # Filtrer par les IDs sélectionnés par l'utilisateur
                if selected_ids is not None and pl['id'] not in selected_ids:
                    continue
                updates.append({
                    'playlist_id': pl['id'],
                    'new_schedule_items': new_items,
                })

            insert_pl = next((p for p in detailed if p.get('id') == insert_playlist_id), None)
            if insert_pl:
                end_time_min = (insert_start + duration_min) % (24 * 60)
                insert_playlist = {
                    'playlist_id': insert_playlist_id,
                    'new_schedule_items': [{
                        'start_time': _bulk_minutes_to_azura_int(insert_start),
                        'end_time': _bulk_minutes_to_azura_int(end_time_min),
                    }],
                }

        elif mode == 'shift':
            shift_cfg = parametres.get('shift', {})
            playlist_ids = shift_cfg.get('playlist_ids', [])
            delta_min = int(shift_cfg.get('delta_minutes', 0))

            shift_playlists = [p for p in detailed if p.get('id') in playlist_ids]
            results = _bulk_compute_shift(shift_playlists, delta_min)
            for pl, new_items, diffs in results:
                if selected_ids is not None and pl['id'] not in selected_ids:
                    continue
                updates.append({
                    'playlist_id': pl['id'],
                    'new_schedule_items': new_items,
                })

        elif mode == 'json_override':
            jo_cfg = parametres.get('json_override', {})
            playlist_ids = jo_cfg.get('playlist_ids', [])
            schedule_config = jo_cfg.get('schedule_config', {})

            for pid in playlist_ids:
                current = next((p for p in detailed if p.get('id') == pid), None)
                if not current:
                    try:
                        current = _bulk_get_playlist(station_id, pid)
                    except Exception:
                        continue
                payload = _bulk_build_payload(current, schedule_config)
                updates.append({
                    'playlist_id': pid,
                    'new_payload': payload,
                })

        # 1. Backup (utiliser les données de la liste, pas de GET individuel)
        backup_playlists = []
        for u in updates:
            pl_data = next((p for p in detailed if p.get('id') == u['playlist_id']), None)
            if pl_data:
                backup_playlists.append(pl_data)
        if insert_playlist:
            pl_data = next((p for p in detailed if p.get('id') == insert_playlist['playlist_id']), None)
            if pl_data:
                backup_playlists.append(pl_data)
        backup_path = _bulk_backup_state(station_id, backup_playlists)
        logger(tache_id, f"  Backup: {backup_path}")

        # 2. Appliquer les modifications (utiliser les données de la liste, pas de GET individuel)
        success = 0
        failures = []
        for u in updates:
            pid = u['playlist_id']
            try:
                # Utiliser les données de la liste (detailed) au lieu de _bulk_get_playlist
                current = next((p for p in detailed if p.get('id') == pid), None)
                if not current:
                    raise RuntimeError(f"Playlist {pid} non trouvée dans la liste")
                if 'new_schedule_items' in u:
                    payload = _bulk_build_payload(current, {'schedule_items': u['new_schedule_items']})
                elif 'new_payload' in u:
                    payload = u['new_payload']
                else:
                    continue
                _bulk_update_playlist(station_id, pid, payload)
                success += 1
                time.sleep(0.1)
            except Exception as e:
                failures.append({'playlist_id': pid, 'error': str(e)})

        # 3. Playlist à insérer (mode insert_event)
        if insert_playlist and mode == 'insert_event':
            try:
                pid = insert_playlist['playlist_id']
                current = next((p for p in detailed if p.get('id') == pid), None)
                if not current:
                    raise RuntimeError(f"Playlist {pid} non trouvée dans la liste")
                payload = _bulk_build_payload(current, {'schedule_items': insert_playlist['new_schedule_items']})
                _bulk_update_playlist(station_id, pid, payload)
                success += 1
            except Exception as e:
                failures.append({'playlist_id': pid, 'error': str(e)})

        result = {
            'status': 'ok' if not failures else 'partial',
            'message': f'{success} OK / {len(failures)} échec(s)',
            'success': success,
            'failures': failures,
            'backup_path': backup_path,
        }
        logger(tache_id, f"  {success} OK / {len(failures)} échec(s)")
        logger(tache_id, "__BULK_RESULT__:" + json.dumps(result, ensure_ascii=False, default=str))
        return

    raise RuntimeError(f"Sub-mode inconnu: {sub_mode}")


def executer_creer_playlist_azura(tache_id, parametres):
    """
    Crée une playlist dans AzuraCast et y importe les fichiers via M3U.
    Si une playlist du même nom existe déjà, les fichiers y sont ajoutés (fusion).
    Les fichiers marqués in_azura=True sont résolus via l'API pour trouver leur chemin réel.

    Parametres (depuis taches_planifiees.parametres, JSON) :
      - station_id: ID de la station cible (ex: 6 ou 7). Fallback 7 si absent.
    """
    # station_id (multi-station). Fallback sur station 7 pour les tâches anciennes.
    station_id = parametres.get('station_id', _DEFAULT_STATION_ID)
    try:
        station_id = int(station_id)
    except (ValueError, TypeError):
        station_id = _DEFAULT_STATION_ID

    api = get_azura_api(station_id)
    if not api:
        logger(tache_id, "ERREUR FATALE: Module azuracast_api non disponible.")
        logger(tache_id, "Vérifiez que azuracast_api.py est dans le même répertoire que worker_ubuntu.py")
        logger(tache_id, "=== TERMINÉ PLAYLIST AZURACAST (ERREUR) ===")
        return

    nom_playlist = parametres.get('nom_playlist', f'Playlist_{datetime.now().strftime("%Y%m%d_%H%M")}')
    fichiers = parametres.get('fichiers', [])
    dossier_cible = parametres.get('dossier_cible', 'imports_push')
    pl_type = parametres.get('type', 'default')
    pl_order = parametres.get('order', 'shuffle')
    mode_fusion = parametres.get('mode_fusion', 'ajouter')  # 'ajouter' ou 'creer_nouveau'
    # is_enabled : par défaut True (comportement historique). Si False, la playlist
    # est créée désactivée (utile pour Programmation avancée en mode creer_nouvelle
    # afin d'éviter qu'une playlist sans schedule ne joue 24/7).
    is_enabled = bool(parametres.get('is_enabled', True))

    # Forcer le type 'default' si la valeur n'est pas valide dans AzuraCast
    TYPES_VALIDES = ('default', 'once_per_x_songs', 'once_per_x_minutes', 'once_per_hour', 'custom')
    if pl_type not in TYPES_VALIDES:
        logger(tache_id, f"ATTENTION: Type '{pl_type}' non supporté. Utilisation de 'default'.")
        pl_type = 'default'

    # ── Vérification d'annulation push avant de commencer les appels API playlist ──
    # On utilise le push_task_id s'il est dans les paramètres, sinon le tache_id lui-même
    _push_task_id = parametres.get('push_task_id') or tache_id
    if check_push_cancel(_push_task_id):
        logger(tache_id, "=== ANNULÉ === Avant création/ajout playlist ===")
        return

    logger(tache_id, f"DÉBUT CRÉATION PLAYLIST AZURACAST (station {station_id})")
    logger(tache_id, f"   Nom    : {nom_playlist}")
    logger(tache_id, f"   Type   : {pl_type}")
    logger(tache_id, f"   Ordre  : {pl_order}")
    logger(tache_id, f"   État   : {'ACTIVÉE' if is_enabled else 'DÉSACTIVÉE (anti-24/7)'}")
    logger(tache_id, f"   Dossier: {dossier_cible}")
    logger(tache_id, f"   Fichiers: {len(fichiers)} titre(s)")

    # Vérification de la connexion API avant de commencer
    try:
        logger(tache_id, "Test de connexion à l'API AzuraCast...")
        test_playlists = api.list_playlists()
        logger(tache_id, f"API joignable — {len(test_playlists)} playlist(s) existante(s)")
    except AzuraCastAPIError as e:
        logger(tache_id, f"ERREUR CONNEXION API : {e}")
        logger(tache_id, f"   Code HTTP : {e.status_code}")
        logger(tache_id, "=== TERMINÉ PLAYLIST AZURACAST (ERREUR) ===")
        return
    except Exception as e:
        logger(tache_id, f"ERREUR RÉSEAU : Impossible de joindre AzuraCast")
        logger(tache_id, f"   Détail : {type(e).__name__} — {e}")
        logger(tache_id, "=== TERMINÉ PLAYLIST AZURACAST (ERREUR) ===")
        return

    try:
        playlist_id = None
        mode_utilise = "creation"

        # ── Vérifier si une playlist du même nom existe déjà ──
        playlist_existante = api.find_playlist_by_name(nom_playlist)

        if playlist_existante:
            playlist_id = playlist_existante.get('id')
            nb_existants = len(playlist_existante.get('items', []))

            if mode_fusion == 'creer_nouveau':
                # Mode : créer une nouvelle playlist avec un nom incrémenté
                compteur = 2
                while api.find_playlist_by_name(f"{nom_playlist}_{compteur}"):
                    compteur += 1
                nom_final = f"{nom_playlist}_{compteur}"
                logger(tache_id, f"Playlist '{nom_playlist}' déjà existante (ID={playlist_id}). Création avec nom alternatif...")
                logger(tache_id, f"Étape 1/2 : Création de la playlist '{nom_final}' via API...")
                playlist = api.create_playlist(
                    name=nom_final,
                    type=pl_type,
                    order=pl_order,
                    is_enabled=is_enabled
                )
                playlist_id = playlist.get('id')
                nom_playlist = nom_final
                mode_utilise = "nouveau_nom"
            else:
                # Mode par défaut : ajouter les fichiers à la playlist existante
                logger(tache_id, f"Playlist '{nom_playlist}' déjà existante (ID={playlist_id}, {nb_existants} titre(s)).")
                logger(tache_id, f"Mode fusion : les {len(fichiers)} fichier(s) seront ajoutés à la playlist existante.")
                mode_utilise = "fusion"
        else:
            # Aucune playlist existante → création normale
            logger(tache_id, "Étape 1/2 : Création de la playlist via API...")
            playlist = api.create_playlist(
                name=nom_playlist,
                type=pl_type,
                order=pl_order,
                is_enabled=is_enabled
            )
            playlist_id = playlist.get('id')
            if not playlist_id:
                logger(tache_id, "ERREUR: La playlist créée n'a pas d'ID valide.")
                logger(tache_id, f"Réponse API brute : {json.dumps(playlist, ensure_ascii=False)[:500]}")
                logger(tache_id, "=== TERMINÉ PLAYLIST AZURACAST (ERREUR) ===")
                return

        logger(tache_id, f"Playlist : ID={playlist_id}, Nom='{nom_playlist}'")

        # ── Import M3U ──
        if fichiers:
            # Séparer les fichiers nouveaux de ceux déjà dans AzuraCast
            fichiers_nouveaux = [f for f in fichiers if not f.get('in_azura', False)]
            fichiers_existants = [f for f in fichiers if f.get('in_azura', False)]

            # Résolution des chemins réels pour TOUS les fichiers via l'API AzuraCast
            # C'est nécessaire car le nom de fichier sur le disque Linux peut différer
            # du path Windows stocké dans RadioDJ (ex: tag ID3 vs nom de fichier)
            logger(tache_id, f"Résolution des chemins réels via l'API AzuraCast pour {len(fichiers)} fichier(s)...")

            # Si des fichiers nouveaux ont été copiés, attendre qu'AzuraCast les indexe
            if fichiers_nouveaux:
                logger(tache_id, f"Attente de l'indexation AzuraCast pour {len(fichiers_nouveaux)} nouveau(x) fichier(s)...")
                time.sleep(5)  # Laisser AzuraCast scanner les nouveaux fichiers

            # Essayer de résoudre les chemins (avec retry pour les fichiers nouvellement copiés)
            MAX_RETRIES = 3
            DELAI_RETRY = 5
            dict_chemins = {}
            fichiers_non_resolus_nouveaux = fichiers_nouveaux[:]

            for tentative in range(1, MAX_RETRIES + 1):
                dict_chemins = _construire_dictionnaire_chemins_azuracast(station_id)
                logger(tache_id, f"  Tentative {tentative}/{MAX_RETRIES} : {len(dict_chemins)} entrée(s) dans l'API.")

                if fichiers_non_resolus_nouveaux:
                    encore_non_resolus = []
                    for f in fichiers_non_resolus_nouveaux:
                        artist = str(f.get('artist', '')).strip()
                        title = str(f.get('title', '')).strip()
                        cle = normaliser_pour_recherche(f"{artist} - {title}")
                        if cle in dict_chemins:
                            f['chemin_reel_azuracast'] = dict_chemins[cle]
                        else:
                            encore_non_resolus.append(f)
                    fichiers_non_resolus_nouveaux = encore_non_resolus
                    if not fichiers_non_resolus_nouveaux:
                        logger(tache_id, f"  Tous les nouveaux fichiers résolus à la tentative {tentative}.")
                        break
                    if tentative < MAX_RETRIES:
                        logger(tache_id, f"  {len(fichiers_non_resolus_nouveaux)} nouveau(x) non encore indexé(s), nouvel essai dans {DELAI_RETRY}s...")
                        time.sleep(DELAI_RETRY)

            # Résoudre les fichiers existants (déjà dans AzuraCast)
            fichiers_resolus = 0
            fichiers_non_resolus = 0
            for f in fichiers_existants:
                artist = str(f.get('artist', '')).strip()
                title = str(f.get('title', '')).strip()
                cle = normaliser_pour_recherche(f"{artist} - {title}")
                chemin_reel = dict_chemins.get(cle)
                if chemin_reel:
                    f['chemin_reel_azuracast'] = chemin_reel
                    fichiers_resolus += 1
                else:
                    logger(tache_id, f"  ⚠ Chemin non trouvé pour : {artist} - {title}")
                    fichiers_non_resolus += 1

            # Compter les résolutions pour les nouveaux aussi
            for f in fichiers_nouveaux:
                if f.get('chemin_reel_azuracast'):
                    fichiers_resolus += 1
                else:
                    fichiers_non_resolus += 1

            logger(tache_id, f"{fichiers_resolus} chemin(s) résolu(s), {fichiers_non_resolus} non résolu(s).")

            # Générer le M3U en utilisant les chemins réels de l'API
            label_etape = "Ajout" if mode_utilise == "fusion" else "Import"
            nb_nouveaux = len(fichiers_nouveaux)
            nb_existants = len(fichiers_existants)
            logger(tache_id, f"Étape 2/2 : {label_etape} de {len(fichiers)} fichier(s) via M3U ({nb_nouveaux} nouveau(x), {nb_existants} existant(s))...")

            m3u_content = ""
            for f in fichiers:
                chemin_reel = f.get('chemin_reel_azuracast')
                if chemin_reel:
                    # Chemin résolu via l'API → utiliser tel quel
                    m3u_content += f"{chemin_reel}\n"
                else:
                    # Fallback : construire à partir du dossier cible et du nom de fichier
                    # On utilise le nom du fichier Linux (celui réellement copié)
                    path_win = str(f.get('path', '')).strip()
                    try:
                        src_path = windows_vers_linux(path_win)
                        filename = src_path.name
                    except (ValueError, Exception):
                        filename = os.path.basename(path_win.replace('/', os.sep))
                    dossier = f.get('dossier_reel', dossier_cible)
                    if dossier:
                        m3u_content += f"{dossier}/{filename}\n"
                    else:
                        m3u_content += f"{filename}\n"

            # Log du M3U généré (debug)
            nb_lignes = len(m3u_content.split('\n'))
            logger(tache_id, f"   M3U généré : {nb_lignes} lignes")

            # Log des 3 premières entrées du M3U pour vérification
            lignes_m3u = m3u_content.split('\n')[:6]
            for ligne in lignes_m3u:
                logger(tache_id, f"   M3U> {ligne}")

            import_result = api.import_m3u(playlist_id, m3u_content)
            msg = import_result.get('message', import_result.get('formatted_message', ''))

            # Analyser le résultat pour détecter les échecs partiels
            nb_fichiers_compare = 0
            nb_fichiers_ok = 0
            if msg:
                import re as _re
                match = _re.search(r'(\d+)\s+fichier[s]?\s+sur\s+(\d+)', msg)
                if match:
                    nb_fichiers_ok = int(match.group(1))
                    nb_fichiers_compare = int(match.group(2))

            if nb_fichiers_compare > 0 and nb_fichiers_ok == 0:
                logger(tache_id, f"⚠ IMPORT M3U ÉCHOUÉ : 0 fichier sur {nb_fichiers_compare} n'a pu être intégré à la playlist.")
                logger(tache_id, f"   Cause probable : les fichiers n'ont pas été trouvés dans le catalogue AzuraCast.")
                if msg:
                    logger(tache_id, f"   Détail AzuraCast : {msg}")
            elif nb_fichiers_compare > 0 and nb_fichiers_ok < nb_fichiers_compare:
                logger(tache_id, f"⚠ IMPORT M3U PARTIEL : {nb_fichiers_ok}/{nb_fichiers_compare} fichier(s) intégré(s) à la playlist.")
                if msg:
                    logger(tache_id, f"   Détail AzuraCast : {msg}")
            else:
                logger(tache_id, f"Import M3U réussi")
                if msg:
                    logger(tache_id, f"   {msg}")
                else:
                    logger(tache_id, f"   Réponse : {json.dumps(import_result, ensure_ascii=False)[:300]}")

            # Mettre à jour le cache local
            logger(tache_id, f"Mise à jour du cache des playlists (station {station_id})...")
            _synchroniser_cache_playlists(station_id)
        else:
            logger(tache_id, "Étape 2/2 : Aucun fichier à importer (playlist vide créée).")

        # Résumé final
        logger(tache_id, f"RÉSUMÉ : Playlist '{nom_playlist}' (ID={playlist_id})")
        if mode_utilise == "fusion":
            logger(tache_id, f"   Mode : FUSION — fichiers ajoutés à la playlist existante")
        elif mode_utilise == "nouveau_nom":
            logger(tache_id, f"   Mode : NOUVEAU NOM — nom modifié car la playlist d'origine existait")
        else:
            logger(tache_id, f"   Mode : CRÉATION — nouvelle playlist créée")
        logger(tache_id, f"   {len(fichiers)} fichier(s) importé(s) ({len(fichiers_nouveaux) if fichiers else 0} nouveau(x), {len(fichiers_existants) if fichiers else 0} existant(s))")
        logger(tache_id, f"   Dossier AzuraCast : {dossier_cible}")
        logger(tache_id, "=== TERMINÉ PLAYLIST AZURACAST ===")

    except AzuraCastAPIError as e:
        logger(tache_id, f"ERREUR API AZURACAST : {e}")
        if e.status_code:
            logger(tache_id, f"   Code HTTP : {e.status_code}")
        if e.response_body:
            logger(tache_id, f"   Réponse : {str(e.response_body)[:500]}")
        logger(tache_id, "=== TERMINÉ PLAYLIST AZURACAST (ERREUR) ===")
    except requests.exceptions.ConnectionError:
        logger(tache_id, f"ERREUR CONNEXION : Impossible de joindre AzuraCast")
        logger(tache_id, f"   Vérifiez la connexion Internet et l'URL de l'API")
        logger(tache_id, "=== TERMINÉ PLAYLIST AZURACAST (ERREUR) ===")
    except requests.exceptions.SSLError as e:
        logger(tache_id, f"ERREUR SSL : Certificat invalide ou auto-signé")
        logger(tache_id, f"   Détail : {e}")
        logger(tache_id, "=== TERMINÉ PLAYLIST AZURACAST (ERREUR) ===")
    except Exception as e:
        logger(tache_id, f"ERREUR INATTENDUE : {type(e).__name__} — {e}")
        import traceback
        logger(tache_id, f"   Traceback : {traceback.format_exc()[:500]}")
        logger(tache_id, "=== TERMINÉ PLAYLIST AZURACAST (ERREUR) ===")


def executer_import_m3u_playlist(tache_id, parametres):
    """
    Importe un contenu M3U dans une playlist AzuraCast existante.
    Gère les fichiers marqués in_azura=True en résolvant leur chemin réel.

    Parametres :
      - station_id: ID de la station cible (ex: 6 ou 7). Fallback 7 si absent.
    """
    # station_id (multi-station). Fallback sur station 7 pour les tâches anciennes.
    station_id = parametres.get('station_id', _DEFAULT_STATION_ID)
    try:
        station_id = int(station_id)
    except (ValueError, TypeError):
        station_id = _DEFAULT_STATION_ID

    api = get_azura_api(station_id)
    if not api:
        logger(tache_id, "ERREUR FATALE: Module azuracast_api non disponible.")
        logger(tache_id, "=== TERMINÉ ===")
        return

    # ── Vérification d'annulation push avant import M3U ──
    _push_task_id = parametres.get('push_task_id') or tache_id
    if check_push_cancel(_push_task_id):
        logger(tache_id, "=== ANNULÉ === Avant import M3U playlist ===")
        return

    playlist_id = parametres.get('playlist_id')
    if not playlist_id:
        logger(tache_id, "ERREUR: playlist_id manquant.")
        logger(tache_id, "=== TERMINÉ ===")
        return

    # Récupérer le nom de la playlist pour un log plus lisible
    playlist_name = f"ID {playlist_id}"
    try:
        playlists_api = api.list_playlists()
        for pl in playlists_api:
            if str(pl.get('id')) == str(playlist_id):
                playlist_name = f'"{pl.get('name', '')}" (ID {playlist_id})'
                break
    except Exception:
        pass

    logger(tache_id, f"DÉBUT IMPORT M3U DANS PLAYLIST {playlist_name}")

    try:
        # Soit on a du M3U brut, soit on le génère à partir des fichiers
        m3u_content = parametres.get('m3u_content')

        if not m3u_content:
            fichiers = parametres.get('fichiers', [])
            dossier_cible = parametres.get('dossier_cible', 'imports_push')
            if not fichiers:
                logger(tache_id, "ERREUR: Ni m3u_content ni fichiers fournis.")
                logger(tache_id, "=== TERMINÉ ===")
                return

            # Séparer les fichiers nouveaux de ceux déjà dans AzuraCast
            fichiers_nouveaux = [f for f in fichiers if not f.get('in_azura', False)]
            fichiers_existants = [f for f in fichiers if f.get('in_azura', False)]

            # Résolution des chemins réels pour TOUS les fichiers via l'API AzuraCast
            logger(tache_id, f"Résolution des chemins réels via l'API AzuraCast pour {len(fichiers)} fichier(s)...")

            # Si des fichiers nouveaux ont été copiés, attendre qu'AzuraCast les indexe
            if fichiers_nouveaux:
                logger(tache_id, f"Attente de l'indexation AzuraCast pour {len(fichiers_nouveaux)} nouveau(x) fichier(s)...")
                time.sleep(5)

            # Essayer de résoudre les chemins (avec retry pour les fichiers nouvellement copiés)
            MAX_RETRIES = 3
            DELAI_RETRY = 5
            dict_chemins = {}
            fichiers_non_resolus_nouveaux = fichiers_nouveaux[:]

            for tentative in range(1, MAX_RETRIES + 1):
                dict_chemins = _construire_dictionnaire_chemins_azuracast(station_id)
                logger(tache_id, f"  Tentative {tentative}/{MAX_RETRIES} : {len(dict_chemins)} entrée(s) dans l'API.")

                if fichiers_non_resolus_nouveaux:
                    encore_non_resolus = []
                    for f in fichiers_non_resolus_nouveaux:
                        artist = str(f.get('artist', '')).strip()
                        title = str(f.get('title', '')).strip()
                        cle = normaliser_pour_recherche(f"{artist} - {title}")
                        if cle in dict_chemins:
                            f['chemin_reel_azuracast'] = dict_chemins[cle]
                        else:
                            encore_non_resolus.append(f)
                    fichiers_non_resolus_nouveaux = encore_non_resolus
                    if not fichiers_non_resolus_nouveaux:
                        logger(tache_id, f"  Tous les nouveaux fichiers résolus à la tentative {tentative}.")
                        break
                    if tentative < MAX_RETRIES:
                        logger(tache_id, f"  {len(fichiers_non_resolus_nouveaux)} nouveau(x) non encore indexé(s), nouvel essai dans {DELAI_RETRY}s...")
                        time.sleep(DELAI_RETRY)

            # Résoudre les fichiers existants
            fichiers_resolus = 0
            fichiers_non_resolus = 0
            for f in fichiers_existants:
                artist = str(f.get('artist', '')).strip()
                title = str(f.get('title', '')).strip()
                cle = normaliser_pour_recherche(f"{artist} - {title}")
                chemin_reel = dict_chemins.get(cle)
                if chemin_reel:
                    f['chemin_reel_azuracast'] = chemin_reel
                    fichiers_resolus += 1
                else:
                    logger(tache_id, f"  ⚠ Chemin non trouvé pour : {artist} - {title}")
                    fichiers_non_resolus += 1

            for f in fichiers_nouveaux:
                if f.get('chemin_reel_azuracast'):
                    fichiers_resolus += 1
                else:
                    fichiers_non_resolus += 1

            logger(tache_id, f"{fichiers_resolus} chemin(s) résolu(s), {fichiers_non_resolus} non résolu(s).")

            # Générer le M3U en utilisant les chemins réels de l'API
            m3u_content = ""
            for f in fichiers:
                chemin_reel = f.get('chemin_reel_azuracast')
                if chemin_reel:
                    m3u_content += f"{chemin_reel}\n"
                else:
                    path_win = str(f.get('path', '')).strip()
                    try:
                        src_path = windows_vers_linux(path_win)
                        filename = src_path.name
                    except (ValueError, Exception):
                        filename = os.path.basename(path_win.replace('/', os.sep))
                    dossier = f.get('dossier_reel', dossier_cible)
                    if dossier:
                        m3u_content += f"{dossier}/{filename}\n"
                    else:
                        m3u_content += f"{filename}\n"

            logger(tache_id, f"M3U généré à partir de {len(fichiers)} fichier(s) ({len(fichiers_nouveaux)} nouveau(x), {len(fichiers_existants)} existant(s))")

        nb_lignes = len(m3u_content.split('\n'))
        logger(tache_id, f"Import de {nb_lignes} lignes M3U...")

        import_result = api.import_m3u(playlist_id, m3u_content)
        msg = import_result.get('message', import_result.get('formatted_message', ''))

        # Analyser le résultat pour détecter les échecs partiels
        nb_fichiers_compare = 0
        nb_fichiers_ok = 0
        if msg:
            # Le message d'AzuraCast est du type : "Playlist importée avec succès; X fichiers sur Y ont été comparés avec succès."
            import re as _re
            match = _re.search(r'(\d+)\s+fichier[s]?\s+sur\s+(\d+)', msg)
            if match:
                nb_fichiers_ok = int(match.group(1))
                nb_fichiers_compare = int(match.group(2))

        if nb_fichiers_compare > 0 and nb_fichiers_ok == 0:
            logger(tache_id, f"⚠ IMPORT M3U ÉCHOUÉ : 0 fichier sur {nb_fichiers_compare} n'a pu être intégré à la playlist.")
            logger(tache_id, f"   Cause probable : les fichiers n'ont pas été trouvés dans le catalogue AzuraCast.")
            if msg:
                logger(tache_id, f"   Détail AzuraCast : {msg}")
        elif nb_fichiers_compare > 0 and nb_fichiers_ok < nb_fichiers_compare:
            logger(tache_id, f"⚠ IMPORT M3U PARTIEL : {nb_fichiers_ok}/{nb_fichiers_compare} fichier(s) intégré(s) à la playlist.")
            if msg:
                logger(tache_id, f"   Détail AzuraCast : {msg}")
        else:
            logger(tache_id, f"Import M3U réussi dans la playlist {playlist_name}")
            if msg:
                logger(tache_id, f"   {msg}")

        # Mettre à jour le cache
        _synchroniser_cache_playlists(station_id)

        if nb_fichiers_compare > 0 and nb_fichiers_ok == 0:
            logger(tache_id, "=== TERMINÉ IMPORT M3U (ÉCHEC) ===")
        else:
            logger(tache_id, "=== TERMINÉ IMPORT M3U ===")

    except AzuraCastAPIError as e:
        logger(tache_id, f"ERREUR API : {e}")
        logger(tache_id, "=== TERMINÉ IMPORT M3U (ERREUR) ===")
    except Exception as e:
        logger(tache_id, f"ERREUR INATTENDUE : {e}")
        logger(tache_id, "=== TERMINÉ IMPORT M3U (ERREUR) ===")


def executer_sync_playlists_cache(tache_id, parametres):
    """
    Synchronise le cache local des playlists AzuraCast dans la table SQL.
    Utilise directement les champs API : num_songs et total_length.

    Parametres :
      - station_ids: liste d'IDs de stations à synchroniser (ex: [6, 7])
                     Si absent → toutes les stations de config.json
    """
    # Résoudre la liste des stations à synchroniser
    station_ids = parametres.get('station_ids')
    if not station_ids or not isinstance(station_ids, list):
        # Par défaut : toutes les stations configurées
        station_ids = [s['id'] for s in lister_stations_config()]
    else:
        station_ids = [int(s) for s in station_ids]

    if not station_ids:
        # Fallback ultime
        station_ids = [_DEFAULT_STATION_ID]

    logger(tache_id, f"DÉBUT SYNCHRONISATION CACHE PLAYLISTS pour station(s) {station_ids}")
    print(f"[Tâche {tache_id}] SYNC_PLAYLISTS_CACHE : stations={station_ids}")

    # ── Migration douce : s'assurer que la table azuracast_playlists_cache
    #    existe AVEC la colonne station_id (multi-station). Sans cette colonne,
    #    l'API /api/azuracast/playlists ne peut pas filtrer par station et le
    #    badge "Station" affiche "?" dans le frontend.
    try:
        avant = _assurer_cache_playlists_schema(station_ids[0] if station_ids else _DEFAULT_STATION_ID)
        logger(tache_id, "Schéma cache playlists vérifié/migré (colonne station_id).")
    except Exception as e:
        logger(tache_id, f"⚠ Erreur migration schéma cache playlists : {e}")

    for sid in station_ids:
        api = get_azura_api(sid)
        if not api:
            logger(tache_id, f"[station {sid}] ERREUR FATALE: Module azuracast_api non disponible.")
            continue

        logger(tache_id, f"[station {sid}] Appel API...")
        try:
            # Récupérer les données brutes depuis l'API
            raw_playlists = api.list_playlists()

            if raw_playlists and len(raw_playlists) > 0:
                first_pl = raw_playlists[0]
                available_keys = list(first_pl.keys())
                logger(tache_id, f"[station {sid}] Clés API playlist : {available_keys}")

            cache_data = _extraire_donnees_playlists(raw_playlists)
            logger(tache_id, f"[station {sid}] {len(cache_data)} playlist(s) récupérée(s) depuis l'API.")

            # Stocker dans la table de cache SQL (avec station_id)
            _inserer_cache_playlists_sql(cache_data, sid)

            for pl in cache_data:
                logger(tache_id, f"  [station {sid}][{pl['azura_id']}] {pl['name']} ({pl['nb_tracks']} titres, {pl['type']}, {'ON' if pl['is_enabled'] else 'OFF'})")

            logger(tache_id, f"[station {sid}] Cache mis à jour avec {len(cache_data)} playlist(s).")

        except AzuraCastAPIError as e:
            logger(tache_id, f"[station {sid}] ERREUR API : {e}")
        except Exception as e:
            logger(tache_id, f"[station {sid}] ERREUR INATTENDUE : {e}")

    logger(tache_id, "=== TERMINÉ SYNC CACHE ===")


def _synchroniser_cache_playlists(station_id=None):
    """Synchronisation silencieuse du cache pour UNE station (sans logger dans une tâche)."""
    if station_id is None:
        station_id = _DEFAULT_STATION_ID
    try:
        api = get_azura_api(station_id)
        if not api:
            return
        raw_playlists = api.list_playlists()
        cache_data = _extraire_donnees_playlists(raw_playlists)
        _inserer_cache_playlists_sql(cache_data, station_id)
    except Exception as e:
        print(f"[CACHE] Erreur sync silencieuse (station {station_id}) : {e}")


def _tache_store_json_result(tache_id, result_text):
    """Stocke un résultat JSON brut dans log_resultat de taches_planifiees.
    UPDATE direct (pas CONCAT) : le champ doit contenir exactement le JSON,
    sans rien d'autre, pour que app.py puisse faire json.loads() directement.
    Utilisé par plusieurs types de tâches (AUDIENCE_GET_DATA, PIGE_LIST, ...)."""
    db = pymysql.connect(**DB_CONFIG)
    cursor = db.cursor()
    cursor.execute(
        "UPDATE taches_planifiees SET log_resultat = %s WHERE id = %s",
        (result_text, tache_id)
    )
    db.commit()
    cursor.close()
    db.close()


def executer_pige_list(tache_id, parametres):
    """Évolution 1, Phase 1A — Liste les fichiers de piges d'une date donnée,
    stockés sur le VPS OVH 1 (pige.airvs.fr). Récupère taille + durée
    (via ffprobe, déjà présent sur le VPS pour l'Évolution 1 / PIGE_EXTRACT)
    en un seul aller-retour SSH.

    Paramètres JSON :
      - date : "YYYY-MM-DD" (obligatoire)
    """
    date_str = (parametres.get("date") or "").strip()
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        raise ValueError(f"Paramètre 'date' invalide ou absent : {date_str!r}")

    remote_dir = f"{PIGES_BASE_PATH}/{date_str}"

    # Une seule commande SSH : liste les .mp3, taille (stat) + durée (ffprobe).
    # Sortie : une ligne par fichier au format  nom|taille_octets|duree_sec
    shell_cmd = (
        f"cd {shlex.quote(remote_dir)} 2>/dev/null && "
        "for f in *.mp3; do "
        "  [ -e \"$f\" ] || continue; "
        "  s=$(stat -c%s \"$f\" 2>/dev/null); "
        "  d=$(ffprobe -v error -show_entries format=duration "
        "      -of default=noprint_wrappers=1:nokey=1 \"$f\" 2>/dev/null); "
        "  echo \"$f|$s|$d\"; "
        "done"
    )

    cmd = [
        "ssh", "-p", VPS_OVH1_SSH_PORT,
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        VPS_OVH1_SSH_HOST, shell_cmd
    ]

    resultat = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    if resultat.returncode != 0 and not (resultat.stdout or "").strip():
        erreur = (resultat.stderr or "").strip() or "Erreur SSH inconnue"
        logger(tache_id, f"PIGE_LIST erreur SSH: {erreur}")
        raise RuntimeError(f"SSH vers VPS OVH 1 a échoué: {erreur}")

    files = []
    total_size = 0
    for line in (resultat.stdout or "").splitlines():
        line = line.strip()
        if not line or '|' not in line:
            continue
        parts = line.split('|')
        if len(parts) != 3:
            continue
        name, size_str, dur_str = parts
        try:
            size_bytes = int(size_str)
        except (ValueError, TypeError):
            size_bytes = 0
        try:
            duration_sec = round(float(dur_str))
        except (ValueError, TypeError):
            duration_sec = None
        total_size += size_bytes
        files.append({
            "name": name,
            "size_bytes": size_bytes,
            "size_human": _humaniser_taille(size_bytes),
            "duration_sec": duration_sec,
        })

    files.sort(key=lambda f: f["name"])

    resultat_json = json.dumps({
        "date": date_str,
        "files": files,
        "total_size_bytes": total_size,
        "total_size_human": _humaniser_taille(total_size),
        "total_files": len(files),
    }, ensure_ascii=False)

    _tache_store_json_result(tache_id, resultat_json)  # même helper d'écriture directe


def _humaniser_taille(taille_octets):
    """Formate une taille en octets en chaîne lisible (55M, 1.3G, ...)."""
    taille = float(taille_octets)
    for unite in ('o', 'K', 'M', 'G'):
        if taille < 1024 or unite == 'G':
            return f"{taille:.0f}{unite}" if unite == 'o' else f"{taille:.1f}{unite}"
        taille /= 1024
    return f"{taille:.1f}G"


def _pige_ssh(remote_cmd, timeout=30):
    """Exécute une commande shell sur le VPS OVH 1 via SSH et retourne le
    CompletedProcess. Lève RuntimeError avec le message stderr en cas d'échec."""
    cmd = [
        "ssh", "-p", VPS_OVH1_SSH_PORT,
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        VPS_OVH1_SSH_HOST, remote_cmd
    ]
    resultat = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if resultat.returncode != 0:
        erreur = (resultat.stderr or "").strip() or "Erreur SSH inconnue"
        raise RuntimeError(f"SSH vers VPS OVH 1 a échoué: {erreur}")
    return resultat


def _pige_extraire_ou_copier(remote_source, remote_output, extract):
    """Prépare le MP3 sur le VPS OVH 1 : découpe ffmpeg si extract.enabled,
    sinon ré-encodage complet (normalise les frames, évite les fichiers
    bruts Icecast/Liquidsoap mal formés). Utilisé par PIGE_EXTRACT et
    PIGE_PROMOTE_PODCAST.

    Args:
        remote_source: chemin complet du MP3 source sur le VPS OVH 1
        remote_output: chemin complet du fichier de sortie sur le VPS OVH 1
        extract: dict {enabled, start_time, duration} ou None/{}

    Returns: dict {size_bytes, duration_sec} du fichier produit.
    """
    extract = extract or {}
    if extract.get("enabled"):
        start_time = extract.get("start_time", "00:00:00")
        duration = extract.get("duration", "00:10:00")
        if not re.match(r'^\d{1,2}:\d{2}:\d{2}$', start_time) or not re.match(r'^\d{1,2}:\d{2}:\d{2}$', duration):
            raise ValueError("Format start_time/duration invalide (attendu HH:MM:SS)")
        # La durée ffprobe n'est qu'informative : elle ne doit jamais faire
        # échouer toute la chaîne (ffmpeg/cp + upload) si le MP3 a une
        # structure de frame inhabituelle. "|| echo 0" garantit que la
        # commande complète réussit toujours et que duration_sec retombe à 0.
        remote_cmd = (
            f"ffmpeg -y -i {shlex.quote(remote_source)} "
            f"-ss {shlex.quote(start_time)} -t {shlex.quote(duration)} "
            f"-c:a libmp3lame -b:a 192k {shlex.quote(remote_output)} "
            f"2>&1 && stat -c%s {shlex.quote(remote_output)} "
            f"&& (ffprobe -v error -show_entries format=duration -of csv=p=0 {shlex.quote(remote_output)} || echo 0)"
        )
    else:
        # Ré-encodage systématique (plutôt qu'une simple copie) : les MP3
        # bruts issus d'Icecast/Liquidsoap ont parfois une structure de
        # frame abîmée (cf. erreur ffprobe "Failed to read frame size").
        # AzuraCast peut alors accepter l'upload en apparence (HTTP 200,
        # "success": true) sans jamais ingérer le fichier (has_media reste
        # False). Le ré-encodage reconstruit des frames MP3 propres et
        # standard, ce qui corrige ce problème à la source.
        remote_cmd = (
            f"ffmpeg -y -i {shlex.quote(remote_source)} "
            f"-c:a libmp3lame -b:a 192k {shlex.quote(remote_output)} "
            f"2>&1 && stat -c%s {shlex.quote(remote_output)} "
            f"&& (ffprobe -v error -show_entries format=duration -of csv=p=0 {shlex.quote(remote_output)} || echo 0)"
        )

    resultat = _pige_ssh(remote_cmd, timeout=180)  # ffmpeg peut prendre du temps sur 1h de son
    lignes = [l for l in (resultat.stdout or "").strip().splitlines() if l.strip()]
    # Les 2 dernières lignes utiles sont : taille, puis durée (ffmpeg peut logger avant)
    size_bytes = int(lignes[-2]) if len(lignes) >= 2 else 0
    try:
        duration_sec = round(float(lignes[-1])) if lignes else 0
    except ValueError:
        duration_sec = 0
    return {"size_bytes": size_bytes, "duration_sec": duration_sec}


def executer_pige_extract(tache_id, parametres):
    """Évolution 1, Phase 1C — Extrait un segment d'une pige via ffmpeg
    (exécuté à distance sur le VPS OVH 1), sans upload vers Azuracast.
    Utile pour prévisualiser une découpe avant promotion.

    Paramètres JSON :
      - source_file : chemin complet sur le VPS OVH 1 (ex: /var/www/pige/2026-07-15/14-00.mp3)
      - start_time : "HH:MM:SS"
      - duration : "HH:MM:SS"
      - output_name : nom du fichier de sortie (optionnel, généré sinon)
    """
    source_file = parametres.get("source_file", "")
    if not source_file.startswith(PIGES_BASE_PATH):
        raise ValueError(f"source_file doit être sous {PIGES_BASE_PATH}")

    output_name = parametres.get("output_name") or f"extrait-{tache_id}.mp3"
    remote_output = f"/tmp/{output_name}"

    infos = _pige_extraire_ou_copier(
        remote_source=source_file,
        remote_output=remote_output,
        extract={
            "enabled": True,
            "start_time": parametres.get("start_time", "00:00:00"),
            "duration": parametres.get("duration", "00:10:00"),
        }
    )

    resultat_json = json.dumps({
        "output_path": remote_output,
        "output_name": output_name,
        "size_bytes": infos["size_bytes"],
        "size_human": _humaniser_taille(infos["size_bytes"]),
        "duration_sec": infos["duration_sec"],
    }, ensure_ascii=False)
    _tache_store_json_result(tache_id, resultat_json)


def _piges_promotions_update(promo_id, **champs):
    """Met à jour une ligne de airvs_piges_promotions (statut, episode_id, erreur, ...)."""
    if not promo_id or not champs:
        return
    db = pymysql.connect(**DB_CONFIG)
    cursor = db.cursor()
    set_clause = ", ".join(f"{k} = %s" for k in champs)
    cursor.execute(
        f"UPDATE airvs_piges_promotions SET {set_clause} WHERE id = %s",
        (*champs.values(), promo_id)
    )
    db.commit()
    cursor.close()
    db.close()


def executer_pige_promote_podcast(tache_id, parametres):
    """Évolution 1, Phase 1D — Chaîne complète de promotion d'une pige en
    épisode de podcast sur Azuracast #2.

    Paramètres JSON (cf. app.py /api/piges/promote) :
      - promo_id : ID de la ligne airvs_piges_promotions associée
      - source_file : chemin complet sur le VPS OVH 1
      - extract : {enabled, start_time, duration} ou absent
      - podcast_metadata : {podcast_id, title, description, episode_number,
                             season_number, publish_date, explicit}
      - artwork_windows_path : chemin Windows de l'artwork uploadé (optionnel)
    """
    if not AZURA_API_MODULE_DISPONIBLE:
        raise RuntimeError("Module azuracast_api.py indisponible — impossible de promouvoir le podcast")

    promo_id = parametres.get("promo_id")
    source_file = parametres.get("source_file", "")
    if not source_file.startswith(PIGES_BASE_PATH):
        raise ValueError(f"source_file doit être sous {PIGES_BASE_PATH}")

    meta = parametres.get("podcast_metadata", {})
    podcast_id = meta.get("podcast_id")
    title = meta.get("title", "").strip()
    if not podcast_id or not title:
        raise ValueError("podcast_metadata.podcast_id et .title sont obligatoires")

    local_mp3 = None
    remote_tmp = None
    succes = False
    try:
        if promo_id:
            _piges_promotions_update(promo_id, status='en_cours')

        # 1) Préparation du MP3 sur le VPS OVH 1 (découpe ou copie)
        remote_tmp = f"/tmp/podcast-promote-{tache_id}.mp3"
        logger(tache_id, "Préparation du MP3 sur le VPS OVH 1...")
        _pige_extraire_ou_copier(source_file, remote_tmp, parametres.get("extract"))

        # 2) Rapatriement du MP3 vers Ubuntu Studio (scp) — le worker a besoin
        #    du fichier en local pour l'uploader vers Azuracast #2 (upload API,
        #    pas de SFTP direct VPS-à-VPS dans cette implémentation).
        logger(tache_id, "Récupération du MP3 depuis le VPS OVH 1 (scp)...")
        local_mp3 = f"/tmp/airvs_podcast_{tache_id}.mp3"
        scp_cmd = [
            "scp", "-P", VPS_OVH1_SSH_PORT, "-o", "BatchMode=yes",
            f"{VPS_OVH1_SSH_HOST}:{remote_tmp}", local_mp3
        ]
        scp_res = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=180)
        if scp_res.returncode != 0:
            raise RuntimeError(f"scp du MP3 a échoué: {(scp_res.stderr or '').strip()}")

        # 3) Connexion à Azuracast #2 + création de l'épisode (métadonnées seules)
        logger(tache_id, "Création de l'épisode sur Azuracast #2...")
        azuracast2 = get_api_for_azuracast2()
        episode = azuracast2.create_podcast_episode(
            podcast_id=podcast_id,
            title=title,
            description=meta.get("description", ""),
            episode_number=meta.get("episode_number"),
            season_number=meta.get("season_number"),
            publish_at=meta.get("publish_date"),
            explicit=meta.get("explicit", False),
        )
        episode_id = episode.get("id")
        if not episode_id:
            raise RuntimeError(f"Azuracast #2 n'a pas renvoyé d'ID d'épisode : {episode}")

        # 4) Upload du média audio — upload_podcast_episode_media() vérifie
        #    désormais elle-même (GET de contrôle) que le fichier a bien été
        #    ingéré par AzuraCast, et lève une erreur explicite sinon (au
        #    lieu du faux {"success": true} générique de l'API). Le dict
        #    renvoyé est donc directement l'objet 'media' réel de l'épisode.
        logger(tache_id, f"Upload du MP3 vers l'épisode {episode_id}...")
        media_response = azuracast2.upload_podcast_episode_media(podcast_id, episode_id, local_mp3)

        # 5) Upload de l'artwork si fourni (chemin Windows -> traduction Linux)
        artwork_windows_path = parametres.get("artwork_windows_path")
        if artwork_windows_path:
            try:
                logger(tache_id, "Upload de l'artwork...")
                artwork_local = resoudre_chemin_insensible(windows_vers_linux(artwork_windows_path))
                azuracast2.upload_podcast_episode_art(podcast_id, episode_id, str(artwork_local))
            except Exception as e:
                # L'artwork n'est pas bloquant pour le reste de la promotion.
                logger(tache_id, f"AVERTISSEMENT artwork non uploadé: {e}")

        # 6) Publication si la date de publication est passée/immédiate
        publish_date = meta.get("publish_date")
        try:
            doit_publier = True
            if publish_date:
                pub_dt = datetime.strptime(publish_date.replace('Z', ''), '%Y-%m-%dT%H:%M:%S')
                doit_publier = pub_dt <= datetime.utcnow()
            if doit_publier:
                azuracast2.publish_podcast_episode(podcast_id, episode_id)
        except Exception as e:
            logger(tache_id, f"AVERTISSEMENT publication non confirmée: {e}")

        # Priorité au lien renvoyé par l'API elle-même si présent (structure
        # "links" observée sur les podcasts, probablement aussi sur les
        # épisodes) ; sinon, on reconstruit le lien de la page podcast
        # publique (format confirmé : /public/{station_id}/podcast/{podcast_id}
        # — il n'existe pas de lien public par épisode individuel constaté).
        episode_url = (
            (episode.get('links') or {}).get('public')
            or f"https://web.podcast.2026.airvs.fr/public/{azuracast2.station_id}/podcast/{podcast_id}"
        )

        # 7) Mise à jour de la ligne de suivi + du résultat de tâche
        if promo_id:
            _piges_promotions_update(
                promo_id, status='termine',
                episode_id=episode_id, episode_url=episode_url
            )

        _duree_brute = media_response.get("length") or media_response.get("duration")
        resultat_json = json.dumps({
            "episode_id": episode_id,
            "episode_url": episode_url,
            "media_id": media_response.get("id") or media_response.get("media_id"),
            "duration_sec": round(_duree_brute) if _duree_brute is not None else None,
        }, ensure_ascii=False)
        _tache_store_json_result(tache_id, resultat_json)
        succes = True

    except Exception as e:
        if promo_id:
            try:
                _piges_promotions_update(promo_id, status='erreur', error_message=str(e)[:2000])
            except Exception:
                pass
        raise

    finally:
        # Nettoyage best-effort (ne doit jamais faire échouer la tâche).
        # En cas d'ÉCHEC, on conserve volontairement local_mp3/remote_tmp
        # pour permettre l'inspection post-mortem exacte du fichier qui a
        # fait échouer l'ingestion côté AzuraCast (cf. débogage has_media
        # False persistant) — nettoyage normal uniquement si tout a réussi.
        if succes:
            if local_mp3:
                try:
                    os.remove(local_mp3)
                except OSError:
                    pass
            if remote_tmp:
                try:
                    _pige_ssh(f"rm -f {shlex.quote(remote_tmp)}", timeout=15)
                except Exception:
                    pass
        else:
            if local_mp3:
                logger(tache_id, f"Échec — MP3 local conservé pour inspection : {local_mp3}")
            if remote_tmp:
                logger(tache_id, f"Échec — MP3 distant (VPS OVH 1) conservé pour inspection : {remote_tmp}")


def executer_podcasts_list(tache_id, parametres):
    """Liste les podcasts définis sur Azuracast #2 (support de /api/podcasts/list)."""
    if not AZURA_API_MODULE_DISPONIBLE:
        raise RuntimeError("Module azuracast_api.py indisponible")
    azuracast2 = get_api_for_azuracast2()
    podcasts = azuracast2.list_podcasts()
    _tache_store_json_result(tache_id, json.dumps({"podcasts": podcasts}, ensure_ascii=False))


def executer_podcasts_episodes(tache_id, parametres):
    """Liste les épisodes d'un podcast Azuracast #2 (support de /api/podcasts/episodes/<id>)."""
    if not AZURA_API_MODULE_DISPONIBLE:
        raise RuntimeError("Module azuracast_api.py indisponible")
    podcast_id = parametres.get("podcast_id")
    if not podcast_id:
        raise ValueError("Paramètre 'podcast_id' obligatoire")
    azuracast2 = get_api_for_azuracast2()
    episodes = azuracast2.list_podcast_episodes(podcast_id)
    _tache_store_json_result(tache_id, json.dumps({"episodes": episodes}, ensure_ascii=False))


def executer_audience_get_data(tache_id, parametres):
    """Évolution 3 — Récupère les données d'audience agrégées (Icecast +
    Azuracast #1 + #2) depuis le dashboard airvs-stats-aggregator hébergé
    sur le VPS OVH 1 (127.0.0.1:8050, accessible uniquement en local sur
    le VPS -> on passe par SSH + curl).

    Paramètres JSON :
      - endpoint : "realtime" (défaut) ou "history?hours=24"
    """
    endpoint = parametres.get("endpoint", "realtime")

    cmd = [
        "ssh", "-p", VPS_OVH1_SSH_PORT,
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        VPS_OVH1_SSH_HOST,
        f"curl -s --max-time 8 http://127.0.0.1:8050/api/{endpoint}"
    ]

    resultat = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

    if resultat.returncode != 0:
        erreur = (resultat.stderr or "").strip() or "Erreur SSH inconnue"
        logger(tache_id, f"AUDIENCE_GET_DATA erreur SSH: {erreur}")
        raise RuntimeError(f"SSH vers VPS OVH 1 a échoué: {erreur}")

    sortie = (resultat.stdout or "").strip()
    if not sortie:
        raise RuntimeError("Réponse vide depuis l'agrégateur (service airvs-stats-dashboard down ?)")

    # Valider que c'est bien du JSON avant de le stocker, pour ne jamais
    # propager du HTML d'erreur ou un message texte vers app.py.
    try:
        json.loads(sortie)
    except json.JSONDecodeError as e:
        logger(tache_id, f"AUDIENCE_GET_DATA erreur: réponse non-JSON ({e}) : {sortie[:200]}")
        raise RuntimeError("Réponse de l'agrégateur non-JSON")

    _tache_store_json_result(tache_id, sortie)


def executer_sync_azura_status(tache_id, parametres):
    """Compare les fichiers AzuraCast avec RadioDJ, met à jour comments='IN_AZURACAST'
    (badge global = présent dans au moins une station), et rafraîchit le cache des dossiers.

    Parametres :
      - station_ids: liste d'IDs de stations à interroger (ex: [6, 7]).
                     Si absent → toutes les stations de config.json.
      - badge_global: True (défaut) → badge si présent dans au moins une station.

    Sécurité dossiers (v2026.08.31) :
      - Utilise /files/directories pour découvrir les dossiers vides
      - Sync différentielle : INSERT nouveaux, DELETE absents (avec seuil de sécurité)
      - Ne JAMAIS vider le cache si l'API retourne trop peu de résultats
    """
    # Résoudre la liste des stations à interroger
    station_ids = parametres.get('station_ids')
    if not station_ids or not isinstance(station_ids, list):
        station_ids = [s['id'] for s in lister_stations_config()]
    else:
        station_ids = [int(s) for s in station_ids]

    if not station_ids:
        station_ids = [_DEFAULT_STATION_ID]

    logger(tache_id, f"Synchronisation IN_AZURACAST démarrée pour station(s) {station_ids} (badge global)")

    # 1. Récupérer tous les fichiers d'AzuraCast via l'API (POUR TOUTES LES STATIONS)
    all_azura_files = set()
    all_folders = set()  # Dossiers agrégés (les 2 stations partagent sshfs_root)
    total_rows = 0
    api_ok_count = 0  # Nombre de stations dont l'API a répondu avec succès

    for sid in station_ids:
        try:
            api_url, api_key = _url_key_for_station(sid)
            headers = {"Authorization": f"Bearer {api_key}"}
            logger(tache_id, f"[station {sid}] Interrogation API {api_url}...")
            resp = requests.get(api_url, headers=headers, params={"limit": 10000}, timeout=60)
            if resp.status_code != 200:
                logger(tache_id, f"[station {sid}] ERREUR: API inaccessible (code {resp.status_code})")
                continue

            data = resp.json()
            rows = data if isinstance(data, list) else data.get("rows", [])
            logger(tache_id, f"[station {sid}] {len(rows)} fichier(s) récupéré(s)")
            total_rows += len(rows)
            api_ok_count += 1

            for morceau in rows:
                if not isinstance(morceau, dict):
                    continue
                fname = morceau.get("path", "")
                if fname:
                    basename = fname.rsplit('/', 1)[-1] if '/' in fname else fname
                    all_azura_files.add(basename.lower().strip())
                    if '/' in fname:
                        parts = fname.split('/')
                        for i in range(1, len(parts)):
                            all_folders.add('/'.join(parts[:i]))
                artist = str(morceau.get("artist", "") or "").strip()
                title = str(morceau.get("title", "") or "").strip()
                if artist and title:
                    chaine = normaliser_pour_recherche(f"{artist} - {title}")
                    all_azura_files.add(chaine)
        except Exception as e:
            logger(tache_id, f"[station {sid}] ERREUR API: {e}")
            continue

    # 1b. Découvrir les dossiers via /files/directories (dossiers ayant contenu des médias)
    dirs_ok_count = 0
    for sid in station_ids:
        try:
            api_url, api_key = _url_key_for_station(sid)
            # Remplacer /files par /files/directories
            dirs_url = api_url.replace('/files', '/files/directories', 1)
            if dirs_url == api_url:
                # Fallback si le remplacement n'a pas fonctionné
                base = api_url.rsplit('/files', 1)[0]
                dirs_url = base + '/files/directories'
            headers = {"Authorization": f"Bearer {api_key}"}
            resp = requests.get(dirs_url, headers=headers, timeout=30)
            if resp.status_code == 200:
                dirs_data = resp.json()
                dirs_list = dirs_data if isinstance(dirs_data, list) else dirs_data.get('rows', dirs_data.get('directories', []))
                for d in dirs_list:
                    if isinstance(d, dict):
                        path = d.get('path', d.get('name', ''))
                    elif isinstance(d, str):
                        path = d
                    else:
                        continue
                    if path and path.strip():
                        all_folders.add(path.strip().strip('/'))
                dirs_ok_count += 1
                logger(tache_id, f"[station {sid}] {len(dirs_list)} répertoire(s) via /directories")
            else:
                logger(tache_id, f"[station {sid}] /directories indisponible (code {resp.status_code}), folders extraits des fichiers uniquement")
        except Exception as e:
            logger(tache_id, f"[station {sid}] /directories erreur: {e}")

    # 1c. Scanner le filesystem SSHFS (dossiers vides JAMAIS scannés par AzuraCast)
    #     Attrape les dossiers créés via interface AzuraCast, SSHFS, SFTP, etc.
    fs_folders_count = 0
    try:
        if os.path.isdir(AZURA_SSHFS_ROOT):
            for entry in os.scandir(AZURA_SSHFS_ROOT):
                if entry.is_dir(follow_symlinks=False) and not entry.name.startswith('.'):
                    all_folders.add(entry.name)
                    fs_folders_count += 1
                    # Scanner aussi les sous-dossiers (ex: NOUVELLES_ENTREES/SEMAINE_36)
                    try:
                        for sub in os.scandir(entry.path):
                            if sub.is_dir(follow_symlinks=False) and not sub.name.startswith('.'):
                                all_folders.add(entry.name + '/' + sub.name)
                                fs_folders_count += 1
                    except PermissionError:
                        pass
            logger(tache_id, f"[filesystem] {fs_folders_count} répertoire(s) trouvés via SSHFS ({AZURA_SSHFS_ROOT})")
        else:
            logger(tache_id, f"[filesystem] SSHFS non monté ({AZURA_SSHFS_ROOT}), scan ignoré")
    except Exception as e:
        logger(tache_id, f"[filesystem] erreur scan SSHFS: {e}")

    all_folders.add('imports_push')  # Dossier par défaut
    logger(tache_id, f"Total agrégé: {total_rows} fichier(s) sur {api_ok_count}/{len(station_ids)} station(s) OK, {len(all_folders)} dossier(s) uniques")

    # 2. Mettre à jour le cache des dossiers (SYNC DIFFÉRENTIELLE avec sécurité)
    #    ── Ne plus utiliser DELETE + INSERT (trop risqué) ──
    #    Nouvelle stratégie :
    #      a) Lire les dossiers existants en cache
    #      b) INSÉRER les nouveaux dossiers
    #      c) SUPPRIMER les dossiers qui n'existent plus dans AzuraCast
    #         (seulement si au moins 1 station API a répondu ET le ratio est acceptable)
    try:
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor(pymysql.cursors.DictCursor)
        # Créer la table AVEC colonne station_id (multi-station)
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS azuracast_folders_cache ("
            "  id INT AUTO_INCREMENT PRIMARY KEY,"
            "  folder_path VARCHAR(500) NOT NULL,"
            "  station_id TINYINT NOT NULL DEFAULT 7,"
            "  date_sync DATETIME DEFAULT CURRENT_TIMESTAMP,"
            "  UNIQUE KEY uq_folder_station (folder_path, station_id)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
        # Détecter si l'ancienne table existe sans colonne station_id (migration douce)
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'azuracast_folders_cache' "
                "AND column_name = 'station_id'"
            )
            col_existe = cursor.fetchone()
            has_station_col = (
                (isinstance(col_existe, dict) and col_existe.get('COUNT(*)', 0) > 0) or
                (isinstance(col_existe, (list, tuple)) and col_existe[0] > 0)
            )
        except Exception:
            has_station_col = False

        # Migration douce : ajouter la colonne station_id si elle manque
        if not has_station_col:
            try:
                cursor.execute(
                    "ALTER TABLE azuracast_folders_cache ADD COLUMN station_id TINYINT NOT NULL DEFAULT 7"
                )
                logger(tache_id, "Migration: colonne station_id ajoutée à azuracast_folders_cache")
                has_station_col = True
            except Exception as e:
                logger(tache_id, f"Migration folders impossible: {e}")

        # ── SYNC DIFFÉRENTIELLE ──
        for sid in station_ids:
            # 2a. Lire les dossiers actuels en cache pour cette station
            cursor.execute(
                "SELECT folder_path FROM azuracast_folders_cache WHERE station_id = %s",
                (sid,)
            )
            existing_rows = cursor.fetchall()
            existing_folders = set()
            for row in existing_rows:
                if isinstance(row, dict):
                    existing_folders.add(row.get('folder_path', ''))
                elif isinstance(row, (list, tuple)):
                    existing_folders.add(row[0])

            # 2b. Insérer les NOUVEAUX dossiers (ceux qui ne sont pas encore en cache)
            new_folders = all_folders - existing_folders
            inserted = 0
            for folder in sorted(new_folders):
                try:
                    cursor.execute(
                        "INSERT IGNORE INTO azuracast_folders_cache (folder_path, station_id) VALUES (%s, %s)",
                        (folder, sid)
                    )
                    if cursor.rowcount > 0:
                        inserted += 1
                except Exception:
                    pass

            # 2c. Supprimer les dossiers obsolètes (présents en cache mais plus dans AzuraCast)
            #     Sécurité : ne supprimer QUE si :
            #       - Au moins 1 station a répondu à l'API fichiers
            #       - Le nouveau set n'est pas trop petit vs l'ancien (seuil 50%)
            deleted = 0
            if api_ok_count > 0 and len(all_folders) > 0:
                # Seuil de sécurité : si le nouveau set a moins de 50% des anciens dossiers,
                # c'est probablement une erreur API → on ne supprime rien
                ratio = len(all_folders) / max(len(existing_folders), 1)
                if ratio >= 0.5:
                    obsolete = existing_folders - all_folders
                    for folder in sorted(obsolete):
                        try:
                            cursor.execute(
                                "DELETE FROM azuracast_folders_cache WHERE folder_path = %s AND station_id = %s",
                                (folder, sid)
                            )
                            deleted += cursor.rowcount
                        except Exception:
                            pass
                else:
                    logger(tache_id, f"[SECURITE] Ratio dossiers {ratio:.0%} < 50% pour station {sid} — suppression obsolètes ANNULÉE (API probablement incomplète)")
            else:
                logger(tache_id, f"[SECURITE] Aucune station API OK — suppression obsolètes ANNULÉE pour station {sid}")

            logger(tache_id, f"[station {sid}] Cache dossiers: +{inserted} nouveaux, -{deleted} obsolètes, {len(all_folders)} total")

        db.commit()
        cursor.close()
        db.close()
        logger(tache_id, f"Cache dossiers mis à jour: {len(all_folders)} dossier(s) × {len(station_ids)} station(s) (sync différentielle)")
    except Exception as e:
        logger(tache_id, f"ERREUR cache dossiers: {e}")

    # 3. Comparer avec la base RadioDJ et marquer IN_AZURACAST (badge global)
    try:
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute("SELECT ID, `path`, artist, title FROM songs WHERE song_type = 0")
        all_songs = cursor.fetchall()

        ids_a_marquer = []
        for song in all_songs:
            song_path = song.get('path', '')
            song_id = song.get('ID')
            if not song_path:
                continue

            # Comparaison par nom de fichier
            basename = song_path.rsplit('\\', 1)[-1] if '\\' in song_path else song_path.rsplit('/', 1)[-1]
            if basename.lower().strip() in all_azura_files:
                ids_a_marquer.append(song_id)
                continue

            # Comparaison par artist - titre (fallback)
            song_artist = str(song.get('artist', '') or '').strip()
            song_title = str(song.get('title', '') or '').strip()
            if song_artist and song_title:
                chaine = normaliser_pour_recherche(f"{song_artist} - {song_title}")
                if chaine in all_azura_files:
                    ids_a_marquer.append(song_id)

        logger(tache_id, f"{len(all_songs)} titre(s) dans RadioDJ, {len(ids_a_marquer)} correspondance(s) trouvée(s)")

        # Mettre à jour les badges IN_AZURACAST
        updated = 0
        if ids_a_marquer:
            for i in range(0, len(ids_a_marquer), 100):
                batch = ids_a_marquer[i:i+100]
                placeholders = ','.join(['%s'] * len(batch))
                cursor.execute(
                    f"UPDATE songs SET comments = 'IN_AZURACAST' WHERE ID IN ({placeholders}) AND (comments IS NULL OR comments != 'IN_AZURACAST')",
                    batch
                )
                updated += cursor.rowcount
            db.commit()

        cursor.close()
        db.close()

        logger(tache_id, f"{updated} titre(s) marqué(s) IN_AZURACAST")
        logger(tache_id, "=== TERMINÉ SYNC AZURA STATUS ===")

    except Exception as e:
        logger(tache_id, f"ERREUR DB: {e}")
        logger(tache_id, "=== TERMINÉ SYNC AZURA STATUS (ERREUR) ===")


# ════════════════════════════════════════════════════════════════════════
# PROGRAMMATION AVANCÉE (Phase 1)
# ════════════════════════════════════════════════════════════════════════

# Plage de tolérance (en secondes) pour le déclenchement d'un créneau.
# Si NOW() est à moins de PLAGE_TOLERANCE_SEC de l'heure du créneau, on déclenche.
PLAGE_TOLERANCE_SEC = 300  # 5 minutes

# Intervalle (en secondes) entre deux vérifications de la grille par main().
INTERVALLE_VERIFICATION_COULEUR = 60
INTERVALLE_AUTO_CHECK = 300  # Vérifie auto-check config toutes les 5 min
INTERVALLE_AUTO_VALIDATION = 300  # Vérifie auto-validation pool toutes les 5 min
INTERVALLE_SYNC_AZURA_AUTO = 3600  # Vérifie sync auto AzuraCast toutes les heures
SYNC_AZURA_AUTO_HEURE_DEBUT = 8   # Plage horaire : 8h
SYNC_AZURA_AUTO_HEURE_FIN = 20    # Plage horaire : 20h

# ── Sync VPS OVH 1 (programmes.airvs.fr) ──
VPS_SYNC_URL = "https://programmes.airvs.fr"
VPS_SYNC_TOKEN = os.getenv("VPS_SYNC_TOKEN", "")  # Token API du VPS
INTERVALLE_SYNC_VPS = 300  # Vérifie sync VPS toutes les 5 min
INTERVALLE_CLEANUP_ORPHELINES = 3600  # Nettoyage tâches orphelines toutes les heures

# Types d'action qui ne sont jamais traités par le worker mais peuvent être
# insérés par le dashboard (annulations, etc.)
TYPES_ORPHELINS_EN_ATTENTE = ('PUSH_CANCEL', 'SHAZAM_PREFILL_CANCEL')


def _assurer_schema_airvs_couleur_worker():
    """Vérifie/crée les tables airvs_grille_couleur et airvs_historique_couleur.
    Appelée par main() au démarrage du worker."""
    try:
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS airvs_grille_couleur (
              id INT AUTO_INCREMENT PRIMARY KEY,
              jour_semaine TINYINT DEFAULT 0,
              heure TIME NOT NULL,
              date_debut DATE DEFAULT NULL,
              date_fin DATE DEFAULT NULL,
              station_id TINYINT NOT NULL DEFAULT 7,
              type_couleur ENUM('artist','genre','subcategory','category','titre','keyword') NOT NULL,
              valeur_couleur VARCHAR(255) NOT NULL,
              titres_ids JSON DEFAULT NULL,
              nb_titres INT NOT NULL DEFAULT 20,
              mode_playlist ENUM('ajouter_existantes','creer_nouvelle','remplacer')
                  NOT NULL DEFAULT 'ajouter_existantes',
              playlist_ids JSON DEFAULT NULL,
              nom_nouvelle_playlist VARCHAR(255) DEFAULT NULL,
              anti_repetition_jours INT NOT NULL DEFAULT 7,
              dossier_cible VARCHAR(255) NOT NULL DEFAULT 'imports_push',
              actif TINYINT NOT NULL DEFAULT 1,
              dry_run TINYINT NOT NULL DEFAULT 1,
              plage_h_debut TIME DEFAULT '06:00:00',
              plage_h_fin TIME DEFAULT '23:00:00',
              dernier_lancement DATETIME DEFAULT NULL,
              commentaire TEXT,
              date_creation DATETIME DEFAULT CURRENT_TIMESTAMP,
              INDEX idx_grille_planif (actif, jour_semaine, heure),
              INDEX idx_grille_event (actif, date_debut, date_fin)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        # Migration douce : modifier l'ENUM et ajouter titres_ids si manquants
        try:
            cursor.execute(
                "ALTER TABLE airvs_grille_couleur "
                "MODIFY COLUMN type_couleur "
                "ENUM('artist','genre','subcategory','category','titre','keyword') NOT NULL"
            )
        except Exception:
            pass
        try:
            cursor.execute(
                "ALTER TABLE airvs_grille_couleur "
                "ADD COLUMN IF NOT EXISTS titres_ids JSON DEFAULT NULL"
            )
        except Exception:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'airvs_grille_couleur' "
                "AND column_name = 'titres_ids'"
            )
            if cursor.fetchone()[0] == 0:
                cursor.execute(
                    "ALTER TABLE airvs_grille_couleur "
                    "ADD COLUMN titres_ids JSON DEFAULT NULL"
                )
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS airvs_historique_couleur (
              id INT AUTO_INCREMENT PRIMARY KEY,
              grille_id INT NOT NULL,
              song_id INT NOT NULL,
              station_id TINYINT NOT NULL,
              date_lancement DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              tache_id INT DEFAULT NULL,
              mode_playlist VARCHAR(20) DEFAULT NULL,
              playlist_cible VARCHAR(255) DEFAULT NULL,
              INDEX idx_song_date (song_id, date_lancement),
              INDEX idx_grille_date (grille_id, date_lancement),
              INDEX idx_station_date (station_id, date_lancement)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # ── 2026.06.24-B : Tables pour le mode Pool de titres validés ──
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS airvs_pool_titres (
              id              INT AUTO_INCREMENT PRIMARY KEY,
              id_pool         VARCHAR(80)  NOT NULL,
              sheet_alias     VARCHAR(40)  NOT NULL,
              source          VARCHAR(50)  NOT NULL,
              feuille         VARCHAR(50)  NULL,
              ligne           INT          NULL,
              semaine         VARCHAR(10)  NULL,
              jour            VARCHAR(10)  NULL,
              song_id         INT          NULL,
              artiste_saisi   VARCHAR(255) NOT NULL,
              titre_saisi     VARCHAR(255) NOT NULL,
              annee_saisie    INT          NULL,
              album_saisi     VARCHAR(255) NULL,
              infos_saisi     TEXT         NULL,
              statut          ENUM('valide','rejete','doublon') NOT NULL DEFAULT 'valide',
              score_match     INT          NULL,
              motif_rejet     VARCHAR(255) NULL,
              date_creation   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
              date_validation DATETIME     NULL,
              valide_par      VARCHAR(100) NULL,
              INDEX idx_pool_statut (id_pool, statut),
              INDEX idx_song (song_id),
              INDEX idx_source (source),
              INDEX idx_date_val (date_validation)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # 2026-06-25-C : migrations idempotentes pour réconcilier le schéma
        # legacy (depuis dashboard.sql, qui avait song_id NOT NULL, onglet NOT
        # NULL sans DEFAULT, et statut ENUM('valide','manquant')) avec le
        # schéma attendu par ce worker multiformat.
        # Sans ces ALTER, sur une base existante, les INSERT crashent avec :
        #   - IntegrityError 1048 "Column 'song_id' cannot be null"
        #     (worker insère None pour les rejetés, schéma legacy NOT NULL)
        #   - "Field 'onglet' doesn't have a default value"
        #     (worker n'insère pas onglet, schéma legacy sans DEFAULT)
        #   - "Data truncated for column 'statut'" (worker insère 'rejete',
        #     schéma legacy ENUM sans 'rejete')
        try:
            # song_id : NOT NULL → NULL DEFAULT 0
            cursor.execute(
                "SELECT IS_NULLABLE FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'airvs_pool_titres' "
                "AND column_name = 'song_id'"
            )
            row = cursor.fetchone()
            if row and row[0] == 'NO':
                cursor.execute(
                    "ALTER TABLE airvs_pool_titres "
                    "MODIFY COLUMN song_id INT NULL DEFAULT 0"
                )
                print("[Worker Schema] airvs_pool_titres.song_id : NOT NULL → NULL DEFAULT 0 (migration)")
        except Exception as e:
            print(f"[Worker Schema] ALTER song_id NULL DEFAULT 0 ignoré : {e}")

        try:
            # onglet : NOT NULL → NOT NULL DEFAULT '' (le worker n'insère pas onglet)
            cursor.execute(
                "SELECT COLUMN_DEFAULT FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'airvs_pool_titres' "
                "AND column_name = 'onglet'"
            )
            row = cursor.fetchone()
            if row and row[0] is None:
                cursor.execute(
                    "ALTER TABLE airvs_pool_titres "
                    "MODIFY COLUMN onglet VARCHAR(60) NOT NULL DEFAULT ''"
                )
                print("[Worker Schema] airvs_pool_titres.onglet : DEFAULT '' ajouté (migration)")
        except Exception as e:
            print(f"[Worker Schema] ALTER onglet DEFAULT '' ignoré : {e}")

        try:
            # statut : ENUM('valide','manquant') → ENUM('valide','manquant','rejete','doublon')
            cursor.execute(
                "SELECT COLUMN_TYPE FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = 'airvs_pool_titres' "
                "AND column_name = 'statut'"
            )
            row = cursor.fetchone()
            if row:
                col_type = (row[0] or '').lower()
                valeurs_manquantes = [v for v in ("'rejete'", "'doublon'", "'manquant'") if v not in col_type]
                if valeurs_manquantes:
                    cursor.execute(
                        "ALTER TABLE airvs_pool_titres "
                        "MODIFY COLUMN statut ENUM('valide','manquant','rejete','doublon') "
                        "NOT NULL DEFAULT 'valide'"
                    )
                    print(f"[Worker Schema] airvs_pool_titres.statut : ENUM étendu avec {valeurs_manquantes}")
        except Exception as e:
            print(f"[Worker Schema] ALTER statut ENUM ignoré : {e}")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS airvs_pool_meta (
              id             INT AUTO_INCREMENT PRIMARY KEY,
              nom            VARCHAR(100) NOT NULL UNIQUE,
              source         VARCHAR(50)  NOT NULL,
              sheet_alias    VARCHAR(40)  NULL,
              jour_defaut    VARCHAR(10)  NULL,
              actif          TINYINT(1)   NOT NULL DEFAULT 1,
              date_creation  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS airvs_pool_auto_validation (
              id                  INT AUTO_INCREMENT PRIMARY KEY,
              sheet_alias         VARCHAR(40)  NOT NULL,
              onglet              VARCHAR(60)  NOT NULL,
              frequence           ENUM('quotidienne','hebdo') NOT NULL DEFAULT 'quotidienne',
              heure               TIME         NOT NULL DEFAULT '03:00:00',
              jour_semaine        TINYINT      NULL,
              actif               TINYINT      NOT NULL DEFAULT 1,
              derniere_execution  DATETIME     NULL,
              date_creation       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
              INDEX idx_actif (actif)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        # Insertion des 3 pools par défaut (Vince, Laurent, Mary)
        cursor.executemany(
            "INSERT IGNORE INTO airvs_pool_meta (nom, source, sheet_alias, jour_defaut, actif) "
            "VALUES (%s, %s, %s, %s, 1)",
            [
                ('Vince',   'vince',   'titres_vince',   None),
                ('Laurent', 'laurent', 'titres_laurent', None),
                ('Mary',    'mary',    'titres_mary',    'Me'),
            ]
        )
        # Étendre l'ENUM type_couleur pour ajouter 'pool' si pas déjà fait
        try:
            cursor.execute(
                "ALTER TABLE airvs_grille_couleur "
                "MODIFY COLUMN type_couleur "
                "ENUM('artist','genre','subcategory','category','titre','keyword','pool') NOT NULL"
            )
        except Exception:
            pass  # Déjà étendu ou version MySQL qui ne supporte pas IF NOT EXISTS sur MODIFY

        db.commit()
        cursor.close()
        db.close()
        return True
    except Exception as e:
        print(f"[ProgrammationAvancée] Schéma impossible: {e}")
        return False


def _rendrer_template_playlist(template, creneau, nb_titres):
    """Remplace les placeholders d'un template de nom de playlist.
    Placeholders supportés :
      {JOUR}      → LUNDI/MARDI/.../DIMANCHE (jour courant, pas jour_semaine du créneau)
      {HEURE}     → HHh (ex: 14H, basé sur creneau.heure)
      {TYPE}      → ARTIST/GENRE/SUBCATEGORY/CATEGORY/KEYWORD/TITRE
      {VALEUR}    → Valeur du critère, sanitizée (espaces/slashes → _)
      {DATE}      → YYYY-MM-DD (date du jour)
      {MOIS}      → MM (01-12)
      {ANNEE}     → YYYY
      {SEMAINE}   → numéro de semaine ISO (01-53)
      {STATION}   → ID station (6 ou 7)
      {NB_TITRES} → Nombre de titres sélectionnés
    """
    if not template:
        return f"PROG_{datetime.now().strftime('%Y%m%d_%H%M')}"
    jours_fr = ['LUNDI', 'MARDI', 'MERCREDI', 'JEUDI', 'VENDREDI', 'SAMEDI', 'DIMANCHE']
    mysql_dow = datetime.now().isoweekday()  # 1=Mon..7=Sun (standard ISO)
    nom_jour = jours_fr[mysql_dow - 1] if 1 <= mysql_dow <= 7 else 'JOUR'
    heure_str = str(creneau.get('heure', '00:00:00'))
    try:
        hh = heure_str.split(':')[0]
    except Exception:
        hh = '00'
    type_map = {
        'artist': 'ARTIST', 'genre': 'GENRE',
        'subcategory': 'SUBCATEGORY', 'category': 'CATEGORY',
        'keyword': 'KEYWORD', 'titre': 'TITRE'
    }
    type_label = type_map.get(creneau.get('type_couleur', ''), 'INCONNU')
    valeur_sanitized = str(creneau.get('valeur_couleur', '')).upper().replace(' ', '_').replace('/', '_')[:50]
    station_id = str(creneau.get('station_id', '') or '')

    now = datetime.now()
    result = template
    result = result.replace('{JOUR}', nom_jour)
    result = result.replace('{HEURE}', f"{hh}H")
    result = result.replace('{TYPE}', type_label)
    result = result.replace('{VALEUR}', valeur_sanitized)
    result = result.replace('{DATE}', now.strftime('%Y-%m-%d'))
    result = result.replace('{MOIS}', now.strftime('%m'))
    result = result.replace('{ANNEE}', now.strftime('%Y'))
    result = result.replace('{SEMAINE}', now.strftime('%V'))  # ISO week (01-53)
    result = result.replace('{STATION}', f"S{station_id}" if station_id else '')
    result = result.replace('{NB_TITRES}', str(nb_titres))
    return result


def _verifier_creneaux_programmation_couleur():
    """Scanne airvs_grille_couleur pour trouver les créneaux à déclencher MAINTENANT.
    (Le nom de la fonction garde 'couleur' pour compat historique du wiring interne.)
    Pour chaque match, insère une tâche PROGRAMMATION_COULEUR dans taches_planifiees
    et marque dernier_lancement = NOW() pour éviter un double déclenchement."""
    try:
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor(pymysql.cursors.DictCursor)

        # Conversion MySQL DAYOFWEEK (1=Sun..7=Sat) vers notre convention (1=Mon..7=Sun)
        cursor.execute("SELECT DAYOFWEEK(NOW()) AS mysql_dow")
        row = cursor.fetchone()
        mysql_dow = row['mysql_dow'] if row else 1
        our_dow = ((mysql_dow + 5) % 7) + 1  # mysql=1(Sun)→our=7, mysql=2(Mon)→our=1, ...

        # Requête de sélection des créneaux à déclencher :
        # - actif=1
        # - dry_run=0  (les créneaux en dry_run ne se déclenchent jamais automatiquement)
        # - Pas déjà lancé aujourd'hui
        # - Dans la plage horaire autorisée
        # - Heure du créneau à moins de PLAGE_TOLERANCE_SEC de NOW()
        # - Soit jour_semaine=0 ou = our_dow (récurrence), soit événementiel dans la plage de dates
        cursor.execute("""
            SELECT id, heure, jour_semaine, type_couleur, valeur_couleur
            FROM airvs_grille_couleur
            WHERE actif = 1
              AND dry_run = 0
              AND (dernier_lancement IS NULL OR DATE(dernier_lancement) < CURDATE())
              AND TIME(NOW()) BETWEEN plage_h_debut AND plage_h_fin
              AND ABS(TIME_TO_SEC(TIMEDIFF(heure, CURTIME()))) < %s
              AND (
                (jour_semaine IS NULL)
                OR (jour_semaine = 0)
                OR (jour_semaine = %s)
                OR (
                  date_debut IS NOT NULL
                  AND date_debut <= CURDATE()
                  AND (date_fin IS NULL OR date_fin >= CURDATE())
                )
              )
        """, (PLAGE_TOLERANCE_SEC, our_dow))
        creneaux = cursor.fetchall()

        if creneaux:
            print(f"[ProgrammationAvancée] {len(creneaux)} créneau(x) à déclencher")

        for c in creneaux:
            params = {
                "grille_id": c['id'],
                "force_dry_run": False,
                "force": False
            }
            cursor.execute(
                "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
                "VALUES ('PROGRAMMATION_COULEUR', 'en_attente', %s, NOW())",
                (json.dumps(params),)
            )
            # Marquer dernier_lancement immédiatement pour éviter un double déclenchement
            # au prochain cycle (la tâche elle-même peut prendre du temps à se traiter).
            cursor.execute(
                "UPDATE airvs_grille_couleur SET dernier_lancement = NOW() WHERE id = %s",
                (c['id'],)
            )
            print(f"[ProgrammationAvancée] Créneau #{c['id']} "
                  f"({c['type_couleur']}={c['valeur_couleur']} @{c['heure']}) "
                  f"→ tâche insérée")

        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        print(f"[ProgrammationAvancée] Erreur vérification: {e}")


def _construire_requete_couleur(type_couleur, valeur_couleur, titres_ids):
    """Construit la requête SQL pour sélectionner les titres selon le type de sélection.

    RadioDJ schema réel :
      songs.id_genre    → genre.ID         (genre.name = texte)
      songs.id_subcat   → subcategory.ID   (subcategory.name = texte)
      subcategory.parentid → category.ID   (category.name = texte)
      songs.artist      : colonne directe (texte)

    Retourne (sql_select, sql_count, params_select, params_count, is_titre_mode).
    """
    cols = ("SELECT s.ID, s.artist, s.title, s.album, s.year, s.`path`, s.comments "
            "FROM songs s ")

    if type_couleur == 'titre':
        if not titres_ids:
            raise ValueError("type_couleur=titre mais titres_ids vide")
        placeholders = ','.join(['%s'] * len(titres_ids))
        sql_sel = cols + f"WHERE s.song_type = 0 AND s.ID IN ({placeholders})"
        sql_cnt = (f"SELECT COUNT(*) AS total FROM songs s "
                   f"WHERE s.song_type = 0 AND s.ID IN ({placeholders})")
        return sql_sel, sql_cnt, list(titres_ids), list(titres_ids), True

    # ── 2026.06.24-B : Mode Pool — sélection depuis airvs_pool_titres ──
    if type_couleur == 'pool':
        sql_sel = (cols
                   + "JOIN airvs_pool_titres p ON s.ID = p.song_id "
                   + "WHERE s.song_type = 0 AND p.id_pool = %s AND p.statut = 'valide'")
        sql_cnt = ("SELECT COUNT(*) AS total FROM songs s "
                   "JOIN airvs_pool_titres p ON s.ID = p.song_id "
                   "WHERE s.song_type = 0 AND p.id_pool = %s AND p.statut = 'valide'")
        return sql_sel, sql_cnt, [valeur_couleur], [valeur_couleur], False

    if type_couleur == 'keyword':
        like = f"%{valeur_couleur}%"
        sql_sel = (cols + "WHERE s.song_type = 0 "
                   "AND (s.artist LIKE %s OR s.title LIKE %s OR s.album LIKE %s)")
        sql_cnt = ("SELECT COUNT(*) AS total FROM songs s WHERE s.song_type = 0 "
                   "AND (s.artist LIKE %s OR s.title LIKE %s OR s.album LIKE %s)")
        return sql_sel, sql_cnt, [like, like, like], [like, like, like], False

    if type_couleur == 'artist':
        sql_sel = cols + "WHERE s.song_type = 0 AND s.artist = %s"
        sql_cnt = "SELECT COUNT(*) AS total FROM songs s WHERE s.song_type = 0 AND s.artist = %s"
        return sql_sel, sql_cnt, [valeur_couleur], [valeur_couleur], False

    if type_couleur == 'genre':
        sql_sel = (cols + "JOIN genre g ON s.id_genre = g.ID "
                   "WHERE s.song_type = 0 AND g.name = %s")
        sql_cnt = ("SELECT COUNT(*) AS total FROM songs s "
                   "JOIN genre g ON s.id_genre = g.ID "
                   "WHERE s.song_type = 0 AND g.name = %s")
        return sql_sel, sql_cnt, [valeur_couleur], [valeur_couleur], False

    if type_couleur == 'subcategory':
        sql_sel = (cols + "JOIN subcategory sc ON s.id_subcat = sc.ID "
                   "WHERE s.song_type = 0 AND sc.name = %s")
        sql_cnt = ("SELECT COUNT(*) AS total FROM songs s "
                   "JOIN subcategory sc ON s.id_subcat = sc.ID "
                   "WHERE s.song_type = 0 AND sc.name = %s")
        return sql_sel, sql_cnt, [valeur_couleur], [valeur_couleur], False

    if type_couleur == 'category':
        sql_sel = (cols
                   + "JOIN subcategory sc ON s.id_subcat = sc.ID "
                   + "JOIN category c ON sc.parentid = c.ID "
                   + "WHERE s.song_type = 0 AND c.name = %s")
        sql_cnt = ("SELECT COUNT(*) AS total FROM songs s "
                   "JOIN subcategory sc ON s.id_subcat = sc.ID "
                   "JOIN category c ON sc.parentid = c.ID "
                   "WHERE s.song_type = 0 AND c.name = %s")
        return sql_sel, sql_cnt, [valeur_couleur], [valeur_couleur], False

    raise ValueError(f"type_couleur invalide: {type_couleur!r}")


def executer_programmation_couleur(tache_id, parametres):
    """
    Exécute un créneau de programmation avancée :
    1. Charge le créneau depuis airvs_grille_couleur
    2. Sélectionne nb_titres titres aléatoirement (avec anti-répétition)
    3. Si dry_run → log seulement, pas de push, pas d'historique
    4. Sinon :
       a. Copie SFTP des titres non-IN_AZURACAST (via executer_copie)
       b. Applique le mode_playlist :
          - ajouter_existantes → import_m3u dans chaque playlist existante
          - creer_nouvelle → create_playlist + import_m3u
          - remplacer → delete_playlist + create_playlist + import_m3u (par playlist)
       c. Historise dans airvs_historique_couleur
       d. Met à jour airvs_grille_couleur.dernier_lancement = NOW()

    Parametres :
      - grille_id (int) : ID du créneau dans airvs_grille_couleur
      - force_dry_run (bool) : si True, force le mode simulation même si dry_run=0
      - force (bool) : si True, ignore la vérification de dernier_lancement
    """
    grille_id = parametres.get('grille_id')
    force_dry_run = bool(parametres.get('force_dry_run', False))
    force = bool(parametres.get('force', False))

    if not grille_id:
        logger(tache_id, "ERREUR: paramètre grille_id manquant")
        logger(tache_id, "=== TERMINÉ PROGRAMMATION AVANCÉE (ERREUR) ===")
        return

    # ── 1. Charger le créneau ──
    try:
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute("SELECT * FROM airvs_grille_couleur WHERE id = %s", (grille_id,))
        creneau = cursor.fetchone()
        cursor.close()
        db.close()
    except Exception as e:
        logger(tache_id, f"ERREUR DB (chargement créneau): {e}")
        logger(tache_id, "=== TERMINÉ PROGRAMMATION AVANCÉE (ERREUR) ===")
        return

    if not creneau:
        logger(tache_id, f"ERREUR: Créneau #{grille_id} introuvable")
        logger(tache_id, "=== TERMINÉ PROGRAMMATION AVANCÉE (ERREUR) ===")
        return

    # Extraire les champs
    type_couleur = creneau['type_couleur']
    valeur_couleur = creneau['valeur_couleur']
    nb_titres_voulu = int(creneau['nb_titres'])
    anti_rep_jours = int(creneau['anti_repetition_jours'])
    mode_playlist = creneau['mode_playlist']
    station_id = int(creneau['station_id']) if creneau['station_id'] else 7
    dossier_cible = creneau['dossier_cible'] or 'imports_push'
    is_dry_run = bool(creneau['dry_run']) or force_dry_run

    # playlist_ids (JSON)
    playlist_ids = creneau.get('playlist_ids')
    if isinstance(playlist_ids, str):
        try:
            playlist_ids = json.loads(playlist_ids)
        except (json.JSONDecodeError, TypeError):
            playlist_ids = []
    if not isinstance(playlist_ids, list):
        playlist_ids = []
    # Convertir en int
    playlist_ids = [int(p) for p in playlist_ids if p]

    # titres_ids (JSON) — pour type_couleur=titre
    titres_ids = creneau.get('titres_ids')
    if isinstance(titres_ids, str):
        try:
            titres_ids = json.loads(titres_ids)
        except (json.JSONDecodeError, TypeError):
            titres_ids = []
    if not isinstance(titres_ids, list):
        titres_ids = []
    titres_ids = [int(t) for t in titres_ids if t]

    nom_template = creneau.get('nom_nouvelle_playlist') or ''

    logger(tache_id, "════════════════════════════════════════════")
    logger(tache_id, f"🎨 PROGRAMMATION AVANCÉE #{grille_id}")
    logger(tache_id, "════════════════════════════════════════════")
    logger(tache_id, f"  Heure            : {creneau['heure']}")
    logger(tache_id, f"  Station cible    : {station_id}")
    logger(tache_id, f"  Type             : {type_couleur}")
    logger(tache_id, f"  Valeur           : {valeur_couleur}")
    if type_couleur == 'titre':
        logger(tache_id, f"  Titres IDs       : {len(titres_ids)} titre(s) explicite(s)")
    logger(tache_id, f"  Titres voulus    : {nb_titres_voulu}")
    logger(tache_id, f"  Mode playlist    : {mode_playlist}")
    if mode_playlist == 'ajouter_existantes':
        logger(tache_id, f"  Playlists cibles : {playlist_ids}")
    elif mode_playlist == 'creer_nouvelle':
        logger(tache_id, f"  Template nom     : {nom_template}")
    elif mode_playlist == 'remplacer':
        logger(tache_id, f"  Playlists à remplacer : {playlist_ids}")
    logger(tache_id, f"  Anti-répétition  : {anti_rep_jours} jours")
    logger(tache_id, f"  Dossier cible    : {dossier_cible}")
    logger(tache_id, f"  DRY-RUN          : {'OUI (simulation)' if is_dry_run else 'NON (push réel)'}")

    # ── 2. Sélection SQL des titres ──
    try:
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor(pymysql.cursors.DictCursor)

        # Construction de la requête via le helper partagé (gère les JOIN sur
        # genre / subcategory / category — sinon "Unknown column 'subcategory'" etc.)
        try:
            sql_sel, sql_cnt, params_sel, params_cnt, is_titre_mode = _construire_requete_couleur(
                type_couleur, valeur_couleur, titres_ids
            )
        except ValueError as ve:
            logger(tache_id, f"ERREUR: {ve} — abandon")
            logger(tache_id, "=== TERMINÉ PROGRAMMATION AVANCÉE (ERREUR) ===")
            cursor.close()
            db.close()
            return

        sql = sql_sel
        params = list(params_sel)

        if is_titre_mode:
            pool_total = len(titres_ids)
            logger(tache_id, f"  Pool total       : {pool_total} titre(s) explicite(s)")
        else:
            cursor.execute(sql_cnt, tuple(params_cnt))
            pool_total = cursor.fetchone()['total']
            if type_couleur == 'keyword':
                logger(tache_id, f"  Pool total       : {pool_total} titre(s) correspondent au mot-clé")
            else:
                logger(tache_id, f"  Pool total       : {pool_total} titre(s) correspondant au critère "
                                 f"({type_couleur}='{valeur_couleur}')")

        if not is_dry_run:
            # Anti-répétition active uniquement en mode push réel
            cursor.execute(
                "SELECT DISTINCT song_id FROM airvs_historique_couleur "
                "WHERE date_lancement > NOW() - INTERVAL %s DAY",
                (anti_rep_jours,)
            )
            exclus = [r['song_id'] for r in cursor.fetchall()]
            if exclus:
                placeholders = ','.join(['%s'] * len(exclus))
                sql += f" AND s.ID NOT IN ({placeholders})"
                params.extend(exclus)
                logger(tache_id, f"  Titres exclus (anti-répétition) : {len(exclus)}")

        # Si type=titre, on garde l'ordre (pas de RAND(), pas de LIMIT)
        if is_titre_mode:
            sql += " ORDER BY s.artist, s.title"
        else:
            sql += " ORDER BY RAND() LIMIT %s"
            params.append(nb_titres_voulu)
        cursor.execute(sql, tuple(params))
        titres = cursor.fetchall()
        cursor.close()
        db.close()

        logger(tache_id, f"  Titres sélectionnés : {len(titres)}"
                         + (f" / {nb_titres_voulu}" if not is_titre_mode else ""))

        if not is_titre_mode and len(titres) < nb_titres_voulu:
            logger(tache_id, f"  ⚠ Pool insuffisant — seulement {len(titres)} titre(s) disponible(s) "
                             f"(demandé: {nb_titres_voulu}). "
                             f"Considérez réduire anti_repetition_jours ou élargir le critère.")

        if not titres:
            logger(tache_id, "  Aucun titre trouvé — abandon")
            logger(tache_id, "=== TERMINÉ PROGRAMMATION AVANCÉE (VIDE) ===")
            return

        # Marquer IN_AZURACAST pour chaque titre
        for t in titres:
            t['in_azura'] = (str(t.get('comments', '')).strip() == 'IN_AZURACAST')

        # Afficher la sélection dans le log
        logger(tache_id, "  ── Sélection ──")
        for i, t in enumerate(titres, 1):
            badge = "✓" if t['in_azura'] else "📡"
            annee = f" [{t.get('year') or '?'}]" if t.get('year') else ""
            logger(tache_id, f"  {i:2d}. {badge} {t['artist']} — {t['title']}{annee}")

    except Exception as e:
        logger(tache_id, f"ERREUR DB (sélection titres): {e}")
        logger(tache_id, "=== TERMINÉ PROGRAMMATION AVANCÉE (ERREUR) ===")
        return

    # ── 3. Si dry_run, on s'arrête là ──
    if is_dry_run:
        logger(tache_id, "  ✋ MODE DRY-RUN — Aucun push effectué")
        logger(tache_id, "  Pour activer le push réel : éditez le créneau et désactivez dry_run")
        logger(tache_id, "=== TERMINÉ PROGRAMMATION AVANCÉE (DRY-RUN) ===")
        return

    # ── 4. Push réel ──
    # Séparer les fichiers à copier des fichiers déjà dans AzuraCast
    fichiers_a_copier = []
    fichiers_deja_azura = []
    for t in titres:
        entry = {
            'path': t['path'],
            'artist': t['artist'],
            'title': t['title']
        }
        if t['in_azura']:
            fichiers_deja_azura.append(entry)
        else:
            fichiers_a_copier.append(entry)

    logger(tache_id, f"  ── Bilan pré-push ──")
    logger(tache_id, f"  → {len(fichiers_a_copier)} fichier(s) à copier par SFTP (non-IN_AZURACAST)")
    logger(tache_id, f"  → {len(fichiers_deja_azura)} fichier(s) déjà dans AzuraCast (skip copie)")
    if fichiers_a_copier:
        # Lister les 5 premiers fichiers à copier pour visibilité
        for i, f in enumerate(fichiers_a_copier[:5], 1):
            logger(tache_id, f"    {i}. {f['artist']} — {f['title']}")
        if len(fichiers_a_copier) > 5:
            logger(tache_id, f"    ... et {len(fichiers_a_copier) - 5} autre(s)")

    # Étape A : Copie SFTP (uniquement si au moins 1 fichier à copier)
    if fichiers_a_copier:
        # IMPORTANT : executer_copie() requiert 'destination' (le root SSHFS AzuraCast).
        # Sans cette clé, os.path.join(None, dossier_nom) lève un TypeError obscur
        # ("expected str, bytes or os.PathLike object, not NoneType").
        # On utilise AZURA_SSHFS_ROOT comme racine, et dossier_cible comme sous-dossier.
        params_copie = {
            'destination': AZURA_SSHFS_ROOT,
            'fichiers': fichiers_a_copier,
            'dossier_nom': dossier_cible,
            'ajouter_sous_dossier_date': False,
            'station_id': station_id
        }
        logger(tache_id, f"  ── Étape 1/2 : Copie SFTP de {len(fichiers_a_copier)} fichier(s) → '{dossier_cible}' ──")
        try:
            # Appel direct de executer_copie avec le MÊME tache_id pour un log unifié
            executer_copie(tache_id, params_copie)
            logger(tache_id, f"  ✓ Étape 1/2 terminée — copie SFTP OK")
        except Exception as e:
            logger(tache_id, f"  ⚠ Erreur lors de la copie SFTP : {e}")
            logger(tache_id, "  → On continue quand même avec les fichiers déjà dans AzuraCast")
    else:
        logger(tache_id, "  ── Étape 1/2 : Aucune copie SFTP nécessaire (tous déjà IN_AZURACAST) ──")

    # Étape B : Opération(s) sur les playlists
    # Préparer la liste de TOUS les fichiers (copiés + déjà AzuraCast) pour l'étape playlist
    tous_fichiers = []
    for f in fichiers_a_copier:
        f_copy = dict(f)
        f_copy['in_azura'] = False
        tous_fichiers.append(f_copy)
    for f in fichiers_deja_azura:
        f_copy = dict(f)
        f_copy['in_azura'] = True
        tous_fichiers.append(f_copy)

    if not tous_fichiers:
        logger(tache_id, "  ⚠ Aucun fichier à traiter — étape playlist ignorée")
    else:
        logger(tache_id, f"  ── Étape 2/2 : Opération playlist (mode={mode_playlist}, {len(tous_fichiers)} fichier(s)) ──")

        if mode_playlist == 'ajouter_existantes':
            if not playlist_ids:
                logger(tache_id, "  ⚠ Aucune playlist cible définie — étape playlist ignorée")
            else:
                for pl_id in playlist_ids:
                    params_pl = {
                        'playlist_id': pl_id,
                        'fichiers': tous_fichiers,
                        'dossier_cible': dossier_cible,
                        'station_id': station_id
                    }
                    logger(tache_id, f"  → Ajout à playlist {pl_id}…")
                    try:
                        executer_import_m3u_playlist(tache_id, params_pl)
                        logger(tache_id, f"    ✓ Playlist {pl_id} enrichie")
                    except Exception as e:
                        logger(tache_id, f"    ⚠ Erreur ajout playlist {pl_id}: {e}")

        elif mode_playlist == 'creer_nouvelle':
            nom_final = _rendrer_template_playlist(nom_template, creneau, len(titres))
            logger(tache_id, f"  → Création nouvelle playlist : '{nom_final}'")
            # ⚠ IMPORTANT : Une nouvelle playlist AzuraCast sans schedule jouera 24/7
            # par défaut. Pour éviter qu'elle ne se diffuse immédiatement en concurrence
            # avec les autres, on la crée DÉSACTIVÉE. L'utilisateur devra l'activer
            # manuellement dans AzuraCast APRÈS avoir configuré son schedule.
            params_pl = {
                'nom_playlist': nom_final,
                'fichiers': tous_fichiers,
                'dossier_cible': dossier_cible,
                'type': 'default',
                'order': 'shuffle',
                'station_id': station_id,
                'mode_fusion': 'creer_nouveau',
                'is_enabled': False  # ← Désactivée par défaut (sécurité anti-24/7)
            }
            try:
                executer_creer_playlist_azura(tache_id, params_pl)
                logger(tache_id, f"    ✓ Playlist '{nom_final}' créée (DÉSACTIVÉE — à activer manuellement dans AzuraCast)")
                logger(tache_id, f"    ℹ Dans AzuraCast : configurez le schedule puis activez la playlist")
            except Exception as e:
                logger(tache_id, f"    ⚠ Erreur création playlist: {e}")

        elif mode_playlist == 'remplacer':
            if not playlist_ids:
                logger(tache_id, "  ⚠ Aucune playlist à remplacer — étape playlist ignorée")
            else:
                api = get_azura_api(station_id)
                if not api:
                    logger(tache_id, "  ⚠ API AzuraCast indisponible — impossible de remplacer les playlists")
                else:
                    for pl_id in playlist_ids:
                        try:
                            # Récupérer la playlist AVANT suppression pour préserver
                            # schedule, is_enabled, type, order, weight, etc.
                            # (sinon on perd la config et la nouvelle playlist joue 24/7)
                            pl = api.get_playlist(pl_id)
                            nom_pl = pl.get('name', f'Playlist_{pl_id}')
                            # Snapshot des attributs à préserver
                            old_schedule = pl.get('schedule_items', []) or []
                            old_is_enabled = pl.get('is_enabled', True)
                            old_type = pl.get('type', 'default')
                            old_order = pl.get('order', 'shuffle')
                            old_weight = pl.get('weight', 1)
                            old_include_in_requests = pl.get('include_in_requests', True)
                            old_avoid_duplicates = pl.get('avoid_duplicates', True)
                            logger(tache_id, f"  → Remplacement playlist {pl_id} ('{nom_pl}')")
                            logger(tache_id, f"    Préservation : type={old_type}, "
                                             f"order={old_order}, "
                                             f"is_enabled={old_is_enabled}, "
                                             f"weight={old_weight}, "
                                             f"schedule_items={len(old_schedule)} entrée(s)")
                            # Supprimer
                            api.delete_playlist(pl_id)
                            logger(tache_id, f"    Playlist {pl_id} supprimée")
                            # Recréer avec attributs préservés (y compris le schedule !)
                            create_kwargs = dict(
                                name=nom_pl,
                                type=old_type,
                                order=old_order,
                                is_enabled=old_is_enabled,
                                include_in_requests=old_include_in_requests,
                                avoid_duplicates=old_avoid_duplicates
                            )
                            if old_schedule:
                                # L'API AzuraCast accepte schedule_items à la création
                                create_kwargs['schedule_items'] = old_schedule
                            new_pl = api.create_playlist(**create_kwargs)
                            new_pl_id = new_pl.get('id')
                            logger(tache_id, f"    Playlist recréée sous ID {new_pl_id} "
                                             f"(is_enabled={old_is_enabled}, "
                                             f"schedule restauré: {len(old_schedule)} entrée(s))")
                            # Restaurer le weight séparément (pas toujours accepté à la création)
                            if old_weight and old_weight != 1:
                                try:
                                    api.update_playlist(new_pl_id, weight=old_weight)
                                except Exception:
                                    pass  # Non critique
                            # Importer les nouveaux titres
                            m3u_lines = ['#EXTM3U']
                            for t in titres:
                                # AzuraCast M3U: juste le basename du fichier côté AzuraCast
                                # Si déjà in_azura, on a juste le path Windows — on prend le basename
                                basename = t['path'].rsplit('\\', 1)[-1].rsplit('/', 1)[-1]
                                m3u_lines.append(basename)
                            m3u_content = '\n'.join(m3u_lines)
                            api.import_m3u(new_pl_id, m3u_content)
                            logger(tache_id, f"    {len(titres)} titre(s) importé(s) dans {new_pl_id}")
                        except Exception as e:
                            logger(tache_id, f"    ⚠ Erreur remplacement playlist {pl_id}: {e}")

    # ── 5. Historisation ──
    try:
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor()
        for t in titres:
            cursor.execute(
                "INSERT INTO airvs_historique_couleur "
                "(grille_id, song_id, station_id, tache_id, mode_playlist, playlist_cible) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (grille_id, t['ID'], station_id, tache_id,
                 mode_playlist,
                 ','.join(str(p) for p in playlist_ids) if playlist_ids else None)
            )
        # Marquer dernier_lancement (si pas déjà fait par le vérificateur cron)
        cursor.execute(
            "UPDATE airvs_grille_couleur SET dernier_lancement = NOW() WHERE id = %s",
            (grille_id,)
        )
        db.commit()
        cursor.close()
        db.close()
        logger(tache_id, f"  ✓ {len(titres)} titre(s) historisé(s)")
    except Exception as e:
        logger(tache_id, f"  ⚠ Erreur historisation: {e}")

    logger(tache_id, "=== TERMINÉ PROGRAMMATION AVANCÉE ===")


def executer_auto_check_sheets(tache_id, parametres):
    """Vérifie les Google Sheets configurés pour détecter de nouvelles entrées.
    Compare le nombre de lignes actuel vs dernier snapshot.
    Si des nouvelles lignes sont détectées → insère une notification.
    NE POUSSE RIEN vers AzuraCast — notification uniquement."""
    sheets_a_checker = parametres.get('sheets_a_checker', [])
    declenche_par = parametres.get('declenche_par', 'automatique')

    logger(tache_id, f"=== AUTO-CHECK GOOGLE SHEETS ({declenche_par}) ===")
    logger(tache_id, f"Sheets à vérifier : {', '.join(sheets_a_checker) if sheets_a_checker else 'aucun'}")

    if not sheets_a_checker:
        logger(tache_id, "⚠ Aucun sheet à vérifier. Fin.")
        return

    # Connexion Google Sheets
    try:
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_file(CREDENTIALS_JSON, scopes=SHEETS_SCOPES)
        client = gspread.authorize(creds)
    except Exception as e:
        logger(tache_id, f"❌ Impossible de se connecter à Google Sheets : {e}")
        return

    db = pymysql.connect(**DB_CONFIG)
    cursor = db.cursor(pymysql.cursors.DictCursor)

    total_nouvelles = 0

    for alias in sheets_a_checker:
        try:
            # Résoudre l'alias
            sheets_id = resoudre_sheets_id(alias)
            logger(tache_id, f"\n── Sheet '{alias}' (ID: {sheets_id[:12]}…) ──")

            # Ouvrir le spreadsheet
            try:
                spreadsheet = client.open_by_key(sheets_id)
            except Exception as e:
                logger(tache_id, f"  ❌ Erreur ouverture sheet '{alias}' : {e}")
                continue

            # Lister les onglets (feuilles)
            worksheets = spreadsheet.worksheets()
            logger(tache_id, f"  {len(worksheets)} onglet(s) trouvé(s)")

            for ws in worksheets:
                try:
                    # Compter les lignes de données (exclure l'en-tête)
                    all_values = ws.get_all_values()
                    nb_lignes = max(0, len(all_values) - 1)  # -1 pour l'en-tête

                    # Dernier snapshot pour cet onglet
                    cursor.execute(
                        "SELECT nb_lignes FROM airvs_auto_check_snapshots "
                        "WHERE sheet_alias = %s AND onglet = %s",
                        (alias, ws.title)
                    )
                    snapshot = cursor.fetchone()
                    ancien_nb = snapshot['nb_lignes'] if snapshot else 0

                    if nb_lignes > ancien_nb:
                        diff = nb_lignes - ancien_nb
                        total_nouvelles += diff
                        logger(tache_id, f"  📊 '{ws.title}' : {nb_lignes} lignes ({diff} nouvelles)")

                        # Insérer une notification
                        message = f"{diff} nouvelle(s) ligne(s) sur l'onglet '{ws.title}' (total: {nb_lignes})"
                        cursor.execute(
                            "INSERT INTO airvs_notifications "
                            "(type_notification, sheet_alias, message, lu, date_creation) "
                            "VALUES ('nouvelles_lignes', %s, %s, 0, NOW())",
                            (alias, message)
                        )
                        db.commit()

                        # Mettre à jour le snapshot
                        if snapshot:
                            cursor.execute(
                                "UPDATE airvs_auto_check_snapshots SET nb_lignes = %s, derniere_verification = NOW() "
                                "WHERE sheet_alias = %s AND onglet = %s",
                                (nb_lignes, alias, ws.title)
                            )
                        else:
                            cursor.execute(
                                "INSERT INTO airvs_auto_check_snapshots "
                                "(sheet_alias, onglet, nb_lignes, derniere_verification) "
                                "VALUES (%s, %s, %s, NOW())",
                                (alias, ws.title, nb_lignes)
                            )
                        db.commit()
                    else:
                        logger(tache_id, f"  ✅ '{ws.title}' : {nb_lignes} lignes (inchangé)")

                except Exception as e:
                    logger(tache_id, f"  ⚠ Erreur onglet '{ws.title}' : {e}")
                    continue

        except Exception as e:
            logger(tache_id, f"❌ Erreur sheet '{alias}' : {e}")
            continue

    # Mettre à jour derniere_execution dans la config
    try:
        cursor.execute(
            "UPDATE airvs_auto_check_config SET derniere_execution = NOW(), "
            "prochaine_execution = NULL WHERE id = 1"
        )
        db.commit()
    except Exception:
        pass

    cursor.close()
    db.close()

    if total_nouvelles > 0:
        logger(tache_id, f"\n🔔 TOTAL : {total_nouvelles} nouvelle(s) entrée(s) détectée(s) sur tous les sheets")
    else:
        logger(tache_id, f"\n✅ Aucune nouvelle entrée détectée")


def _verifier_auto_check_sheets():
    """Vérifie périodiquement si un auto-check doit être déclenché.
    Lit airvs_auto_check_config, compare les horaires avec l'heure actuelle,
    et crée une tâche AUTO_CHECK_SHEETS si c'est l'heure."""
    try:
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor(pymysql.cursors.DictCursor)

        # Vérifier que la table existe
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'airvs_auto_check_config'"
        )
        if not cursor.fetchone():
            cursor.close()
            db.close()
            return

        # Lire la config
        cursor.execute("SELECT * FROM airvs_auto_check_config LIMIT 1")
        cfg = cursor.fetchone()
        cursor.close()
        db.close()

        if not cfg or not cfg.get('actif'):
            return

        # Parser les horaires
        horaires = cfg.get('horaires', [])
        if isinstance(horaires, str):
            horaires = json.loads(horaires)

        if not horaires:
            return

        # Vérifier si on est dans un créneau de déclenchement (±2 min)
        now = datetime.now()
        heure_actuelle = now.strftime('%H:%M')

        doit_declencher = False
        for h in horaires:
            # Tolérance de 2 minutes
            try:
                h_minutes = int(h.split(':')[0]) * 60 + int(h.split(':')[1])
                now_minutes = now.hour * 60 + now.minute
                if abs(now_minutes - h_minutes) <= 2:
                    doit_declencher = True
                    break
            except (ValueError, IndexError):
                continue

        if not doit_declencher:
            return

        # Vérifier qu'on n'a pas déjà déclenché récemment (dans les 10 dernières minutes)
        derniere = cfg.get('derniere_execution')
        if derniere:
            if isinstance(derniere, str):
                derniere = datetime.strptime(derniere, '%Y-%m-%d %H:%M:%S')
            delta = (now - derniere).total_seconds()
            if delta < 600:  # 10 minutes
                return

        # Vérifier qu'il n'y a pas déjà une tâche AUTO_CHECK en attente
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM taches_planifiees "
            "WHERE type_action = 'AUTO_CHECK_SHEETS' AND statut IN ('en_attente', 'en_cours')"
        )
        row = cursor.fetchone()
        if row[0] > 0:
            cursor.close()
            db.close()
            return

        # Créer la tâche
        sheets_surveilles = cfg.get('sheets_surveilles', [])
        if isinstance(sheets_surveilles, str):
            sheets_surveilles = json.loads(sheets_surveilles)

        parametres = json.dumps({
            'sheets_a_checker': sheets_surveilles,
            'declenche_par': 'automatique',
        })
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
            "VALUES ('AUTO_CHECK_SHEETS', 'en_attente', %s, NOW())",
            (parametres,)
        )
        db.commit()
        cursor.close()
        db.close()
        print(f"[Auto-check] Tâche AUTO_CHECK_SHEETS créée automatiquement ({len(sheets_surveilles)} sheet(s))")

    except Exception as e:
        print(f"[Auto-check] Erreur vérification périodique : {e}")


def _verifier_auto_validations():
    """Vérifie périodiquement si une auto-validation Pool doit être déclenchée.
    Lit airvs_pool_auto_validation, compare les fréquences/horaires avec l'heure
    actuelle, et crée une tâche VALIDATION_POOL_SHEETS si c'est l'heure."""
    try:
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor(pymysql.cursors.DictCursor)

        # Vérifier que la table existe
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'airvs_pool_auto_validation'"
        )
        if not cursor.fetchone():
            cursor.close()
            db.close()
            return

        # Lire toutes les auto-validations actives
        cursor.execute(
            "SELECT id, sheet_alias, onglet, frequence, heure, jour_semaine, "
            "       actif, derniere_execution "
            "FROM airvs_pool_auto_validation "
            "WHERE actif = 1"
        )
        rows = cursor.fetchall()
        cursor.close()
        db.close()

        if not rows:
            return

        now = datetime.now()
        heure_actuelle_str = now.strftime('%H:%M')  # ex: "22:00"
        jour_semaine_actuel = now.isoweekday()  # 1=Lun ... 7=Dim

        for av in rows:
            av_id = av['id']
            frequence = av['frequence']  # 'quotidienne' ou 'hebdo'
            heure_brute = av['heure']
            # pymysql retourne les colonnes TIME comme datetime.timedelta
            # Convertir en string "HH:MM"
            if isinstance(heure_brute, timedelta):
                total_sec = int(heure_brute.total_seconds())
                h = total_sec // 3600
                m = (total_sec % 3600) // 60
                heure_av = f"{h:02d}:{m:02d}"
            else:
                heure_av = str(heure_brute or '')[:5]  # "22:00" (enlever les secondes)
            jour_av = av['jour_semaine']  # 1-7 si hebdo, NULL si quotidienne
            derniere = av['derniere_execution']

            # Vérifier si on est dans le créneau de déclenchement (±2 min)
            doit_declencher = False

            if frequence == 'hebdo':
                if jour_av is not None and int(jour_av) != jour_semaine_actuel:
                    continue  # Pas le bon jour

            # Comparer l'heure (tolérance 2 min, comme _verifier_auto_check_sheets)
            try:
                av_minutes = int(heure_av.split(':')[0]) * 60 + int(heure_av.split(':')[1])
                now_minutes = now.hour * 60 + now.minute
                if abs(now_minutes - av_minutes) <= 2:
                    doit_declencher = True
            except (ValueError, IndexError):
                continue

            if not doit_declencher:
                continue

            # Vérifier qu'on n'a pas déjà déclenché récemment (dans les 10 dernières minutes)
            if derniere:
                if isinstance(derniere, str):
                    derniere = datetime.strptime(derniere, '%Y-%m-%d %H:%M:%S')
                delta = (now - derniere).total_seconds()
                if delta < 600:  # 10 minutes
                    continue

            # Vérifier qu'il n'y a pas déjà une tâche VALIDATION_POOL en attente
            # pour ce même sheet_alias + onglet
            db2 = pymysql.connect(**DB_CONFIG)
            cur2 = db2.cursor()
            cur2.execute(
                "SELECT COUNT(*) AS cnt FROM taches_planifiees "
                "WHERE type_action = 'VALIDATION_POOL_SHEETS' "
                "AND statut IN ('en_attente', 'en_cours') "
                "AND parametres LIKE %s",
                (f'%"sheet_alias": "{av["sheet_alias"]}"%',)
            )
            row = cur2.fetchone()
            if row[0] > 0:
                cur2.close()
                db2.close()
                continue  # Tâche déjà en attente pour ce sheet

            # Créer la tâche VALIDATION_POOL_SHEETS
            parametres = json.dumps({
                'sheets_id': av['sheet_alias'],
                'onglet': av['onglet'],
                'semaine_filter': '',
                'dry_run': False,
                'declenche_par': 'auto_validation',
                'auto_validation_id': av_id,
            })
            cur2.execute(
                "INSERT INTO taches_planifiees "
                "(type_action, statut, parametres, date_creation) "
                "VALUES ('VALIDATION_POOL_SHEETS', 'en_attente', %s, NOW())",
                (parametres,)
            )

            # Mettre à jour derniere_execution
            cur2.execute(
                "UPDATE airvs_pool_auto_validation "
                "SET derniere_execution = NOW() WHERE id = %s",
                (av_id,)
            )
            db2.commit()
            cur2.close()
            db2.close()
            print(f"[Auto-validation] Tâche VALIDATION_POOL_SHEETS créée pour "
                  f"sheet={av['sheet_alias']} onglet={av['onglet']} "
                  f"(auto_validation #{av_id})")

    except Exception as e:
        print(f"[Auto-validation] Erreur vérification périodique : {e}")


def executer_shazam_sync_sheet(tache_id, parametres):
    """Sync les reconnaissances Shazam vers l'onglet SHAZAM du Google Sheet.
    Le worker (Ubuntu) a internet, contrairement au dashboard Flask (Windows isolé)."""
    sheets_alias_ou_id = parametres.get('sheets_id', '')
    onglet = parametres.get('onglet', 'SHAZAM')

    logger(tache_id, f"☁ DÉBUT SYNC SHAZAM → Google Sheet (onglet: {onglet})")

    try:
        # 1) Lire les entrées Shazam depuis MariaDB
        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT id, artiste, titre, date_reconnaissance "
            "FROM airvs_shazam ORDER BY date_reconnaissance DESC"
        )
        shazam_rows = cursor.fetchall()
        cursor.close()
        db.close()
        logger(tache_id, f"  → {len(shazam_rows)} entrée(s) Shazam lue(s) en base")

        if not shazam_rows:
            logger(tache_id, "⚠ Aucune entrée Shazam à synchroniser.")
            return

        # 2) Se connecter au Google Sheet
        sheets_id = resoudre_sheets_id(sheets_alias_ou_id)
        creds = Credentials.from_service_account_file(CREDENTIALS_JSON, scopes=SHEETS_SCOPES)
        client = gspread.authorize(creds)

        # 3) Écrire dans l'onglet SHAZAM
        try:
            feuille = client.open_by_key(sheets_id).worksheet(onglet)
        except gspread.exceptions.WorksheetNotFound:
            # Créer l'onglet s'il n'existe pas
            logger(tache_id, f"  → Onglet '{onglet}' introuvable, création...")
            spreadsheet = client.open_by_key(sheets_id)
            feuille = spreadsheet.add_worksheet(title=onglet, rows=len(shazam_rows) + 1, cols=4)

        # Préparer les données
        header = ["Artiste", "Titre", "Date Reconnaissance", "Statut"]
        rows_data = [header]
        for sr in shazam_rows:
            date_str = ''
            if sr.get('date_reconnaissance'):
                if hasattr(sr['date_reconnaissance'], 'strftime'):
                    date_str = sr['date_reconnaissance'].strftime('%d/%m/%Y %H:%M')
                else:
                    date_str = str(sr['date_reconnaissance'])
            rows_data.append([
                sr.get('artiste', ''),
                sr.get('titre', ''),
                date_str,
                'à vérifier'
            ])

        feuille.update(values=rows_data, range_name='A1')
        logger(tache_id, f"✅ {len(shazam_rows)} ligne(s) écrites dans onglet '{onglet}'")

    except Exception as e:
        logger(tache_id, f"❌ Erreur sync Shazam Sheet : {e}")
        raise


def _sync_azura_automatique():
    """Insère les tâches SYNC_AZURA_STATUS + SYNC_PLAYLISTS_CACHE si :
      - on est dans la plage 8h-20h (timezone Europe/Paris)
      - aucune tâche du même type n'est déjà en attente/en_cours
      - la dernière exécution auto remonte à plus d'une heure."""
    try:
        from datetime import datetime as dt
        # Heure actuelle en Europe/Paris
        try:
            import tzlocal
            tz = tzlocal.get_localzone()
            now_local = dt.now(tz)
        except ImportError:
            now_local = dt.now()  # Fallback sans timezone

        heure = now_local.hour
        if heure < SYNC_AZURA_AUTO_HEURE_DEBUT or heure >= SYNC_AZURA_AUTO_HEURE_FIN:
            return  # Hors plage horaire

        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor(pymysql.cursors.DictCursor)

        # Vérifier qu'aucune sync n'est déjà en attente/en_cours
        cursor.execute(
            "SELECT id, type_action FROM taches_planifiees "
            "WHERE type_action IN ('SYNC_AZURA_STATUS', 'SYNC_PLAYLISTS_CACHE') "
            "AND statut IN ('en_attente', 'en_cours') "
            "LIMIT 1"
        )
        en_cours = cursor.fetchone()
        if en_cours:
            cursor.close()
            db.close()
            return  # Tâche déjà en file

        # Vérifier la dernière exécution auto (via un marqueur dans les paramètres)
        cursor.execute(
            "SELECT date_creation FROM taches_planifiees "
            "WHERE type_action = 'SYNC_AZURA_STATUS' "
            "AND parametres LIKE '%auto_scheduled%' "
            "ORDER BY id DESC LIMIT 1"
        )
        derniere = cursor.fetchone()
        if derniere and derniere['date_creation']:
            # Si la dernière date est dans la dernière heure, ne pas relancer
            from datetime import timedelta
            if dt.now() - derniere['date_creation'] < timedelta(hours=1):
                cursor.close()
                db.close()
                return

        # Résoudre les stations
        try:
            station_ids = [s['id'] for s in lister_stations_config()]
        except Exception:
            station_ids = []

        if not station_ids:
            cursor.close()
            db.close()
            return

        # Insérer SYNC_AZURA_STATUS
        params_status = json.dumps({
            'station_ids': station_ids,
            'badge_global': True,
            'auto_scheduled': True
        })
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
            "VALUES ('SYNC_AZURA_STATUS', 'en_attente', %s, NOW())",
            (params_status,)
        )
        task_status_id = cursor.lastrowid

        # Insérer SYNC_PLAYLISTS_CACHE
        params_cache = json.dumps({
            'station_ids': station_ids,
            'auto_scheduled': True
        })
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
            "VALUES ('SYNC_PLAYLISTS_CACHE', 'en_attente', %s, NOW())",
            (params_cache,)
        )
        task_cache_id = cursor.lastrowid

        db.commit()
        cursor.close()
        db.close()
        print(f"[SYNC AUTO] Tâches planifiées : SYNC_AZURA_STATUS #{task_status_id} + "
              f"SYNC_PLAYLISTS_CACHE #{task_cache_id} (stations {station_ids})")

    except Exception as e:
        print(f"[SYNC AUTO] Erreur : {e}")


# ============================================================
# Sync VPS OVH 1 — Soumissions programmeurs + Shazam externe
# ============================================================

def _passe_from_methode(methode):
    """Extrait le numéro de passe (1-8) depuis la chaîne methode du matching."""
    if not methode:
        return None
    m = methode.lower()
    if m.startswith('strict'):
        return 1
    if m.startswith('principal'):
        return 2
    if m.startswith('nu'):
        return 3
    if m.startswith('normalis'):
        return 4
    if m.startswith('mots-cles'):
        return 5
    if 'invers' in m:
        if 'strict' in m:
            return 6
        if 'principal' in m:
            return 7
        return 8
    return None


def _vps_get(path, timeout=15):
    """Requête GET authentifiée vers l'API VPS."""
    if not VPS_SYNC_TOKEN:
        return None
    try:
        r = requests.get(
            f"{VPS_SYNC_URL}{path}",
            headers={"X-API-Token": VPS_SYNC_TOKEN},
            timeout=timeout
        )
        if r.status_code == 200:
            return r.json()
        print(f"[VPS SYNC] GET {path} → {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        print(f"[VPS SYNC] Erreur GET {path}: {e}")
        return None


def _vps_post(path, payload, timeout=15):
    """Requête POST authentifiée vers l'API VPS."""
    if not VPS_SYNC_TOKEN:
        return None
    try:
        r = requests.post(
            f"{VPS_SYNC_URL}{path}",
            json=payload,
            headers={"X-API-Token": VPS_SYNC_TOKEN},
            timeout=timeout
        )
        if r.status_code == 200:
            return r.json()
        print(f"[VPS SYNC] POST {path} → {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        print(f"[VPS SYNC] Erreur POST {path}: {e}")
        return None


def executer_sync_vps_soumissions(tache_id, parametres):
    """Tâche planifiable : pull les soumissions du VPS, match, insert dans airvs_animateurs."""
    logger(tache_id, "[VPS SYNC] Début sync soumissions animateurs")

    if not VPS_SYNC_TOKEN:
        logger(tache_id, "[VPS SYNC] ERREUR : VPS_SYNC_TOKEN non configuré")
        return

    # 1) Pull
    data = _vps_get("/api/sync/pull")
    if data is None:
        logger(tache_id, "[VPS SYNC] Erreur lors du pull des soumissions")
        return

    soumissions = data.get("soumissions", [])
    if not soumissions:
        logger(tache_id, "[VPS SYNC] Aucune soumission en attente")
        return

    logger(tache_id, f"[VPS SYNC] {len(soumissions)} soumission(s) récupérée(s)")

    # 2) Traitement : match + insert dans airvs_animateurs
    db = pymysql.connect(**DB_CONFIG)
    cursor = db.cursor(pymysql.cursors.DictCursor)

    # S'assurer que la table airvs_animateurs existe
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS airvs_animateurs (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            vps_id          INT          DEFAULT NULL,
            artiste         VARCHAR(255) NOT NULL,
            titre           VARCHAR(255) NOT NULL,
            genre           VARCHAR(50)  DEFAULT NULL,
            source          VARCHAR(50)  NOT NULL,
            commentaire     TEXT         DEFAULT NULL,
            video_url       VARCHAR(500) DEFAULT NULL,
            animateur       VARCHAR(100) DEFAULT NULL,
            match_song_id   INT          DEFAULT NULL,
            match_passe     TINYINT      DEFAULT NULL,
            statut_vps      ENUM('pending','synced') DEFAULT 'synced',
            date_soumission DATETIME     DEFAULT NULL,
            date_sync       DATETIME     DEFAULT CURRENT_TIMESTAMP,
            UNIQUE INDEX uq_vps_id (vps_id),
            INDEX idx_animateur (animateur),
            INDEX idx_source (source),
            INDEX idx_date (date_soumission)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    db.commit()

    ids_synced = []
    nb_match = 0
    nb_no_match = 0

    for s in soumissions:
        artiste = s["artiste"].strip()
        titre = s["titre"].strip()
        vps_id = s["id"]
        animateur = (s.get("animateur") or "").strip() or None
        source_vps = (s.get("source") or "").strip() or "Autre"
        genre_vps = (s.get("genre") or "").strip() or None
        commentaire_vps = (s.get("commentaire") or "").strip() or None
        video_url_vps = (s.get("video_url") or "").strip() or None
        date_soumission_vps = s.get("date_soumission") or None
        statut_vps = (s.get("statut") or "synced").strip()

        if not artiste or not titre:
            ids_synced.append(vps_id)
            continue

        # Dédup 24h dans airvs_animateurs (par vps_id ou artiste+titre)
        cursor.execute(
            "SELECT id FROM airvs_animateurs "
            "WHERE vps_id = %s LIMIT 1",
            (vps_id,)
        )
        if cursor.fetchone():
            logger(tache_id, f"  ~ {artiste} - {titre} (vps_id #{vps_id} déjà dans airvs_animateurs)")
            ids_synced.append(vps_id)
            continue
        cursor.execute(
            "SELECT id FROM airvs_animateurs "
            "WHERE LOWER(artiste) = LOWER(%s) AND LOWER(titre) = LOWER(%s) "
            "AND date_sync > NOW() - INTERVAL 24 HOUR LIMIT 1",
            (artiste, titre)
        )
        if cursor.fetchone():
            logger(tache_id, f"  ~ {artiste} - {titre} (doublon 24h dans airvs_animateurs)")
            ids_synced.append(vps_id)
            continue

        # Match 5 passes contre radiodb.songs
        match_result, match_methode = _match_titre_songs(cursor, artiste, titre)

        if match_result:
            nb_match += 1
            methode = match_methode or "?"
            song_id = match_result["ID"]
            logger(tache_id, f"  ✓ {artiste} - {titre} → song #{song_id} ({methode})")
        else:
            nb_no_match += 1
            logger(tache_id, f"  ✗ {artiste} - {titre} (aucun match)")
            try:
                cursor.execute(
                    "INSERT IGNORE INTO airvs_manquants "
                    "(artiste, titre, source, animateur, origine) "
                    "VALUES (%s, %s, 'animateurs', %s, %s)",
                    (_normaliser_manquant(artiste), _normaliser_manquant(titre), animateur, source_vps)
                )
                if cursor.rowcount > 0:
                    logger(tache_id, f"    → manquant ajouté (animateurs)")
            except Exception as _manq_e:
                logger(tache_id, f"    → persistance manquant échouée : {_manq_e}")

        # Insert dans airvs_animateurs (match ou pas — le dashboard l'affiche)
        cursor.execute(
            "INSERT INTO airvs_animateurs "
            "(vps_id, artiste, titre, genre, source, commentaire, video_url, animateur, "
            " match_song_id, match_passe, statut_vps, date_soumission) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (vps_id, artiste, titre, genre_vps, source_vps, commentaire_vps, video_url_vps,
             animateur,
             match_result["ID"] if match_result else None,
             _passe_from_methode(match_methode) if match_result else None,
             statut_vps, date_soumission_vps)
        )
        db.commit()
        ids_synced.append(vps_id)

    cursor.close()
    db.close()

    # 3) ACK
    if ids_synced:
        ack_data = _vps_post("/api/sync/ack", {"ids": ids_synced})
        nb_acked = ack_data["acknowledged"] if ack_data else 0
        logger(tache_id, f"[VPS SYNC] {nb_acked} soumission(s) ackée(s) sur le VPS")

    logger(tache_id,
        f"[VPS SYNC] Terminé : {len(soumissions)} pullées, "
        f"{nb_match} match, {nb_no_match} sans match")


def executer_sync_vps_shazam_externe(tache_id, parametres):
    """Tâche planifiable : pull les shazam_external du VPS, insert dans airvs_shazam."""
    logger(tache_id, "[VPS SYNC] Début sync shazam externe")

    if not VPS_SYNC_TOKEN:
        logger(tache_id, "[VPS SYNC] ERREUR : VPS_SYNC_TOKEN non configuré")
        return

    # 1) Pull
    data = _vps_get("/api/shazam/external/pull")
    if data is None:
        logger(tache_id, "[VPS SYNC] Erreur lors du pull shazam_external")
        return

    entries = data.get("entries", [])
    if not entries:
        logger(tache_id, "[VPS SYNC] Aucun shazam_external en attente")
        return

    logger(tache_id, f"[VPS SYNC] {len(entries)} shazam_external récupérée(s)")

    # 2) Insert dans airvs_shazam
    db = pymysql.connect(**DB_CONFIG)
    cursor = db.cursor(pymysql.cursors.DictCursor)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS airvs_shazam (
            id INT AUTO_INCREMENT PRIMARY KEY,
            artiste VARCHAR(255) NOT NULL,
            titre VARCHAR(255) NOT NULL,
            date_reconnaissance DATETIME DEFAULT CURRENT_TIMESTAMP,
            date_import DATETIME DEFAULT CURRENT_TIMESTAMP,
            source VARCHAR(50) DEFAULT 'macrodroid',
            animateur VARCHAR(100) DEFAULT NULL,
            INDEX idx_artiste_titre (artiste, titre),
            INDEX idx_date (date_reconnaissance),
            INDEX idx_animateur (animateur)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    # Migration : ajouter la colonne animateur si absente
    try:
        cursor.execute("SHOW COLUMNS FROM airvs_shazam LIKE 'animateur'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE airvs_shazam ADD COLUMN animateur VARCHAR(100) DEFAULT NULL AFTER source")
            cursor.execute("CREATE INDEX idx_animateur ON airvs_shazam (animateur)")
            db.commit()
            logger.info("[Shazam ext] Migration : colonne animateur ajoutée")
    except Exception as e:
        logger.debug(f"[Shazam ext] Migration animateur (ignorée si déjà faite) : {e}")
    db.commit()

    ids_synced = []
    nb_inserted = 0
    nb_dup = 0

    for e in entries:
        artiste = e["artiste"].strip()
        titre = e["titre"].strip()
        vps_id = e["id"]
        animateur = (e.get("animateur") or "").strip() or None

        if not artiste or not titre:
            ids_synced.append(vps_id)
            continue

        # Dédup 24h
        cursor.execute(
            "SELECT id FROM airvs_shazam "
            "WHERE LOWER(artiste) = LOWER(%s) AND LOWER(titre) = LOWER(%s) "
            "AND date_import > NOW() - INTERVAL 24 HOUR LIMIT 1",
            (artiste, titre)
        )
        existant = cursor.fetchone()
        if existant:
            nb_dup += 1
            ids_synced.append(vps_id)
            continue



        # Match 5 passes contre radiodb.songs (B1.2)
        try:
            _shz_match, _shz_methode = _match_titre_songs(cursor, artiste, titre)
            if _shz_match:
                logger(tache_id, f"  ✓ {artiste} - {titre} → song #{_shz_match['ID']} ({_shz_methode})")
            else:
                logger(tache_id, f"  ✗ {artiste} - {titre} (aucun match)")
                try:
                    cursor.execute(
                        "INSERT IGNORE INTO airvs_manquants "
                        "(artiste, titre, source, animateur, origine) "
                        "VALUES (%s, %s, 'shazam', %s, 'shazam_externe')",
                        (_normaliser_manquant(artiste), _normaliser_manquant(titre), animateur)
                    )
                    if cursor.rowcount > 0:
                        logger(tache_id, f"    → manquant ajouté (shazam)")
                except Exception as _manq_shz_e:
                    logger(tache_id, f"    → persistance manquant échouée : {_manq_shz_e}")
        except Exception as _shz_match_e:
            logger(tache_id, f"  ⚠ match échoué pour {artiste} - {titre} : {_shz_match_e}")

        cursor.execute(
            "INSERT INTO airvs_shazam (artiste, titre, source, animateur) VALUES (%s, %s, %s, %s)",
            (artiste, titre, "shazam_externe", animateur)
        )
        db.commit()
        nb_inserted += 1
        ids_synced.append(vps_id)
        logger(tache_id, f"  + {artiste} - {titre}")

    cursor.close()
    db.close()

    # 3) ACK
    if ids_synced:
        ack_data = _vps_post("/api/shazam/external/ack", {"ids": ids_synced})
        nb_acked = ack_data["acknowledged"] if ack_data else 0
        logger(tache_id, f"[VPS SYNC] {nb_acked} shazam_external acké(s) sur le VPS")

    logger(tache_id,
        f"[VPS SYNC] Terminé : {len(entries)} pullées, "
        f"{nb_inserted} insérées, {nb_dup} doublons")


def _sync_vps_automatique():
    """Insère les tâches SYNC_VPS_SOUMISSIONS + SYNC_VPS_SHAZAM_EXT si :
      - aucune tâche du même type n'est déjà en attente/en_cours.
    """
    try:
        if not VPS_SYNC_TOKEN:
            return  # Token non configuré

        db = pymysql.connect(**DB_CONFIG)
        cursor = db.cursor(pymysql.cursors.DictCursor)

        for action in ("SYNC_VPS_SOUMISSIONS", "SYNC_VPS_SHAZAM_EXT"):
            cursor.execute(
                "SELECT id FROM taches_planifiees "
                "WHERE type_action = %s AND statut IN ('en_attente', 'en_cours') "
                "LIMIT 1",
                (action,)
            )
            if cursor.fetchone():
                continue  # Déjà en file

            cursor.execute(
                "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
                "VALUES (%s, 'en_attente', %s, NOW())",
                (action, json.dumps({"auto_scheduled": True}))
            )
            task_id = cursor.lastrowid
            db.commit()
            print(f"[VPS SYNC AUTO] Tâche {action} #{task_id} planifiée")

        cursor.close()
        db.close()
    except Exception as e:
        print(f"[VPS SYNC AUTO] Erreur : {e}")


def main():
    print(f"Worker Ubuntu démarré (version {WORKER_VERSION}). En attente de tâches SQL...")
    # Créer les tables airvs_* si manquantes (idempotent)
    _assurer_schema_airvs_couleur_worker()

    # ── Enregistrer la version du worker en base pour vérification dashboard ──
    # On crée une table dédiée si elle n'existe pas, puis on UPSERT la version.
    try:
        db_v = pymysql.connect(**DB_CONFIG)
        cur_v = db_v.cursor()
        cur_v.execute("""
            CREATE TABLE IF NOT EXISTS airvs_worker_info (
                id INT PRIMARY KEY DEFAULT 1,
                version VARCHAR(64) NOT NULL,
                demarrage_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                hostname VARCHAR(128) DEFAULT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur_v.execute("DELETE FROM airvs_worker_info WHERE id = 1")
        cur_v.execute(
            "INSERT INTO airvs_worker_info (id, version, demarrage_le, hostname) "
            "VALUES (1, %s, NOW(), %s)",
            (WORKER_VERSION, os.uname().nodename if hasattr(os, 'uname') else 'unknown')
        )
        db_v.commit()
        cur_v.close()
        db_v.close()
        print(f"  ✓ Version {WORKER_VERSION} enregistrée dans airvs_worker_info")
    except Exception as e:
        print(f"  ⚠ Impossible d'enregistrer la version en base: {e}")

    # ── Nettoyer les tâches orphelines du run précédent ──
    try:
        db_orph = pymysql.connect(**DB_CONFIG)
        cur_orph = db_orph.cursor()
        # Tâches en_cours depuis plus d'1h = le worker précédent a crashé
        cur_orph.execute(
            "UPDATE taches_planifiees SET statut = 'erreur', date_fin = NOW() "
            "WHERE statut = 'en_cours' AND date_creation < NOW() - INTERVAL 1 HOUR"
        )
        nb_orph_cours = cur_orph.rowcount
        # Tâches d'annulation orphelines (types jamais traités)
        placeholders = ','.join(['%s'] * len(TYPES_ORPHELINS_EN_ATTENTE))
        cur_orph.execute(
            f"DELETE FROM taches_planifiees "
            f"WHERE type_action IN ({placeholders}) AND statut = 'en_attente'",
            TYPES_ORPHELINS_EN_ATTENTE
        )
        nb_orph_att = cur_orph.rowcount
        db_orph.commit()
        cur_orph.close()
        db_orph.close()
        if nb_orph_cours or nb_orph_att:
            print(f"  ✓ Nettoyage orphelines : {nb_orph_cours} en_cours->erreur, "
                  f"{nb_orph_att} annulation(s) supprimée(s)")
        else:
            print(f"  ✓ Aucune tâche orpheline")
    except Exception as e:
        print(f"  ⚠ Nettoyage orphelines échoué : {e}")

    # ── Détecter les colonnes optionnelles de songs au démarrage (cache) ──
    try:
        opt_cols = _detecter_colonnes_optionnelles_worker()
        detectees = {k: v for k, v in opt_cols.items() if v}
        if detectees:
            print(f"  ✓ Colonnes optionnelles songs détectées : {detectees}")
        else:
            print(f"  ⚠ Aucune colonne optionnelle songs détectée (sm_weight/rating/play_count/date_played)")
    except Exception as e:
        print(f"  ⚠ Détection colonnes optionnelles échouée au démarrage : {e}")

    # ── 2026.06.30-A : Tables pour l'auto-check Google Sheets ──
    try:
        db_ac = pymysql.connect(**DB_CONFIG)
        cur_ac = db_ac.cursor()
        cur_ac.execute("""
            CREATE TABLE IF NOT EXISTS airvs_auto_check_config (
                id                  INT AUTO_INCREMENT PRIMARY KEY,
                actif               TINYINT      NOT NULL DEFAULT 0,
                frequence           VARCHAR(20)  NOT NULL DEFAULT '2x_jour',
                horaires            JSON         NOT NULL,
                sheets_surveilles   JSON         NOT NULL,
                derniere_execution  DATETIME     NULL,
                prochaine_execution DATETIME     NULL,
                date_creation       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur_ac.execute("""
            CREATE TABLE IF NOT EXISTS airvs_notifications (
                id                  INT AUTO_INCREMENT PRIMARY KEY,
                type_notification   VARCHAR(30)  NOT NULL DEFAULT 'nouvelles_lignes',
                sheet_alias         VARCHAR(40)  NOT NULL,
                message             TEXT         NOT NULL,
                lu                  TINYINT      NOT NULL DEFAULT 0,
                date_creation       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_lu (lu),
                INDEX idx_date (date_creation)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur_ac.execute("""
            CREATE TABLE IF NOT EXISTS airvs_auto_check_snapshots (
                id                      INT AUTO_INCREMENT PRIMARY KEY,
                sheet_alias             VARCHAR(40)  NOT NULL,
                onglet                  VARCHAR(60)  NOT NULL,
                nb_lignes               INT          NOT NULL DEFAULT 0,
                derniere_verification   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE INDEX idx_alias_onglet (sheet_alias, onglet)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        db_ac.commit()
        cur_ac.close()
        db_ac.close()
        print("  ✓ Tables auto-check créées/vérifiées")
    except Exception as e:
        print(f"  ⚠ Impossible de créer les tables auto-check : {e}")

    # ── Évolution 1 (Piges → Podcasts) : table de suivi des promotions ──
    try:
        db_pp = pymysql.connect(**DB_CONFIG)
        cur_pp = db_pp.cursor()
        cur_pp.execute("""
            CREATE TABLE IF NOT EXISTS airvs_piges_promotions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                task_id INT NULL COMMENT 'Référence vers taches_planifiees.id (NULL tant que la tâche worker n''est pas encore créée)',

                source_date DATE NOT NULL,
                source_file VARCHAR(255) NOT NULL COMMENT 'ex: 14-00.mp3',
                extract_enabled TINYINT DEFAULT 0,
                extract_start TIME NULL,
                extract_duration TIME NULL,

                podcast_id VARCHAR(40) NOT NULL COMMENT 'ID podcast Azuracast #2 (UUID)',
                episode_id VARCHAR(40) NULL COMMENT 'ID épisode créé côté Azuracast (UUID)',
                episode_url VARCHAR(500) NULL,

                title VARCHAR(255) NOT NULL,
                description TEXT,
                episode_number INT NULL,
                season_number INT NULL,
                publish_date DATETIME NOT NULL,
                category VARCHAR(100),
                explicit TINYINT DEFAULT 0,
                artwork_path VARCHAR(255) NULL,

                status ENUM('en_attente', 'en_cours', 'termine', 'erreur') DEFAULT 'en_attente',
                error_message TEXT NULL,

                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_by VARCHAR(50),

                INDEX idx_pp_status (status),
                INDEX idx_pp_source_date (source_date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        db_pp.commit()
        cur_pp.close()
        db_pp.close()
        print("  ✓ Table airvs_piges_promotions créée/vérifiée")
    except Exception as e:
        print(f"  ⚠ Impossible de créer la table airvs_piges_promotions : {e}")

    # -- 2026.08.28-B : Table airvs_manquants (pivot 4 flux editoriaux) --
    try:
        db_mq = pymysql.connect(**DB_CONFIG)
        cur_mq = db_mq.cursor()
        cur_mq.execute("""
            CREATE TABLE IF NOT EXISTS airvs_manquants (
              id              INT AUTO_INCREMENT PRIMARY KEY,
              artiste         VARCHAR(500) NOT NULL,
              titre           VARCHAR(500) NOT NULL,
              annee           VARCHAR(10)  DEFAULT NULL,
              album           VARCHAR(500) DEFAULT NULL,
              source          ENUM('prefill_sheets','shazam','animateurs') NOT NULL,
              sheet_alias     VARCHAR(40)  DEFAULT NULL,
              onglet          VARCHAR(60)  DEFAULT NULL,
              semaine         VARCHAR(10)  DEFAULT NULL,
              animateur       VARCHAR(100) DEFAULT NULL,
              origine         VARCHAR(255) DEFAULT NULL,
              statut          ENUM('en_attente','importe','resolu') DEFAULT 'en_attente',
              id_import       INT          DEFAULT NULL,
              resolu_le       DATETIME     DEFAULT NULL,
              date_creation   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE INDEX uq_art_titre_src (artiste(255), titre(255), source),
              INDEX idx_statut (statut),
              INDEX idx_source (source)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        db_mq.commit()
        cur_mq.close()
        db_mq.close()
        print("  ✓ Table airvs_manquants creee/verifiee")
    except Exception as e:
        print(f"  ⚠ Impossible de creer la table airvs_manquants : {e}")


    # Compteur pour la vérification périodique des créneaux
    dernier_check_couleur = 0
    dernier_check_auto = 0
    dernier_check_auto_validation = 0
    dernier_check_sync_azura_auto = 0
    dernier_check_sync_vps = 0
    dernier_check_orphelines = 0
    while True:
        try:
            # ── Vérification périodique des créneaux PROGRAMMATION_COULEUR ──
            now_ts = time.time()
            if now_ts - dernier_check_couleur >= INTERVALLE_VERIFICATION_COULEUR:
                _verifier_creneaux_programmation_couleur()
                dernier_check_couleur = now_ts

            # ── Vérification périodique de l'auto-check Google Sheets ──
            if now_ts - dernier_check_auto >= INTERVALLE_AUTO_CHECK:
                _verifier_auto_check_sheets()
                dernier_check_auto = now_ts

            # ── Vérification périodique des auto-validations Pool ──
            if now_ts - dernier_check_auto_validation >= INTERVALLE_AUTO_VALIDATION:
                _verifier_auto_validations()
                dernier_check_auto_validation = now_ts

            # ── Synchronisation automatique AzuraCast (8h-20h, 1x/h) ──
            if now_ts - dernier_check_sync_azura_auto >= INTERVALLE_SYNC_AZURA_AUTO:
                _sync_azura_automatique()
                dernier_check_sync_azura_auto = now_ts

            # ── Synchronisation automatique VPS (toutes les 5 min) ──
            if now_ts - dernier_check_sync_vps >= INTERVALLE_SYNC_VPS:
                _sync_vps_automatique()
                dernier_check_sync_vps = now_ts

            # ── Nettoyage périodique des tâches orphelines (toutes les heures) ──
            if now_ts - dernier_check_orphelines >= INTERVALLE_CLEANUP_ORPHELINES:
                try:
                    _oc = pymysql.connect(**DB_CONFIG)
                    _occ = _oc.cursor()
                    _occ.execute(
                        "UPDATE taches_planifiees SET statut = 'erreur', date_fin = NOW() "
                        "WHERE statut = 'en_cours' AND date_creation < NOW() - INTERVAL 1 HOUR"
                    )
                    nb_ec = _occ.rowcount
                    _ph = ','.join(['%s'] * len(TYPES_ORPHELINS_EN_ATTENTE))
                    _occ.execute(
                        f"DELETE FROM taches_planifiees "
                        f"WHERE type_action IN ({_ph}) AND statut = 'en_attente'",
                        TYPES_ORPHELINS_EN_ATTENTE
                    )
                    nb_ea = _occ.rowcount
                    _oc.commit()
                    _occ.close()
                    _oc.close()
                    if nb_ec or nb_ea:
                        print(f"[CLEANUP] {nb_ec} en_cours->erreur, {nb_ea} orpheline(s) supprimée(s)")
                except Exception:
                    pass
                dernier_check_orphelines = now_ts

            db = pymysql.connect(**DB_CONFIG)
            cursor = db.cursor(pymysql.cursors.DictCursor)
            query = (
                "SELECT * FROM taches_planifiees "
                "WHERE statut = 'en_attente' "
                "AND type_action IN ('COPIE_FICHIERS', 'COPIE_SHEETS', 'PREFILL_SHEETS', "
                "    'CREER_PLAYLIST_AZURA', 'IMPORT_M3U_PLAYLIST', 'SYNC_PLAYLISTS_CACHE', "
                "    'SYNC_AZURA_STATUS', 'PROGRAMMATION_COULEUR', "
                "    'IMPORT_SHEETS_COULEUR', 'VALIDATION_POOL_SHEETS', "
                "    'AUTO_CHECK_SHEETS', 'SYNC_GRILLE_WEB', 'BULK_SCHEDULE', "
                "    'AUDIENCE_GET_DATA', 'PIGE_LIST', 'PIGE_EXTRACT', "
                "    'PIGE_PROMOTE_PODCAST', 'PODCASTS_LIST', 'PODCASTS_EPISODES', "
                "    'SHAZAM_SYNC_SHEET', 'SYNC_VPS_SOUMISSIONS', 'SYNC_VPS_SHAZAM_EXT', "
                "    'IMPORT_MASSE', 'SUSPECT_REJECT') "
                "ORDER BY id ASC LIMIT 1"
            )
            cursor.execute(query)
            tache = cursor.fetchone()

            if tache:
                tache_id = tache['id']
                parametres = json.loads(tache['parametres']) if tache['parametres'] else {}

                cursor.execute(
                    "UPDATE taches_planifiees SET statut = 'en_cours' WHERE id = %s",
                    (tache_id,)
                )
                db.commit()
                cursor.close()
                db.close()

                type_action = tache['type_action']
                print(f"Tâche {tache_id} ({type_action}) interceptée.")

                task_exception = None
                try:
                    if type_action == 'PREFILL_SHEETS':
                        executer_prefill_sheets(tache_id, parametres)
                    elif type_action == 'COPIE_SHEETS':
                        executer_copie_sheets(tache_id, parametres)
                    elif type_action == 'CREER_PLAYLIST_AZURA':
                        executer_creer_playlist_azura(tache_id, parametres)
                    elif type_action == 'IMPORT_M3U_PLAYLIST':
                        executer_import_m3u_playlist(tache_id, parametres)
                    elif type_action == 'SYNC_PLAYLISTS_CACHE':
                        executer_sync_playlists_cache(tache_id, parametres)
                    elif type_action == 'SYNC_AZURA_STATUS':
                        executer_sync_azura_status(tache_id, parametres)
                    elif type_action == 'PROGRAMMATION_COULEUR':
                        executer_programmation_couleur(tache_id, parametres)
                    elif type_action == 'IMPORT_SHEETS_COULEUR':
                        executer_import_sheets_couleur(tache_id, parametres)
                    elif type_action == 'VALIDATION_POOL_SHEETS':
                        executer_validation_pool_sheets(tache_id, parametres)
                    elif type_action == 'AUTO_CHECK_SHEETS':
                        executer_auto_check_sheets(tache_id, parametres)
                    elif type_action == 'SYNC_GRILLE_WEB':
                        executer_sync_grille_web(tache_id, parametres)
                    elif type_action == 'BULK_SCHEDULE':
                        executer_bulk_schedule(tache_id, parametres)
                    elif type_action == 'AUDIENCE_GET_DATA':
                        executer_audience_get_data(tache_id, parametres)
                    elif type_action == 'PIGE_LIST':
                        executer_pige_list(tache_id, parametres)
                    elif type_action == 'PIGE_EXTRACT':
                        executer_pige_extract(tache_id, parametres)
                    elif type_action == 'PIGE_PROMOTE_PODCAST':
                        executer_pige_promote_podcast(tache_id, parametres)
                    elif type_action == 'PODCASTS_LIST':
                        executer_podcasts_list(tache_id, parametres)
                    elif type_action == 'PODCASTS_EPISODES':
                        executer_podcasts_episodes(tache_id, parametres)
                    elif type_action == 'SHAZAM_SYNC_SHEET':
                        executer_shazam_sync_sheet(tache_id, parametres)
                    elif type_action == 'SYNC_VPS_SOUMISSIONS':
                        executer_sync_vps_soumissions(tache_id, parametres)
                    elif type_action == 'SYNC_VPS_SHAZAM_EXT':
                        executer_sync_vps_shazam_externe(tache_id, parametres)
                    elif type_action == 'IMPORT_MASSE':
                        if IMPORT_MASSE_MODULE_DISPONIBLE:
                            executer_import_masse(
                                tache_id, parametres,
                                _worker_ctx={
                                    'logger': logger,
                                    'windows_vers_linux': windows_vers_linux,
                                }
                            )
                        else:
                            logger(tache_id, "ERREUR : module import_masse.py non disponible.")
                            raise RuntimeError("Module import_masse.py non disponible")
                    elif type_action == 'SUSPECT_REJECT':
                        executer_suspect_reject(tache_id, parametres)
                    else:
                        executer_copie(tache_id, parametres)
                except Exception as exc:
                    # Ne pas laisser la tâche en 'en_cours' à jamais : on logge et on marque 'erreur'
                    task_exception = exc
                    try:
                        logger(tache_id, f"!!! EXCEPTION FATALE pendant l'exécution: "
                                         f"{type(exc).__name__}: {exc}")
                    except Exception:
                        print(f"[Tâche {tache_id}] Exception fatale: {type(exc).__name__}: {exc}")
                    print(f"Tâche {tache_id} a levé une exception: {exc}")

                db = pymysql.connect(**DB_CONFIG)
                cursor = db.cursor()
                if task_exception is None:
                    # Vérifier si la tâche s'est terminée par une annulation push
                    # (le log contient "=== ANNULÉ ===" dans ce cas)
                    cursor.execute(
                        "SELECT log_resultat FROM taches_planifiees WHERE id = %s",
                        (tache_id,)
                    )
                    row = cursor.fetchone()
                    _log_text = row[0] if row and row[0] else ''
                    if '=== ANNULÉ ===' in _log_text:
                        cursor.execute(
                            "UPDATE taches_planifiees SET statut = 'annule', date_fin = NOW() WHERE id = %s",
                            (tache_id,)
                        )
                        print(f"Tâche {tache_id} annulée.")
                    else:
                        cursor.execute(
                            "UPDATE taches_planifiees SET statut = 'termine', date_fin = NOW() WHERE id = %s",
                            (tache_id,)
                        )
                        print(f"Tâche {tache_id} terminée.")
                else:
                    # 1. Logger le message d'erreur proprement (logger() utilise une
                    #    requête paramétrée → pas de problème d'apostrophes)
                    try:
                        logger(tache_id, f"\n!!! TÂCHE MARQUÉE EN ERREUR: "
                                         f"{type(task_exception).__name__}: {task_exception}")
                    except Exception:
                        pass
                    # 2. UPDATE simple sans embed le message (déjà loggé ci-dessus)
                    cursor.execute(
                        "UPDATE taches_planifiees "
                        "SET statut = 'erreur', date_fin = NOW() "
                        "WHERE id = %s",
                        (tache_id,)
                    )
                db.commit()
                cursor.close()
                db.close()
                print(f"Tâche {tache_id} {'terminée' if task_exception is None else 'marquée en erreur'}.")
            else:
                cursor.close()
                db.close()
                time.sleep(2)

        except pymysql.Error as e:
            print(f"Erreur SQL: {e}. Nouvelle tentative dans 5s...")
            time.sleep(5)
        except Exception as e:
            print(f"Erreur inattendue: {e}. Nouvelle tentative dans 5s...")
            time.sleep(5)


if __name__ == "__main__":
    main()
