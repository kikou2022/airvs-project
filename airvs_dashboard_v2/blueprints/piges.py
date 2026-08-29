"""blueprints/piges.py -- Piges, promotions et podcasts.

Routes extraites de app.py pour le blueprint piges_bp.

Gestion des piges (enregistrements radio), extraction de segments,
promotion vers podcasts Azuracast, et consultation des épisodes.

Helper :
  - _worker_task_proxy : insère une tâche worker et attend le résultat
    par polling (utilisé aussi par les routes audience dans app.py).
  - _serialize_promo : sérialisation datetime/timedelta pour jsonify.

Routes :
  - GET  /api/piges/dates
  - GET  /api/piges/list/<date_str>
  - POST /api/piges/extract
  - POST /api/piges/upload_artwork
  - POST /api/piges/promote
  - GET  /api/piges/promote/status/<int:task_id>
  - GET  /api/piges/promotions/recent
  - DELETE /api/piges/promotions/<int:promo_id>
  - POST /api/piges/promotions/purge_errors
  - GET  /api/podcasts/list
  - GET  /api/podcasts/episodes/<podcast_id>
"""

import os
import re
import json
import time

import pymysql

from datetime import date, timedelta, datetime

from flask import Blueprint, request, jsonify, session

from config import PIGES_BASE_PATH, PIGES_ARTWORK_WINDOWS_DIR
from utils import get_db_connection
from blueprints.auth import login_requis


piges_bp = Blueprint('piges_bp', __name__)


def _worker_task_proxy(type_action, parametres, timeout=20):
    """Insère une tâche pour le worker Ubuntu et attend son résultat JSON.
    Version générique factorisée depuis _audience_proxy — réutilisable par
    toute nouvelle tâche worker suivant le pattern insertion + polling.

    Returns: (task_id, response_body, http_status)
    """
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, parametres, statut) VALUES (%s, %s, 'en_attente')",
            (type_action, json.dumps(parametres))
        )
        db.commit()
        task_id = cursor.lastrowid
        cursor.close()
        db.close()
    except Exception as e:
        return None, {"error": f"Erreur création tâche: {e}"}, 500

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.3)
        try:
            db = get_db_connection()
            cursor = db.cursor(pymysql.cursors.DictCursor)
            cursor.execute(
                "SELECT statut, log_resultat FROM taches_planifiees WHERE id = %s",
                (task_id,)
            )
            tache = cursor.fetchone()
            cursor.close()
            db.close()
        except Exception:
            continue

        if not tache:
            continue
        if tache['statut'] == 'termine' and tache['log_resultat']:
            try:
                return task_id, json.loads(tache['log_resultat']), 200
            except json.JSONDecodeError:
                return task_id, {"error": "Réponse worker invalide (non-JSON)"}, 502
        if tache['statut'] == 'erreur':
            return task_id, {"error": "Tâche en erreur — voir logs worker Ubuntu"}, 503

    return task_id, {"error": "Timeout — le worker n'a pas répondu à temps"}, 504


# ══════════════════════════════════════════
# ROUTES PIGES → PODCASTS (Évolution 1, Phase 1A)
# ══════════════════════════════════════════


@piges_bp.route('/api/piges/dates')
@login_requis
def api_piges_dates():
    """Liste des dates disponibles pour les piges (les 4 derniers jours,
    aujourd'hui inclus). Purement calculée côté dashboard, pas besoin du
    worker pour cette route."""
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(4)]
    return jsonify({"dates": dates})


@piges_bp.route('/api/piges/list/<date_str>')
@login_requis
def api_piges_list(date_str):
    """Liste les fichiers de piges d'une date donnée sur le VPS OVH 1,
    via la tâche worker PIGE_LIST (SSH + stat + ffprobe)."""
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return jsonify({"error": "Format de date invalide (attendu YYYY-MM-DD)"}), 400

    _, body, status = _worker_task_proxy('PIGE_LIST', {"date": date_str}, timeout=20)
    return jsonify(body), status


