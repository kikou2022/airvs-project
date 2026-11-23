#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  SYNCHRO DIFFUSION A — T2 (Phase 21, tranche A « scoring »)              ║
# ║  Module worker : réécriture des compteurs RadioDJ depuis l'historique    ║
# ║  AzuraCast (stations 6 et 7, bibliothèque partagée).                     ║
# ╠══════════════════════════════════════════════════════════════════════════╣
# ║  Spécification : RESULTAT_SONDES_T1_2026-11-21.md §6 (verrouillée par    ║
# ║  les sondes T1) + cadrage CADRAGE_SYNCHRO_DIFFUSION_A_2026-11-13.md.     ║
# ║  Arbitrages validés par l'exploitant le 22/11/2026 :                     ║
# ║    D1 : count_played = vérité AzuraCast (somme S6+S7, historique fin)    ║
# ║        + cumul des deltas RadioDJ (directs BUTT invisibles d'AzuraCast,  ║
# ║        F6) ; date_played = date la plus récente (AzuraCast vs RadioDJ).  ║
# ║    D2 : variante « écrasement + delta local » (tranché par F6).          ║
# ║    D3 : 1er run = UPDATE uniquement des lignes réellement modifiées ;    ║
# ║        non-musique « AIRVS » exclue ; ambiguïté = +1 sur TOUS les        ║
# ║        candidats ; échecs journalisés sans écriture.                     ║
# ║    D4-a : 1er run manuel puis cron quotidien (automation, GF7).          ║
# ║    D5 : source = /api/station/{6,7}/history complet sans limit (F2, F4). ║
# ╠══════════════════════════════════════════════════════════════════════════╣
# ║  Chaîne de résolution (passes hash inopérantes — F8) :                   ║
# ║    P3 exact → P4 normalisé → P5 artiste inclus → P6 titre sans           ║
# ║    parenthèses → N1 séparateurs multi-artistes → N2 clé compacte         ║
# ║    (candidat unique exigé, plusieurs candidats → ambiguïté).             ║
# ║  Performance attendue : ~89-90 % de la musique résolue (F15).            ║
# ╠══════════════════════════════════════════════════════════════════════════╣
# ║  Sécurité (cadrage §3) :                                                 ║
# ║   - snapshot bak_songs_counters_<date> créé automatiquement AVANT la     ║
# ║     première écriture du 1er run (rollback instantané) ;                 ║
# ║   - UPDATE par lots de 300 lignes (cadrage : 200-500) — RadioDJ en prod ; ║
# ║   - journal_operations_azura type_op='synchro_diffusion' (ENUM élargi    ║
# ║     de façon idempotente ici et au démarrage du worker) ;                ║
# ║   - weight / date_added / comments : JAMAIS touchés ;                    ║
# ║   - clé API jamais affichée ni journalisée.                              ║
# ╠══════════════════════════════════════════════════════════════════════════╣
# ║  Source du delta RadioDJ : option (a) du cadrage — différence du         ║
# ║  compteur radiodb.songs.count_played entre deux cycles (snapshot par     ║
# ║  titre dans synchro_diffusion_compteurs). Sensible aux corrections       ║
# ║  manuelles de compteurs (delta négatif borné à 0, jamais décompté).      ║
# ║  La table radiodb.history n'est PAS utilisée (schéma/volume non          ║
# ║  vérifiés par les sondes — option (b) réservée).                         ║
# ╚══════════════════════════════════════════════════════════════════════════╝

import json
import os
import re
import sys
import unicodedata
import datetime as _dt

try:
    import pymysql
except ImportError:  # selftest sans MySQL
    pymysql = None

try:
    import requests
except ImportError:
    requests = None

SYNCHRO_VERSION = "2026.11.22-A"

# ── Paramètres opérationnels (D3 : UPDATE par lots 200-500 ; F2 : history
#    complet sans limit — timeout généreux, appel mesuré < 120 s) ──
TAILLE_LOT = 300
HIST_TIMEOUT_S = 180
NON_MUSIQUE_PREFIXE = "AIRVS"          # F9/F13 : IDs, jingles, virgules, promos
CAP_ECHECS_DETAILS = 200               # échantillon stocké dans le journal JSON
TZ_DEFAUT = "Europe/Paris"             # date_played radiodb = DATETIME local (.30)

