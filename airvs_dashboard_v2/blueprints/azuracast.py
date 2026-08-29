"""blueprints/azuracast.py -- Routes AzuraCast (push, sync, stations, folders, playlists, M3U, bulk schedule, diagnostic, mark).

Routes extraites de app.py pour le blueprint azuracast_bp.

Gestion du pont RadioDJ → AzuraCast :
- Push de titres vers AzuraCast (modes assisté, SQL, M3U)
- Synchronisation des statuts IN_AZURACAST
- Gestion des playlists AzuraCast (création, import M3U, sync cache)
- Gestion des dossiers AzuraCast
- Diagnostic multi-station
- Bulk schedule de playlists

Routes :
  - GET  /api/azuracast/stations
  - POST /api/push_preview
  - POST /lancer_push_azura
  - POST /api/push/cancel
  - GET  /lire_log_push
  - GET  /api/azuracast/folders
  - POST /api/marquer_in_azuracast
  - POST /api/sync_azura_status
  - GET  /api/azuracast/diagnostic
  - GET  /api/azuracast/playlists
  - POST /api/azuracast/sync_cache
  - POST /api/azuracast/creer_playlist
  - POST /api/azuracast/import_m3u
  - GET  /lire_log_playlist
  - POST /lancer_sync_playlists_cache
  - GET  /lire_log_sync_playlists
  - POST /api/azuracast/bulk_schedule/preview
  - POST /api/azuracast/bulk_schedule/apply
"""

import json
import os
import re
import time
import urllib.request
import urllib.parse
import ssl
import subprocess as sp
import threading
from datetime import datetime

import pymysql
from flask import Blueprint, request, jsonify

from config import (
    CONFIG_JSON_PATH,
    API_TOKEN,
    DEBIAN_3_IP, DEBIAN_3_SSH_USER,
    _SUBPROCESS_CREATIONFLAGS,
    AZURA_API_URL, AZURA_API_KEY, AZURA_MEDIA_BASE,
    PUSH_LIMIT_HARDCAP,
    BULK_SCHEDULE_MAX_PLAYLISTS, BULK_SCHEDULE_POLL_TIMEOUT,
)
from utils import get_db_connection, _logger, _charger_config_json
from blueprints.auth import login_requis


azuracast_bp = Blueprint('azuracast', __name__)


# ══════════════════════════════════════════
# DETECTION DYNAMIQUE DES COLONNES SONGS
# ══════════════════════════════════════════
# Les colonnes « optionnelles » (sm_weight, sm_rating, play_count, date_played)
# peuvent porter des noms différents selon la version de RadioDJ.
# On les détecte une fois au runtime et on met en cache le résultat.

_COLONNES_OPTIONNELLES_CANDIDATS = {
    # clé logique  → liste de noms SQL possibles (ordre de préférence)
    'sm_weight':  ['sm_weight', 'weight', 'song_weight', 'rotation_weight'],
    'sm_rating':  ['sm_rating', 'rating', 'song_rating'],
    'play_count': ['play_count', 'count_played', 'played_count'],
    'date_played':['date_played', 'last_played', 'date_last_played'],
}
_colonnes_cache = None  # sera peuplé au 1er appel


def _detecter_colonnes_optionnelles():
    """Interroge INFORMATION_SCHEMA pour trouver les colonnes optionnelles
    réellement présentes dans la table songs. Résultat mis en cache.
    
    Retourne un dict :
      {
        'sm_weight':  'nom_trouve_ou_None',
        'sm_rating':  'nom_trouve_ou_None',
        'play_count': 'nom_trouve_ou_None',
        'date_played':'nom_trouve_ou_None',
      }
    """
    global _colonnes_cache
    if _colonnes_cache is not None:
        return _colonnes_cache

    db = get_db_connection()
    resultat = {k: None for k in _COLONNES_OPTIONNELLES_CANDIDATS}
    if db:
        try:
            cursor = db.cursor()
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
            _logger.warning(f"[azuracast] Détection colonnes songs échouée : {e}")
            try:
                db.close()
            except Exception:
                pass
    else:
        _logger.warning("[azuracast] Détection colonnes songs : connexion DB impossible")

    _colonnes_cache = resultat
    _logger.info(f"[azuracast] Colonnes optionnelles détectées : "
                  f"{ {k: v for k, v in resultat.items() if v} }")
    return resultat


def _construire_select_optionnel(alias='s'):
    """Retourne les fragments SQL pour les colonnes optionnelles détectées.
    
    Retourne :
      select_parts : liste de "s.nom_col AS cle_logique"
      disponibles  : dict des clés logiques disponibles
    """
    cols = _detecter_colonnes_optionnelles()
    select_parts = []
    for cle_logique, nom_sql in cols.items():
        if nom_sql:
            select_parts.append(f"{alias}.{nom_sql} AS {cle_logique}")
    return select_parts, {k: v for k, v in cols.items() if v}


# ══════════════════════════════════════════
# HELPERS AZURACAST
# ══════════════════════════════════════════

def _charger_azura_config():
    """Sous-fonction : retourne uniquement la section 'azuracast' de config.json.
    Retourne None si absent ou malformé."""
    config = _charger_config_json()
    azura = config.get("azuracast") if isinstance(config, dict) else None
    if not isinstance(azura, dict):
        return None
    return azura


def _lister_stations():
    """Retourne la liste des stations connues, avec fallback sur station 7 seule
    si config.json est absent ou ne contient pas la section 'azuracast.stations'."""
    azura = _charger_azura_config()
    if not azura or not isinstance(azura.get("stations"), list):
        # Fallback : comportement historique (station 7 seule)
        return [{"id": 7, "nom": "Station 7"}]
    stations = []
    for s in azura["stations"]:
        if isinstance(s, dict) and "id" in s:
            stations.append({
                "id": int(s["id"]),
                "nom": s.get("nom", f"Station {s['id']}")
            })
    if not stations:
        return [{"id": 7, "nom": "Station 7"}]
    return stations


def _station_par_defaut():
    """Retourne l'ID de station par défaut (première station de config.json)."""
    stations = _lister_stations()
    return stations[0]["id"] if stations else 7


def _azura_api_get(path, params=None, timeout=8):
    """Appel HTTP GET vers l'API AzuraCast via urllib (pas de dépendance requests)."""
    url = AZURA_API_URL + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        'Authorization': 'Bearer ' + AZURA_API_KEY,
        'Accept': 'application/json'
    })
    # Ignorer la vérification SSL si nécessaire (certificat auto-signé)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        if resp.status == 200:
            import json as _json
            return _json.loads(resp.read().decode('utf-8'))
        return None


