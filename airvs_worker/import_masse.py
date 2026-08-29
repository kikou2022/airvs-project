#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════
# IMPORT DE MASSE DÉDOUBLONNÉ — Module worker (v3)
# ═══════════════════════════════════════════════════════════════
# Pipeline intégré au worker Ubuntu Studio :
#   Dédoublonnage -> mp3gain (89 dB) -> Copie FolderSync -> Trigger REST
#
# Architecture :
#   - Source fichiers : /mnt/stockage_160go (montage réseau vers USB Debian)
#   - DB RadioDJ     : pymysql vers 192.168.1.30 (radiodb, LAN)
#   - mp3gain        : local sur Ubuntu Studio
#   - Dossier sync   : /mnt/projet_radio3 (montage local de E:\)
#   - REST Server    : HTTP vers 192.168.1.39:7777 (RadioDJ Windows)
#
# Ce module est importé par worker_ubuntu.py.
# Les fonctions worker logger() et windows_vers_linux() sont passées
# via le paramètre _worker_ctx.
#
# Historique :
#   v3 (aout 2026) : réécriture pour Ubuntu Studio, intégré au worker.
# ═══════════════════════════════════════════════════════════════

import re
import json
import shutil
import subprocess
import unicodedata
from pathlib import Path
from datetime import datetime
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import pymysql

# DB RadioDJ (sur Debian .30) — connexion réseau depuis Ubuntu Studio
RADIODB_CONFIG = {
    "host": "192.168.1.30",
    "port": 3306,
    "user": "sebastien",
    "password": "idylle@SL",
    "database": "radiodb",
    "charset": "utf8mb4",
}

# Base du dossier de synchronisation FolderSync (montage local de E:)
FOLDERSYNC_BASE = Path("/mnt/projet_radio3/Musique")

# DB airvs_dashboard (sur Debian .30) — pour suspects_validated
DASHBOARD_DB_CONFIG = {
    "host": RADIODB_CONFIG["host"],
    "port": RADIODB_CONFIG["port"],
    "user": RADIODB_CONFIG["user"],
    "password": RADIODB_CONFIG["password"],
    "database": "airvs_dashboard",
    "charset": "utf8mb4",
    "collation": "utf8mb4_general_ci",
}

# Lot mp3gain (fichiers par appel)
LOT_TAILLE = 50

# ID événement RadioDJ par défaut (SyncFolderSync)
RADIODJ_EVENT_ID_DEFAUT = 182

# mutagen (optionnel — fallback sur dédoublonnage par nom seul)
try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, TIT2, TPE1
    from mutagen import File as MutagenFile
    MUTAGEN_OK = True
except ImportError:
    MUTAGEN_OK = False


# ─── Dossier de sync éditorial ────────────────────────────────────

def _dossier_sync_courant(sync_folder_force=None):
    """Retourne le dossier FolderSync de la semaine courante.

    Période éditoriale :
      Septembre à juin  : NOUVELLES_ENTREES/SEMAINE_XX/
      Juillet-août       : NOUVELLES_ENTREES_ETE/SEMAINE_XX/

    Args:
        sync_folder_force: si fourni, utilisé tel quel (rattrapage).
    """
    if sync_folder_force:
        p = Path(sync_folder_force)
        p.mkdir(parents=True, exist_ok=True)
        return p

    mois = datetime.now().month
    semaine = datetime.now().isocalendar()[1]

    if mois in (7, 8):
        base = FOLDERSYNC_BASE / "NOUVELLES_ENTREES_ETE"
    else:
        base = FOLDERSYNC_BASE / "NOUVELLES_ENTREES"

    dossier = base / f"SEMAINE_{semaine}"
    dossier.mkdir(parents=True, exist_ok=True)
    return dossier


# ─── Helpers ─────────────────────────────────────────────────────

def _format_duree(secondes):
    """Formatte une durée en secondes vers M:SS."""
    if not secondes or secondes <= 0:
        return "N/A"
    m, s = divmod(int(secondes), 60)
    return f"{m}:{s:02d}"


def _format_taille(octets):
    """Formatte une taille en octets vers Mo."""
    if not octets:
        return "N/A"
    return f"{octets / 1024 / 1024:.1f} Mo"


# ─── Tags ID3 ────────────────────────────────────────────────────

def _lire_tags(fichier):
    """Retourne (artist, title) depuis les tags ID3."""
    if not MUTAGEN_OK:
        return "", ""
    try:
        tags = ID3(str(fichier))
        artist = str(tags.get("TPE1", "")).strip()
        title = str(tags.get("TIT2", "")).strip()
        return artist, title
    except Exception:
        return "", ""


