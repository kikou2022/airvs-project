#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
# verifier_synchro_T2.py — vérification du bon fonctionnement de SYNCHRO_DIFFUSION
# (T2 — compteurs RadioDJ réécrits depuis l'historique AzuraCast, D4-a bis)
#
# Outil de CONTRÔLE en lecture seule (aucune écriture MySQL, aucune écriture
# de fichier hors /tmp) : il vérifie l'état réel du cycle sur pige (.17) et
# affiche un rapport section par section avec un verdict final.
#
# Contrôles effectués :
#   1. Version du worker en service (airvs_worker_info) vs version attendue
#   2. Interrupteur cron (config.json automation.synchro_diffusion_actif/heure)
#   3. Tables d'état : synchro_diffusion_cursors (dernier_vu, premier_run)
#   4. synchro_diffusion_compteurs : volume + cohérence interne
#      (radiodj_snapshot = azura_count + delta_cumul, sauf titres disparus)
#   5. Cohérence radiodb.songs vs état (échantillon aléatoire) :
#      écarts positifs = RadioDJ a joué depuis le dernier cycle (normal,
#      absorbé au cycle suivant) ; écarts négatifs = correction manuelle
#      (réécrite au cycle suivant) ; 0 = conforme
#   6. Derniers cycles dans journal_operations_azura (type_op='synchro_diffusion')
#      : triggered_by, dry_run, lignes écrites, taux de résolution (~89-90 %)
#        attendus — spécification RESULTAT_SONDES_T1 §6)
#   7. ENUM type_op élargi (contient 'synchro_diffusion')
#   8. Tâches taches_planifiees SYNCHRO_DIFFUSION (statut, trigger_source)
#   9. Snapshot(s) bak_songs_counters_<date> présents
#  10. Échantillon d'échecs du dernier cycle (vivier campagne manuelle P8b/P7)
#  11. --test-api : authentification AzuraCast (code HTTP seulement, la clé
#      n'est JAMAIS affichée)
#
# Usage — sur pige (.17, accès MySQL 192.168.1.30, dans le dossier du worker) :
#   python3 verifier_synchro_T2.py --dbconfig db_config.json
#   python3 verifier_synchro_T2.py --dbconfig db_config.json --test-api
#   python3 verifier_synchro_T2.py --dbconfig db_config.json --echantillon 500
#   python3 verifier_synchro_T2.py --dbconfig db_config.json --json   # dump rapport
#
# Codes de sortie : 0 = synchro opérationnelle · 1 = manques/anomalies ·
#                   2 = dbconfig absent · 8 = MySQL indisponible
# ============================================================================
import argparse
import datetime
import json
import os
import sys

DB_DEFAUTS = {
    "host": "192.168.1.30",
    "port": 3306,
    "user": "airvs_user",
    "database": "airvs_dashboard",
    "charset": "utf8mb4",
}

STATIONS_DEFAUT = (6, 7)