@piges_bp.route('/api/piges/extract', methods=['POST'])
@login_requis
def api_piges_extract():
    """Évolution 1, Phase 1C — Extrait un segment d'une pige via ffmpeg
    (prévisualisation d'une découpe, sans promotion podcast)."""
    data = request.get_json(silent=True) or {}
    source_date = data.get('source_date', '')
    source_file = data.get('source_file', '')
    start_time = data.get('start_time', '00:00:00')
    duration = data.get('duration', '00:10:00')

    if not re.match(r'^\d{4}-\d{2}-\d{2}$', source_date):
        return jsonify({"error": "source_date invalide (attendu YYYY-MM-DD)"}), 400
    if not source_file or '/' in source_file or '..' in source_file:
        return jsonify({"error": "source_file invalide"}), 400

    remote_path = f"{PIGES_BASE_PATH}/{source_date}/{source_file}"
    _, body, status = _worker_task_proxy('PIGE_EXTRACT', {
        "source_file": remote_path,
        "start_time": start_time,
        "duration": duration,
    }, timeout=200)  # ffmpeg peut prendre du temps sur 1h de son
    return jsonify(body), status


@piges_bp.route('/api/piges/upload_artwork', methods=['POST'])
@login_requis
def api_piges_upload_artwork():
    """Reçoit l'image d'artwork depuis le navigateur et l'enregistre sur un
    lecteur réseau accessible au worker (celui-ci n'a pas d'autre moyen de
    lire un fichier envoyé au dashboard, la machine Windows n'ayant pas
    d'accès Internet direct pour le lui transmettre autrement)."""
    if 'file' not in request.files:
        return jsonify({"error": "Aucun fichier"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Nom de fichier vide"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png'):
        return jsonify({"error": "Format non supporté (JPG/PNG uniquement)"}), 400

    try:
        os.makedirs(PIGES_ARTWORK_WINDOWS_DIR, exist_ok=True)
    except OSError as e:
        return jsonify({"error": f"Dossier artwork inaccessible ({PIGES_ARTWORK_WINDOWS_DIR}) : {e}"}), 500

    nom_fichier = f"artwork_{int(time.time())}{ext}"
    chemin_windows = os.path.join(PIGES_ARTWORK_WINDOWS_DIR, nom_fichier)
    file.save(chemin_windows)

    return jsonify({"artwork_windows_path": chemin_windows, "filename": nom_fichier})


@piges_bp.route('/api/piges/promote', methods=['POST'])
@login_requis
def api_piges_promote():
    """Évolution 1, Phase 1D/1E — Crée une promotion pige -> podcast.
    Insère la ligne de suivi (airvs_piges_promotions) puis la tâche worker
    PIGE_PROMOTE_PODCAST, sans attendre le résultat (l'upload + la création
    d'épisode peuvent prendre plusieurs minutes) — le frontend poll ensuite
    /api/piges/promote/status/<task_id>."""
    data = request.get_json(silent=True) or {}

    source_date = data.get('source_date', '')
    source_file = data.get('source_file', '')
    podcast_id = data.get('podcast_id')
    title = (data.get('title') or '').strip()
    description = data.get('description', '')
    # int ou None uniquement — Azuracast rejette une string même numérique
    # (ex: erreur 500 "must be one of int, null (string given)").
    def _to_int_or_none(v):
        if v is None or v == '':
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    episode_number = _to_int_or_none(data.get('episode_number'))
    season_number = _to_int_or_none(data.get('season_number'))
    publish_date = data.get('publish_date')  # ISO 8601, ex: 2026-07-15T15:00:00
    category = data.get('category', '')
    explicit = bool(data.get('explicit', False))
    extract = data.get('extract') or {"enabled": False}
    artwork_windows_path = data.get('artwork_windows_path')

    if not re.match(r'^\d{4}-\d{2}-\d{2}$', source_date):
        return jsonify({"error": "source_date invalide (attendu YYYY-MM-DD)"}), 400
    if not source_file or '/' in source_file or '..' in source_file:
        return jsonify({"error": "source_file invalide"}), 400
    if not podcast_id or not title:
        return jsonify({"error": "podcast_id et title sont obligatoires"}), 400
    if not publish_date:
        return jsonify({"error": "publish_date est obligatoire"}), 400

    # Normalisation : accepte soit "YYYY-MM-DDTHH:MM" (ancien format brut du
    # <input type="datetime-local">), soit un ISO complet avec millisecondes
    # "YYYY-MM-DDTHH:MM:SS.sssZ" (format envoyé par le JS depuis la correction
    # du bug de fuseau horaire, via Date.toISOString()). On ramène toujours
    # au format "YYYY-MM-DDTHH:MM:SS" + 'Z', sans fraction de seconde.
    pd_clean = publish_date.replace('Z', '')
    if '.' in pd_clean:
        pd_clean = pd_clean.split('.')[0]
    if len(pd_clean) == 16:  # "YYYY-MM-DDTHH:MM"
        pd_clean += ':00'
    publish_date_iso = pd_clean + 'Z'
    publish_date_sql = pd_clean.replace('T', ' ')[:19]

    remote_path = f"{PIGES_BASE_PATH}/{source_date}/{source_file}"

    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("""
            INSERT INTO airvs_piges_promotions
                (source_date, source_file, extract_enabled, extract_start, extract_duration,
                 podcast_id, title, description, episode_number, season_number,
                 publish_date, category, explicit, artwork_path, status, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'en_attente', %s)
        """, (
            source_date, source_file, bool(extract.get('enabled')),
            extract.get('start_time'), extract.get('duration'),
            podcast_id, title, description, episode_number, season_number,
            publish_date_sql, category, explicit,
            artwork_windows_path, session.get('user', 'inconnu')
        ))
        db.commit()
        promo_id = cursor.lastrowid
        cursor.close()
        db.close()
    except Exception as e:
        return jsonify({"error": f"Erreur création promotion: {e}"}), 500

    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, parametres, statut) VALUES (%s, %s, 'en_attente')",
            ('PIGE_PROMOTE_PODCAST', json.dumps({
                "promo_id": promo_id,
                "source_file": remote_path,
                "extract": extract,
                "artwork_windows_path": artwork_windows_path,
                "podcast_metadata": {
                    "podcast_id": podcast_id,
                    "title": title,
                    "description": description,
                    "episode_number": episode_number,
                    "season_number": season_number,
                    "publish_date": publish_date_iso,
                    "category": category,
                    "explicit": explicit,
                },
            }))
        )
        db.commit()
        task_id = cursor.lastrowid
        cursor.execute(
            "UPDATE airvs_piges_promotions SET task_id = %s WHERE id = %s",
            (task_id, promo_id)
        )
        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        return jsonify({"error": f"Erreur création tâche worker: {e}"}), 500

    return jsonify({"promo_id": promo_id, "task_id": task_id, "status": "en_attente"})


