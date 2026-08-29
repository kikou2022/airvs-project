"""blueprints/pipeline_import.py -- Routes pipeline import de masse.

Permet de lancer le pipeline dedoublonnage -> mp3gain -> dossier sync RadioDJ
depuis le dashboard AIRVS.

Architecture v3 (aout 2026) :
  Dashboard (Windows .39) -> INSERT DB -> Worker Ubuntu Studio (.17)
  Le worker traite la tache de bout en bout (dedoublonnage, mp3gain,
  copie FolderSync, declenchement REST Server).
  Le dashboard suit l'avancement via le log de la tache (comme toute tache worker).

Plus de SSH, plus de nohup, plus de polling fichier distant.
Le dashboard ne fait qu'un INSERT dans taches_planifiees.

Routes :
  - POST /api/pipeline/launch       Lance le pipeline (INSERT en DB)
  - GET  /api/pipeline/status/<id>  Etat d'une tache pipeline
  - POST /api/pipeline/sync-radiodj  Declenche manuellement la sync RadioDJ
  - GET  /api/pipeline/browse        Liste les sous-dossiers d'un chemin

Routes validation suspects (E1) :
  - GET  /api/suspects              Liste les fichiers suspects + match DB
  - GET  /api/suspects/play/<fn>    Stream un suspect
  - GET  /api/suspects/play-existing/<id>  Stream le fichier existant en DB
  - POST /api/suspects/validate     Valide un suspect vers FolderSync
  - POST /api/suspects/reject       Rejete (supprime) un suspect

Routes manquants (airvs_manquants) :
  - GET    /api/manquants/liste     Liste avec filtres/pagination
  - GET    /api/manquants/stats     Compteurs par source/statut
  - POST   /api/manquants/resolve   Marque resolu (faux positif)
  - DELETE /api/manquants/delete    Supprime un manquant
"""

import os
import re
import json
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import pymysql
from flask import Blueprint, request, jsonify, send_file

from utils import _logger, get_db_connection
from blueprints.auth import login_requis
from config import (
    SUSPECTS_DIR, FOLDERSYNC_WINDOWS_BASE, MP3GAIN_PATH,
    DEBIAN_3_IP, DEBIAN_3_SSH_USER, _SUBPROCESS_CREATIONFLAGS,
)


pipeline_import_bp = Blueprint('pipeline_import_bp', __name__)

# Configuration REST Server v4 de RadioDJ
# Plugin REST Server v4.1.0.2 - RadioDJ 2.0.5.1
# Format valide decouvert (aout 2026) :
#   http://<IP>:<PORT>/opt?auth=<PASS>&command=RunEvent&arg=<EVENT_ID>
#
# Le bouton manuel « Sync RadioDJ » est appelé par le dashboard Windows
#   -> localhost car meme machine que RadioDJ
# Le pipeline est exécuté par le worker Ubuntu
#   -> il faut l'IP Windows du poste RadioDJ

# Bouton manuel Sync RadioDJ (appelé depuis le dashboard = meme machine)
RADIODJ_API_HOST = os.getenv('RADIODJ_API_HOST', '127.0.0.1')
RADIODJ_API_PORT = os.getenv('RADIODJ_API_PORT', '7777')
RADIODJ_API_PASS = os.getenv('RADIODJ_API_PASS', '')

# Pipeline (appelé par le worker Ubuntu depuis une autre machine)
RADIODJ_WORKER_HOST = os.getenv('RADIODJ_WORKER_HOST', '192.168.1.39')
RADIODJ_WORKER_PORT = os.getenv('RADIODJ_WORKER_PORT', RADIODJ_API_PORT)

# ID numerique de l'evenement RadioDJ qui declenche FolderSync.
# Pour trouver l'ID : SELECT id, name FROM events WHERE name='SyncFolderSync';
RADIODJ_EVENT_ID = int(os.getenv('RADIODJ_EVENT_ID', '182'))


@pipeline_import_bp.route('/api/pipeline/launch', methods=['POST'])
@login_requis
def api_pipeline_launch():
    """Lance le pipeline d'import de masse.

    Insert une tache IMPORT_MASSE dans taches_planifiees.
    Le worker Ubuntu Studio la capte automatiquement et traite le pipeline.

    Parametres JSON :
      - source (str, obligatoire) : dossier source MP3
          Chemin Linux : "/mnt/stockage_160go/A_trier"
          Chemin Windows : "U:\\A_trier" (converti auto par le worker)
      - execute (bool) : execution reelle (defaut: false = dry-run)
      - recursive (bool) : scan recursif (defaut: false)
      - skip_mp3gain (bool) : sauter mp3gain (defaut: false)
      - sync_folder (str, optionnel) : forcer le dossier de sync
      - radiodj_api_url (str) : URL REST Server (defaut: http://192.168.1.39:7777)
      - radiodj_api_pass (str) : mot de passe REST Server v4
    """
    data = request.json or {}
    source = data.get('source', '').strip()

    if not source:
        return jsonify({"error": "'source' est obligatoire"}), 400

    # Construction des parametres pour le worker
    parametres = {
        'source': source,
        'execute': bool(data.get('execute', False)),
        'recursive': bool(data.get('recursive', False)),
        'skip_mp3gain': bool(data.get('skip_mp3gain', False)),
        'sync_folder': data.get('sync_folder', ''),
        'radiodj_api_url': data.get(
            'radiodj_api_url',
            f'http://{RADIODJ_WORKER_HOST}:{RADIODJ_WORKER_PORT}'
        ),
        'radiodj_api_pass': data.get('radiodj_api_pass', '') or RADIODJ_API_PASS,
        'radiodj_event_id': data.get('radiodj_event_id', '') or str(RADIODJ_EVENT_ID),
    }

    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO taches_planifiees "
            "(type_action, statut, parametres, date_creation) "
            "VALUES (%s, 'en_attente', %s, NOW())",
            ('IMPORT_MASSE', json.dumps(parametres, ensure_ascii=False))
        )
        tache_id = cursor.lastrowid
        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        _logger.error(f'Pipeline : erreur INSERT tache : {e}')
        return jsonify({"error": f"Erreur creation tache : {e}"}), 500

    mode = "execution reelle" if parametres['execute'] else "dry-run"
    _logger.info(
        f'Pipeline : tache IMPORT_MASSE #{tache_id} creee '
        f'(source={source}, mode={mode})'
    )

    return jsonify({
        "status": "en_attente",
        "tache_id": tache_id,
        "mode": mode,
        "source": source,
        "message": (
            f"Tache #{tache_id} en file. "
            f"Le worker Ubuntu Studio la traitera automatiquement."
        ),
    })