# ── Tables d'état T2 (base airvs_dashboard, créées idempotent) ──
SQL_TABLE_CURSORS = """
CREATE TABLE IF NOT EXISTS synchro_diffusion_cursors (
  station_id INT PRIMARY KEY,
  dernier_vu BIGINT UNSIGNED NOT NULL DEFAULT 0
      COMMENT 'epoch Unix max deja traite pour cette station (history)',
  premier_run TINYINT NOT NULL DEFAULT 1
      COMMENT '1 = prochain cycle = premier run (snapshot + fenetre complete)',
  date_dernier_cycle DATETIME NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""
SQL_TABLE_COMPTEURS = """
CREATE TABLE IF NOT EXISTS synchro_diffusion_compteurs (
  song_id_radiodj INT PRIMARY KEY,
  azura_count INT UNSIGNED NOT NULL DEFAULT 0
      COMMENT 'verite AzuraCast cumulee (somme S6+S7)',
  azura_last_ts BIGINT UNSIGNED NULL
      COMMENT 'dernier played_at AzuraCast (epoch Unix)',
  delta_cumul INT NOT NULL DEFAULT 0
      COMMENT 'cumul des +1 RadioDJ (directs) depuis le 1er run',
  radiodj_snapshot INT NOT NULL DEFAULT 0
      COMMENT 'compteur RadioDJ lu au dernier cycle (base du delta)',
  radiodj_date DATETIME NULL
      COMMENT 'date_played RadioDJ lue au dernier cycle',
  date_creation DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""
# ENUM journal : élargissement idempotent (même manipulation que
# mapping_bootstrap — cadrage §3 point 3, RESULTAT_SONDES §6 point 6).
SQL_ENUM_JOURNAL = """
ALTER TABLE journal_operations_azura MODIFY type_op ENUM(
  'push', 'playlist_add', 'playlist_remove', 'move', 'delete',
  'rename', 'empty_playlist', 'rotation_soft', 'rotation_hard',
  'auto_purge', 'flag_force', 'flag_resync', 'm3u_push',
  'mapping_bootstrap', 'mirror_regen', 'rotation_preview',
  'synchro_diffusion'
) NOT NULL
"""


# ═══════════════════════════════════════════════════════════════════════════
# Normalisation — copie conforme au résolveur rotation_azura.py / sondes
# ═══════════════════════════════════════════════════════════════════════════

def normaliser_pour_recherche(texte):
    """Normalisation pour comparaison floue (accents, casse, ponctuation)."""
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


def normaliser_etendu(texte):
    """N1 — séparateurs multi-artistes (« ; », « , », « / », « | ») → espace
    AVANT la normalisation (F15 : AzuraCast « A; B » vs RadioDJ « A;B »)."""
    return normaliser_pour_recherche(re.sub(r"[;/|,]", " ", str(texte or "")))


def cle_compacte(texte):
    """N2 — clé compacte alphanumérique seule (AC/DC ↔ ACDC, C’est ↔ C'est)."""
    return re.sub(r"[^a-z0-9]", "", normaliser_pour_recherche(texte))


def sans_parens(texte):
    """P6 — retire les segments entre parenthèses."""
    return re.sub(r"\([^)]*\)", " ", str(texte or ""))


def artiste_inclus(a_az, a_rdj):
    """P5/P6 — artiste inclus dans les deux sens (comparaison normalisée)."""
    a_az, a_rdj = (a_az or "").strip(), (a_rdj or "").strip()
    if not a_az or not a_rdj:
        return False
    return a_rdj in a_az or a_az in a_rdj


def est_non_musique(artiste):
    """Exclusion D3 : non-musique « artiste commençant par AIRVS » (F9, F13)."""
    return (artiste or "").strip().upper().startswith(NON_MUSIQUE_PREFIXE)


# ═══════════════════════════════════════════════════════════════════════════
# Résolveur T2 — RESULTAT_SONDES_T1 §6 point 3 (chaîne verrouillée)
# ═══════════════════════════════════════════════════════════════════════════

class ResolveurSynchro:
    """Index du mapping_radiodj_azura (union S6+S7, song_id_radiodj uniques).

    resoudre() retourne (methode, ids, ambigu) :
      - ids : liste de song_id_radiodj (>= 1 si résolu) ;
      - ambigu : True si plusieurs candidats → règle D3 « +1 sur TOUS les
        candidats » (F10/F13 : le scoring rotation groupe par artist+title
        normalisé → total de groupe exact) ;
      - (None, [], False) si échec → journalisation, aucune écriture (D3).
    """

    def __init__(self, rows_mapping):
        uniques = {}
        for r in rows_mapping:
            sid = r.get("song_id_radiodj")
            if sid is None:
                continue
            # première ligne vue pour ce song_id (union des stations)
            uniques.setdefault(int(sid), r)
        self.idx_exact = {}       # (artist strip, title strip) → [sid]
        self.idx_norm = {}        # (a_n, t_n) → [sid]
        self.titre_norm = {}      # t_n → [(a_n, sid)]            (P5)
        self.titre_sp = {}        # t_n(sans parens) → [(a_n, sid)] (P6)
        self.titre_etendu = {}    # t_ext → [(a_ext, sid)]        (N1)
        self.compacte = {}        # (ca, ct) → [sid]              (N2)
        for sid, r in uniques.items():
            a = (r.get("artist_radiodj") or "").strip()
            t = (r.get("title_radiodj") or "").strip()
            if not a or not t:
                continue
            a_n = normaliser_pour_recherche(a)
            t_n = normaliser_pour_recherche(t)
            self.idx_exact.setdefault((a, t), []).append(sid)
            if t_n:
                self.idx_norm.setdefault((a_n, t_n), []).append(sid)
                self.titre_norm.setdefault(t_n, []).append((a_n, sid))
            t_sp = normaliser_pour_recherche(sans_parens(t))
            if t_sp:
                self.titre_sp.setdefault(t_sp, []).append((a_n, sid))
            a_ext, t_ext = normaliser_etendu(a), normaliser_etendu(t)
            if a_ext and t_ext:
                self.titre_etendu.setdefault(t_ext, []).append((a_ext, sid))
            ca, ct = cle_compacte(a), cle_compacte(t)
            if ca and ct:
                self.compacte.setdefault((ca, ct), []).append(sid)

    @staticmethod
    def _conclure(cands):
        """Liste de song_id → (methode_applicable, ids_dedup, ambigu)."""
        ids = sorted({int(x) for x in cands if x is not None})
        if not ids:
            return [], False
        return ids, len(ids) > 1

    def resoudre(self, artist_az, title_az):
        """Chaîne §6 : P3 → P4 → P5 → P6 → N1 → N2 (candidat unique exigé)."""
        a = (artist_az or "").strip()
        t = (title_az or "").strip()
        if not a or not t:
            return None, [], False
        # P3 — artist+title exact
        ids, amb = self._conclure(self.idx_exact.get((a, t), []))
        if ids:
            return "P3_exact", ids, amb
        # P4 — artist+title normalisé
        a_n, t_n = normaliser_pour_recherche(a), normaliser_pour_recherche(t)
        if t_n:
            ids, amb = self._conclure(self.idx_norm.get((a_n, t_n), []))
            if ids:
                return "P4_normalise", ids, amb
            # P5 — titre normalisé égal + artiste inclus (2 sens)
            cands = [sid for a2, sid in self.titre_norm.get(t_n, [])
                     if artiste_inclus(a_n, a2)]
            ids, amb = self._conclure(cands)
            if ids:
                return "P5_artiste_inclus", ids, amb
            # P6 — titre débarrassé de ses parenthèses + artiste inclus
            t_sp = normaliser_pour_recherche(sans_parens(t))
            if t_sp and t_sp != t_n:
                cands = [sid for a2, sid in self.titre_sp.get(t_sp, [])
                         if artiste_inclus(a_n, a2)]
                ids, amb = self._conclure(cands)
                if ids:
                    return "P6_titre_sans_parens", ids, amb
        # N1 — séparateurs multi-artistes + égalité stricte des deux champs
        a_ext, t_ext = normaliser_etendu(a), normaliser_etendu(t)
        if t_ext and a_ext:
            cands = [sid for a2, sid in self.titre_etendu.get(t_ext, [])
                     if a_ext == a2]
            ids, amb = self._conclure(cands)
            if ids:
                return "N1_separateurs", ids, amb
        # N2 — clé compacte alphanumérique ; candidat unique exigé,
        # plusieurs candidats → ambiguïté (+1 sur tous — §6 point 4)
        ca, ct = cle_compacte(a), cle_compacte(t)
        if ca and ct:
            ids, amb = self._conclure(self.compacte.get((ca, ct), []))
            if ids:
                return "N2_cle_compacte" + ("_ambigu" if amb else ""), ids, amb
        return None, [], False


# ═══════════════════════════════════════════════════════════════════════════
# Schéma d'état + journal (idempotent)
# ═══════════════════════════════════════════════════════════════════════════

def assurer_schema_synchro(db_config, log_fn=None):
    """Crée les tables d'état T2 et élargit l'ENUM du journal (idempotent).
    Non bloquant : un échec est signalé mais ne doit pas tuer le worker."""
    _log = log_fn or (lambda m: print(m))
    ok = True
    try:
        db = pymysql.connect(**db_config)
        cur = db.cursor()
        cur.execute(SQL_TABLE_CURSORS)
        cur.execute(SQL_TABLE_COMPTEURS)
        try:
            cur.execute(SQL_ENUM_JOURNAL)
        except Exception:
            pass  # ENUM déjà élargi ou droits insuffisants — non bloquant
        db.commit()
        cur.close()
        db.close()
        _log(f"[SYNCHRO] Tables d'état vérifiées (cursors + compteurs) — v{SYNCHRO_VERSION}")
    except Exception as e:
        ok = False
        _log(f"[SYNCHRO] Schéma d'état non créé (non bloquant) : {e}")
    return ok


def assurer_snapshot_compteurs(db_config, log_fn=None, nom_table=None):
    """Cadrage §3 point 1 — snapshot rollback instantané AVANT le 1er run :
    CREATE TABLE airvs_dashboard.bak_songs_counters_<date> AS
    SELECT ID, count_played, date_played FROM radiodb.songs ;
    Si un snapshot du jour existe déjà → réutilisé (idempotent).
    Retourne (nom_table, stats) ou (None, {'erreur': ...})."""
    _log = log_fn or (lambda m: print(m))
    stats = {}
    try:
        db = pymysql.connect(**db_config)
        cur = db.cursor()
        if not nom_table:
            nom_table = "bak_songs_counters_" + _dt.datetime.now().strftime("%Y%m%d")
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (nom_table,))
        deja_la = (cur.fetchone()[0] or 0) > 0
        if not deja_la:
            cur.execute(
                "CREATE TABLE {0} AS "
                "SELECT ID, count_played, date_played FROM radiodb.songs".format(nom_table))
            _log(f"[SYNCHRO] Snapshot créé : {nom_table} (rollback instantané possible)")
        else:
            _log(f"[SYNCHRO] Snapshot du jour déjà présent : {nom_table} (réutilisé)")
        cur.execute(
            "SELECT COUNT(*) AS lignes, COALESCE(SUM(count_played),0) AS total_cpt "
            "FROM {0}".format(nom_table))
        row = cur.fetchone()
        keys = [d[0] for d in cur.description]
        stats = dict(zip(keys, row))
        db.commit()
        cur.close()
        db.close()
        _log(f"[SYNCHRO] Snapshot {nom_table} : {stats.get('lignes')} lignes, "
             f"total count_played = {stats.get('total_cpt')}")
        return nom_table, stats
    except Exception as e:
        _log(f"[SYNCHRO] Snapshot impossible (BLOQUANT pour l'écriture) : {e}")
        return None, {"erreur": str(e)}


