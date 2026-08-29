"""blueprints/programmation.py -- Programmation avancée + Pool + Auto-check AIRVS.

Routes extraites de app.py pour le blueprint programmation_bp.

Programmation avancée :
  - CRUD créneaux (list, create, update, delete)
  - Dry-run, test pool, forcer lancement
  - Statistiques détaillées, historique
  - Import depuis Google Sheets

Pool de titres validés :
  - Validation, migration/réparation schéma
  - Stats, résultats paginés, export CSV
  - Auto-validations (list, upsert, toggle, delete)

Auto-check Google Sheets :
  - Config (get/save), trigger manuel
  - Notifications, dernier résultat
"""

import json
import os
import re
import time
import threading
import traceback
import csv
import io
from datetime import datetime

import pymysql
from flask import Blueprint, request, jsonify, session, Response

from utils import get_db_connection, _logger, _charger_config_json, _assurer_schema_airvs_avance
from blueprints.auth import login_requis


programmation_bp = Blueprint('programmation', __name__)


# ─── Helpers ────────────────────────────────────────────────────────────

def _construire_requete_avance(type_critere, valeur_critere, titres_ids):
    """Retourne (sql_select, sql_count, params_select, params_count, is_titre_mode).

    sql_select : SELECT s.ID, s.artist, s.title, s.album, s.year, s.path, s.comments
                 FROM ... WHERE ... (sans ORDER BY / LIMIT / anti-rép)
    sql_count  : SELECT COUNT(*) AS total FROM ... WHERE ... (même filtre)
    params_*   : listes de paramètres (sans ORDER/LIMIT/anti-rép)
    is_titre_mode : True si type_critere='titre' (pas de RAND/LIMIT)
    """
    cols = ("SELECT s.ID, s.artist, s.title, s.album, s.year, s.`path`, s.comments "
            "FROM songs s ")

    if type_critere == 'titre':
        if not titres_ids:
            raise ValueError("type_critere=titre mais titres_ids vide")
        placeholders = ','.join(['%s'] * len(titres_ids))
        sql_sel = cols + f"WHERE s.song_type = 0 AND s.ID IN ({placeholders})"
        sql_cnt = (f"SELECT COUNT(*) AS total FROM songs s "
                   f"WHERE s.song_type = 0 AND s.ID IN ({placeholders})")
        return sql_sel, sql_cnt, list(titres_ids), list(titres_ids), True

    if type_critere == 'keyword':
        like = f"%{valeur_critere}%"
        sql_sel = (cols + "WHERE s.song_type = 0 "
                   "AND (s.artist LIKE %s OR s.title LIKE %s OR s.album LIKE %s)")
        sql_cnt = ("SELECT COUNT(*) AS total FROM songs s WHERE s.song_type = 0 "
                   "AND (s.artist LIKE %s OR s.title LIKE %s OR s.album LIKE %s)")
        return sql_sel, sql_cnt, [like, like, like], [like, like, like], False

    if type_critere == 'artist':
        sql_sel = cols + "WHERE s.song_type = 0 AND s.artist = %s"
        sql_cnt = "SELECT COUNT(*) AS total FROM songs s WHERE s.song_type = 0 AND s.artist = %s"
        return sql_sel, sql_cnt, [valeur_critere], [valeur_critere], False

    if type_critere == 'genre':
        sql_sel = (cols + "JOIN genre g ON s.id_genre = g.ID "
                   "WHERE s.song_type = 0 AND g.name = %s")
        sql_cnt = ("SELECT COUNT(*) AS total FROM songs s "
                   "JOIN genre g ON s.id_genre = g.ID "
                   "WHERE s.song_type = 0 AND g.name = %s")
        return sql_sel, sql_cnt, [valeur_critere], [valeur_critere], False

    if type_critere == 'subcategory':
        sql_sel = (cols + "JOIN subcategory sc ON s.id_subcat = sc.ID "
                   "WHERE s.song_type = 0 AND sc.name = %s")
        sql_cnt = ("SELECT COUNT(*) AS total FROM songs s "
                   "JOIN subcategory sc ON s.id_subcat = sc.ID "
                   "WHERE s.song_type = 0 AND sc.name = %s")
        return sql_sel, sql_cnt, [valeur_critere], [valeur_critere], False

    if type_critere == 'category':
        # category n'a pas de FK directe dans songs : on passe par subcategory.parentid
        sql_sel = (cols
                   + "JOIN subcategory sc ON s.id_subcat = sc.ID "
                   + "JOIN category c ON sc.parentid = c.ID "
                   + "WHERE s.song_type = 0 AND c.name = %s")
        sql_cnt = ("SELECT COUNT(*) AS total FROM songs s "
                   "JOIN subcategory sc ON s.id_subcat = sc.ID "
                   "JOIN category c ON sc.parentid = c.ID "
                   "WHERE s.song_type = 0 AND c.name = %s")
        return sql_sel, sql_cnt, [valeur_critere], [valeur_critere], False

    if type_critere == 'pool':
        # VALEUR contient l'id_pool (ex: "titres_laurent/CRENEAUX_PROG/S27")
        sql_sel = (cols
                   + "JOIN airvs_pool_titres p ON s.ID = p.song_id "
                   + "WHERE s.song_type = 0 AND p.id_pool = %s AND p.statut = 'valide'")
        sql_cnt = ("SELECT COUNT(*) AS total FROM songs s "
                   "JOIN airvs_pool_titres p ON s.ID = p.song_id "
                   "WHERE s.song_type = 0 AND p.id_pool = %s AND p.statut = 'valide'")
        return sql_sel, sql_cnt, [valeur_critere], [valeur_critere], False

    raise ValueError(f"type_critere invalide: {type_critere!r}")


def _calculer_prochaine_execution(frequence, horaires):
    """Calcule la prochaine date/heure d'exécution en fonction de la fréquence et des horaires."""
    from datetime import datetime, timedelta
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    # Trier les horaires
    horaires_sorted = sorted(horaires)
    for h in horaires_sorted:
        target = datetime.strptime(f"{today_str} {h}", "%Y-%m-%d %H:%M")
        if target > now:
            return target.strftime('%Y-%m-%d %H:%M:%S')
    # Tous les horaires sont passés aujourd'hui → prochain jour, 1er horaire
    if horaires_sorted:
        target = datetime.strptime(f"{today_str} {horaires_sorted[0]}", "%Y-%m-%d %H:%M")
        target += timedelta(days=1)
        return target.strftime('%Y-%m-%d %H:%M:%S')
    return None


# ─── Routes : Programmation avancée ─────────────────────────────────────

@programmation_bp.route('/api/programmation_avance')
@programmation_bp.route('/api/programmation_couleur')  # alias legacy (frontend utilise l'ancien nom)
@login_requis
def api_programmation_avance_list():
    """Liste tous les créneaux de programmation avancée."""
    if not _assurer_schema_airvs_avance():
        return jsonify({"status": "error", "message": "Schéma SQL inaccessible"}), 500
    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT * FROM airvs_grille_avance ORDER BY "
            "IF(jour_semaine IS NULL, 99, jour_semaine), heure ASC"
        )
        rows = cursor.fetchall()
        cursor.close()
        db.close()
        # Convertir les objets non-sérialisables
        for r in rows:
            if r.get('heure') is not None:
                r['heure'] = str(r['heure'])
            if r.get('date_debut') is not None:
                r['date_debut'] = r['date_debut'].isoformat() if hasattr(r['date_debut'], 'isoformat') else str(r['date_debut'])
            if r.get('date_fin') is not None:
                r['date_fin'] = r['date_fin'].isoformat() if hasattr(r['date_fin'], 'isoformat') else str(r['date_fin'])
            if r.get('plage_h_debut') is not None:
                r['plage_h_debut'] = str(r['plage_h_debut'])
            if r.get('plage_h_fin') is not None:
                r['plage_h_fin'] = str(r['plage_h_fin'])
            if r.get('dernier_lancement') is not None:
                r['dernier_lancement'] = r['dernier_lancement'].isoformat() if hasattr(r['dernier_lancement'], 'isoformat') else str(r['dernier_lancement'])
            if r.get('date_creation') is not None:
                r['date_creation'] = r['date_creation'].isoformat() if hasattr(r['date_creation'], 'isoformat') else str(r['date_creation'])
            # Parser playlist_ids JSON -> liste
            if r.get('playlist_ids') is not None and isinstance(r['playlist_ids'], str):
                try:
                    r['playlist_ids'] = json.loads(r['playlist_ids'])
                except (json.JSONDecodeError, TypeError):
                    pass
            # Parser titres_ids JSON -> liste
            if r.get('titres_ids') is not None and isinstance(r['titres_ids'], str):
                try:
                    r['titres_ids'] = json.loads(r['titres_ids'])
                except (json.JSONDecodeError, TypeError):
                    pass
            r['actif'] = bool(r.get('actif', 0))
            r['dry_run'] = bool(r.get('dry_run', 1))
        return jsonify(rows)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/programmation_avance', methods=['POST'])
