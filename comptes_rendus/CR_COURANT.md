# CR COURANT — projet AIRVS

> Fichier roulant : remplacé à chaque étape importante (jamais empilé).
> Historique complet : `git log -p -- comptes_rendus/CR_COURANT.md`.
> **Mis à jour** : 26/11/2026 (matin) · **Base git** : `410f64d` (origin/main) · Rédaction : session agent IA

## Dernière étape — Lot 21.4-a : OBSERVATION ACTIVÉE en production (26/11 00h10) + gardes confirmés (26/11 matin)

**Activation (26/11 ~00h10, paquet 2026.11.26-B)** : `activer_purge_observation_214.py`
exécuté sur pige — `rotation.auto_purge_actif=true`, `auto_purge_mode="observation"`,
défauts posés (seuil 90 j, plafond 25/run, protections vides), sauvegarde préalable
`config.json.bak_avant_activation_214`, JSON validé après écriture, git propre (config
hors suivi). Aucun redémarrage requis (config relu à chaque exécution).

**Premier run (tâche insérée manuellement, `trigger_source: manuel:test`) — conforme** :
mode observation, **0 candidat**, 0 erreur, **zéro écriture** RadioDJ/AzuraCast ; seuil
90 j et plafond 25 correctement lus du config. Les **3 titres RETIRED actuels sont ignorés
volontairement** (âge de retrait indéterminable : retraits antérieurs au journal 21.1 → ils
ne seront **jamais** purgés automatiquement ; leur sort est une décision manuelle de
l'exploitant, quand voulu). Dorénavant, tout retrait passé par le dashboard est journalisé
et daté → le titre deviendra candidat après 90 jours.

**Gardes confirmés en exploitation (26/11 matin)** :
- Retour base SQL (.30) à ~08:45 → reprise normale : SYNCHRO_DIFFUSION quotidienne
  conforme (311 lignes écrites ; 29 échecs de résolution = vivier P8b/P7 connu, extraction
  manuelle prévue à ~2 semaines de T2) ; SYNC_AZURA_STATUS rattrapage GF4 OK (15 764
  fichiers, 0 écriture) ; syncs VPS 5 min OK ; NOWPLAYING OK.
- **Garde 1×/jour** : aucun nouvel AUTO_PURGE planifié le 26 — le run manuel de 00h10
  compte pour la journée (comportement attendu, confirmé).
- **Garde GF4 <24 h** : au démarrage worker du 26/11 09:07:37 CET, l'auto-purge n'est pas
  réinsérée (dernière terminée < 24 h) — comportement attendu, confirmé.
- Worker redémarré à 09:07:37 par l'exploitant (`NRestarts=0` → aucun crash auto) ;
  journalctl opérationnel (NOWPLAYING, `[VPS SYNC AUTO] … planifiée`, tâches interceptée/
  terminée). Le log d'exécution des tâches reste lu en base/dashboard (conception).

**Attendu ensuite** : un run AUTO_PURGE_RETIRED/jour planifié par le cron (plage 8h-20h,
probablement peu après le retour de la base, modèle 08:45-09:00), **0 candidat attendu**
tant que les retraits journalisés n'atteignent pas 90 j — c'est un résultat valide :
l'observation valide la mécanique (1 run/jour, zéro écriture). **Bascule
`auto_purge_mode: "execution"` décidée par l'exploitant après 2-3 runs journaliers
conformes** (une clé à changer dans le config.json — effectif au run suivant, sans
redémarrage ; en execution : flag REMOVED, FICHIER CONSERVÉ, suppression physique toujours
hors périmètre).

## État Phase 21

- 21.1 FAIT · 21.3 FAIT · **21.2 CLOS** (R2/R4/R5 validés en exploitation, série 9 patchs
  poussée `b6002b4`) · **21.4-a OBSERVATION ACTIVE depuis le 26/11 00h10** (runs journaliers
  en cours ; bascule execution après 2-3 runs conformes) → restent 21.5 (~4-5 j,
  audit/dédoublonnage, appui sonde v1), 21.6 (~2-3 j), 21.7 (candidat, ~2-3 j, à chiffrer).
  GF3 (indexes) + comptage `IN_AZURACAST` = op DB sur .30/.39 ; GF5 partiel.
- Restes reportés (candidats 21.5/21.6) : exporteur encore sur `IN_AZURACAST`, push
  `AZURA_PUSHED_S6_S7` en dur.
- **Prochaines étapes opérationnelles** : (1) laisser courir les runs journaliers 21.4-a et
  basculer en execution après 2-3 runs conformes ; (2) cadrage 21.5 (dédoublonnage, appui
  `sonde_doublons_v1.py`) ; (3) chiffrage du candidat 21.7.
- **21.7 (demande exploitant 25/11, non démarré)** : modal d'édition média style AzuraCast
  0.24.0 dans le dashboard (nom fichier, titre, artiste, genre, album, ISRC…) avec
  propagation vers `songs` RadioDJ (référence ultime) + PUT média AzuraCast ; périmètre
  pressenti : routes dashboard + modal (onglets, Fermer/Sauvegarder), opération retour
  worker dédiée (style R5), garde station, journal. À chiffrer au cadrage.

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
