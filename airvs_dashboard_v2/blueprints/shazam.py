# ██████ AIRVS PATCH PROGRAMMATION v2.5 — 2026-10-09 — ZONE DOWNLOAD AI ██████
# Correctif : réintégration des lignes déjà « Matché » dans le flux éditorial
#   Pont → AzuraCast (prefill + pipeline) : défaut de filtre statut
#   'nouveau,matche', résolution directe par match_song_id (sans repasser
#   dans le moteur), qualification auto 'pousse_azura' des morceaux déjà
#   IN_AZURACAST. Cause de la régression : le filtre R5 défaut='nouveau'
#   était écrit quand la colonne statut n'était jamais alimentée (bug
#   TINYINT v2.3) — une fois l'écriture réparée, les matchés sortaient
#   du Pont et n'étaient plus poussés vers AzuraCast.
# Base : v2.4 (persister normalisé + bandeau). utils.py/pipeline_import.py
#   v2.4 inchangés — seuls shazam.py et azuracast.py évoluent en v2.5.
# Vérif déploiement : cette ligne doit être la 1re ligne du fichier sur .39
"""blueprints/shazam.py -- Shazam + VPS sync AIRVS.

Routes extraites de app.py pour le blueprint shazam_bp.

Intégration Shazam (MacroDroid → Flask → MariaDB) :
  - Endpoint public /api/shazam/add (sans auth, appelé par le téléphone)
  - CRUD entrées Shazam (list, animateurs, update, delete)
  - Import CSV/JSONL avec matching 5 passes contre RadioDJ
  - Pré-remplissage (prefill) pour le Pont
  - Sync VPS immédiat (soumissions + shazam_ext depuis OVH)
  - Pipeline Shazam (matching synchrone + sync Sheet via worker)
"""

import os
import re
import json
import time
import csv as csv_mod
import io
import unicodedata
from pathlib import Path
from datetime import datetime

import pymysql
from flask import Blueprint, request, jsonify

from utils import (get_db_connection, _logger, _assurer_schema_airvs_avance,
                   _charger_config_json, correler_manquants_radiodj,
                   assurer_type_match_passe)
from blueprints.auth import login_requis


shazam_bp = Blueprint('shazam', __name__)


# ─── Helpers ────────────────────────────────────────────────────────────

def _assurer_schema_shazam():
    """Crée la table airvs_shazam si elle n'existe pas."""
    try:
        db = get_db_connection()
        if not db:
            return False
        cursor = db.cursor()
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
        # Migration : ajouter la colonne animateur si elle n'existe pas
        try:
            cursor.execute("SHOW COLUMNS FROM airvs_shazam LIKE 'animateur'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE airvs_shazam ADD COLUMN animateur VARCHAR(100) DEFAULT NULL AFTER source")
                cursor.execute("CREATE INDEX idx_animateur ON airvs_shazam (animateur)")
                db.commit()
                _logger.info("Migration : colonne animateur ajoutée à airvs_shazam")
        except Exception as e:
            _logger.debug(f"Migration animateur (ignorée si déjà faite) : {e}")

        # ── R1 : Migration statut + match_song_id + match_passe sur airvs_shazam ──
        try:
            cursor.execute("SHOW COLUMNS FROM airvs_shazam LIKE 'statut'")
            if not cursor.fetchone():
                cursor.execute(
                    "ALTER TABLE airvs_shazam "
                    "ADD COLUMN statut ENUM('nouveau','matche','pousse_azura','rejete') DEFAULT 'nouveau' "
                    "AFTER animateur, "
                    "ADD COLUMN match_song_id INT DEFAULT NULL AFTER statut, "
                    "ADD COLUMN match_passe VARCHAR(50) DEFAULT NULL AFTER match_song_id, "
                    "ADD INDEX idx_statut (statut)"
                )
                db.commit()
                _logger.info("Migration : colonnes statut/match_song_id/match_passe ajoutées à airvs_shazam")
        except Exception as e:
            _logger.debug(f"Migration statut shazam (ignorée si déjà faite) : {e}")

        # ── R2 : Migration statut + match_song_id/match_passe sur airvs_animateurs ──
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = 'airvs_animateurs'"
            )
            # DictCursor global : jamais fetchone()[0] (KeyError) — accès dict-safe
            _row_t = cursor.fetchone()
            if _row_t and list(_row_t.values())[0]:
                # Ajouter match_song_id si absent (créé par le worker VPS ou migration)
                try:
                    cursor.execute("SHOW COLUMNS FROM airvs_animateurs LIKE 'match_song_id'")
                    if not cursor.fetchone():
                        cursor.execute(
                            "ALTER TABLE airvs_animateurs "
                            "ADD COLUMN match_song_id INT DEFAULT NULL, "
                            "ADD COLUMN match_passe VARCHAR(50) DEFAULT NULL"
                        )
                        db.commit()
                        _logger.info("Migration : colonnes match_song_id/match_passe ajoutées à airvs_animateurs")
                except Exception as e:
                    _logger.debug(f"Migration match_song_id animateurs (ignorée) : {e}")

                # Ajouter statut si absent
                cursor.execute("SHOW COLUMNS FROM airvs_animateurs LIKE 'statut'")
                if not cursor.fetchone():
                    cursor.execute(
                        "ALTER TABLE airvs_animateurs "
                        "ADD COLUMN statut ENUM('nouveau','matche','pousse_azura','rejete') DEFAULT 'nouveau', "
                        "ADD INDEX idx_statut (statut)"
                    )
                    db.commit()
                    _logger.info("Migration : colonne statut ajoutée à airvs_animateurs")
        except Exception as e:
            _logger.debug(f"Migration statut animateurs (ignorée si déjà faite) : {e}")

        # ── R2-c (v2.3) : type de match_passe sur airvs_animateurs ──
        # La table est créée par le worker VPS avec match_passe TINYINT alors
        # que le dashboard écrit des chaînes ('trouve_import', méthodes du
        # moteur…) : erreur 1366 « Incorrect integer value » avalée par les
        # try/except → sources jamais « Matché », « source(s) Matché : 0 ».
        # Conversion idempotente VARCHAR(50) + rattrapage one-shot (utils.py).
        try:
            assurer_type_match_passe(logger=_logger)
        except Exception:
            pass

        # ── R1-bis : enrichir l'ENUM statut des tables sources avec 'rejete_corrige' ──
        # Nécessaire à /api/manquants/corriger (R16) : sans cette valeur dans l'ENUM,
        # l'UPDATE statut='rejete_corrige' échoue en mode SQL strict
        # (Data truncated → exception → HTTP 500 sur le bouton ✎ Corriger).
        for _tbl_enum in ('airvs_shazam', 'airvs_animateurs'):
            try:
                cursor.execute(
                    "SELECT COLUMN_TYPE FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = %s "
                    "AND column_name = 'statut'",
                    (_tbl_enum,)
                )
                row_e = cursor.fetchone()
                # DictCursor global : accès par nom de colonne (jamais [0])
                _col_type = str(list(row_e.values())[0]) if row_e else ''
                if _col_type and 'rejete_corrige' not in _col_type.lower():
                    cursor.execute(
                        "ALTER TABLE %s MODIFY COLUMN statut "
                        "ENUM('nouveau','matche','pousse_azura','rejete','rejete_corrige') DEFAULT 'nouveau'"
                        % _tbl_enum
                    )
                    db.commit()
                    _logger.info(f"Migration : ENUM statut enrichi avec 'rejete_corrige' sur {_tbl_enum}")
            except Exception as e:
                _logger.warning(f"Migration ENUM statut {_tbl_enum} échouée : {e}")

        db.commit()
        cursor.close()
        db.close()
        return True
    except Exception as e:
        _logger.error(f"Erreur création table : {e}")
        return False


def _persister_manquants_shazam(not_found, source_type):
    """Persiste les titres non trouvés dans airvs_manquants.

    Args:
        not_found: liste de dicts avec clés 'artist', 'title',
                   et optionnellement 'animateur' et 'source' (origine airvs_shazam)
        source_type: 'shazam' ou 'animateurs'
    Returns:
        int: nombre de lignes insérées
    """
    if not not_found:
        return 0
    try:
        db_m = get_db_connection()
        if not db_m:
            return 0
        cur_m = db_m.cursor()
        nb = 0
        for item in not_found:
            # v2.4 — normalisation \s+ à la création : tabulations /
            # retours ligne / espaces insécables des saisies (collages,
            # tags de compilation) ne doivent pas s'incruster en DB,
            # sinon les corrélations (égalité/TRIM + LIKE) ratent le
            # manquant à vie (cas « Maxime Le Forestier\t »).
            art = re.sub(r'\s+', ' ', str(item.get('artist') or '')).strip()[:500]
            tit = re.sub(r'\s+', ' ', str(item.get('title') or '')).strip()[:500]
            if not art and not tit:
                continue
            anim = str(item.get('animateur') or '').strip() or None
            orig = str(item.get('source') or '').strip() or None

            # ── R14 : Dédup cross-source — vérifier si déjà rejeté dans UNE QUELCONQUE source ──
            try:
                cur_m.execute(
                    "SELECT id FROM airvs_manquants "
                    "WHERE LOWER(artiste) = LOWER(%s) AND LOWER(titre) = LOWER(%s) "
                    "AND statut IN ('rejete','rejete_corrigé') LIMIT 1",
                    (art, tit)
                )
                if cur_m.fetchone():
                    continue  # Déjà rejeté, ne pas re-persister
            except Exception:
                pass  # Ne pas bloquer si la requête échoue

            try:
                cur_m.execute(
                    "INSERT IGNORE INTO airvs_manquants "
                    "(artiste, titre, source, animateur, origine) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (art, tit, source_type, anim, orig)
                )
                if cur_m.rowcount > 0:
                    nb += 1
            except Exception:
                pass
        db_m.commit()
        cur_m.close()
        db_m.close()
        return nb
    except Exception:
        return 0