def charger_dbconfig(chemin):
    with open(chemin, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for k, v in DB_DEFAUTS.items():
        cfg.setdefault(k, v)
    if not cfg.get("password"):
        sys.exit("✗ mot de passe absent de %s" % chemin)
    return cfg


def epoch_vers_local(ts):
    """epoch Unix → chaîne lisible Europe/Paris (conversion hors ligne du
    serveur — DATETIME .30 est en heure locale, affichage indicatif)."""
    if not ts:
        return "-"
    try:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("Europe/Paris")
        except Exception:
            tz = None
        if tz is not None:
            d = datetime.datetime.fromtimestamp(int(ts), tz).replace(tzinfo=None)
        else:
            d = datetime.datetime.fromtimestamp(int(ts))
        return d.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


class Rapport:
    """Rapport console + résumé machine (verdicts par contrôle)."""

    def __init__(self):
        self.sections = []          # (titre, verdict, details)

    def section(self, titre, verdict, details=None):
        """verdict : 'ok' | 'warn' | 'ko' | 'info'"""
        self.sections.append((titre, verdict, details or ""))
        puce = {"ok": "✓", "warn": "⚠", "ko": "✗", "info": "·"}[verdict]
        print("\n" + "─" * 74)
        print("%s %s" % (puce, titre))
        if details:
            print(details)

    def verdict_final(self):
        print("\n" + "═" * 74)
        kos = [t for t, v, _ in self.sections if v == "ko"]
        warns = [t for t, v, _ in self.sections if v == "warn"]
        if kos:
            print("VERDICT : ✗ SYNCHRO NON OPÉRATIONNELLE — %d contrôle(s) en échec"
                  % len(kos))
            for t in kos:
                print("  ✗ " + t)
            return 1
        if warns:
            print("VERDICT : ⚠ Opérationnelle avec points d'attention (%d)" % len(warns))
            for t in warns:
                print("  ⚠ " + t)
            return 0
        print("VERDICT : ✓ SYNCHRO OPÉRATIONNELLE — tous les contrôles passent")
        return 0

    def to_dict(self):
        return [{"controle": t, "verdict": v, "details": d}
                for t, v, d in self.sections]


def _verifier_worker(cur, r, version_attendue):
    try:
        cur.execute("SELECT version, demarrage_le, hostname "
                    "FROM airvs_worker_info WHERE id = 1")
        row = cur.fetchone()
        if not row:
            r.section("Version worker (airvs_worker_info)", "warn",
                      "Table absente ou vide — le worker n'a pas encore "
                      "démarré avec l'enregistrement de version.")
            return
        v = (row["version"] or "").strip()
        det = ("version en service : %s\n  démarré le : %s (%s)\n"
               "  version attendue : %s" %
               (v, row["demarrage_le"], row.get("hostname") or "-",
                version_attendue))
        if v == version_attendue:
            r.section("Version worker (airvs_worker_info)", "ok", det)
        else:
            r.section("Version worker (airvs_worker_info)", "warn",
                      det + "\n  → redémarrer le service airvs-worker pour "
                            "charger la version attendue.")
    except Exception as e:
        r.section("Version worker (airvs_worker_info)", "warn",
                  "Contrôle impossible : %s" % e)


def _verifier_interrupteur(chemin_config, r):
    try:
        with open(chemin_config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        auto = cfg.get("automation") or {}
        actif = auto.get("synchro_diffusion_actif")
        heure = auto.get("synchro_diffusion_heure")
        det = ("synchro_diffusion_actif : %s\n"
               "synchro_diffusion_heure : %s\n(lecture de %s)" %
               (actif, heure, chemin_config))
        if actif is True:
            r.section("Interrupteur cron quotidien (config.json)", "ok", det)
        elif actif is False:
            r.section("Interrupteur cron quotidien (config.json)", "warn",
                      det + "\n  → mettre true pour la mise en service "
                            "(le cycle partira sous 5 min si l'heure est passée).")
        else:
            r.section("Interrupteur cron quotidien (config.json)", "warn",
                      det + "\n  → clé absente : défaut worker = False (D4-a). "
                            "Ajouter la clé dans la section automation.")
    except Exception as e:
        r.section("Interrupteur cron quotidien (config.json)", "warn",
                  "Lecture impossible de %s : %s" % (chemin_config, e))


def _verifier_cursors(cur, r):
    try:
        cur.execute("SELECT station_id, dernier_vu, premier_run, "
                    "date_dernier_cycle FROM synchro_diffusion_cursors "
                    "ORDER BY station_id")
        rows = cur.fetchall()
    except Exception as e:
        r.section("Tables d'état (synchro_diffusion_cursors)", "ko",
                  "Table absente/illisible : %s\n  → aucun cycle n'a encore "
                  "tourné ou schéma non créé (le worker la crée au démarrage)." % e)
        return
    if not rows:
        r.section("Tables d'état (synchro_diffusion_cursors)", "ko",
                  "Aucune ligne — aucun cycle SYNCHRO_DIFFUSION n'a encore "
                  "tourné (pas même en dry-run).")
        return
    det = []
    premier_run_restant = False
    for row in rows:
        sid = row["station_id"]
        pr = bool(int(row["premier_run"] or 0))
        premier_run_restant = premier_run_restant or pr
        det.append("  station %s : dernier_vu = %s (%s) · premier_run = %s · "
                   "dernier cycle = %s" %
                   (sid, row["dernier_vu"],
                    epoch_vers_local(row["dernier_vu"]),
                    1 if pr else 0, row["date_dernier_cycle"] or "-"))
    if premier_run_restant:
        r.section("Tables d'état (synchro_diffusion_cursors)", "warn",
                  "\n".join(det) +
                  "\n  → premier_run = 1 : le prochain cycle réel "
                    "(re)couvrira la fenêtre complète + snapshot.")
    else:
        r.section("Tables d'état (synchro_diffusion_cursors)", "ok",
                  "\n".join(det))


def _verifier_compteurs(cur, r):
    try:
        cur.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(azura_count),0) AS s_az, "
            "COALESCE(SUM(delta_cumul),0) AS s_d, "
            "COALESCE(SUM(radiodj_snapshot),0) AS s_s, "
            "MAX(updated_at) AS maj "
            "FROM synchro_diffusion_compteurs")
        row = cur.fetchone()
    except Exception as e:
        r.section("État des compteurs (synchro_diffusion_compteurs)", "ko",
                  "Table absente/illisible : %s" % e)
        return
    n = int(row["n"] or 0)
    if n == 0:
        r.section("État des compteurs (synchro_diffusion_compteurs)", "ko",
                  "0 titre suivi — le 1er run réel n'a pas encore été effectué.")
        return
    r.section("État des compteurs (synchro_diffusion_compteurs)", "ok",
              "titres suivis : %s\n  vérité AzuraCast cumulée : %s diffusions\n"
              "  cumul delta RadioDJ (directs) : %s\n"
              "  somme snapshots : %s\n  dernière mise à jour : %s" %
              (n, row["s_az"], row["s_d"], row["s_s"], row["maj"] or "-"))