@pipeline_import_bp.route('/api/pipeline/status/<int:tache_id>')
@login_requis
def api_pipeline_status(tache_id):
    """Retourne l'etat d'une tache pipeline.

    Lit le log_resultat et le statut depuis taches_planifiees.
    Meme mecanisme que toutes les autres taches du worker.
    """
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT id, type_action, statut, date_creation, date_fin, "
            "parametres, log_resultat "
            "FROM taches_planifiees WHERE id = %s",
            (tache_id,)
        )
        tache = cursor.fetchone()
        cursor.close()
        db.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not tache:
        return jsonify({"error": f"Tache {tache_id} non trouvee"}), 404

    if tache['type_action'] != 'IMPORT_MASSE':
        return jsonify({
            "error": f"Tache {tache_id} n'est pas un import de masse "
                     f"(type: {tache['type_action']})"
        }), 400

    return jsonify({
        "tache_id": tache['id'],
        "statut": tache['statut'],
        "date_creation": tache['date_creation'].isoformat() if tache['date_creation'] else None,
        "date_fin": tache['date_fin'].isoformat() if tache['date_fin'] else None,
        "log": tache['log_resultat'] or '',
    })


import platform

# Racines autorisees pour le navigateur de dossiers.
# Adaptation automatique Windows / Linux.
# Sur Windows on utilise des chemins avec / (Python les gère correctement)
if platform.system() == 'Windows':
    BROWSE_ROOTS = ['U:/', 'M:/', 'L:/', 'D:/', 'E:/', 'N:/', 'Z:/']
    BROWSE_DEFAULT = 'U:/'
else:
    BROWSE_ROOTS = ['/mnt', '/home', '/media']
    BROWSE_DEFAULT = '/mnt'

# Dossiers a masquer (caches, systeme)
BROWSE_HIDDEN = {
    'lost+found', '.Trash-0', '.Trash-1',
    '$RECYCLE.BIN', 'System Volume Information',
    'Recovery', 'Windows', 'Program Files',
    'Program Files (x86)', 'ProgramData', 'AppData',
}


def _is_under_root(path_str):
    """Verifie que path_str est sous une racine autorisee."""
    p = path_str.replace(chr(92), '/').upper()  # normalise \ -> /
    for root in BROWSE_ROOTS:
        r = root.upper()
        # r finit deja par '/' sur Windows (U:/) donc pas besoin d'en rajouter
        sep = '' if r.endswith('/') else '/'
        if p == r or p.startswith(r + sep):
            return True
    return False


def _parent_path(path_str):
    """Retourne le chemin parent, ou None si racine."""
    p = Path(path_str)
    if str(p) == p.anchor or str(p.parent) == str(p):
        return None
    return str(p.parent)


@pipeline_import_bp.route('/api/pipeline/browse')
@login_requis
def api_pipeline_browse():
    """Liste les sous-dossiers d'un chemin donne.

    Adaptatif Windows / Linux : detecte l'OS du dashboard.
    Sur Windows : navigue les lecteurs reseau (U:/, M:/, etc.)
    Sur Linux : navigue /mnt, /home, /media

    Query params :
      - path (str) : chemin a lister (defaut: BROWSE_DEFAULT)

    Retourne JSON :
      - current : chemin courant
      - parent  : chemin parent (null si a la racine)
      - dirs    : liste des sous-dossiers [{name, path, has_subdirs, subdir_count, mtime}]
    """
    raw = request.args.get('path', '').strip()

    # --- Mode racines : path vide ou 'roots' -> liste les racines autorisees ---
    if not raw or raw == 'roots':
        dirs = []
        for root in BROWSE_ROOTS:
            rp = Path(root)
            if rp.is_dir():
                has_sub = False
                subdir_count = 0
                mtime = None
                try:
                    visible = [e for e in rp.iterdir()
                               if e.name not in BROWSE_HIDDEN and not e.name.startswith('.')]
                    has_sub = any(e.is_dir() for e in visible)
                    subdir_count = sum(1 for e in visible if e.is_dir())
                    all_times = [e.stat().st_mtime for e in visible]
                    if all_times:
                        mtime = datetime.fromtimestamp(max(all_times)).strftime('%d/%m/%Y %H:%M')
                except (PermissionError, OSError):
                    pass
                dirs.append({
                    'name': root,
                    'path': root,
                    'has_subdirs': has_sub,
                    'subdir_count': subdir_count,
                    'mtime': mtime,
                })
        return jsonify({
            'current': '[ Racines ]',
            'parent': None,
            'dirs': dirs,
        })

    target = Path(raw)

    # Securite : le chemin doit etre sous une racine autorisee
    if not _is_under_root(raw):
        return jsonify({
            'error': 'Chemin non autorise.',
            'allowed': BROWSE_ROOTS,
        }), 403

    if not target.is_dir():
        return jsonify({'error': 'Dossier introuvable : ' + raw}), 404

    try:
        entries = sorted(target.iterdir())
    except PermissionError:
        return jsonify({'error': 'Permission refusee'}), 403
    except OSError as e:
        return jsonify({'error': str(e)}), 403

    dirs = []
    for entry in entries:
        if not entry.is_dir():
            continue
        name = entry.name
        if name in BROWSE_HIDDEN:
            continue
        if name.startswith('.'):
            continue
        try:
            visible = [e for e in entry.iterdir()
                       if e.name not in BROWSE_HIDDEN and not e.name.startswith('.')]
            has_sub = any(e.is_dir() for e in visible)
            subdir_count = sum(1 for e in visible if e.is_dir())
            all_times = [e.stat().st_mtime for e in visible]
            mtime = datetime.fromtimestamp(max(all_times)).strftime('%d/%m/%Y %H:%M') if all_times else None
        except (PermissionError, OSError):
            has_sub = False
            subdir_count = 0
            mtime = None
        dirs.append({
            'name': name,
            'path': str(entry),
            'has_subdirs': has_sub,
            'subdir_count': subdir_count,
            'mtime': mtime,
        })

    parent = _parent_path(raw)

    return jsonify({
        'current': str(target),
        'parent': parent,
        'dirs': dirs,
    })