def _sync_vps_immediat(db):
    """Option C — déclenche un sync VPS immédiat et attend la complétion.

    Insère les tâches SYNC_VPS_SOUMISSIONS et SYNC_VPS_SHAZAM_EXT
    dans taches_planifiees, puis polle la DB toutes les 2 secondes
    jusqu'à ce que les deux tâches soient terminées ou en erreur (timeout 30 s).

    La machine Windows n'ayant pas d'accès internet, le sync passe
    obligatoirement par le worker Ubuntu via la DB partagée.

    Args:
        db: connexion pymysql ouverte (sera réutilisée, pas fermée)
    Returns:
        dict avec 'ok' (bool) et 'logs' (list[str]).
    """
    logs = []
    task_ids = []
    try:
        cursor = db.cursor(pymysql.cursors.DictCursor)

        # 1) Insérer les 2 tâches de sync (si aucune n'est déjà en_attente/en_cours)
        for action in ('SYNC_VPS_SOUMISSIONS', 'SYNC_VPS_SHAZAM_EXT'):
            cursor.execute(
                "SELECT id, statut FROM taches_planifiees "
                "WHERE type_action = %s AND statut IN ('en_attente', 'en_cours') "
                "ORDER BY id ASC LIMIT 1",
                (action,)
            )
            existing = cursor.fetchone()
            if existing:
                task_ids.append(existing['id'])
                logs.append(f'[Sync VPS] {action} : tâche #{existing["id"]} déjà en file ({existing["statut"]})')
            else:
                cursor.execute(
                    "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
                    "VALUES (%s, 'en_attente', %s, NOW())",
                    (action, json.dumps({"option_c": True}))
                )
                tid = cursor.lastrowid
                task_ids.append(tid)
                db.commit()
                logs.append(f'[Sync VPS] {action} : tâche #{tid} insérée')

        cursor.close()

        # 2) Attendre la complétion (polling toutes les 2 s, timeout 30 s)
        timeout = 30
        poll_interval = 2
        elapsed = 0
        while elapsed < timeout:
            time.sleep(poll_interval)
            elapsed += poll_interval

            cursor2 = db.cursor(pymysql.cursors.DictCursor)
            cursor2.execute(
                "SELECT id, type_action, statut FROM taches_planifiees "
                "WHERE id IN (%s, %s)" % tuple(task_ids)
            )
            rows = cursor2.fetchall()
            cursor2.close()

            pending = [r for r in rows if r['statut'] in ('en_attente', 'en_cours')]
            if not pending:
                for r in rows:
                    status_icon = '✓' if r['statut'] == 'termine' else '⚠'
                    logs.append(f'[Sync VPS] {status_icon} {r["type_action"]} #{r["id"]} → {r["statut"]} ({elapsed}s)')
                break
            if elapsed % 6 == 0:
                logs.append(f'[Sync VPS] En attente du worker... ({elapsed}s)')
        else:
            logs.append(f'[Sync VPS] ⚠ Timeout {timeout}s — certaines tâches sont encore en cours')
            for tid in task_ids:
                cursor3 = db.cursor(pymysql.cursors.DictCursor)
                cursor3.execute(
                    "SELECT id, type_action, statut FROM taches_planifiees WHERE id = %s", (tid,)
                )
                r = cursor3.fetchone()
                if r:
                    logs.append(f'  → {r["type_action"]} #{r["id"]} : {r["statut"]}')
                cursor3.close()

    except Exception as e:
        logs.append(f'[Sync VPS] Erreur : {e}')

    return {'ok': True, 'logs': logs}


def _normaliser_pour_recherche(texte):
    """Normalise un texte pour comparaison floue :
    minuscules, sans accents, sans ponctuation, mots de liaison remplacés.
    Portée de worker_ubuntu.py::normaliser_pour_recherche."""
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


def _resoudre_song_par_id(cursor, song_id):
    """v2.5 — Résout un morceau RadioDJ par son ID, avec EXACTEMENT les
    mêmes colonnes que le moteur de matching (le frontend du Pont lit
    path/comments/bpm/duration/year/album pour le tableau de push).

    Sert à réintégrer les demandes déjà statut='matche' dans le flux
    Pont → AzuraCast SANS refaire tourner les 5 passes (leur song_id est
    déjà connu et persisté)."""
    try:
        cursor.execute(
            "SELECT s.ID, s.artist, s.title, s.year, s.duration, s.`path`, "
            "s.comments, s.album, s.bpm, s.id_genre, g.name AS genre_name "
            "FROM songs s LEFT JOIN genre g ON s.id_genre = g.id "
            "WHERE s.ID = %s AND s.song_type = 0 LIMIT 1",
            (int(song_id),)
        )
        return cursor.fetchone()
    except Exception:
        return None


def _qualifier_pousse_azura(db, source_ids, table_src):
    """v2.5 — Passe les lignes source indiquées de statut='matche' à
    statut='pousse_azura' (badge « 📡 Diffusé » côté Animateurs/Shazam).

    Deux usages :
      - prefill/pipeline : le morceau lié est déjà IN_AZURACAST (poussé
        avant la v2.5) → qualification immédiate, la ligne quitte le
        flux Pont (elle ne doit plus être re-poussée) ;
      - azuracast.py (homologue `_qualifier_sources_pousse_azura`) :
        après une copie COPIE_FICHIERS terminée.

    Non bloquant : la colonne statut est créée par la migration R1/R2 ;
    si absente ou déjà convertie, on ignore silencieusement.
    """
    if not source_ids:
        return 0
    try:
        cur = db.cursor()
        _ph = ",".join(["%s"] * len(source_ids))
        cur.execute(
            "UPDATE %s SET statut = 'pousse_azura' "
            "WHERE id IN (%s) AND statut = 'matche'" % (table_src, _ph),
            tuple(source_ids)
        )
        nb = cur.rowcount
        db.commit()
        cur.close()
        if nb:
            _logger.info(
                f"[v2.5] {_tbl_label(table_src)} : {nb} ligne(s) "
                f"'matche' → 'pousse_azura' (morceau déjà IN_AZURACAST)"
            )
        return nb
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        _logger.warning(f"[v2.5] qualification pousse_azura ignorée : {e}")
        return 0


def _tbl_label(table_src):
    return 'airvs_shazam' if 'shazam' in (table_src or '') else 'airvs_animateurs'


def _match_shazam_songs(cursor, artiste, titre):
    """Moteur de matching 5 passes pour Shazam, porté du worker.
    Mêmes passes que _match_titre_songs dans worker_ubuntu.py :
      1. Strict (LIKE %artiste% AND LIKE %titre%)
      2. Principal (sans feat.)
      2b. Nu (sans feat. ni parenthèses)
      3. Normalisé (sans accents, minuscules)
      4. Mots-clés (score >= 2)
      5. Inversion artiste/titre (5a/5b/5c)
    Retourne (resultat_dict_ou_None, methode_str)."""
    base_query = (
        "SELECT s.ID, s.artist, s.title, s.year, s.duration, s.`path`, "
        "s.comments, s.album, s.bpm, s.id_genre, g.name AS genre_name "
        "FROM songs s LEFT JOIN genre g ON s.id_genre = g.id "
        "WHERE s.song_type = 0"
    )

    resultat = None
    methode = ""

    # ── Passe 1 : stricte ──
    # ORDER BY CHAR_LENGTH préfère les titres courts/exacts
    # ("Get Off" avant "Get Off (You Fascinate Me) (instrumental)")
    query = base_query + " AND s.artist LIKE %s AND s.title LIKE %s ORDER BY CHAR_LENGTH(s.title) ASC LIMIT 1"
    cursor.execute(query, (f"%{artiste}%", f"%{titre}%"))
    resultat = cursor.fetchone()
    if resultat:
        methode = "strict"

    # ── Passe 2 : principal (sans feat.) ──
    if not resultat:
        art_p = _extraire_principal(artiste)
        tit_p = _extraire_principal(titre)
        if art_p != artiste or tit_p != titre:
            cursor.execute(query, (f"%{art_p}%", f"%{tit_p}%"))
            resultat = cursor.fetchone()
            if resultat:
                methode = f"principal (artiste='{art_p}', titre='{tit_p}')"

    # ── Passe 2b : nu (sans feat. ni parenthèses) ──
    if not resultat:
        art_nu = _extraire_nu(artiste)
        tit_nu = _extraire_nu(titre)
        if (art_nu != _extraire_principal(artiste) or tit_nu != _extraire_principal(titre)):
            if art_nu and tit_nu:
                cursor.execute(query, (f"%{art_nu}%", f"%{tit_nu}%"))
                resultat = cursor.fetchone()
                if resultat:
                    methode = f"nu (artiste='{art_nu}', titre='{tit_nu}')"

    # ── Passe 3 : normalisé ──
    if not resultat:
        art_norm = _normaliser_pour_recherche(artiste)
        tit_norm = _normaliser_pour_recherche(titre)
        art_p_norm = _normaliser_pour_recherche(_extraire_principal(artiste))
        tit_p_norm = _normaliser_pour_recherche(_extraire_principal(titre))
        art_nu_norm = _normaliser_pour_recherche(_extraire_nu(artiste))
        tit_nu_norm = _normaliser_pour_recherche(_extraire_nu(titre))
        query_norm = base_query + " AND LOWER(s.artist) LIKE %s AND LOWER(s.title) LIKE %s ORDER BY CHAR_LENGTH(s.title) ASC LIMIT 1"
        for a_s, t_s in [
            (art_nu_norm, tit_nu_norm),
            (art_p_norm, tit_p_norm),
            (art_norm, tit_norm),
        ]:
            if a_s and t_s:
                cursor.execute(query_norm, (f"%{a_s}%", f"%{t_s}%"))
                resultat = cursor.fetchone()
                if resultat:
                    methode = f"normalisé (artiste='{a_s}', titre='{t_s}')"
                    break

    # ── Passe 4 : mots-clés (score >= 2) ──
    if not resultat:
        art_mots = set(m for m in _normaliser_pour_recherche(_extraire_nu(artiste)).split() if len(m) >= 3)
        tit_mots = set(m for m in _normaliser_pour_recherche(_extraire_nu(titre)).split() if len(m) >= 3)
        if len(art_mots) + len(tit_mots) >= 2:
            art_best = max(art_mots, key=len) if art_mots else ""
            tit_best = max(tit_mots, key=len) if tit_mots else ""
            if art_best and tit_best:
                query4 = base_query + " AND LOWER(s.artist) LIKE %s AND LOWER(s.title) LIKE %s LIMIT 5"
                cursor.execute(query4, (f"%{art_best}%", f"%{tit_best}%"))
                candidats = cursor.fetchall()
                if candidats:
                    meilleur_score = 0
                    meilleur_resultat = None
                    for cand in candidats:
                        c_art_mots = set(m for m in _normaliser_pour_recherche(_extraire_nu(cand['artist'])).split() if len(m) >= 3)
                        c_tit_mots = set(m for m in _normaliser_pour_recherche(_extraire_nu(cand['title'])).split() if len(m) >= 3)
                        score = len(art_mots & c_art_mots) + len(tit_mots & c_tit_mots)
                        if score > meilleur_score and score >= 2:
                            meilleur_score = score
                            meilleur_resultat = cand
                    if meilleur_resultat:
                        resultat = meilleur_resultat
                        methode = f"mots-cles (artiste='{art_best}', titre='{tit_best}', score={meilleur_score})"

    # ── Passe 5 : inversion artiste/titre ──
    if not resultat:
        art_inv, tit_inv = titre, artiste
        query_inv = base_query + " AND s.artist LIKE %s AND s.title LIKE %s ORDER BY CHAR_LENGTH(s.title) ASC LIMIT 1"
        cursor.execute(query_inv, (f"%{art_inv}%", f"%{tit_inv}%"))
        resultat = cursor.fetchone()
        if resultat:
            methode = f"inversé-strict"
        else:
            art_inv_p = _extraire_principal(art_inv)
            tit_inv_p = _extraire_principal(tit_inv)
            if art_inv_p != art_inv or tit_inv_p != tit_inv:
                cursor.execute(query_inv, (f"%{art_inv_p}%", f"%{tit_inv_p}%"))
                resultat = cursor.fetchone()
                if resultat:
                    methode = f"inversé-principal"
        if not resultat:
            art_inv_nu_norm = _normaliser_pour_recherche(_extraire_nu(art_inv))
            tit_inv_nu_norm = _normaliser_pour_recherche(_extraire_nu(tit_inv))
            art_inv_norm = _normaliser_pour_recherche(art_inv)
            tit_inv_norm = _normaliser_pour_recherche(tit_inv)
            query_inv_norm = base_query + " AND LOWER(s.artist) LIKE %s AND LOWER(s.title) LIKE %s ORDER BY CHAR_LENGTH(s.title) ASC LIMIT 1"
            for a_s, t_s in [(art_inv_nu_norm, tit_inv_nu_norm), (art_inv_norm, tit_inv_norm)]:
                if a_s and t_s:
                    cursor.execute(query_inv_norm, (f"%{a_s}%", f"%{t_s}%"))
                    resultat = cursor.fetchone()
                    if resultat:
                        methode = f"inversé-normalisé (artiste='{a_s}', titre='{t_s}')"
                        break

    return resultat, methode