def _serialize_promo(promo):
    """Convertit les champs date/datetime/timedelta d'une ligne
    airvs_piges_promotions en chaînes, pour éviter que jsonify() ne plante :
    Flask ne sait pas sérialiser un datetime.timedelta nativement (ce que
    pymysql renvoie pour une colonne TIME, ex: extract_start/extract_duration).
    Cette erreur produisait une page d'erreur HTML au lieu du JSON attendu,
    ce que le frontend interprétait comme un échec de parsing silencieux
    (la promotion restait 'en cours' indéfiniment côté UI malgré un succès
    réel en base)."""
    from datetime import date, datetime, timedelta
    safe = {}
    for k, v in promo.items():
        if isinstance(v, (datetime, date)):
            safe[k] = v.isoformat()
        elif isinstance(v, timedelta):
            safe[k] = str(v)
        else:
            safe[k] = v
    return safe


@piges_bp.route('/api/piges/promote/status/<int:task_id>')
@login_requis
def api_piges_promote_status(task_id):
    """Statut d'une promotion en cours, identifiée par son task_id."""
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT * FROM airvs_piges_promotions WHERE task_id = %s", (task_id,)
        )
        promo = cursor.fetchone()
        cursor.close()
        db.close()
    except Exception as e:
        return jsonify({"error": f"Erreur lecture statut: {e}"}), 500

    if not promo:
        return jsonify({"error": "Promotion introuvable pour ce task_id"}), 404
    return jsonify(_serialize_promo(promo))