@pipeline_import_bp.route('/api/pipeline/test-mp3gain', methods=['GET'])
@login_requis
def api_pipeline_test_mp3gain():
    """Diagnostic mp3gain : verifie que le binaire est accessible et fonctionnel.

    Retourne le chemin, la version, et le resultat d'un test a vide.
    """
    # Verifier le chemin
    result = {'path': MP3GAIN_PATH, 'exists': os.path.exists(MP3GAIN_PATH) if os.path.isabs(MP3GAIN_PATH) else None}
    try:
        proc = subprocess.run(
            [MP3GAIN_PATH, '-v'],
            capture_output=True, text=True, timeout=10
        )
        result['version'] = (proc.stdout or proc.stderr or '').strip()[:200]
        result['returncode'] = proc.returncode
        result['ok'] = True
    except FileNotFoundError:
        result['ok'] = False
        result['error'] = f"Binaire introuvable : {MP3GAIN_PATH}"
    except Exception as e:
        result['ok'] = False
        result['error'] = str(e)
    return jsonify(result)


@pipeline_import_bp.route('/api/pipeline/sync-radiodj', methods=['POST'])
@login_requis
def api_pipeline_sync_radiodj():
    """Declenche manuellement la synchronisation RadioDJ.

    Bouton de secours dans le dashboard si la sync automatique echoue.
    Appel HTTP localhost vers le REST Server v4 de RadioDJ sur la meme machine.

    Format REST Server v4 : http://<IP>:<PORT>/opt?auth=<PASS>&command=RunEvent&arg=<ID>
    """
    if not RADIODJ_API_PASS:
        return jsonify({
            'ok': False,
            'message': (
                'RADIODJ_API_PASS non defini dans .env. '
                "Ajoutez RADIODJ_API_PASS=votre_mot_de_passe dans le .env."
            ),
        }), 502

    base = f'http://{RADIODJ_API_HOST}:{RADIODJ_API_PORT}'
    pass_q = quote(RADIODJ_API_PASS, safe='')
    url = f'{base}/opt?auth={pass_q}&command=RunEvent&arg={RADIODJ_EVENT_ID}'

    try:
        req = Request(url, method="GET")
        req.add_header('User-Agent', 'AIRVS-Dashboard/3.0')
        with urlopen(req, timeout=5) as resp:
            body = resp.read().decode('utf-8', errors='replace')[:500]
            m = re.search(r'<string[^>]*>(\d+)</string>', body.strip())
            if m:
                code_body = int(m.group(1))
                if code_body == 200:
                    return jsonify({
                        'ok': True,
                        'message': (
                            f"REST Server v4 : RunEvent arg={RADIODJ_EVENT_ID} "
                            f"execute avec succes -> FolderSync declenche."
                        ),
                        'url': url,
                    })
                else:
                    return jsonify({
                        'ok': False,
                        'message': (
                            f"REST Server v4 : argument invalide (body {code_body}). "
                            f"Verifiez RADIODJ_EVENT_ID={RADIODJ_EVENT_ID} dans .env."
                        ),
                        'url': url,
                    }), 502
            if 200 <= resp.status < 300:
                return jsonify({
                    'ok': True,
                    'message': 'REST Server v4 : reponse positive (format inattendu).',
                    'url': url,
                })
            return jsonify({
                'ok': False,
                'message': f"REST Server v4 : HTTP {resp.status} inattendu.",
                'url': url,
            }), 502
    except HTTPError as e:
        return jsonify({'ok': False, 'message': f'HTTP {e.code} : {e.reason}'}), 502
    except URLError as e:
        return jsonify({'ok': False, 'message': f'Connexion echouee : {e.reason}'}), 502
    except Exception as e:
        return jsonify({'ok': False, 'message': f'Erreur : {e}'}), 502


# ═══════════════════════════════════════════════════════════════════════
# VALIDATION DES SUSPECTS (E1)
# ═══════════════════════════════════════════════════════════════════════

# mutagen pour lire les tags ID3 des suspects
TAGS_FIELDS = [
    ("TPE1", "artist"),
    ("TIT2", "title"),
    ("TALB", "album"),
    ("TCON", "genre"),
    ("TPE2", "album_artist"),
    ("TCOM", "composer"),
    ("TPUB", "publisher"),
    ("TRCK", "track"),
]
YEAR_FRAMES = ("TDRC", "TYER")


def _lire_tags_suspect(filepath):
    """Lit les tags ID3 d\'un suspect et retourne un dict.

    Retourne : { artist, title, album, year, genre, album_artist,
                 composer, publisher, track }
    """
    default = {"artist": "Inconnu", "title": Path(filepath).stem,
               "album": "", "year": "", "genre": "",
               "album_artist": "", "composer": "", "publisher": "", "track": ""}
    try:
        from mutagen.id3 import ID3
        tags = ID3(filepath)
        result = dict(default)
        for frame_id, key in TAGS_FIELDS:
            val = str(tags.get(frame_id, "")).strip()
            if val:
                result[key] = val
        # Annee : TDRC (v2.4) ou TYER (v2.3)
        for yf in YEAR_FRAMES:
            yf_val = tags.get(yf)
            if yf_val:
                result["year"] = str(yf_val).strip()[:4]
                break
        return result
    except Exception:
        return dict(default)


def _run_mp3gain_windows(filepath):
    """Normalise un fichier MP3 a 89 dB via mp3gain local (Windows)."""
    try:
        proc = subprocess.run(
            [MP3GAIN_PATH, '-r', '-c', '-k', str(filepath)],
            capture_output=True, text=True, timeout=120
        )
        if proc.returncode in (0, 2):
            dB_info = ''
            for line in (proc.stdout or '').splitlines():
                if 'dB' in line:
                    dB_info = line.strip()
                    break
            _logger.info(f'mp3gain OK : {os.path.basename(filepath)} -> {dB_info}')
            return {'ok': True, 'message': dB_info or 'Normalise'}
        else:
            err = (proc.stderr or proc.stdout or '').strip()[:200]
            _logger.warning(f'mp3gain retour {proc.returncode} : {err}')
            return {'ok': False, 'message': f'Retour {proc.returncode}: {err}'}
    except FileNotFoundError:
        _logger.error('mp3gain non trouve sur le systeme')
        return {'ok': False, 'message': 'mp3gain non trouve'}
    except subprocess.TimeoutExpired:
        _logger.warning(f'mp3gain timeout : {filepath}')
        return {'ok': False, 'message': 'Timeout mp3gain'}
    except Exception as e:
        _logger.error(f'mp3gain erreur : {e}')
        return {'ok': False, 'message': str(e)}