def _restaurer_depuis_snapshot(db_config, nom_table, log_fn=None):
    """Rollback : remet count_played/date_played depuis le snapshot (rare)."""
    _log = log_fn or (lambda m: print(m))
    db = pymysql.connect(**db_config)
    cur = db.cursor()
    cur.execute(
        "UPDATE radiodb.songs s JOIN {0} b ON s.ID = b.ID "
        "SET s.count_played = b.count_played, s.date_played = b.date_played".format(nom_table))
    nb = cur.rowcount
    db.commit()
    cur.close()
    db.close()
    _log(f"[SYNCHRO] Rollback depuis {nom_table} : {nb} lignes restaurées")
    return nb


# ═══════════════════════════════════════════════════════════════════════════
# Lecture d'état + API AzuraCast
# ═══════════════════════════════════════════════════════════════════════════

def _charger_config_azura(chemin_config="config.json"):
    """Lit (base_url, api_key, station_ids) dans config.json section azuracast.
    La clé n'est JAMAIS retournée dans un message de log."""
    with open(chemin_config, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    sec = cfg.get("azuracast") or {}
    base_url = (sec.get("base_url") or "").rstrip("/")
    api_key = sec.get("api_key") or ""
    stations = [int(s["id"]) for s in (sec.get("stations") or []) if s.get("id")]
    if not base_url or not api_key or not stations:
        raise RuntimeError("config.json : section azuracast incomplète "
                           "(base_url / api_key / stations)")
    return base_url, api_key, stations


def recuperer_history(base_url, api_key, station_id, timeout=HIST_TIMEOUT_S):
    """GET /api/station/{id}/history SANS limit (F2 : la build ignore limit
    et renvoie tout l'historique en une réponse ; auth Bearer — F1)."""
    if requests is None:
        raise RuntimeError("module requests indisponible")
    r = requests.get(
        "{0}/api/station/{1}/history".format(base_url, station_id),
        headers={"Authorization": "Bearer {0}".format(api_key),
                 "Accept": "application/json"},
        timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = data.get("results", data.get("rows", []))
    return data or []


def _charger_cursors(db_config, stations):
    cursors = {}
    db = pymysql.connect(**db_config)
    cur = db.cursor(pymysql.cursors.DictCursor)
    cur.execute("SELECT station_id, dernier_vu, premier_run "
                "FROM synchro_diffusion_cursors")
    for row in cur.fetchall():
        cursors[int(row["station_id"])] = {
            "dernier_vu": int(row["dernier_vu"] or 0),
            "premier_run": bool(int(row["premier_run"] or 0)),
        }
    cur.close()
    db.close()
    return {sid: cursors.get(sid, {"dernier_vu": 0, "premier_run": True})
            for sid in stations}


def _charger_etat(db_config):
    """synchro_diffusion_compteurs → {sid: dict(état)}."""
    etat = {}
    db = pymysql.connect(**db_config)
    cur = db.cursor(pymysql.cursors.DictCursor)
    cur.execute(
        "SELECT song_id_radiodj, azura_count, azura_last_ts, delta_cumul, "
        "radiodj_snapshot, radiodj_date FROM synchro_diffusion_compteurs")
    for row in cur.fetchall():
        etat[int(row["song_id_radiodj"])] = {
            "azura_count": int(row["azura_count"] or 0),
            "azura_last_ts": int(row["azura_last_ts"]) if row["azura_last_ts"] is not None else None,
            "delta_cumul": int(row["delta_cumul"] or 0),
            "radiodj_snapshot": int(row["radiodj_snapshot"] or 0),
            "radiodj_date": row["radiodj_date"],
        }
    cur.close()
    db.close()
    return etat


def _charger_compteurs_radiodj(db_config, song_ids, log_fn=None):
    """Lit (count_played, date_played) courant de radiodb.songs pour les IDs
    donnés (IN() par paquets de 500). Retourne {sid: {'count': int, 'date': dt|None}}."""
    courant = {}
    ids = sorted(song_ids)
    db = pymysql.connect(**db_config)
    cur = db.cursor()
    for i in range(0, len(ids), 500):
        paquet = ids[i:i + 500]
        ph = ",".join(["%s"] * len(paquet))
        cur.execute(
            "SELECT ID, count_played, date_played FROM radiodb.songs "
            "WHERE ID IN ({0})".format(ph), tuple(paquet))
        for sid, cpt, dpt in cur.fetchall():
            courant[int(sid)] = {
                "count": int(cpt or 0),
                "date": dpt if hasattr(dpt, "year") else None,
            }
    db.close()
    if log_fn:
        log_fn(f"[SYNCHRO] Compteurs RadioDJ lus : {len(courant)}/{len(ids)} titre(s)")
    return courant


def appliquer_cycle_etat(etat, courant, azura_plays, azura_last):
    """Fonction pure — met à jour l'état (in place) pour un cycle :
      - titres DÉJÀ suivis : delta RadioDJ entre deux cycles (option (a) du
        cadrage : courant - snapshot, borné à 0) + nouvelles diffusions
        AzuraCast accumulées (azura_plays) ;
      - titres NOUVEAUX (1er run ou première résolution) : delta initialisé
        au compteur RadioDJ courant (D2 : l'historique RadioDJ préexistant
        est conservé) et vérité AzuraCast de la fenêtre (F12 : biais
        accepté).
    courant : {sid: {'count': int, 'date': dt|None}} lu dans radiodb.songs.
    Retourne le total des +1 RadioDJ observés ce cycle (pour les stats)."""
    delta_total = 0
    for sid, cpt_cur in courant.items():
        plays = azura_plays.get(sid, 0)
        last_ts = azura_last.get(sid)
        if sid in etat:
            st = etat[sid]
            delta_cycle = max(0, cpt_cur["count"] - st["radiodj_snapshot"])
            delta_total += delta_cycle   # source du delta : option (a) du cadrage
            st["delta_cumul"] += delta_cycle
            st["azura_count"] += plays
            if last_ts and (not st.get("azura_last_ts") or last_ts > st["azura_last_ts"]):
                st["azura_last_ts"] = last_ts
        else:
            etat[sid] = {
                "azura_count": plays,             # fenêtre du 1er run (F12)
                "azura_last_ts": last_ts,
                "delta_cumul": cpt_cur["count"],  # D2 : historique RadioDJ conservé
                "radiodj_snapshot": cpt_cur["count"],
                "radiodj_date": cpt_cur["date"],
            }
    return delta_total


def finaliser_etat_apres_ecriture(etat, courant):
    """Fonction pure — radiodj_snapshot = valeur de count_played APRÈS notre
    passage (count_new = azura_count + delta_cumul pour les lignes écrites,
    valeur courante — identique — pour les lignes déjà conformes).
    Appelé AVANT les UPDATE : le snapshot stocké est la valeur PRÉVUE après
    écriture. RadioDJ incrémente ensuite DEPUIS cette valeur : le delta du
    prochain cycle = courant_next - snapshot, soit uniquement ses propres +1
    (aucun double comptage des écritures T2 elles-mêmes).
    Cas dégradé : crash pendant les UPDATE → quelques lignes non écrites avec
    snapshot trop haut → delta négatif borné à 0 au prochain cycle (les
    UPDATE manquants sont refaits)."""
    for sid, st in etat.items():
        if sid in courant:
            st["radiodj_snapshot"] = max(0, int(st["azura_count"] or 0)
                                         + int(st["delta_cumul"] or 0))
            st["radiodj_date"] = courant[sid]["date"]


def _fuseau_local():
    """Datetime naive LOCAL pour comparer avec date_played (DATETIME local .30,
    cadrage §6 : conversion explicite). TZ : env SYNCHRO_TZ > Europe/Paris."""
    tz_name = os.environ.get("SYNCHRO_TZ", TZ_DEFAUT)
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo(tz_name)).tzinfo
    except Exception:
        try:
            import tzlocal
            return tzlocal.get_localzone()
        except Exception:
            return None  # heure locale système


def epoch_vers_local(ts):
    """epoch Unix → datetime naive local (Europe/Paris). None si ts vide."""
    if not ts:
        return None
    tz = _fuseau_local()
    if tz is None:
        return _dt.datetime.fromtimestamp(int(ts))
    return _dt.datetime.fromtimestamp(int(ts), tz).replace(tzinfo=None)


# ═══════════════════════════════════════════════════════════════════════════
# Planification des écritures (fonction pure — testable offline)
# ═══════════════════════════════════════════════════════════════════════════

def planifier_ecritures(etat, courant):
    """D3 : uniquement les lignes RÉELLEMENT modifiées.

    etat    : {sid: état complet après cycle (azura_count, azura_last_ts,
              delta_cumul, radiodj_date…)} — état CIBLE ;
    courant : {sid: {'count': int, 'date': datetime|None}} — valeurs RadioDJ.

    Retour (updates, detail) :
      updates = [(sid, count_new, date_new), …] où count/date diffèrent ;
      detail  = {'deja_conforme': n, 'hors_radiodj': [sid…]}.
    """
    updates, deja_conforme, hors_radiodj = [], 0, []
    for sid, st in sorted(etat.items()):
        cur = courant.get(sid)
        if cur is None:
            hors_radiodj.append(sid)  # disparu de radiodb.songs — rien à écrire
            continue
        count_new = int(st["azura_count"] or 0) + int(st["delta_cumul"] or 0)
        if count_new < 0:
            count_new = 0
        date_new = None
        d_az = epoch_vers_local(st.get("azura_last_ts"))
        # date RadioDJ = lecture FRAÎCHE de radiodb.songs.date_played de ce
        # cycle (RadioDJ l'a peut-être réécrite en jouant le titre) ; la valeur
        # stockée dans l'état est celle du cycle précédent, uniquement
        # indicative.
        d_rdj = cur["date"]
        if hasattr(d_rdj, "year"):
            date_new = d_rdj
        if d_az is not None and (date_new is None or d_az > date_new):
            date_new = d_az
        if count_new != cur["count"] or date_new != cur["date"]:
            updates.append((sid, count_new, date_new))
        else:
            deja_conforme += 1
    detail = {"deja_conforme": deja_conforme, "hors_radiodj": sorted(hors_radiodj)}
    return updates, detail


def executer_update_par_lots(db_config, updates, taille_lot=TAILLE_LOT, log_fn=None):
    """UPDATE radiodb.songs (count_played, date_played) par transactions de
    `taille_lot` lignes (cadrage §3 : 200-500 — éviter les locks longs).
    Retourne le nombre de lignes écrites."""
    _log = log_fn or (lambda m: None)
    ecrites = 0
    if not updates:
        return 0
    db = pymysql.connect(**db_config)
    cur = db.cursor()
    for i in range(0, len(updates), taille_lot):
        lot = updates[i:i + taille_lot]
        for sid, count_new, date_new in lot:
            cur.execute(
                "UPDATE radiodb.songs SET count_played = %s, date_played = %s "
                "WHERE ID = %s", (count_new, date_new, sid))
        db.commit()
        ecrites += len(lot)
        _log(f"[SYNCHRO] Lot {i // taille_lot + 1} : {len(lot)} ligne(s) écrites "
             f"({ecrites}/{len(updates)})")
    cur.close()
    db.close()
    return ecrites


def _sauvegarder_etat(db_config, etat, cursors, taille_lot=TAILLE_LOT):
    """Upsert l'état final (compteurs) + cursors en UNE transaction — point de
    reprise du cycle. Appelé AVANT les UPDATE radiodb.songs : si le worker
    crashe pendant les écritures, l'état est déjà cohérent et le cycle suivant
    se répare (écritures manquantes refaites ; seuls les +1 RadioDJ du crash
    lui-même peuvent manquer, bornés à 0)."""
    if not etat and not cursors:
        return
    lignes = []
    for sid, st in etat.items():
        lignes.append((int(sid),
                       int(st["azura_count"] or 0),
                       st.get("azura_last_ts"),
                       int(st["delta_cumul"] or 0),
                       int(st["radiodj_snapshot"] or 0),
                       st.get("radiodj_date")))
    db = pymysql.connect(**db_config)
    cur = db.cursor()
    for i in range(0, len(lignes), taille_lot):
        lot = lignes[i:i + taille_lot]
        cur.executemany(
            "INSERT INTO synchro_diffusion_compteurs "
            "(song_id_radiodj, azura_count, azura_last_ts, delta_cumul, "
            " radiodj_snapshot, radiodj_date) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE azura_count = VALUES(azura_count), "
            "azura_last_ts = VALUES(azura_last_ts), delta_cumul = VALUES(delta_cumul), "
            "radiodj_snapshot = VALUES(radiodj_snapshot), radiodj_date = VALUES(radiodj_date)",
            lot)
    for sid, c in (cursors or {}).items():
        cur.execute(
            "INSERT INTO synchro_diffusion_cursors "
            "(station_id, dernier_vu, premier_run, date_dernier_cycle) "
            "VALUES (%s, %s, 0, NOW()) "
            "ON DUPLICATE KEY UPDATE dernier_vu = VALUES(dernier_vu), "
            "premier_run = 0, date_dernier_cycle = NOW()",
            (int(sid), int(c.get("dernier_vu") or 0)))
    db.commit()   # un seul point de commit : état + cursors ensemble
    cur.close()
    db.close()


def _journaliser_synchro(db_config, type_op, station_id=None, song_id_radiodj=None,
                         triggered_by="manual", task_id=None, trigger_source=None,
                         details=None):
    """INSERT journal_operations_azura (non bloquant — table absente → ignore)."""
    try:
        db = pymysql.connect(**db_config)
        cur = db.cursor()
        cur.execute(
            "INSERT INTO journal_operations_azura "
            "(type_op, station_id, media_id_azura, song_id_radiodj, "
            " triggered_by, task_id, trigger_source, details, reversible) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (type_op, station_id, None, song_id_radiodj, triggered_by, task_id,
             trigger_source,
             json.dumps(details, ensure_ascii=False, default=str) if details else None,
             1))
        db.commit()
        cur.close()
        db.close()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# Cycle principal
