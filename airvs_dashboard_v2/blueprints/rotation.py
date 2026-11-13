# -*- coding: utf-8 -*-
"""
rotation.py — Phase 21.3 : Blueprint Flask de l'onglet Rotation éditoriale.

Le dashboard Windows .39 est volontairement isolé d'internet : il n'atteint
PAS l'API AzuraCast directement. Tout le calcul (list_media, scoring) se fait
côté worker Ubuntu .17 via des tâches planifiées (ROTATION_PREVIEW /
ROTATION_EXECUTE / MAPPING_BOOTSTRAP — priorité interactive GF1) :

  POST /api/rotation/preview        → crée une tâche ROTATION_PREVIEW
  GET  /api/rotation/preview/<tid>  → statut tâche + résultat (rotation_previews)
  POST /api/rotation/execute        → crée une tâche ROTATION_EXECUTE
  GET  /api/rotation/task/<tid>     → statut d'une tâche (log tail)
  GET  /api/rotation/history        → journal_operations_azura (dernières ops)
  GET  /api/rotation/mapping/stats  → qualité du résolveur mapping
  POST /api/rotation/mapping/bootstrap → crée une tâche MAPPING_BOOTSTRAP
  GET  /api/rotation/config         → paramètres par défaut (config.json §rotation)
  GET  /api/rotation/mirror/state   → état du drainer régénération miroir

Mode hard (suppression physique) : refusé par ce blueprint tant que le cadrage
question 6 n'est pas validé (défaut config worker rotation_hard_autorise=false)
→ le worker retombera en soft même si un hard était demandé.
"""

import json
import pymysql
from flask import Blueprint, jsonify, request

from utils import get_db_connection, _charger_config_json
from blueprints.auth import login_requis

rotation_bp = Blueprint('rotation', __name__)

# Paramètres autorisés dans la requête de preview (whitelist)
_PARAMS_PREVIEW = (
    'ratio_rotation', 'duree_protection_jours', 'seuil_play_count_min',
    'seuil_anciennete_jours', 'nb_max_candidats',
    'categories_elegibles', 'dossiers_proteges',
)

_STATIONS_AUTORISEES = (6, 7)


def _station_valide(sid):
    try:
        return int(sid) in _STATIONS_AUTORISEES
    except (ValueError, TypeError):
        return False


def _creer_tache(type_action, parametres_dict):
    """Insère une tâche dans taches_planifiees (pattern blueprints/azuracast.py).

    Returns: (task_id, None) ou (None, message_erreur).
    """
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
            "VALUES (%s, 'en_attente', %s, NOW())",
            (type_action, json.dumps(parametres_dict, ensure_ascii=False))
        )
        db.commit()
        task_id = cursor.lastrowid
        cursor.close()
        db.close()
        return task_id, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _statut_tache(task_id, log_tail=1500):
    """Statut + fin de log d'une tâche (polling UI)."""
    db = get_db_connection()
    cursor = db.cursor(pymysql.cursors.DictCursor)
    cursor.execute(
        "SELECT id, type_action, statut, log_resultat, date_creation, date_fin "
        "FROM taches_planifiees WHERE id = %s",
        (task_id,)
    )
    tache = cursor.fetchone()
    cursor.close()
    db.close()
    if not tache:
        return None
    log = tache.get('log_resultat') or ''
    tache['log_tail'] = log[-log_tail:]
    return tache


def _demander_regen_miroir(raison):
    """Marque le miroir à régénérer (drainé par le thread du worker .17).
    Le dashboard ne fait qu'écrire le drapeau — try/except non bloquant."""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "UPDATE mirror_regen_state SET pending = 1, date_demande = NOW(), "
            "last_raison = %s WHERE id = 1",
            (str(raison)[:255],)
        )
        db.commit()
        cursor.close()
        db.close()
    except Exception:
        pass  # table absente (worker 21.3 pas encore démarré) → non bloquant


# ═══════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════

