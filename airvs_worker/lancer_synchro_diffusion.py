#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
# lancer_synchro_diffusion.py — lanceur MANUEL de SYNCHRO_DIFFUSION (D4-a)
#
# (Ex-lancer_synchro_premier_run.py, renommé le 23/11/2026 — créé pour le
#  1er run du lancement, c'est désormais le lanceur standard du quotidien :
#  dry-run de contrôle, run réel, suivi --statut.)
#
# Insère la tâche dans taches_planifiees ; le worker Ubuntu de pige (.17) la
# prend en charge dans les secondes qui suivent. C'est le chemin standard du
# worker : même traitement que le cron quotidien, mêmes journaux, même sécurité.
#
# Dry-run de contrôle (recommandé de temps en temps) :
#   python3 lancer_synchro_diffusion.py --dbconfig db_config.json --dry-run
#   → calcule tout (résolution, écritures prévues, échecs) mais N'ÉCRIT RIEN
#     (ni radiodb.songs, ni tables d'état).
#
# Le run réel crée automatiquement le snapshot bak_songs_counters_<date>
# AVANT la première écriture (double sécurité avec snapshot_compteurs_T2.py).
#
# Le mot de passe MySQL n'est JAMAIS affiché.
#
# Usage — sur pige (.17, accès MySQL 192.168.1.30) :
#   python3 lancer_synchro_diffusion.py --dbconfig db_config.json            # run réel
#   python3 lancer_synchro_diffusion.py --dbconfig db_config.json --dry-run  # simulation
#   python3 lancer_synchro_diffusion.py --statut                             # suivi tâche
#
# Codes de sortie : 0 ok · 2 dbconfig absent · 8 MySQL indisponible ·
#                   9 tâche SYNCHRO_DIFFUSION déjà en attente/en cours
# ============================================================================
import argparse
import hashlib
import json
import os
import sys
import datetime

DB_DEFAUTS = {
    "host": "192.168.1.30",
    "port": 3306,
    "user": "airvs_user",
    "database": "airvs_dashboard",
    "charset": "utf8mb4",
}


def charger_dbconfig(chemin):
    with open(chemin, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for k, v in DB_DEFAUTS.items():
        cfg.setdefault(k, v)
    if not cfg.get("password"):
        sys.exit("✗ mot de passe absent de %s" % chemin)
    return cfg


def main():
    ap = argparse.ArgumentParser(
        description="Lanceur manuel SYNCHRO_DIFFUSION (dry-run, run réel, suivi)")
    ap.add_argument("--dbconfig", default="db_config.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="simulation complète, aucune écriture (recommandé d'abord)")
    ap.add_argument("--stations", default="6,7",
                    help="stations traitées, défaut « 6,7 »")
    ap.add_argument("--statut", action="store_true",
                    help="affiche l'état des dernières tâches SYNCHRO_DIFFUSION")
    args = ap.parse_args()

    if not os.path.isfile(args.dbconfig):
        sys.exit("✗ dbconfig introuvable : %s (créer avec creer_db_config.py)" % args.dbconfig)
    cfg = charger_dbconfig(args.dbconfig)

    try:
        import pymysql
    except ImportError:
        sys.exit("✗ pymysql absent — pip3 install pymysql")

    try:
        db = pymysql.connect(**cfg)
    except Exception as e:
        print("✗ Connexion MySQL impossible : %s" % e)
        sys.exit(8)
    cur = db.cursor(pymysql.cursors.DictCursor)

    if args.statut:
        cur.execute(
            "SELECT id, statut, date_creation, date_fin, "
            "LEFT(log_resultat, 12000) AS extrait_log "
            "FROM taches_planifiees WHERE type_action = 'SYNCHRO_DIFFUSION' "
            "ORDER BY id DESC LIMIT 5")
        for t in cur.fetchall():
            print("─" * 72)
            print("tâche #%s — %s — créée %s — fin %s" %
                  (t["id"], t["statut"], t["date_creation"], t["date_fin"]))
            if t["extrait_log"]:
                print(t["extrait_log"].replace("\n", "\n  "))
        cur.close()
        db.close()
        return

    cur.execute(
        "SELECT id, statut FROM taches_planifiees "
        "WHERE type_action = 'SYNCHRO_DIFFUSION' "
        "AND statut IN ('en_attente', 'en_cours') LIMIT 1")
    deja = cur.fetchone()
    if deja:
        print("✗ Une tâche SYNCHRO_DIFFUSION #%s est déjà %s — attendre sa fin"
              % (deja["id"], deja["statut"]))
        sys.exit(9)

    stations = []
    for s in args.stations.split(","):
        s = s.strip()
        if s:
            stations.append(int(s))

    params = json.dumps({
        "station_ids": stations,
        "auto_scheduled": False,
        "trigger_source": "manuel:lancer_synchro_diffusion",
        "dry_run": bool(args.dry_run),
    })
    mode = "SIMULATION (dry-run, aucune écriture)" if args.dry_run else "RUN RÉEL"
    cur.execute(
        "INSERT INTO taches_planifiees (type_action, statut, parametres, date_creation) "
        "VALUES ('SYNCHRO_DIFFUSION', 'en_attente', %s, NOW())", (params,))
    task_id = cur.lastrowid
    db.commit()
    cur.close()
    db.close()

    print("")
    print("Tâche SYNCHRO_DIFFUSION #%s insérée — %s" % (task_id, mode))
    print("  stations : %s" % stations)
    if not args.dry_run:
        print("  sécurité : snapshot bak_songs_counters_<date> créé automatiquement")
        print("             avant la première écriture")
    print("")
    print("Suivi :")
    print("  python3 %s --dbconfig %s --statut" % (os.path.basename(sys.argv[0]), args.dbconfig))
    print("  (ou via le dashboard : tâche #%s, log_resultat)" % task_id)


if __name__ == "__main__":
    main()
