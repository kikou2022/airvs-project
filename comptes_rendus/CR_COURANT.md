# CR COURANT — projet AIRVS

> Fichier roulant : remplacé à chaque étape importante (jamais empilé).
> Historique complet : `git log -p -- comptes_rendus/CR_COURANT.md`.
> **Mis à jour** : 26/11/2026 (après-midi) · **Base git** : `d889eac` (origin/main) · Rédaction : session agent IA

## Dernière étape — 21.7 P1 CLOSE (26/11 13:09) : PUT custom_fields ACCEPTÉ, R2 levée

**V6 exécuté par l'exploitant (13:09) et ACCEPTÉ — rapport final : 12 OK / 0 KO.**
- PUT neutre avec bloc `custom_fields` (slugs `bpm`/`annee`/`commentaires`, valeurs
  réécrites à l'identique) sur le média de test 11918 : **HTTP OK, relecture
  strictement identique** (idempotent, rien changé en production).
- **R2 levée : aucun repli à activer** — les trois champs personnalisés
  (BPM, Année, Commentaires) seront éditables dès la P2.
- **P1 est CLOSE** : toutes les vérifications socle du cadrage 21.7 §12 sont vertes
  (slugs, colonnes `isrc`/`bpm`/`comments`, remplissage, mapping, GET/PUT
  `custom_fields`). Toutes les vigilances sont levées : **R1/R2/R3/R4/R5**.

**Rappel du socle vérifié (rapports 12:44 et 13:09, lot 2026.11.26-E)** :
- slugs réels : « BPM » → `bpm`, « Année » → `annee`, « Commentaires » → `commentaires`
  (R1) ; colonnes `isrc` (38,2 % rempli) et `bpm` (68,7 %) présentes (R4) ;
  `songs.comments` = flags `AZURA_*` exclusifs en exploitation — colonne réservée
  pipeline/badge, le modal n'y écrira jamais (critère n°10) ; cache
  `mapping_radiodj_azura` sain : 18 628 lignes, confiance moyenne 0,857, stations 6+7 (R5).
- **R3 levée sans correctif** (revue de code agent) : le pipeline n'émet **aucun PUT de
  métadonnées média** (écritures AzuraCast existantes = push SFTP, DELETE, playlists
  whitelist) — rien ne peut écraser `custom_fields` ; réserve chiffrée (< 0,5 j) non
  consommée.
- **Découverte P1** : le dashboard Flask (Windows) est **sans internet** (confirmé en revue
  de code) → le PUT du modal passera par le mécanisme d'actions du worker (cadrage §5.2 :
  le dashboard insère la tâche, le worker exécute SQL puis PUT, clé API côté worker
  uniquement). Chiffrage inchangé (enveloppe 6,5-8,0 j).

**MEDIA_FIELDS au config worker** (lot 2026.11.26-F, script `integrer_media_fields_217.py`,
indépendant du put-test) : section racine de 8 champs (slugs réels), autres sections
préservées, sauvegarde unique, JSON revalidé, relançable sans risque. Aucun effet
immédiat (aucun code ne lit encore la section — P2 la lira) ; aucun redémarrage ;
config hors suivi git.

**Ensuite : P2 — onglet « Métadonnées » du modal.** L'agent a demandé à l'exploitant les
**copies fraîches du code en production** (`airvs_worker/worker_ubuntu.py` version
2026.11.25-A + fichiers dashboard courants, backend Flask Windows et
`airvs_dashboard_v2/` inclus) pour construire P2 sur le code réel — les copies locales de
l'agent sont antérieures (worker du 22/11, dashboard du 11/11). 21.4-a continue de courir
en parallèle (aucune interaction).

## État Phase 21

- 21.1 FAIT · 21.3 FAIT · **21.2 CLOS** (R2/R4/R5 validés en exploitation, série 9 patchs
  poussée `b6002b4`) · **21.4-a OBSERVATION ACTIVE** (26/11 00h10, runs journaliers en
  cours ; bascule execution après 2-3 runs conformes) · **21.7 : P1 CLOSE (26/11 13:09,
  12 OK / 0 KO — PUT custom_fields accepté) ; R1/R2/R3/R4/R5 toutes levées ; P2 démarre** ;
  reste 21.6 (~2-3 j) ; **21.5 mis de côté** (décision exploitant
  26/11). GF3 (indexes) + comptage `IN_AZURACAST` = op DB sur .30/.39 ; GF5 partiel.
- **21.4-a trace courante** : premier run (manuel, 26/11 00h10) conforme — 0 candidat,
  zéro écriture RadioDJ/AzuraCast ; les 3 titres RETIRED actuels sont ignorés volontairement
  (retraits antérieurs au journal 21.1, âge indéterminable → **jamais purgés
  automatiquement**, sort = décision manuelle exploitant). Gardes confirmés : un run
  AUTO_PURGE/jour (le run du jour compte pour la journée), garde GF4 <24 h (pas de
  réinsertion au démarrage du 26/11 09:07:37 ; `NRestarts=0`, aucun crash auto).
  Bascule `auto_purge_mode: "execution"` = une clé dans le config.json, effectif au run
  suivant, décidée par l'exploitant après 2-3 runs journaliers conformes.
- Restes reportés (candidats 21.5/21.6) : exporteur encore sur `IN_AZURACAST`, push
  `AZURA_PUSHED_S6_S7` en dur.
- **Prochaines étapes opérationnelles** : (1) laisser courir les runs journaliers 21.4-a
  puis basculer en execution ; (2) 21.7 : config (script paquet F si pas déjà passé) +
  envoyer à l'agent les copies fraîches du code (worker + dashboard) → il livre P2 ;
  (3) 21.6 ensuite. 21.5 : hors lot courant (mis de côté).