@rotation_bp.route('/api/rotation/config')
@login_requis
def api_rotation_config():
    """Paramètres de rotation par défaut (config.json §rotation + §automation)
    + autorisation hard (cadrage Q6). Le worker peut surcharger par tâche."""
    cfg = _charger_config_json()
    rot = cfg.get('rotation') or {}
    auto = cfg.get('automation') or {}
    return jsonify({
        'ratio_rotation': rot.get('ratio_rotation', 0.10),
        'duree_protection_jours': rot.get('duree_protection_jours', 30),
        'seuil_play_count_min': rot.get('seuil_play_count_min', 5),
        'seuil_anciennete_jours': rot.get('seuil_anciennete_jours', 60),
        'nb_max_candidats': rot.get('nb_max_candidats', 2000),
        'categories_elegibles': rot.get('categories_elegibles', []),
        'dossiers_proteges': rot.get('dossiers_proteges', []),
        'mode_defaut': 'soft',
        'hard_autorise': bool(auto.get('rotation_hard_autorise', False)),
        'mirror_regen_actif': bool(auto.get('mirror_regen_actif', True)),
        'mirror_regen_configure': bool(auto.get('mirror_regen_export_path', '')),
    })


# ═══════════════════════════════════════════════════════════════════════
# PREVIEW (Phase 9)
# ═══════════════════════════════════════════════════════════════════════

@rotation_bp.route('/api/rotation/preview', methods=['POST'])
@login_requis
def api_rotation_preview():
    """Lance le calcul des candidats (tâche ROTATION_PREVIEW sur .17).

    Body JSON : {station_id, params?: {ratio_rotation, duree_protection_jours,
    seuil_play_count_min, seuil_anciennete_jours, nb_max_candidats,
    categories_elegibles, dossiers_proteges}}
    Returns: {task_id} — puis GET /api/rotation/preview/<task_id> pour le résultat.
    """
    data = request.get_json(silent=True) or {}
    station_id = data.get('station_id', 7)
    if not _station_valide(station_id):
        return jsonify({'erreur': f"station_id invalide ({_STATIONS_AUTORISEES})"}), 400

    params = {}
    for cle in _PARAMS_PREVIEW:
        if cle in (data.get('params') or {}):
            params[cle] = data['params'][cle]

    task_id, err = _creer_tache('ROTATION_PREVIEW', {
        'station_id': int(station_id),
        'params': params,
    })
    if err:
        return jsonify({'erreur': f"création tâche impossible : {err}"}), 500
    return jsonify({'task_id': task_id})


@rotation_bp.route('/api/rotation/preview/<int:task_id>')
@login_requis
def api_rotation_preview_resultat(task_id):
    """Statut de la tâche de preview + résultat si prêt (rotation_previews)."""
    tache = _statut_tache(task_id)
    if not tache:
        return jsonify({'erreur': 'tâche introuvable'}), 404

    reponse = {
        'task_id': task_id,
        'statut': tache['statut'],
        'date_creation': str(tache.get('date_creation') or ''),
        'date_fin': str(tache.get('date_fin') or ''),
        'log_tail': tache.get('log_tail', ''),
    }
    if tache['statut'] != 'termine':
        return jsonify(reponse)

    # Tâche terminée → lire la preview associée (la plus récente pour ce task_id)
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT id, station_id, parametres, config_effective, resultats, "
            "nb_candidats, nb_groupes, statut, date_creation "
            "FROM rotation_previews WHERE task_id = %s "
            "ORDER BY id DESC LIMIT 1",
            (task_id,)
        )
        preview = cursor.fetchone()
        cursor.close()
        db.close()
    except Exception as e:
        return jsonify({**reponse, 'erreur': f"lecture preview : {e}"}), 500

    if not preview:
        reponse['statut'] = 'erreur'
        reponse['erreur'] = "aucune preview associée (voir log_tail)"
        return jsonify(reponse)

    try:
        resultats = json.loads(preview['resultats']) if preview['resultats'] else []
        parametres = json.loads(preview['parametres']) if preview['parametres'] else {}
        config_eff = json.loads(preview['config_effective']) if preview['config_effective'] else {}
    except (json.JSONDecodeError, TypeError):
        resultats, parametres, config_eff = [], {}, {}

    reponse.update({
        'preview_id': preview['id'],
        'station_id': preview['station_id'],
        'statut': preview['statut'],  # pret / erreur
        'nb_candidats': preview['nb_candidats'],
        'nb_groupes': preview['nb_groupes'],
        'parametres': parametres,
        'config_effective': config_eff,
        'groupes': resultats,
        'date_preview': str(preview.get('date_creation') or ''),
    })
    return jsonify(reponse)


# ═══════════════════════════════════════════════════════════════════════
# EXECUTE (Phase 10) — toujours via tâche, jamais direct
# ═══════════════════════════════════════════════════════════════════════