def _valider_requete_push(requete):
    """Valide et normalise une requête SQL libre pour le push."""
    cleaned = requete.strip()
    first_word = cleaned.lstrip().split()[0].upper() if cleaned.lstrip() else ''

    if first_word != 'SELECT':
        return None, "Seules les requêtes SELECT sont autorisées."

    upper = cleaned.upper()
    dangerous = [
        'INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE',
        'TRUNCATE', 'REPLACE', 'GRANT', 'REVOKE', 'CALL', 'EXECUTE'
    ]
    for mot in dangerous:
        if re.search(r'\b' + mot + r'\b', upper):
            return None, f"Le mot-clé '{mot}' est interdit."

    if 'FROM' not in upper:
        return None, "La requête doit contenir FROM songs."

    # Vérifier que la requête retourne au minimum ID et path
    select_part = upper.split('FROM')[0] if 'FROM' in upper else upper
    if 'ID' not in select_part or 'PATH' not in select_part:
        return None, "La requête doit retourner au minimum ID et path."

    limit_match = re.search(r'LIMIT\s+(\d+)', upper)
    if limit_match:
        limit_val = int(limit_match.group(1))
        if limit_val > PUSH_LIMIT_HARDCAP:
            cleaned = re.sub(
                r'LIMIT\s+\d+', f'LIMIT {PUSH_LIMIT_HARDCAP}',
                cleaned, flags=re.IGNORECASE
            )
    else:
        cleaned = cleaned.rstrip(';') + f" LIMIT {PUSH_LIMIT_HARDCAP}"

    return cleaned, None


def _construire_requete_assistee(data):
    """Construit une requête SQL sécurisée à partir des filtres assistés.
    
    Supporte la recherche multi-valeurs pour artiste et titre :
    - Valeur unique : "Daft Punk" → LIKE %Daft Punk%
    - Valeurs multiples (séparées par virgules) : "Daft Punk, Madonna" → (LIKE %Daft Punk% OR LIKE %Madonna%)
    """
    conditions = ["s.song_type = 0", "s.enabled = 1"]
    params = []

    # ── Filtre Artiste (multi-valeurs avec virgules) ──
    artiste_raw = data.get('artiste', '').strip()
    if artiste_raw:
        artistes = [a.strip() for a in artiste_raw.split(',') if a.strip()]
        if len(artistes) == 1:
            conditions.append("s.artist LIKE %s")
            params.append(f"%{artistes[0]}%")
        elif len(artistes) > 1:
            or_parts = []
            for a in artistes:
                or_parts.append("s.artist LIKE %s")
                params.append(f"%{a}%")
            conditions.append(f"({' OR '.join(or_parts)})")

    # ── Filtre Titre (multi-valeurs avec virgules) ──
    titre_raw = data.get('titre', '').strip()
    if titre_raw:
        titres = [t.strip() for t in titre_raw.split(',') if t.strip()]
        if len(titres) == 1:
            conditions.append("s.title LIKE %s")
            params.append(f"%{titres[0]}%")
        elif len(titres) > 1:
            or_parts = []
            for t in titres:
                or_parts.append("s.title LIKE %s")
                params.append(f"%{t}%")
            conditions.append(f"({' OR '.join(or_parts)})")

    # ── Évolution 2a : Filtre Album (multi-valeurs avec virgules) ──
    # Le champ album de la table songs est utilisé pour filtrer la recherche.
    # Supporte aussi bien "Album" que plusieurs albums séparés par virgules.
    album_raw = data.get('album', '').strip()
    if album_raw:
        albums = [a.strip() for a in album_raw.split(',') if a.strip()]
        if len(albums) == 1:
            conditions.append("s.album LIKE %s")
            params.append(f"%{albums[0]}%")
        elif len(albums) > 1:
            or_parts = []
            for a in albums:
                or_parts.append("s.album LIKE %s")
                params.append(f"%{a}%")
            conditions.append(f"({' OR '.join(or_parts)})")

    bpm_min = data.get('bpm_min', '').strip()
    bpm_max = data.get('bpm_max', '').strip()
    if bpm_min:
        conditions.append("s.bpm >= %s")
        params.append(int(bpm_min))
    if bpm_max:
        conditions.append("s.bpm < %s")
        params.append(int(bpm_max))

    cat_id = data.get('category_id')
    subcat_id = data.get('subcategory_id')
    if subcat_id:
        conditions.append("s.id_subcat = %s")
        params.append(subcat_id)
    elif cat_id:
        conditions.append("s.id_subcat IN (SELECT ID FROM subcategory WHERE parentid = %s)")
        params.append(cat_id)

    genre = data.get('genre', '').strip()
    if genre:
        conditions.append("g.name LIKE %s")
        params.append(f"%{genre}%")

    annee_min = data.get('annee_min', '').strip()
    annee_max = data.get('annee_max', '').strip()
    if annee_min:
        conditions.append("s.year >= %s")
        params.append(int(annee_min))
    if annee_max:
        conditions.append("s.year <= %s")
        params.append(int(annee_max))

    exclure_bpm_zero = data.get('exclure_bpm_zero', False)
    if exclure_bpm_zero:
        conditions.append("s.bpm > 0")

    # ── Filtre chronologique date_added ──
    date_added_apres = data.get('date_added_apres', '').strip()
    if date_added_apres:
        conditions.append("s.date_added >= %s")
        params.append(date_added_apres)

    # ── Colonnes optionnelles détectées dynamiquement ──
    opt_select_parts, opt_disponibles = _construire_select_optionnel('s')

    # ── Filtre poids minimum (sm_weight) ──
    poids_min = data.get('poids_min', '').strip()
    if poids_min and opt_disponibles.get('sm_weight'):
        conditions.append(f"s.{opt_disponibles['sm_weight']} >= %s")
        params.append(float(poids_min))

    # ── Filtre note minimum (sm_rating) ──
    note_min = data.get('note_min', '').strip()
    if note_min and opt_disponibles.get('sm_rating'):
        conditions.append(f"s.{opt_disponibles['sm_rating']} >= %s")
        params.append(int(note_min))

    # ── Filtre play_count ──
    play_count_mode = data.get('play_count_mode', '').strip()
    if play_count_mode and opt_disponibles.get('play_count'):
        pc_col = opt_disponibles['play_count']
        if play_count_mode == 'jamais':
            conditions.append(f"s.{pc_col} = 0")
        else:
            try:
                max_pc = int(play_count_mode)
                conditions.append(f"s.{pc_col} < %s")
                params.append(max_pc)
            except ValueError:
                pass

    # ── Filtre pas diffuse depuis X jours ──
    pas_diffuse_jours = data.get('pas_diffuse_jours', '').strip()
    if pas_diffuse_jours and opt_disponibles.get('date_played'):
        dp_col = opt_disponibles['date_played']
        conditions.append(f"(s.{dp_col} IS NULL OR s.{dp_col} < NOW() - INTERVAL %s DAY)")
        params.append(int(pas_diffuse_jours))

    # ── Whitelist tri dynamique ──
    _tri_fixes = (
        'RAND()',
        's.artist ASC', 's.artist DESC',
        's.bpm ASC', 's.bpm DESC',
        's.year DESC', 's.year ASC',
        's.date_added DESC', 's.date_added ASC',
    )
    _tri_optionnels = []
    for cle, nom_sql in opt_disponibles.items():
        _tri_optionnels.extend([
            f's.{nom_sql} DESC',
            f's.{nom_sql} ASC',
        ])
    ordre = data.get('ordre', 'RAND()')
    if ordre not in _tri_fixes and ordre not in _tri_optionnels:
        ordre = 'RAND()'

    limite = min(int(data.get('limite', 10)), PUSH_LIMIT_HARDCAP)

    # ── Construction du SELECT ──
    opt_select_str = (', ' + ', '.join(opt_select_parts)) if opt_select_parts else ''
    query = (
        f"SELECT s.ID, s.artist, s.title, s.bpm, s.duration, s.`path`, s.comments, "
        f"s.album, s.year, s.date_added{opt_select_str} "
        f"FROM songs s LEFT JOIN genre g ON s.id_genre = g.id "
        f"WHERE {' AND '.join(conditions)} "
        f"GROUP BY s.ID ORDER BY {ordre} LIMIT {limite}"
    )
    return query, tuple(params)


