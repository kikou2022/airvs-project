# CR COURANT — projet AIRVS

> Fichier roulant : remplacé à chaque étape importante (jamais empilé).
> Historique complet : `git log -p -- comptes_rendus/CR_COURANT.md`.
> **Mis à jour** : 26/11/2026 (journée) · **Base git** : `d889eac` (origin/main) · Rédaction : session agent IA

## Dernière étape — Cadrage 21.7 v2 ARBITRÉ ET CHIFFRÉ (26/11) ; 21.5 mis de côté

**Cadrage 21.7 (modal édition média) arbitré par l'exploitant, doc v2 livré** :
- Architecture validée : le modal n'utilise `PUT /api/station/{id}/file/{media_id}` QUE
  pour les métadonnées (delta strict) ; renommage via opération R5 ; écriture `songs` par
  le canal SQL du dashboard ; un champ = une source de vérité, propagation immédiate puis
  rattrapage par synchro (aucune file d'attente nouvelle).
- Corrections v1 : `songs.isrc` natif confirmé sur l'instance (aucun ALTER TABLE) ; les
  champs personnalisés AzuraCast **BPM, Année, Commentaires** sont intégrés ; Année
  éditable des deux côtés. Commentaires = source AzuraCast seule, pour raison structurelle
  (pas de colonne `songs` standard) — **non lié au badge AzuraCast** (cf. point ouvert).
- Liste des champs pilotée par la configuration (`MEDIA_FIELDS` du config dashboard) :
  ajouter un champ futur = une entrée de config, sans code.
- `login_requis` suffit ; bouton « Éditer » sur toute vue média + panneau tâches après
  action, dans l'Explorateur ET les onglets de Programmation.
- **Annexe A** (nouveau) : portage des actions Déplacer/Vider/Renommer sur les onglets de
  Programmation (constat exploitant : absentes de ces onglets) — sans réimplémentation,
  renommage = R5 partout.
- **Chiffrage recalculé** : modal 4,5-5,5 j + annexe A 2,0-2,5 j = **enveloppe 6,5-8,0 j**.
  Séquencement P1 (socle + vérifs API) → P2 (onglet Métadonnées) → P3 (onglet Fichier +
  points d'entrée Explorateur) → Annexe A → P4 (après-action Programmation) ; livrable
  utile dès P3.
- Vérifications Phase 1 : PUT de test avec `custom_fields` sur l'instance OVH (exposition
  des custom fields confirmée côté API, écriture à valider par un test) ; slugs réels des
  champs personnalisés via `GET /api/admin/custom_fields` ; présence/remplissage de
  `songs.bpm` ; **delta strict des PUT de synchro** (ne jamais écraser `custom_fields`,
  sinon perte des Commentaires à chaque synchro — correctif mineur prévu si besoin).
- **Point ouvert tracé (question exploitant 26/11)** : si le badge AzuraCast du dashboard
  repose (même partiellement) sur le champ personnalisé Commentaires, son comportement
  après édition via le modal est à instruire dans un futur CR. Ne bloque pas 21.7.
- **21.5 (audit doublons) mis de côté par décision exploitant** : hors lot courant,
  non prioritaire.

## État Phase 21

- 21.1 FAIT · 21.3 FAIT · **21.2 CLOS** (R2/R4/R5 validés en exploitation, série 9 patchs
  poussée `b6002b4`) · **21.4-a OBSERVATION ACTIVE** (26/11 00h10, runs journaliers en
  cours ; bascule execution après 2-3 runs conformes) · **21.7 CADRÉ (26/11, v2),
  prioritaire du lot** → reste 21.6 (~2-3 j) ; **21.5 mis de côté** (décision exploitant
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
  puis basculer en execution ; (2) 21.7 : implémentation, démarrer par P1 (vérifications
  API, cf. cadrage) ; (3) 21.6 ensuite. 21.5 : hors lot courant (mis de côté).
- **21.7 (cadrage v2 du 26/11, arbitrages exploitant intégrés)** : modal d'édition média
  dans le dashboard — métadonnées via PUT AzuraCast delta + SQL `songs` (titre, artiste,
  album, genre, ISRC, année, BPM, commentaires), renommage via R5 (onglet « Fichier »),
  champs pilotés par config (`MEDIA_FIELDS`), `login_requis`, journal en base ; annexe A :
  Déplacer/Vider/Renommer sur les onglets de Programmation. Enveloppe 6,5-8 j. Point
  ouvert : badge AzuraCast ↔ champ Commentaires (futur CR).

## Architecture (référence — vérifiée avec l'exploitant le 26/11)

- **AzuraCast** (stations 6/7) : **serveur dédié OVH** (`azuracast.2026.airvs.fr`) — 24/7.
  AzuraCast n'a **jamais** tourné sur radiodb.
- **Miroir public** : VPS OVH 1 (`miroir.db.airvs.fr`) — 24/7.
- **radiodb** (debian .30) : **serveur SQL pour RadioDJ ET pour le dashboard** (base
  `airvs_dashboard`) — **éteinte la nuit**.
- **Worker** : **pige** (machine studio, jamais « pigeon »), service **`airvs-worker.service`**
  (systemd : relance au boot ; `NRestarts` distingue crash auto vs redémarrage manuel).
- **Dashboard v2** : machine **Windows**, **tâche planifiée au démarrage**.
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
