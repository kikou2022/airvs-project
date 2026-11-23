#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ════════════════════════════════════════════════════════════════════════════
#  patch_d4a_bis_cron_demarrage.py — D4-a bis (23/11/2026) — révision B
# ════════════════════════════════════════════════════════════════════════════
#  Adapte le cron quotidien SYNCHRO_DIFFUSION aux machines du studio éteintes
#  la nuit, allumées à heure variable (décision exploitant — option C) :
#
#  worker_ubuntu.py :
#    1. fenêtre « hour == H » → « hour >= H » : si le worker démarre après
#       l'heure configurée, le cycle part au premier passage suivant
#       (rattrapage au démarrage, sous 5 minutes — GF1 inchangé) ;
#    2. fix du garde « une seule exécution AUTO par jour » : filtre sur
#       trigger_source cron:quotidien — les tâches manuelles, qui portent
#       aussi la clé auto_scheduled dans leurs parametres, bloquaient à
#       tort le cycle auto du jour ;
#    3. docstring + message de log actualisés ;
#    4. WORKER_VERSION → 2026.11.23-A.
#
#  airvs_dashboard_v2/config.py :
#    WORKER_VERSION_ATTENDUE → 2026.11.23-A (badge de la page tâches).
#
#  Révision B (23/11/2026, après 1er passage sur pige) :
#    - ancrage config.py recalé sur la ligne réelle du dépôt sur pige
#      (commentaire « + Phase 21.3 … C : hotfix cleanup orphelines ») :
#      seul le numéro de version par défaut est remplacé (ancrage ASCII),
#      le commentaire d'historique est mis à jour à part (optionnel) ;
#    - IDEMPOTENT : un remplacement dont le texte cible est déjà présent
#      est ignoré (« = déjà appliqué ») → le script peut être relancé
#      sans risque après un abandon en cours d'exécution ;
#    - contrôle de syntaxe par compile() SANS écriture de .pyc
#      (__pycache__ appartient à l'utilisateur du service sur pige).
#
#  Sécurité : chaque remplacement est ancré sur un texte EXACT, compté
#  (0 ou plusieurs occurrences → abandon SANS écriture, sauf ancrages
#  « (optionnel) » ignorés) ; sauvegarde .bak.d4abis.<horodatage> avant
#  écriture (masque *.bak.* déjà ignoré par git) ; syntaxe des deux
#  fichiers contrôlée avant/après patch, restauration automatique si échec.
#
#  Usage (sur pige, depuis la racine du dépôt) :
#    python3 airvs_worker/scripts_db/patch_d4a_bis_cron_demarrage.py
#  Ou avec chemins explicites :
#    python3 patch_d4a_bis_cron_demarrage.py [worker_ubuntu.py] [config.py]
# ════════════════════════════════════════════════════════════════════════════
import os
import shutil
import sys
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# worker_ubuntu.py — 8 remplacements
# ─────────────────────────────────────────────────────────────────────────────
R_WORKER = [
    (
        '''    """T2 — D4-a : insère une tâche SYNCHRO_DIFFUSION une fois par jour à
    l'heure configurée (config.json automation :''',
        '''    """T2 — D4-a bis : insère une tâche SYNCHRO_DIFFUSION une fois par jour,
    au premier passage à partir de l'heure configurée (config.json automation :''',
        "docstring 1/3 (titre + sémantique)",
    ),
    (
        '''      synchro_diffusion_heure  : heure locale de déclenchement, défaut 4).''',
        '''      synchro_diffusion_heure  : heure locale à partir de laquelle le cycle
                                 peut s'exécuter, défaut 4).''',
        "docstring 2/3 (rôle de l'heure configurée)",
    ),
    (
        '''    Déclenché hors plage si l'heure n'est pas atteinte ; une seule tâche auto
    par jour (marqueur auto_scheduled + comparaison à minuit local)."""''',
        '''    Machines studio éteintes la nuit, allumage à heure variable : si le
    worker démarre après l'heure configurée, le cycle part au premier
    passage suivant (rattrapage au démarrage, sous 5 minutes) — plus de
    fenêtre horaire stricte à rater. Une seule tâche auto par jour ; les
    manuelles ne comptent pas (filtre sur trigger_source cron:quotidien)."""''',
        "docstring 3/3 (rattrapage + garde journalier)",
    ),
    (
        '''        if now_local.hour != int(cfg_auto.get('synchro_diffusion_heure', 4)):
            return''',
        '''        # D4-a bis : « >= » et non « == » — si le worker démarre après
        # l'heure planifiée, le cycle part au premier passage (rattrapage).
        if now_local.hour < int(cfg_auto.get('synchro_diffusion_heure', 4)):
            return''',
        "fenêtre horaire stricte → plancher (rattrapage au démarrage)",
    ),
    (
        '''            "WHERE type_action = 'SYNCHRO_DIFFUSION' "
            "AND parametres LIKE '%auto_scheduled%' "''',
        '''            "WHERE type_action = 'SYNCHRO_DIFFUSION' "
            "AND parametres LIKE '%cron:quotidien%' "''',
        "garde journalier : seules les tâches AUTO comptent",
    ),
    (
        '''        print(f"[SYNCHRO AUTO] Tâche SYNCHRO_DIFFUSION #{task_id} planifiée "
              f"(cron quotidien {cfg_auto.get('synchro_diffusion_heure')}h)")''',
        '''        print(f"[SYNCHRO AUTO] Tâche SYNCHRO_DIFFUSION #{task_id} planifiée "
              f"(cycle quotidien, à partir de {cfg_auto.get('synchro_diffusion_heure')}h)")''',
        "message de log actualisé",
    ),
    (
        '''WORKER_VERSION = "2026.11.22-A"''',
        '''WORKER_VERSION = "2026.11.23-A"''',
        "WORKER_VERSION → 2026.11.23-A",
    ),
    (
        '''# + T2 SYNCHRO_DIFFUSION : compteurs RadioDJ depuis''',
        '''# + D4-a bis : cycle quotidien SYNCHRO_DIFFUSION avec rattrapage au démarrage (machines studio éteintes la nuit) + fix garde journalier (manuelles exclues via trigger_source). Historique : + T2 SYNCHRO_DIFFUSION : compteurs RadioDJ depuis''',
        "commentaire de version (historique D4-a bis en tête)",
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# airvs_dashboard_v2/config.py — 2 remplacements
# ─────────────────────────────────────────────────────────────────────────────
R_DASHBOARD = [
    (
        '''os.getenv('WORKER_VERSION_ATTENDUE', '2026.11.22-A')''',
        '''os.getenv('WORKER_VERSION_ATTENDUE', '2026.11.23-A')''',
        "WORKER_VERSION_ATTENDUE → 2026.11.23-A (défaut)",
    ),
    (
        '''# + Phase 21.3 : rotation éditoriale (mapping + preview/execute) + regen miroir. C : hotfix cleanup orphelines''',
        '''# D4-a bis : cycle quotidien SYNCHRO_DIFFUSION avec rattrapage au démarrage (machines studio éteintes la nuit). Historique : 2026.11.22-A (T2 SYNCHRO_DIFFUSION), + Phase 21.3 : rotation éditoriale (mapping + preview/execute) + regen miroir, C : hotfix cleanup orphelines''',
        "(optionnel) commentaire d'historique de la ligne WORKER_VERSION_ATTENDUE",
    ),
]


def resoudre(candidats, etiquette):
    for c in candidats:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return None


def verifier_syntaxe(chemin):
    """compile() sans écriture de .pyc — __pycache__ peut appartenir au
    compte du service (Permission denied constatée sur pige)."""
    with open(chemin, "r", encoding="utf-8") as f:
        compile(f.read(), chemin, "exec")


def appliquer(chemin, remplacements, etiquette):
    """Applique les remplacements, en ignorant ceux déjà appliqués.
    Retourne (chemin_sauvegarde_ou_None, nombre_appliqué)."""
    with open(chemin, "r", encoding="utf-8") as f:
        texte = f.read()
    a_appliquer = []
    for old, new, desc in remplacements:
        if new in texte:
            print("  = %s — déjà appliqué, ignoré : %s" % (etiquette, desc))
            continue
        n = texte.count(old)
        if n == 1:
            a_appliquer.append((old, new))
        elif desc.startswith("(optionnel)"):
            print("  ! %s — ancrage optionnel absent ou ambigu (%d), ignoré : %s"
                  % (etiquette, n, desc))
        else:
            sys.exit("✗ %s — ancrage trouvé %d fois (attendu : 1) : %s\n"
                     "  → ABANDON, fichier NON modifié." % (etiquette, n, desc))
    if not a_appliquer:
        return None, 0
    horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = "%s.bak.d4abis.%s" % (chemin, horodatage)
    shutil.copy2(chemin, bak)
    for old, new in a_appliquer:
        texte = texte.replace(old, new)
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(texte)
    return bak, len(a_appliquer)


def main():
    dossier_script = os.path.dirname(os.path.abspath(__file__))
    worker = resoudre(
        [sys.argv[1] if len(sys.argv) > 1 else None,
         os.path.join(dossier_script, "worker_ubuntu.py"),
         os.path.join(dossier_script, "..", "worker_ubuntu.py")],
        "worker_ubuntu.py")
    config_dash = resoudre(
        [sys.argv[2] if len(sys.argv) > 2 else None,
         os.path.join(dossier_script, "..", "airvs_dashboard_v2", "config.py"),
         os.path.join(dossier_script, "..", "..", "airvs_dashboard_v2", "config.py")],
        "config.py (dashboard)")

    if not worker:
        sys.exit("✗ worker_ubuntu.py introuvable — passer son chemin en argument 1.")
    print("Cible worker   : %s" % worker)
    if config_dash:
        print("Cible dashboard: %s" % config_dash)
    else:
        print("⚠ config.py du dashboard introuvable — badge à mettre à jour à la main.")

    # Contrôle préalable : le worker doit être syntaxiquement valide AVANT
    # toute écriture (couvre une exécution précédente interrompue).
    try:
        verifier_syntaxe(worker)
    except Exception as e:
        sys.exit("✗ worker_ubuntu.py est syntaxiquement INVALIDE avant patch —\n"
                 "  restaurer un .bak.d4abis.* puis relancer :\n  %s" % e)

    bak_worker, n_worker = appliquer(worker, R_WORKER, "worker_ubuntu.py")
    bak_config, n_config = (appliquer(config_dash, R_DASHBOARD, "config.py")
                            if config_dash else (None, 0))

    # Contrôle de syntaxe après écriture ; restauration si échec
    try:
        verifier_syntaxe(worker)
        if config_dash:
            verifier_syntaxe(config_dash)
    except Exception as e:
        if bak_worker:
            shutil.copy2(bak_worker, worker)
        if bak_config:
            shutil.copy2(bak_config, config_dash)
        sys.exit("✗ Échec contrôle de syntaxe — fichiers RESTAURÉS depuis les .bak :\n  %s" % e)

    # Vérifications post-patch (contenu)
    with open(worker, "r", encoding="utf-8") as f:
        wt = f.read()
    ok = ('WORKER_VERSION = "2026.11.23-A"' in wt
          and "now_local.hour < int(cfg_auto.get('synchro_diffusion_heure', 4)):" in wt
          and wt.count("LIKE '%cron:quotidien%'") == 1)
    if not ok:
        if bak_worker:
            shutil.copy2(bak_worker, worker)
        sys.exit("✗ Vérification post-patch échouée — worker_ubuntu.py RESTAURÉ.")

    print("")
    print("PATCH APPLIQUÉ — D4-a bis (2026.11.23-A)")
    if n_worker:
        print("  worker_ubuntu.py : %d remplacement(s) — sauvegarde : %s" % (n_worker, bak_worker))
    else:
        print("  worker_ubuntu.py : déjà conforme (aucun changement écrit)")
    if config_dash:
        if n_config:
            print("  config.py        : %d remplacement(s) — sauvegarde : %s" % (n_config, bak_config))
        else:
            print("  config.py        : déjà conforme (aucun changement écrit)")
    print("")
    print("Prochaines étapes :")
    print("  1. Redémarrer le service worker sur pige, puis vérifier au démarrage :")
    print("     « Worker Ubuntu démarré (version 2026.11.23-A) »")
    print("  2. Commit (depuis la racine du dépôt) :")
    print("     git add airvs_worker/worker_ubuntu.py airvs_dashboard_v2/config.py \\")
    print("             airvs_worker/scripts_db/patch_d4a_bis_cron_demarrage.py")
    print('     git commit -m "feat(worker): D4-a bis - rattrapage au demarrage du cycle quotidien SYNCHRO_DIFFUSION + fix garde journalier"')
    print("  3. Activer l'interrupteur dans airvs_worker/config.json :")
    print('     "synchro_diffusion_actif": false → true')
    print("     (le cycle partira sous 5 min si les machines sont allumées)")
    print("  4. Badge dashboard jaune jusqu'au redéploiement Windows (config.py déjà à jour dans le dépôt).")


if __name__ == "__main__":
    main()
