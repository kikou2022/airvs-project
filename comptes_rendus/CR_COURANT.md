# CR COURANT — projet AIRVS

> Fichier roulant : remplacé à chaque étape importante (jamais empilé).
> Historique complet : `git log -p -- comptes_rendus/CR_COURANT.md`.
> **Mis à jour** : 24/11/2026 · **Base git** : `d64d1e3` (`origin/main`) · Rédaction : session agent IA

## Dernière étape importante — Hotfix 2026.11.24-A (SYNCHRO_DIFFUSION via dashboard)

- **Déployé et poussé** : commit `d64d1e3` (parent `636a49e`) — 2 fichiers (`blueprints/taches.py`, `index.html`), 462 insertions, 0 suppression — md5 conformes aux sources livrées, `config.json` non touché.
- **Vérifié** : test hors-ligne 24/24 PASS relancé sur l'état GitHub exact ; chaîne complète patch officiel → application → commit → contenu GitHub contrôlée à chaque maillon.
- **Incident d'emballage** résolu (base attendue `a0ace69` = SHA local hors GitHub, au lieu de `origin/main` `636a49e`) : script d'application **v1.2** (base réalignée, message d'erreur clarifié, règle durable : base = `rev-parse origin/main` après fetch). Détail : CR de livraison `livraison_synchro_diffusion_dashboard_2026-11-24` §11.
- **Reste côté exploitation** : redémarrage du service dashboard, puis test interface — carte SYNCHRO_DIFFUSION visible, lancement mode **simulation** → tâche avec `dry_run=true`, statut affiché, rien écrit dans RadioDJ.
- Le lancement manuel utilise le lanceur CLI existant (`lancer_synchro_diffusion.py`) : le dashboard écrit une tâche avec les mêmes `parametres` que le CLI (`trigger_source: "manuel:dashboard"`) ; le comportement hors 4h00 a été validé (rattrapage par curseurs, garde de file, idempotence).

## Contexte T2 SYNCHRO_DIFFUSION (clos)

- Tour T2 + hotfix poussés le 23/11 (`636a49e`/`9d35c6c`) ; verdict 10/10 au 23/11 **et** au 24/11 ; rattrapage après extinction à 4h00 prouvé (CR T2 §11.5-11.6, zone de livraison 2026-11-23).
- Vérification indépendante de l'exploitant : titre diffusé sur AzuraCast visible dans RadioDJ à la seconde près.

## Chantiers ouverts

1. **Consigner au CR la vérification des doublons du 24/11** (validé par l'exploitant) — à intégrer au prochain CR.
2. **Déduplication RadioDJ** : chantier à part, reporté de longue date par l'exploitant ; en attendant, détection des doublons à l'entrée en base active depuis le dashboard Windows.
3. **Diagnostic réseau** : en pause (consigne). **SSHFS→SAMBA** : hors périmètre.

## Règles actives

- 9 consignes permanentes + **n°10 en ratification** : le présent fichier est remplacé et commité (`docs(cr): …`, poussé immédiatement) à chaque étape importante ; l'historique git fait foi.
- Version worker attendue par le dashboard : 2026.11.23-A (le hotfix `d64d1e3` ne modifie que le dashboard).
- `config.json` de production **jamais commités** ; aucune donnée sensible dans les CR (valeurs `<MASQUÉ>`).