def _nettoyer_dossier_suspects_si_vide():
    """Supprime les sidecars .match.json orphelins puis le dossier suspects s'il est vide.

    os.rmdir echoue silencieusement si le dossier n'est pas vide ou n'existe pas,
    ce qui rend cette operation sans risque.
    """
    try:
        # Nettoyer les sidecars orphelins (sans le .mp3 correspondant)
        for sf in Path(SUSPECTS_DIR).glob('*.match.json'):
            mp3_name = sf.name.replace('.match.json', '')
            if not (sf.parent / mp3_name).exists():
                try:
                    sf.unlink()
                except Exception:
                    pass
        os.rmdir(SUSPECTS_DIR)
        _logger.info(f'Dossier suspects supprime (vide) : {SUSPECTS_DIR}')
    except OSError:
        pass  # dossier non vide, inexistant, ou autre erreur -> ignorer


def _dossier_foldersync_courant():
    """Retourne le dossier FolderSync de la semaine courante sur E:\\.

    Periode editoriale :
      Septembre a juin  : NOUVELLES_ENTREES/SEMAINE_XX/
      Juillet-aout       : NOUVELLES_ENTREES_ETE/
    """
    base = Path(FOLDERSYNC_WINDOWS_BASE)
    mois = datetime.now().month
    semaine = datetime.now().isocalendar()[1]

    if mois in (7, 8):
        base = base / "NOUVELLES_ENTREES_ETE"
    else:
        base = base / "NOUVELLES_ENTREES"

    dossier = base / f"SEMAINE_{semaine}"
    dossier.mkdir(parents=True, exist_ok=True)
    return dossier


def _chercher_match_db(artist, title):
    """Cherche une entrée correspondante dans radiodb.songs.

    Recherche par tags exacts (artist + title, case-insensitive via collation).
    Retourne un dict de la ligne BDD ou None.
    """
    if not artist or artist == "Inconnu" or not title:
        return None
    try:
        # get_db_connection() se connecte a airvs_dashboard.
        # La table songs est dans radiodb. On utilise le nom qualifie.
        db = get_db_connection()
        if not db:
            return None
        cursor = db.cursor()
        cursor.execute(
            "SELECT id, artist, title, album, year, path, duration "
            "FROM radiodb.songs "
            "WHERE artist = %s AND title = %s "
            "LIMIT 5",
            (artist, title)
        )
        rows = cursor.fetchall()
        cursor.close()
        db.close()
        return rows if rows else None
    except Exception as e:
        _logger.error(f'Suspects match DB erreur : {e}')
        return None




def _lire_sidecar_suspect(filepath):
    """Lit le sidecar .match.json adjacent a un fichier suspect.

    Retourne le dict JSON ou None.
    """
    sidecar_path = Path(filepath + '.match.json')
    if not sidecar_path.is_file():
        return None
    try:
        with open(sidecar_path, 'r', encoding='utf-8') as sf:
            return json.load(sf)
    except Exception:
        return None


def _enregistrer_decision_suspect(filename, decision, sidecar=None, tags=None):
    """Enregistre une decision humaine (valide/rejete) dans suspects_validated.

    Cette table permet au worker de ne pas re-detecter un fichier
    deja traite lors d'un import futur.

    Parameters :
      filename : nom du fichier suspect
      decision : 'valide' ou 'rejete'
      sidecar  : dict du .match.json (optionnel)
      tags     : dict des tags ID3 {artist, title, ...} (optionnel, lu du fichier si absent)
    """
    try:
        # Lire les tags si non fournis
        if not tags:
            filepath = os.path.join(SUSPECTS_DIR, filename)
            if os.path.exists(filepath):
                tags = _lire_tags_suspect(filepath)
            else:
                tags = {}

        artist = tags.get('artist', '')
        title = tags.get('title', '')
        methode = (sidecar or {}).get('methode', '')
        bdd_row = (sidecar or {}).get('bdd_row') or {}
        version_terms = (sidecar or {}).get('version_terms', [])

        db = get_db_connection()
        if not db:
            _logger.warning('suspects_validated : connexion DB echouee')
            return
        try:
            cursor = db.cursor()
            cursor.execute(
                "INSERT INTO suspects_validated "
                "(filename, artist, title, methode_detection, "
                "matched_song_id, matched_artist, matched_title, version_terms, "
                "decision, validated_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    filename,
                    artist,
                    title,
                    methode,
                    bdd_row.get('id'),
                    bdd_row.get('artist'),
                    bdd_row.get('title'),
                    json.dumps(version_terms, ensure_ascii=False) if version_terms else None,
                    decision,
                    'dashboard',
                )
            )
            db.commit()
            cursor.close()
            _logger.info(f'suspects_validated : {decision} pour {filename}')
        except Exception as e:
            _logger.error(f'suspects_validated erreur INSERT : {e}')
        finally:
            db.close()
    except Exception as e:
        _logger.error(f'suspects_validated erreur : {e}')