# ─── Routes ─────────────────────────────────────────────────────────────

# ── Shazam : endpoint MacroDroid (sans auth — appelé par le téléphone) ──
@shazam_bp.route('/api/shazam/add', methods=['POST'])
def api_shazam_add():
    """
    Reçoit une reconnaissance Shazam depuis MacroDroid.
    Body JSON :
      {"artiste": "...", "titre": "..."}
      ou {"texte": "titre artiste"}  (format brut)
    Ou form-data : artiste=...&titre=...

    Cet endpoint est SANS authentification car il est appelé par MacroDroid
    sur le téléphone (pas de session navigateur).
    """
    if not _assurer_schema_shazam():
        return jsonify({"status": "error", "message": "DB indisponible"}), 500

    artiste = ""
    titre = ""

    # Accepter JSON ou form-data
    if request.is_json:
        data = request.get_json(silent=True) or {}
        artiste = (data.get('artiste') or '').strip()
        titre = (data.get('titre') or '').strip()
        # Mode brut : texte unique
        texte_brut = (data.get('texte') or '').strip()
        if texte_brut and not artiste and not titre:
            parts = texte_brut.rsplit(' ', 1)
            if len(parts) == 2:
                titre = parts[0].strip()
                artiste = parts[1].strip()
            else:
                titre = texte_brut
    else:
        artiste = (request.form.get('artiste') or '').strip()
        titre = (request.form.get('titre') or '').strip()
        texte_brut = (request.form.get('texte') or '').strip()
        if texte_brut and not artiste and not titre:
            parts = texte_brut.rsplit(' ', 1)
            if len(parts) == 2:
                titre = parts[0].strip()
                artiste = parts[1].strip()
            else:
                titre = texte_brut

    # Nettoyer le champ artiste : retirer les suffixes Shazam
    # ("Travis Scott Shazam Dashboard" → "Travis Scott")
    suffixes_a_retirer = ["Shazam Dashboard", "Shazam"]
    for suffixe in suffixes_a_retirer:
        if artiste.endswith(suffixe):
            artiste = artiste[:-len(suffixe)].strip()

    if not titre:
        return jsonify({"status": "error", "message": "titre requis"}), 400

    # Animateur depuis query param (MacroDroid) ou JSON body
    animateur = (data.get('animateur') if request.is_json and data else None) or (request.args.get('animateur') or '').strip() or None

    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)

        # ── Déduplication avec cooldown 10s ──
        # Si le même artiste+titre (insensible à la casse) a été reconnu
        # dans les 10 dernières secondes, on met à jour la date au lieu d'insérer.
        # (évite les doublons quand Shazam envoie 2 notifications simultanées)
        cursor.execute(
            "SELECT id, date_import FROM airvs_shazam "
            "WHERE LOWER(artiste) = LOWER(%s) AND LOWER(titre) = LOWER(%s) "
            "AND date_import > NOW() - INTERVAL 10 SECOND "
            "LIMIT 1",
            (artiste, titre)
        )
        existant = cursor.fetchone()

        if existant:
            # Mise à jour de la date de reconnaissance (garde l'entrée fraîche)
            cursor.execute(
                "UPDATE airvs_shazam SET date_reconnaissance = NOW() WHERE id = %s",
                (existant['id'],)
            )
            db.commit()
            row_id = existant['id']
            cursor.close()
            db.close()
            _logger.info(f"~ {artiste} - {titre} (cooldown, id={row_id})")
            return jsonify({
                "status": "ok", "id": row_id, "artiste": artiste, "titre": titre,
                "dedup": True
            })

        # Nouvelle entrée
        # Toutes les requêtes MacroDroid arrivant sur le dashboard local (192.168.1.39)
        # proviennent du studio → source = shazam_studio
        cursor.execute(
            "INSERT INTO airvs_shazam (artiste, titre, date_reconnaissance, source, animateur) VALUES (%s, %s, NOW(), %s, %s)",
            (artiste, titre, 'shazam_studio', animateur)
        )
        db.commit()
        row_id = cursor.lastrowid
        cursor.close()
        db.close()
        _logger.info(f"+ {artiste} - {titre} (id={row_id}, source=shazam_studio, animateur={animateur or 'N/A'})")
        return jsonify({"status": "ok", "id": row_id, "artiste": artiste, "titre": titre})
    except Exception as e:
        _logger.error(f"Erreur insert : {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Shazam : lire le log (pour debug depuis Ubuntu) ──

@shazam_bp.route('/api/shazam/log')
def api_shazam_log():
    """Retourne les N dernières lignes du log Shazam (auth requis)."""
    import os as _os
    lines = int(request.args.get('n', 30))
    log_path = Path(__file__).parent / 'logs' / 'airvs.log'
    if not log_path.exists():
        return jsonify({"lines": [], "error": "Fichier log introuvable"})
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
        tail = [l.rstrip() for l in all_lines[-lines:]]
        return jsonify({"lines": tail, "total": len(all_lines)})
    except Exception as e:
        return jsonify({"lines": [], "error": str(e)}), 500


# ── Shazam : lister les entrées (avec auth) ──
@shazam_bp.route('/api/shazam/list')
@login_requis
def api_shazam_list():
    """Renvoie les N dernières reconnaissances Shazam (avec statut si disponible).

    Deux modes :
      • Legacy : ``?limit=N`` → renvoie un array JSON (rétro-compatibilité)
      • Pagination : ``?page=N&page_size=M`` → renvoie
        {rows: [...], page, page_size, total_count, total_pages}
    """
    limite_arg = request.args.get('limit')
    page_arg = request.args.get('page')
    page_size_arg = request.args.get('page_size', '20')

    # Mode pagination (préféré si page est fourni)
    use_pagination = bool(page_arg)
    page = max(int(page_arg or '1'), 1)
    page_size = min(max(int(page_size_arg), 1), 100)
    # Legacy : limite codée (max 500)
    limite = min(max(int(limite_arg or '50'), 1), 500)

    try:
        db = get_db_connection()
        cursor = db.cursor(pymysql.cursors.DictCursor)
        # R1 : Inclure statut, match_song_id, match_passe si les colonnes existent
        try:
            cursor.execute("SHOW COLUMNS FROM airvs_shazam LIKE 'statut'")
            _has_statut = bool(cursor.fetchone())
        except Exception:
            _has_statut = False

        select_cols = ("id, artiste, titre, date_reconnaissance, source, animateur "
                       + (", statut, match_song_id, match_passe" if _has_statut else ""))

        if use_pagination:
            # Compter le total
            cursor.execute("SELECT COUNT(*) AS c FROM airvs_shazam")
            total_count = cursor.fetchone()['c']
            total_pages = max((total_count + page_size - 1) // page_size, 1)
            offset = (page - 1) * page_size
            cursor.execute(
                f"SELECT {select_cols} FROM airvs_shazam "
                f"ORDER BY date_reconnaissance DESC LIMIT %s OFFSET %s",
                (page_size, offset)
            )
            rows = cursor.fetchall()
            cursor.close()
            db.close()
            for r in rows:
                if isinstance(r.get('date_reconnaissance'), datetime):
                    r['date_reconnaissance'] = r['date_reconnaissance'].strftime('%d/%m/%Y %H:%M')
            return jsonify({
                "rows": rows,
                "page": page,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": total_pages
            })
        else:
            cursor.execute(
                f"SELECT {select_cols} FROM airvs_shazam ORDER BY date_reconnaissance DESC LIMIT %s",
                (limite,)
            )
            rows = cursor.fetchall()
            cursor.close()
            db.close()
            for r in rows:
                if isinstance(r.get('date_reconnaissance'), datetime):
                    r['date_reconnaissance'] = r['date_reconnaissance'].strftime('%d/%m/%Y %H:%M')
            return jsonify(rows)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Shazam : liste des animateurs distincts (pour dropdown) ──
@shazam_bp.route('/api/shazam/animateurs')
@login_requis
def api_shazam_animateurs():
    """Retourne la liste des animateurs distincts."""
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"animateurs": [], "error": "Connexion DB impossible"})
        cursor = db.cursor()
        cursor.execute(
            "SELECT DISTINCT animateur FROM airvs_shazam "
            "WHERE animateur IS NOT NULL AND animateur != '' ORDER BY animateur"
        )
        animateurs = [r["animateur"] for r in cursor.fetchall()]
        cursor.close()
        db.close()
        return jsonify({"animateurs": animateurs})
    except Exception as e:
        return jsonify({"animateurs": [], "error": str(e), "type": type(e).__name__})