# ═══════════════════════════════════════════════════════════════════════════

def executer_synchro_diffusion(tache_id=None, parametres=None, db_config=None,
                               log_fn=None, chemin_config="config.json"):
    """Tâche SYNCHRO_DIFFUSION — un cycle complet (1er run ou incrémental).

    parametres : {'dry_run': bool, 'auto_scheduled': bool, 'station_ids': […]}
    Retour : dict de stats (aussi écrit dans le journal + logs).
    """
    _log = log_fn or (lambda m: print(m))
    parametres = parametres or {}
    dry_run = bool(parametres.get("dry_run"))
    auto = bool(parametres.get("auto_scheduled"))
    triggered_by = "cron" if auto else "manual"
    stats = {"version": SYNCHRO_VERSION, "dry_run": dry_run,
             "triggered_by": triggered_by}

    # ── 0. schéma d'état + cursors + config ──────────────────────────────
    assurer_schema_synchro(db_config, log_fn=_log)
    base_url, api_key, stations_cfg = _charger_config_azura(chemin_config)
    stations = parametres.get("station_ids") or stations_cfg
    cursors = _charger_cursors(db_config, stations)
    premier_run = any(c["premier_run"] for c in cursors.values())
    stats["stations"] = stations
    stats["premier_run"] = premier_run
    _log(f"[SYNCHRO] Cycle — stations {stations}, premier_run={premier_run}, "
         f"dry_run={dry_run}")

    # ── 1. mapping actif → résolveur ─────────────────────────────────────
    db = pymysql.connect(**db_config)
    cur = db.cursor(pymysql.cursors.DictCursor)
    cur.execute(
        "SELECT song_id_radiodj, artist_radiodj, title_radiodj, actif "
        "FROM mapping_radiodj_azura WHERE actif = TRUE")
    rows_mapping = cur.fetchall()
    cur.close()
    db.close()
    resolveur = ResolveurSynchro(rows_mapping)
    stats["mapping_lignes_actives"] = len(rows_mapping)
    _log(f"[SYNCHRO] Mapping actif : {len(rows_mapping)} ligne(s)")

    # ── 2. history par station → filtrage → résolution → accumulation ────
    #    azura_plays[sid] = +1 par diffusion résolue (somme S6+S7 — D1)
    azura_plays, azura_last = {}, {}
    echecs, nb_non_musique, nb_entrees, nb_resolues = [], 0, 0, 0
    par_methode = {}
    max_vus = {sid: cursors[sid]["dernier_vu"] for sid in stations}

    for sid in stations:
        entrees = recuperer_history(base_url, api_key, sid)
        stats["history_station_{0}".format(sid)] = len(entrees)
        dernier_vu = cursors[sid]["dernier_vu"]
        vus_cote = set()
        for e in entrees:
            song = e.get("song") or {}
            played_at = int(e.get("played_at") or 0)
            if played_at <= dernier_vu:
                continue  # déjà traité lors d'un cycle précédent (F12 : fenêtre)
            nb_entrees += 1
            hash_az = str(song.get("id") or "")
            cle = (hash_az or "{0}|{1}".format(song.get("artist") or "",
                                               song.get("title") or ""),
                   played_at)
            if cle in vus_cote:
                continue  # dédoublonnage intra-réponse (même entrée re-listée)
            vus_cote.add(cle)
            if played_at > max_vus[sid]:
                max_vus[sid] = played_at
            artiste = (song.get("artist") or "").strip()
            titre = (song.get("title") or "").strip()
            if est_non_musique(artiste):
                nb_non_musique += 1      # D3 : aucun compteur pour ces entrées
                continue
            methode, ids, _amb = resolveur.resoudre(artiste, titre)
            if not ids:
                echecs.append({"station": sid, "artist": artiste, "title": titre,
                               "played_at": played_at})
                continue
            nb_resolues += 1
            par_methode[methode] = par_methode.get(methode, 0) + 1
            for cible in ids:            # ambiguïté → +1 sur TOUS (D3)
                azura_plays[cible] = azura_plays.get(cible, 0) + 1
                if played_at > (azura_last.get(cible) or 0):
                    azura_last[cible] = played_at
        _log(f"[SYNCHRO] Station {sid} : {len(entrees)} entrée(s) lue(s), "
             f"nouvelle(s) après filtrage dernier_vu={dernier_vu}")

    stats.update({
        "entrees_nouvelles": nb_entrees, "resolues": nb_resolues,
        "non_musique_exclue": nb_non_musique,
        "echecs_musique": len(echecs), "par_methode": par_methode,
        "titres_touches": len(azura_plays),
    })
    _log(f"[SYNCHRO] Nouvelles entrées musicales : {nb_entrees} ; résolues : "
         f"{nb_resolues} ; échecs : {len(echecs)} ; non-musique exclue : "
         f"{nb_non_musique} ; par méthode : {par_methode}")

    # ── 3. état : init 1er run (D2 : delta initialisé aux compteurs RDJ) ──
    etat = _charger_etat(db_config)
    ids_a_lire = set(etat) | set(azura_plays)
    courant = _charger_compteurs_radiodj(db_config, ids_a_lire, log_fn=_log)
    nouveaux_titres = [sid for sid in azura_plays if sid not in etat]
    stats["titres_nouveaux"] = len(nouveaux_titres)
    stats["delta_radio_dj_cycle"] = appliquer_cycle_etat(etat, courant,
                                                         azura_plays, azura_last)

    # ── 4. écritures ─────────────────────────────────────────────────────
    updates, detail_plan = planifier_ecritures(etat, courant)
    stats["updates_prevus"] = len(updates)
    stats.update(detail_plan)
    _log(f"[SYNCHRO] Lignes à écrire : {len(updates)} "
         f"(déjà conformes : {detail_plan['deja_conforme']}, "
         f"hors radiodb : {len(detail_plan['hors_radiodj'])})")

    if dry_run:
        _log("[SYNCHRO] DRY RUN — aucune écriture (radiodb et état inchangés)")
        stats["status"] = "dry_run"
        _journaliser_synchro(db_config, "synchro_diffusion", triggered_by=triggered_by,
                             task_id=tache_id, trigger_source=parametres.get("trigger_source"),
                             details={"stats": stats, "dry_run": True,
                                      "echecs_sample": echecs[:CAP_ECHECS_DETAILS]})
        return stats

    # Garde de sécurité : snapshot AVANT la première écriture du 1er run
    if premier_run and updates:
        nom_snap, snap_stats = assurer_snapshot_compteurs(db_config, log_fn=_log)
        if nom_snap is None:
            _log("[SYNCHRO] ABANDON : snapshot impossible, aucune écriture effectuée")
            stats["status"] = "abandon_snapshot"
            return stats
        stats["snapshot"] = nom_snap

    # ── 5. état final — snapshot = valeur PRÉVUE après écriture ─────────
    #    (RadioDJ incrémentera depuis cette valeur → delta du prochain cycle
    #    = ses propres +1 uniquement, aucun double comptage des écritures T2)
    finaliser_etat_apres_ecriture(etat, courant)

    # ── 6. Sauvegarde état + cursors (UNE transaction) AVANT les UPDATE ──
    #    Reprise propre si crash pendant les écritures : les UPDATE manquants
    #    sont refaits au cycle suivant (dernier_vu déjà avancé, azura_count
    #    déjà crédité → aucun double comptage de l'historique AzuraCast).
    cursors_finaux = {sid: {"dernier_vu": max_vus[sid]} for sid in stations}
    _sauvegarder_etat(db_config, etat, cursors_finaux)

    # ── 7. écritures radiodb.songs par lots ──────────────────────────────
    ecrites = executer_update_par_lots(db_config, updates, log_fn=_log)
    stats["lignes_ecrites"] = ecrites

    # ── 8. journal ───────────────────────────────────────────────────────
    details = {"stats": stats,
               "echecs_sample": echecs[:CAP_ECHECS_DETAILS],
               "nb_echecs_echantillonnes": min(len(echecs), CAP_ECHECS_DETAILS)}
    _journaliser_synchro(db_config, "synchro_diffusion", triggered_by=triggered_by,
                         task_id=tache_id,
                         trigger_source=parametres.get("trigger_source"),
                         details=details)
    if echecs:
        echantillon = echecs[:30]
        _log("[SYNCHRO] Échecs de résolution (échantillon, aucune écriture) :")
        for e in echantillon:
            _log("    S{station} — {artist} — {title} (@{played_at})".format(**e))
        _log(f"[SYNCHRO] {len(echecs)} échec(s) au total — vivier campagne "
             f"manuelle (P8b/P7) à extraire du journal à ~2 semaines de T2")
    stats["status"] = "ok"
    _log(f"[SYNCHRO] === TERMINÉ === lignes écrites : {ecrites}")
    return stats


