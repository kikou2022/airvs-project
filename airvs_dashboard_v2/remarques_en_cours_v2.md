Le `push_preview_wrapper` est un élément **partagé** en dehors des tab-panes — donc si tu fais un prefill depuis l'onglet Animateurs, que le tableau s'affiche, puis que tu bascules sur l'onglet Shazam ou Assisté, **le tableau de l'Animateur reste visible** avec ses données, ses checkboxes, etc. C'est confus car le contexte ne correspond plus à l'onglet actif.

**Quelques pistes pour gérer ça :**

1. **Masquer à chaque changement d'onglet** — le plus simple. Le listener `shown.bs.tab` (déjà en place ligne 7998 pour masquer `push_shared_params` en mode Bulk) pourrait aussi cacher le preview + missing quand l'onglet change. L'utilisateur doit refaire un prefill dans le nouvel onglet.

2. **Conserver avec indicateur de source** — ne pas cacher, mais afficher un badge clair (ex: "📝 Source : Animateurs") et peut-être griser/désactiver les boutons Envoyer si l'onglet actif ne correspond pas. L'utilisateur peut quand même utiliser le tableau.

3. **Buffer par onglet** — stocker les résultats par préfixe (`shazam`, `animateurs`, `sheets`...) et restaurer le bon tableau quand on revient sur l'onglet. Plus complexe mais plus ergonomique.

L'option 1 est la plus logique pour la Phase 3 (on garde le comportement existant, on ajoute juste le masquage). On peut le faire maintenant ou à la toute fin quand tout sera modularisé — ton choix.