def _parser_m3u(chemin_fichier):
    """Parse un fichier M3U et retourne la liste des chemins non-commentaires."""
    chemins = []
    try:
        with open(chemin_fichier, 'r', encoding='utf-8') as f:
            for ligne in f:
                ligne = ligne.strip()
                if not ligne or ligne.startswith('#'):
                    continue
                chemins.append(ligne)
    except Exception as e:
        return None, str(e)
    return chemins, None


def _resoudre_titres_m3u(chemins_m3u):
    """Pour chaque chemin de M3U, trouve le titre correspondant en SQL.
    Inclut les colonnes optionnelles (sm_weight, sm_rating, etc.) si disponibles."""
    db = get_db_connection()
    if not db:
        return None, "Erreur DB"

    # Colonnes optionnelles détectées dynamiquement
    opt_select_parts, opt_disponibles = _construire_select_optionnel('')
    opt_select_str = (', ' + ', '.join(opt_select_parts)) if opt_select_parts else ''

    cursor = db.cursor()
    resultats = []

    for chemin in chemins_m3u:
        filename = os.path.basename(chemin)
        cursor.execute(
            f"SELECT ID, artist, title, bpm, duration, `path`, comments, album, year, date_added{opt_select_str} "
            f"FROM songs WHERE `path` LIKE %s AND song_type = 0 LIMIT 1",
            (f"%{filename}%",)
        )
        row = cursor.fetchone()
        if row:
            resultats.append(dict(row))
        else:
            resultats.append({
                "ID": None, "artist": "?", "title": filename,
                "bpm": 0, "duration": 0, "path": chemin, "_absent": True
            })

    cursor.close()
    db.close()
    return resultats, None


def _bulk_insert_and_wait(parametres):
    """Insère une tâche BULK_SCHEDULE dans taches_planifiees et attend le résultat.

    Retourne (success: bool, result_json: dict ou None, error_msg: str)."""
    conn = get_db_connection()
    if not conn:
        return False, None, "Connexion DB impossible"
    try:
        cur = conn.cursor()
        # Insérer la tâche
        cur.execute("""
            INSERT INTO taches_planifiees (type_action, parametres, statut, date_creation)
            VALUES ('BULK_SCHEDULE', %s, 'en_attente', NOW())
        """, (json.dumps(parametres),))
        tache_id = cur.lastrowid
        conn.commit()
    except Exception as e:
        conn.close()
        return False, None, f"Insertion tâche échouée: {e}"
    finally:
        conn.close()

    # Poll le statut jusqu'à termine/erreur ou timeout
    import time as _time
    start = _time.time()
    while _time.time() - start < BULK_SCHEDULE_POLL_TIMEOUT:
        conn = get_db_connection()
        if not conn:
            _time.sleep(2)
            continue
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT statut, log_resultat
                FROM taches_planifiees WHERE id = %s
            """, (tache_id,))
            row = cur.fetchone()
            conn.close()
            if not row:
                _time.sleep(1)
                continue
            statut = row['statut']
            if statut == 'termine':
                # Récupérer le résultat depuis log_resultat.
                # Le worker stocke le JSON avec un marqueur __BULK_RESULT__:
                log_raw = row.get('log_resultat', '') or ''
                result = None
                marker = '__BULK_RESULT__:'
                if marker in log_raw:
                    idx = log_raw.index(marker) + len(marker)
                    json_candidate = log_raw[idx:]
                    # Le JSON peut avoir un \n à la fin — le retirer
                    json_candidate = json_candidate.rstrip('\n\r ')
                    try:
                        result = json.loads(json_candidate)
                    except json.JSONDecodeError:
                        pass
                return True, result, None
            elif statut == 'erreur':
                log_raw = row.get('log_resultat', '') or ''
                return False, None, f"Worker erreur: {log_raw[:500]}"
            # Sinon en_attente ou en_cours → continuer à poller
        except Exception as e:
            print(f"[Bulk schedule] Poll erreur: {e}")
        _time.sleep(1)

    return False, None, f"Timeout ({BULK_SCHEDULE_POLL_TIMEOUT}s) — le worker n'a pas traité la tâche"


# ══════════════════════════════════════════
# ROUTES AZURACAST STATIONS
# ══════════════════════════════════════════

@azuracast_bp.route('/api/azuracast/stations')
@login_requis
def api_azuracast_stations():
    """Retourne la liste des stations AzuraCast configurées dans config.json.
    Permet au dashboard d'alimenter le <select> 'Station cible'."""
    return jsonify({
        "status": "ok",
        "stations": _lister_stations(),
        "default_station_id": _station_par_defaut()
    })


# ══════════════════════════════════════════
# ROUTES PONT RADIODJ → AZURACAST
# ══════════════════════════════════════════

