# CR COURANT — projet AIRVS

> Fichier roulant : remplacé à chaque étape importante (jamais empilé).
> Historique complet : `git log -p -- comptes_rendus/CR_COURANT.md`.
> **Mis à jour** : 26/11/2026 · **Base git** : `b6002b4` (origin/main) · Rédaction : session agent IA

## Dernière étape — Lot 21.2 CLÔTURÉ (26/11) : série 9 patchs appliquée (`b6002b4`) + R5 validé en exploitation

**Application de la série (25/11 soir → 26/11)** : les 9 patchs (`git am` 0001→0009) sont
appliqués sur pige et poussés — HEAD = `b6002b4` = origin/main. Deux incidents d'application,
tous deux résolus sans aucune perte :
- **0001 refusé au premier essai** (« ne correspond pas à l'index ») : les étapes préalables du
  LISEZMOI (sauvegarde + `git checkout --` des fichiers modifiés) n'avaient pas été jouées — les
  2 config.json de production ET les 7 fichiers du lot 21.2 déjà déployés hors git divergeaient
  de l'index. Récupération : `git am --abort` → sauvegarde tar des fichiers modifiés → checkout
  vers l'état du dépôt → série en une commande (`git am git_commits/000*.patch`, ordre
  0001→0009 garanti par le tri, un seul `git am` à la fois).
- **0009 refusé** (contexte d'en-tête du CR différent entre le dépôt à `1513a0a` et la référence
  interne de la série) : CR final restauré depuis la sauvegarde + `git add` + `git am --continue`
  → commit `b6002b4`. Le push anticipé des 8 premiers (`1513a0a..4b475a3`) n'a causé aucun dégât.
- Vérifications post-application : py_compile OK (l'erreur `__pycache__` Permission denied est
  cosmétique — écriture du cache refusée au compte courant, la syntaxe compile) ;
  `git ls-files | grep config.json` = **gabarits seuls** (`config.json.exemple` ×2) — 0006 a
  bien sorti les configs du suivi ; `airvs_worker/azuracast_api.py` md5
  `1d0518afc00b585cefa954be21b034a3` (fix R5 inclus).

**R5 VALIDÉ EN EXPLOITATION (26/11)** : re-test du renommage après le fix `file`/`newPath`
(contrat réel vérifié dans la source AzuraCast 0.24.0) — succès, le HTTP 500 « Fichier non
spécifié. » ne réapparaît plus ; maj du cache mapping et regen miroir enchaînées. **Lot 21.2
entièrement clos** : R2 (déplacement inter-playlists) et R4 (vidage playlist + resync
anti-doublon) validés le 25/11, R5 le 26/11.

**Apports définitifs du lot + hotfix** :
- 0001 hotfix 2026.11.25-B : **plus aucune clé API réelle dans le dépôt** ; 0007 : épisode clés
  clos (rotation exploitant ~22/11, clés publiées = mortes ; effacement d'historique = option
  cosmétique, à la demande).
- 0006 chore : config.json **hors suivi git** (untracked + `.gitignore`, gabarits
  `config.json.exemple` pour clones neufs) — le bruit « config.json modifié » disparaît et
  committer une config est devenu impossible.
- 0002/0003/0004/0008 : opérations retour R2/R4/R5 (worker + dashboard, résolution media_id
  mutualisée), draineur miroir backoff opérationnel + défauts alignés sur l'architecture réelle,
  fix R5 body API.

**Nettoyage post-lot (à la convenance, R5 validé)** : `git_commits/`,
`LISEZMOI_APPLICATION_2026-11-25.md` et `/tmp/backup_prod_2026-11-25/` peuvent être archivés ou
supprimés. `comptes_rendus/CR_COURANT_2026.11.25-A.md` (archive CR) et
`airvs_worker/sonde_doublons_v1.py` restent en non-suivi.

## État Phase 21