@pipeline_import_bp.route('/api/suspects')
@login_requis
def api_suspects_list():
    """Liste les fichiers suspects avec leurs tags et le match DB.

    Query params :
      - dir (str) : chemin du dossier suspects (defaut: SUSPECTS_DIR de config)

    Retourne : { suspects: [...], count: int }
    Chaque suspect contient :
      - filename, size, filepath
      - suspect_artist, suspect_title (tags ID3)
      - matches : [{id, artist, title, album, year, path, duration, song_id}] (matchs DB)
    """
    suspects_dir = request.args.get('dir', '').strip() or SUSPECTS_DIR
    sp = Path(suspects_dir)

    if not sp.is_dir():
        return jsonify({"suspects": [], "count": 0})

    mp3_files = sorted(
        [f for f in sp.iterdir() if f.suffix.lower() == '.mp3' and f.is_file()],
        key=lambda f: f.name.lower()
    )

    suspects = []
    for f in mp3_files:
        tags = _lire_tags_suspect(str(f))
        artist = tags["artist"]
        title = tags["title"]
        try:
            size = f.stat().st_size
        except OSError:
            size = 0

        # Lire le sidecar .match.json (écrit par le worker lors de l'import)
        sidecar = None
        sidecar_path = Path(str(f) + '.match.json')
        if sidecar_path.is_file():
            try:
                with open(sidecar_path, 'r', encoding='utf-8') as sf:
                    sidecar = json.load(sf)
            except Exception:
                pass

        # Chercher le match en DB (recherche exacte)
        matches = _chercher_match_db(artist, title)
        match_list = []
        if matches:
            for m in matches:
                match_list.append({
                    "song_id": m["id"],
                    "artist": m["artist"],
                    "title": m["title"],
                    "album": m.get("album", ""),
                    "year": m.get("year", ""),
                    "path": m["path"],
                    "duration": m.get("duration", 0),
                })
        elif sidecar and sidecar.get('bdd_row'):
            # Pas de match DB direct, mais le sidecar contient le match
            # trouvé par le worker (passe NU, principal, etc.)
            bdd = sidecar['bdd_row']
            match_list.append({
                "song_id": bdd["id"],
                "artist": bdd["artist"],
                "title": bdd["title"],
                "album": bdd.get("album", ""),
                "year": bdd.get("year", ""),
                "path": bdd["path"],
                "duration": bdd.get("duration", 0),
                "_from_sidecar": True,
            })

        suspects.append({
            "filename": f.name,
            "size": size,
            "size_human": f"{size / 1024 / 1024:.1f} Mo" if size else "?",
            "filepath": str(f),
            "suspect_artist": artist,
            "suspect_title": title,
            "suspect_album": tags["album"],
            "suspect_year": tags["year"],
            "suspect_genre": tags["genre"],
            "suspect_album_artist": tags["album_artist"],
            "suspect_composer": tags["composer"],
            "suspect_publisher": tags["publisher"],
            "suspect_track": tags["track"],
            "matches": match_list,
            "methode": sidecar.get('methode', '') if sidecar else '',
            "version_terms": sidecar.get('version_terms', []) if sidecar else [],
            "src_duration": (sidecar.get('src_meta') or {}).get('duration', 0) if sidecar else 0,
            "previous_decision": None,
        })

    # ── Enrichir avec les decisions precedentes (batch query) ──
    try:
        db = get_db_connection()
        if db:
            cursor = db.cursor()
            for s in suspects:
                a, t = s["suspect_artist"], s["suspect_title"]
                if not a or not t:
                    continue
                try:
                    cursor.execute(
                        "SELECT decision, validated_at "
                        "FROM suspects_validated "
                        "WHERE artist = %s AND title = %s "
                        "ORDER BY validated_at DESC LIMIT 1",
                        (a, t)
                    )
                    row = cursor.fetchone()
                    if row:
                        s["previous_decision"] = {
                            "decision": row["decision"],
                            "date": str(row["validated_at"])[:16],
                        }
                except Exception:
                    pass
            cursor.close()
            db.close()
    except Exception:
        pass

    return jsonify({"suspects": suspects, "count": len(suspects)})


@pipeline_import_bp.route('/api/suspects/play/<path:filename>')
@login_requis
def api_suspect_play(filename):
    """Stream un fichier suspect (lecture seule, U:\\ SMB)."""
    filepath = os.path.join(SUSPECTS_DIR, filename)
    # Securite : pas de traversal
    if not os.path.abspath(filepath).startswith(os.path.abspath(SUSPECTS_DIR)):
        return "Acces interdit", 403
    if not os.path.exists(filepath):
        return "Fichier suspect introuvable", 404
    return send_file(filepath, mimetype='audio/mpeg')


@pipeline_import_bp.route('/api/suspects/play-existing/<int:song_id>')
@login_requis
def api_suspect_play_existing(song_id):
    """Stream le fichier existant en DB (meme pattern que /ecouter/<id>)."""
    db = get_db_connection()
    if not db:
        return "Erreur DB", 500
    try:
        cursor = db.cursor()
        cursor.execute("SELECT `path` FROM radiodb.songs WHERE ID = %s", (song_id,))
        result = cursor.fetchone()
        cursor.close()
        db.close()
    except Exception:
        return "Erreur requete DB", 500

    if not result or not result["path"]:
        return "Fichier introuvable en base", 404

    filepath = result["path"].replace("/", "\\")
    if not os.path.exists(filepath):
        return "Fichier absent du disque dur", 404

    return send_file(filepath, mimetype='audio/mpeg')


