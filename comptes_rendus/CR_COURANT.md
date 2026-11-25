# CR COURANT — projet AIRVS

> Fichier roulant : remplacé à chaque étape importante (jamais empilé).
> Historique complet : `git log -p -- comptes_rendus/CR_COURANT.md`.
> **Mis à jour** : 25/11/2026 · **Base git** : `1513a0a` (origin/main) · Rédaction : session agent IA

## Dernière étape — Épisode clés API CLOS + config.json sortis du suivi git ; Lot 21.2 en attente d'application

**Clés API — CLÔTURE (25/11)** :
- L'exploitant a **rotaté les clés des deux installations AzuraCast (~22/11)**, porté les nouvelles valeurs dans ses config.json LOCAUX (jamais commités) et redémarré les services — tout validé en exploitation depuis (miroir HTTP 200). Les valeurs visibles dans l'historique git public sont donc des **clés mortes** : plus aucun effet de sécurité, épisode clos.
- Cause du blocage d'alors (403) : clé en dur dans `azuracast_api.py` — réglée par la lecture dynamique (lot 2026.11.25-A, poussé) puis le hotfix 2026.11.25-B (plus aucune clé en dur dans le dépôt, gabarits `<MASQUÉ>`).
- **Ce lot** : les deux `config.json` **sortent définitivement du suivi git** (`git rm --cached` + `.gitignore`) — plus aucune config ne peut être commitée par mégarde, et `git status` ne montrera plus les config « modifiés » en permanence. Gabarits sans clé fournis : `config.json.exemple` (worker + dashboard).
- Effacement de l'historique (réécriture + force push) : **option cosmétique**, à la demande de l'exploitant, sans urgence (clés mortes). Conséquences à réserver : tous les SHA changent (mise à jour du CR une fois), re-clonage du dépôt partout, résidus GitHub accessibles par lien direct quelques jours.

**Lot 21.2 — opérations retour complémentaires (R2/R4/R5)** — conforme à `ETUDE_API_AZURACAST_RETOUR.md` §2.2/2.4/2.5, principe « RadioDJ = référence ultime ». **En attente : déploiement/test par l'exploitant, puis application de la série de 7 patchs + push.**
- API (`azuracast_api.py`) : `empty_playlist()` (DELETE /playlist/{id}/empty), `move_media_between_playlists()` (PUT média + re-lecture anti-folder-based), `rename_media_file()` (PUT /files/rename).
- Worker : 3 exécuteurs `AZURA_MOVE_MEDIA` / `AZURA_EMPTY_PLAYLIST` / `AZURA_RENAME_MEDIA` — garde station 2026.11.21-A, journal `move`/`empty_playlist`/`rename` (ENUM existant), regen miroir demandée ; **R4 enchaîne un SYNC_AZURA_STATUS** (anti-doublon) pour réaligner les flags des titres de la playlist. `WORKER_VERSION` 2026.11.25-A (WORKER_VERSION_ATTENDUE alignée).
- Dashboard : 3 routes + UI (boutons ↔ Déplacer PLS / 🧹 Vider PLS / 📁 Renommer fichier ; double confirmation sur le vidage ; journal contextuel pattern 21-B).
- Résolution media_id **mutualisée** (`_resoudre_media_id_azura`) : cache mapping 21.3 (confiance ≥ 0.75) d'abord → fallback `list_media_all` — concrétise l'ex-TODO « Phase 6 » (~35 s économisées par résolution quand le cache est peuplé).
- Draineur miroir **FIX backoff** : nouvelle colonne `last_tentative` (posée à chaque tentative) — l'ancien test portait sur `last_regen` (succès seulement) donc le backoff ne s'appliquait jamais après un échec (retry toutes les 60 s) ; garde `en_cours` avec réveil (> 15 min = worker coupé → reprise) ; défauts host/port alignés sur l'architecture réelle (192.168.1.17:22, export sur pige même) ; ALTER idempotent au boot (MariaDB 10.2+), non bloquant.
- Tests hors-ligne **46/46 PASS** (`tests/test_lot_212.py` livré hors dépôt — FakeDB/FakeSession, aucune infra réelle).

## État Phase 21

- 21.1 FAIT · 21.3 FAIT · 21.4-a APPLIQUÉ et VALIDÉ (observation activable quand voulu) · **21.2 LIVRÉ (25/11), en attente déploiement/test** → reste 21.5 (~4-5 j), 21.6 (~2-3 j). GF3 (indexes) + comptage `IN_AZURACAST` = op DB sur .30/.39 ; GF5 partiel.
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
- `config.json` de production **jamais commités** — et désormais **hors suivi git** (untracked + `.gitignore`, commit `chore(repo)` de cette série) : plus aucune config ne peut entrer dans le dépôt ; gabarits `config.json.exemple` pour les clones neufs.
- Nom de la machine studio : **pige** (jamais « pigeon »).
