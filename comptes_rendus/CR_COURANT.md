# CR COURANT — projet AIRVS

> Fichier roulant : remplacé à chaque étape importante (jamais empilé).
> Historique complet : `git log -p -- comptes_rendus/CR_COURANT.md`.
> **Mis à jour** : 25/11/2026 (soir) · **Base git** : `1513a0a` (origin/main) · Rédaction : session agent IA

## Dernière étape — Lot 21.2 testé en exploitation : R2/R4 VALIDÉS, R5 corrigé (contrat API réel) ; série de 9 patchs en attente d'application

**Retours exploitation du 25/11 (7 fichiers déployés en direct par l'exploitant + redémarrage worker)** :
- **R2 déplacement inter-playlists : VALIDÉ** — média 13915 déplacé playlist 164 → 410, re-lecture API confirmée (playlists restantes [179, 406, 410]).
- **R4 vidage playlist : VALIDÉ** — playlist 282 vidée (`success: true`, fichiers conservés dans AzuraCast), resync anti-doublon enchaîné (« pas de doublon »).
- **R5 renommage : ÉCHEC → corrigé** — HTTP 500 « Fichier non spécifié. ». Cause racine vérifiée dans la source du tag AzuraCast **0.24.0** (`backend/src/Controller/Api/Stations/Files/RenameAction.php`) : l'endpoint est bien `PUT /files/rename` mais lit `file` et `newPath` dans le body — renvoie « File not specified. » (fr. « Fichier non spécifié. ») en **HTTP 500** quand `file` manque. Le worker envoyait `{"from": ..., "to": ...}` (contrat erroné de l'étude §2.5). Tout le reste de R5 a bien fonctionné (résolution media_id via cache mapping `artist_title_normalized` confiance 0.75, re-fetch du chemin courant).
- **Correctif** (commit `fix(worker)` du 25/11 soir) : `azuracast_api.py` uniquement — body `{'file': ..., 'newPath': ...}`. **Seul fichier à recopier sur pige** puis re-tester R5. Tests hors-ligne **52/52 PASS** (A8 aligné sur le contrat réel + A8b régression sur la réponse d'erreur 500).

**Série de patchs : 9 désormais** (0001→0007 inchangés + `0008-fix-worker-r5-rename` + `0009-docs-cr`) :
- Pas besoin de re-télécharger le lot complet : seuls **0008 et 0009** sont nouveaux ; les 7 patchs d'origine restent valides (md5 inchangés).
- Application dans l'ordre 0001→0009 (`git am` série), puis push. Production : remplacer uniquement `airvs_worker/azuracast_api.py` par la version du 25/11 soir (md5 au CHECKSUMS) et re-tester R5.

**Évolution demandée le 25/11 — modal édition média style AzuraCast (candidat lot 21.7)** :
- Demande de l'exploitant, capture fournie (modal « Modifier le média » d'AzuraCast 0.24.0, 25/11 16h19) : un modal du même type dans le dashboard AIRVS pour éditer les tags/métadonnées d'un titre (nom de fichier, titre, artiste, genre, album, ISRC…), avec **propagation vers la table `songs` de RadioDJ** (référence ultime) et vers le média AzuraCast correspondant.
- Périmètre pressenti : routes dashboard + modal (onglets à la AzuraCast, boutons Fermer/Sauvegarder), opération retour worker dédiée (style R5) écrivant dans `songs` RadioDJ + PUT média AzuraCast, garde station, journal. Estimation indicative ~2-3 j — à chiffrer après validation R5. **Non démarré.**

## État Phase 21

- 21.1 FAIT · 21.3 FAIT · 21.4-a APPLIQUÉ et VALIDÉ (observation activable quand voulu) · **21.2 DÉPLOYÉ et TESTÉ le 25/11 — R2/R4 validés, R5 corrigé (0008) ; en attente : re-test R5 puis application des 9 patchs + push** → reste 21.5 (~4-5 j), 21.6 (~2-3 j), 21.7 (nouveau candidat, modal édition média ~2-3 j). GF3 (indexes) + comptage `IN_AZURACAST` = op DB sur .30/.39 ; GF5 partiel.
- Restes 21.2 reportés (candidats 21.5/21.6) : exporteur encore sur `IN_AZURACAST`, push `AZURA_PUSHED_S6_S7` en dur.

## Sonde doublons (consignée 25/11)

- `airvs_worker/sonde_doublons_v1.py` : fichier **non suivi** sur pige, mis de côté par l'exploitant — servira de point d'appui pour une **campagne de dédoublonnage** (→ lot 21.5, audit doublons). Décision : ni commité ni supprimé pour l'instant.

## Contexte persistant

- T2 SYNCHRO_DIFFUSION : clos et validé (hotfix 2026.11.24-A `d64d1e3`, 10/10 au 23/11 et 24/11, rattrapage 4h00 prouvé).
- Miroir public : lot 2026.11.25-A clôturé (clé API dynamique, Passe 2 HTTP 200, draineur vert au 24/11 18h30). Épisode clés API clos (rotation exploitant ~22/11, clés publiées = mortes) ; `config.json` hors suivi git (gabarits `config.json.exemple`) ; effacement d'historique = option cosmétique à la demande, sans urgence.
- Déduplication RadioDJ : mise de côté (décision exploitant) ; détection à l'entrée en base active ; sonde v1 consignée ci-dessus.
- Diagnostic réseau : en pause (consigne) ; SSHFS→SAMBA hors périmètre.
- Versions en production pige (au 25/11 soir) : fichiers du lot 21.2 (worker `2026.11.25-A` + dashboard) **déployés** hors git, avec le fix R5 à recopier ; commits pas encore appliqués — les versions resteront « contrôlées rouges » côté dashboard jusqu'au push de la série.

## Règles actives

- 9 consignes permanentes + **n°10 opérationnelle** : remplacer ce fichier et pousser (`docs(cr): …`, commit séparé du feat) à chaque étape importante ; `git pull` + lecture du CR avant chaque étape ; l'historique git fait foi.
- **SHA vérifiables** : tout SHA cité dans un document poussé doit exister sur `origin/main` après fetch (`git cat-file -e <sha>^{commit}`) — jamais un SHA local de session non poussée.
- `config.json` de production **jamais commités** — hors suivi git (untracked + `.gitignore`, commit `chore(repo)` de la série) ; gabarits `config.json.exemple` pour les clones neufs.
- **Contrat API AzuraCast : vérifier la source du tag exact avant d'implémenter** (leçon R5 : l'étude §2.5 annonçait `from`/`to`, le contrat réel est `file`/`newPath` — HTTP 500 pour des erreurs paramétriques).
- Nom de la machine studio : **pige** (jamais « pigeon »).
