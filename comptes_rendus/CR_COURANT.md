# CR COURANT — projet AIRVS

> Fichier roulant : remplacé à chaque étape importante (jamais empilé).
> Historique complet : `git log -p -- comptes_rendus/CR_COURANT.md`.
> **Mis à jour** : 24/11/2026 · **Base git** : `8bd34e6` (recadrage Phase 21 — à pousser avec le présent commit) · Rédaction : session agent IA

## Dernière étape — Cadrage du lot 21.4 « Auto-purge RETIRED > 90 j » VALIDÉ (24/11)

L'exploitant a validé les cinq points ouverts du recadrage précédent, avec une addition : **les titres purgés en soft doivent rester clairement identifiés et loggés comme candidats à une future suppression physique** (quand la question 6 du cadrage 11/11 sera tranchée).

| Point | Décision validée |
|---|---|
| **Mode** | **Soft seul** : retrait playlists (déjà hors playlists : titres RETIRED) + flag `AZURA_RETIRED_S*` → `AZURA_REMOVED_S*`. Jamais de suppression de fichier. Le hard reste une action manuelle ponctuelle tant que Q6 non tranchée |
| **Traçabilité pour suppression future** | Chaque purge soft journalisée : `type_op='auto_purge'`, `song_id_radiodj`, `media_id_azura`, `station_id`, `details` JSON (artiste, titre, chemin fichier, âge RETIRED, seuil appliqué), `reversible=1`. Identification double : requête journal (une ligne SQL) **et** flag `AZURA_REMOVED_S*` dans `songs.comments` |
| **Seuil** | 90 jours, paramétrable `rotation.seuil_purge_jours` (défaut 90) |
| **Âge RETIRED** | Source = `journal_operations_azura` (rotation_soft / flag_resync) ; **titre ignoré si âge indéterminable** (titres RETIRED antérieurs au socle 21.1 : accumulation progressive de l'historique) |
| **Déclenchement** | Cron modèle `SYNC_AZURA_STATUS` : plage 8h-20h, garde de file anti-doublon, **+ inclusion dans le rattrapage GF4 au démarrage** |
| **Pilotage « automatique débrayable »** | Le moteur relit `config.json` **à chaque exécution** (instanciation par run) → interrupteurs effectifs au run suivant, sans redémarrage. Nouvelles clés section `rotation` : `auto_purge_actif` (**False** défaut), `auto_purge_mode` (`'observation'` → journalise sans écrire ; `'execution'` → purge soft réelle), `auto_purge_plafond_par_run` (défaut 25), `titres_proteges_ids` (song_ids jamais purgés — besoin éditorial spécifique), `dossiers_proteges` (existe déjà) |
| **Progression** | 21.4-a = implémentation + cycles en `'observation'` → l'exploitant valide les candidats → 21.4-b = bascule `'execution'` (une ligne de config) + confort UI si besoin |

Le `rotation_hard_autorise` (défaut False) reste inchangé et hors périmètre de ce lot.

## État Phase 21 (rappel compressé — détail au commit précédent)

- **21.1 FAIT** · **21.3 FAIT** (détail et preuves : CR recadrage `8bd34e6`, `git show 8bd34e6`).
- **21.4 en cours** : cadrage validé (ci-dessus) → implémentation 21.4-a.
- **À faire ensuite** : 21.2 (~1-2 j), 21.5 (~4-5 j), 21.6 (~2-3 j).
- Garde-fous ouverts uniquement : GF3 (indexes à vérifier en op sur .30), GF5 partiel (pas de `services_disponibles()` systématique). GF8 : l'indicateur « dernière auto-purge » complétera `/api/system/freshness` en 21.4-a.

## Plan d'implémentation 21.4-a (proposé, à lancer)

1. `airvs_worker/rotation_azura.py` : clés config nouvelles dans `lire_config_rotation` (défauts sûrs) ; `MoteurRotation.candidats_auto_purge()` (détection + âge + protections) ; `MoteurRotation.executer_auto_purge()` (modes observation/execution, plafond, journalisation).
2. `airvs_worker/worker_ubuntu.py` : type `AUTO_PURGE_RETIRED` dans la whitelist (L6517) ; handler `executer_auto_purge_retired` ; insertion cron dans le modèle `_sync_azura_automatique` (gardes identiques) ; inclusion GF4 ; bump version worker.
3. `airvs_dashboard_v2/config.py` : `WORKER_VERSION_ATTENDUE` alignée sur la nouvelle version worker (badge vert).
4. `airvs_dashboard_v2/blueprints/taches.py` : champ `derniere_auto_purge` dans `/api/system/freshness` (GF8). UI carte : restes minimaux en 21.4-a (la tâche apparaît dans les logs tâches existants).
5. Livraison : patch idempotent (motif éprouvé `patch_d4a_bis` : ancres exactes, compte 0/n → abandon, backup `.bak`, `compile()` sans .pyc, rattrapage automatique) + test hors-ligne avant application sur pige.
6. Version worker visée : `2026.11.24-B` (la lettre A du 24/11 est déjà prise par le hotfix dashboard `d64d1e3` — à contredire si l'exploitant préfère un autre schéma).

## Points de vigilance maintenus (cadrage §4)

- Migration R0 en DB : `IN_AZURACAST` restants à compter sur .30/.39 (op DB).
- GF3 : `idx_statut_type`, `idx_comments` à vérifier sur .30 (op DB).
- Chemin réel `export_db_mirror_V4.py` sur .30 à renseigner dans `mirror_regen_export_path` (vide = désactivé) ; logique interne de l'exporteur encore sur `IN_AZURACAST`.
- Push `AZURA_PUSHED_S6_S7` écrit en dur (azuracast.py L852/L1013) : harmonisation quand le push deviendra sélectif par station.
- TODO L2000 (cache mapping) à nettoyer à l'occasion — couvert par 21.3, commentaire obsolète.

## Contexte persistant

- **T2 SYNCHRO_DIFFUSION : clos et validé** (hotfix 2026.11.24-A dashboard `d64d1e3`, 10/10 au 23/11 et 24/11, rattrapage 4h00 prouvé).
- **Déduplication RadioDJ : mise de côté** (décision exploitant) ; cas 2h31 consigné ; détection à l'entrée en base active.
- **Diagnostic réseau : en pause** (consigne) ; SSHFS→SAMBA hors périmètre.
- **Versions** : worker `2026.11.23-A` ; dashboard attendu `2026.11.23-A` (alignées jusqu'au déploiement 21.4-a).

## Règles actives

- 9 consignes permanentes + **n°10 opérationnelle** : remplacer ce fichier et pousser (`docs(cr): …`) à chaque étape importante ; `git pull` + lecture du CR avant chaque étape importante ; l'historique git fait foi.
- `config.json` de production **jamais commités** (sections `automation`/`rotation` absentes du dépôt par conception) ; aucune donnée sensible dans les CR.
- Nom de la machine studio : **pige** (jamais « pigeon »).