def _lire_meta(fichier):
    """Lit les métadonnées ID3 pour le rapport."""
    info = {
        'artist': 'Inconnu', 'title': fichier.stem,
        'album': '', 'year': '0', 'duration': 0.0,
    }
    try:
        audio = MutagenFile(str(fichier), easy=True)
        if audio is not None:
            info['artist'] = str(audio.get('artist', ['Inconnu'])[0])
            info['title'] = str(audio.get('title', [fichier.stem])[0])
            info['album'] = str(audio.get('album', [''])[0])
            year_raw = str(audio.get('date', ['0'])[0])
            info['year'] = year_raw.split('-')[0]
    except Exception:
        pass
    try:
        audio_full = MutagenFile(str(fichier), easy=False)
        if audio_full and hasattr(audio_full.info, 'length') and audio_full.info.length:
            info['duration'] = float(audio_full.info.length)
    except Exception:
        pass
    return info


# ─── Fonctions de normalisation (portées de blueprints/shazam.py) ──
# Mêmes fonctions que dans shazam.py et app_vps_ovh_1.py.
# Post-bascule : à mutualiser dans un module partagé.

def _normaliser_pour_recherche(texte):
    """Normalise un texte pour comparaison floue :
    minuscules, sans accents, sans ponctuation, mots de liaison remplacés."""
    if not texte:
        return ""
    texte = str(texte).strip().lower()
    texte = ''.join(
        c for c in unicodedata.normalize('NFD', texte)
        if unicodedata.category(c) != 'Mn'
    )
    for caractere in "()[]&'+-":
        texte = texte.replace(caractere, " ")
    texte = re.sub(r'\b(?:et|and|x)\b', ' ', texte, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', texte).strip()


def _extraire_principal(texte):
    """Extrait la partie principale : supprime feat./ft./featuring.
    Conserve les parenthèses non-feat (live, radio edit, etc.)."""
    if not texte:
        return texte
    texte = re.sub(r'\s*\([^)]*(?:feat\.?|ft\.?|featuring)[^)]*\)\s*', ' ', texte, flags=re.IGNORECASE).strip()
    texte = re.split(r'\s+(?:feat\.?|ft\.?|featuring)\s+', texte, flags=re.IGNORECASE)[0].strip()
    return texte


def _extraire_nu(texte):
    """Extrait le noyau nu : supprime TOUTES les parenthèses ET les feat.
    Plus agressif qu'extraire_principal."""
    if not texte:
        return texte
    texte = re.sub(r'\s*\([^)]*\)\s*', ' ', texte).strip()
    texte = re.split(r'\s+(?:feat\.?|ft\.?|featuring)\s+', texte, flags=re.IGNORECASE)[0].strip()
    return texte


def _extraire_artiste_principal(texte):
    """Extrait le premier artiste avant tout séparateur.
    Étend _extraire_principal en gérant aussi ; et , comme séparateurs
    d'artistes (ex: "Mark Ronson; Bruno Mars" → "Mark Ronson").
    Gère aussi les séparateurs mal espacés (ex: "Feat.Bruno" → "Feat. Bruno").
    Spécifique au pipeline d'import."""
    if not texte:
        return ""
    # Normaliser les séparateurs mal espacés : "Feat." sans espace après
    texte = re.sub(r'(feat\.?|ft\.?|featuring)(?=[A-Za-zÀ-ÿ])', r'\1 ', texte, flags=re.IGNORECASE)
    art = _extraire_principal(texte)
    # Couper sur ; et , (séparateurs alternatifs d'artistes)
    art = re.split(r'\s*[;,]\s*', art)[0].strip()
    return art


# ─── Termes de version (remix, club mix, etc.) ──────────────

_TERMES_VERSION = re.compile(
    r'\b(remix|club\s*mix|radio\s*edit|extended|dub|rework|'  
    r'reprise|live|acoustic|instrumental|a\s*cappella|'  
    r'mashup|bootleg|vip|flip|rerub|reinterpretation|'  
    r'cover|medley|megamix|dj\s*mix|preview|snippet|'  
    r'interlude|outro|intro|transition|bonus\s*track|'  
    r'deluxe|explicit|clean|short|long|full|original|'  
    r'alternate|alternative|version|re-?recorded|remaster(?:ed)?|'  
    r'demo|single|album\s*version|12"|7"|\d+\s*bit)\b',
    re.IGNORECASE
)


def _extraire_parentheses(texte):
    """Extrait le contenu de toutes les parenthèses : (contenu) ou [contenu]."""
    if not texte:
        return []
    return re.findall(r'[\(\[]([^\)\]]*)[\)\]]', texte)


def _detecter_termes_version(texte):
    """Détecte les termes de version dans les parenthèses d'un texte.
    Retourne la liste des groupes parenthétiques contenant au moins un terme de version.
    """
    if not texte:
        return []
    termes = []
    for p in _extraire_parentheses(texte):
        if _TERMES_VERSION.search(p):
            termes.append(p.strip())
    return termes


# ─── Vérification en base RadioDJ ───────────────────────────────

def _chercher_decision_precedente(artist, title):
    """Interroge suspects_validated pour une decision humaine anterieure.

    Recherche par artist + title (case-insensitive via collation).
    Retourne le dict de la decision la plus recente ou None.
    """
    if not artist or not title:
        return None
    try:
        db = pymysql.connect(
            **DASHBOARD_DB_CONFIG, cursorclass=pymysql.cursors.DictCursor
        )
        cursor = db.cursor()
        cursor.execute(
            "SELECT decision, methode_detection, matched_song_id, "
            "matched_artist, matched_title, version_terms, validated_at "
            "FROM suspects_validated "
            "WHERE artist = %s AND title = %s "
            "ORDER BY validated_at DESC LIMIT 1",
            (artist, title)
        )
        row = cursor.fetchone()
        cursor.close()
        db.close()
        return row
    except Exception:
        return None


def _verifier_en_base(cursor, fichier):
    """Vérifie si le fichier MP3 est déjà en base RadioDJ.

    Passe 1 (FILENAME)     : correspondance exacte du nom de fichier en fin de path.
      -> Catégorie DOUBLON (haute confiance)

    Passe 2 (TAGS EXACT)   : correspondance exacte artist + titre
      (case-insensitive via collation utf8mb4_general_ci).
      -> Catégorie SUSPECT

    Passe 2b (PRINCIPAL)   : premier artiste (sans feat/ft/featuring/;/,)
      + titre exact. Gère les variantes de séparation : "ft." vs "Feat." vs ";".
      Vérification Python-side après requête SQL filtrée.
      -> Catégorie SUSPECT

    Passe 2c (NU)          : artiste et titre sans parenthèses ni feat.
      Gère : "Song (Radio Edit)" vs "Song", "Artist (feat. X) (Remix)" vs "Artist".
      -> Catégorie SUSPECT

    Retourne (trouve, methode, detail, song_id, bdd_row).
      bdd_row est le dict de la ligne BDD ou None.
      methode ∈ {"filename", "tags", "tags_principal", "tags_nu", "non_trouve"}
    """
    nom = fichier.name
    nom_lower = nom.lower()

    # Lire les tags ID3 une seule fois (réutilisé par les passes 2/2b/2c)
    artist, title = _lire_tags(fichier)

    # ── Passe 1 : recherche par nom de fichier (match exact en fin de path) ──
    try:
        cursor.execute(
            "SELECT id, artist, title, path, duration FROM songs "
            "WHERE path LIKE %s LIMIT 10",
            (f"%{nom}",)
        )
        rows = cursor.fetchall()
        rows = [r for r in rows if r['path'].lower().endswith(nom_lower)]
        if rows:
            r = rows[0]
            detail = f"ID:{r['id']} {r['artist']} - {r['title']} [{r['path']}]"
            return True, "filename", detail, r['id'], r
    except Exception:
        pass

    # ── Passe 2 : correspondance exacte par tags ID3 (artist + titre) ──
    # Collation utf8mb4_general_ci = case-insensitive
    if artist and title:
        try:
            cursor.execute(
                "SELECT id, artist, title, path, duration FROM songs "
                "WHERE artist = %s AND title = %s LIMIT 10",
                (artist, title)
            )
            rows = cursor.fetchall()
            if rows:
                r = rows[0]
                detail = (
                    f"ID:{r['id']} {r['artist']} - {r['title']} "
                    f"[tags exact: {artist} / {title}]"
                )
                return True, "tags", detail, r['id'], r
        except Exception:
            pass

    # ── Passe 2b : artiste principal (sans feat/ft/featuring/;/,) ──
    # Gère les variantes de notation des artistes collaborateurs :
    #   "Mark Ronson ft. Bruno Mars" vs "Mark Ronson Feat. Bruno Mars"
    #   "Mark Ronson; Bruno Mars"  vs "Mark Ronson Feat. Bruno Mars"
    #   "Mark Ronson"               vs "Mark Ronson Feat. Bruno Mars"
    # Stratégie : requête SQL filtrée sur titre exact + artiste LIKE principal,
    # puis vérification Python-side que le principal du DB artiste correspond.
    if artist and title:
        art_principal = _extraire_artiste_principal(artist)
        if art_principal:
            try:
                cursor.execute(
                    "SELECT id, artist, title, path, duration FROM songs "
                    "WHERE title = %s AND artist LIKE %s LIMIT 50",
                    (title, f"%{art_principal}%")
                )
                for r in cursor.fetchall():
                    db_art_p = _extraire_artiste_principal(r['artist'])
                    if db_art_p.lower() == art_principal.lower():
                        detail = (
                            f"ID:{r['id']} {r['artist']} - {r['title']} "
                            f"[tags_principal: '{artist}' ↔ '{r['artist']}' → '{art_principal}']"
                        )
                        return True, "tags_principal", detail, r['id'], r
            except Exception:
                pass

    # ── Passe 2c : nu (sans parenthèses ni feat) ──
    # Gère les variantes de titres : "Song (Radio Edit)" vs "Song"
    # et les combinaisons artiste+titre avec parenthèses diverses.
    if artist and title:
        art_nu = _extraire_nu(artist)
        tit_nu = _extraire_nu(title)
        if art_nu and tit_nu:
            # Ne lancer que si la forme nue diffère des formes précédentes
            if (art_nu.lower() != artist.lower() or
                    tit_nu.lower() != title.lower()):
                try:
                    cursor.execute(
                        "SELECT id, artist, title, path, duration FROM songs "
                        "WHERE title = %s AND artist LIKE %s LIMIT 50",
                        (tit_nu, f"%{art_nu}%")
                    )
                    for r in cursor.fetchall():
                        db_art_nu = _extraire_nu(r['artist'])
                        db_tit_nu = _extraire_nu(r['title'])
                        if (db_art_nu.lower() == art_nu.lower() and
                                db_tit_nu.lower() == tit_nu.lower()):
                            detail = (
                                f"ID:{r['id']} {r['artist']} - {r['title']} "
                                f"[tags_nu: '{artist}'/'{title}' → "
                                f"'{art_nu}'/'{tit_nu}']"
                            )
                            return True, "tags_nu", detail, r['id'], r
                except Exception:
                    pass

    return False, "non_trouve", "", 0, None


# ─── Déplacement / copie avec gestion collisions ──────────────────

def _deplacer(src, dst_dir):
    """Déplace src vers dst_dir, avec gestion de collisions (stem_1, ...)."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    if not dst.exists():
        shutil.move(str(src), str(dst))
        return dst
    stem, ext = src.stem, src.suffix
    idx = 1
    while True:
        dst = dst_dir / f"{stem}_{idx}{ext}"
        if not dst.exists():
            shutil.move(str(src), str(dst))
            return dst
        idx += 1


def _copier(src, dst_dir):
    """Copie src vers dst_dir, avec gestion de collisions."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    if not dst.exists():
        shutil.copy2(str(src), str(dst))
        return dst
    stem, ext = src.stem, src.suffix
    idx = 1
    while True:
        dst = dst_dir / f"{stem}_{idx}{ext}"
        if not dst.exists():
            shutil.copy2(str(src), str(dst))
            return dst
        idx += 1


# ─── mp3gain ──────────────────────────────────────────────────────

def _run_mp3gain(fichiers, log_fn):
    """Normalise les fichiers MP3 à 89 dB via mp3gain (track mode).

    Traite fichier par fichier pour un suivi en temps réel dans le log.

    Args:
        fichiers: liste de Path
        log_fn: callable(str) — fonction de log du worker

    Retourne {'succes': [Path], 'erreurs': [dict]}.
    """
    resultats = {'succes': [], 'erreurs': []}
    total = len(fichiers)
    if total == 0:
        return resultats

    log_fn(f"mp3gain : {total} fichier(s) à normaliser (89 dB, track mode)")

    # Traiter fichier par fichier pour un log en temps réel
    for i, fpath in enumerate(fichiers, 1):
        fname = fpath.name
        try:
            proc = subprocess.Popen(
                ["mp3gain", "-r", "-c", "-k", str(fpath)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            stdout, stderr = proc.communicate(timeout=120)

            if proc.returncode in (0, 2):
                resultats['succes'].append(fpath)
                # Extraire l'ajustement dB depuis le stdout de mp3gain
                # Format typique : "Applying adjustment +3.2dB to file..."
                # ou "File.mp3 -1.5dB" (déjà traité)
                dB_info = ""
                for line in (stdout or "").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    # mp3gain affiche la ligne de résultat avec le dB
                    if "dB" in line:
                        dB_info = line.split("dB")[0].strip() + "dB"
                        break
                tag = f"{i}/{total}"
                if dB_info:
                    log_fn(f"  [{tag}] {fname} -> {dB_info}")
                else:
                    log_fn(f"  [{tag}] {fname} -> OK")
            else:
                err_msg = (stderr or stdout or "").strip()[:200]
                log_fn(f"  [{i}/{total}] {fname} -> ERREUR mp3gain (rc={proc.returncode}) : {err_msg}")
                resultats['erreurs'].append(
                    {'filepath': str(fpath), 'error': f'mp3gain rc={proc.returncode}: {err_msg}'}
                )
        except subprocess.TimeoutExpired:
            log_fn(f"  [{i}/{total}] {fname} -> TIMEOUT")
            resultats['erreurs'].append(
                {'filepath': str(fpath), 'error': 'Timeout mp3gain'}
            )
        except FileNotFoundError:
            log_fn("  mp3gain non trouvé. sudo apt install mp3gain")
            resultats['erreurs'].extend(
                {'filepath': str(f), 'error': 'mp3gain non trouvé'} for f in fichiers[i-1:]
            )
            break
        except Exception as e:
            log_fn(f"  [{i}/{total}] {fname} -> ERREUR : {e}")
            resultats['erreurs'].append(
                {'filepath': str(fpath), 'error': str(e)}
            )

    return resultats


# ─── REST Server v4 RadioDJ ──────────────────────────────────────

def _declencher_sync_radiodj(api_url, api_pass, event_id, log_fn):
    """Déclenche la synchronisation FolderSync via REST Server v4.

    Format valide (aout 2026) :
      http://<IP>:<PORT>/opt?auth=<PASS>&command=RunEvent&arg=<EVENT_ID>
    Réponse : <string>200</string> = succès.

    Retourne {'ok': bool, 'message': str}.
    """
    result = {'ok': False, 'message': ''}

    if not api_url or not api_pass:
        result['message'] = (
            "API REST Server non configurée. Les fichiers sont dans le "
            "dossier de sync, la sync se fera au prochain cycle auto de FolderSync."
        )
        log_fn(result['message'])
        return result

    base = api_url.rstrip('/')
    pass_q = quote(api_pass, safe='')
    url = f"{base}/opt?auth={pass_q}&command=RunEvent&arg={event_id}"

    log_fn(f"Declenchement sync FolderSync via REST Server v4")
    log_fn(f"  URL      : {url}")
    log_fn(f"  Event ID : {event_id}")

    try:
        req = Request(url, method="GET")
        req.add_header("User-Agent", "AIRVS-Worker/3.0")

        with urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")[:1000]
            m = re.search(r'<string[^>]*>(\d+)</string>', body.strip())
            if m:
                code_body = int(m.group(1))
                if code_body == 200:
                    result['ok'] = True
                    result['message'] = (
                        f"REST Server v4 : RunEvent arg={event_id} succès "
                        f"-> FolderSync déclenché."
                    )
                elif code_body == 400:
                    result['message'] = (
                        f"REST Server v4 : argument invalide (body 400). "
                        f"Vérifiez event_id={event_id}."
                    )
                else:
                    result['message'] = f"REST Server v4 : échec (body {code_body})."
            else:
                if 200 <= resp.status < 300:
                    result['ok'] = True
                    result['message'] = f"HTTP {resp.status} — réponse positive (format inattendu)."
                else:
                    result['message'] = f"HTTP {resp.status} inattendu."
            log_fn(result['message'])
    except HTTPError as e:
        result['message'] = f"HTTP {e.code} : {e.reason}"
        log_fn(result['message'])
    except URLError as e:
        result['message'] = f"Connexion échouée : {e.reason}"
        log_fn(result['message'])
    except Exception as e:
        result['message'] = f"Erreur appel REST Server : {e}"
        log_fn(result['message'])

    return result


# ─── Pipeline principal ──────────────────────────────────────────

def executer_import_masse(tache_id, parametres, _worker_ctx=None):
    """Tâche planifiable : pipeline d'import de masse dédoublonné.

    Étapes :
      1. Dédoublonnage (SELECT sur radiodb.songs via réseau LAN)
      2. mp3gain 89 dB (local Ubuntu Studio, fichiers sur montage réseau)
      3. Copie vers dossier FolderSync de la semaine (montage local E:)
      4. Déclenchement sync RadioDJ via REST Server v4

    Paramètres dans tache['parametres'] :
      - source (str, obligatoire)  : chemin Linux ou Windows (ex: "/mnt/stockage_160go/A_trier" ou "U:\\A_trier")
      - execute (bool)             : true = réel, false = dry-run (défaut: false)
      - recursive (bool)           : scan récursif (défaut: false)
      - skip_mp3gain (bool)        : sauter mp3gain (défaut: false)
      - sync_folder (str, optionnel): forcer le dossier de sync
      - radiodj_api_url (str)      : URL REST Server (défaut: http://192.168.1.39:7777)
      - radiodj_api_pass (str)     : mot de passe REST Server v4
      - radiodj_event_id (int)    : ID événement (défaut: 182)

    _worker_ctx : dict avec les fonctions du worker
      - 'logger': callable(tache_id, message)
      - 'windows_vers_linux': callable(chemin_windows) -> Path
    """
    # Extraction du contexte worker (injection de dépendances)
    if _worker_ctx is None:
        _worker_ctx = {}
    _log = _worker_ctx.get('logger')
    _w2l = _worker_ctx.get('windows_vers_linux')

    # Fallback : si le module est utilisé hors du worker (tests, CLI)
    def _fallback_log(msg):
        print(f"[IMPORT_MASSE] {msg}")

    log_fn = _log if _log else _fallback_log

    # Wrapper logger(tache_id, msg)
    def log(msg):
        log_fn(tache_id, msg)

    # ── Extraction des paramètres ──
    source_raw = parametres.get('source', '').strip()
    execute = parametres.get('execute', False)
    recursive = parametres.get('recursive', False)
    skip_mp3gain = parametres.get('skip_mp3gain', False)
    sync_folder_force = parametres.get('sync_folder', '').strip() or None
    radiodj_api_url = parametres.get('radiodj_api_url', 'http://192.168.1.39:7777')
    radiodj_api_pass = parametres.get('radiodj_api_pass', '')
    radiodj_event_id = int(parametres.get('radiodj_event_id', RADIODJ_EVENT_ID_DEFAUT))

    mode_label = "EXECUTION RÉELLE" if execute else "DRY-RUN (simulation)"
    log(f"=== IMPORT DE MASSE - {mode_label} ===")
    log(f"Source (saisie)   : {source_raw}")

    # ── Conversion du chemin source Windows -> Linux ──
    if not source_raw:
        log("ERREUR : paramètre 'source' manquant.")
        raise RuntimeError("paramètre 'source' manquant")

    # Détection automatique : si le chemin commence par /, c'est déjà un chemin Linux
    if source_raw.startswith('/'):
        src_dir = Path(source_raw)
    elif _w2l:
        try:
            src_dir = _w2l(source_raw)
        except ValueError as e:
            log(f"ERREUR conversion chemin : {e}")
            raise RuntimeError(f"Conversion chemin source impossible : {e}")
    else:
        src_dir = Path(source_raw)

    if not src_dir.is_dir():
        log(f"ERREUR : dossier source introuvable : {src_dir}")
        raise RuntimeError(f"Dossier source introuvable : {src_dir}")

    log(f"Source (Linux)   : {src_dir}")

    # ── Détermination du dossier de sync ──
    sync_dir = _dossier_sync_courant(sync_folder_force)
    log(f"Dossier sync     : {sync_dir}")

    # ── Scan MP3 ──
    if recursive:
        mp3s = sorted(src_dir.rglob("*.mp3")) + sorted(src_dir.rglob("*.MP3"))
    else:
        mp3s = sorted(src_dir.glob("*.mp3")) + sorted(src_dir.glob("*.MP3"))
    mp3s = list(dict.fromkeys(mp3s))  # déduplication

    if not mp3s:
        log(f"Aucun fichier MP3 dans : {src_dir}")
        log("=== IMPORT DE MASSE TERMINE (aucun fichier) ===")
        return

    log(f"{len(mp3s)} fichier(s) MP3 trouvé(s)")

    # ════════════════════════════════════════════════════════════
    # ETAPE 1 : DEDOUBLONNAGE
    # ════════════════════════════════════════════════════════════
    log("--- ETAPE 1 : DEDOUBLONNAGE ---")
    log("Connexion radiodb (192.168.1.30)...")

    try:
        db = pymysql.connect(
            **RADIODB_CONFIG, cursorclass=pymysql.cursors.DictCursor
        )
        cursor = db.cursor()
        log("Connexion MySQL établie (radiodb)")
    except Exception as e:
        log(f"ERREUR connexion radiodb : {e}")
        raise RuntimeError(f"Connexion radiodb échouée : {e}")

    doublons_filename = []
    doublons_tags = []
    suspects = []
    nouveaux = []

    for fichier in mp3s:
        try:
            trouve, methode, detail, song_id, bdd_row = _verifier_en_base(cursor, fichier)
        except Exception as e:
            log(f"  Erreur scan {fichier.name} : {e}")
            continue

        if not trouve:
            log(f"  [NOUVEAU] {fichier.name}")
            nouveaux.append({'path': fichier, 'nom': fichier.name})
            continue

        # Lire les infos du fichier source pour comparaison
        src_meta = _lire_meta(fichier)
        try:
            src_size = fichier.stat().st_size
        except OSError:
            src_size = 0
        src_taille = _format_taille(src_size)
        src_duree = _format_duree(src_meta.get('duration', 0))

        # Infos BDD
        bdd_duree = _format_duree(bdd_row.get('duration', 0)) if bdd_row else 'N/A'
        bdd_path = bdd_row['path'] if bdd_row else ''

        if methode == "filename":
            # Haute confiance : même nom de fichier
            label = "DOUBLON/FILENAME"
            log(f"  [{label}] {fichier.name}")
            log(f"    Source : {src_meta['artist']} - {src_meta['title']} | {src_taille} | {src_duree}")
            log(f"    BDD    : ID:{song_id} {bdd_path}")
            entry = {
                'path': fichier, 'nom': fichier.name,
                'detail': detail, 'song_id': song_id,
            }
            doublons_filename.append(entry)
        else:
            # Tags match (exact, principal, ou nu) — pourrait être un remaster,
            # version live, ou simplement une variante de notation d'artiste.
            bdd_artist = bdd_row['artist'] if bdd_row else ''
            bdd_title = bdd_row['title'] if bdd_row else ''
            methode_label = methode.upper().replace('TAGS_', 'TAGS/')
            log(f"  [SUSPECT/{methode_label}] {fichier.name}")
            log(f"    Source : {src_meta['artist']} - {src_meta['title']} | {src_taille} | {src_duree}")
            log(f"    BDD    : {bdd_artist} - {bdd_title} | ID:{song_id} | {bdd_path} | {bdd_duree}")
            # Détecter les termes de version dans le titre du fichier
            version_terms = _detecter_termes_version(src_meta['title'])
            entry = {
                'path': fichier, 'nom': fichier.name,
                'detail': detail, 'song_id': song_id,
                'methode': methode,
                'src_meta': src_meta,
                'bdd_row': {
                    'id': bdd_row['id'],
                    'artist': bdd_row['artist'],
                    'title': bdd_row['title'],
                    'path': bdd_row['path'],
                    'duration': bdd_row.get('duration', 0),
                    'album': bdd_row.get('album', ''),
                    'year': bdd_row.get('year', ''),
                } if bdd_row else None,
                'version_terms': version_terms,
            }

            # ── E1-bis : verifier si une decision humaine existe deja ──
            decision_precedente = _chercher_decision_precedente(
                src_meta['artist'], src_meta['title']
            )
            if decision_precedente:
                decision = decision_precedente['decision']
                date_decision = str(decision_precedente['validated_at'])
                if decision == 'valide':
                    log(f"  [AUTO-VALIDE] {fichier.name} "
                        f"(decision du {date_decision})")
                    log(f"    -> traite comme NOUVEAU (importe automatiquement)")
                    nouveaux.append({'path': fichier, 'nom': fichier.name})
                elif decision == 'rejete':
                    log(f"  [AUTO-REJETE] {fichier.name} "
                        f"(decision du {date_decision})")
                    log(f"    -> ignore (deplace vers doublons)")
                    doublons_filename.append(entry)
                continue

            suspects.append(entry)

    cursor.close()
    db.close()

    nb_doublons = len(doublons_filename)
    nb_suspects = len(suspects)
    nb_nouveaux = len(nouveaux)
    log(
        f"Dédoublonnage : {nb_nouveaux} nouveau(x), {nb_doublons} doublon(s), "
        f"{nb_suspects} suspect(s)"
    )

    # Déplacer les doublons (même s'il n'y a aucun nouveau)
    if nb_doublons > 0 and execute:
        dir_doublons = src_dir / "doublons"
        log(f"Déplacement de {nb_doublons} doublon(s) vers {dir_doublons}...")
        for d in doublons_filename:
            try:
                _deplacer(d['path'], dir_doublons)
            except Exception as e:
                log(f"  Erreur déplacement {d['nom']} : {e}")
    elif nb_doublons > 0:
        log(
            f"[DRY-RUN] {nb_doublons} doublon(s) seraient déplacés "
            f"vers {src_dir / 'doublons'}"
        )

    # Déplacer les suspects vers un dossier séparé
    if nb_suspects > 0 and execute:
        dir_suspects = src_dir / "suspects"
        log(f"Déplacement de {nb_suspects} suspect(s) vers {dir_suspects}...")
        log("  (À vérifier manuellement : possibles remasters, versions live, etc.)")
        for s in suspects:
            try:
                dst = _deplacer(s['path'], dir_suspects)
                # Écrire un sidecar .match.json avec les infos du match
                # pour que le dashboard puisse afficher le match et la comparaison audio
                if s.get('bdd_row') or s.get('version_terms'):
                    sidecar = {
                        'methode': s.get('methode', ''),
                        'detail': s.get('detail', ''),
                        'version_terms': s.get('version_terms', []),
                        'src_meta': {
                            'artist': s['src_meta']['artist'],
                            'title': s['src_meta']['title'],
                            'duration': s['src_meta'].get('duration', 0),
                        } if s.get('src_meta') else None,
                        'bdd_row': s.get('bdd_row'),
                        'timestamp': datetime.now().isoformat(),
                    }
                    sidecar_path = dst.with_suffix(dst.suffix + '.match.json')
                    try:
                        with open(sidecar_path, 'w', encoding='utf-8') as sf:
                            json.dump(sidecar, sf, ensure_ascii=False, indent=2)
                    except Exception as e_sidecar:
                        log(f"  Avertissement: sidecar non écrit pour {dst.name}: {e_sidecar}")
            except Exception as e:
                log(f"  Erreur déplacement {s['nom']} : {e}")
    elif nb_suspects > 0:
        log(
            f"[DRY-RUN] {nb_suspects} suspect(s) seraient déplacés "
            f"vers {src_dir / 'suspects'}"
        )

    if nb_nouveaux == 0:
        log("Aucune nouveauté à traiter. Pipeline terminé.")
        log("=== IMPORT DE MASSE TERMINE (que des doublons) ===")
        return

    # ════════════════════════════════════════════════════════════
    # ETAPE 2 : MP3GAIN (89 dB)
    # ════════════════════════════════════════════════════════════
    if not execute:
        log(f"[DRY-RUN] {nb_nouveaux} fichier(s) seraient traités avec execute=true")
        log("=== IMPORT DE MASSE TERMINE (dry-run) ===")
        return

    etape2 = {'succes': [], 'erreurs': []}
    if skip_mp3gain:
        log("--- ETAPE 2 : MP3GAIN (SAUTÉE) ---")
        etape2['succes'] = [n['path'] for n in nouveaux]
    else:
        log("--- ETAPE 2 : MP3GAIN (89 dB) ---")
        fichiers_a_normaliser = [n['path'] for n in nouveaux]
        etape2 = _run_mp3gain(fichiers_a_normaliser, log)

    log(f"mp3gain : {len(etape2['succes'])} OK, {len(etape2['erreurs'])} erreurs")

    if not etape2['succes']:
        log("Aucun fichier normalisé. Abandon avant copie.")
        raise RuntimeError("Aucun fichier normalisé par mp3gain")

    # ════════════════════════════════════════════════════════════
    # ETAPE 3 : DEPLACEMENT VERS DOSSIER DE SYNCHRONISATION
    # ════════════════════════════════════════════════════════════
    log("--- ETAPE 3 : DEPLACEMENT VERS DOSSIER SYNC ---")
    log(f"Destination : {sync_dir}")

    # Vérifier l'accès en écriture
    try:
        test_file = sync_dir / ".worker_import_test"
        test_file.write_text("test")
        test_file.unlink()
    except Exception as e:
        log(f"ERREUR : dossier de sync non accessible en écriture : {e}")
        raise RuntimeError(f"Dossier de sync non accessible en écriture : {e}")

    nb_deplaces = 0
    nb_erreurs_depl = 0
    for fpath in etape2['succes']:
        p = Path(fpath)
        if not p.exists():
            log(f"  Fichier absent après mp3gain : {p.name}")
            nb_erreurs_depl += 1
            continue
        try:
            meta = _lire_meta(p)
            dest = _deplacer(p, sync_dir)
            nb_deplaces += 1
            log(f"  Déplacé : {meta['artist']} - {meta['title']} -> {dest.name}")
        except Exception as e:
            nb_erreurs_depl += 1
            log(f"  Erreur déplacement {p.name} : {e}")

    log(f"Déplacement dossier sync : {nb_deplaces} déplacés, {nb_erreurs_depl} erreurs")

    # ════════════════════════════════════════════════════════════
    # ETAPE 4 : DECLNCHEMENT SYNC RADIODJ
    # ════════════════════════════════════════════════════════════
    log("--- ETAPE 4 : SYNC RADIODJ (REST Server v4) ---")
    etape4 = _declencher_sync_radiodj(
        radiodj_api_url, radiodj_api_pass, radiodj_event_id, log
    )

    # ════════════════════════════════════════════════════════════
    # RAPPORT FINAL
    # ════════════════════════════════════════════════════════════
    log("")
    log("========================================")
    log("  RAPPORT FINAL")
    log(f"  Scannés      : {len(mp3s)}")
    log(f"  Doublons     : {nb_doublons}")
    log(f"  Suspects     : {nb_suspects}")
    log(f"  Normalisés   : {len(etape2['succes'])}")
    log(f"  Déplacés sync : {nb_deplaces}")
    log(f"  Sync RadioDJ : {'OK' if etape4['ok'] else 'non déclenchée'}")
    log(f"  Dossier sync : {sync_dir}")
    log("========================================")
    log("=== IMPORT DE MASSE TERMINE ===")