# ═══════════════════════════════════════════════════════════════════════════
# Selftest — logique seule, sans MySQL ni API (python3 synchro_diffusion.py)
# ═══════════════════════════════════════════════════════════════════════════

def selftest():
    """Vérifie la chaîne de résolution et la planification d'écritures."""
    mapping = [
        {"song_id_radiodj": 101, "artist_radiodj": "Depeche Mode", "title_radiodj": "Enjoy the Silence"},
        {"song_id_radiodj": 102, "artist_radiodj": "Nena", "title_radiodj": "99 Luftballons"},
        {"song_id_radiodj": 103, "artist_radiodj": "Sylvie Vartan", "title_radiodj": "Nicola"},
        {"song_id_radiodj": 104, "artist_radiodj": "A;B", "title_radiodj": "Fade"},
        {"song_id_radiodj": 105, "artist_radiodj": "AC/DC", "title_radiodj": "You Shook Me All Night Long"},
        {"song_id_radiodj": 106, "artist_radiodj": "AC DC", "title_radiodj": "You Shook Me All Night Long"},
        {"song_id_radiodj": 107, "artist_radiodj": "The Carpenters", "title_radiodj": "Yesterday Once More"},
        {"song_id_radiodj": 108, "artist_radiodj": "Maxi Priest", "title_radiodj": "Wild World"},
        {"song_id_radiodj": 109, "artist_radiodj": "Queensryche", "title_radiodj": "Silent Lucidity"},
        {"song_id_radiodj": 110, "artist_radiodj": "C’est Encore", "title_radiodj": "Mr. Mister Break"},
        {"song_id_radiodj": 111, "artist_radiodj": "Azura Truc", "title_radiodj": "Titre Exclu"},
    ]
    r = ResolveurSynchro(mapping)

    def attendu(nom, artiste, titre, methode_att, ids_att=None, ambigu_att=None):
        m, ids, amb = r.resoudre(artiste, titre)
        ok = (m == methode_att
              and (ids_att is None or ids == ids_att)
              and (ambigu_att is None or amb == ambigu_att))
        print(("  PASS " if ok else "  FAIL ") + nom +
              f" → {m} {ids} ambigu={amb}")
        return ok

    ok = True
    print("Selftest synchro_diffusion v{0} — résolveur §6".format(SYNCHRO_VERSION))
    ok &= attendu("P3 exact", "Depeche Mode", "Enjoy the Silence", "P3_exact", [101], False)
    ok &= attendu("P4 normalisé (casse/accents)", "DEPECHE MODE", "enjoy the silence",
                  "P4_normalise", [101], False)
    # ambiguïté P3 : doublon artist+title RadioDJ → +1 sur TOUS les candidats (F10)
    r2 = ResolveurSynchro(mapping + [
        {"song_id_radiodj": 112, "artist_radiodj": "Nena", "title_radiodj": "99 Luftballons"}])
    m, ids, amb = r2.resoudre("Nena", "99 Luftballons")
    _ok_dup = (m == "P3_exact" and sorted(ids) == [102, 112] and amb is True)
    ok &= _ok_dup
    print(("  PASS " if _ok_dup else "  FAIL ") + f"ambiguïté P3 → {ids} ambigu={amb}")
    ok &= attendu("P5 artiste inclus", "Sylvie Vartan & Charles Aznavour", "Nicola",
                  "P5_artiste_inclus", [103], False)
    ok &= attendu("N1 séparateurs « A; B » vs « A;B »", "A; B", "Fade",
                  "N1_separateurs", [104], False)
    ok &= attendu("N1 séparateurs « A/B »", "A/B", "Fade", "N1_separateurs", [104], False)
    ok &= attendu("N2 compact (AC/DC ↔ A_DC) ambigu → +1 tous", "ACDC",
                  "You Shook Me All Night Long", "N2_cle_compacte_ambigu", [105, 106], True)
    ok &= attendu("P6 titre AzuraCast avec parenthèses, RadioDJ sans",
                  "Queensryche", "Silent Lucidity (Live)",
                  "P6_titre_sans_parens", [109], False)
    ok &= attendu("N2 compact apostrophe typographique", "C'est Encore", "Mr Mister Break",
                  "N2_cle_compacte", [110], False)
    # P7 rejetée : titre seul sans artiste plausible ne doit PAS passer par P5/P6/N1/N2
    m, ids, _ = r.resoudre("Iggy Pop", "Wild World")  # artiste différent → échec
    _ok_p7 = (m is None and ids == [])
    ok &= _ok_p7
    print(("  PASS " if _ok_p7 else "  FAIL ") + "P7 rejetée (artiste différent → échec, aucune écriture)")
    # exclusion AIRVS
    _ok_airvs = est_non_musique("AIRVS - Jingle 01") and not est_non_musique("Nena")
    ok &= _ok_airvs
    print(("  PASS " if _ok_airvs else "  FAIL ") + "exclusion non-musique AIRVS")

    # planifier_ecritures : uniquement les lignes réellement modifiées
    print("Selftest — planification des écritures (D3)")
    import datetime as dtm
    cible_date = dtm.datetime(2026, 11, 22, 10, 30, 0)
    etat = {
        101: {"azura_count": 12, "azura_last_ts": None, "delta_cumul": 3,
              "radiodj_snapshot": 50, "radiodj_date": cible_date},
        102: {"azura_count": 5, "azura_last_ts": None, "delta_cumul": 4,
              "radiodj_snapshot": 9, "radiodj_date": cible_date},
        999: {"azura_count": 1, "azura_last_ts": None, "delta_cumul": 0,
              "radiodj_snapshot": 1, "radiodj_date": None},
    }
    # La date cible vient de radiodj_date (deterministe, sans fuseau) ; le max
    # AzuraCast/RadioDJ est couvert par la logique de planifier_ecritures.
    courant = {101: {"count": 15, "date": cible_date},     # conforme → aucune écriture
               102: {"count": 8, "date": cible_date},      # attendu 9 → écriture
               }
    updates, detail = planifier_ecritures(etat, courant)
    ids_upd = [u[0] for u in updates]
    ok2 = (ids_upd == [102] and detail["deja_conforme"] == 1
           and detail["hors_radiodj"] == [999])
    print(("  PASS " if ok2 else "  FAIL ") + f"updates={ids_upd} detail={detail}")
    c102 = [u for u in updates if u[0] == 102]
    ok2 &= (c102 and c102[0][1] == 9)  # azura 5 + delta 4
    print(("  PASS " if ok2 else "  FAIL ") + "count_new = azura_count + delta_cumul (102 → 9)")
    ok &= ok2

    # Simulation 3 cycles (fonctions réelles) — continuité du delta D1/D2
    print("Selftest — simulation 3 cycles (delta RadioDJ, aucun double comptage)")
    SID = 50
    etat_c = {}                       # synchro_diffusion_compteurs (vide au 1er run)
    courant_db = {SID: {"count": 10, "date": None}}   # radiodb.songs.count_played
    d1 = dtm.datetime(2026, 11, 22, 8, 0, 0)

    # Cycle 1 — premier run : fenêtre AzuraCast = 5 diffusions, RadioDJ = 10
    # (sans played_at : date de référence = celle de RadioDJ, déterministe)
    d_c1 = appliquer_cycle_etat(etat_c, courant_db, {SID: 5}, {})
    up1, _ = planifier_ecritures(etat_c, courant_db)
    finaliser_etat_apres_ecriture(etat_c, courant_db)
    _c1 = (d_c1 == 0 and up1 == [(SID, 15, None)]
           and etat_c[SID]["radiodj_snapshot"] == 15
           and etat_c[SID]["delta_cumul"] == 10 and etat_c[SID]["azura_count"] == 5)
    print(("  PASS " if _c1 else "  FAIL ") + "cycle 1 : écrit 15 (azura 5 + delta 10), snapshot 15")
    ok2 &= _c1
    # RadioDJ a joué 2 fois depuis (compteur 10 → écrit 15 → 15+2 = 17) ; azura +1
    courant_db = {SID: {"count": 17, "date": d1}}
    d_c2 = appliquer_cycle_etat(etat_c, courant_db, {SID: 1}, {})
    up2, _ = planifier_ecritures(etat_c, courant_db)
    finaliser_etat_apres_ecriture(etat_c, courant_db)
    _c2 = (d_c2 == 2 and up2 == [(SID, 18, d1)]
           and etat_c[SID]["radiodj_snapshot"] == 18
           and etat_c[SID]["delta_cumul"] == 12 and etat_c[SID]["azura_count"] == 6)
    print(("  PASS " if _c2 else "  FAIL ")
          + f"cycle 2 : delta RadioDJ = {d_c2} (+2 directs), écrit 18, delta_cumul 12")
    ok2 &= _c2
    # Cycle 3 : rien ne bouge → déjà conforme, aucune écriture
    courant_db = {SID: {"count": 18, "date": d1}}
    d_c3 = appliquer_cycle_etat(etat_c, courant_db, {}, {})
    up3, det3 = planifier_ecritures(etat_c, courant_db)
    _c3 = (d_c3 == 0 and up3 == [] and det3["deja_conforme"] == 1)
    print(("  PASS " if _c3 else "  FAIL ") + "cycle 3 : déjà conforme, 0 écriture")
    ok2 &= _c3
    # Cycle 4 : correction MANUELLE du compteur RadioDJ (18 → 10)
    # → delta borné à 0, auto-réparation : réécrit 18 (pas de sous-comptage)
    courant_db = {SID: {"count": 10, "date": d1}}
    d_c4 = appliquer_cycle_etat(etat_c, courant_db, {}, {})
    up4, _ = planifier_ecritures(etat_c, courant_db)
    _c4 = (d_c4 == 0 and up4 == [(SID, 18, d1)])
    print(("  PASS " if _c4 else "  FAIL ")
          + "cycle 4 : correction manuelle absorbée (delta 0, réécrit 18)")
    ok2 &= _c4
    ok &= ok2

    print("\nRÉSULTAT GLOBAL : " + ("TOUS LES TESTS PASSENT" if ok else "ÉCHECS PRÉSENTS"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest())
