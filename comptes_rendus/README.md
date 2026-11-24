# CR courant — mémoire partagée du projet AIRVS

Ce dossier contient **un seul document** : `CR_COURANT.md` — l'état contextuel
du projet à la dernière étape importante. Il est **remplacé** (pas empilé) à
chaque nouvelle étape : le nouveau CR prend sa place ; l'ancien reste
consultable dans l'historique git (`git log -p -- comptes_rendus/CR_COURANT.md`).

## Règle de tenue (24/11/2026 — à ratifier comme consigne permanente n°10)

1. **Avant d'agir** : `git pull`, puis lire `comptes_rendus/CR_COURANT.md`.
2. **À chaque étape importante** (hotfix appliqué, incident et correction,
   décision de chantier, audit) : rédiger le nouveau `CR_COURANT.md` en
   reprenant les rubriques encore d'actualité, le committer et pousser
   **immédiatement** — message `docs(cr): <sujet>`, commit séparé du commit
   fonctionnel (`fix(...)` / `feat(...)`), qui reste « ensemble » (consigne n°4).
3. **Rien n'est perdu** : chaque version du CR reste dans git ; les CR
   d'emballage détaillés (avec CHECKSUMS) restent stockés dans la zone de
   livraison physique `SAS_DE_VERIFICATION/`, hors git.
4. **Confidentialité** : aucun identifiant, clé, mot de passe ni valeur de
   `config.json` de production dans le CR — valeurs remplacées par `<MASQUÉ>`.
5. **Ne pas recréer d'archive** : ce dossier ne doit contenir que le CR courant
   et ce README.