# ── Shazam : mettre à jour l'animateur d'une entrée (avec auth) ──
@shazam_bp.route('/api/shazam/<int:shazam_id>/animateur', methods=['PUT'])
@login_requis
def api_shazam_update_animateur(shazam_id):
    """Met à jour le champ animateur d'une entrée Shazam.
    Paramètre GET : ?animateur=Nom
    Utilisé par le dropdown de badge animateur dans le dashboard."""
    animateur = (request.args.get('animateur') or '').strip()
    if not animateur:
        return jsonify({"error": "animateur requis"}), 400
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "UPDATE airvs_shazam SET animateur = %s WHERE id = %s",
            (animateur, shazam_id)
        )
        db.commit()
        affected = cursor.rowcount
        cursor.close()
        db.close()
        if affected == 0:
            return jsonify({"error": "entrée introuvable"}), 404
        return jsonify({"ok": True, "animateur": animateur})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Shazam : supprimer une entrée (avec auth) ──
@shazam_bp.route('/api/shazam/delete/<int:shazam_id>', methods=['DELETE'])
@login_requis
def api_shazam_delete(shazam_id):
    """Supprime une reconnaissance Shazam."""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute("DELETE FROM airvs_shazam WHERE id = %s", (shazam_id,))
        db.commit()
        affected = cursor.rowcount
        cursor.close()
        db.close()
        if affected:
            return jsonify({"status": "ok"})
        return jsonify({"status": "error", "message": "Non trouvé"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ══════════════════════════════════════════
# ANIMATEURS : endpoints dédiés (table airvs_animateurs)
# ══════════════════════════════════════════

@shazam_bp.route('/api/animateurs/list')
@login_requis
def api_animateurs_list():
    """Renvoie les N dernières soumissions animateurs (table airvs_animateurs).
    Cette table est alimentée par le worker via sync VPS soumissions.

    Deux modes :
      • Legacy : ``?limit=N`` → renvoie un array JSON (rétro-compatibilité)
      • Pagination : ``?page=N&page_size=M`` → renvoie
        {rows: [...], page, page_size, total_count, total_pages}
    """
    limite_arg = request.args.get('limit')
    page_arg = request.args.get('page')
    page_size_arg = request.args.get('page_size', '20')
    animateur_filter = (request.args.get('animateur') or '').strip()

    use_pagination = bool(page_arg)
    page = max(int(page_arg or '1'), 1)
    page_size = min(max(int(page_size_arg), 1), 100)
    limite = min(max(int(limite_arg or '150'), 1), 500)

    try:
        db = get_db_connection()
        if not db:
            return jsonify({"status": "error", "message": "DB indisponible"}), 500
        cursor = db.cursor(pymysql.cursors.DictCursor)

        # Vérifier que la table existe (créée par le worker)
        cursor.execute(
            "SELECT COUNT(*) AS c FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'airvs_animateurs'"
        )
        if not cursor.fetchone().get('c', 0):
            cursor.close()
            db.close()
            return jsonify([] if not use_pagination else
                           {"rows": [], "page": page, "page_size": page_size,
                            "total_count": 0, "total_pages": 0})

        # Construire le SELECT dynamiquement en fonction des colonnes existantes
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'airvs_animateurs'"
        )
        _existing_cols = {r['column_name'] for r in cursor.fetchall()}

        _required_cols = ['id', 'vps_id', 'artiste', 'titre', 'genre', 'source',
                          'commentaire', 'video_url', 'animateur', 'statut_vps',
                          'date_soumission', 'date_sync']
        _optional_cols = ['match_song_id', 'match_passe', 'statut']
        _select_cols = [c for c in _required_cols if c in _existing_cols]
        _select_cols += [c for c in _optional_cols if c in _existing_cols]
        if not _select_cols:
            cursor.close()
            db.close()
            return jsonify([] if not use_pagination else
                           {"rows": [], "page": page, "page_size": page_size,
                            "total_count": 0, "total_pages": 0})

        select_str = ", ".join(_select_cols)
        where_clause = ""
        params = ()
        if animateur_filter:
            where_clause = " WHERE animateur = %s"
            params = (animateur_filter,)

        if use_pagination:
            # Compter le total (avec filtre éventuel)
            cursor.execute(f"SELECT COUNT(*) AS c FROM airvs_animateurs{where_clause}", params)
            total_count = cursor.fetchone()['c']
            total_pages = max((total_count + page_size - 1) // page_size, 1)
            offset = (page - 1) * page_size
            cursor.execute(
                f"SELECT {select_str} FROM airvs_animateurs{where_clause} "
                f"ORDER BY date_soumission DESC LIMIT %s OFFSET %s",
                params + (page_size, offset)
            )
            rows = cursor.fetchall()
            cursor.close()
            db.close()
            for r in rows:
                for k in ('date_soumission', 'date_sync'):
                    if isinstance(r.get(k), datetime):
                        r[k] = r[k].strftime('%d/%m/%Y %H:%M')
            return jsonify({
                "rows": rows,
                "page": page,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": total_pages
            })
        else:
            cursor.execute(
                f"SELECT {select_str} FROM airvs_animateurs{where_clause} "
                f"ORDER BY date_soumission DESC LIMIT %s",
                params + (limite,)
            )
            rows = cursor.fetchall()
            cursor.close()
            db.close()
            for r in rows:
                for k in ('date_soumission', 'date_sync'):
                    if isinstance(r.get(k), datetime):
                        r[k] = r[k].strftime('%d/%m/%Y %H:%M')
            return jsonify(rows)

    except Exception as e:
        _logger.error(f"Erreur animateurs list : {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@shazam_bp.route('/api/animateurs/animateurs')
@login_requis
def api_animateurs_distinct():
    """Retourne la liste des animateurs distincts depuis airvs_animateurs."""
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"animateurs": [], "error": "DB indisponible"})
        cursor = db.cursor(pymysql.cursors.DictCursor)

        cursor.execute(
            "SELECT COUNT(*) AS c FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'airvs_animateurs'"
        )
        if not cursor.fetchone().get('c', 0):
            cursor.close()
            db.close()
            return jsonify({"animateurs": []})

        cursor.execute(
            "SELECT DISTINCT animateur FROM airvs_animateurs "
            "WHERE animateur IS NOT NULL AND animateur != '' ORDER BY animateur"
        )
        animateurs = [r["animateur"] for r in cursor.fetchall()]
        cursor.close()
        db.close()
        return jsonify({"animateurs": animateurs})
    except Exception as e:
        _logger.error(f"Erreur animateurs distincts : {e}")
        return jsonify({"animateurs": [], "error": str(e)}), 500


# ── Stats agrégées par animateur (pour vue synthétique au-dessus de la liste) ──
@shazam_bp.route('/api/animateurs/stats')
@login_requis
def api_animateurs_stats():
    """Retourne les compteurs agrégés par animateur depuis airvs_animateurs.

    Response JSON :
    {
        "total": 150,                          # nombre total d'entrées
        "par_animateur": [                    # trié par count décroissant
            {"animateur": "Vince", "count": 42, "derniere_date": "12/09/2026 14:30"},
            {"animateur": "Mary",  "count": 28, "derniere_date": "10/09/2026 09:15"},
            ...
        ]
    }
    """
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"error": "DB indisponible"}), 500
        cursor = db.cursor(pymysql.cursors.DictCursor)

        # Vérifier que la table existe
        cursor.execute(
            "SELECT COUNT(*) AS c FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'airvs_animateurs'"
        )
        if not cursor.fetchone().get('c', 0):
            cursor.close()
            db.close()
            return jsonify({"total": 0, "par_animateur": []})

        # Total
        cursor.execute("SELECT COUNT(*) AS c FROM airvs_animateurs")
        total = cursor.fetchone()['c']

        # Par animateur (exclut les entrées sans animateur)
        cursor.execute(
            "SELECT animateur, COUNT(*) AS count, MAX(date_soumission) AS derniere_date "
            "FROM airvs_animateurs "
            "WHERE animateur IS NOT NULL AND animateur != '' "
            "GROUP BY animateur ORDER BY count DESC"
        )
        rows = cursor.fetchall()
        cursor.close()
        db.close()

        for r in rows:
            if isinstance(r.get('derniere_date'), datetime):
                r['derniere_date'] = r['derniere_date'].strftime('%d/%m/%Y %H:%M')

        return jsonify({
            "total": total,
            "par_animateur": rows
        })
    except Exception as e:
        _logger.error(f"Erreur animateurs stats : {e}")
        return jsonify({"error": str(e)}), 500