@pipeline_import_bp.route('/api/suspects/validate', methods=['POST'])
@login_requis
def api_suspect_validate():
    """Valide un suspect : copie vers FolderSync E:\\ + supprime le suspect.

    Le dashboard fait directement :
      1. shutil.copy2 de U:\\ (SMB) vers E:\\ (local) = FolderSync
      2. os.remove du suspect sur U:\\ (SMB)
      3. Declenche la sync RadioDJ via REST Server v4

    Body JSON : { filename: str }
    """
    data = request.json or {}
    filename = data.get('filename', '').strip()
    if not filename:
        return jsonify({"error": "'filename' est obligatoire"}), 400

    # Securite : nom de fichier simple (pas de chemin)
    if '/' in filename or '\\' in filename or '..' in filename:
        return jsonify({"error": "Nom de fichier invalide"}), 400

    src = os.path.join(SUSPECTS_DIR, filename)
    if not os.path.exists(src):
        return jsonify({"error": f"Fichier suspect introuvable : {filename}"}), 404

    # Determiner le dossier FolderSync de la semaine
    try:
        sync_dir = _dossier_foldersync_courant()
    except Exception as e:
        return jsonify({"error": f"Erreur FolderSync : {e}"}), 500

    # Copier vers FolderSync (U:\ SMB -> E:\ local)
    dst = os.path.join(str(sync_dir), filename)
    # Gerer les collisions
    if os.path.exists(dst):
        stem, ext = os.path.splitext(filename)
        idx = 1
        while os.path.exists(dst):
            dst = os.path.join(str(sync_dir), f"{stem}_{idx}{ext}")
            idx += 1

    try:
        shutil.copy2(src, dst)
    except Exception as e:
        return jsonify({"error": f"Erreur copie vers FolderSync : {e}"}), 500

    # Normaliser le volume a 89 dB (SYNCHRONE — obligatoire avant sync RadioDJ)
    mp3gain_result = _run_mp3gain_windows(dst)
    if not mp3gain_result['ok']:
        # mp3gain a echoue : supprimer le fichier de FolderSync et signaler l'erreur
        _logger.error(f'mp3gain echoue pour suspect {filename} : {mp3gain_result["message"]}')
        try:
            os.remove(dst)
        except Exception:
            pass
        return jsonify({
            "error": f"mp3gain echoue : {mp3gain_result['message']}. "
                   f"Fichier non importe. Corrigez le fichier ou validez sans mp3gain si necessaire.",
            "mp3gain": mp3gain_result,
        }), 500

    # Lire le sidecar AVANT suppression du suspect
    sidecar_val = _lire_sidecar_suspect(src)

    # Supprimer le suspect
    try:
        os.remove(src)
    except Exception as e:
        # Le fichier est copie, on signale l'echec de suppression mais c'est OK
        _logger.warning(f"Suspect valide mais suppression echouee : {e}")
    # Supprimer le sidecar .match.json
    try:
        os.remove(src + '.match.json')
    except Exception:
        pass

    # Declencher la sync RadioDJ (APRES mp3gain termine avec succes)
    sync_result = None
    try:
        base = f'http://{RADIODJ_API_HOST}:{RADIODJ_API_PORT}'
        pass_q = quote(RADIODJ_API_PASS, safe='')
        url = f'{base}/opt?auth={pass_q}&command=RunEvent&arg={RADIODJ_EVENT_ID}'
        req = Request(url, method="GET")
        req.add_header('User-Agent', 'AIRVS-Dashboard/3.1')
        with urlopen(req, timeout=5) as resp:
            body = resp.read().decode('utf-8', errors='replace')[:500]
            m = re.search(r'<string[^>]*>(\d+)</string>', body.strip())
            sync_ok = m and int(m.group(1)) == 200
            sync_result = {
                "ok": sync_ok,
                "message": "FolderSync declenche" if sync_ok else "Echec sync"
            }
    except Exception as e:
        sync_result = {"ok": False, "message": str(e)}

    # Enregistrer la decision dans suspects_validated
    _enregistrer_decision_suspect(filename, 'valide', sidecar=sidecar_val)

    _logger.info(f'Suspect valide : {filename} -> {dst}')
    _nettoyer_dossier_suspects_si_vide()
    return jsonify({
        "ok": True,
        "filename": filename,
        "destination": str(sync_dir),
        "mp3gain": mp3gain_result,
        "sync_radiodj": sync_result,
    })


@pipeline_import_bp.route('/api/suspects/edit-validate', methods=['POST'])
@login_requis
def api_suspect_edit_validate():
    """Edite les tags ID3 d'un suspect puis valide (copie vers FolderSync).

    Body JSON : { filename, artist, title, album, year, genre,
                  album_artist, composer, publisher, track }
    Flow :
      1. Ecrire les nouveaux tags ID3 (mutagen)
      2. Copier vers FolderSync + supprimer suspect (comme validate)
      3. Declencher sync RadioDJ
    """
    data = request.json or {}
    filename = data.get('filename', '').strip()
    tag_values = {
        'artist':       data.get('artist', '').strip(),
        'title':        data.get('title', '').strip(),
        'album':        data.get('album', '').strip(),
        'year':         str(data.get('year', '')).strip()[:4] or '0',
        'genre':        data.get('genre', '').strip(),
        'album_artist': data.get('album_artist', '').strip(),
        'composer':     data.get('composer', '').strip(),
        'publisher':    data.get('publisher', '').strip(),
        'track':        data.get('track', '').strip(),
    }

    if not filename or not tag_values['artist'] or not tag_values['title']:
        return jsonify({"error": "filename, artist et title sont obligatoires"}), 400

    # Securite : nom de fichier simple
    if '/' in filename or '\\' in filename or '..' in filename:
        return jsonify({"error": "Nom de fichier invalide"}), 400

    src = os.path.join(SUSPECTS_DIR, filename)
    if not os.path.exists(src):
        return jsonify({"error": f"Fichier suspect introuvable : {filename}"}), 404

    # 1. Ecrire les tags ID3 via la table TAGS_FIELDS
    # Mapping frame ID3 -> cle dict
    FRAME_MAP = {
        'artist': 'TPE1', 'title': 'TIT2', 'album': 'TALB',
        'genre': 'TCON', 'album_artist': 'TPE2', 'composer': 'TCOM',
        'publisher': 'TPUB', 'track': 'TRCK',
    }
    try:
        from mutagen.id3 import (ID3, TDRC, TPE1, TIT2, TALB,
                                  TCON, TPE2, TCOM, TPUB, TRCK)
        FRAME_CLASSES = {
            'TPE1': TPE1, 'TIT2': TIT2, 'TALB': TALB,
            'TCON': TCON, 'TPE2': TPE2, 'TCOM': TCOM,
            'TPUB': TPUB, 'TRCK': TRCK,
        }
        tags = ID3(src)
        for key, frame_id in FRAME_MAP.items():
            val = tag_values[key]
            if val:
                tags.add(FRAME_CLASSES[frame_id](encoding=3, text=val))
        # Annee : supprimer anciens frames, ajouter le nouveau
        for yf in ('TDRC', 'TYER'):
            if yf in tags:
                del tags[yf]
        if tag_values['year'] and tag_values['year'] != '0':
            tags.add(TDRC(encoding=3, text=tag_values['year']))
        tags.save()
        _logger.info(f'Tags ID3 ecrits pour {filename} : {tag_values["artist"]} - {tag_values["title"]}')
    except Exception as e:
        _logger.error(f'Erreur ecriture tags ID3 : {e}')
        return jsonify({"error": f"Erreur ecriture tags ID3 : {e}"}), 500

    # 2. Copier vers FolderSync (meme logique que api_suspect_validate)
    try:
        sync_dir = _dossier_foldersync_courant()
    except Exception as e:
        return jsonify({"error": f"Erreur FolderSync : {e}"}), 500

    dst = os.path.join(str(sync_dir), filename)
    if os.path.exists(dst):
        stem, ext = os.path.splitext(filename)
        idx = 1
        while os.path.exists(dst):
            dst = os.path.join(str(sync_dir), f"{stem}_{idx}{ext}")
            idx += 1

    try:
        shutil.copy2(src, dst)
    except Exception as e:
        return jsonify({"error": f"Erreur copie vers FolderSync : {e}"}), 500

    # Normaliser le volume a 89 dB (SYNCHRONE — obligatoire avant sync RadioDJ)
    mp3gain_result = _run_mp3gain_windows(dst)
    if not mp3gain_result['ok']:
        # mp3gain a echoue : supprimer le fichier de FolderSync et signaler l'erreur
        _logger.error(f'mp3gain echoue pour suspect {filename} : {mp3gain_result["message"]}')
        try:
            os.remove(dst)
        except Exception:
            pass
        return jsonify({
            "error": f"mp3gain echoue : {mp3gain_result['message']}. "
                   f"Fichier non importe. Corrigez le fichier ou validez sans mp3gain si necessaire.",
            "mp3gain": mp3gain_result,
        }), 500

    # Lire le sidecar AVANT suppression du suspect
    sidecar_ev = _lire_sidecar_suspect(src)

    # Supprimer le suspect
    try:
        os.remove(src)
    except Exception as e:
        _logger.warning(f"Suspect edit-valide mais suppression echouee : {e}")
    # Supprimer le sidecar .match.json
    try:
        os.remove(src + '.match.json')
    except Exception:
        pass

    # 3. Declencher sync RadioDJ (APRES mp3gain termine avec succes)
    sync_result = None
    try:
        base_url = f'http://{RADIODJ_API_HOST}:{RADIODJ_API_PORT}'
        pass_q = quote(RADIODJ_API_PASS, safe='')
        url = f'{base_url}/opt?auth={pass_q}&command=RunEvent&arg={RADIODJ_EVENT_ID}'
        req = Request(url, method="GET")
        req.add_header('User-Agent', 'AIRVS-Dashboard/3.1')
        with urlopen(req, timeout=5) as resp:
            body = resp.read().decode('utf-8', errors='replace')[:500]
            m = re.search(r'<string[^>]*>(\d+)</string>', body.strip())
            sync_ok = m and int(m.group(1)) == 200
            sync_result = {
                "ok": sync_ok,
                "message": "FolderSync declenche" if sync_ok else "Echec sync"
            }
    except Exception as e:
        sync_result = {"ok": False, "message": str(e)}

    # Enregistrer la decision dans suspects_validated
    # Pour edit-validate, les tags ont pu etre modifies -> utiliser les nouveaux tags
    _enregistrer_decision_suspect(
        filename, 'valide', sidecar=sidecar_ev,
        tags={'artist': tag_values['artist'], 'title': tag_values['title']}
    )

    _logger.info(f'Suspect edit+valide : {filename} -> {dst}')
    _nettoyer_dossier_suspects_si_vide()
    return jsonify({
        "ok": True,
        "filename": filename,
        "new_tags": {"artist": tag_values["artist"], "title": tag_values["title"], "album": tag_values["album"], "year": tag_values["year"]},
        "destination": str(sync_dir),
        "mp3gain": mp3gain_result,
        "sync_radiodj": sync_result,
    })