@rotation_bp.route('/api/rotation/execute', methods=['POST'])
@login_requis
def api_rotation_execute():
    """Exécute la rotation sur les song_ids sélectionnés (tâche ROTATION_EXECUTE).

    Body JSON : {station_id, song_ids: [int], mode?: 'soft'|'hard',
                 trigger_source?: str}
    Mode hard : refusé ici si non autorisé (cadrage Q6) — double garde avec
    le worker qui retombe en soft de toute façon.
    """
    data = request.get_json(silent=True) or {}
    station_id = data.get('station_id', 7)
    song_ids = data.get('song_ids') or []
    mode = str(data.get('mode', 'soft')).lower()

    if not _station_valide(station_id):
        return jsonify({'erreur': f"station_id invalide ({_STATIONS_AUTORISEES})"}), 400
    if not isinstance(song_ids, list) or not song_ids:
        return jsonify({'erreur': 'song_ids vide'}), 400
    song_ids = [int(s) for s in song_ids if str(s).strip().lstrip('-').isdigit()]
    if not song_ids:
        return jsonify({'erreur': 'song_ids sans identifiant valide'}), 400

    cfg = _charger_config_json()
    hard_autorise = bool((cfg.get('automation') or {}).get('rotation_hard_autorise', False))
    if mode == 'hard' and not hard_autorise:
        return jsonify({
            'erreur': "mode hard non autorisé (cadrage Phase 21 question 6 en "
                      "réflexion — automation.rotation_hard_autorise=false)",
            'mode_force': 'soft',
        }), 403
    if mode not in ('soft', 'hard'):
        mode = 'soft'

    task_id, err = _creer_tache('ROTATION_EXECUTE', {
        'station_id': int(station_id),
        'song_ids': song_ids,
        'mode': mode,
        'trigger_source': f"dashboard:{data.get('utilisateur', 'ui')}",
    })
    if err:
        return jsonify({'erreur': f"création tâche impossible : {err}"}), 500
    return jsonify({'task_id': task_id, 'mode': mode, 'nb_titres': len(song_ids)})


@rotation_bp.route('/api/rotation/task/<int:task_id>')
@login_requis
def api_rotation_task(task_id):
    """Statut générique d'une tâche rotation/mapping (polling UI)."""
    tache = _statut_tache(task_id)
    if not tache:
        return jsonify({'erreur': 'tâche introuvable'}), 404
    return jsonify({
        'task_id': task_id,
        'type_action': tache['type_action'],
        'statut': tache['statut'],
        'date_creation': str(tache.get('date_creation') or ''),
        'date_fin': str(tache.get('date_fin') or ''),
        'log_tail': tache.get('log_tail', ''),
    })


@rotation_bp.route('/api/rotation/task/last')
@login_requis
def api_rotation_task_last():
    """Hotfix C — dernière tâche Rotation/MAPPING (tous statuts).
    Appelée à l'ouverture de l'onglet pour remplir la carte « Log de la
    dernière tâche worker » (jusqu'ici, elle restait vide car elle n'était
    alimentée que pendant un polling actif de la session courante)."""
    db = get_db_connection()
    cursor = db.cursor(pymysql.cursors.DictCursor)
    cursor.execute(
        "SELECT id FROM taches_planifiees "
        "WHERE type_action IN ('MAPPING_BOOTSTRAP', 'ROTATION_PREVIEW', "
        "       'ROTATION_EXECUTE') "
        "ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    cursor.close()
    db.close()
    if not row:
        return jsonify({'task_id': None})
    tache = _statut_tache(row['id'])
    if not tache:
        return jsonify({'task_id': None})
    return jsonify({
        'task_id': row['id'],
        'type_action': tache['type_action'],
        'statut': tache['statut'],
        'date_creation': str(tache.get('date_creation') or ''),
        'date_fin': str(tache.get('date_fin') or ''),
        'log_tail': tache.get('log_tail', ''),
    })


# ═══════════════════════════════════════════════════════════════════════
# MAPPING (résolveur)
# ═══════════════════════════════════════════════════════════════════════

@rotation_bp.route('/api/rotation/mapping/stats')
@login_requis
def api_rotation_mapping_stats():
    """Qualité du résolveur (par méthode, actifs, couverture)."""
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT methode_matching, COUNT(*) AS total, "
            "SUM(CASE WHEN actif THEN 1 ELSE 0 END) AS actifs, "
            "ROUND(AVG(confiance), 2) AS confiance_moyenne "
            "FROM mapping_radiodj_azura GROUP BY methode_matching"
        )
        par_methode = cursor.fetchall()
        cursor.execute(
            "SELECT COUNT(*) AS n FROM mapping_radiodj_azura WHERE actif = TRUE")
        total_actifs = (cursor.fetchone() or {}).get('n', 0)
        cursor.execute("SELECT COUNT(*) AS n FROM radiodb.songs WHERE song_type = 0")
        total_songs = (cursor.fetchone() or {}).get('n', 0)
        cursor.close()
        db.close()
        taux = round(100.0 * total_actifs / total_songs, 1) if total_songs else 0.0
        return jsonify({
            'par_methode': par_methode,
            'total_actifs': total_actifs,
            'total_songs_radiodj': total_songs,
            'taux_couverture_pct': taux,
        })
    except Exception as e:
        return jsonify({'erreur': f"{type(e).__name__}: {e}",
                        'par_methode': [], 'total_actifs': 0,
                        'taux_couverture_pct': 0.0})