def _verifier_incoherences(cur, r):
    """radiodj_snapshot doit = azura_count + delta_cumul (finaliser_etat).
    Exceptions attendues : titres disparus de radiodb.songs après leur
    suivi (hors_radiodj)."""
    try:
        cur.execute(
            "SELECT song_id_radiodj, azura_count, delta_cumul, radiodj_snapshot "
            "FROM synchro_diffusion_compteurs "
            "WHERE radiodj_snapshot <> azura_count + delta_cumul LIMIT 200")
        rows = cur.fetchall()
    except Exception as e:
        r.section("Cohérence interne de l'état", "warn", "Contrôle impossible : %s" % e)
        return
    if not rows:
        r.section("Cohérence interne de l'état", "ok",
                  "radiodj_snapshot = azura_count + delta_cumul sur tous les "
                  "titres suivis.")
        return
    det = ["%s lignes d'écart (sur %s max affichés)" % (len(rows), 200)]
    for row in rows[:10]:
        det.append("  sid %s : snapshot %s ≠ azura %s + delta %s" %
                   (row["song_id_radiodj"], row["radiodj_snapshot"],
                    row["azura_count"], row["delta_cumul"]))
    r.section("Cohérence interne de l'état", "warn",
              "\n".join(det) + "\n  → attendu uniquement pour des titres "
              "disparus de radiodb.songs (hors_radiodj) ; vérifier sinon.")


def _verifier_radiodj(cur, r, echantillon):
    """Échantillon aléatoire : count_played vs cible = azura_count+delta_cumul.
    Écart ≥ 0 : RadioDJ a joué depuis le dernier cycle (absorbé au suivant).
    Écart < 0  : correction manuelle du compteur (réécrite au suivant)."""
    try:
        cur.execute(
            "SELECT e.song_id_radiodj AS sid, e.azura_count, e.delta_cumul, "
            "s.count_played "
            "FROM synchro_diffusion_compteurs e "
            "LEFT JOIN radiodb.songs s ON s.ID = e.song_id_radiodj "
            "ORDER BY RAND() LIMIT %s", (int(echantillon),))
        rows = cur.fetchall()
    except Exception as e:
        r.section("Cohérence radiodb.songs (échantillon)", "warn",
                  "Contrôle impossible : %s" % e)
        return
    if not rows:
        r.section("Cohérence radiodb.songs (échantillon)", "ko",
                  "État vide — rien à comparer.")
        return
    conformes, joues, corrections, disparus = 0, 0, 0, 0
    max_pos, max_neg = 0, 0
    for row in rows:
        if row["count_played"] is None:
            disparus += 1
            continue
        ecart = int(row["count_played"]) - (int(row["azura_count"])
                                            + int(row["delta_cumul"]))
        if ecart == 0:
            conformes += 1
        elif ecart > 0:
            joues += 1
            max_pos = max(max_pos, ecart)
        else:
            corrections += 1
            max_neg = min(max_neg, ecart)
    total = len(rows)
    det = ("échantillon aléatoire : %s titre(s) sur l'état\n"
           "  conformes (écart 0)                : %s\n"
           "  RadioDJ a joué depuis (écart +1..+%s) : %s — normal entre "
           "cycles, absorbé au cycle suivant\n"
           "  corrections manuelles (écart %s..-1) : %s — réécrites au "
           "cycle suivant (delta borné à 0)\n"
           "  disparus de radiodb.songs           : %s" %
           (total, conformes, max_pos, joues, max_neg, corrections, disparus))
    if disparus > total * 0.05:
        r.section("Cohérence radiodb.songs (échantillon)", "warn",
                  det + "\n  → plus de 5 %% de titres suivis introuvables dans "
                        "radiodb.songs : vérifier les suppressions RadioDJ.")
    else:
        r.section("Cohérence radiodb.songs (échantillon)", "ok", det)