@azuracast_bp.route('/api/azuracast/colonnes_disponibles', methods=['GET'])
@login_requis
def api_colonnes_disponibles():
    """Retourne les colonnes optionnelles détectées dans la table songs.
    Permet au frontend de savoir quels filtres/colonnes afficher."
    """
    cols = _detecter_colonnes_optionnelles()
    # Retourne uniquement les clés logiques dont la valeur n'est pas None
    disponibles = {k: v for k, v in cols.items() if v is not None}
    return jsonify({"disponibles": disponibles, "toutes": list(_COLONNES_OPTIONNELLES_CANDIDATS.keys())})


@azuracast_bp.route('/api/push_preview', methods=['POST'])
@login_requis
def api_push_preview():
    data = request.json
    mode = data.get('mode', 'assiste')

    # Inclure les colonnes disponibles dans la réponse pour le frontend
    opt_info = _detecter_colonnes_optionnelles()
    opt_disponibles = {k: v for k, v in opt_info.items() if v is not None}

    if mode == 'assiste':
        query, params = _construire_requete_assistee(data)
        db = get_db_connection()
        if not db:
            return jsonify({"error": "Erreur DB"}), 500
        cursor = db.cursor()
        try:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        except Exception as e:
            cursor.close()
            db.close()
            return jsonify({"error": f"Erreur SQL : {e}"}), 400
        cursor.close()
        db.close()
        return jsonify({"titres": [dict(r) for r in rows], "mode": "assiste", "colonnes_disponibles": opt_disponibles})

    elif mode == 'sql':
        requete = data.get('requete', '').strip()
        if not requete:
            return jsonify({"error": "Requête vide"}), 400
        requete_ok, erreur = _valider_requete_push(requete)
        if erreur:
            return jsonify({"error": erreur}), 400
        db = get_db_connection()
        if not db:
            return jsonify({"error": "Erreur DB"}), 500
        cursor = db.cursor()
        try:
            cursor.execute(requete_ok)
            rows = cursor.fetchall()
        except Exception as e:
            cursor.close()
            db.close()
            return jsonify({"error": f"Erreur SQL : {e}"}), 400
        cursor.close()
        db.close()
        return jsonify({"titres": [dict(r) for r in rows], "mode": "sql", "colonnes_disponibles": opt_disponibles})

    elif mode == 'm3u':
        chemins = data.get('chemins_m3u', [])

        # Mode fallback : chemin local sur le serveur Flask (Windows)
        if not chemins:
            chemin_m3u = data.get('chemin_m3u', '').strip()
            if not chemin_m3u:
                return jsonify({"error": "Fournissez un fichier M3U ou un chemin."}), 400
            if not os.path.exists(chemin_m3u):
                return jsonify({"error": f"Fichier introuvable : {chemin_m3u}"}), 400
            chemins, erreur = _parser_m3u(chemin_m3u)
            if erreur:
                return jsonify({"error": f"Erreur lecture M3U : {erreur}"}), 400

        if not chemins:
            return jsonify({"error": "Le fichier M3U est vide."}), 400

        titres, erreur = _resoudre_titres_m3u(chemins)
        if erreur:
            return jsonify({"error": erreur}), 500
        return jsonify({"titres": titres, "mode": "m3u", "colonnes_disponibles": opt_disponibles})

    else:
        return jsonify({"error": f"Mode inconnu : {mode}"}), 400


@azuracast_bp.route('/lancer_push_azura', methods=['POST'])
@login_requis
def lancer_push_azura():
    data = request.get_json(force=True)
    fichiers = data.get('fichiers', [])
    fichiers_deja_azura = data.get('fichiers_deja_azura', [])
    dossier_cible = data.get('dossier_cible', 'imports_push')

    # ── Paramètre station_id (multi-station) ──
    # Si absent, on utilise la station par défaut (première de config.json, généralement 7)
    try:
        station_id = int(data.get('station_id', 0)) or _station_par_defaut()
    except (ValueError, TypeError):
        station_id = _station_par_defaut()

    # ── Nouveaux paramètres playlist ──
    creer_playlist = data.get('creer_playlist', False)
    nom_playlist = data.get('nom_playlist', '')
    pl_type = data.get('playlist_type', 'default')
    pl_order = data.get('playlist_order', 'shuffle')
    playlist_ids = data.get('playlist_ids', [])  # Tableau d'IDs de playlists existantes
    # Rétrocompatibilité : si l'ancien paramètre playlist_id_existing est encore utilisé
    playlist_id_existing = data.get('playlist_id_existing')
    if playlist_id_existing and not playlist_ids:
        playlist_ids = [int(playlist_id_existing)]

    total_fichiers = len(fichiers) + len(fichiers_deja_azura)
    if total_fichiers == 0:
        return jsonify({'status': 'error', 'message': 'Aucun fichier sélectionné.'}), 400

    if total_fichiers > PUSH_LIMIT_HARDCAP:
        return jsonify({
            'status': 'error',
            'message': f'Limite dépassée : {total_fichiers} fichiers (max {PUSH_LIMIT_HARDCAP}).'
        }), 400

    destination = AZURA_MEDIA_BASE
    if dossier_cible:
        destination = AZURA_MEDIA_BASE + '/' + dossier_cible.strip('/')

    # Déterminer si on ajoute un sous-dossier daté automatiquement
    # Oui uniquement si dossier_cible est le défaut "imports_push" (pas un dossier choisi par l'utilisateur)
    ajouter_sous_dossier_date = (dossier_cible.strip('/') == 'imports_push')

    try:
        db = get_db_connection()
        cursor = db.cursor()

        # Tâche 1 : Copie SFTP (uniquement les fichiers PAS encore dans AzuraCast)
        # La copie SFTP est AGNOSTIQUE de la station (les 2 stations partagent sshfs_root).
        # Mais on propage quand même station_id pour que le worker sache quelle API interroger
        # pour vérifier les doublons.
        task_id = None
        if fichiers:
            params_copie = {
                'destination': destination,
                'fichiers': fichiers,
                'dossier_nom': '',
                'ajouter_sous_dossier_date': ajouter_sous_dossier_date,
                'station_id': station_id
            }
            cursor.execute(
                "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
                "VALUES ('COPIE_FICHIERS', 'en_attente', %s, NOW())",
                (json.dumps(params_copie),)
            )
            db.commit()
            task_id = cursor.lastrowid

        # Tâche 2 (optionnelle) : Playlist(s) — avec TOUS les fichiers (copiés + déjà AzuraCast)
        # ICI station_id est critique : la playlist est créée sur UNE station précise.
        playlist_task_ids = []
        if creer_playlist and total_fichiers > 0:
            # Fusionner les deux listes en marquant ceux déjà dans AzuraCast
            tous_fichiers = []
            for f in fichiers:
                f_copy = dict(f) if isinstance(f, dict) else {'path': str(f)}
                f_copy['in_azura'] = False
                tous_fichiers.append(f_copy)
            for f in fichiers_deja_azura:
                f_copy = dict(f) if isinstance(f, dict) else {'path': str(f)}
                f_copy['in_azura'] = True
                tous_fichiers.append(f_copy)

            # Ajouter à chaque playlist existante sélectionnée
            for pl_id in playlist_ids:
                params_playlist = {
                    'playlist_id': int(pl_id),
                    'fichiers': tous_fichiers,
                    'dossier_cible': dossier_cible,
                    'station_id': station_id,
                    'push_task_id': task_id
                }
                cursor.execute(
                    "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
                    "VALUES ('IMPORT_M3U_PLAYLIST', 'en_attente', %s, NOW())",
                    (json.dumps(params_playlist),)
                )
                playlist_task_ids.append(cursor.lastrowid)

            # Créer une nouvelle playlist si un nom est fourni
            if nom_playlist:
                params_playlist = {
                    'nom_playlist': nom_playlist,
                    'fichiers': tous_fichiers,
                    'dossier_cible': dossier_cible,
                    'type': pl_type,
                    'order': pl_order,
                    'station_id': station_id,
                    'push_task_id': task_id
                }
                cursor.execute(
                    "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
                    "VALUES ('CREER_PLAYLIST_AZURA', 'en_attente', %s, NOW())",
                    (json.dumps(params_playlist),)
                )
                playlist_task_ids.append(cursor.lastrowid)

            db.commit()

        cursor.close()
        db.close()

        result = {'status': 'ok', 'task_id': task_id, 'station_id': station_id}
        if playlist_task_ids:
            result['playlist_task_ids'] = playlist_task_ids
        if fichiers_deja_azura:
            result['nb_deja_azura'] = len(fichiers_deja_azura)

        return jsonify(result)

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500




