# CR COURANT — projet AIRVS

> Fichier roulant : remplacé à chaque étape importante (jamais empilé).
> Historique complet : `git log -p -- comptes_rendus/CR_COURANT.md`.
> **Mis à jour** : 24/11/2026 · **Base git** : `6b8695e` (`origin/main`) · Rédaction : session agent IA

## Dernière étape importante — Déploiement du hotfix 2026.11.24-A VALIDÉ par l'exploitant (24/11)

- **Déploiement effectué, tests interface OK** : carte SYNCHRO_DIFFUSION opérationnelle en production (lancement simulation/réel + statut). Redémarrage du service réalisé.
- Chaîne complète maîtrisée : patch officiel → script d'application v1.2 → commit `d64d1e3` (parent `636a49e`, 2 fichiers, 462 insertions, md5 conformes aux sources livrées) → test hors-ligne 24/24 PASS sur l'état GitHub.
- Incident d'emballage du jour (base `a0ace69` locale ≠ `origin/main`) résolu par le script v1.2 ; règle durable : base d'un patch = `rev-parse origin/main` après fetch (détail : CR de livraison `livraison_synchro_diffusion_dashboard_2026-11-24` §11).
- Fonctionnalité : le lancement manuel depuis le dashboard écrit une tâche avec les mêmes `parametres` que le lanceur CLI (`trigger_source: "manuel:dashboard"`) — comportement hors 4h00 validé (rattrapage par curseurs, garde de file, idempotence).

## Vérification des doublons (24/11) — consignée

- L'exploitant a contrôlé les doublons côté RadioDJ suite à la synchro : un titre diffusé à 2h31 sur AzuraCast apparaît dans RadioDJ à la même heure à la seconde près, avec un doublon du même titre à la même date.
- La **déduplication complète reste un chantier à part, reporté** de longue date par l'exploitant ; en attendant, la détection des doublons à l'entrée en base (dashboard Windows) reste active — déjà une avancée selon l'exploitant.

## Contexte T2 SYNCHRO_DIFFUSION (clos)

- Tour T2 + hotfix poussés le 23/11 (`636a49e`/`9d35c6c`) ; verdict 10/10 au 23/11 **et** au 24/11 ; rattrapage après extinction à 4h00 prouvé (CR T2 §11.5-11.6, zone de livraison 2026-11-23).

## Chantiers ouverts

1. **Déduplication RadioDJ** : chantier à part, reporté — priorisation à définir avec l'exploitant.
2. **Diagnostic réseau** : en pause (consigne). **SSHFS→SAMBA** : hors périmètre.

## Règles actives

- 9 consignes permanentes + **n°10 opérationnelle depuis le 24/11** (premier remplacement du CR courant : le présent commit) : remplacer ce fichier et pousser (`docs(cr): …`) à chaque étape importante ; l'historique git fait foi.
- Version worker attendue par le dashboard : 2026.11.23-A (le hotfix `d64d1e3` ne modifie que le dashboard).
- `config.json` de production **jamais commités** ; aucune donnée sensible dans les CR (valeurs `<MASQUÉ>`).