- **21.7 (cadrage v3 du 26/11, P1 intégré)** : modal d'édition média dans le dashboard —
  métadonnées via PUT AzuraCast delta + SQL `songs` (titre, artiste, album, genre, ISRC,
  année, BPM, commentaires), renommage via R5 (onglet « Fichier »), champs pilotés par
  config (`MEDIA_FIELDS` intégrées au config, slugs réels), `login_requis`, journal en
  base ; PUT porté par le worker (dashboard Flask sans internet — §5.2) ; annexe A :
  Déplacer/Vider/Renommer sur les onglets de Programmation. Enveloppe 6,5-8 j inchangée.
  Point badge tranché côté code ET confirmé terrain : `songs.comments` = flag AZURA
  réservé (badge), le modal n'y écrira jamais.

## Architecture (référence — vérifiée avec l'exploitant le 26/11)

- **AzuraCast** (stations 6/7) : **serveur dédié OVH** (`azuracast.2026.airvs.fr`) — 24/7.
  AzuraCast n'a **jamais** tourné sur radiodb.
- **Miroir public** : VPS OVH 1 (`miroir.db.airvs.fr`) — 24/7.
- **radiodb** (debian .30) : **serveur SQL pour RadioDJ ET pour le dashboard** (base
  `airvs_dashboard`) — **éteinte la nuit**.
- **Worker** : **pige** (machine studio, jamais « pigeon »), service **`airvs-worker.service`**
  (systemd : relance au boot ; `NRestarts` distingue crash auto vs redémarrage manuel).
- **Dashboard v2** : machine **Windows**, **tâche planifiée au démarrage** — **sans accès
  internet** (confirmé en revue de code le 26/11 : les appels API AzuraCast passent par le
  worker, jamais par le dashboard).
- Conséquence : machines locales éteintes la nuit → les cycles quotidiens du worker
  intègrent le **rattrapage au démarrage (GF4)** ; absence de lignes en base pendant la nuit
  = normal (base éteinte, aucune insertion possible).

## Sonde doublons (consignée 25/11)

- `airvs_worker/sonde_doublons_v1.py` : fichier non suivi sur pige, mis de côté par
  l'exploitant — servira de point d'appui pour une campagne de dédoublonnage (→ lot 21.5).
  Ni commité ni supprimé.

## Contexte persistant

- T2 SYNCHRO_DIFFUSION : clos et validé (hotfix 2026.11.24-A `d64d1e3`, rattrapage 4h00
  prouvé ; cycle quotidien désormais en plage 8h-20h + rattrapage au démarrage).
- Miroir public : lot 2026.11.25-A clos (clé API dynamique, Passe 2 HTTP 200, draineur vert)
  — chaîne complète opérationnelle (rotation/retrait/resync/push → export auto ≤ 60 s).
- Lot 21.2 CLOS : plus aucune clé API réelle dans le dépôt (hotfix 2026.11.25-B, épisode
  clés clos) ; `airvs_worker/azuracast_api.py` md5 `1d0518afc00b585cefa954be21b034a3`
  (fix R5 inclus).
- Dépôt : config.json **hors suivi git** — règle appliquée mécaniquement (commit 0006) ;
  versions en production pige = versions du dépôt, worker `2026.11.25-A`, dashboard au vert.
  Déduplication RadioDJ : mise de côté (décision exploitant), détection à l'entrée en base
  active ; diagnostic réseau : en pause (consigne), SSHFS→SAMBA hors périmètre.
- Nettoyage à la convenance (21.2 clos) : `git_commits/`, `LISEZMOI_APPLICATION_2026-11-25.md`,
  `/tmp/backup_prod_2026-11-25/` archivables ; restent en non-suivi :
  `comptes_rendus/CR_COURANT_2026.11.25-A.md` (archive CR) et la sonde doublons.

## Règles actives

- 9 consignes permanentes + **n°10 opérationnelle** : le CR est **rédigé par l'agent et
  livré en fichier complet** dans le paquet de déploiement — jamais de bloc à coller à la
  main (méthode unique, fixée le 26/11) ; l'exploitant remplace le fichier, pousse
  (`docs(cr): …`, commit séparé du feat) à chaque étape importante ; `git pull` + lecture du
  CR avant chaque étape importante ; l'historique git fait foi.
- **Architecture : se référer à la section Architecture, jamais de supposition** sur
  l'emplacement des composants (leçon 26/11 : AzuraCast = dédié OVH ; .30 = SQL RadioDJ +
  dashboard, éteinte la nuit).
- **SHA vérifiables** : tout SHA cité dans un document poussé doit exister sur `origin/main`
  après fetch (`git cat-file -e <sha>^{commit}`) — jamais un SHA local de session non poussée.
- `config.json` de production **jamais commités** — hors suivi git (untracked + `.gitignore`,
  effectif depuis le commit `chore(repo)` 0006) ; gabarits `config.json.exemple` pour les
  clones neufs.
- **Contrat API AzuraCast : vérifier la source du tag exact avant d'implémenter** (leçon R5 :
  l'étude §2.5 annonçait `from`/`to`, le contrat réel est `file`/`newPath` — HTTP 500 pour
  des erreurs paramétriques).
- **Application de séries `git am`** : sauvegarder puis ramener à l'état du dépôt
  (`git checkout --`) les fichiers modifiés touchés par la série AVANT d'appliquer ; un seul
  `git am` à la fois (série entière en une commande, ordre garanti par le tri) ; en cas de
  refus : ne rien enchaîner d'autre, `git am --show-current-patch=diff` puis rapport.
- Nom de la machine studio : **pige** (jamais « pigeon »).