def _verifier_journal(cur, r):
    try:
        cur.execute(
            "SELECT id, date_creation, triggered_by, trigger_source, task_id, "
            "details FROM journal_operations_azura "
            "WHERE type_op = 'synchro_diffusion' ORDER BY id DESC LIMIT 5")
        rows = cur.fetchall()
    except Exception as e:
        if "Data truncated" in str(e) or "ENUM" in str(e):
            r.section("Journal des cycles (journal_operations_azura)", "ko",
                      "ENUM type_op non élargi (synchro_diffusion refusé) : %s\n"
                      "  → démarrer le worker (élargissement idempotent au "
                      "démarrage) puis relancer un cycle." % e)
            return
        r.section("Journal des cycles (journal_operations_azura)", "ko",
                  "Journal illisible/absent : %s" % e)
        return
    if not rows:
        r.section("Journal des cycles (journal_operations_azura)", "ko",
                  "Aucun cycle journalisé type_op='synchro_diffusion' — "
                  "aucun run réel ni dry-run n'a été lancé.")
        return
    # ligne la plus récente : détail complet
    last = rows[0]
    try:
        det_json = json.loads(last["details"] or "{}")
    except Exception:
        det_json = {}
    stats = det_json.get("stats", det_json)
    sec = stats.get("stats", stats) if isinstance(stats, dict) else {}
    ent = sec.get("entrees_nouvelles")
    res = sec.get("resolues")
    ech = sec.get("echecs_musique")
    nm = sec.get("non_musique_exclue")
    ec = sec.get("lignes_ecrites")
    up = sec.get("updates_prevus")
    dry = sec.get("dry_run")
    statut = sec.get("status")
    taux = (100.0 * res / (res + ech)) if isinstance(res, int) and isinstance(ech, int) and (res + ech) > 0 else None
    det = [
        "dernier cycle journalisé : #%s du %s" % (last["id"], last["date_creation"]),
        "  déclenchement : triggered_by=%s · trigger_source=%s · task_id=%s" %
        (last["triggered_by"], last["trigger_source"] or "-", last["task_id"] or "-"),
        "  statut : %s · dry_run : %s" % (statut or "-", dry),
        "  entrées nouvelles : %s · résolues : %s · échecs : %s · "
        "non-musique exclue : %s" % (ent, res, ech, nm),
        "  taux de résolution musique : %s" %
        ("%.1f %% (attendu ~89-90 %%)" % taux if taux is not None else "n/d"),
        "  écritures prévues : %s · lignes écrites : %s" % (up, ec),
    ]
    if dry:
        det.append("  ⚠ dernier cycle = SIMULATION (dry-run) — aucune écriture")
    if (res or 0) > 0 and taux is not None and taux < 80.0:
        det.append("  ⚠ taux de résolution nettement sous l'attendu — "
                   "vérifier l'état du mapping actif")
    for row in rows[1:5]:
        try:
            j = json.loads(row["details"] or "{}")
            s = j.get("stats", j)
            if isinstance(s, dict) and isinstance(s.get("stats"), dict):
                s = s["stats"]
        except Exception:
            s = {}
        det.append("  cycle #%s du %s — %s%s : écrites %s, échecs %s, dry_run=%s" %
                   (row["id"], row["date_creation"], row["triggered_by"],
                    "/%s" % (row["trigger_source"] or "-"),
                    s.get("lignes_ecrites", "?"), s.get("echecs_musique", "?"),
                    s.get("dry_run", "?")))
    r.section("Journal des cycles (journal_operations_azura)", "ok", "\n".join(det))
    # échantillon d'échecs (vivier campagne manuelle P8b/P7)
    sample = det_json.get("echecs_sample") or []
    nb_ech = sec.get("echecs_musique") or 0
    if nb_ech:
        lignes = ["échecs musicaux du dernier cycle : %s (vivier campagne "
                  "manuelle P8b/P7 à ~2 semaines de T2 réel)" % nb_ech]
        for e in sample[:12]:
            lignes.append("  S%s — %s — %s (@%s)" %
                          (e.get("station"), e.get("artist"),
                           e.get("title"),
                           epoch_vers_local(e.get("played_at"))))
        if len(sample) > 12:
            lignes.append("  … (échantillon limité à 12 affichés)")
        r.section("Vivier campagne manuelle (échecs du dernier cycle)", "info",
                  "\n".join(lignes))