# ── Push : annuler un envoi en cours ──
@azuracast_bp.route('/api/push/cancel', methods=['POST'])
@login_requis
def api_push_cancel():
    """Pose un flag d'annulation pour un push en cours.
    Insère une tâche PUSH_CANCEL en_attente que le worker vérifiera
    dans la boucle SFTP et avant chaque création/import de playlist."""
    try:
        data = request.get_json(force=True)
        push_task_id = data.get('task_id')
        if not push_task_id:
            return jsonify({"error": "task_id requis"}), 400
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
            "VALUES ('PUSH_CANCEL', 'en_attente', %s, NOW())",
            (json.dumps({'push_task_id': push_task_id}),)
        )
        db.commit()
        tid = cursor.lastrowid
        cursor.close()
        db.close()
        return jsonify({"ok": True, "cancel_task_id": tid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@azuracast_bp.route('/lire_log_push')
@login_requis
def lire_log_push():
    task_id = request.args.get('task_id')
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        if task_id:
            cursor.execute(
                "SELECT id, type_action, log_resultat, statut, parametres FROM taches_planifiees WHERE id = %s",
                (task_id,)
            )
        else:
            cursor.execute(
                "SELECT id, type_action, log_resultat, statut, parametres FROM taches_planifiees "
                "WHERE type_action = 'COPIE_FICHIERS' ORDER BY id DESC LIMIT 1"
            )
        tache = cursor.fetchone()

        # Si la tâche est terminée et de type COPIE_FICHIERS, marquer les fichiers comme IN_AZURACAST
        if tache and tache.get('statut') == 'termine' and tache.get('type_action') == 'COPIE_FICHIERS':
            try:
                params = json.loads(tache.get('parametres', '{}')) if tache.get('parametres') else {}
                fichiers = params.get('fichiers', [])
                updated = 0
                for f in fichiers:
                    fpath = f.get('path', '') if isinstance(f, dict) else str(f)
                    if fpath:
                        cursor.execute(
                            "UPDATE songs SET comments = 'IN_AZURACAST' WHERE `path` = %s AND (comments IS NULL OR comments != 'IN_AZURACAST')",
                            (fpath,)
                        )
                        updated += cursor.rowcount
                if updated > 0:
                    db.commit()
            except Exception:
                pass

        cursor.close()
        db.close()

        # IMPORTANT : toujours renvoyer le VRAI statut de la tâche (en_attente /
        # en_cours / termine / erreur), même si log_resultat est encore NULL.
        if not tache:
            return jsonify({"log": "Tâche introuvable.", "statut": "introuvable",
                             "type_action": None})
        vrai_statut = tache.get('statut') or 'inconnu'
        type_action = tache.get('type_action') or ''
        log = tache.get('log_resultat') or ''
        if not log:
            if vrai_statut == 'en_attente':
                log = ("⏳ Tâche en attente — le worker Ubuntu (192.168.1.17) ne l'a pas "
                       "encore prise en charge. Si rien ne bouge après 15-30s, vérifiez "
                       "que le worker tourne et qu'il a été redéployé avec la dernière "
                       "version de worker_ubuntu.py (qui gère le type_action='"
                       + type_action + "').")
            elif vrai_statut == 'en_cours':
                log = ("🔄 Tâche en cours d'exécution par le worker Ubuntu — "
                       "les logs apparaîtront ici dès qu'ils seront écrits en base.")
            else:
                log = "(Aucun log disponible pour cette tâche.)"
        return jsonify({"log": log, "statut": vrai_statut,
                         "type_action": type_action})
    except Exception as e:
        return jsonify({"log": f"Erreur : {e}", "statut": "erreur"})        


# ══════════════════════════════════════════
# ROUTES AZURACAST PLAYLISTS & FOLDERS API
# ══════════════════════════════════════════

@azuracast_bp.route('/api/azuracast/folders')
@login_requis
def api_azuracast_folders():
    """Retourne la liste des dossiers AzuraCast (depuis le cache DB, ou fallback historique tâches).
    Filtre par station si ?station_id= est fourni et que la colonne existe."""
    # Lecture du paramètre station_id (facultatif)
    try:
        station_id = int(request.args.get('station_id', '0'))
    except (ValueError, TypeError):
        station_id = 0

    all_folders = set()

    # 1. Lire depuis la table cache (renseignée par le worker Ubuntu)
    try:
        db = get_db_connection()
        if db:
            cursor = db.cursor()
            # Vérifier que la table cache existe
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = 'azuracast_folders_cache'"
            )
            table_existe = cursor.fetchone()
            table_ok = False
            if isinstance(table_existe, dict):
                table_ok = table_existe.get('COUNT(*)', 0) > 0
            elif isinstance(table_existe, (list, tuple)):
                table_ok = table_existe[0] > 0

            if table_ok:
                # Détecter la colonne station_id (migration multi-station)
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = 'azuracast_folders_cache' "
                    "AND column_name = 'station_id'"
                )
                col_existe = cursor.fetchone()
                has_station_col = False
                if isinstance(col_existe, dict):
                    has_station_col = col_existe.get('COUNT(*)', 0) > 0
                elif isinstance(col_existe, (list, tuple)):
                    has_station_col = col_existe[0] > 0

                # ── STRATÉGIE MULTI-STATION POUR LES DOSSIERS ──
                # Les stations 6 et 7 partagent le MÊME système de fichiers physique
                # (/home/sebastien/music_azuracast6_7/). Les folder_path sont des chemins
                # RELATIFS identiques pour les deux stations.
                # On retourne donc TOUJOURS l'UNION des dossiers de toutes les stations
                # présentes dans le cache, peu importe la station demandée.
                cursor.execute(
                    "SELECT DISTINCT folder_path FROM azuracast_folders_cache "
                    "ORDER BY folder_path ASC"
                )
                rows = cursor.fetchall()
                for row in rows:
                    if isinstance(row, dict):
                        all_folders.add(row.get('folder_path', ''))
                    elif isinstance(row, (list, tuple)):
                        all_folders.add(row[0])
            cursor.close()
            db.close()
    except Exception:
        pass

    # 2. Fallback : dossiers connus dans l'historique des tâches (si cache vide)
    if not all_folders:
        try:
            db = get_db_connection()
            if db:
                cursor = db.cursor()
                cursor.execute(
                    "SELECT parametres FROM taches_planifiees "
                    "WHERE type_action IN ('COPIE_FICHIERS', 'CREER_PLAYLIST_AZURA') "
                    "AND parametres IS NOT NULL "
                    "ORDER BY id DESC LIMIT 500"
                )
                rows = cursor.fetchall()
                cursor.close()
                db.close()
                for row in rows:
                    try:
                        if isinstance(row, dict):
                            params = json.loads(row.get('parametres', '{}'))
                        else:
                            continue
                        d = params.get('dossier_cible', '').strip()
                        if d:
                            all_folders.add(d)
                            parts = d.split('/')
                            for i in range(1, len(parts) + 1):
                                all_folders.add('/'.join(parts[:i]))
                    except (json.JSONDecodeError, AttributeError):
                        continue
        except Exception:
            pass

    # Ajouter le dossier par défaut
    all_folders.add('imports_push')

    result = sorted(all_folders)
    return jsonify(result)


