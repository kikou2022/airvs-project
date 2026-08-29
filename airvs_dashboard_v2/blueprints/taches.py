import json
import datetime

import pymysql
from flask import Blueprint, jsonify

from utils import get_db_connection, _charger_config_json, _logger
from blueprints.auth import login_requis, token_requis
from config import WORKER_VERSION_ATTENDUE

taches_bp = Blueprint('taches', __name__)


# ══════════════════════════════════════════
# ROUTES PURGE SQL
# ══════════════════════════════════════════
@taches_bp.route('/api/taches_stats')
@login_requis
def api_taches_stats():
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN statut = 'en_attente' THEN 1 ELSE 0 END) AS en_attente,
                SUM(CASE WHEN statut = 'en_cours' THEN 1 ELSE 0 END) AS en_cours,
                SUM(CASE WHEN statut = 'termine' THEN 1 ELSE 0 END) AS termine,
                MIN(date_creation) AS plus_ancienne,
                MAX(date_fin) AS derniere_fin
            FROM taches_planifiees
        """)
        stats = cursor.fetchone()
        cursor.close()
        db.close()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@taches_bp.route('/purger_taches', methods=['POST'])
@login_requis
def purger_taches():
    try:
        import datetime
        db = get_db_connection()
        cursor = db.cursor()
        seuil = datetime.datetime.now() - datetime.timedelta(days=7)
        cursor.execute(
            "DELETE FROM taches_planifiees WHERE statut = 'termine' AND date_fin < %s",
            (seuil,)
        )
        nb = cursor.rowcount
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"status": "success", "message": f"{nb} tâche(s) purgée(s)."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════
# ROUTES SHEETS (lecture config.json)
# ══════════════════════════════════════════
@taches_bp.route('/api/sheets/list')
@login_requis
def api_sheets_list():
    """Retourne la liste des aliases de Sheets Google définis dans config.json.
    Permet au dashboard d'alimenter un <select> au lieu d'un <input> libre."""
    config = _charger_config_json()
    sheets_dict = config.get("sheets", {}) if isinstance(config, dict) else {}
    # Transformer le dict {alias: sheet_id} en liste triée par alias
    sheets = [{"alias": alias, "sheet_id": sid}
              for alias, sid in sheets_dict.items()]
    sheets.sort(key=lambda x: x["alias"])
    return jsonify({
        "status": "ok",
        "sheets": sheets,
        "count": len(sheets),
        "config_present": bool(sheets_dict)
    })


# ══════════════════════════════════════════
# ROUTE DEBUG TÂCHE (inspection complète)
# ══════════════════════════════════════════
@taches_bp.route('/api/tache_debug/<int:task_id>')
@login_requis
def api_tache_debug(task_id):
    """Renvoie le JSON complet d'une tâche (parametres, dates, log, statut).
    Utilisé pour diagnostiquer une tâche qui reste bloquée en 'en_attente'.
    NB : la table taches_planifiees n'a PAS de colonne date_debut — seulement
    date_creation et date_fin (le worker passe directement à 'en_cours' sans
    enregistrer le moment du début)."""
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT id, type_action, statut, parametres, log_resultat, "
            "date_creation, date_fin "
            "FROM taches_planifiees WHERE id = %s",
            (task_id,)
        )
        tache = cursor.fetchone()
        cursor.close()
        db.close()
        if not tache:
            return jsonify({"status": "error", "message": "Tâche introuvable"}), 404
        # Parser parametres (JSON string → dict) pour affichage lisible
        try:
            if tache.get('parametres'):
                tache['parametres'] = json.loads(tache['parametres'])
        except Exception:
            pass
        # Convertir dates en ISO pour sérialisation JSON
        for k in ('date_creation', 'date_fin'):
            v = tache.get(k)
            if v and hasattr(v, 'isoformat'):
                tache[k] = v.isoformat()
        return jsonify({"status": "ok", "tache": tache})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════
# ROUTE WORKER VERSION (vérification déploiement)
# ══════════════════════════════════════════
@taches_bp.route('/api/worker_version')
@login_requis
def api_worker_version():
    """Vérifie la version du worker Ubuntu déployé.
    Lit la table airvs_worker_info (renseignée par le worker au démarrage).
    Permet de détecter un worker qui tourne avec une vieille version."""
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        # Vérifier que la table existe
        cursor.execute(
            "SELECT COUNT(*) AS c FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'airvs_worker_info'"
        )
        row = cursor.fetchone()
        table_existe = (row.get('c', 0) > 0) if isinstance(row, dict) else (row[0] > 0)
        if not table_existe:
            cursor.close()
            db.close()
            return jsonify({
                "status": "ok",
                "worker_deployed": False,
                "version_attendue": WORKER_VERSION_ATTENDUE,
                "version_actuelle": None,
                "demarrage_le": None,
                "hostname": None,
                "message": ("Table airvs_worker_info inexistante — le worker "
                            "Ubuntu tourne avec une version ANTIÉRIEURE à "
                            + WORKER_VERSION_ATTENDUE + " (cette table a été "
                            "introduite dans cette version). Redéployez worker_ubuntu.py "
                            "sur 192.168.1.17 et redémarrez le service."),
                "match": False
            })
        cursor.execute(
            "SELECT version, demarrage_le, hostname "
            "FROM airvs_worker_info WHERE id = 1"
        )
        info = cursor.fetchone()
        cursor.close()
        db.close()
        if not info:
            return jsonify({
                "status": "ok",
                "worker_deployed": False,
                "version_attendue": WORKER_VERSION_ATTENDUE,
                "version_actuelle": None,
                "demarrage_le": None,
                "hostname": None,
                "message": "Table airvs_worker_info vide — worker pas encore démarré avec la nouvelle version.",
                "match": False
            })
        version_actuelle = info.get('version', '')
        demarrage = info.get('demarrage_le')
        if demarrage and hasattr(demarrage, 'isoformat'):
            demarrage = demarrage.isoformat()
        match = (version_actuelle == WORKER_VERSION_ATTENDUE)
        # Calculer l'âge du démarrage (pour vérifier que le worker est vivant)
        message = ""
        if not match:
            message = (f"⚠ Version worker déployée = '{version_actuelle}' mais "
                       f"version attendue = '{WORKER_VERSION_ATTENDUE}'. "
                       f"Redéployez worker_ubuntu.py sur 192.168.1.17 et redémarrez le service.")
        else:
            message = f"✓ Worker à jour (version {version_actuelle}, démarré le {demarrage} sur {info.get('hostname','?')})."
        return jsonify({
            "status": "ok",
            "worker_deployed": True,
            "version_attendue": WORKER_VERSION_ATTENDUE,
            "version_actuelle": version_actuelle,
            "demarrage_le": demarrage,
            "hostname": info.get('hostname'),
            "message": message,
            "match": match
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