- 21.1 FAIT · 21.3 FAIT · 21.4-a APPLIQUÉ et VALIDÉ (observation activable quand voulu) ·
  **21.2 CLOS (R2/R4/R5 validés en exploitation, série `b6002b4` poussée)** → reste 21.5
  (~4-5 j, audit/dédoublonnage, appui sonde v1), 21.6 (~2-3 j), 21.7 (candidat, ~2-3 j, à
  chiffrer). GF3 (indexes) + comptage `IN_AZURACAST` = op DB sur .30/.39 ; GF5 partiel.
- Restes reportés (candidats 21.5/21.6) : exporteur encore sur `IN_AZURACAST`, push
  `AZURA_PUSHED_S6_S7` en dur.
- **Prochaine étape opérationnelle suggérée : activation 21.4** — `auto_purge_actif: true`
  (observation) dans la section `rotation` du config.json worker, 2-3 cycles de contrôle
  (tâche AUTO_PURGE_RETIRED, candidats journalisés, aucune suppression), puis bascule
  `auto_purge_mode: "execution"` (config relu à chaque exécution, sans redémarrage).
- **21.7 (demande exploitant 25/11, non démarré)** : modal d'édition média style AzuraCast
  0.24.0 dans le dashboard (nom fichier, titre, artiste, genre, album, ISRC…) avec propagation
  vers `songs` RadioDJ (référence ultime) + PUT média AzuraCast ; périmètre pressenti : routes
  dashboard + modal (onglets, Fermer/Sauvegarder), opération retour worker dédiée (style R5),
  garde station, journal. À chiffrer au cadrage.

## Sonde doublons (consignée 25/11)

- `airvs_worker/sonde_doublons_v1.py` : fichier non suivi sur pige, mis de côté par
  l'exploitant — servira de point d'appui pour une campagne de dédoublonnage (→ lot 21.5).
  Ni commité ni supprimé.

## Contexte persistant

- T2 SYNCHRO_DIFFUSION : clos et validé (hotfix 2026.11.24-A `d64d1e3`, 10/10 au 23/11 et
  24/11, rattrapage 4h00 prouvé).
- Miroir public : lot 2026.11.25-A clos (clé API dynamique, Passe 2 HTTP 200, draineur vert
  au 24/11) — chaîne complète opérationnelle (rotation/retrait/resync/push → export auto ≤ 60 s).
- Dépôt : config.json hors suivi git — règle **appliquée mécaniquement** depuis 0006 (plus
  seulement consignée) ; versions en production pige = versions du dépôt (`b6002b4`), worker
  `2026.11.25-A`, dashboard au vert. Déduplication RadioDJ : mise de côté (décision
  exploitant), détection à l'entrée en base active ; diagnostic réseau : en pause (consigne),
  SSHFS→SAMBA hors périmètre.

## Règles actives

- 9 consignes permanentes + **n°10 opérationnelle** : remplacer ce fichier et pousser
  (`docs(cr): …`, commit séparé du feat) à chaque étape importante ; `git pull` + lecture du CR
  avant chaque étape importante ; l'historique git fait foi.
- **SHA vérifiables** : tout SHA cité dans un document poussé doit exister sur `origin/main`
  après fetch (`git cat-file -e <sha>^{commit}`) — jamais un SHA local de session non poussée.
- `config.json` de production **jamais commités** — hors suivi git (untracked + `.gitignore`,
  effectif depuis le commit `chore(repo)` 0006) ; gabarits `config.json.exemple` pour les
  clones neufs.
- **Contrat API AzuraCast : vérifier la source du tag exact avant d'implémenter** (leçon R5 :
  l'étude §2.5 annonçait `from`/`to`, le contrat réel est `file`/`newPath` — HTTP 500 pour des
  erreurs paramétriques).
- **Application de séries `git am`** : sauvegarder puis ramener à l'état du dépôt
  (`git checkout --`) les fichiers modifiés touchés par la série AVANT d'appliquer ; un seul
  `git am` à la fois (série entière en une commande, ordre garanti par le tri) ; en cas de
  refus : ne rien enchaîner d'autre, `git am --show-current-patch=diff` puis rapport.
- Nom de la machine studio : **pige** (jamais « pigeon »).