@programmation_bp.route('/api/programmation_couleur', methods=['POST'])  # alias legacy
@login_requis
def api_programmation_avance_create():
    """Crée un nouveau créneau de programmation avancée."""
    if not _assurer_schema_airvs_avance():
        return jsonify({"status": "error", "message": "Schéma SQL inaccessible"}), 500
    try:
        data = request.get_json(force=True, silent=True) or {}
        # Champs obligatoires
        heure = (data.get('heure') or '').strip()
        type_critere = (data.get('type_critere') or '').strip()
        valeur_critere = (data.get('valeur_critere') or '').strip()
        if not heure or not type_critere:
            return jsonify({"status": "error",
                            "message": "Champs requis manquants: heure, type_critere"}), 400
        if type_critere not in ('artist', 'genre', 'subcategory', 'category', 'titre', 'keyword'):
            return jsonify({"status": "error", "message": "type_critere invalide"}), 400
        # Cas particulier : type=titre → valeur_critere peut être vide mais titres_ids doit être non vide
        titres_ids = data.get('titres_ids', [])
        if isinstance(titres_ids, str):
            try:
                titres_ids = json.loads(titres_ids)
            except json.JSONDecodeError:
                titres_ids = []
        if type_critere == 'titre':
            if not titres_ids or not isinstance(titres_ids, list):
                return jsonify({"status": "error",
                                "message": "type_critere=titre nécessite titres_ids (liste non vide)"}), 400
            if not valeur_critere:
                valeur_critere = f"{len(titres_ids)} titre(s) sélectionné(s)"
        elif not valeur_critere:
            return jsonify({"status": "error",
                            "message": "Champ requis manquant: valeur_critere"}), 400

        # Champs optionnels avec defaults
        jour_semaine = data.get('jour_semaine', 0)
        if jour_semaine is not None:
            try:
                jour_semaine = int(jour_semaine)
                if jour_semaine < 0 or jour_semaine > 7:
                    raise ValueError
            except (ValueError, TypeError):
                return jsonify({"status": "error",
                                "message": "jour_semaine doit être 0-7 (0=tous les jours)"}), 400
        date_debut = data.get('date_debut') or None
        date_fin = data.get('date_fin') or None
        station_id = int(data.get('station_id', 7))
        nb_titres = int(data.get('nb_titres', 20))
        if nb_titres < 1 or nb_titres > 200:
            return jsonify({"status": "error", "message": "nb_titres doit être entre 1 et 200"}), 400
        mode_playlist = data.get('mode_playlist', 'ajouter_existantes')
        if mode_playlist not in ('ajouter_existantes', 'creer_nouvelle', 'remplacer'):
            return jsonify({"status": "error", "message": "mode_playlist invalide"}), 400
        playlist_ids = data.get('playlist_ids', [])
        if isinstance(playlist_ids, str):
            try:
                playlist_ids = json.loads(playlist_ids)
            except json.JSONDecodeError:
                playlist_ids = []
        nom_nouvelle_playlist = data.get('nom_nouvelle_playlist') or None
        anti_repetition_jours = int(data.get('anti_repetition_jours', 7))
        dossier_cible = data.get('dossier_cible', 'imports_push')
        actif = 1 if data.get('actif', True) else 0
        dry_run = 1 if data.get('dry_run', True) else 0
        plage_h_debut = data.get('plage_h_debut', '06:00:00')
        plage_h_fin = data.get('plage_h_fin', '23:00:00')
        commentaire = data.get('commentaire') or None

        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO airvs_grille_avance "
            "(jour_semaine, heure, date_debut, date_fin, station_id, type_critere, "
            " valeur_critere, titres_ids, nb_titres, mode_playlist, playlist_ids, nom_nouvelle_playlist, "
            " anti_repetition_jours, dossier_cible, actif, dry_run, plage_h_debut, plage_h_fin, commentaire) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (jour_semaine, heure, date_debut, date_fin, station_id, type_critere,
             valeur_critere,
             json.dumps(titres_ids) if titres_ids else None,
             nb_titres, mode_playlist,
             json.dumps(playlist_ids) if playlist_ids else None,
             nom_nouvelle_playlist, anti_repetition_jours, dossier_cible,
             actif, dry_run, plage_h_debut, plage_h_fin, commentaire)
        )
        db.commit()
        new_id = cursor.lastrowid
        cursor.close()
        db.close()
        return jsonify({"status": "ok", "id": new_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/programmation_avance/<int:creneau_id>', methods=['PUT'])
@programmation_bp.route('/api/programmation_couleur/<int:creneau_id>', methods=['PUT'])  # alias legacy
@login_requis
def api_programmation_avance_update(creneau_id):
    """Modifie un créneau existant."""
    if not _assurer_schema_airvs_avance():
        return jsonify({"status": "error", "message": "Schéma SQL inaccessible"}), 500
    try:
        data = request.get_json(force=True, silent=True) or {}
        db = get_db_connection()
        cursor = db.cursor()
        # Vérifier l'existence
        cursor.execute("SELECT id FROM airvs_grille_avance WHERE id = %s", (creneau_id,))
        if not cursor.fetchone():
            cursor.close()
            db.close()
            return jsonify({"status": "error", "message": "Créneau introuvable"}), 404

        # Champs autorisés à la modification
        champs_autorises = {
            'jour_semaine': 'jour_semaine',
            'heure': 'heure',
            'date_debut': 'date_debut',
            'date_fin': 'date_fin',
            'station_id': 'station_id',
            'type_critere': 'type_critere',
            'valeur_critere': 'valeur_critere',
            'nb_titres': 'nb_titres',
            'mode_playlist': 'mode_playlist',
            'nom_nouvelle_playlist': 'nom_nouvelle_playlist',
            'anti_repetition_jours': 'anti_repetition_jours',
            'dossier_cible': 'dossier_cible',
            'actif': 'actif',
            'dry_run': 'dry_run',
            'plage_h_debut': 'plage_h_debut',
            'plage_h_fin': 'plage_h_fin',
            'commentaire': 'commentaire',
        }
        set_clauses = []
        values = []
        for json_key, db_col in champs_autorises.items():
            if json_key in data:
                val = data[json_key]
                if json_key in ('actif', 'dry_run'):
                    val = 1 if val else 0
                elif json_key in ('station_id', 'nb_titres', 'anti_repetition_jours', 'jour_semaine'):
                    try:
                        val = int(val) if val is not None else None
                    except (ValueError, TypeError):
                        val = None
                set_clauses.append(f"{db_col} = %s")
                values.append(val)

        # Cas spécial playlist_ids (JSON)
        if 'playlist_ids' in data:
            pl = data['playlist_ids']
            if isinstance(pl, str):
                try:
                    pl = json.loads(pl)
                except json.JSONDecodeError:
                    pl = []
            set_clauses.append("playlist_ids = %s")
            values.append(json.dumps(pl) if pl else None)

        # Cas spécial titres_ids (JSON, pour type_critere=titre)
        if 'titres_ids' in data:
            ti = data['titres_ids']
            if isinstance(ti, str):
                try:
                    ti = json.loads(ti)
                except json.JSONDecodeError:
                    ti = []
            set_clauses.append("titres_ids = %s")
            values.append(json.dumps(ti) if ti else None)

        if not set_clauses:
            cursor.close()
            db.close()
            return jsonify({"status": "error", "message": "Aucun champ à mettre à jour"}), 400

        values.append(creneau_id)
        cursor.execute(
            f"UPDATE airvs_grille_avance SET {', '.join(set_clauses)} WHERE id = %s",
            tuple(values)
        )
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"status": "ok", "id": creneau_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/programmation_avance/<int:creneau_id>', methods=['DELETE'])
@programmation_bp.route('/api/programmation_couleur/<int:creneau_id>', methods=['DELETE'])  # alias legacy
@login_requis
def api_programmation_avance_delete(creneau_id):
    """Supprime un créneau (l'historique est conservé pour traçabilité)."""
    if not _assurer_schema_airvs_avance():
        return jsonify({"status": "error", "message": "Schéma SQL inaccessible"}), 500
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM airvs_grille_avance WHERE id = %s", (creneau_id,))
        db.commit()
        affected = cursor.rowcount
        cursor.close()
        db.close()
        if affected == 0:
            return jsonify({"status": "error", "message": "Créneau introuvable"}), 404
        return jsonify({"status": "ok", "id": creneau_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/programmation_avance/<int:creneau_id>/dry_run')
@programmation_bp.route('/api/programmation_couleur/<int:creneau_id>/dry_run')  # alias legacy
@login_requis
def api_programmation_avance_dry_run(creneau_id):
    """Simule le créneau : renvoie la liste des titres qui seraient sélectionnés SANS pousser.
    Exclut l'anti-répétition si ?skip_anti_repetition=1 (utile pour déboguer)."""
    if not _assurer_schema_airvs_avance():
        return jsonify({"status": "error", "message": "Schéma SQL inaccessible"}), 500
    try:
        skip_anti_rep = request.args.get('skip_anti_repetition', '0') == '1'
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute("SELECT * FROM airvs_grille_avance WHERE id = %s", (creneau_id,))
        creneau = cursor.fetchone()
        if not creneau:
            cursor.close()
            db.close()
            return jsonify({"status": "error", "message": "Créneau introuvable"}), 404

        type_critere = creneau['type_critere']
        valeur_critere = creneau['valeur_critere']
        nb_titres = creneau['nb_titres']
        anti_rep_jours = creneau['anti_repetition_jours']
        titres_ids_raw = creneau.get('titres_ids')

        # Parser titres_ids si présent
        titres_ids = []
        if isinstance(titres_ids_raw, str):
            try:
                titres_ids = json.loads(titres_ids_raw) or []
            except (json.JSONDecodeError, TypeError):
                titres_ids = []
        elif isinstance(titres_ids_raw, list):
            titres_ids = titres_ids_raw

        # Construction de la requête de sélection
        try:
            sql_sel, sql_cnt, params_sel, params_cnt, is_titre_mode = _construire_requete_avance(
                type_critere, valeur_critere, titres_ids
            )
        except ValueError as ve:
            cursor.close()
            db.close()
            return jsonify({"status": "error", "message": str(ve)}), 400

        sql = sql_sel
        params = list(params_sel)

        if is_titre_mode:
            pool_total = len(titres_ids)
        else:
            cursor.execute(sql_cnt, tuple(params_cnt))
            pool_total = cursor.fetchone()['total']

        # Anti-répétition : exclure les song_id déjà utilisés sur N derniers jours
        if not skip_anti_rep:
            cursor.execute(
                "SELECT DISTINCT song_id FROM airvs_historique_avance "
                "WHERE date_lancement > NOW() - INTERVAL %s DAY",
                (anti_rep_jours,)
            )
            exclus = [r['song_id'] for r in cursor.fetchall()]
            if exclus:
                # Créer la liste de placeholders
                placeholders = ','.join(['%s'] * len(exclus))
                sql += f" AND s.ID NOT IN ({placeholders})"
                params.extend(exclus)

        # Si type=titre, on ne fait pas de RAND() (ordre souhaité) ni de LIMIT
        if is_titre_mode:
            sql += " ORDER BY s.artist, s.title"
        else:
            sql += " ORDER BY RAND() LIMIT %s"
            params.append(nb_titres)

        cursor.execute(sql, tuple(params))
        titres = cursor.fetchall()

        # Nombre d'exclus par anti-répétition
        cursor.execute(
            "SELECT COUNT(DISTINCT song_id) AS nb FROM airvs_historique_avance "
            "WHERE date_lancement > NOW() - INTERVAL %s DAY",
            (anti_rep_jours,)
        )
        exclus_count = cursor.fetchone()['nb']

        # Pour chaque titre, indiquer s'il est déjà IN_AZURACAST
        for t in titres:
            t['in_azura'] = (str(t.get('comments', '')).strip() == 'IN_AZURACAST')
            if t.get('year') is not None and hasattr(t['year'], 'isoformat'):
                t['year'] = t['year'].year if hasattr(t['year'], 'year') else int(t['year'])

        cursor.close()
        db.close()

        return jsonify({
            "status": "ok",
            "creneau": {
                "id": creneau['id'],
                "heure": str(creneau['heure']),
                "type_critere": type_critere,
                "valeur_critere": valeur_critere,
                "nb_titres": nb_titres,
                "mode_playlist": creneau['mode_playlist'],
                "anti_repetition_jours": anti_rep_jours,
            },
            "pool_total": pool_total,
            "pool_exclus_anti_repetition": exclus_count if not skip_anti_rep else 0,
            "anti_repetition_active": not skip_anti_rep,
            "titres": titres,
            "nb_selectionnes": len(titres),
            "warning_pool_insuffisant": len(titres) < nb_titres
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/programmation_avance/test_pool', methods=['POST'])
@programmation_bp.route('/api/programmation_couleur/test_pool', methods=['POST'])  # alias legacy
@login_requis
def api_programmation_avance_test_pool():
    """Teste la requête SQL SANS enregistrer le créneau.
    Body JSON: {type_critere, valeur_critere, titres_ids?, nb_titres?, anti_repetition_jours?, skip_anti_repetition?}
    Retourne: {status, pool_total, pool_exclus_anti_repetition, nb_selectionnes, sample_titres}
    Idéal pour prévisualiser la taille du pool AVANT d'enregistrer un créneau."""
    if not _assurer_schema_airvs_avance():
        return jsonify({"status": "error", "message": "Schéma SQL inaccessible"}), 500
    try:
        data = request.get_json(force=True, silent=True) or {}
        type_critere = (data.get('type_critere') or '').strip()
        valeur_critere = (data.get('valeur_critere') or '').strip()
        titres_ids = data.get('titres_ids') or []
        if isinstance(titres_ids, str):
            try:
                titres_ids = json.loads(titres_ids)
            except (json.JSONDecodeError, TypeError):
                titres_ids = []
        try:
            nb_titres = int(data.get('nb_titres', 20))
        except (ValueError, TypeError):
            nb_titres = 20
        try:
            anti_rep_jours = int(data.get('anti_repetition_jours', 7))
        except (ValueError, TypeError):
            anti_rep_jours = 7
        skip_anti_rep = bool(data.get('skip_anti_repetition', False))

        if not type_critere:
            return jsonify({"status": "error", "message": "type_critere requis"}), 400
        if type_critere != 'titre' and not valeur_critere:
            return jsonify({"status": "error", "message": "valeur_critere requis pour ce type"}), 400

        # Construction de la requête
        try:
            sql_sel, sql_cnt, params_sel, params_cnt, is_titre_mode = _construire_requete_avance(
                type_critere, valeur_critere, titres_ids
            )
        except ValueError as ve:
            return jsonify({"status": "error", "message": str(ve)}), 400

        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)

        if is_titre_mode:
            pool_total = len(titres_ids)
        else:
            cursor.execute(sql_cnt, tuple(params_cnt))
            pool_total = cursor.fetchone()['total']

        sql = sql_sel
        params = list(params_sel)

        exclus_count = 0
        if not skip_anti_rep:
            cursor.execute(
                "SELECT DISTINCT song_id FROM airvs_historique_avance "
                "WHERE date_lancement > NOW() - INTERVAL %s DAY",
                (anti_rep_jours,)
            )
            exclus = [r['song_id'] for r in cursor.fetchall()]
            exclus_count = len(exclus)
            if exclus:
                placeholders = ','.join(['%s'] * len(exclus))
                sql += f" AND s.ID NOT IN ({placeholders})"
                params.extend(exclus)

        # Limit pour ne récupérer qu'un échantillon (les 50 premiers aléatoires)
        if is_titre_mode:
            sql += " ORDER BY s.artist, s.title LIMIT %s"
            params.append(min(50, nb_titres))
        else:
            sql += " ORDER BY RAND() LIMIT %s"
            params.append(min(50, nb_titres))

        cursor.execute(sql, tuple(params))
        titres = cursor.fetchall()

        for t in titres:
            t['in_azura'] = (str(t.get('comments', '')).strip() == 'IN_AZURACAST')
            if t.get('year') is not None and hasattr(t['year'], 'isoformat'):
                t['year'] = t['year'].year if hasattr(t['year'], 'year') else int(t['year'])

        cursor.close()
        db.close()

        return jsonify({
            "status": "ok",
            "pool_total": pool_total,
            "pool_exclus_anti_repetition": exclus_count if not skip_anti_rep else 0,
            "anti_repetition_active": not skip_anti_rep,
            "nb_selectionnes": len(titres),
            "sample_titres": titres,
            "warning_pool_insuffisant": pool_total < nb_titres,
            "warning_pool_empty": pool_total == 0
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/programmation_avance/<int:creneau_id>/forcer_lancement', methods=['POST'])
@programmation_bp.route('/api/programmation_couleur/<int:creneau_id>/forcer_lancement', methods=['POST'])  # alias legacy
@login_requis
def api_programmation_avance_forcer(creneau_id):
    """Insère une tâche PROGRAMMATION_AVANCE dans taches_planifiees pour exécution immédiate.
    Si le créneau est en dry_run, l'exécution se fera en mode simulation (pas de push).
    Body optionnel: {"bypass_dry_run": true} pour forcer un push réel même si dry_run=1."""
    if not _assurer_schema_airvs_avance():
        return jsonify({"status": "error", "message": "Schéma SQL inaccessible"}), 500
    try:
        data = request.get_json(force=True, silent=True) or {}
        bypass_dry_run = bool(data.get('bypass_dry_run', False))
        db = get_db_connection()
        # IMPORTANT : get_db_connection() (utils.py) set cursorclass=DictCursor globalement,
        # donc db.cursor() retourne un DictCursor. On l'impose explicitement pour éviter
        # toute ambiguïté tuple/dict.
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute("SELECT id, dry_run FROM airvs_grille_avance WHERE id = %s", (creneau_id,))
        row = cursor.fetchone()
        if not row:
            cursor.close()
            db.close()
            return jsonify({"status": "error", "message": "Créneau introuvable"}), 404
        # row est un dict {id, dry_run} (DictCursor) — accès par clé, pas par index
        dry_run_creneau = bool(row.get('dry_run', 1))
        # Si bypass_dry_run demandé, on force force_dry_run=False (push réel)
        # Sinon, on respecte le flag dry_run du créneau
        if bypass_dry_run:
            force_dry_run = False
            effective_dry_run = False
        else:
            force_dry_run = dry_run_creneau
            effective_dry_run = dry_run_creneau
        params = {
            "grille_id": creneau_id,
            "force_dry_run": force_dry_run,
            "force": True              # Force l'exécution même si déjà lancé aujourd'hui
        }
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
            "VALUES ('PROGRAMMATION_AVANCE', 'en_attente', %s, NOW())",
            (json.dumps(params),)
        )
        db.commit()
        task_id = cursor.lastrowid
        cursor.close()
        db.close()
        return jsonify({
            "status": "ok",
            "task_id": task_id,
            "dry_run": effective_dry_run,
            "bypass_dry_run": bypass_dry_run,
            "message": "Tâche PROGRAMMATION_AVANCE insérée — le worker va la traiter."
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/programmation_avance/stats')
@programmation_bp.route('/api/programmation_couleur/stats')  # alias legacy
@login_requis
def api_programmation_avance_stats():
    """Statistiques d'usage de la Programmation avancée.
    Paramètres query (tous facultatifs) :
      - jours : fenêtre en jours (défaut 7, max 90)
      - grille_id : filtrer par créneau spécifique
      - station_id : filtrer par station (6 ou 7)
    Retourne :
      - periode : {jours, debut, fin}
      - kpis : {nb_titres_pousses, nb_creneaux_declenches, nb_titres_uniques,
                nb_artistes_uniques, nb_creneaux_actifs, taux_repetition_pct,
                nb_lancements (alias rétrocompat. = nb_titres_pousses)}
      - par_creneau : [{grille_id, type_critere, valeur_critere, heure,
                        nb_lancements, nb_titres_uniques, taux_repetition_pct,
                        dernier_lancement}]
      - top_titres : [{song_id, artist, title, nb_pushes, dernier_push}]
      - top_artistes : [{artist, nb_pushes, nb_titres_distincts}]
      - distribution_journaliere : [{date, nb_lancements, nb_titres_uniques}]
      - par_station : [{station_id, nb_lancements, nb_titres_uniques}]
      - par_mode : [{mode_playlist, nb_lancements, nb_titres_uniques}]
    """
    if not _assurer_schema_airvs_avance():
        return jsonify({"status": "error", "message": "Schéma SQL inaccessible"}), 500
    try:
        jours = max(min(int(request.args.get('jours', '7')), 90), 1)
        grille_id = request.args.get('grille_id', type=int)
        station_id = request.args.get('station_id', type=int)

        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)

        # Construction clause WHERE commune
        where_clauses = ["h.date_lancement > NOW() - INTERVAL %s DAY"]
        params = [jours]
        if grille_id:
            where_clauses.append("h.grille_id = %s")
            params.append(grille_id)
        if station_id:
            where_clauses.append("h.station_id = %s")
            params.append(station_id)
        where_sql = " AND ".join(where_clauses)

        # ── 1. KPIs globaux ──
        cursor.execute(
            f"SELECT COUNT(*) AS nb_titres_pousses, "
            f"COUNT(DISTINCT h.song_id) AS nb_titres_uniques, "
            f"COUNT(DISTINCT s.artist) AS nb_artistes_uniques, "
            f"COUNT(DISTINCT h.grille_id) AS nb_creneaux_actifs, "
            f"COUNT(DISTINCT h.tache_id) AS nb_creneaux_declenches "
            f"FROM airvs_historique_avance h "
            f"LEFT JOIN songs s ON s.ID = h.song_id "
            f"WHERE {where_sql}",
            tuple(params)
        )
        kpi_row = cursor.fetchone() or {}
        nb_titres_pousses = int(kpi_row.get('nb_titres_pousses') or 0)
        nb_titres_uniques = int(kpi_row.get('nb_titres_uniques') or 0)
        nb_artistes_uniques = int(kpi_row.get('nb_artistes_uniques') or 0)
        nb_creneaux_actifs = int(kpi_row.get('nb_creneaux_actifs') or 0)
        nb_creneaux_declenches = int(kpi_row.get('nb_creneaux_declenches') or 0)
        if nb_titres_pousses > 0:
            taux_repetition_pct = round((nb_titres_pousses - nb_titres_uniques) * 100.0 / nb_titres_pousses, 1)
        else:
            taux_repetition_pct = 0.0

        # ── 2. Stats par créneau ──
        cursor.execute(
            f"SELECT h.grille_id, g.type_critere, g.valeur_critere, "
            f"       g.heure AS grille_heure, g.station_id, "
            f"       COUNT(*) AS nb_lancements, "
            f"       COUNT(DISTINCT h.song_id) AS nb_titres_uniques, "
            f"       MAX(h.date_lancement) AS dernier_lancement "
            f"FROM airvs_historique_avance h "
            f"LEFT JOIN airvs_grille_avance g ON g.id = h.grille_id "
            f"WHERE {where_sql} "
            f"GROUP BY h.grille_id, g.type_critere, g.valeur_critere, "
            f"         g.heure, g.station_id "
            f"ORDER BY nb_lancements DESC, h.grille_id ASC",
            tuple(params)
        )
        rows_par_creneau = cursor.fetchall()
        par_creneau = []
        for r in rows_par_creneau:
            nb_l = int(r.get('nb_lancements') or 0)
            nb_tu = int(r.get('nb_titres_uniques') or 0)
            par_creneau.append({
                'grille_id': r.get('grille_id'),
                'type_critere': r.get('type_critere'),
                'valeur_critere': r.get('valeur_critere'),
                'heure': str(r['grille_heure']) if r.get('grille_heure') else None,
                'station_id': r.get('station_id'),
                'nb_lancements': nb_l,
                'nb_titres_uniques': nb_tu,
                'taux_repetition_pct': round((nb_l - nb_tu) * 100.0 / nb_l, 1) if nb_l > 0 else 0.0,
                'dernier_lancement': r['dernier_lancement'].isoformat() if r.get('dernier_lancement') and hasattr(r['dernier_lancement'], 'isoformat') else (str(r['dernier_lancement']) if r.get('dernier_lancement') else None)
            })

        # ── 3. Top titres les plus poussés ──
        cursor.execute(
            f"SELECT h.song_id, s.artist, s.title, s.album, "
            f"       COUNT(*) AS nb_pushes, "
            f"       MAX(h.date_lancement) AS dernier_push, "
            f"       MIN(h.date_lancement) AS premier_push "
            f"FROM airvs_historique_avance h "
            f"LEFT JOIN songs s ON s.ID = h.song_id "
            f"WHERE {where_sql} "
            f"GROUP BY h.song_id, s.artist, s.title, s.album "
            f"ORDER BY nb_pushes DESC, dernier_push DESC "
            f"LIMIT 30",
            tuple(params)
        )
        rows_top = cursor.fetchall()
        top_titres = []
        for r in rows_top:
            top_titres.append({
                'song_id': r.get('song_id'),
                'artist': r.get('artist'),
                'title': r.get('title'),
                'album': r.get('album'),
                'nb_pushes': int(r.get('nb_pushes') or 0),
                'dernier_push': r['dernier_push'].isoformat() if r.get('dernier_push') and hasattr(r['dernier_push'], 'isoformat') else None,
                'premier_push': r['premier_push'].isoformat() if r.get('premier_push') and hasattr(r['premier_push'], 'isoformat') else None
            })

        # ── 4. Top artistes ──
        cursor.execute(
            f"SELECT s.artist, "
            f"       COUNT(*) AS nb_pushes, "
            f"       COUNT(DISTINCT h.song_id) AS nb_titres_distincts, "
            f"       MAX(h.date_lancement) AS dernier_push "
            f"FROM airvs_historique_avance h "
            f"LEFT JOIN songs s ON s.ID = h.song_id "
            f"WHERE {where_sql} AND s.artist IS NOT NULL AND s.artist != '' "
            f"GROUP BY s.artist "
            f"ORDER BY nb_pushes DESC, nb_titres_distincts DESC "
            f"LIMIT 15",
            tuple(params)
        )
        rows_art = cursor.fetchall()
        top_artistes = []
        for r in rows_art:
            top_artistes.append({
                'artist': r.get('artist'),
                'nb_pushes': int(r.get('nb_pushes') or 0),
                'nb_titres_distincts': int(r.get('nb_titres_distincts') or 0),
                'dernier_push': r['dernier_push'].isoformat() if r.get('dernier_push') and hasattr(r['dernier_push'], 'isoformat') else None
            })

        # ── 5. Distribution journalière ──
        cursor.execute(
            f"SELECT DATE(h.date_lancement) AS jour, "
            f"       COUNT(*) AS nb_lancements, "
            f"       COUNT(DISTINCT h.song_id) AS nb_titres_uniques "
            f"FROM airvs_historique_avance h "
            f"WHERE {where_sql} "
            f"GROUP BY DATE(h.date_lancement) "
            f"ORDER BY jour ASC",
            tuple(params)
        )
        rows_dist = cursor.fetchall()
        distribution_journaliere = []
        for r in rows_dist:
            jour = r.get('jour')
            distribution_journaliere.append({
                'date': jour.isoformat() if jour and hasattr(jour, 'isoformat') else (str(jour) if jour else None),
                'nb_lancements': int(r.get('nb_lancements') or 0),
                'nb_titres_uniques': int(r.get('nb_titres_uniques') or 0)
            })

        # ── 6. Par station ──
        cursor.execute(
            f"SELECT h.station_id, "
            f"       COUNT(*) AS nb_lancements, "
            f"       COUNT(DISTINCT h.song_id) AS nb_titres_uniques "
            f"FROM airvs_historique_avance h "
            f"WHERE {where_sql} "
            f"GROUP BY h.station_id "
            f"ORDER BY h.station_id ASC",
            tuple(params)
        )
        rows_station = cursor.fetchall()
        par_station = []
        for r in rows_station:
            par_station.append({
                'station_id': int(r.get('station_id') or 0),
                'nb_lancements': int(r.get('nb_lancements') or 0),
                'nb_titres_uniques': int(r.get('nb_titres_uniques') or 0)
            })

        # ── 7. Par mode_playlist ──
        cursor.execute(
            f"SELECT h.mode_playlist, "
            f"       COUNT(*) AS nb_lancements, "
            f"       COUNT(DISTINCT h.song_id) AS nb_titres_uniques "
            f"FROM airvs_historique_avance h "
            f"WHERE {where_sql} "
            f"GROUP BY h.mode_playlist "
            f"ORDER BY nb_lancements DESC",
            tuple(params)
        )
        rows_mode = cursor.fetchall()
        par_mode = []
        for r in rows_mode:
            par_mode.append({
                'mode_playlist': r.get('mode_playlist') or 'inconnu',
                'nb_lancements': int(r.get('nb_lancements') or 0),
                'nb_titres_uniques': int(r.get('nb_titres_uniques') or 0)
            })

        cursor.close()
        db.close()

        return jsonify({
            "status": "ok",
            "periode": {"jours": jours, "grille_id": grille_id, "station_id": station_id},
            "kpis": {
                "nb_titres_pousses": nb_titres_pousses,
                "nb_creneaux_declenches": nb_creneaux_declenches,
                "nb_titres_uniques": nb_titres_uniques,
                "nb_artistes_uniques": nb_artistes_uniques,
                "nb_creneaux_actifs": nb_creneaux_actifs,
                "taux_repetition_pct": taux_repetition_pct,
                "nb_lancements": nb_titres_pousses
            },
            "par_creneau": par_creneau,
            "top_titres": top_titres,
            "top_artistes": top_artistes,
            "distribution_journaliere": distribution_journaliere,
            "par_station": par_station,
            "par_mode": par_mode
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/programmation_avance/historique')
@programmation_bp.route('/api/programmation_couleur/historique')  # alias legacy
@login_requis
def api_programmation_avance_historique():
    """Renvoie l'historique des lancements (par défaut : 7 derniers jours).
    Paramètres query (tous facultatifs) :
      - jours : fenêtre en jours (défaut 7, max 90)
      - grille_id : filtrer par créneau spécifique
      - station_id : filtrer par station (6 ou 7)
      - q : recherche textuelle (LIKE sur artist/title/album)
      - limit : nombre max de résultats (défaut 500, max 2000)
    """
    if not _assurer_schema_airvs_avance():
        return jsonify({"status": "error", "message": "Schéma SQL inaccessible"}), 500
    try:
        jours = int(request.args.get('jours', '7'))
        limite = min(max(int(request.args.get('limit', '500')), 1), 2000)
        grille_id = request.args.get('grille_id', type=int)
        station_id = request.args.get('station_id', type=int)
        q = (request.args.get('q') or '').strip()

        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)

        sql = (
            "SELECT h.id, h.grille_id, h.song_id, h.station_id, "
            "       h.date_lancement, h.tache_id, h.mode_playlist, h.playlist_cible, "
            "       s.artist, s.title, s.album, s.year, "
            "       g.type_critere, g.valeur_critere, g.heure AS grille_heure "
            "FROM airvs_historique_avance h "
            "LEFT JOIN songs s ON s.ID = h.song_id "
            "LEFT JOIN airvs_grille_avance g ON g.id = h.grille_id "
            "WHERE h.date_lancement > NOW() - INTERVAL %s DAY"
        )
        params = [jours]
        if grille_id:
            sql += " AND h.grille_id = %s"
            params.append(grille_id)
        if station_id:
            sql += " AND h.station_id = %s"
            params.append(station_id)
        if q:
            sql += " AND (s.artist LIKE %s OR s.title LIKE %s OR s.album LIKE %s)"
            like = f"%{q}%"
            params.extend([like, like, like])
        sql += " ORDER BY h.date_lancement DESC, h.id DESC LIMIT %s"
        params.append(limite)

        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()

        for r in rows:
            if r.get('date_lancement') is not None:
                r['date_lancement'] = r['date_lancement'].isoformat() if hasattr(r['date_lancement'], 'isoformat') else str(r['date_lancement'])
            if r.get('grille_heure') is not None:
                r['grille_heure'] = str(r['grille_heure'])
            if r.get('year') is not None and hasattr(r['year'], 'isoformat'):
                r['year'] = r['year'].year if hasattr(r['year'], 'year') else int(r['year'])

        cursor.close()
        db.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/programmation_avance/import_sheets', methods=['POST'])
@programmation_bp.route('/api/programmation_couleur/import_sheets', methods=['POST'])  # alias legacy
@login_requis
def api_programmation_avance_import_sheets():
    """Planifie une tâche IMPORT_SHEETS_AVANCE qui sera exécutée par le worker Ubuntu.
    Body JSON: {sheets_id, onglet, dry_run_import (bool, défaut False)}
      - sheets_id : alias défini dans config.json OU ID Google Sheets brut
      - onglet    : nom de l'onglet à lire
      - dry_run_import : si True, parse seulement et logge — n'écrit pas en base
    Retourne: {status, task_id}
    """
    if not _assurer_schema_airvs_avance():
        return jsonify({"status": "error", "message": "Schéma SQL inaccessible"}), 500
    try:
        data = request.get_json(force=True, silent=True) or {}
        sheets_id = (data.get('sheets_id') or '').strip()
        onglet = (data.get('onglet') or '').strip()
        dry_run_import = bool(data.get('dry_run_import', False))

        if not sheets_id or not onglet:
            return jsonify({"status": "error",
                            "message": "Paramètres requis: sheets_id, onglet"}), 400

        params = {
            'sheets_id': sheets_id,
            'onglet': onglet,
            'dry_run_import': dry_run_import,
            'demande_par': session.get('user', 'inconnu') if hasattr(session, 'get') else 'inconnu'
        }
        db = get_db_connection()
        if not db:
            return jsonify({"status": "error", "message": "DB inaccessible"}), 500
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
            "VALUES ('IMPORT_SHEETS_AVANCE', 'en_attente', %s, NOW())",
            (json.dumps(params),)
        )
        task_id = cursor.lastrowid
        db.commit()
        cursor.close()
        db.close()
        return jsonify({
            "status": "ok",
            "task_id": task_id,
            "message": (f"Tâche d'import créée (#{task_id}). "
                        f"Le worker Ubuntu va lire le Sheet '{sheets_id}' onglet '{onglet}'. "
                        + ("Mode DRY-RUN (parse seulement)." if dry_run_import else "Mode ÉCRITURE."))
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ─── Routes : Pool de titres validés ────────────────────────────────────

@programmation_bp.route('/api/pool/valider', methods=['POST'])
@login_requis
def api_pool_valider():
    """Déclenche une validation manuelle d'un Sheet vers un pool.
    Crée une tâche VALIDATION_POOL_SHEETS que le worker exécutera."""
    _assurer_schema_airvs_avance()
    try:
        data = request.get_json(force=True) or {}
        sheets_id = (data.get('sheets_id') or '').strip()
        onglet = (data.get('onglet') or '').strip()
        semaine = (data.get('semaine') or '').strip()
        dry_run = bool(data.get('dry_run', False))
        demande_par = (data.get('demande_par') or session.get('user', 'inconnu')).strip()

        if not sheets_id or not onglet:
            return jsonify({"status": "error",
                            "message": "Champs requis: sheets_id, onglet"}), 400

        # Résoudre l'alias via config.json
        sheets_resolved = sheets_id
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            aliases = cfg.get('sheets_aliases', {})
            if sheets_id in aliases:
                sheets_resolved = aliases[sheets_id]
        except Exception:
            pass

        parametres = {
            'sheets_id': sheets_resolved if sheets_resolved != sheets_id else sheets_id,
            'onglet': onglet,
            'semaine': semaine,
            'dry_run': dry_run,
            'demande_par': demande_par,
        }
        db = get_db_connection()
        if not db:
            return jsonify({"status": "error", "message": "DB indisponible"}), 500
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
            "VALUES ('VALIDATION_POOL_SHEETS', 'en_attente', %s, NOW())",
            (json.dumps(parametres),)
        )
        task_id = cursor.lastrowid
        db.commit()
        cursor.close()
        db.close()

        return jsonify({
            "status": "ok",
            "task_id": task_id,
            "message": (f"Tâche de validation pool créée (#{task_id}). "
                        f"Le worker va lire le Sheet '{sheets_id}' onglet '{onglet}'. "
                        + ("Mode DRY-RUN." if dry_run else "Mode ÉCRITURE."))
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/pool/migrer', methods=['POST'])
@login_requis
def api_pool_migrer():
    actions = []
    erreurs = []

    try:
        if _assurer_schema_airvs_avance():
            actions.append("✓ _assurer_schema_airvs_avance() exécuté (tables + migrations de base)")
        else:
            erreurs.append("_assurer_schema_airvs_avance() a échoué (voir logs Flask)")

        db = get_db_connection()
        if not db:
            return jsonify({"status": "error",
                            "message": "Connexion DB impossible",
                            "actions": actions,
                            "erreurs": erreurs + ["get_db_connection() a retourné None"]}), 500
        cursor = db.cursor()
        dcursor = db.cursor(pymysql.cursors.DictCursor)  # pour information_schema

        # 2. Créer airvs_pool_meta si elle n'existe pas
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS airvs_pool_meta (
                  id              INT AUTO_INCREMENT PRIMARY KEY,
                  nom             VARCHAR(100) NOT NULL,
                  source          VARCHAR(50)  NOT NULL,
                  sheet_alias     VARCHAR(40)  DEFAULT NULL,
                  jour_defaut     VARCHAR(10)  DEFAULT NULL,
                  actif           TINYINT(1)   NOT NULL DEFAULT 1,
                  date_creation   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE KEY nom (nom)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            actions.append("✓ Table airvs_pool_meta vérifiée/créée")
        except Exception as e:
            erreurs.append(f"CREATE airvs_pool_meta : {type(e).__name__}: {e}")

        # 3. Ajouter les colonnes multiformat à airvs_pool_titres si manquantes
        colonnes_multiformat = [
            ("source",         "VARCHAR(50)  NOT NULL DEFAULT 'inconnu'"),
            ("feuille",        "VARCHAR(50)  DEFAULT NULL"),
            ("ligne",          "INT          DEFAULT NULL"),
            ("jour",           "VARCHAR(10)  DEFAULT NULL"),
            ("artiste_saisi",  "VARCHAR(255) NOT NULL DEFAULT ''"),
            ("titre_saisi",    "VARCHAR(255) NOT NULL DEFAULT ''"),
            ("annee_saisie",   "INT          DEFAULT NULL"),
            ("album_saisi",    "VARCHAR(255) DEFAULT NULL"),
            ("infos_saisi",    "TEXT         DEFAULT NULL"),
            ("score_match",    "INT          DEFAULT NULL"),
            ("motif_rejet",    "VARCHAR(255) DEFAULT NULL"),
            ("valide_par",     "VARCHAR(100) DEFAULT NULL"),
        ]
        for col, defn in colonnes_multiformat:
            try:
                dcursor.execute(
                    "SELECT COLUMN_NAME FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() "
                    "AND table_name = 'airvs_pool_titres' "
                    "AND column_name = %s",
                    (col,)
                )
                if dcursor.fetchone() is None:
                    cursor.execute(
                        f"ALTER TABLE airvs_pool_titres "
                        f"ADD COLUMN {col} {defn}"
                    )
                    actions.append(f"✓ Colonne airvs_pool_titres.{col} ajoutée")
            except Exception as e:
                erreurs.append(f"ADD COLUMN airvs_pool_titres.{col} : {type(e).__name__}: {e}")

        # 4. Sécuriser la colonne onglet
        try:
            dcursor.execute(
                "SELECT COLUMN_DEFAULT FROM information_schema.columns "
                "WHERE table_schema = DATABASE() "
                "AND table_name = 'airvs_pool_titres' "
                "AND column_name = 'onglet'"
            )
            row = dcursor.fetchone()
            if row and row.get('COLUMN_DEFAULT') is None:
                cursor.execute(
                    "ALTER TABLE airvs_pool_titres "
                    "MODIFY COLUMN onglet VARCHAR(60) NOT NULL DEFAULT ''"
                )
                actions.append("✓ Colonne airvs_pool_titres.onglet : DEFAULT '' ajouté (sécurité legacy)")
        except Exception as e:
            erreurs.append(f"MODIFY onglet DEFAULT : {type(e).__name__}: {e}")

        # 4b. Rendre song_id tolérant aux NULL
        try:
            dcursor.execute(
                "SELECT IS_NULLABLE FROM information_schema.columns "
                "WHERE table_schema = DATABASE() "
                "AND table_name = 'airvs_pool_titres' "
                "AND column_name = 'song_id'"
            )
            row = dcursor.fetchone()
            if row and row.get('IS_NULLABLE') == 'NO':
                cursor.execute(
                    "ALTER TABLE airvs_pool_titres "
                    "MODIFY COLUMN song_id INT NULL DEFAULT 0"
                )
                actions.append("✓ Colonne airvs_pool_titres.song_id : NOT NULL → NULL DEFAULT 0 (tolérance worker multiformat)")
        except Exception as e:
            erreurs.append(f"MODIFY song_id NULL DEFAULT 0 : {type(e).__name__}: {e}")

        # 4c. Étendre ENUM statut
        try:
            dcursor.execute(
                "SELECT COLUMN_TYPE FROM information_schema.columns "
                "WHERE table_schema = DATABASE() "
                "AND table_name = 'airvs_pool_titres' "
                "AND column_name = 'statut'"
            )
            row = dcursor.fetchone()
            if row:
                col_type = (row.get('COLUMN_TYPE') or '').lower()
                valeurs_manquantes = [v for v in ("'rejete'", "'doublon'") if v not in col_type]
                if valeurs_manquantes:
                    cursor.execute(
                        "ALTER TABLE airvs_pool_titres "
                        "MODIFY COLUMN statut ENUM('valide','manquant','rejete','doublon') "
                        "NOT NULL DEFAULT 'valide'"
                    )
                    actions.append(f"✓ Colonne airvs_pool_titres.statut : ENUM étendu avec {valeurs_manquantes} (worker multiformat)")
        except Exception as e:
            erreurs.append(f"MODIFY statut ENUM rejete/doublon : {type(e).__name__}: {e}")

        # 5. Index supplémentaire sur source
        try:
            dcursor.execute(
                "SELECT INDEX_NAME FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() "
                "AND table_name = 'airvs_pool_titres' "
                "AND index_name = 'idx_source' "
                "LIMIT 1"
            )
            if dcursor.fetchone() is None:
                cursor.execute(
                    "ALTER TABLE airvs_pool_titres ADD INDEX idx_source (source)"
                )
                actions.append("✓ Index idx_source ajouté sur airvs_pool_titres")
        except Exception as e:
            erreurs.append(f"ADD INDEX idx_source : {type(e).__name__}: {e}")

        # 6. Vérifier que airvs_worker_info existe
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS airvs_worker_info (
                  id              INT NOT NULL DEFAULT 1,
                  version         VARCHAR(64)  NOT NULL,
                  demarrage_le    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  hostname        VARCHAR(128) DEFAULT NULL,
                  PRIMARY KEY (id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            actions.append("✓ Table airvs_worker_info vérifiée/créée")
        except Exception as e:
            erreurs.append(f"CREATE airvs_worker_info : {type(e).__name__}: {e}")

        try:
            dcursor.close()
        except Exception:
            pass
        db.commit()
        cursor.close()
        db.close()

        # 7. Statut renvoyé au frontend
        schema_errors = getattr(_assurer_schema_airvs_avance, '_last_errors', [])
        if schema_errors:
            erreurs.extend(schema_errors)

        if erreurs:
            statut = "partiel" if actions else "error"
        else:
            statut = "ok"

        return jsonify({
            "status": statut,
            "actions": actions,
            "erreurs": erreurs,
            "debug_url": "/api/pool/debug_schema",
            "message": (f"Migration terminée — {len(actions)} action(s) effectuée(s), "
                        f"{len(erreurs)} erreur(s). "
                        f"Voir /api/pool/debug_schema pour diagnostic détaillé.")
        })
    except Exception as e:
        import traceback
        return jsonify({
            "status": "error",
            "message": f"Exception pendant la migration : {type(e).__name__}: {e}",
            "actions": actions,
            "erreurs": erreurs + [f"{type(e).__name__}: {e}", traceback.format_exc()]
        }), 500


@programmation_bp.route('/api/pool/debug_schema')
@login_requis
def api_pool_debug_schema():
    """Diagnostique détaillé du schéma SQL."""
    ret = _assurer_schema_airvs_avance()
    erreurs = getattr(_assurer_schema_airvs_avance, '_last_errors', [])

    try:
        db = get_db_connection()
        if not db:
            return jsonify({
                "return_value": ret,
                "erreurs_schema": erreurs,
                "error": "DB indisponible"
            })
        cursor = db.cursor(pymysql.cursors.DictCursor)

        cursor.execute("""
            SELECT table_name, table_rows, engine, table_comment
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            AND table_name LIKE 'airvs_%'
            ORDER BY table_name
        """)
        tables = [dict(r) for r in cursor.fetchall()]

        cursor.execute("""
            SELECT column_name, column_type, is_nullable, column_default, extra
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
            AND table_name = 'airvs_pool_titres'
            ORDER BY ordinal_position
        """)
        colonnes = [dict(r) for r in cursor.fetchall()]

        tables_attendues = [
            'airvs_grille_avance',
            'airvs_historique_avance',
            'airvs_pool_titres',
            'airvs_pool_auto_validation',
            'airvs_pool_meta',
            'airvs_worker_info',
            'airvs_auto_check_config',
            'airvs_notifications',
        ]
        cursor.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = DATABASE()
            AND table_name IN ('airvs_grille_avance','airvs_historique_avance',
                              'airvs_pool_titres','airvs_pool_auto_validation',
                              'airvs_pool_meta','airvs_worker_info',
                              'airvs_auto_check_config','airvs_notifications')
        """)
        existantes = set(r['table_name'] for r in cursor.fetchall())
        tables_attendues_status = [
            {"table": t, "existe": 1 if t in existantes else 0}
            for t in tables_attendues
        ]

        try:
            cursor.execute("SELECT COUNT(*) AS c FROM taches_planifiees")
            taches_count = cursor.fetchone()['c']
        except Exception as e:
            taches_count = f"ERREUR: {type(e).__name__}: {e}"

        cursor.close()
        db.close()

        return jsonify({
            "return_value": ret,
            "erreurs_schema": erreurs,
            "tables_airvs": tables,
            "tables_attendues": tables_attendues_status,
            "colonnes_airvs_pool_titres": colonnes,
            "taches_planifiees_count": taches_count,
            "db_host": getattr(get_db_connection, '_last_host', None),
        })
    except Exception as e:
        import traceback
        return jsonify({
            "return_value": ret,
            "erreurs_schema": erreurs,
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc()
        }), 500


@programmation_bp.route('/api/pool/stats')
@login_requis
def api_pool_stats():
    _assurer_schema_airvs_avance()
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"error": "DB indisponible"}), 500
        cursor = db.cursor(pymysql.cursors.DictCursor)

        cursor.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(CASE WHEN statut='valide'  THEN 1 END) AS valide,
                COUNT(CASE WHEN statut='rejete'  THEN 1 END) AS rejete,
                COUNT(CASE WHEN statut='doublon' THEN 1 END) AS doublon,
                COUNT(CASE WHEN statut='manquant' THEN 1 END) AS manquant
            FROM airvs_pool_titres
        """)
        totaux = cursor.fetchone() or {}
        result = {
            "total":   int(totaux.get('total')   or 0),
            "valide":  int(totaux.get('valide')  or 0),
            "rejete":  int(totaux.get('rejete')  or 0),
            "doublon": int(totaux.get('doublon') or 0),
            "manquant":int(totaux.get('manquant')or 0),
        }

        try:
            cursor.execute("""
                SELECT
                    COALESCE(source, 'inconnu') AS source,
                    COUNT(*) AS total,
                    COUNT(CASE WHEN statut='valide' THEN 1 END) AS valide,
                    COUNT(CASE WHEN statut='rejete' THEN 1 END) AS rejete
                FROM airvs_pool_titres
                GROUP BY source
                ORDER BY total DESC
            """)
            result["par_source"] = [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            print(f"[pool/stats] par_source ignoré : {type(e).__name__}: {e}")
            result["par_source"] = []

        try:
            cursor.execute("""
                SELECT
                    COALESCE(semaine, '—') AS semaine,
                    COUNT(*) AS total,
                    COUNT(CASE WHEN statut='valide' THEN 1 END) AS valide,
                    COUNT(CASE WHEN statut='rejete' THEN 1 END) AS rejete
                FROM airvs_pool_titres
                GROUP BY semaine
                ORDER BY semaine DESC
            """)
            result["par_semaine"] = [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            print(f"[pool/stats] par_semaine ignoré : {type(e).__name__}: {e}")
            result["par_semaine"] = []

        try:
            cursor.execute("""
                SELECT
                    id_pool,
                    MAX(sheet_alias) AS sheet_alias,
                    MAX(source) AS source,
                    COUNT(*) AS total,
                    COUNT(CASE WHEN statut='valide' THEN 1 END) AS valide,
                    COUNT(CASE WHEN statut='rejete' THEN 1 END) AS rejete,
                    MAX(date_validation) AS derniere_validation
                FROM airvs_pool_titres
                GROUP BY id_pool
                ORDER BY derniere_validation DESC
                LIMIT 10
            """)
            result["derniers_pools"] = [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            print(f"[pool/stats] derniers_pools ignoré : {type(e).__name__}: {e}")
            result["derniers_pools"] = []

        def _ser(o):
            if hasattr(o, 'isoformat'):
                return o.isoformat()
            return str(o)
        for k, v in list(result.items()):
            if isinstance(v, list):
                for row in v:
                    for kk, vv in list(row.items()):
                        if hasattr(vv, 'isoformat'):
                            row[kk] = vv.isoformat()

        cursor.close()
        db.close()
        return jsonify(result)
    except Exception as e:
        import traceback
        print(f"[pool/stats] ERREUR : {type(e).__name__}: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@programmation_bp.route('/api/pool/resultats')
@login_requis
def api_pool_resultats():
    _assurer_schema_airvs_avance()
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"error": "DB indisponible"}), 500
        cursor = db.cursor(pymysql.cursors.DictCursor)

        where_clauses = []
        params = []

        id_pool = request.args.get('id_pool', '').strip()
        if id_pool:
            where_clauses.append("id_pool = %s")
            params.append(id_pool)

        source = request.args.get('source', '').strip()
        if source:
            where_clauses.append("source = %s")
            params.append(source)

        statut = request.args.get('statut', '').strip()
        if statut:
            where_clauses.append("statut = %s")
            params.append(statut)

        semaine = request.args.get('semaine', '').strip()
        if semaine:
            where_clauses.append("semaine = %s")
            params.append(semaine)

        recherche = request.args.get('recherche', '').strip()
        if recherche:
            where_clauses.append("(artiste_saisi LIKE %s OR titre_saisi LIKE %s OR artist LIKE %s OR title LIKE %s)")
            like = f"%{recherche}%"
            params.extend([like, like, like, like])

        try:
            limit = int(request.args.get('limit', 50))
        except (ValueError, TypeError):
            limit = 50
        try:
            offset = int(request.args.get('offset', 0))
        except (ValueError, TypeError):
            offset = 0
        limit = max(1, min(limit, 500))
        offset = max(0, offset)

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        cursor.execute(f"SELECT COUNT(*) AS total FROM airvs_pool_titres{where_sql}", params)
        total = cursor.fetchone()['total'] or 0

        cursor.execute(f"""
            SELECT id, id_pool, sheet_alias, source, feuille, ligne, jour, semaine,
                   song_id, artist, title, year, album,
                   artiste_saisi, titre_saisi, annee_saisie, album_saisi, infos_saisi,
                   score_match, motif_rejet, valide_par,
                   statut, date_validation
            FROM airvs_pool_titres
            {where_sql}
            ORDER BY date_validation DESC, id DESC
            LIMIT %s OFFSET %s
        """, params + [limit, offset])
        rows = cursor.fetchall()

        for r in rows:
            if r.get('date_validation') and hasattr(r['date_validation'], 'isoformat'):
                r['date_validation'] = r['date_validation'].isoformat()

        cursor.close()
        db.close()
        return jsonify({"rows": rows, "total": total})
    except Exception as e:
        import traceback
        print(f"[pool/resultats] ERREUR : {type(e).__name__}: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e), "rows": [], "total": 0}), 500


@programmation_bp.route('/api/pool/export')
@login_requis
def api_pool_export():
    _assurer_schema_airvs_avance()
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"error": "DB indisponible"}), 500
        cursor = db.cursor(pymysql.cursors.DictCursor)

        where_clauses = []
        params = []
        for col in ('id_pool', 'source', 'statut', 'semaine'):
            val = request.args.get(col, '').strip()
            if val:
                where_clauses.append(f"{col} = %s")
                params.append(val)
        recherche = request.args.get('recherche', '').strip()
        if recherche:
            where_clauses.append("(artiste_saisi LIKE %s OR titre_saisi LIKE %s OR artist LIKE %s OR title LIKE %s)")
            like = f"%{recherche}%"
            params.extend([like, like, like, like])
        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        cursor.execute(f"""
            SELECT id_pool, sheet_alias, source, feuille, ligne, jour, semaine,
                   song_id, artist, title, year, album,
                   artiste_saisi, titre_saisi, annee_saisie, album_saisi, infos_saisi,
                   score_match, motif_rejet, valide_par,
                   statut, date_validation
            FROM airvs_pool_titres
            {where_sql}
            ORDER BY date_validation DESC, id DESC
            LIMIT 5000
        """, params)
        rows = cursor.fetchall()
        cursor.close()
        db.close()

        si = io.StringIO()
        si.write('\ufeff')
        writer = csv.writer(si, delimiter=';', quoting=csv.QUOTE_MINIMAL)
        writer.writerow([
            'id_pool', 'sheet_alias', 'source', 'feuille', 'ligne', 'jour', 'semaine',
            'song_id', 'artist', 'title', 'year', 'album',
            'artiste_saisi', 'titre_saisi', 'annee_saisie', 'album_saisi', 'infos_saisi',
            'score_match', 'motif_rejet', 'valide_par',
            'statut', 'date_validation'
        ])
        for r in rows:
            writer.writerow([
                r.get('id_pool'), r.get('sheet_alias'), r.get('source'),
                r.get('feuille'), r.get('ligne'), r.get('jour'), r.get('semaine'),
                r.get('song_id'), r.get('artist'), r.get('title'),
                r.get('year'), r.get('album'),
                r.get('artiste_saisi'), r.get('titre_saisi'), r.get('annee_saisie'),
                r.get('album_saisi'), r.get('infos_saisi'),
                r.get('score_match'), r.get('motif_rejet'), r.get('valide_par'),
                r.get('statut'),
                r['date_validation'].isoformat() if r.get('date_validation') and hasattr(r['date_validation'], 'isoformat') else ''
            ])
        csv_data = si.getvalue()
        return Response(
            csv_data,
            mimetype='text/csv; charset=utf-8',
            headers={'Content-Disposition': 'attachment; filename=airvs_pool_titres.csv'}
        )
    except Exception as e:
        import traceback
        print(f"[pool/export] ERREUR : {type(e).__name__}: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@programmation_bp.route('/api/pools/list')
@login_requis
def api_pools_list():
    """Liste les pools existants (id_pool distincts) avec leur nombre de titres
    validés et leur date de dernière validation."""
    _assurer_schema_airvs_avance()
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"status": "error", "message": "DB indisponible"}), 500
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute("""
            SELECT id_pool,
                   COUNT(CASE WHEN statut='valide'  THEN 1 END) AS nb_valide,
                   COUNT(CASE WHEN statut='rejete'  THEN 1 END) AS nb_rejete,
                   COUNT(CASE WHEN statut='manquant' THEN 1 END) AS nb_manquant,
                   COUNT(CASE WHEN statut='doublon' THEN 1 END) AS nb_doublon,
                   MAX(date_validation) AS derniere_validation
            FROM airvs_pool_titres
            GROUP BY id_pool
            ORDER BY derniere_validation DESC
        """)
        rows = cursor.fetchall()
        for r in rows:
            if r.get('derniere_validation') and hasattr(r['derniere_validation'], 'isoformat'):
                r['derniere_validation'] = r['derniere_validation'].isoformat()
        cursor.close()
        db.close()
        return jsonify({"status": "ok", "pools": rows})
    except Exception as e:
        import traceback
        print(f"[pools/list] ERREUR : {type(e).__name__}: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/pool/auto/list')
@login_requis
def api_pool_auto_list():
    """Liste les auto-validations configurées."""
    _assurer_schema_airvs_avance()
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"status": "error", "message": "DB indisponible"}), 500
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute("""
            SELECT id, sheet_alias, onglet, frequence, heure, jour_semaine,
                   actif, derniere_execution, date_creation
            FROM airvs_pool_auto_validation
            ORDER BY id ASC
        """)
        rows = cursor.fetchall()
        cursor.close()
        db.close()
        return jsonify({"status": "ok", "items": rows})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/pool/auto/upsert', methods=['POST'])
@login_requis
def api_pool_auto_upsert():
    """Crée ou met à jour une auto-validation."""
    _assurer_schema_airvs_avance()
    try:
        data = request.get_json(force=True) or {}
        auto_id = data.get('id')
        sheet_alias = (data.get('sheet_alias') or '').strip()
        onglet = (data.get('onglet') or '').strip()
        frequence = (data.get('frequence') or 'quotidienne').strip()
        heure = (data.get('heure') or '03:00').strip()
        jour_semaine = data.get('jour_semaine')
        actif = 1 if data.get('actif', True) else 0

        if not sheet_alias or not onglet:
            return jsonify({"status": "error",
                            "message": "Champs requis: sheet_alias, onglet"}), 400
        if frequence not in ('quotidienne', 'hebdo'):
            return jsonify({"status": "error",
                            "message": "frequence doit être 'quotidienne' ou 'hebdo'"}), 400
        if frequence == 'hebdo':
            try:
                jour_semaine = int(jour_semaine) if jour_semaine is not None else None
                if jour_semaine is None or not (1 <= jour_semaine <= 7):
                    return jsonify({"status": "error",
                                    "message": "jour_semaine requis (1-7) pour frequence=hebdo"}), 400
            except (ValueError, TypeError):
                return jsonify({"status": "error",
                                "message": "jour_semaine doit être un entier 1-7"}), 400
        else:
            jour_semaine = None

        db = get_db_connection()
        if not db:
            return jsonify({"status": "error", "message": "DB indisponible"}), 500
        cursor = db.cursor()
        if auto_id:
            cursor.execute(
                "UPDATE airvs_pool_auto_validation "
                "SET sheet_alias=%s, onglet=%s, frequence=%s, heure=%s, "
                "    jour_semaine=%s, actif=%s WHERE id=%s",
                (sheet_alias, onglet, frequence, heure + ':00' if heure.count(':') == 1 else heure,
                 jour_semaine, actif, auto_id)
            )
        else:
            cursor.execute(
                "INSERT INTO airvs_pool_auto_validation "
                "(sheet_alias, onglet, frequence, heure, jour_semaine, actif, date_creation) "
                "VALUES (%s, %s, %s, %s, %s, %s, NOW())",
                (sheet_alias, onglet, frequence, heure + ':00' if heure.count(':') == 1 else heure,
                 jour_semaine, actif)
            )
            auto_id = cursor.lastrowid
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"status": "ok", "id": auto_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/pool/auto/toggle', methods=['POST'])
@login_requis
def api_pool_auto_toggle():
    """Active/désactive une auto-validation."""
    _assurer_schema_airvs_avance()
    try:
        data = request.get_json(force=True) or {}
        auto_id = data.get('id')
        actif = 1 if data.get('actif') else 0
        if not auto_id:
            return jsonify({"status": "error", "message": "id requis"}), 400
        db = get_db_connection()
        if not db:
            return jsonify({"status": "error", "message": "DB indisponible"}), 500
        cursor = db.cursor()
        cursor.execute(
            "UPDATE airvs_pool_auto_validation SET actif=%s WHERE id=%s",
            (actif, auto_id)
        )
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/pool/auto/delete', methods=['POST'])
@login_requis
def api_pool_auto_delete():
    """Supprime une auto-validation."""
    _assurer_schema_airvs_avance()
    try:
        data = request.get_json(force=True) or {}
        auto_id = data.get('id')
        if not auto_id:
            return jsonify({"status": "error", "message": "id requis"}), 400
        db = get_db_connection()
        if not db:
            return jsonify({"status": "error", "message": "DB indisponible"}), 500
        cursor = db.cursor()
        cursor.execute(
            "DELETE FROM airvs_pool_auto_validation WHERE id=%s",
            (auto_id,)
        )
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ─── Routes : Auto-check Google Sheets ─────────────────────────────────

@programmation_bp.route('/api/auto_check/config', methods=['GET'])
@login_requis
def api_auto_check_config_get():
    """Retourne la configuration de l'auto-check Google Sheets.
    Si aucune config n'existe encore, retourne les valeurs par défaut."""
    _assurer_schema_airvs_avance()
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"status": "error", "message": "DB indisponible"}), 500
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute("SELECT COUNT(*) AS cnt FROM airvs_auto_check_config")
        row = cursor.fetchone()
        if row['cnt'] == 0:
            cursor.close()
            db.close()
            return jsonify({
                "status": "ok",
                "config": {
                    "actif": False,
                    "frequence": "2x_jour",
                    "horaires": ["03:00", "15:00"],
                    "sheets_surveilles": [],
                    "derniere_execution": None,
                    "prochaine_execution": None,
                }
            })
        cursor.execute("""
            SELECT actif, frequence, horaires, sheets_surveilles,
                   derniere_execution, prochaine_execution
            FROM airvs_auto_check_config LIMIT 1
        """)
        cfg = cursor.fetchone()
        cursor.close()
        db.close()
        if isinstance(cfg.get('horaires'), str):
            cfg['horaires'] = json.loads(cfg['horaires'])
        if isinstance(cfg.get('sheets_surveilles'), str):
            cfg['sheets_surveilles'] = json.loads(cfg['sheets_surveilles'])
        return jsonify({"status": "ok", "config": cfg})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/auto_check/config', methods=['POST'])
@login_requis
def api_auto_check_config_save():
    """Sauvegarde la configuration de l'auto-check."""
    _assurer_schema_airvs_avance()
    try:
        data = request.get_json(force=True) or {}
        actif = 1 if data.get('actif', False) else 0
        frequence = (data.get('frequence') or '2x_jour').strip()
        horaires = data.get('horaires', ['03:00', '15:00'])
        sheets_surveilles = data.get('sheets_surveilles', [])

        frequences_valides = ['1x_jour', '2x_jour', '3x_jour', 'personnalise']
        if frequence not in frequences_valides:
            return jsonify({"status": "error",
                            "message": f"Fréquence invalide. Valeurs: {frequences_valides}"}), 400

        if not isinstance(horaires, list) or len(horaires) == 0:
            return jsonify({"status": "error",
                            "message": "Au moins un horaire requis"}), 400
        for h in horaires:
            import re as _re
            if not _re.match(r'^\d{2}:\d{2}$', h):
                return jsonify({"status": "error",
                                "message": f"Format horaire invalide: {h} (HH:MM attendu)"}), 400

        if not isinstance(sheets_surveilles, list):
            return jsonify({"status": "error",
                            "message": "sheets_surveilles doit être un tableau"}), 400

        prochaine_execution = _calculer_prochaine_execution(frequence, horaires)

        db = get_db_connection()
        if not db:
            return jsonify({"status": "error", "message": "DB indisponible"}), 500
        cursor = db.cursor()

        cursor.execute("SELECT COUNT(*) AS cnt FROM airvs_auto_check_config")
        cnt = cursor.fetchone()[0]
        if cnt > 0:
            cursor.execute("""
                UPDATE airvs_auto_check_config
                SET actif=%s, frequence=%s, horaires=%s, sheets_surveilles=%s,
                    prochaine_execution=%s
            """, (actif, frequence, json.dumps(horaires),
                  json.dumps(sheets_surveilles), prochaine_execution))
        else:
            cursor.execute("""
                INSERT INTO airvs_auto_check_config
                (actif, frequence, horaires, sheets_surveilles, prochaine_execution, date_creation)
                VALUES (%s, %s, %s, %s, %s, NOW())
            """, (actif, frequence, json.dumps(horaires),
                  json.dumps(sheets_surveilles), prochaine_execution))
        db.commit()
        cursor.close()
        db.close()
        return jsonify({"status": "ok", "message": "Configuration auto-check sauvegardée"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/auto_check/trigger', methods=['POST'])
@login_requis
def api_auto_check_trigger():
    """Déclenche manuellement une vérification des Google Sheets."""
    _assurer_schema_airvs_avance()
    try:
        data = request.get_json(force=True) or {}
        sheets_a_checker = data.get('sheets', [])

        if not sheets_a_checker:
            db = get_db_connection()
            if not db:
                return jsonify({"status": "error", "message": "DB indisponible"}), 500
            cursor = db.cursor(pymysql.cursors.DictCursor)
            cursor.execute("SELECT sheets_surveilles FROM airvs_auto_check_config LIMIT 1")
            cfg = cursor.fetchone()
            cursor.close()
            db.close()
            if cfg:
                sheets_a_checker = json.loads(cfg['sheets_surveilles']) if isinstance(cfg['sheets_surveilles'], str) else cfg['sheets_surveilles']
            if not sheets_a_checker:
                config = _charger_config_json()
                sheets_a_checker = list(config.get("sheets", {}).keys())

        if not sheets_a_checker:
            return jsonify({"status": "error",
                            "message": "Aucun Sheet à vérifier. Configurez les sheets dans l'auto-check."}), 400

        parametres = {
            'sheets_a_checker': sheets_a_checker,
            'declenche_par': 'manuel',
        }
        db = get_db_connection()
        if not db:
            return jsonify({"status": "error", "message": "DB indisponible"}), 500
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
            "VALUES ('AUTO_CHECK_SHEETS', 'en_attente', %s, NOW())",
            (json.dumps(parametres),)
        )
        task_id = cursor.lastrowid
        db.commit()
        cursor.close()
        db.close()
        return jsonify({
            "status": "ok",
            "task_id": task_id,
            "message": f"Vérification lancée pour {len(sheets_a_checker)} sheet(s) — tâche #{task_id}"
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/auto_check/notifications', methods=['GET'])
@login_requis
def api_auto_check_notifications():
    """Retourne les notifications de nouvelles entrées Google Sheets."""
    _assurer_schema_airvs_avance()
    try:
        non_lues = request.args.get('non_lues', '0') == '1'
        limit = min(int(request.args.get('limit', '50')), 200)

        db = get_db_connection()
        if not db:
            return jsonify({"status": "error", "message": "DB indisponible"}), 500
        cursor = db.cursor(pymysql.cursors.DictCursor)

        if non_lues:
            cursor.execute("""
                SELECT id, type_notification, sheet_alias, message, lu, date_creation
                FROM airvs_notifications
                WHERE lu = 0
                ORDER BY date_creation DESC LIMIT %s
            """, (limit,))
        else:
            cursor.execute("""
                SELECT id, type_notification, sheet_alias, message, lu, date_creation
                FROM airvs_notifications
                ORDER BY date_creation DESC LIMIT %s
            """, (limit,))

        rows = cursor.fetchall()

        cursor.execute("SELECT COUNT(*) AS cnt FROM airvs_notifications WHERE lu = 0")
        nb_non_lues = cursor.fetchone()['cnt']

        cursor.close()
        db.close()
        return jsonify({
            "status": "ok",
            "notifications": rows,
            "nb_non_lues": nb_non_lues
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/auto_check/notifications/mark_read', methods=['POST'])
@login_requis
def api_auto_check_notifications_mark_read():
    """Marque des notifications comme lues."""
    _assurer_schema_airvs_avance()
    try:
        data = request.get_json(force=True) or {}
        db = get_db_connection()
        if not db:
            return jsonify({"status": "error", "message": "DB indisponible"}), 500
        cursor = db.cursor()

        if data.get('all'):
            cursor.execute("UPDATE airvs_notifications SET lu = 1 WHERE lu = 0")
        elif data.get('ids'):
            placeholders = ','.join(['%s'] * len(data['ids']))
            cursor.execute(
                f"UPDATE airvs_notifications SET lu = 1 WHERE id IN ({placeholders})",
                tuple(data['ids'])
            )
        else:
            cursor.close()
            db.close()
            return jsonify({"status": "error", "message": "ids[] ou all=true requis"}), 400

        db.commit()
        cursor.close()
        db.close()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@programmation_bp.route('/api/auto_check/last_result', methods=['GET'])
@login_requis
def api_auto_check_last_result():
    """Retourne le résultat de la dernière vérification auto-check."""
    _assurer_schema_airvs_avance()
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"status": "error", "message": "DB indisponible"}), 500
        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute("""
            SELECT id, statut, log_resultat, date_creation, date_fin
            FROM taches_planifiees
            WHERE type_action = 'AUTO_CHECK_SHEETS'
            ORDER BY id DESC LIMIT 1
        """)
        row = cursor.fetchone()
        cursor.close()
        db.close()
        if not row:
            return jsonify({"status": "ok", "last_result": None})
        return jsonify({"status": "ok", "last_result": row})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
