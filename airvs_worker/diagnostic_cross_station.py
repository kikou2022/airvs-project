#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Diagnostic 2026.11.21-A — Cross-station médias/playlists AzuraCast
===================================================================
Contexte : lors du retrait d'un titre d'une playlist (onglet Action AzuraCast),
la liste des playlists remontée ne correspondait pas à la station sélectionnée
(mêmes IDs de playlists [465, 438, 437, 440] servis pour station 7 puis station 6).

Ce script VÉRIFIE (lecture seule, aucun PUT/DELETE) :
  1. Le song RadioDJ 68528 (le titre manipulé) : artist, title, path.
  2. Dans quelle(s) bibliothèque(s) de station figure le média 13650
     (listing GET /station/{s}/files — détecte si le listing est cross-station).
  3. Si GET /station/{s}/file/13650 répond depuis les DEUX stations
     (permissivité de l'API en lecture cross-station).
  4. À quelle station appartiennent réellement les playlists 465, 438, 437, 440.
  5. L'état du cache SQL azuracast_playlists_cache (colonne station_id présente ?).

Usage (machine worker, à côté de azuracast_api.py) :
    python3 diagnostic_cross_station.py
Env : AIRVS_WORKER_DIR=/chemin/vers/worker si le script est lancé ailleurs.

READ-ONLY : uniquement des GET API et des SELECT SQL.
"""

import json
import os
import sys

MEDIA_ID = 13650
SONG_ID = 68528
PLAYLISTS_A_LOCALISER = [465, 438, 437, 440]
STATIONS = [6, 7]

# ─────────────────────────────────────────────────────────────────
# Résolution du module azuracast_api (portable : dossier du script,
# sous-dossier airvs_worker/, ou variable AIRVS_WORKER_DIR)
# ─────────────────────────────────────────────────────────────────
def resoudre_dossier_worker():
    candidats = [
        os.path.dirname(os.path.abspath(__file__)),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'airvs_worker'),
        os.environ.get('AIRVS_WORKER_DIR', ''),
    ]
    for c in candidats:
        if c and os.path.isfile(os.path.join(c, 'azuracast_api.py')):
            return c
    return None


DOSSIER_WORKER = resoudre_dossier_worker()
if not DOSSIER_WORKER:
    print("[ERREUR] azuracast_api.py introuvable (dossier du script, ./airvs_worker/, AIRVS_WORKER_DIR).")
    sys.exit(1)

sys.path.insert(0, DOSSIER_WORKER)
try:
    from azuracast_api import get_api_for_station, AzuraCastAPIError
except ImportError as e:
    print(f"[ERREUR] Import azuracast_api impossible depuis {DOSSIER_WORKER} : {e}")
    sys.exit(1)

print("=" * 66)
print("DIAGNOSTIC CROSS-STATION 2026.11.21-A  (lecture seule)")
print(f"Dossier worker : {DOSSIER_WORKER}")
print(f"Média ciblé : {MEDIA_ID} · Song RadioDJ : {SONG_ID} · Playlists : {PLAYLISTS_A_LOCALISER}")
print("=" * 66)

# ─────────────────────────────────────────────────────────────────
# Connexion MySQL (mêmes paramètres que DB_CONFIG du worker)
# ─────────────────────────────────────────────────────────────────
import pymysql

DB_CONFIG = {
    "host": "192.168.1.30",
    "port": 3306,
    "user": "airvs_user",
    "password": "idylle@SL2026!",
    "database": "airvs_dashboard",
    "charset": "utf8mb4",
}


def mysql_query(sql, params=None):
    db = pymysql.connect(**DB_CONFIG)
    try:
        cur = db.cursor(pymysql.cursors.DictCursor)
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        db.close()


song = None
basename_radiodj = None
try:
    rows = mysql_query("SELECT ID, artist, title, `path` FROM radiodb.songs WHERE ID = %s", (SONG_ID,))
    song = rows[0] if rows else None
except Exception as e:
    print(f"[2a] ERREUR MySQL lecture song {SONG_ID} : {e}")

print()
print("─── [1/5] Song RadioDJ ────────────────────────────────────────")
if song:
    print(f"  Song {SONG_ID} : {song.get('artist')} — {song.get('title')}")
    song_path = song.get('path') or ''
    print(f"  Path RadioDJ : {song_path}")
    if '\\' in song_path:
        basename_radiodj = song_path.rsplit('\\', 1)[-1]
    else:
        basename_radiodj = song_path.rsplit('/', 1)[-1]
    print(f"  Basename (clé de matching worker) : {basename_radiodj}")
else:
    print(f"  Song {SONG_ID} introuvable — la comparaison par basename sera sautée.")

# ─────────────────────────────────────────────────────────────────
print()
print("─── [2/5] Listing des bibliothèques par station ───────────────")
listing = {}
for sid in STATIONS:
    try:
        api = get_api_for_station(sid)
        data = api.list_media_all()
        rows = data if isinstance(data, list) else (data or {}).get('rows', [])
        listing[sid] = rows
        total = len(rows)
        id_presente = any(int(m.get('id', -1)) == MEDIA_ID for m in rows)
        matches_basename = []
        if basename_radiodj:
            for m in rows:
                ap = m.get('path', '') or ''
                b = ap.rsplit('/', 1)[-1] if '/' in ap else ap
                if b.lower().strip() == basename_radiodj.lower().strip():
                    matches_basename.append({'id': m.get('id'), 'path': ap})
        print(f"  Station {sid} : {total} média(s) listé(s) · média {MEDIA_ID} présent : "
              f"{'OUI' if id_presente else 'NON'} · matches basename : "
              f"{[(m['id'], m['path']) for m in matches_basename] or 'aucun'}")
    except Exception as e:
        listing[sid] = []
        print(f"  Station {sid} : ERREUR listing : {e}")

if listing.get(6) and listing.get(7):
    ids6 = {int(m.get('id', -1)) for m in listing[6]}
    ids7 = {int(m.get('id', -1)) for m in listing[7]}
    croisement = ids6 & ids7
    print(f"  → IDs présents dans les DEUX listes : {len(croisement)} "
          f"({'exemples: ' + str(sorted(list(croisement))[:8]) if croisement else 'aucun'})")
    if croisement:
        print("  ⚠ CONSTAT : le listing GET /station/{id}/files renvoie des médias hors station "
              "(bibliothèques croisées) — la résolution song→media peut trouver un média de l'autre station.")
    else:
        print("  ✓ CONSTAT : les listings sont bien séparés par station.")

# ─────────────────────────────────────────────────────────────────
print()
print("─── [3/5] GET /station/{s}/file/13650 (lecture cross-station) ─")
get_media_par_station = {}
for sid in STATIONS:
    try:
        api = get_api_for_station(sid)
        media = api.get_media(MEDIA_ID)
        pls = media.get('playlists', [])
        ids = [p.get('id') if isinstance(p, dict) else p for p in pls]
        path_media = media.get('path', '?')
        get_media_par_station[sid] = ids
        print(f"  Station {sid} : média {MEDIA_ID} (path={path_media}) → playlists {ids}")
    except AzuraCastAPIError as e:
        get_media_par_station[sid] = None
        print(f"  Station {sid} : erreur API (média peut-être absent de cette station) : {e}")
    except Exception as e:
        get_media_par_station[sid] = None
        print(f"  Station {sid} : ERREUR : {type(e).__name__}: {e}")

if 6 in get_media_par_station and 7 in get_media_par_station \
        and get_media_par_station[6] is not None and get_media_par_station[7] is not None:
    if get_media_par_station[6] == get_media_par_station[7]:
        print("  ⚠ CONSTAT : GET /file/{id} répond IDENTIQUEMENT depuis les deux stations "
              "→ l'API est permissive en lecture cross-station.")
    else:
        print("  ✓ CONSTAT : les réponses diffèrent selon la station (filtrage effectif).")

# ─────────────────────────────────────────────────────────────────
print()
print("─── [4/5] Station réelle des playlists 465 / 438 / 437 / 440 ──")
playlists_par_station = {}
for sid in STATIONS:
    try:
        api = get_api_for_station(sid)
        pls = api.list_playlists() or []
        playlists_par_station[sid] = {int(p.get('id')): p for p in pls if p.get('id') is not None}
        print(f"  Station {sid} : {len(playlists_par_station[sid])} playlist(s) déclarée(s).")
    except Exception as e:
        playlists_par_station[sid] = {}
        print(f"  Station {sid} : ERREUR list_playlists : {e}")

print()
for pid in PLAYLISTS_A_LOCALISER:
    localisations = []
    for sid, d in playlists_par_station.items():
        if pid in d:
            p = d[pid]
            localisations.append(f"S{sid} « {p.get('name')} » (folder={p.get('folder')}, type={p.get('type')})")
    if localisations:
        print(f"  Playlist {pid} : appartient à {' ; '.join(localisations)}")
    else:
        print(f"  Playlist {pid} : introuvable dans les playlists des stations {STATIONS}")

# ─────────────────────────────────────────────────────────────────
print()
print("─── [5/5] Cache SQL azuracast_playlists_cache ─────────────────")
try:
    rows = mysql_query(
        "SELECT COUNT(*) AS n FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = 'azuracast_playlists_cache' "
        "AND column_name = 'station_id'"
    )
    has_col = rows and rows[0]['n'] > 0
    print(f"  Colonne station_id présente : {'OUI' if has_col else 'NON'}")
    if has_col:
        ph = ', '.join(['%s'] * len(PLAYLISTS_A_LOCALISER))
        cache_rows = mysql_query(
            f"SELECT azura_id, name, station_id FROM azuracast_playlists_cache "
            f"WHERE azura_id IN ({ph}) ORDER BY azura_id", tuple(PLAYLISTS_A_LOCALISER))
        for r in cache_rows:
            print(f"    azura_id {r['azura_id']} : « {r['name']} » → cache station_id={r['station_id']}")
        if not cache_rows:
            print("    (aucune de ces playlists dans le cache — sync playlists à relancer)")
    else:
        print("  ⚠ Sans colonne station_id, /api/azuracast/playlists renvoie TOUTES les playlists "
              "(toutes stations confondues) — impacte aussi l'onglet « Ajouter PLS ».")
except Exception as e:
    print(f"  ERREUR lecture cache : {e}")

# ─────────────────────────────────────────────────────────────────
print()
print("═" * 66)
print("BILAN — copiez cette sortie entière dans la conversation.")
print("═" * 66)
