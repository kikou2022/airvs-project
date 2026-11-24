# CR COURANT — projet AIRVS

> Fichier roulant : remplacé à chaque étape importante (jamais empilé).
> Historique complet : `git log -p -- comptes_rendus/CR_COURANT.md`.
> **Mis à jour** : 25/11/2026 · **Base git** : `be9793e` (fix miroir clé API 2026.11.25-A, poussé sur origin/main) · Rédaction : session agent IA

## Dernière étape — Lot 2026.11.25-A : miroir public — CLÔTURÉ (clé API dynamique + draineur, validés en exploitation 24/11)

**Demande exploitant (25/11)** : « le script est sur la même machine que le worker → indiquer une
variable plutôt que l'API en dur, éviter d'y revenir ». Fait : `export_db_mirror_V4.py` ne porte
**plus AUCUNE clé** — il lit la clé active dans le **config.json du worker** (§ `azuracast`,
`../airvs_worker/config.json` résolu depuis `__file__`). Renouveler la clé = éditer ce seul
config.json, jamais le script.

- **Résolution** (nouvelle fonction `lire_config_api_azura()`) : ① env `AIRVS_AZURA_API_KEY`
  (écrase tout, test/cron ; `AIRVS_AZURA_BASE_URL` / `AIRVS_AZURA_CONFIG` surchargent aussi) →
  ② config.json worker § `azuracast.api_key` + `base_url` → ③ rien trouvé = **clé vide → Passe 2
  sautée proprement**, export non bloqué (Passe 1 noms locaux couvre l'essentiel ; comportement
  tolérant inchangé). `AZURA_API_URL` reconstruite depuis `base_url` config + `AZURA_STATION_ID=7`
  (URL identique à l'ancienne en dur) ; la **source** est loguée, jamais la clé.
- **Tests hors-ligne 8/8 PASS** (env prioritaire ; chemin défaut `../airvs_worker/config.json` ;
  clé lue + URL reconstruite ; config absent → clé vide ; ancienne clé et motif `AZURA_API_KEY = "…"`
  absents du fichier ; garde Passe 2 présente) + `py_compile` OK.
- **Livraison** : `fichiers_a_deployer_2026.11.25-A/` (fichier complet 432 l.,
  md5 `b5b717474801f2e30ee19a0750b3f6a9` + LISEZMOI) — méthode fichiers complets, patch abandonné.
- **LISEZMOI = séquence complète** : ① pré-vol — vérifier que le config.json worker de production
  porte la clé ACTIVE (== constante L211 worker_ubuntu.py déployé ; commande auto fournie ; sinon
  l'éditer, relu à chaque appel) ; ② dépôt sur pige + `chmod +x` + md5 ; ③ test manuel —
  « [CONFIG] Clé API chargée (source : …/airvs_worker/config.json) », Passe 2 HTTP 200
  (**plus de 403**), « Succès vers debian12/vps » ; ④ test négatif optionnel (`AIRVS_AZURA_CONFIG`
  inexistant → Passe 2 sautée, export réussi quand même) ; ⑤ **Restant 2** draineur (voir État).
- **Rappel test décisif 24/11 17h11 (Option A)** : 51300 titres, SCP debian12 + vps OK — accès BDD
  distant et clés BatchMode pige→.30 / pige→VPS prouvés ; seul le 403 Passe 2 restait → traité ici.
- **VALIDATION SCRIPT EN EXPLOITATION (24/11 17h41)** : « [CONFIG] Clé API AzuraCast chargée —
  source : …/airvs_worker/config.json » → pré-vol clé production validé de fait ; Passe 2 API :
  **8828 correspondances (HTTP 200) — le 403 est révolu** ; 51300 titres exportés, 226 matchs
  Shazam, « Succès vers debian12 » + « Succès vers vps » (≈ 57 s au total).
- **RESTANT 2 FERMÉ (24/11 18h28-18h30)** : prérequis SSH validé (17h40 « SSH LOCAL OK » sur
  `sebastien@192.168.1.17` — IP pleine de pige, pas localhost) ; section `"automation"` fusionnée
  dans les **deux** config.json (worker : 4 clés `mirror_regen_*` ajoutées aux 5 clés sync
  existantes ; dashboard : section minimale) ; `python3 -m json.tool` OK des deux côtés ; draineur
  testé par `pending=1` forcé → log « [MIROIR] Régénération db.json OK : export exécuté sur
  sebastien@192.168.1.17:22 (…) », état « pending non / statut ✓ OK / dernière regen 18:30:48 » —
  **panneau [MIROIR] au vert**. Chaîne complète opérationnelle : prochaine rotation/retrait/resync/
  push → export automatique ≤ 60 s (debounce 10 min, ≈ 1 min/run, thread de fond, sans incidence
  sur les tâches).
- **Commit + push : FAIT** — `34cc406` (`feat` 21.4-a, 4 fichiers) et `be9793e` (`fix(mirror)`
  2026.11.25-A) sont sur origin/main. Reste : activation observation/execution 21.4 quand voulu
  (config relu par exécution, sans redémarrage).

## Étape précédente — Lot 21.4-a APPLIQUÉ et VALIDÉ sur pige (24/11)

**Livraison par fichiers complets** (le patch `deploiement_214a_auto_purge/` est abandonné).
Application pige 24/11 ≈ 16h09 puis validation par l'exploitant : log « version 2026.11.24-B »
(redémarrage routine automatique) ; `py_compile` 4/4 ; **cron débrayé vérifié** (0 tâche
AUTO_PURGE_RETIRED, garde présente cron + rattrapage GF4) ; **run d'observation manuel — tâche 45411
→ termine** : `{"mode": "observation", "detectes": 0, "purges": 0, "erreurs": 0, "ignores_age": 3,
"proteges": 0, "seuil_jours": 90, "plafond": 25}` — zéro écriture RadioDJ/AzuraCast ; journal
`auto_purge` vide = cohérent (journalisation PAR CANDIDAT) ; les 3 « âge inconnu » = titres RETIRED
antérieurs au socle 21.1 (accumulation progressive via playlist_remove). Tests T1-T5 hors-ligne
tous PASS (feat poussé : `34cc406` ; détail des tests dans le LISEZMOI 2026.11.24-B, zone de
livraison hors dépôt).

## État Phase 21

- **21.1 FAIT** · **21.3 FAIT** · **21.4-a : APPLIQUÉ et VALIDÉ sur pige (24/11)** → observation activable quand voulu → 21.4-b (execution).
- **Reste ensuite** : 21.2 (~1-2 j), 21.5 (~4-5 j), 21.6 (~2-3 j). GF3 (indexes) + comptage `IN_AZURACAST` restants = op DB sur .30/.39 ; GF5 partiel.
- Points de vigilance maintenus : **miroir public — Option A VALIDÉE par test (24/11 17h11)**. Architecture (preuves dépôt) : `airvs_mirror/export_db_mirror_V4.py` (432 l. depuis le lot 2026.11.25-A, dans le repo = à jour flags 21.1) lit `radiodb` + `airvs_dashboard` sur 192.168.1.30:3306 + API AzuraCast HTTPS, écrit /tmp/airvs_db_export.json puis SCP vers **DEUX** destinations — debian12 `sebastien@192.168.1.30:/var/www/html/airvs_mirror/db.json` (LAN) et vps `ubuntu@54.37.38.117:/var/www/html/airvs_mirror/db.json` (**VPS OVH 1**, miroir public `https://miroir.db.airvs.fr`, vhost nginx `programmes-vps/install_vps_ovh_1.sh`, chargé par `app_vps_ovh_1.py` via MIRROR_JSON_PATH). Ne peut tourner ni sur AzuraCast ni sur le VPS (pas d'accès BDD LAN) ; find sur .30 vide (jamais déployé). **Test décisif réussi depuis pige** : 51300 titres exportés, « Succès vers debian12 » + « Succès vers vps » — miroir rafraîchi sur .30 ET le VPS ; accès BDD distant et clés BatchMode pige→.30 / pige→VPS prouvés. **Clé API — RÉSOLUE et VALIDÉE en exploitation (lot 2026.11.25-A déployé, 24/11 17h41)** : plus aucune clé en dur dans le script — résolution env `AIRVS_AZURA_API_KEY` → config.json worker § `azuracast` (détails en tête de CR ; Passe 2 sautée proprement si clé absente, export non bloqué) ; log d'exploitation : clé chargée du config.json worker, Passe 2 API 8828 correspondances **HTTP 200** (le 403 est révolu). Restent les fallbacks L54/L999 d'`azuracast_api.py` (ancienne clé en dur) → alignement candidat 21.2. **Restant 2 — config draineur (Option A) — FERMÉ, validé 24/11 18h28-18h30** : prérequis SSH validé (17h40 « SSH LOCAL OK » sur `sebastien@192.168.1.17`, IP pleine de pige — pas localhost) ; `"automation"` fusionnée dans les **deux** config.json (worker = déclenchement réel : 4 clés `mirror_regen_*` ajoutées aux clés sync existantes ; dashboard = badges : section minimale) ; test par `pending=1` forcé → « [MIROIR] Régénération db.json OK : export exécuté sur sebastien@192.168.1.17:22 », état « pending non / ✓ OK / 18:30:48 », panneau au vert — **chaîne miroir complète opérationnelle** (rotation/retrait/resync/push → export auto ≤ 60 s). **Texte descriptif du panneau périmé** (`index.html` L4662-4664 : « déclenche export_db_mirror_V4.py sur .30 » — réel : pige/.17 même, SSH loopback) → cosmétique candidat 21.2. **Défauts du code trompeurs** (`mirror_regen_host` par défaut azuracast:2022 — ne correspond pas à l'architecture réelle) + backoff inopérant tant qu'aucun succès (`last_regen` posé aux succès seulement) + texte panneau périmé (ci-dessus) = candidats micro-lot 21.2 (sans effet au quotidien depuis que le config.json fixe les valeurs). Commits : feat 4 fichiers 21.4-a (`34cc406`) + fix(mirror) 2026.11.25-A (`be9793e`) poussés sur origin/main ; docs(cr) côté session. Restent : exporteur encore sur `IN_AZURACAST`, push `AZURA_PUSHED_S6_S7` en dur, TODO cache mapping L2000 (obsolète, à nettoyer).

## Contexte persistant

- **T2 SYNCHRO_DIFFUSION : clos et validé** (hotfix 2026.11.24-A dashboard `d64d1e3`, 10/10 au 23/11 et 24/11, rattrapage 4h00 prouvé).
- **Déduplication RadioDJ : mise de côté** (décision exploitant) ; cas 2h31 consigné ; détection à l'entrée en base active.
- **Diagnostic réseau : en pause** (consigne) ; SSHFS→SAMBA hors périmètre.
- **Versions en production pige (24/11 16h09)** : worker `2026.11.24-B` ; dashboard `2026.11.24-B`.

## Règles actives

- 9 consignes permanentes + **n°10 opérationnelle** : remplacer ce fichier et pousser (`docs(cr): …`, commit séparé du feat) à chaque étape importante ; `git pull` + lecture du CR avant chaque étape importante ; l'historique git fait foi.
- **SHA vérifiables** : tout SHA cité dans un document poussé doit exister sur `origin/main` après fetch (`git cat-file -e <sha>^{commit}`) — jamais un SHA local de session non poussée (correctif du 25/11 : 2 SHA morts de ce CR remplacés par les SHA poussés).
- `config.json` de production **jamais commités** (sections `automation`/`rotation` absentes du dépôt par conception) ; aucune donnée sensible dans les CR.
- Nom de la machine studio : **pige** (jamais « pigeon »).
