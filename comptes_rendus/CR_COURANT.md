# CR COURANT — projet AIRVS

> Fichier roulant : remplacé à chaque étape importante (jamais empilé).
> Historique complet : `git log -p -- comptes_rendus/CR_COURANT.md`.
> **Mis à jour** : 24/11/2026 · **Base git** : `809c984` (`origin/main`) · Rédaction : session agent IA

## Dernière étape — Recadrage sur la Phase 21 « Rotation éditoriale » (24/11)

- Décision exploitant : la **déduplication RadioDJ reste de côté** (chantier reporté de longue date) ; la ligne de développement reprend la logique prévue dans `CADRAGE_Phase21_rotation_editoriale_2026-11-11.md` (document exploitant, hors dépôt ; il référence `ETUDE_API_AZURACAST_RETOUR.md`, `SPEC_TECHNIQUE_IMPLEMENTATION.md`, `MEMO_CONTRAINTES_IMPLEMENTATION.md`, maquettes validées du 03/11).
- Le présent CR **remplace** la matrice de convergence du cadrage (datée du 11/11) par l'état réellement constaté dans le dépôt au 24/11 (`809c984`) : les lots **21.1 et 21.3 sont livrés**, le lot **21.4 est le prochain** de l'enchaînement recommandé.
- Convention de nommage maintenue : les phases de la spec rotation sont préfixées **R** (R0→R17) pour ne pas collisionner avec les phases du projet (17-20).

## État d'évolution Phase 21 au 24/11 — lot par lot

| Lot | Contenu | État | Preuves (dépot `809c984`) |
|---|---|---|---|
| **21.1** — Consolidation & garde-fous | GF1, GF4, pagination, tables, GF8, régénération miroir | **FAIT** | Priorité SELECT `ORDER BY CASE` (worker L6533) ; rattrapage démarrage `_rattrapage_crons_au_demarrage` (L8699, branché au boot L8992) ; pagination `list_media_all` (azuracast_api.py L332, 20 pages × 10 000 — couvre le point de vigilance « 24 000 médias ») ; tables `journal_operations_azura`/`audit_doublons_radiodj`/`mapping_radiodj_azura`/`rotation_previews`/`mirror_regen_state` créées en idempotent au démarrage (L8559+) ; GF8 endpoint `/api/system/freshness` (blueprints/taches.py L458+) ; régénération miroir câblée : `demander_regen_miroir` + thread 60 s + debounce 10 min + backoff 30 min (`rotation_azura.py` L167-L420) |
| **21.3** — Onglet Rotation éditoriale (cœur R9-R12) | mapping + moteur + preview/execute + UI | **FAIT** | `rotation_azura.py` (1354 l. : `ResolveurMapping`, `MoteurRotation`, scoring hotfix 2026.11.13-E conditionné aux signaux réellement présents) ; blueprint `rotation.py` (10 endpoints : config/preview/preview résultat/execute/task/last/mapping stats/bootstrap) ; `rotation.js` v2 (hotfix C) branché `nav-rotation` ; carte « Rotation éditoriale » en production (index.html L681/L4576) |
| **21.4** — Auto-purge RETIRED > 90 j | tâche `AUTO_PURGE_RETIRED` + rattrapage + confirmation | **À FAIRE — prochain de l'enchaînement recommandé** | Reporté explicitement dans le code : « l'âge RETIRED vient avec l'auto-purge, lot 21.4 » (`rotation_azura.py` L935) ; type `auto_purge` déjà préparé dans l'ENUM de `journal_operations_azura` (003_create_journal_operations.sql L27/L40 et worker L8573) |
| **21.2** — Compléments opérations retour | R4 empty playlist, R5 rename/move, R2 move entre playlists (si encore utile) | **À FAIRE** (~1-2 j) | Seuls les ENUM `empty_playlist`/`rename` existent (worker L8572, synchro_diffusion.py L105) ; la méthode dédiée `remove_media_from_playlist` est déjà livrée (azuracast_api.py L484) ; pas d'`empty_playlist`, pas de `rename_file` |
| **21.5** — Audit doublons (R13-R15) | détection, marquage RadioDJ, exécution | **À FAIRE** (~4-5 j) | Table `audit_doublons_radiodj` (002) en place mais « sera remplie par le lot 21.5 » (worker L8562) ; aucune logique de détection |
| **21.6** — Push depuis rotation RadioDJ (Palier 1, R17) | automatisation du workflow M3U | **À FAIRE** (~2-3 j) | ENUM `m3u_push` préparée (worker L8573), aucune implémentation |

**Reste à faire : ~8 à 11 jours** (21.4 : 1 j · 21.2 : 1-2 j · 21.5 : 4-5 j · 21.6 : 2-3 j), hors opérations DB (GF3, vérif migration R0).

## État des 8 garde-fous au 24/11