@azuracast_bp.route('/api/marquer_in_azuracast', methods=['POST'])
@login_requis
def api_marquer_in_azuracast():
    """Marque un ou plusieurs titres comme IN_AZURACAST dans la base RadioDJ.
    Appelé par le worker Ubuntu après copie réussie, ou manuellement depuis le dashboard."""
    try:
        data = request.get_json(force=True)
        fichiers = data.get('fichiers', [])  # Liste de chemins fichiers
        ids = data.get('ids', [])  # Liste d'IDs songs

        if not fichiers and not ids:
            return jsonify({'status': 'error', 'message': 'Aucun fichier ou ID fourni.'}), 400

        db = get_db_connection()
        if not db:
            return jsonify({'status': 'error', 'message': 'Connexion DB impossible'}), 500
        cursor = db.cursor()
        updated = 0

        # Marquer par chemin de fichier
        for chemin in fichiers:
            cursor.execute(
                "UPDATE songs SET comments = 'IN_AZURACAST' WHERE `path` = %s AND (comments IS NULL OR comments = '' OR comments != 'IN_AZURACAST')",
                (chemin,)
            )
            updated += cursor.rowcount

        # Marquer par ID
        for sid in ids:
            cursor.execute(
                "UPDATE songs SET comments = 'IN_AZURACAST' WHERE ID = %s AND (comments IS NULL OR comments = '' OR comments != 'IN_AZURACAST')",
                (sid,)
            )
            updated += cursor.rowcount

        db.commit()
        cursor.close()
        db.close()

        return jsonify({'status': 'ok', 'updated': updated})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@azuracast_bp.route('/api/sync_azura_status', methods=['POST'])