@pipeline_import_bp.route('/api/suspects/reject', methods=['POST'])
@login_requis
def api_suspect_reject():
    """Rejette un suspect : supprime le fichier.

    Strategie :
      1. Suppression directe via SMB (U:\) - synchrone, immediat
      2. Si echec SMB, fallback via tache worker (SSHFS/SSH vers Debian)

    Body JSON : { filename: str }
    """
    data = request.json or {}
    filename = data.get('filename', '').strip()
    if not filename:
        return jsonify({"error": "'filename' est obligatoire"}), 400

    # Securite : nom de fichier simple
    if '/' in filename or '\\' in filename or '..' in filename:
        return jsonify({"error": "Nom de fichier invalide"}), 400

    src = os.path.join(SUSPECTS_DIR, filename)
    if not os.path.exists(src):
        return jsonify({"error": f"Fichier suspect introuvable : {filename}"}), 404

    # ── 1. Suppression directe via SMB (synchrone, fiable) ──
    try:
        # Lire le sidecar AVANT suppression
        sidecar_rej = _lire_sidecar_suspect(src)
        os.remove(src)
        # Supprimer le sidecar .match.json
        try:
            os.remove(src + '.match.json')
        except Exception:
            pass
        # Enregistrer la decision dans suspects_validated
        _enregistrer_decision_suspect(filename, 'rejete', sidecar=sidecar_rej)

        _logger.info(f'Suspect supprime directement (SMB) : {filename}')
        _nettoyer_dossier_suspects_si_vide()
        return jsonify({
            "ok": True,
            "filename": filename,
            "method": "direct",
            "message": "Fichier supprime.",
        })
    except PermissionError as e:
        _logger.warning(f'Suppression directe refusee (permissions) : {e}')
    except OSError as e:
        _logger.warning(f'Suppression directe echouee (OSError) : {e}')
    except Exception as e:
        _logger.warning(f'Suppression directe echouee : {e}')

    # ── 2. Fallback : tache worker (SSHFS/SSH vers Debian) ──
    from utils import windows_vers_linux
    try:
        linux_path = windows_vers_linux(src)
    except (ValueError, Exception) as e:
        return jsonify({"error": f"Suppression directe echouee et conversion chemin impossible : {e}"}), 500

    parametres = {
        'filepath': str(linux_path),
        'original_filename': filename,
    }

    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO taches_planifiees "
            "(type_action, statut, parametres, date_creation) "
            "VALUES (%s, 'en_attente', %s, NOW())",
            ('SUSPECT_REJECT', json.dumps(parametres, ensure_ascii=False))
        )
        tache_id = cursor.lastrowid
        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        _logger.error(f'Suspect reject fallback erreur INSERT : {e}')
        return jsonify({"error": f"Suppression directe echouee, erreur creation tache : {e}"}), 500

    _logger.info(f'Suspect rejete via worker (tache #{tache_id}) : {filename}')
    return jsonify({
        "ok": True,
        "filename": filename,
        "method": "worker",
        "tache_id": tache_id,
        "message": f"Suppression directe echouee, tache #{tache_id} en file pour le worker.",
    })


# ═══════════════════════════════════════════════════════════════════════
# Routes manquants (airvs_manquants) — B2
# ═══════════════════════════════════════════════════════════════════════