def _verifier_enum(cur, r):
    try:
        cur.execute("SHOW COLUMNS FROM journal_operations_azura LIKE 'type_op'")
        row = cur.fetchone()
        col = (row.get("Type") if row else "") or ""
        if "'synchro_diffusion'" in col:
            r.section("ENUM type_op du journal", "ok",
                      "ENUM élargi — 'synchro_diffusion' accepté.")
        else:
            r.section("ENUM type_op du journal", "ko",
                      "'synchro_diffusion' absent de l'ENUM : %s\n"
                      "  → démarrer le worker (élargissement idempotent) ou "
                      "vérifier les droits MySQL." % col)
    except Exception as e:
        r.section("ENUM type_op du journal", "warn", "Contrôle impossible : %s" % e)


def _verifier_taches(cur, r):
    try:
        cur.execute(
            "SELECT id, statut, date_creation, date_fin, parametres, "
            "LEFT(log_resultat, 400) AS log_head "
            "FROM taches_planifiees WHERE type_action = 'SYNCHRO_DIFFUSION' "
            "ORDER BY id DESC LIMIT 5")
        rows = cur.fetchall()
    except Exception as e:
        r.section("Tâches SYNCHRO_DIFFUSION (taches_planifiees)", "warn",
                  "Contrôle impossible : %s" % e)
        return
    if not rows:
        r.section("Tâches SYNCHRO_DIFFUSION (taches_planifiees)", "warn",
                  "Aucune tâche — ni manuelle ni cron n'a encore été déclenché.")
        return
    det = []
    for t in rows:
        try:
            p = json.loads(t["parametres"] or "{}")
        except Exception:
            p = {}
        det.append("  #%s — %s — créée %s, fin %s — trigger : %s%s" %
                   (t["id"], t["statut"], t["date_creation"],
                    t["date_fin"] or "-",
                    p.get("trigger_source") or p.get("auto_scheduled", "?"),
                    " · dry_run" if p.get("dry_run") else ""))
        head = (t.get("log_head") or "").strip()
        if head:
            det.append("      log : %s…" % head.replace("\n", " ")[:180])
    en_cours = [t for t in rows if t["statut"] in ("en_attente", "en_cours")]
    r.section("Tâches SYNCHRO_DIFFUSION (taches_planifiees)",
              "warn" if en_cours else "ok",
              "\n".join(det) + ("\n  ⚠ une tâche est en file/en cours — attendre "
                                "sa fin avant tout nouveau déclenchement."
                                if en_cours else ""))


def _verifier_snapshot(cur, r):
    try:
        cur.execute("SHOW TABLES LIKE 'bak_songs_counters_%'")
        rows = cur.fetchall()
    except Exception as e:
        r.section("Snapshot(s) rollback (bak_songs_counters_<date>)", "warn",
                  "Contrôle impossible : %s" % e)
        return
    noms = [list(x.values())[0] for x in rows]
    if not noms:
        r.section("Snapshot(s) rollback (bak_songs_counters_<date>)", "warn",
                  "Aucun snapshot — normal seulement si aucun run réel n'a "
                  "encore écrit (le snapshot est créé automatiquement avant "
                  "la première écriture du 1er run).")
        return
    det = []
    for nom in sorted(noms):
        cur.execute("SELECT COUNT(*) AS n FROM %s" % nom)
        det.append("  %s : %s lignes" % (nom, cur.fetchone()["n"]))
    r.section("Snapshot(s) rollback (bak_songs_counters_<date>)", "ok",
              "\n".join(det) + "\n  rollback instantané possible : "
              "UPDATE radiodb.songs s JOIN <snap> b ON s.ID=b.ID SET "
              "s.count_played=b.count_played, s.date_played=b.date_played")


