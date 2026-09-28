import json
import datetime

import pymysql
from flask import Blueprint, jsonify, request

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
    """Purge manuelle des tâches terminées/erronées/annulées.
    
    Body JSON optionnel:
      - retention_days (int, défaut: valeur DB ou 14) : rétention en jours
      - statut (str, défaut: 'termine,erreur,annule') : statuts à purger
        Valeurs possibles: 'termine', 'erreur', 'annule', ou combinaison
        séparée par virgule. 'tous' = les 3.
    """
    try:
        import datetime
        data = request.get_json(silent=True) or {}
        
        # Récupérer rétention (param > DB > défaut 14)
        retention_days = data.get('retention_days')
        if not retention_days:
            # Lire la config DB
            try:
                db_cfg = get_db_connection()
                cur_cfg = db_cfg.cursor()
                cur_cfg.execute(
                    "SELECT purge_retention_jours FROM airvs_maintenance_config WHERE id = 1"
                )
                row = cur_cfg.fetchone()
                cur_cfg.close()
                db_cfg.close()
                retention_days = int(row[0]) if row and row[0] else 14
            except Exception:
                retention_days = 14
        
        # Valider rétention (sécurité : minimum 1 jour)
        try:
            retention_days = max(1, int(retention_days))
        except (TypeError, ValueError):
            retention_days = 14
        
        # Récupérer statuts à purger
        statut_param = data.get('statut', 'termine,erreur,annule')
        if statut_param == 'tous':
            statuts = ['termine', 'erreur', 'annule']
        else:
            # Sécurité : filtrer uniquement les statuts valides
            statuts_valides = {'termine', 'erreur', 'annule'}
            statuts = [s.strip() for s in statut_param.split(',') if s.strip() in statuts_valides]
            if not statuts:
                return jsonify({
                    "status": "error",
                    "message": "Aucun statut valide fourni (termine/erreur/annule)"
                }), 400
        
        seuil = datetime.datetime.now() - datetime.timedelta(days=retention_days)
        placeholders = ','.join(['%s'] * len(statuts))
        
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            f"DELETE FROM taches_planifiees "
            f"WHERE statut IN ({placeholders}) AND date_fin < %s",
            (*statuts, seuil)
        )
        nb = cursor.rowcount
        db.commit()
        cursor.close()
        db.close()
        return jsonify({
            "status": "success",
            "message": f"{nb} tâche(s) purgée(s).",
            "nb_purged": nb,
            "retention_days": retention_days,
            "statuts": statuts
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ════════════════════════════════════════
# ROUTES MAINTENANCE CONFIG (2026.09.28)
# ════════════════════════════════════════
@taches_bp.route('/api/maintenance/config')
@login_requis
def api_maintenance_config():
    """Lit la configuration de maintenance (rétention purge, activation)."""
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        # Vérifier que la table existe
        cursor.execute(
            "SELECT COUNT(*) AS c FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'airvs_maintenance_config'"
        )
        row = cursor.fetchone()
        table_existe = (row.get('c', 0) > 0) if isinstance(row, dict) else False
        if not table_existe:
            # La table sera créée au prochain démarrage du worker
            return jsonify({
                "status": "ok",
                "config_present": False,
                "purge_retention_jours": 14,
                "purge_auto_active": True,
                "purge_interval_secondes": 21600,
                "derniere_purge_le": None,
                "message": "Table airvs_maintenance_config inexistante — sera créée au prochain démarrage du worker."
            })
        cursor.execute(
            "SELECT purge_retention_jours, purge_auto_active, "
            "purge_interval_secondes, derniere_purge_le, maj_le "
            "FROM airvs_maintenance_config WHERE id = 1"
        )
        cfg = cursor.fetchone()
        cursor.close()
        db.close()
        if not cfg:
            return jsonify({
                "status": "ok",
                "config_present": False,
                "purge_retention_jours": 14,
                "purge_auto_active": True,
                "purge_interval_secondes": 21600,
                "derniere_purge_le": None
            })
        # Convertir les dates en ISO
        for k in ('derniere_purge_le', 'maj_le'):
            v = cfg.get(k)
            if v and hasattr(v, 'isoformat'):
                cfg[k] = v.isoformat()
        return jsonify({
            "status": "ok",
            "config_present": True,
            **cfg
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@taches_bp.route('/api/maintenance/config', methods=['POST'])
@login_requis
def api_maintenance_config_update():
    """Met à jour la configuration de maintenance.
    
    Body JSON:
      - purge_retention_jours (int, 1-365) : rétention en jours
      - purge_auto_active (bool) : active/désactive la purge auto du worker
      - purge_interval_secondes (int, 3600-86400) : intervalle entre 2 purges
    """
    try:
        data = request.get_json(silent=True) or {}
        updates = []
        params = []
        
        if 'purge_retention_jours' in data:
            try:
                v = int(data['purge_retention_jours'])
                if not (1 <= v <= 365):
                    return jsonify({
                        "status": "error",
                        "message": "purge_retention_jours doit être entre 1 et 365"
                    }), 400
                updates.append("purge_retention_jours = %s")
                params.append(v)
            except (TypeError, ValueError):
                return jsonify({
                    "status": "error",
                    "message": "purge_retention_jours doit être un entier"
                }), 400
        
        if 'purge_auto_active' in data:
            v = 1 if data['purge_auto_active'] else 0
            updates.append("purge_auto_active = %s")
            params.append(v)
        
        if 'purge_interval_secondes' in data:
            try:
                v = int(data['purge_interval_secondes'])
                if not (3600 <= v <= 86400):
                    return jsonify({
                        "status": "error",
                        "message": "purge_interval_secondes doit être entre 3600 (1h) et 86400 (24h)"
                    }), 400
                updates.append("purge_interval_secondes = %s")
                params.append(v)
            except (TypeError, ValueError):
                return jsonify({
                    "status": "error",
                    "message": "purge_interval_secondes doit être un entier"
                }), 400
        
        if not updates:
            return jsonify({
                "status": "error",
                "message": "Aucun champ à mettre à jour. Champs acceptés: "
                           "purge_retention_jours, purge_auto_active, purge_interval_secondes"
            }), 400
        
        # Vérifier que la table existe
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'airvs_maintenance_config'"
        )
        if not cursor.fetchone()[0]:
            cursor.close()
            db.close()
            return jsonify({
                "status": "error",
                "message": "Table airvs_maintenance_config inexistante. Démarrez/redémarrez le worker Ubuntu pour la créer."
            }), 409
        
        # S'assurer que la ligne id=1 existe
        cursor.execute(
            "INSERT IGNORE INTO airvs_maintenance_config (id) VALUES (1)"
        )
        
        query = f"UPDATE airvs_maintenance_config SET {', '.join(updates)} WHERE id = 1"
        cursor.execute(query, params)
        db.commit()
        cursor.close()
        db.close()
        return jsonify({
            "status": "success",
            "message": "Configuration de maintenance mise à jour.",
            "updated_fields": [u.split(' = ')[0] for u in updates]
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@taches_bp.route('/api/maintenance/tables_sizes')
@login_requis
def api_maintenance_tables_sizes():
    """Retourne la taille de toutes les tables de la base airvs_dashboard."""
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute("""
            SELECT 
                table_name AS 'table',
                ROUND(data_length/1024/1024, 2) AS 'data_mib',
                ROUND(index_length/1024/1024, 2) AS 'index_mib',
                ROUND((data_length + index_length)/1024/1024, 2) AS 'total_mib',
                table_rows AS 'rows',
                engine AS 'engine'
            FROM information_schema.tables 
            WHERE table_schema = DATABASE()
            ORDER BY (data_length + index_length) DESC
        """)
        tables = cursor.fetchall()
        # Calculer le total
        total = sum(t.get('total_mib', 0) or 0 for t in tables)
        cursor.close()
        db.close()
        return jsonify({
            "status": "ok",
            "total_mib": round(total, 2),
            "nb_tables": len(tables),
            "tables": tables
        })
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