@login_requis
def api_sync_azura_status():
    """Crée une tâche SYNC_AZURA_STATUS dans la file pour que le worker Ubuntu
    (qui a internet) compare les fichiers AzuraCast avec RadioDJ et mette à jour
    le champ comments='IN_AZURACAST' (badge global).

    Paramètres (JSON body) :
      - station_ids: liste d'IDs de stations à synchroniser (ex: [6, 7])
                     Si absent → toutes les stations de config.json
      - station_id : alternative (entier unique) — converti en [station_id]
    """
    try:
        data = request.get_json(silent=True) or {}

        # Résoudre la liste des stations à synchroniser
        if 'station_ids' in data and isinstance(data['station_ids'], list):
            station_ids = [int(s) for s in data['station_ids'] if s]
        elif 'station_id' in data and data['station_id']:
            station_ids = [int(data['station_id'])]
        else:
            # Par défaut : toutes les stations configurées
            station_ids = [s['id'] for s in _lister_stations()]

        if not station_ids:
            return jsonify({'status': 'error', 'message': 'Aucune station à synchroniser.'}), 400

        db = get_db_connection()
        if not db:
            return jsonify({'status': 'error', 'message': 'Connexion DB impossible'}), 500
        cursor = db.cursor()

        # Vérifier qu'il n'y a pas déjà une tâche en attente
        cursor.execute(
            "SELECT id FROM taches_planifiees "
            "WHERE type_action = 'SYNC_AZURA_STATUS' AND statut = 'en_attente' "
            "LIMIT 1"
        )
        existing = cursor.fetchone()
        if existing:
            cursor.close()
            db.close()
            return jsonify({'status': 'ok', 'message': 'Une synchronisation est déjà en attente.', 'task_id': existing.get('id') if isinstance(existing, dict) else existing[0]})

        parametres = {
            'station_ids': station_ids,
            'badge_global': True  # Le badge IN_AZURACAST reste global (présent dans au moins une station)
        }
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
            "VALUES ('SYNC_AZURA_STATUS', 'en_attente', %s, NOW())",
            (json.dumps(parametres),)
        )
        db.commit()
        task_id = cursor.lastrowid
        cursor.close()
        db.close()

        # Diagnostic : retourner aussi les stations configurées pour que le
        # frontend puisse afficher un avertissement si station_ids ne couvre
        # pas toutes les stations de config.json
        stations_configurees = _lister_stations()
        ids_configures = [s['id'] for s in stations_configurees]
        ids_manquants = [sid for sid in ids_configures if sid not in station_ids]
        warning = None
        if len(stations_configurees) <= 1:
            warning = (
                "⚠ config.json ne contient qu'une seule station (ou aucune section "
                "'azuracast.stations'). Vérifiez que le fichier config.json côté "
                "Windows contient bien la section 'azuracast.stations' avec les "
                "stations 6 ET 7."
            )
        elif ids_manquants:
            warning = (
                f"⚠ Stations configurées mais NON synchronisées : {ids_manquants}. "
                f"Si vous vouliez synchroniser toutes les stations, sélectionnez "
                f"l'option 'Les deux stations à la suite' dans le menu déroulant."
            )

        return jsonify({
            'status': 'ok',
            'message': f'Tâche de synchronisation créée pour station(s) {station_ids}.',
            'task_id': task_id,
            'station_ids': station_ids,
            'stations_configurees': stations_configurees,
            'warning': warning
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@azuracast_bp.route('/api/azuracast/diagnostic')
@login_requis
def api_azuracast_diagnostic():
    """Diagnostic multi-station : retourne l'état de config.json côté Windows.
    Permet de comprendre pourquoi certaines stations ne sont pas synchronisées."""
    config = _charger_config_json()
    azura = _charger_azura_config()
    stations = _lister_stations()
    # Censure : masquer la clé API pour ne pas l'exposer dans le JSON
    azura_safe = None
    if azura:
        azura_safe = dict(azura)
        if 'api_key' in azura_safe:
            k = str(azura_safe['api_key'])
            azura_safe['api_key'] = (k[:6] + '…' + k[-4:]) if len(k) > 12 else '***'
    return jsonify({
        'status': 'ok',
        'config_json_path': CONFIG_JSON_PATH,
        'config_json_exists': os.path.exists(CONFIG_JSON_PATH),
        'config_keys': list(config.keys()) if isinstance(config, dict) else [],
        'azura_section_present': azura is not None,
        'azura_section': azura_safe,
        'stations_detectees': stations,
        'default_station_id': _station_par_defaut(),
        'nombre_stations': len(stations)
    })


@azuracast_bp.route('/api/azuracast/playlists')
@login_requis
def api_azuracast_playlists():
    """Liste les playlists AzuraCast depuis le cache SQL.
    Filtre par station si la colonne 'station_id' existe dans la table cache
    (migration multi-station) et si le paramètre ?station_id= est fourni.
    Sinon, retourne toutes les playlists (compatibilité descendante)."""
    try:
        # Lecture du paramètre station_id (facultatif)
        try:
            station_id = int(request.args.get('station_id', '0'))
        except (ValueError, TypeError):
            station_id = 0

        db = get_db_connection()
        if not db:
            return jsonify({'error': 'Connexion DB impossible'}), 500
        cursor = db.cursor()

        # Vérifier que la table de cache existe
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'azuracast_playlists_cache'"
        )
        table_existe = cursor.fetchone()
        if not table_existe or (isinstance(table_existe, dict) and table_existe.get('COUNT(*)', 0) == 0) \
                or (isinstance(table_existe, (list, tuple)) and table_existe[0] == 0):
            cursor.close()
            db.close()
            return jsonify([])

        # Détecter la présence de la colonne station_id (migration multi-station)
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

        if has_station_col and station_id > 0:
            cursor.execute(
                "SELECT azura_id, name, type, source, `order`, is_enabled, "
                "nb_tracks, total_duration, date_sync, station_id "
                "FROM azuracast_playlists_cache "
                "WHERE station_id = %s ORDER BY name ASC",
                (station_id,)
            )
        elif has_station_col:
            # station_id non spécifié : tout retourner MAIS avec la colonne station_id
            cursor.execute(
                "SELECT azura_id, name, type, source, `order`, is_enabled, "
                "nb_tracks, total_duration, date_sync, station_id "
                "FROM azuracast_playlists_cache ORDER BY name ASC"
            )
        else:
            # Compatibilité descendante : ancienne table sans colonne station_id
            cursor.execute(
                "SELECT azura_id, name, type, source, `order`, is_enabled, "
                "nb_tracks, total_duration, date_sync "
                "FROM azuracast_playlists_cache ORDER BY name ASC"
            )
        rows = cursor.fetchall()
        cursor.close()
        db.close()

        playlists = []
        for row in rows:
            if isinstance(row, dict):
                item = {
                    'id': row['azura_id'],
                    'name': row['name'],
                    'type': row['type'],
                    'source': row['source'],
                    'order': row['order'],
                    'is_enabled': bool(row['is_enabled']),
                    'nb_tracks': row['nb_tracks'],
                    'total_duration': row['total_duration'],
                    'date_sync': str(row['date_sync']) if row['date_sync'] else None
                }
                if 'station_id' in row:
                    item['station_id'] = row['station_id']
            else:
                item = {
                    'id': row[0],
                    'name': row[1],
                    'type': row[2],
                    'source': row[3],
                    'order': row[4],
                    'is_enabled': bool(row[5]),
                    'nb_tracks': row[6],
                    'total_duration': row[7],
                    'date_sync': str(row[8]) if row[8] else None
                }
                if len(row) > 9:
                    item['station_id'] = row[9]
            playlists.append(item)

        return jsonify(playlists)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@azuracast_bp.route('/api/azuracast/sync_cache', methods=['POST'])
@login_requis
def api_azuracast_sync_cache():
    """Crée une tâche SYNC_PLAYLISTS_CACHE pour rafraîchir le cache des playlists.
    Paramètres (JSON body, facultatifs) :
      - station_id: ID de la station à synchroniser (ex: 6 ou 7)
                    Si absent → toutes les stations configurées sont synchronisées
    """
    try:
        data = request.get_json(silent=True) or {}

        # Résoudre la liste des stations à synchroniser
        if 'station_id' in data and data['station_id']:
            station_ids = [int(data['station_id'])]
        else:
            station_ids = [s['id'] for s in _lister_stations()]

        parametres = {'station_ids': station_ids}

        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
            "VALUES (%s, 'en_attente', %s, NOW())",
            ('SYNC_PLAYLISTS_CACHE', json.dumps(parametres))
        )
        db.commit()
        task_id = cursor.lastrowid
        cursor.close()
        db.close()

        return jsonify({'status': 'ok', 'task_id': task_id, 'station_ids': station_ids})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@azuracast_bp.route('/api/azuracast/creer_playlist', methods=['POST'])