def _test_api(chemin_config, r):
    """Authentification AzuraCast — code HTTP seulement, corps abandonné
    (stream), clé jamais affichée."""
    try:
        import requests
    except ImportError:
        r.section("Authentification AzuraCast (--test-api)", "warn",
                  "module requests absent — contrôle sauté.")
        return
    try:
        with open(chemin_config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        sec = cfg.get("azuracast") or {}
        base = (sec.get("base_url") or "").rstrip("/")
        cle = sec.get("api_key") or ""
        stations = [int(s["id"]) for s in (sec.get("stations") or [])
                    if s.get("id")] or list(STATIONS_DEFAUT)
        if not base or not cle:
            r.section("Authentification AzuraCast (--test-api)", "ko",
                      "section azuracast incomplète dans %s" % chemin_config)
            return
        det, ko = [], False
        for sid in stations:
            try:
                resp = requests.get(
                    "%s/api/station/%d/history" % (base, sid),
                    headers={"Authorization": "Bearer %s" % cle,
                             "Accept": "application/json"},
                    timeout=15, stream=True)
                code = resp.status_code
                resp.close()          # corps abandonné (réponse volumineuse)
                det.append("  station %d : HTTP %s %s" %
                           (sid, code, "OK (auth Bearer acceptée)" if code == 200
                            else "✗ auth/refus"))
                if code != 200:
                    ko = True
            except Exception as e:
                det.append("  station %d : ERREUR %s" % (sid, e))
                ko = True
        r.section("Authentification AzuraCast (--test-api)",
                  "ko" if ko else "ok", "\n".join(det))
    except Exception as e:
        r.section("Authentification AzuraCast (--test-api)", "warn",
                  "Contrôle impossible : %s" % e)


def main():
    ap = argparse.ArgumentParser(
        description="Vérification du bon fonctionnement de SYNCHRO_DIFFUSION "
                    "(lecture seule)")
    ap.add_argument("--dbconfig", default="db_config.json")
    ap.add_argument("--config", default="config.json",
                    help="config.json du worker (interrupteur + clé API)")
    ap.add_argument("--version-attendue", default="2026.11.23-A",
                    help="version worker attendue (défaut 2026.11.23-A)")
    ap.add_argument("--echantillon", type=int, default=300,
                    help="taille de l'échantillon radiodb.songs (défaut 300)")
    ap.add_argument("--test-api", action="store_true",
                    help="test d'authentification AzuraCast (code HTTP seulement)")
    ap.add_argument("--json", action="store_true",
                    help="dump du rapport en JSON dans /tmp")
    args = ap.parse_args()

    if not os.path.isfile(args.dbconfig):
        sys.exit("✗ dbconfig introuvable : %s" % args.dbconfig)
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
    r = Rapport()
    print("╔" + "═" * 72 + "╗")
    print("║  VÉRIFICATION SYNCHRO_DIFFUSION (T2) — lecture seule — %s" %
          datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("╚" + "═" * 72 + "╝")

    _verifier_worker(cur, r, args.version_attendue)
    _verifier_interrupteur(args.config, r)
    _verifier_cursors(cur, r)
    _verifier_compteurs(cur, r)
    _verifier_incoherences(cur, r)
    _verifier_radiodj(cur, r, args.echantillon)
    _verifier_journal(cur, r)
    _verifier_enum(cur, r)
    _verifier_taches(cur, r)
    _verifier_snapshot(cur, r)
    if args.test_api:
        _test_api(args.config, r)

    cur.close()
    db.close()
    code = r.verdict_final()
    if args.json:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        dossier = "/tmp/verifier_synchro_T2_%s" % ts
        os.makedirs(dossier, exist_ok=True)
        chemin = os.path.join(dossier, "rapport.json")
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump({"horodatage": ts,
                       "version_attendue": args.version_attendue,
                       "controles": r.to_dict()}, f,
                      ensure_ascii=False, indent=2, default=str)
        print("\nRapport JSON : %s" % chemin)
    sys.exit(code)


if __name__ == "__main__":
    main()
