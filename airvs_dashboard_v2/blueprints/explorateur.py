import os

import pymysql
from flask import Blueprint, jsonify, request, send_file

from utils import get_db_connection
from blueprints.auth import login_requis

explorateur_bp = Blueprint('explorateur', __name__)


# ── API Recherche rapide ──
@explorateur_bp.route('/api/search', methods=['POST'])
@login_requis
def api_search():
    data = request.json
    recherche = data.get('recherche', '').strip()
    if not recherche:
        return jsonify([])

    db = get_db_connection()
    if not db:
        return jsonify({"error": "Erreur DB"}), 500

    cursor = db.cursor()
    query = """
        SELECT ID, artist, title, year, duration, `path`, comments
        FROM songs
        WHERE song_type = 0 AND (artist LIKE %s OR title LIKE %s)
        ORDER BY artist ASC LIMIT 100
    """
    criteres = f"%{recherche}%"
    cursor.execute(query, (criteres, criteres))
    titres = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify([dict(titre) for titre in titres])

@explorateur_bp.route('/api/latest')
@login_requis
def api_latest():
    db = get_db_connection()
    if not db:
        return jsonify({"error": "Erreur DB"}), 500
    cursor = db.cursor()
    query = """
        SELECT ID, artist, title, year, duration, `path`, comments
        FROM songs
        WHERE song_type = 0
        ORDER BY ID DESC LIMIT 30
    """
    cursor.execute(query)
    titres = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify([dict(t) for t in titres])


# ── Recherche plein texte multi-champs ──
@explorateur_bp.route('/api/search_fulltext', methods=['POST'])
@login_requis
def api_search_fulltext():
    """Recherche plein texte sur tous les champs de la base RadioDJ."""
    data = request.json
    recherche = data.get('recherche', '').strip()
    if not recherche:
        return jsonify([])

    db = get_db_connection()
    if not db:
        return jsonify({"error": "Erreur DB"}), 500

    cursor = db.cursor()

    criteres = f"%{recherche}%"

    # Si la recherche est potentiellement une année (4 chiffres)
    if recherche.isdigit() and len(recherche) == 4:
        query = """
            SELECT s.ID, s.artist, s.title, s.year, s.duration, s.`path`,
                   s.comments, s.album, MAX(g.name) AS genre_name
            FROM songs s
            LEFT JOIN genre g ON s.id_genre = g.id
            WHERE s.song_type = 0
              AND (
                  s.artist LIKE %s
                  OR s.title LIKE %s
                  OR s.album LIKE %s
                  OR s.comments LIKE %s
                  OR s.`path` LIKE %s
                  OR g.name LIKE %s
                  OR s.year = %s
              )
            GROUP BY s.ID
            ORDER BY
                CASE
                    WHEN s.artist LIKE %s THEN 0
                    WHEN s.title LIKE %s THEN 1
                    WHEN s.year = %s THEN 2
                    ELSE 3
                END,
                s.artist ASC
            LIMIT 200
        """
        params = (criteres, criteres, criteres, criteres, criteres, criteres,
                  int(recherche),
                  criteres, criteres, int(recherche))
    else:
        query = """
            SELECT s.ID, s.artist, s.title, s.year, s.duration, s.`path`,
                   s.comments, s.album, MAX(g.name) AS genre_name
            FROM songs s
            LEFT JOIN genre g ON s.id_genre = g.id
            WHERE s.song_type = 0
              AND (
                  s.artist LIKE %s
                  OR s.title LIKE %s
                  OR s.album LIKE %s
                  OR s.comments LIKE %s
                  OR s.`path` LIKE %s
                  OR g.name LIKE %s
              )
            GROUP BY s.ID
            ORDER BY
                CASE
                    WHEN s.artist LIKE %s THEN 0
                    WHEN s.title LIKE %s THEN 1
                    ELSE 2
                END,
                s.artist ASC
            LIMIT 200
        """
        params = (criteres, criteres, criteres, criteres, criteres, criteres,
                  criteres, criteres)

    cursor.execute(query, params)
    titres = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify([dict(t) for t in titres])


# ── Écouter un morceau ──
@explorateur_bp.route('/ecouter/<int:song_id>')
@login_requis
def ecouter_morceau(song_id):
    db = get_db_connection()
    if not db:
        return "Erreur DB", 500

    cursor = db.cursor()
    cursor.execute("SELECT `path` FROM songs WHERE ID = %s", (song_id,))
    result = cursor.fetchone()
    cursor.close()
    db.close()

    if not result or not result['path']:
        return "Fichier introuvable", 404

    filepath = result['path'].replace("/", "\\")
    if not os.path.exists(filepath):
        return "Fichier absent du disque dur", 404

    return send_file(filepath, mimetype='audio/mpeg')