# ── Stats agrégées Shazam (par animateur) ──
@shazam_bp.route('/api/shazam/stats')
@login_requis
def api_shazam_stats():
    """Retourne les compteurs agrégés par animateur depuis airvs_shazam.

    Response JSON : voir api_animateurs_stats (même structure).
    """
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"error": "DB indisponible"}), 500
        cursor = db.cursor(pymysql.cursors.DictCursor)

        # Total
        cursor.execute("SELECT COUNT(*) AS c FROM airvs_shazam")
        total = cursor.fetchone()['c']

        # Par animateur
        cursor.execute(
            "SELECT animateur, COUNT(*) AS count, MAX(date_reconnaissance) AS derniere_date "
            "FROM airvs_shazam "
            "WHERE animateur IS NOT NULL AND animateur != '' "
            "GROUP BY animateur ORDER BY count DESC"
        )
        rows = cursor.fetchall()
        cursor.close()
        db.close()

        for r in rows:
            if isinstance(r.get('derniere_date'), datetime):
                r['derniere_date'] = r['derniere_date'].strftime('%d/%m/%Y %H:%M')

        return jsonify({
            "total": total,
            "par_animateur": rows
        })
    except Exception as e:
        _logger.error(f"Erreur shazam stats : {e}")
        return jsonify({"error": str(e)}), 500


@shazam_bp.route('/api/animateurs/<int:anim_id>/animateur', methods=['PUT'])
@login_requis
def api_animateur_update_animateur(anim_id):
    """Met à jour le champ animateur d'une entrée airvs_animateurs."""
    animateur = (request.args.get('animateur') or '').strip()
    if not animateur:
        return jsonify({"error": "animateur requis"}), 400
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "UPDATE airvs_animateurs SET animateur = %s WHERE id = %s",
            (animateur, anim_id)
        )
        db.commit()
        affected = cursor.rowcount
        cursor.close()
        db.close()
        if affected == 0:
            return jsonify({"error": "entrée introuvable"}), 404
        return jsonify({"ok": True, "animateur": animateur})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Shazam : import CSV/JSONL (avec auth) ──
@shazam_bp.route('/api/shazam/import_csv', methods=['POST'])
@login_requis
def api_shazam_import_csv():
    """Importe un fichier Shazam (SyncedSongs.csv ou AnalyticsSongs.jsonl) dans airvs_shazam.
    Formats supportés :
      - SyncedSongs.csv : colonnes artist,title,status,date,longitude,latitude
        (avec ou sans en-tête)
      - AnalyticsSongs.jsonl : 1 objet JSON par ligne avec artistname,tracktitle,date

    Pour chaque ligne :
      1. Dédup contre airvs_shazam (insensible à la casse) → skip si doublon
      2. Matching 5 passes contre radiodb.songs (fichier MP3 local ?)
      3. Insert dans airvs_shazam

    La source est positionnée à 'csv_import'.
    Retourne found (disponibles en local) + not_found (à acquérir) pour
    alimentation directe du tableau de prévisualisation push."""
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "Aucun fichier fourni"}), 400

    fichier = request.files['file']
    if not fichier.filename:
        return jsonify({"status": "error", "message": "Fichier vide"}), 400

    nom_fichier = fichier.filename
    ext = nom_fichier.rsplit('.', 1)[-1].lower() if '.' in nom_fichier else ''

    if ext not in ('csv', 'jsonl'):
        return jsonify({"status": "error", "message": f"Format non supporté : .{ext} (csv ou jsonl requis)"}), 400

    if not _assurer_schema_shazam():
        return jsonify({"status": "error", "message": "DB indisponible"}), 500

    # ── Parser le fichier ──
    titres = []  # liste de dicts {artiste, titre, date_iso}

    try:
        if ext == 'csv':
            contenu = fichier.read().decode('utf-8-sig')
            reader = csv_mod.reader(io.StringIO(contenu), delimiter=',', quotechar='"')
            lignes_lues = 0
            for row in reader:
                lignes_lues += 1
                if len(row) < 4:
                    continue

                # Détecter si la 1re ligne est un en-tête
                if lignes_lues == 1 and row[0].strip().lower() in ('artist', 'artiste'):
                    continue

                artiste = row[0].strip()
                titre = row[1].strip()
                date_iso = row[3].strip() if len(row) > 3 else ''

                # Nettoyer les N/A
                date_iso = '' if date_iso.upper() in ('N/A', 'NA', '-') else date_iso

                if not artiste or not titre:
                    continue

                titres.append({"artiste": artiste, "titre": titre, "date_iso": date_iso})

        elif ext == 'jsonl':
            contenu = fichier.read().decode('utf-8-sig')
            for line in contenu.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                artiste = str(obj.get('artistname', '')).strip()
                titre = str(obj.get('tracktitle', '')).strip()
                date_iso = str(obj.get('date', '')).strip()
                if not artiste or not titre:
                    continue
                titres.append({"artiste": artiste, "titre": titre, "date_iso": date_iso})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erreur lecture fichier : {e}"}), 400

    if not titres:
        return jsonify({"status": "ok", "message": "Aucun titre exploitable", "total": 0,
                           "imported": 0, "skipped": 0, "found": [], "not_found": []})

    # ── Insertion en base avec dédup + matching contre songs ──
    db = get_db_connection()
    if not db:
        return jsonify({"status": "error", "message": "DB indisponible"}), 500

    cursor = db.cursor(pymysql.cursors.DictCursor)
    imported = 0
    skipped_dup = 0
    skipped_matched = 0
    erreurs = 0
    found = []
    not_found = []

    for t in titres:
        artiste = t['artiste']
        titre = t['titre']
        date_iso = t['date_iso']

        # 1) Dédup : artiste+titre déjà dans airvs_shazam ?
        cursor.execute(
            "SELECT id FROM airvs_shazam "
            "WHERE LOWER(artiste) = LOWER(%s) AND LOWER(titre) = LOWER(%s) LIMIT 1",
            (artiste, titre)
        )
        if cursor.fetchone():
            skipped_dup += 1
            continue

        # 2) Matching 5 passes contre radiodb.songs
        cursor2 = db.cursor(pymysql.cursors.DictCursor)
        match, methode = _match_shazam_songs(cursor2, artiste, titre)
        cursor2.close()

        # 3) Parser la date ISO
        date_reco = None
        if date_iso and len(date_iso) >= 10 and date_iso[4] == '-':
            try:
                date_reco = datetime.strptime(date_iso[:19].replace('Z', '+00:00').split('+')[0], '%Y-%m-%dT%H:%M:%S')
            except ValueError:
                try:
                    date_reco = datetime.strptime(date_iso[:10], '%Y-%m-%d')
                except ValueError:
                    pass

        # 4) INSERT dans airvs_shazam
        try:
            if date_reco:
                cursor.execute(
                    "INSERT INTO airvs_shazam (artiste, titre, date_reconnaissance, source) VALUES (%s, %s, %s, %s)",
                    (artiste, titre, date_reco, 'csv_import')
                )
            else:
                cursor.execute(
                    "INSERT INTO airvs_shazam (artiste, titre, source) VALUES (%s, %s, %s)",
                    (artiste, titre, 'csv_import')
                )
            shazam_id = cursor.lastrowid
            imported += 1
        except Exception:
            erreurs += 1
            continue

        # 5) Alimenter found / not_found
        if match:
            match['in_shazam'] = True
            match['_shazam_id'] = shazam_id
            match['_shazam_date'] = date_reco
            match['_match_methode'] = methode
            found.append(match)
        else:
            not_found.append({
                'artist': artiste,
                'title': titre,
                'display': artiste + ' \u2014 ' + titre
            })

    db.commit()
    cursor.close()
    db.close()

    _logger.info(f"Import CSV : {imported} inséré(s), {skipped_dup} doublon(s), {erreurs} erreur(s), "
          f"{len(found)} en base RadioDJ, {len(not_found)} absent(s) ({nom_fichier})")
    return jsonify({
        "status": "ok",
        "message": f"{imported} importé(s) \u00b7 {len(found)} disponible(s) en local \u00b7 {len(not_found)} absent(s) \u00b7 {skipped_dup} doublon(s)",
        "total": len(titres),
        "imported": imported,
        "skipped": skipped_dup,
        "errors": erreurs,
        "filename": nom_fichier,
        "found": found,
        "not_found": not_found
    })