@pipeline_import_bp.route('/api/manquants/liste')
@login_requis
def api_manquants_liste():
    """Liste les titres manquants avec filtres et pagination.

    Query params :
      - source  (str) : filtre par source ('prefill_sheets','shazam','animateurs')
      - statut  (str) : filtre par statut ('en_attente','importe','resolu')
      - q       (str) : recherche libre (artiste ou titre)
      - limit   (int): nb max de resultats (defaut 100)
      - offset  (int): decalage (defaut 0)
    """
    source = request.args.get('source', '').strip()
    statut = request.args.get('statut', '').strip()
    q = request.args.get('q', '').strip()
    try:
        limit = min(int(request.args.get('limit', 100)), 500)
    except (ValueError, TypeError):
        limit = 100
    try:
        offset = max(int(request.args.get('offset', 0)), 0)
    except (ValueError, TypeError):
        offset = 0

    db = get_db_connection()
    if not db:
        return jsonify({"error": "Erreur DB"}), 500

    try:
        cursor = db.cursor()

        # Compteur total
        where_clauses = ["1=1"]
        params = []
        if source and source in ('prefill_sheets', 'shazam', 'animateurs'):
            where_clauses.append("source = %s")
            params.append(source)
        if statut and statut in ('en_attente', 'importe', 'resolu'):
            where_clauses.append("statut = %s")
            params.append(statut)
        if q:
            where_clauses.append("(artiste LIKE %s OR titre LIKE %s)")
            q_like = f"%{q}%"
            params.extend([q_like, q_like])

        where_sql = " AND ".join(where_clauses)

        cursor.execute(f"SELECT COUNT(*) AS cnt FROM airvs_manquants WHERE {where_sql}", params)
        total = cursor.fetchone()["cnt"]

        # Resultats
        cursor.execute(
            f"SELECT m.*, DATEDIFF(NOW(), m.date_creation) AS age_jours "
            f"FROM airvs_manquants m "
            f"WHERE {where_sql} "
            f"ORDER BY m.date_creation DESC "
            f"LIMIT %s OFFSET %s",
            params + [limit, offset]
        )
        rows = cursor.fetchall()

        # Serializer les datetime
        items = []
        for r in rows:
            item = dict(r)
            for k, v in item.items():
                if hasattr(v, 'isoformat'):
                    item[k] = v.isoformat()
                elif hasattr(v, 'strftime'):
                    item[k] = v.strftime('%Y-%m-%d %H:%M:%S')
            items.append(item)

        cursor.close()
        db.close()

    except Exception as e:
        try:
            cursor.close()
            db.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "filters": {
            "source": source or "all",
            "statut": statut or "all",
            "q": q,
        },
    })


@pipeline_import_bp.route('/api/manquants/stats')
@login_requis
def api_manquants_stats():
    """Compteurs des manquants par source et statut."""
    db = get_db_connection()
    if not db:
        return jsonify({"error": "Erreur DB"}), 500

    try:
        cursor = db.cursor()

        # Par source
        cursor.execute(
            "SELECT source, statut, COUNT(*) AS nb "
            "FROM airvs_manquants GROUP BY source, statut"
        )
        by_source = cursor.fetchall()

        # Totaux
        cursor.execute("SELECT COUNT(*) AS cnt FROM airvs_manquants WHERE statut = 'en_attente'")
        en_attente = cursor.fetchone()["cnt"]
        cursor.execute("SELECT COUNT(*) AS cnt FROM airvs_manquants WHERE statut = 'importe' AND resolu_le > NOW() - INTERVAL 7 DAY")
        importes_7j = cursor.fetchone()["cnt"]
        cursor.execute("SELECT COUNT(*) AS cnt FROM airvs_manquants WHERE statut = 'resolu'")
        resolus = cursor.fetchone()["cnt"]

        cursor.close()
        db.close()

    except Exception as e:
        try:
            cursor.close()
            db.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "en_attente": en_attente,
        "importes_7j": importes_7j,
        "resolus": resolus,
        "by_source": [
            {"source": r["source"], "statut": r["statut"], "nb": r["nb"]}
            for r in by_source
        ],
    })


@pipeline_import_bp.route('/api/manquants/resolve', methods=['POST'])
@login_requis
def api_manquants_resolve():
    """Marque un manquant comme resolu (faux positif ou traite manuellement).

    Body JSON : { id: int, note: str (optionnel) }
    """
    data = request.json or {}
    manq_id = data.get('id')
    note = data.get('note', '').strip()

    if not manq_id:
        return jsonify({"error": "'id' est obligatoire"}), 400

    db = get_db_connection()
    if not db:
        return jsonify({"error": "Erreur DB"}), 500

    try:
        cursor = db.cursor()
        cursor.execute(
            "UPDATE airvs_manquants SET statut = 'resolu', resolu_le = NOW() WHERE id = %s AND statut = 'en_attente'",
            (manq_id,)
        )
        updated = cursor.rowcount
        if updated == 0:
            cursor.close()
            db.close()
            return jsonify({"error": "Aucun manquant en_attente avec cet ID"}), 404
        if note:
            cursor.execute(
                "UPDATE airvs_manquants SET origine = CONCAT(IFNULL(origine,''), ' | resolu: ', %s) WHERE id = %s",
                (note, manq_id)
            )
        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        try:
            cursor.close()
            db.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, "id": manq_id, "statut": "resolu"})


@pipeline_import_bp.route('/api/manquants/delete', methods=['DELETE'])
@login_requis
def api_manquants_delete():
    """Supprime un manquant (faux positif definitif).

    Body JSON : { id: int }
    """
    data = request.json or {}
    manq_id = data.get('id')

    if not manq_id:
        return jsonify({"error": "'id' est obligatoire"}), 400

    db = get_db_connection()
    if not db:
        return jsonify({"error": "Erreur DB"}), 500

    try:
        cursor = db.cursor()
        cursor.execute("DELETE FROM airvs_manquants WHERE id = %s", (manq_id,))
        deleted = cursor.rowcount
        db.commit()
        cursor.close()
        db.close()

        if deleted == 0:
            return jsonify({"error": "Aucun manquant avec cet ID"}), 404

    except Exception as e:
        try:
            cursor.close()
            db.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, "id": manq_id})