# ══════════════════════════════════════════
# ROUTES CATÉGORIES / SOUS-CATÉGORIES
# ══════════════════════════════════════════
@explorateur_bp.route('/api/categories', methods=['GET'])
@login_requis
def get_categories():
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"error": "Erreur de connexion"}), 500
        cursor = db.cursor()
        cursor.execute("SELECT ID, name FROM category ORDER BY name")
        cats = cursor.fetchall()
        cursor.close()
        db.close()
        return jsonify(cats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@explorateur_bp.route('/api/subcategories', methods=['POST'])
@login_requis
def get_subcategories():
    try:
        cat_id = request.json.get('category_id')
        db = get_db_connection()
        if not db:
            return jsonify({"error": "Erreur de connexion"}), 500
        cursor = db.cursor()
        cursor.execute(
            "SELECT ID, name FROM subcategory WHERE parentid = %s ORDER BY name",
            (cat_id,)
        )
        subcats = cursor.fetchall()
        cursor.close()
        db.close()
        return jsonify(subcats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@explorateur_bp.route('/api/genres')
@login_requis
def get_genres():
    """Liste tous les genres RadioDJ (pour dropdown Programmation avancée)."""
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"error": "Erreur de connexion"}), 500
        cursor = db.cursor()
        cursor.execute("SELECT ID, name FROM genre ORDER BY name")
        genres = cursor.fetchall()
        cursor.close()
        db.close()
        return jsonify(genres)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@explorateur_bp.route('/api/souscategories_all')
@login_requis
def get_all_subcategories():
    """Liste toutes les sous-catégories RadioDJ avec leur parent (pour dropdown
    Programmation avancée — pas de filtre par catégorie parent)."""
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"error": "Erreur de connexion"}), 500
        cursor = db.cursor()
        cursor.execute(
            "SELECT s.ID, s.name, s.parentid, c.name AS parent_name "
            "FROM subcategory s LEFT JOIN category c ON c.ID = s.parentid "
            "ORDER BY c.name, s.name"
        )
        rows = cursor.fetchall()
        cursor.close()
        db.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@explorateur_bp.route('/api/radiodj/titres')
@login_requis
def api_radiodj_titres():
    """Recherche LARGE de titres dans la base RadioDJ (pour sélecteur Programmation avancée).
    Paramètres:
      - q: texte recherché ( LIKE %q% sur artist, title, album )
      - limit: 50 max 200
    Retourne [{ID, artist, title, album, year, comments}]."""
    try:
        q = (request.args.get('q') or '').strip()
        try:
            limit = min(max(int(request.args.get('limit', '50')), 1), 200)
        except (ValueError, TypeError):
            limit = 50
        db = get_db_connection()
        if not db:
            return jsonify([])
        cursor = db.cursor(pymysql.cursors.DictCursor)
        if not q:
            cursor.execute(
                "SELECT ID, artist, title, album, year, comments "
                "FROM songs WHERE song_type = 0 "
                "ORDER BY date_added DESC LIMIT %s",
                (limit,)
            )
        else:
            like = f"%{q}%"
            cursor.execute(
                "SELECT ID, artist, title, album, year, comments "
                "FROM songs WHERE song_type = 0 "
                "AND (artist LIKE %s OR title LIKE %s OR album LIKE %s) "
                "ORDER BY artist, title LIMIT %s",
                (like, like, like, limit)
            )
        rows = cursor.fetchall()
        for r in rows:
            if r.get('year') is not None and hasattr(r['year'], 'isoformat'):
                r['year'] = r['year'].year if hasattr(r['year'], 'year') else int(r['year'])
            r['in_azura'] = (str(r.get('comments', '')).strip() == 'IN_AZURACAST')
        cursor.close()
        db.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════
# ROUTES RECHERCHE AVANCÉE & COPIE (Atelier)
# ══════════════════════════════════════════
@explorateur_bp.route('/api/search_advanced', methods=['POST'])
@login_requis
def api_search_advanced():
    data = request.json
    artist = data.get('artist', '').strip()
    title = data.get('title', '').strip()
    year = data.get('year', '').strip()
    genre = data.get('genre', '').strip()
    cat_id = data.get('category_id')
    subcat_id = data.get('subcategory_id')

    if not any([artist, title, year, genre, cat_id]):
        return jsonify([])

    db = get_db_connection()
    if not db:
        return jsonify({"error": "Erreur DB"}), 500

    cursor = db.cursor()

    conditions = ["s.song_type = 0"]
    parametres = []

    if artist:
        conditions.append("s.artist LIKE %s")
        parametres.append(f"%{artist}%")
    if title:
        conditions.append("s.title LIKE %s")
        parametres.append(f"%{title}%")
    if year:
        conditions.append("s.year = %s")
        parametres.append(year)
    if genre:
        conditions.append("g.name LIKE %s")
        parametres.append(f"%{genre}%")

    # Arborescence catégorie / sous-catégorie
    if cat_id and subcat_id:
        conditions.append("s.id_subcat = %s")
        parametres.append(subcat_id)
    elif cat_id and not subcat_id:
        conditions.append("s.id_subcat IN (SELECT ID FROM subcategory WHERE parentid = %s)")
        parametres.append(cat_id)

    clause_where = "WHERE " + " AND ".join(conditions)
    query = (
        f"SELECT s.ID, s.path, s.artist, s.title, s.year, s.duration, MAX(g.name) AS genre_name "
        f"FROM songs s LEFT JOIN genre g ON s.id_genre = g.id "
        f"{clause_where} GROUP BY s.ID ORDER BY s.artist ASC LIMIT 500"
    )

    cursor.execute(query, tuple(parametres))
    titres = cursor.fetchall()
    cursor.close()
    db.close()
    return jsonify([dict(t) for t in titres if t.get('path')])
