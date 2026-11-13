#!/usr/bin/env python3
# ════════════════════════════════════════════════════════════════════════════
# patch_config_automation.py — Phase 21.1 / GF7
# ════════════════════════════════════════════════════════════════════════════
# Ajoute la section "automation" au config.json du worker Ubuntu (.17),
# SANS toucher aux autres sections (sheets, azuracast, azuracast2…).
#
# NOTE : cette section est OPTIONNELLE — le worker 2026.11.12-A retombe sur
# les mêmes valeurs par défaut (8h-20h, 60 min) si elle est absente. Ce
# script sert à rendre la configuration explicite et ajustable.
#
# Usage (sur .17, dans le dossier du worker) :
#   python3 patch_config_automation.py            # dossier courant
#   python3 patch_config_automation.py /chemin/config.json
#
# Le fichier original est sauvegardé en config.json.bak_gf7_<horodatage>.
# ════════════════════════════════════════════════════════════════════════════
import json
import os
import shutil
import sys
from datetime import datetime

SECTION_DEFAUT = {
    "automation": {
        "sync_azura_heure_debut": 8,      # Début plage auto-sync (heure locale)
        "sync_azura_heure_fin": 20,       # Fin plage auto-sync (exclusive)
        "sync_azura_intervalle_min": 60,  # Intervalle entre 2 syncs auto
        # GF1 : types traités en PRIORITÉ BASSE par le SELECT du worker
        # (crons lourds / analytics — les interactifs passent avant).
        # Laisser vide [] pour revenir au comportement historique (id ASC pur).
        "priorite_fond": [
            "SYNC_AZURA_STATUS", "SYNC_PLAYLISTS_CACHE", "AUTO_CHECK_SHEETS",
            "AUDIENCE_GET_DATA", "PIGE_LIST", "PIGE_EXTRACT",
            "PODCASTS_LIST", "PODCASTS_EPISODES"
        ]
    }
}


def main():
    # Cible : argument OU config.json du dossier courant
    if len(sys.argv) > 1:
        cible = sys.argv[1]
    else:
        cible = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if not os.path.exists(cible):
        print(f"ERREUR : {cible} introuvable")
        sys.exit(1)

    with open(cible, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    if "automation" in cfg:
        deja = cfg["automation"]
        manquantes = [k for k in SECTION_DEFAUT["automation"] if k not in deja]
        if not manquantes:
            print("Section 'automation' déjà complète — rien à faire.")
            return
        # Compléter uniquement les clés manquantes
        for k in manquantes:
            deja[k] = SECTION_DEFAUT["automation"][k]
        action = f"clés ajoutées : {', '.join(manquantes)}"
    else:
        cfg["automation"] = SECTION_DEFAUT["automation"]
        action = "section ajoutée"

    # Sauvegarde horodatée avant écriture
    horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = f"{cible}.bak_gf7_{horodatage}"
    shutil.copy2(cible, bak)

    with open(cible, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    print(f"OK : {action} dans {cible}")
    print(f"Sauvegarde : {bak}")
    print("Redémarrer le service worker pour prendre en compte la config.")


if __name__ == "__main__":
    main()