@rotation_bp.route('/api/rotation/mapping/bootstrap', methods=['POST'])
@login_requis
def api_rotation_mapping_bootstrap():
    """Lance le remplissage complet du cache mapping (tâche MAPPING_BOOTSTRAP).

    Body JSON : {station_ids?: [6, 7]} — défaut = toutes les stations config.
    """
    data = request.get_json(silent=True) or {}
    station_ids = data.get('station_ids') or [6, 7]
    if not isinstance(station_ids, list) or \
            not all(_station_valide(s) for s in station_ids):
        return jsonify({'erreur': f"station_ids invalide ({_STATIONS_AUTORISEES})"}), 400
    task_id, err = _creer_tache('MAPPING_BOOTSTRAP', {
        'station_ids': [int(s) for s in station_ids],
    })
    if err:
        return jsonify({'erreur': f"création tâche impossible : {err}"}), 500
    return jsonify({'task_id': task_id})


# ═══════════════════════════════════════════════════════════════════════
# HISTORIQUE (journal_operations_azura)
# ═══════════════════════════════════════════════════════════════════════

@rotation_bp.route('/api/rotation/history')
@login_requis
def api_rotation_history():
    """Dernières opérations journalisées (rotation, resync, miroir, mapping).

    Query: limit (défaut 25, max 100), type_op (filtre optionnel).
    """
    try:
        limit = min(int(request.args.get('limit', 25)), 100)
    except (ValueError, TypeError):
        limit = 25
    filtre = request.args.get('type_op')
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        sql = ("SELECT id, date_op, type_op, station_id, media_id_azura, "
               "song_id_radiodj, triggered_by, task_id, trigger_source, details, "
               "reversible FROM journal_operations_azura")
        params = []
        if filtre:
            sql += " WHERE type_op = %s"
            params.append(filtre)
        sql += " ORDER BY id DESC LIMIT %s"
        params.append(limit)
        cursor.execute(sql, params)
        lignes = cursor.fetchall()
        cursor.close()
        db.close()
        for l in lignes:
            l['date_op'] = str(l.get('date_op') or '')
            if l.get('details') and not isinstance(l['details'], dict):
                try:
                    l['details'] = json.loads(l['details'])
                except (json.JSONDecodeError, TypeError):
                    pass
        return jsonify({'operations': lignes})
    except Exception as e:
        return jsonify({'erreur': f"{type(e).__name__}: {e}", 'operations': []})


# ═══════════════════════════════════════════════════════════════════════
# MIROIR (drainer régénération db.json — mémo §5.1)
# ═══════════════════════════════════════════════════════════════════════

@rotation_bp.route('/api/rotation/mirror/state')
@login_requis
def api_rotation_mirror_state():
    """État du drainer de régénération miroir (pending, dernière regen...)."""
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute("SELECT * FROM mirror_regen_state WHERE id = 1")
        etat = cursor.fetchone()
        cursor.close()
        db.close()
        if not etat:
            return jsonify({'erreur': 'table mirror_regen_state absente '
                                      '(worker 21.3 pas encore démarré)'})
        for cle in ('date_demande', 'last_regen'):
            etat[cle] = str(etat.get(cle) or '')
        return jsonify(etat)
    except Exception as e:
        return jsonify({'erreur': f"{type(e).__name__}: {e}"})