# ── Shazam : pré-remplissage pour le Pont (matching contre songs) ──
@shazam_bp.route('/api/shazam/prefill')
@login_requis
def api_shazam_prefill():
    """Retourne les titres Shazam matchés contre la base RadioDJ.
    Sépare les trouvés (avec infos complètes) des non-trouvés.
    Utilisé par le sous-onglet Shazam du Pont pour alimenter le tableau
    de prévisualisation push (populatePushPreviewFromShazam).

    Paramètre GET optionnel :
      ?mode=web       → ne retourner que les entrées formulaire/animateur/programmeur
      ?mode=shazam    → ne retourner que les entrées Shazam (studio + extérieur)
      ?mode=shazam_studio → studio uniquement
      ?mode=shazam_ext     → extérieur uniquement
      ?sync_vps=1     → Option C : déclenche un sync immédiat VPS avant le matching
                         (insère les tâches SYNC_VPS_SOUMISSIONS + SYNC_VPS_SHAZAM_EXT
                          dans taches_planifiees et attend leur complétion, max 30 s)
      (absent ou autre = tout retourner)"""
    try:
        db = get_db_connection()
        if not db:
            return jsonify({"error": "DB indisponible"}), 500

        # ── Option C : sync VPS immédiat avant le matching ──
        sync_logs = []
        if (request.args.get('sync_vps') or '').strip() == '1':
            sync_ok = _sync_vps_immediat(db)
            sync_logs = sync_ok.get('logs', [])

        mode = (request.args.get('mode') or '').strip().lower()
        animateur_filter = (request.args.get('animateur') or '').strip()

        # Construction dynamique de la requête selon le mode
        # Phase 2 : mode=web lit airvs_animateurs (soumissions VPS),
        # les autres modes continuent de lire airvs_shazam (reconnaissances MacroDroid).
        _src_table = 'airvs_animateurs' if mode == 'web' else 'airvs_shazam'
        # v2.5 — présence de la colonne statut vérifiée UNE fois ici :
        # sert à la fois au filtre R5 ci-dessous ET à la lecture des
        # colonnes statut/match_song_id/match_passe dans le SELECT
        # (partition nouveau / déjà-matché).
        _has_statut = True
        try:
            chk_s0 = db.cursor()
            chk_s0.execute("SHOW COLUMNS FROM %s LIKE 'statut'" % _src_table)
            _has_statut = bool(chk_s0.fetchone())
            chk_s0.close()
        except Exception:
            _has_statut = False
        _cols_statut = ", statut, match_song_id, match_passe" if _has_statut else ""
        if mode == 'web':
            base_query = ("SELECT id, artiste, titre, "
                          "date_soumission AS date_reconnaissance, "
                          "source, animateur" + _cols_statut +
                          " FROM airvs_animateurs")
            params = ()
        else:
            base_query = ("SELECT id, artiste, titre, date_reconnaissance, "
                          "source, animateur" + _cols_statut +
                          " FROM airvs_shazam")
            params = ()
            if mode == 'shazam':
                base_query += " WHERE source NOT IN ('Shazam', 'Recommandation', 'Saisie manuelle', 'Autre', 'formulaire_programmeur') AND (source NOT LIKE '%%externe%%' OR source IS NULL)"
            elif mode == 'shazam_studio':
                base_query += " WHERE source NOT IN ('Shazam', 'Recommandation', 'Saisie manuelle', 'Autre', 'formulaire_programmeur') AND (source NOT LIKE '%%externe%%' OR source IS NULL)"
            elif mode == 'shazam_ext':
                base_query += " WHERE source LIKE '%%externe%%'"

        # ── R5 : Filtre statut ──
        # v2.5 — défaut 'nouveau,matche' (était 'nouveau') : le Pont doit
        # AUSSI voir les lignes déjà « Matché » — sinon elles ne remontent
        # plus dans la prévisualisation de push et ne sont jamais poussées
        # vers AzuraCast (régression apparue avec la v2.3 : une fois
        # l'écriture des statuts réparée, les matchés sortaient du filtre).
        # Le paramètre ?statut= reste prioritaire (ex: statut=nouveau).
        statut_filter = (request.args.get('statut') or 'nouveau,matche').strip()
        if statut_filter != 'tous':
            _statuts_valides = [s.strip() for s in statut_filter.split(',') if s.strip()]
            if _statuts_valides and _has_statut:
                _statut_clause = ",".join(["%s"] * len(_statuts_valides))
                if 'WHERE' in base_query:
                    base_query += f" AND statut IN ({_statut_clause})"
                else:
                    base_query += f" WHERE statut IN ({_statut_clause})"
                params = params + tuple(_statuts_valides)

        # Filtre optionnel par animateur
        if animateur_filter:
            if 'WHERE' in base_query:
                base_query += " AND animateur = %s"
            else:
                base_query += " WHERE animateur = %s"
            params = params + (animateur_filter,)

        base_query += " ORDER BY date_reconnaissance DESC"

        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute(base_query, params)
        shazam_rows = cursor.fetchall()

        if not shazam_rows:
            cursor.close()
            db.close()
            return jsonify({"found": [], "not_found": [], "total_shazam": 0})

        # 2) Matcher contre songs (algorithme 5 passes)
        found = []
        not_found = []
        matched_ids = []  # R3 : IDs à mettre à jour après matching
        not_found_ids = []  # P1 : IDs des non-trouvés (marquage match_passe='non_trouve')

        # ── v2.5 — partition nouveau / déjà-matché ──────────────────
        # Les lignes déjà 'matche' avec un match_song_id ne repassent PAS
        # dans le moteur : leur morceau est résolu directement par ID
        # (rapide, zéro risque de changer un match existant). Celles dont
        # le morceau est déjà IN_AZURACAST sont qualifiées 'pousse_azura'
        # (cycle éditorial terminé) et écartées du tableau de push.
        rows_engine = []
        rows_deja = []
        for sr in shazam_rows:
            if _has_statut and (sr.get('statut') == 'matche') and sr.get('match_song_id'):
                rows_deja.append(sr)
            else:
                rows_engine.append(sr)

        _nb_deja_reintegres = 0
        _ids_pousse_azura_prefill = []
        if rows_deja:
            cursor_d = db.cursor(pymysql.cursors.DictCursor)
            for sr in rows_deja:
                _art = str(sr.get('artiste') or '').strip()
                _tit = str(sr.get('titre') or '').strip()
                # R9 : couple rejeté en manquants → hors flux (même règle
                # que le moteur ci-dessous)
                try:
                    chk_rd = db.cursor()
                    chk_rd.execute(
                        "SELECT id FROM airvs_manquants "
                        "WHERE LOWER(artiste) = LOWER(%s) AND LOWER(titre) = LOWER(%s) "
                        "AND statut IN ('rejete','rejete_corrigé') LIMIT 1",
                        (_art, _tit)
                    )
                    _rejected = bool(chk_rd.fetchone())
                    chk_rd.close()
                    if _rejected:
                        continue
                except Exception:
                    pass
                _song = _resoudre_song_par_id(cursor_d, sr.get('match_song_id'))
                if not _song:
                    # morceau disparu de RadioDJ : retente le moteur
                    # (une nouvelle version a pu être importée entre-temps)
                    rows_engine.append(sr)
                    continue
                if str(_song.get('comments') or '') == 'IN_AZURACAST':
                    # déjà poussé vers AzuraCast par le passé : on qualifie
                    # la ligne source 'pousse_azura' et on la retire du flux
                    # (elle ne doit plus remonter dans le push)
                    _ids_pousse_azura_prefill.append(sr['id'])
                    continue
                _song['in_shazam'] = True
                _song['_shazam_id'] = sr['id']
                _song['_shazam_date'] = sr.get('date_reconnaissance')
                _song['_shazam_source'] = sr.get('source', 'macrodroid')
                _song['_shazam_animateur'] = sr.get('animateur')
                _song['_match_methode'] = str(sr.get('match_passe') or 'deja_matche')
                _song['_original_artiste'] = sr['artiste']
                _song['_original_titre'] = sr['titre']
                _song['_deja_matche'] = True
                found.append(_song)
                _nb_deja_reintegres += 1
            cursor_d.close()
            if _ids_pousse_azura_prefill and _has_statut:
                _qualifier_pousse_azura(db, _ids_pousse_azura_prefill, _src_table)

        for idx, sr in enumerate(rows_engine):
            # ── Vérification d'annulation toutes les ~10 itérations ──
            if idx > 0 and idx % 10 == 0:
                try:
                    chk = db.cursor(pymysql.cursors.DictCursor)
                    chk.execute(
                        "SELECT id FROM taches_planifiees "
                        "WHERE type_action = 'SHAZAM_PREFILL_CANCEL' "
                        "AND statut = 'en_attente' "
                        "ORDER BY id DESC LIMIT 1"
                    )
                    cancel_row = chk.fetchone()
                    chk.close()
                    if cancel_row:
                        clr = db.cursor()
                        clr.execute(
                            "UPDATE taches_planifiees SET statut = 'annule' WHERE id = %s",
                            (cancel_row['id'],)
                        )
                        db.commit()
                        clr.close()
                        result = {
                            'found': found,
                            'not_found': not_found,
                            'total_shazam': len(shazam_rows),
                            'cancelled': True,
                            'processed': idx
                        }
                        if sync_logs:
                            result['sync_logs'] = sync_logs
                        return jsonify(result)
                except Exception:
                    pass  # Ne pas interrompre le matching pour une vérification échouée

            art = str(sr['artiste'] or '').strip()
            tit = str(sr['titre'] or '').strip()

            # ── R9 : Vérifier si ce couple est déjà rejeté dans airvs_manquants ──
            try:
                chk_r = db.cursor()
                chk_r.execute(
                    "SELECT id FROM airvs_manquants "
                    "WHERE LOWER(artiste) = LOWER(%s) AND LOWER(titre) = LOWER(%s) "
                    "AND statut IN ('rejete','rejete_corrigé') LIMIT 1",
                    (art, tit)
                )
                if chk_r.fetchone():
                    chk_r.close()
                    continue  # Déjà rejeté, skip
                chk_r.close()
            except Exception:
                pass  # Ne pas bloquer si la requête échoue

            cursor2 = db.cursor(pymysql.cursors.DictCursor)
            match, methode = _match_shazam_songs(cursor2, art, tit)
            cursor2.close()

            if match:
                match['in_shazam'] = True
                match['_shazam_id'] = sr['id']
                match['_shazam_date'] = sr['date_reconnaissance']
                match['_shazam_source'] = sr.get('source', 'macrodroid')
                match['_shazam_animateur'] = sr.get('animateur')
                match['_match_methode'] = methode
                match['_original_artiste'] = sr['artiste']
                match['_original_titre'] = sr['titre']
                found.append(match)
                # R3 : mémoriser pour persister après la boucle
                matched_ids.append((sr['id'], match['ID'], methode))
            else:
                not_found_ids.append(sr['id'])  # P1
                not_found.append({
                    'id': sr['id'],  # P1 : id ligne source — permet au JS de mettre à jour les statuts en mémoire
                    'artist': sr['artiste'],
                    'title': sr['titre'],
                    'source': sr.get('source', 'macrodroid'),
                    'animateur': sr.get('animateur'),
                    '_match_methode': '',
                    'display': (sr['artiste'] or '') + ' — ' + (sr['titre'] or '')
                })

        cursor.close()

        # ── R3 : Persister les matchs sur la table source ──
        _nb_persisted = 0
        if matched_ids:
            try:
                cur_up = db.cursor()
                for _sid, _song_id, _methode in matched_ids:
                    cur_up.execute(
                        "UPDATE %s SET statut = 'matche', match_song_id = %%s, match_passe = %%s WHERE id = %%s" % _src_table,
                        (_song_id, _methode, _sid)
                    )
                    _nb_persisted += cur_up.rowcount
                db.commit()
                cur_up.close()
            except Exception as e:
                _logger.warning(f"R3 : persister matchs ÉCHOUÉ (les badges Statut resteront 'Nouveau') : {e}")

        # ── P1 : Marquer les non-trouvés (match_passe='non_trouve') pour que
        # la colonne Match (✗) et les filtres Statut survivent au rechargement ──
        _nb_notfound = 0
        if not_found_ids:
            try:
                cur_nf = db.cursor()
                for _nid in not_found_ids:
                    cur_nf.execute(
                        "UPDATE %s SET match_passe = 'non_trouve' "
                        "WHERE id = %%s AND statut = 'nouveau'" % _src_table,
                        (_nid,)
                    )
                    _nb_notfound += cur_nf.rowcount
                db.commit()
                cur_nf.close()
            except Exception as e:
                _logger.warning(f"P1 : persister non-trouvés ignoré : {e}")

        db.close()

        # Persister les manquants (non bloquant)
        _src_type = 'animateurs' if mode == 'web' else 'shazam'
        _nb_manq = _persister_manquants_shazam(not_found, _src_type)

        # ── P2 : Corrélation manquants ↔ RadioDJ (non bloquante) ──
        # Les couples not_found courants viennent d'échouer au même moteur :
        # on les exclut pour ne pas re-tester du déjà-testé.
        _exclure = set()
        for _nf in not_found:
            try:
                _exclure.add((str(_nf.get('artist') or '').strip().lower(),
                              str(_nf.get('title') or '').strip().lower()))
            except Exception:
                pass
        _nb_corr, _ = correler_manquants_radiodj(
            exclure_cles=_exclure, limite=200, logger=_logger
        )

        result = {
            'found': found,
            'not_found': not_found,
            'total_shazam': len(shazam_rows)
        }
        # v2.5 — transparence sur la réintégration des déjà-matchés
        if _nb_deja_reintegres:
            result['deja_matches_reintegres'] = _nb_deja_reintegres
        if _ids_pousse_azura_prefill:
            result['qualifies_pousse_azura'] = len(_ids_pousse_azura_prefill)
        if _nb_corr:
            result['manquants_correles'] = _nb_corr
        if sync_logs:
            result['sync_logs'] = sync_logs
        if _nb_manq:
            result['manquants_persistes'] = _nb_manq
        if _nb_persisted:
            result['matchs_persistes'] = _nb_persisted
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Shazam : annuler un prefill/pipeline en cours ──
@shazam_bp.route('/api/shazam/prefill/cancel', methods=['POST'])
@login_requis
def api_shazam_prefill_cancel():
    """Pose un flag d'annulation pour le prefill/pipeline en cours.
    Insère une tâche SHAZAM_PREFILL_CANCEL en_attente que la boucle
    de matching vérifie toutes les ~10 itérations."""
    try:
        db = get_db_connection()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
            "VALUES ('SHAZAM_PREFILL_CANCEL', 'en_attente', %s, NOW())",
            (json.dumps({"source": "dashboard"}),)
        )
        db.commit()
        tid = cursor.lastrowid
        cursor.close()
        db.close()
        return jsonify({"ok": True, "task_id": tid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@shazam_bp.route('/api/sync/vps/now', methods=['POST'])
@login_requis
def api_sync_vps_now():
    """Endpoint pour le bouton '↻ Sync VPS' dans les onglets Shazam/Animateurs.
    Déclenche un sync immédiat des soumissions et shazam_ext depuis le VPS OVH.
    """
    try:
        db = get_db_connection()
        if not db:
            return jsonify({'error': 'DB indisponible'}), 500
        result = _sync_vps_immediat(db)
        db.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Shazam : endpoint pipeline (matching synchrone + sync Sheet via worker) ──
@shazam_bp.route('/api/shazam/pipeline/trigger', methods=['POST'])
@login_requis
def api_shazam_pipeline_trigger():
    """Pipeline Shazam :
    0. Nettoyage rétroactif des doublons
    1. Matching synchrone contre RadioDJ (DB locale, pas d'internet requis)
    2. Sync vers Google Sheet via le worker Ubuntu (qui a internet)
    Retourne found/not_found immédiatement + lance la tâche Sheet en arrière-plan."""
    logs = []
    try:
        # ── Étape 0 : nettoyage rétroactif des doublons Shazam ──
        logs.append('[0/3] Nettoyage des doublons Shazam...')
        try:
            db0 = get_db_connection()
            if db0:
                c0 = db0.cursor()
                c0.execute(
                    "DELETE s1 FROM airvs_shazam s1 "
                    "INNER JOIN airvs_shazam s2 "
                    "ON LOWER(s1.artiste) = LOWER(s2.artiste) "
                    "  AND LOWER(s1.titre) = LOWER(s2.titre) "
                    "  AND s1.id > s2.id"
                )
                deleted = c0.rowcount
                db0.commit()
                c0.close()
                db0.close()
                logs.append(f'  → {deleted} doublon(s) supprimé(s)')
        except Exception as e0:
            logs.append(f'  → {e0}')

        # ── Étape 1 : lire les entrées Shazam ──
        logs.append('[1/3] Lecture des reconnaissances Shazam...')
        db = get_db_connection()
        if not db:
            return jsonify({'status': 'error', 'message': 'DB indisponible'}), 500

        # Filtrage par mode (si fourni dans le POST body)
        pipeline_mode = (request.get_json(silent=True) or {}).get('mode', '')
        pipeline_animateur = (request.get_json(silent=True) or {}).get('animateur', '')
        # v2.5 — défaut 'nouveau,matche' (était 'nouveau') : le pipeline
        # alimente aussi le push preview du Pont via _onAnimateursMatchResult
        # → les lignes déjà « Matché » doivent remonter elles aussi.
        pipeline_statut = (request.get_json(silent=True) or {}).get('statut', 'nouveau,matche')
        # Phase 2 : mode=web lit airvs_animateurs (soumissions VPS)
        _pipe_src_table = 'airvs_animateurs' if pipeline_mode == 'web' else 'airvs_shazam'
        # v2.5 — présence de la colonne statut vérifiée UNE fois (filtre + SELECT)
        _has_statut_pipe = True
        try:
            chk_sp0 = db.cursor()
            chk_sp0.execute("SHOW COLUMNS FROM %s LIKE 'statut'" % _pipe_src_table)
            _has_statut_pipe = bool(chk_sp0.fetchone())
            chk_sp0.close()
        except Exception:
            _has_statut_pipe = False
        _cols_statut_pipe = ", statut, match_song_id, match_passe" if _has_statut_pipe else ""
        if pipeline_mode == 'web':
            base_query = ("SELECT id, artiste, titre, "
                          "date_soumission AS date_reconnaissance, "
                          "source, animateur" + _cols_statut_pipe +
                          " FROM airvs_animateurs")
            params = ()
        else:
            base_query = ("SELECT id, artiste, titre, date_reconnaissance, "
                          "source, animateur" + _cols_statut_pipe +
                          " FROM airvs_shazam")
            params = ()

        if pipeline_mode == 'shazam':
            base_query += " WHERE (source IS NULL OR source = 'macrodroid' OR source LIKE '%%shazam%%') AND (source IS NULL OR source NOT LIKE '%%formulaire%%') AND (source IS NULL OR source NOT LIKE '%%animateur%%') AND (source IS NULL OR source NOT LIKE '%%programmeur%%')"
        elif pipeline_mode == 'shazam_studio':
            base_query += " WHERE source = 'shazam_studio' OR source IS NULL"
        elif pipeline_mode == 'shazam_ext':
            base_query += " WHERE source LIKE '%%externe%%'"

        # ── R5 : Filtre statut dans pipeline ──
        if pipeline_statut and pipeline_statut != 'tous':
            _statuts_p = [s.strip() for s in pipeline_statut.split(',') if s.strip()]
            if _statuts_p and _has_statut_pipe:
                _sc = ",".join(["%s"] * len(_statuts_p))
                if 'WHERE' in base_query:
                    base_query += f" AND statut IN ({_sc})"
                else:
                    base_query += f" WHERE statut IN ({_sc})"
                params = params + tuple(_statuts_p)

        if pipeline_animateur:
            if 'WHERE' in base_query:
                base_query += " AND animateur = %s"
            else:
                base_query += " WHERE animateur = %s"
            params = params + (pipeline_animateur,)

        base_query += " ORDER BY date_reconnaissance DESC"

        cursor = db.cursor(pymysql.cursors.DictCursor)
        cursor.execute(base_query, params)
        shazam_rows = cursor.fetchall()
        logs.append(f'  → {len(shazam_rows)} entrée(s) Shazam trouvée(s)')

        if not shazam_rows:
            cursor.close()
            db.close()
            return jsonify({
                'status': 'ok',
                'logs': logs,
                'found': [], 'not_found': [], 'total_shazam': 0,
                'sheets_task_id': None
            })

        # ── Étape 2 : matching contre RadioDJ (DB locale) ──
        logs.append('[2/3] Matching contre la base RadioDJ...')
        found = []
        not_found = []
        matched_ids = []  # R3 : IDs à mettre à jour après matching
        not_found_ids = []  # P1 : IDs des non-trouvés (marquage match_passe='non_trouve')

        # ── v2.5 — partition nouveau / déjà-matché (identique au prefill) ──
        rows_engine = []
        rows_deja = []
        for sr in shazam_rows:
            if _has_statut_pipe and (sr.get('statut') == 'matche') and sr.get('match_song_id'):
                rows_deja.append(sr)
            else:
                rows_engine.append(sr)

        _nb_deja_reintegres_pipe = 0
        _ids_pousse_azura_pipe = []
        if rows_deja:
            cursor_d = db.cursor(pymysql.cursors.DictCursor)
            for sr in rows_deja:
                _art = str(sr.get('artiste') or '').strip()
                _tit = str(sr.get('titre') or '').strip()
                try:
                    chk_rd = db.cursor()
                    chk_rd.execute(
                        "SELECT id FROM airvs_manquants "
                        "WHERE LOWER(artiste) = LOWER(%s) AND LOWER(titre) = LOWER(%s) "
                        "AND statut IN ('rejete','rejete_corrigé') LIMIT 1",
                        (_art, _tit)
                    )
                    _rejected = bool(chk_rd.fetchone())
                    chk_rd.close()
                    if _rejected:
                        continue
                except Exception:
                    pass
                _song = _resoudre_song_par_id(cursor_d, sr.get('match_song_id'))
                if not _song:
                    rows_engine.append(sr)
                    continue
                if str(_song.get('comments') or '') == 'IN_AZURACAST':
                    _ids_pousse_azura_pipe.append(sr['id'])
                    continue
                _song['in_shazam'] = True
                _song['_shazam_id'] = sr['id']
                _song['_shazam_date'] = sr.get('date_reconnaissance')
                _song['_shazam_source'] = sr.get('source', 'macrodroid')
                _song['_shazam_animateur'] = sr.get('animateur')
                _song['_match_methode'] = str(sr.get('match_passe') or 'deja_matche')
                _song['_original_artiste'] = sr['artiste']
                _song['_original_titre'] = sr['titre']
                _song['_deja_matche'] = True
                found.append(_song)
                _nb_deja_reintegres_pipe += 1
            cursor_d.close()
            if _ids_pousse_azura_pipe and _has_statut_pipe:
                _nb_q = _qualifier_pousse_azura(db, _ids_pousse_azura_pipe, _pipe_src_table)
                if _nb_q:
                    logs.append(f'  → {_nb_q} ligne(s) déjà diffusée(s) qualifiée(s) « Diffusé » (pousse_azura)')
        if rows_deja:
            logs.append(f'  → {len(rows_deja)} déjà matché(s) pris en compte, '
                        f'{_nb_deja_reintegres_pipe} réintégré(s) au push, '
                        f'{len(_ids_pousse_azura_pipe)} déjà dans AzuraCast (hors push)')

        for idx, sr in enumerate(rows_engine):
            # ── Vérification d'annulation toutes les ~10 itérations ──
            if idx > 0 and idx % 10 == 0:
                try:
                    chk = db.cursor(pymysql.cursors.DictCursor)
                    chk.execute(
                        "SELECT id FROM taches_planifiees "
                        "WHERE type_action = 'SHAZAM_PREFILL_CANCEL' "
                        "AND statut = 'en_attente' "
                        "ORDER BY id DESC LIMIT 1"
                    )
                    cancel_row = chk.fetchone()
                    chk.close()
                    if cancel_row:
                        clr = db.cursor()
                        clr.execute(
                            "UPDATE taches_planifiees SET statut = 'annule' WHERE id = %s",
                            (cancel_row['id'],)
                        )
                        db.commit()
                        clr.close()
                        logs.append(f'[2/3] Annulé après {idx}/{len(shazam_rows)} entrées')
                        return jsonify({
                            'status': 'cancelled',
                            'logs': logs,
                            'found': found,
                            'not_found': not_found,
                            'total_shazam': len(shazam_rows),
                            'processed': idx,
                            'sheets_task_id': None
                        })
                except Exception:
                    pass

            art = str(sr['artiste'] or '').strip()
            tit = str(sr['titre'] or '').strip()

            # ── R9 : Vérifier si ce couple est déjà rejeté dans airvs_manquants ──
            try:
                chk_r = db.cursor()
                chk_r.execute(
                    "SELECT id FROM airvs_manquants "
                    "WHERE LOWER(artiste) = LOWER(%s) AND LOWER(titre) = LOWER(%s) "
                    "AND statut IN ('rejete','rejete_corrigé') LIMIT 1",
                    (art, tit)
                )
                if chk_r.fetchone():
                    chk_r.close()
                    continue  # Déjà rejeté, skip
                chk_r.close()
            except Exception:
                pass

            cursor2 = db.cursor(pymysql.cursors.DictCursor)
            match, methode = _match_shazam_songs(cursor2, art, tit)
            cursor2.close()

            if match:
                match['in_shazam'] = True
                match['_shazam_id'] = sr['id']
                match['_shazam_date'] = sr['date_reconnaissance']
                match['_shazam_source'] = sr.get('source', 'macrodroid')
                match['_shazam_animateur'] = sr.get('animateur')
                match['_match_methode'] = methode
                match['_original_artiste'] = sr['artiste']
                match['_original_titre'] = sr['titre']
                found.append(match)
                # R3 : mémoriser pour persister après la boucle
                matched_ids.append((sr['id'], match['ID'], methode))
            else:
                not_found_ids.append(sr['id'])  # P1
                not_found.append({
                    'id': sr['id'],  # P1 : id ligne source — permet au JS de mettre à jour les statuts en mémoire
                    'artist': sr['artiste'],
                    'title': sr['titre'],
                    'source': sr.get('source', 'macrodroid'),
                    'animateur': sr.get('animateur'),
                    '_match_methode': '',
                    'display': (sr['artiste'] or '') + ' — ' + (sr['titre'] or '')
                })

        cursor.close()

        # ── R3 : Persister les matchs sur la table source ──
        _nb_persisted_pipe = 0
        if matched_ids:
            try:
                cur_up = db.cursor()
                for _sid, _song_id, _methode in matched_ids:
                    cur_up.execute(
                        "UPDATE %s SET statut = 'matche', match_song_id = %%s, match_passe = %%s WHERE id = %%s" % _pipe_src_table,
                        (_song_id, _methode, _sid)
                    )
                    _nb_persisted_pipe += cur_up.rowcount
                db.commit()
                cur_up.close()
            except Exception as e:
                _logger.warning(f"R3 pipeline : persister matchs ÉCHOUÉ (les badges Statut resteront 'Nouveau') : {e}")

        # ── P1 pipeline : marquer les non-trouvés ──
        _nb_notfound_pipe = 0
        if not_found_ids:
            try:
                cur_nf = db.cursor()
                for _nid in not_found_ids:
                    cur_nf.execute(
                        "UPDATE %s SET match_passe = 'non_trouve' "
                        "WHERE id = %%s AND statut = 'nouveau'" % _pipe_src_table,
                        (_nid,)
                    )
                    _nb_notfound_pipe += cur_nf.rowcount
                db.commit()
                cur_nf.close()
            except Exception as e:
                _logger.warning(f"P1 pipeline : persister non-trouvés ignoré : {e}")

        if _nb_persisted_pipe:
            logs.append(f'  → {_nb_persisted_pipe} match(s) persisté(s) sur {_pipe_src_table}')

        db.close()

        logs.append(f'  → {len(found)} matché(s) · {len(not_found)} absent(s)')
        logs.append('✅ Matching terminé.')

        # Persister les manquants (non bloquant)
        _pipe_mode = (request.get_json(silent=True) or {}).get('mode', '')
        _pipe_src = 'animateurs' if _pipe_mode == 'web' else 'shazam'
        _nb_manq_pipe = _persister_manquants_shazam(not_found, _pipe_src)
        if _nb_manq_pipe:
            logs.append(f'  → {_nb_manq_pipe} manquant(s) persisté(s) dans airvs_manquants')

        # ── P2 : Corrélation manquants ↔ RadioDJ (non bloquante) ──
        _exclure_pipe = set()
        for _nf in not_found:
            try:
                _exclure_pipe.add((str(_nf.get('artist') or '').strip().lower(),
                                   str(_nf.get('title') or '').strip().lower()))
            except Exception:
                pass
        try:
            _nb_corr_pipe, _ = correler_manquants_radiodj(
                exclure_cles=_exclure_pipe, limite=200, logger=_logger
            )
            if _nb_corr_pipe:
                logs.append(f'  → {_nb_corr_pipe} manquant(s) corrélé(s) à RadioDJ (statut « importe »)')
        except Exception as _e:
            _logger.warning(f"P2 pipeline : corrélation ignorée : {_e}")

        # ── Lancer la sync Sheet via le worker (tâche asynchrone) ──
        # NOTE : On ne fait JAMAIS de fallback sur le premier Sheet.
        # Si l'utilisateur n'a pas choisi de Sheet cible, on skip la sync
        # (le matching RadioDJ a déjà été fait ci-dessus, c'est l'essentiel).
        # Sinon on risquerait de créer un onglet SHAZAM parasite dans un
        # Sheet tiers programmeur (bug v2.2.1).
        sheets_task_id = None
        try:
            data = request.json or {}
            user_sheets_id = data.get('sheets_id', '')
            config = _charger_config_json()
            sheets_cfg = config.get('sheets', {})

            # Valider que l'alias fourni existe bien dans la config
            if user_sheets_id and user_sheets_id in sheets_cfg:
                sheets_id = user_sheets_id
            else:
                sheets_id = None
                if user_sheets_id:
                    logs.append(f'⚠ Alias "{user_sheets_id}" non trouvé dans config.json, sync Sheet ignorée')
                else:
                    logs.append('ℹ Aucun Sheet sélectionné pour la sync — matching uniquement')

            if sheets_id:
                db2 = get_db_connection()
                cursor_t = db2.cursor()
                parametres = json.dumps({
                    'sheets_id': sheets_id,
                    'onglet': 'SHAZAM',
                    'found_count': len(found),
                    'not_found_count': len(not_found),
                    'total': len(shazam_rows)
                })
                cursor_t.execute(
                    "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
                    "VALUES ('SHAZAM_SYNC_SHEET', 'en_attente', %s, NOW())",
                    (parametres,)
                )
                sheets_task_id = cursor_t.lastrowid
                db2.commit()
                cursor_t.close()
                db2.close()
                logs.append(f'☁ Sync Sheet lancée (tâche #{sheets_task_id})')
        except Exception as se:
            logs.append(f'⚠ Sync Sheet non lancée : {se}')

        return jsonify({
            'status': 'ok',
            'logs': logs,
            'found': found,
            'not_found': not_found,
            'total_shazam': len(shazam_rows),
            'sheets_task_id': sheets_task_id
        })
    except Exception as e:
        logs.append(f'❌ Erreur : {e}')
        return jsonify({'status': 'error', 'message': str(e), 'logs': logs}), 500