| Garde-fou | État | Détail |
|---|---|---|
| GF1 — priorité SELECT worker | **FAIT** | `ORDER BY CASE` en place (L6533) |
| GF2 — optimisation resync flags | **OK / complété** | Pagination `list_media_all` livrée ; cache `mapping_radiodj_azura` livré (21.3) — le TODO L2000 (anciennement L1952) reste à nettoyer à l'occasion |
| GF3 — indexes MySQL | **À vérifier en DB** | Op sur .30 (`idx_statut_type`, `idx_comments`) — non traçable depuis le dépôt |
| GF4 — rattrapage démarrage | **FAIT** | L8699 : réinsertion `SYNC_AZURA_STATUS` + `SYNC_PLAYLISTS_CACHE` sous 5 min si dernière exécution > intervalle |
| GF5 — détection de disponibilité | **PARTIEL** (inchangé) | Pas de `services_disponibles()` systématique avant crons (vérifié par absence au 24/11) ; les handlers wrappent les erreurs |
| GF6 — file d'attente | **PARTIEL → couvert en pratique** | `date_planifiee` respectée (Phase 19.1, index L8947) ; le rattrapage ciblé GF4 couvre le besoin constaté |
| GF7 — config `automation` | **Couvert autrement** | `lire_config_rotation` lit la section `automation` du config.json worker : `rotation_hard_autorise` (défaut **False** — cadrage Q6 en réflexion), `mirror_regen_*` ; clés de scoring section `rotation`. Le `patch_config_automation.py` (scripts_db) est l'ancien mécanisme GF7 — **ne pas lancer** |
| GF8 — indicateurs de fraîcheur | **FAIT** (résync) | `/api/system/freshness` : `dernier_resync_azura` (+source), `derniere_tache`, `dernier_flag_resync` (journal 21.1). L'indicateur « dernier audit » suivra le lot 21.5 |

## Ce qu'il reste à faire — enchaînement et cadrage du lot 21.4

Enchaînement recommandé (cadrage §5, ajusté) : **21.4 → 21.2 → 21.5 → 21.6**. Pour démarrer 21.4, cinq points restent à valider par l'exploitant :

1. **Mode de purge** : retrait *soft* (flag → `AZURA_REMOVED_S*`) seul ; le *hard* (suppression physique) resterait verrouillé tant que `rotation_hard_autorise=False` (Q6 cadrage en réflexion).
2. **Seuil** : 90 j confirmé, paramétrable (`rotation.seuil_purge_jours`, défaut 90) ?
3. **Source de l'âge RETIRED** : proposition = `journal_operations_azura` (historique fiable depuis 21.1/21.3) ; titre ignoré si âge indéterminable (principe prudent).
4. **Déclenchement** : cron même modèle que `SYNC_AZURA_STATUS` (plage 8h-20h, garde de file anti-doublon) **+ inclusion dans le rattrapage GF4**.
5. **Confirmation avant exécution** : cycle preview → execute depuis la carte Rotation (comme la rotation manuelle), ou automatique avec plafond de titres purgés par run.

## Points de vigilance maintenus du cadrage (§4)

- **Migration R0 en DB** : nombre de `IN_AZURACAST` restants à vérifier sur .30/.39 (op DB).
- **Renommage des commentaires** « Phase 1/2/3 » → R1/R2/R3 lors des prochaines modifications (collision de numérotation).
- **Chemin réel de `export_db_mirror_V4.py` sur .30** à renseigner dans `mirror_regen_export_path` (vide = régénération désactivée, zéro risque) ; logique interne de l'exporteur encore sur l'ancien mot-clé `IN_AZURACAST` — nettoyage à l'occasion.
- **Push `AZURA_PUSHED_S6_S7` écrit en dur** (azuracast.py L852/L1013) : à harmoniser avec `get_default_flag_after_push_multi()` le jour où le push deviendra sélectif par station.

## Contexte persistant

- **T2 SYNCHRO_DIFFUSION : clos** — hotfix 2026.11.24-A déployé et **validé par l'exploitant** (24/11, tests interface OK) ; verdict 10/10 au 23/11 et au 24/11 ; rattrapage après extinction 4h00 prouvé. Détails : CR de livraison `livraison_synchro_diffusion_dashboard_2026-11-24` §11.
- **Vérification doublons (24/11)** : cas 2h31 consigné (titre diffusé sur AzuraCast apparaît dans RadioDJ à la même heure à la seconde près, avec doublon même date) ; détection à l'entrée en base (dashboard Windows) active ; déduplication complète = chantier séparé **mis de côté** par décision exploitant.
- **Chantiers ouverts** : 1) Phase 21 rotation éditoriale (ligne active, présent CR) ; 2) déduplication RadioDJ (de côté) ; 3) diagnostic réseau (en pause, consigne ; SSHFS→SAMBA hors périmètre).
- **Versions** : worker `2026.11.23-A` (D4-a bis) ; dashboard attendu par config.py : `2026.11.23-A` (badge jaune jusqu'au redéploiement Windows du worker si applicable).

## Règles actives

- 9 consignes permanentes + **n°10 opérationnelle** : remplacer ce fichier et pousser (`docs(cr): …`) à chaque étape importante ; l'historique git fait foi ; `git pull` + lecture du CR avant chaque étape importante.
- `config.json` de production **jamais commités** (section `automation`/`rotation` absentes du dépôt par conception) ; aucune donnée sensible dans les CR (valeurs `<MASQUÉ>`).
- Nom de la machine studio : **pige** (jamais « pigeon »).