@login_requis
def api_azuracast_creer_playlist():
    try:
        data = request.get_json(force=True)

        nom_playlist = data.get('nom_playlist', '').strip()
        fichiers = data.get('fichiers', [])
        dossier_cible = data.get('dossier_cible', 'imports_push')
        pl_type = data.get('type', 'default')
        pl_order = data.get('order', 'shuffle')

        if not nom_playlist:
            return jsonify({'status': 'error', 'message': 'Nom de playlist requis.'}), 400

        parametres = {
            'nom_playlist': nom_playlist,
            'fichiers': fichiers,
            'dossier_cible': dossier_cible,
            'type': pl_type,
            'order': pl_order
        }

        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
            "VALUES (%s, 'en_attente', %s, NOW())",
            ('CREER_PLAYLIST_AZURA', json.dumps(parametres))
        )
        db.commit()
        task_id = cursor.lastrowid
        cursor.close()
        db.close()

        return jsonify({'status': 'ok', 'task_id': task_id})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@azuracast_bp.route('/api/azuracast/import_m3u', methods=['POST'])
@login_requis
def api_azuracast_import_m3u():
    try:
        data = request.get_json(force=True)

        playlist_id = data.get('playlist_id')
        if not playlist_id:
            return jsonify({'status': 'error', 'message': 'playlist_id requis.'}), 400

        parametres = {
            'playlist_id': int(playlist_id),
            'fichiers': data.get('fichiers', []),
            'dossier_cible': data.get('dossier_cible', 'imports_push'),
            'm3u_content': data.get('m3u_content', '')
        }

        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
            "VALUES (%s, 'en_attente', %s, NOW())",
            ('IMPORT_M3U_PLAYLIST', json.dumps(parametres))
        )
        db.commit()
        task_id = cursor.lastrowid
        cursor.close()
        db.close()

        return jsonify({'status': 'ok', 'task_id': task_id})

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@azuracast_bp.route('/lire_log_playlist')
@login_requis
def lire_log_playlist():
    task_id = request.args.get('task_id', type=int)

    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)

        if task_id:
            cursor.execute(
                "SELECT log_resultat, statut FROM taches_planifiees WHERE id = %s",
                (task_id,)
            )
        else:
            cursor.execute(
                "SELECT log_resultat, statut FROM taches_planifiees "
                "WHERE type_action IN ('CREER_PLAYLIST_AZURA', 'IMPORT_M3U_PLAYLIST', 'SYNC_PLAYLISTS_CACHE') "
                "ORDER BY id DESC LIMIT 1"
            )

        row = cursor.fetchone()
        cursor.close()
        db.close()

        if not row or not row.get('log_resultat'):
            return jsonify({'log': '', 'statut': 'vide'})
        return jsonify({'log': row['log_resultat'], 'statut': row.get('statut', 'inconnu')})

    except Exception as e:
        return jsonify({'log': f'Erreur: {e}', 'statut': 'erreur'})


# ══════════════════════════════════════════
# ROUTES SYNC PLAYLISTS CACHE AZURACAST
# ══════════════════════════════════════════
@azuracast_bp.route('/lancer_sync_playlists_cache', methods=['POST'])
@login_requis
def lancer_sync_playlists_cache():
    """Déclenche une tâche SYNC_PLAYLISTS_CACHE via le worker Ubuntu."""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, parametres, statut, date_creation) "
            "VALUES (%s, %s, 'en_attente', NOW())",
            ('SYNC_PLAYLISTS_CACHE', '{}')
        )
        db.commit()
        task_id = cursor.lastrowid
        cursor.close()
        db.close()
        return jsonify({"status": "queued", "task_id": task_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@azuracast_bp.route('/lire_log_sync_playlists')
@login_requis
def lire_log_sync_playlists():
    """Lit le log de la dernière tâche SYNC_PLAYLISTS_CACHE."""
    task_id = request.args.get('task_id')
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        if task_id:
            cursor.execute(
                "SELECT log_resultat, statut FROM taches_planifiees WHERE id = %s",
                (task_id,)
            )
        else:
            cursor.execute(
                "SELECT log_resultat, statut FROM taches_planifiees "
                "WHERE type_action = 'SYNC_PLAYLISTS_CACHE' ORDER BY id DESC LIMIT 1"
            )
        tache = cursor.fetchone()
        cursor.close()
        db.close()

        if not tache or not tache.get('log_resultat'):
            return jsonify({"log": "En attente du worker Ubuntu...", "statut": "vide"})
        return jsonify({"log": tache['log_resultat'], "statut": tache['statut']})
    except Exception as e:
        return jsonify({"log": f"Erreur : {e}", "statut": "erreur"})


# ═══════════════════════════════════════════════════════════════════════
# ═══ Bulk Schedule AzuraCast (intégration 2026-07-08) ══════════════════
# Modifie en masse la planification de plusieurs playlists AzuraCast.
# Toutes les opérations passent par le worker Ubuntu (tâche BULK_SCHEDULE)
# car la machine Windows n'a pas accès à Internet.
# ═══════════════════════════════════════════════════════════════════════


@azuracast_bp.route('/api/azuracast/bulk_schedule/preview', methods=['POST'])
@login_requis
def api_bulk_schedule_preview():
    """Prévisualise les modifications (dry-run). Passe par le worker."""
    data = request.get_json() or {}
    data['sub_mode'] = 'preview'
    success, result, error = _bulk_insert_and_wait(data)
    if not success:
        return jsonify({'status': 'error', 'message': error}), 500
    if result:
        return jsonify(result)
    return jsonify({'status': 'error', 'message': 'Aucun résultat retourné par le worker'}), 500


@azuracast_bp.route('/api/azuracast/bulk_schedule/apply', methods=['POST'])
@login_requis
def api_bulk_schedule_apply():
    """Applique les modifications. Passe par le worker (avec backup)."""
    data = request.get_json() or {}
    data['sub_mode'] = 'apply'
    success, result, error = _bulk_insert_and_wait(data)
    if not success:
        return jsonify({'status': 'error', 'message': error}), 500
    if result:
        return jsonify(result)
    return jsonify({'status': 'error', 'message': 'Aucun résultat retourné par le worker'}), 500
