# CR COURANT — projet AIRVS

> Fichier roulant : remplacé à chaque étape importante (jamais empilé).
> Historique complet : `git log -p -- comptes_rendus/CR_COURANT.md`.
> **Mis à jour** : 25/11/2026 · **Base git** : `1513a0a` (origin/main) · Rédaction : session agent IA

## Dernière étape — Lot 21.2 + Hotfix 2026.11.25-B LIVRÉS (25/11, en attente d'application)

**Hotfix 2026.11.25-B — CONFIDENTIALITÉ (priorité à l'application)** : des clés API réelles étaient dans le dépôt public.
- Trouvé lors de l'audit 21.2 : clé AzuraCast principale en dur dans `azuracast_api.py` (docstring + fallback `_DEFAULT_API_KEY`) ET clés réelles (azuracast + azuracast2) dans les **deux `config.json` commités** (worker + dashboard). Dépôt lisible anonymement → **rotation des clés recommandée** (AzuraCast → Admin → API Keys, les deux installations) — l'historique git conserve les anciennes valeurs, seule la rotation rend l'exposition sans effet.
- Corrigé : `config.json` commités → gabarits `<MASQUÉ>` (+ `_note`) ; `azuracast_api.py` → `_DEFAULT_API_KEY = None`, résolution ① env `AIRVS_AZURA_API_KEY` (même nom que l'exporteur miroir, lot 2026.11.25-A) → ② config.json **LOCAL** § `azuracast.api_key` → ③ erreur explicite (tâches marquées « erreur », message actionnable) ; gabarit `<MASQUÉ>` refusé (jamais de Bearer `<MASQU…>`).
- ⚠️ Pull particulier : vos config.json LOCAUX (production) diffèrent du dépôt → sauvegarder → `git checkout --` sur les 2 chemins → pull/am → restaurer (procédure exacte dans le LISEZMOI de la livraison).

**Lot 21.2 — opérations retour complémentaires (R2/R4/R5)** — conforme à `ETUDE_API_AZURACAST_RETOUR.md` §2.2/2.4/2.5, principe « RadioDJ = référence ultime ».
- API (`azuracast_api.py`) : `empty_playlist()` (DELETE /playlist/{id}/empty), `move_media_between_playlists()` (PUT média + re-lecture anti-folder-based), `rename_media_file()` (PUT /files/rename).
- Worker : 3 exécuteurs `AZURA_MOVE_MEDIA` / `AZURA_EMPTY_PLAYLIST` / `AZURA_RENAME_MEDIA` — garde station 2026.11.21-A, journal `move`/`empty_playlist`/`rename` (ENUM existant), regen miroir demandée ; **R4 enchaîne un SYNC_AZURA_STATUS** (anti-doublon) pour réaligner les flags des titres de la playlist. `WORKER_VERSION` 2026.11.25-A (WORKER_VERSION_ATTENDUE alignée).
- Dashboard : 3 routes + UI (boutons ↔ Déplacer PLS / 🧹 Vider PLS / 📁 Renommer fichier ; double confirmation sur le vidage ; journal contextuel pattern 21-B).
- Résolution media_id **mutualisée** (`_resoudre_media_id_azura`) : cache mapping 21.3 (confiance ≥ 0.75) d'abord → fallback `list_media_all` — concrétise l'ex-TODO « Phase 6 » (~35 s économisées par résolution quand le cache est peuplé).
- Draineur miroir **FIX backoff** : nouvelle colonne `last_tentative` (posée à chaque tentative) — l'ancien test portait sur `last_regen` (succès seulement) donc le backoff ne s'appliquait jamais après un échec (retry toutes les 60 s) ; garde `en_cours` avec réveil (> 15 min = worker coupé → reprise, plus de blocage définitif) ; défauts host/port alignés sur l'architecture réelle (192.168.1.17:22, export sur pige même) ; docstrings + libellé panneau [MIROIR] corrigés ; ALTER idempotent au boot (MariaDB 10.2+), non bloquant.
- Tests hors-ligne **46/46 PASS** (`tests/test_lot_212.py` livré hors dépôt — FakeDB/FakeSession, aucune infra réelle).

## État Phase 21

- 21.1 FAIT · 21.3 FAIT · 21.4-a APPLIQUÉ et VALIDÉ (observation activable quand voulu) · **21.2 LIVRÉ (25/11)** → reste 21.5 (~4-5 j), 21.6 (~2-3 j). GF3 (indexes) + comptage `IN_AZURACAST` = op DB sur .30/.39 ; GF5 partiel.
- Restes 21.2 reportés (candidats 21.5/21.6) : exporteur encore sur `IN_AZURACAST`, push `AZURA_PUSHED_S6_S7` en dur.

## Sonde doublons (consignée 25/11)

- `airvs_worker/sonde_doublons_v1.py` : fichier **non suivi** sur pige, mis de côté par l'exploitant — servira de point d'appui pour une **campagne de dédoublonnage** (→ lot 21.5, audit doublons). Décision : ni commité ni supprimé pour l'instant.

## Contexte persistant

- T2 SYNCHRO_DIFFUSION : clos et validé (hotfix 2026.11.24-A `d64d1e3`, 10/10 au 23/11 et 24/11, rattrapage 4h00 prouvé).
- Miroir public : lot 2026.11.25-A clôturé (clé API dynamique, Passe 2 HTTP 200, draineur vert au 24/11 18h30).
- Déduplication RadioDJ : mise de côté (décision exploitant) ; détection à l'entrée en base active ; sonde v1 consignée ci-dessus.
- Diagnostic réseau : en pause (consigne) ; SSHFS→SAMBA hors périmètre.
- Versions en production pige (au 24/11) : worker `2026.11.24-B`, dashboard `2026.11.24-B` — le lot 21.2 porte `2026.11.25-A` après application.

## Règles actives

- 9 consignes permanentes + **n°10 opérationnelle** : remplacer ce fichier et pousser (`docs(cr): …`, commit séparé du feat) à chaque étape importante ; `git pull` + lecture du CR avant chaque étape ; l'historique git fait foi.
- **SHA vérifiables** : tout SHA cité dans un document poussé doit exister sur `origin/main` après fetch (`git cat-file -e <sha>^{commit}`) — jamais un SHA local de session non poussée.
- `config.json` de production **jamais commités** — les versions du dépôt sont des gabarits `<MASQUÉ>` depuis 2026.11.25-B ; aucune donnée sensible dans les CR.
- Nom de la machine studio : **pige** (jamais « pigeon »).