@piges_bp.route('/api/piges/promotions/recent')
@login_requis
def api_piges_promotions_recent():
    """Les N dernières promotions (pour le panneau 'Statut des promotions
    récentes' de l'onglet Piges)."""
    limit = request.args.get('limit', 10, type=int)
    limit = min(max(limit, 1), 50)
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT * FROM airvs_piges_promotions ORDER BY id DESC LIMIT %s", (limit,)
        )
        rows = cursor.fetchall()
        cursor.close()
        db.close()
    except Exception as e:
        return jsonify({"error": f"Erreur lecture promotions: {e}"}), 500
    return jsonify({"promotions": [_serialize_promo(r) for r in rows]})


@piges_bp.route('/api/piges/promotions/<int:promo_id>', methods=['DELETE'])
@login_requis
def api_piges_promotions_delete(promo_id):
    """Supprime une ligne de suivi de promotion (historique uniquement —
    ne touche pas à l'épisode déjà publié sur Azuracast le cas échéant).
    Refuse la suppression d'une promotion encore en cours pour éviter de
    perdre le suivi d'une tâche active."""
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute("SELECT status FROM airvs_piges_promotions WHERE id = %s", (promo_id,))
        promo = cursor.fetchone()
        if not promo:
            cursor.close()
            db.close()
            return jsonify({"error": "Promotion introuvable"}), 404
        if promo['status'] in ('en_attente', 'en_cours'):
            cursor.close()
            db.close()
            return jsonify({"error": "Impossible de supprimer une promotion encore en cours"}), 409
        cursor.execute("DELETE FROM airvs_piges_promotions WHERE id = %s", (promo_id,))
        db.commit()
        cursor.close()
        db.close()
    except Exception as e:
        return jsonify({"error": f"Erreur suppression: {e}"}), 500
    return jsonify({"deleted": promo_id})


@piges_bp.route('/api/piges/promotions/purge_errors', methods=['POST'])
@login_requis
def api_piges_promotions_purge_errors():
    """Supprime en une fois toutes les promotions au statut 'erreur'
    (nettoyage des essais/échecs de mise au point, cf. onglet Piges)."""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM airvs_piges_promotions WHERE status = 'erreur'")
        db.commit()
        nb_supprimees = cursor.rowcount
        cursor.close()
        db.close()
    except Exception as e:
        return jsonify({"error": f"Erreur purge: {e}"}), 500
    return jsonify({"deleted_count": nb_supprimees})


@piges_bp.route('/api/podcasts/list')
@login_requis
def api_podcasts_list():
    """Liste les podcasts définis sur Azuracast #2 (pour le sélecteur du
    formulaire de promotion)."""
    _, body, status = _worker_task_proxy('PODCASTS_LIST', {}, timeout=20)
    return jsonify(body), status


@piges_bp.route('/api/podcasts/episodes/<podcast_id>')
@login_requis
def api_podcasts_episodes(podcast_id):
    """Liste les épisodes d'un podcast Azuracast #2 donné."""
    _, body, status = _worker_task_proxy('PODCASTS_EPISODES', {"podcast_id": podcast_id}, timeout=20)
    return jsonify(body), status
