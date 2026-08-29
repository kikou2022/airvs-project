// [P3-S4] Phase 3 Étape 4 — Extraction JS couleur (programmation couleur, formulaires, playlists)
/**
 * AIRVS Dashboard - couleur
 * Extrait de index.html (Phase 3, etape 4c)
 * Onglet Programmation Couleur : grille, formulaire, dry-run, historique, stats,
 * import Sheets, playlists picker, selecteur de titres.
 */

(function() {
    "use strict";

    // Alias vers utilitaires globaux (utils.js)
    var escapeHtml = window.escapeHtml;

// ══════════════════════════════════════════════════════
// PROGRAMMATION COULEUR (Phase 1)
// ══════════════════════════════════════════════════════

const couleurGrilleBody = document.getElementById('couleur_grille_body');
const couleurFormCard = document.getElementById('couleur_form_card');
const couleurDryrunCard = document.getElementById('couleur_dryrun_card');
const couleurDryrunBody = document.getElementById('couleur_dryrun_body');
const couleurDryrunStats = document.getElementById('couleur_dryrun_stats');
const couleurLogCard = document.getElementById('couleur_log_card');
const terminalCouleur = document.getElementById('terminal_couleur_output');
const couleurHistoriqueBody = document.getElementById('couleur_historique_body');

let couleurCurrentDryrunId = null;  // ID du créneau actuellement en dry-run
let couleurCurrentInterval = null;  // setInterval pour suivi de tâche
let couleurCurrentTaskId = null;    // ID de la tâche en cours de suivi (pour bouton Détails)
let couleurDernierHistorique = [];  // Phase 2 : cache pour export CSV

// State pour les sélecteurs dynamiques
let couleurCachedDossiers = [];          // [{path, station_id}]
let couleurCachedPlaylists = [];          // [{id, name, ...}] pour la station sélectionnée
let couleurSelectedPlaylistIds = [];      // IDs actuellement sélectionnés (lors de l'édition)
let couleurSelectedTitresIds = [];        // IDs titres sélectionnés (mode type=titre)
let couleurCachedGenres = [];             // [{ID, name}]
let couleurCachedSubcats = [];            // [{ID, name, parentid, parent_name}]
let couleurCachedCats = [];               // [{ID, name}]

const JOURS_COULEUR = ['Tous', 'Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'];


function loadCouleurGrille() {
    fetch('/api/programmation_couleur?t=' + Date.now())
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!Array.isArray(data)) {
                couleurGrilleBody.innerHTML = '<tr><td colspan="12" class="text-danger small p-3">'
                    + (data.message || 'Erreur de chargement') + '</td></tr>';
                return;
            }
            if (data.length === 0) {
                couleurGrilleBody.innerHTML = '<tr><td colspan="12" class="text-center text-muted p-3 small">'
                    + 'Aucun créneau. Cliquez sur "+ Nouveau créneau" pour commencer.'
                    + '</td></tr>';
                return;
            }
            let html = '';
            data.forEach(function(c) {
                let badgeJour = (c.jour_semaine === null || c.jour_semaine === undefined)
                    ? '<span class="badge bg-info">ÉVÉNEMENTIEL</span>'
                    : '<span class="badge bg-secondary">' + (JOURS_COULEUR[c.jour_semaine] || '?') + '</span>';
                let badgeDry = c.dry_run
                    ? '<span class="badge bg-warning text-dark">DRY</span>'
                    : '<span class="badge bg-success">OK</span>';
                let badgeActif = c.actif
                    ? '<span class="badge bg-success">✓</span>'
                    : '<span class="badge bg-danger">✗</span>';
                let dernier = c.dernier_lancement
                    ? new Date(c.dernier_lancement).toLocaleString('fr-FR', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'})
                    : '<span class="text-muted">—</span>';
                let plIds = (c.playlist_ids && c.playlist_ids.length)
                    ? c.playlist_ids.join(',')
                    : '<span class="text-muted">—</span>';
                let typeLabel = {artist:'Artiste', genre:'Genre', subcategory:'Sous-cat', category:'Catégorie', keyword:'Mot-clé', titre:'Titres'}[c.type_couleur] || c.type_couleur;
                let modeLabel = {ajouter_existantes:'Ajouter', creer_nouvelle:'Créer', remplacer:'Remplacer'}[c.mode_playlist] || c.mode_playlist;
                let modeBadge = (c.mode_playlist === 'creer_nouvelle')
                    ? '<span class="badge bg-warning text-dark" title="Créée désactivée (anti 24/7)">' + modeLabel + '</span>'
                    : '<small>' + escapeHtml(modeLabel) + '</small>';
                let eventInfo = (c.date_debut || c.date_fin)
                    ? '<br><small class="text-muted">' + (c.date_debut || '?') + ' → ' + (c.date_fin || '∞') + '</small>'
                    : '';
                html += '<tr>'
                    + '<td>' + c.id + '</td>'
                    + '<td><code>' + (c.heure || '').substring(0, 5) + '</code></td>'
                    + '<td>' + badgeJour + eventInfo + '</td>'
                    + '<td><span class="badge ' + (c.station_id === 6 ? 'bg-info' : 'bg-primary') + '">S' + c.station_id + '</span></td>'
                    + '<td><small>' + escapeHtml(typeLabel) + '</small></td>'
                    + '<td>' + escapeHtml(c.valeur_couleur)
                        + (c.commentaire ? '<br><small class="text-muted">' + escapeHtml(c.commentaire) + '</small>' : '')
                        + '</td>'
                    + '<td><strong>' + c.nb_titres + '</strong></td>'
                    + '<td>' + modeBadge + '<br><small class="text-muted">[' + plIds + ']</small></td>'
                    + '<td>' + badgeDry + '</td>'
                    + '<td>' + badgeActif + '</td>'
                    + '<td><small>' + dernier + '</small></td>'
                    + '<td class="text-nowrap">'
                    + '<button class="btn btn-sm btn-outline-warning btn-couleur-dryrun" data-id="' + c.id + '" title="Prévisualiser">👁</button> '
                    + '<button class="btn btn-sm btn-outline-primary btn-couleur-edit" data-id="' + c.id + '" title="Éditer">✏</button> '
                    + '<button class="btn btn-sm btn-outline-danger btn-couleur-delete" data-id="' + c.id + '" title="Supprimer">🗑</button>'
                    + '</td>'
                    + '</tr>';
            });
            couleurGrilleBody.innerHTML = html;
            // Bind buttons
            couleurGrilleBody.querySelectorAll('.btn-couleur-dryrun').forEach(function(btn) {
                btn.addEventListener('click', function() { lancerCouleurDryRun(parseInt(this.dataset.id)); });
            });
            couleurGrilleBody.querySelectorAll('.btn-couleur-edit').forEach(function(btn) {
                btn.addEventListener('click', function() { ouvrirCouleurForm(parseInt(this.dataset.id)); });
            });
            couleurGrilleBody.querySelectorAll('.btn-couleur-delete').forEach(function(btn) {
                btn.addEventListener('click', function() { supprimerCouleur(parseInt(this.dataset.id)); });
            });
        })
        .catch(function(err) {
            couleurGrilleBody.innerHTML = '<tr><td colspan="12" class="text-danger small p-3">Erreur réseau: ' + err + '</td></tr>';
        });
}

// ── Chargement des caches pour les dropdowns ──
function loadCouleurDossiers() {
    return fetch('/api/azuracast/folders?t=' + Date.now())
        .then(function(r) { return r.json(); })
        .then(function(data) {
            couleurCachedDossiers = (data.folders || data || []);
            let sel = document.getElementById('couleur_form_dossier');
            let currentVal = sel.value;
            sel.innerHTML = '';
            couleurCachedDossiers.forEach(function(f) {
                let path = (typeof f === 'string') ? f : (f.path || f.folder_path);
                if (!path) return;
                let opt = document.createElement('option');
                opt.value = path;
                opt.textContent = path;
                sel.appendChild(opt);
            });
            if (currentVal && Array.from(sel.options).some(function(o){return o.value===currentVal;})) {
                sel.value = currentVal;
            }
        })
        .catch(function(err) { console.warn('loadCouleurDossiers error', err); });
}

function loadCouleurPlaylists(stationId) {
    let url = '/api/azuracast/playlists?station_id=' + encodeURIComponent(stationId) + '&t=' + Date.now();
    return fetch(url)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            couleurCachedPlaylists = (data.playlists || data || []);
        })
        .catch(function(err) {
            console.warn('loadCouleurPlaylists error', err);
            couleurCachedPlaylists = [];
        });
}

function loadCouleurRadiodjRefs() {
    let p1 = fetch('/api/genres?t=' + Date.now()).then(function(r){return r.json();}).then(function(d){
        couleurCachedGenres = Array.isArray(d) ? d : [];
        let sel = document.getElementById('couleur_form_valeur_genre');
        sel.innerHTML = '<option value="">— Sélectionner —</option>';
        couleurCachedGenres.forEach(function(g){
            let o = document.createElement('option');
            o.value = g.name;
            o.textContent = g.name + ' (ID ' + g.ID + ')';
            sel.appendChild(o);
        });
    }).catch(function(){});
    let p2 = fetch('/api/souscategories_all?t=' + Date.now()).then(function(r){return r.json();}).then(function(d){
        couleurCachedSubcats = Array.isArray(d) ? d : [];
        let sel = document.getElementById('couleur_form_valeur_subcat');
        sel.innerHTML = '<option value="">— Sélectionner —</option>';
        couleurCachedSubcats.forEach(function(s){
            let o = document.createElement('option');
            o.value = s.name;
            let label = s.name + ' (ID ' + s.ID + ')';
            if (s.parent_name) label += ' [' + s.parent_name + ']';
            o.textContent = label;
            sel.appendChild(o);
        });
    }).catch(function(){});
    let p3 = fetch('/api/categories?t=' + Date.now()).then(function(r){return r.json();}).then(function(d){
        couleurCachedCats = Array.isArray(d) ? d : [];
        let sel = document.getElementById('couleur_form_valeur_cat');
        sel.innerHTML = '<option value="">— Sélectionner —</option>';
        couleurCachedCats.forEach(function(c){
            let o = document.createElement('option');
            o.value = c.name;
            o.textContent = c.name + ' (ID ' + c.ID + ')';
            sel.appendChild(o);
        });
    }).catch(function(){});
    return Promise.all([p1, p2, p3]);
}

function updateCouleurValeurVisibility() {
    let t = document.getElementById('couleur_form_type').value;
    document.getElementById('couleur_form_valeur_wrap_generic').style.display = (t === 'artist' || t === 'keyword') ? '' : 'none';
    document.getElementById('couleur_form_valeur_wrap_genre').style.display = (t === 'genre') ? '' : 'none';
    document.getElementById('couleur_form_valeur_wrap_subcat').style.display = (t === 'subcategory') ? '' : 'none';
    document.getElementById('couleur_form_valeur_wrap_cat').style.display = (t === 'category') ? '' : 'none';
    document.getElementById('couleur_titres_selector_row').style.display = (t === 'titre') ? '' : 'none';
    let hint = document.getElementById('couleur_nb_hint');
    if (t === 'titre') {
        hint.textContent = 'Tous les titres cochés seront poussés';
        hint.style.color = '#dc3545';
    } else {
        hint.textContent = 'Tirage aléatoire';
        hint.style.color = '';
    }
    let lab = document.getElementById('couleur_form_valeur_label');
    if (t === 'artist') lab.textContent = 'Artiste *';
    else if (t === 'keyword') lab.textContent = 'Mot-clé *';
    else lab.textContent = 'Valeur *';
    document.getElementById('couleur_form_valeur').placeholder = (t === 'artist')
        ? 'Ex: Daft Punk (saisie libre)'
        : (t === 'keyword')
            ? 'Ex: love, summer, radio (cherche dans artist/titre/album)'
            : 'Valeur';
}

function updateCouleurModeHint() {
    let mode = document.getElementById('couleur_form_mode').value;
    let hint = document.getElementById('couleur_mode_hint');
    if (!hint) return;
    if (mode === 'creer_nouvelle') {
        hint.style.display = '';
        hint.innerHTML = '<i class="text-warning d-block">⚠ <strong>PIÈGE 24/7 :</strong> une nouvelle playlist AzuraCast est planifiée '
            + '<strong>H24 7j/7 par défaut</strong>, ce qui ferait tourner vos titres en boucle permanente. '
            + 'Pour éviter cela, la playlist sera créée <strong>DÉSACTIVÉE</strong>. '
            + 'Vous devrez configurer son schedule (heure/poids/jour) puis l\'activer manuellement. '
            + '<br>👉 <strong>Préférez le mode « Ajouter à existantes »</strong> qui enrichit une playlist déjà réglée.</i>';
    } else if (mode === 'remplacer') {
        hint.style.display = '';
        hint.innerHTML = '<i class="text-info d-block">ℹ Le schedule, le type et l\'activation de la playlist cible sont <strong>préservés</strong> '
            + '(seul le contenu est remplacé par les nouveaux titres tirés).</i>';
    } else {
        // ajouter_existantes
        hint.style.display = '';
        hint.innerHTML = '<i class="text-success d-block">✓ <strong>Mode recommandé.</strong> Les titres sont ajoutés aux playlists '
            + 'déjà configurées (schedule, poids, activation conservés). '
            + 'Aucun risque de diffusion 24/7 involontaire.</i>';
    }
}

function updateCouleurPlaylistsSummary() {
    let summary = document.getElementById('couleur_playlists_summary');
    if (!couleurSelectedPlaylistIds.length) {
        summary.textContent = '— Aucune playlist sélectionnée —';
        return;
    }
    let names = couleurSelectedPlaylistIds.map(function(id) {
        let p = couleurCachedPlaylists.find(function(x) { return x.id === id; });
        return p ? (id + ':' + p.name) : ('#' + id);
    });
    summary.textContent = names.join(', ');
}

function renderCouleurPlaylistsPicker() {
    let body = document.getElementById('couleur_playlists_picker_body');
    if (!couleurCachedPlaylists.length) {
        body.innerHTML = '<tr><td colspan="5" class="text-center text-muted p-3 small">Aucune playlist trouvée. Vérifiez le cache playlists.</td></tr>';
        return;
    }
    let filter = (document.getElementById('couleur_playlists_filter').value || '').toLowerCase();
    let html = '';
    couleurCachedPlaylists.forEach(function(p) {
        if (filter && !String(p.name || '').toLowerCase().includes(filter)) return;
        let checked = couleurSelectedPlaylistIds.includes(p.id) ? 'checked' : '';
        html += '<tr>'
            + '<td><input type="checkbox" class="form-check-input couleur-pl-pick" data-id="' + p.id + '" ' + checked + '></td>'
            + '<td>' + p.id + '</td>'
            + '<td>' + escapeHtml(p.name || '') + '</td>'
            + '<td><small>' + escapeHtml(p.type || '') + '</small></td>'
            + '<td><small>' + (p.nb_tracks || p.num_songs || 0) + '</small></td>'
            + '</tr>';
    });
    body.innerHTML = html || '<tr><td colspan="5" class="text-muted p-2 small">Aucun match</td></tr>';
    body.querySelectorAll('.couleur-pl-pick').forEach(function(cb) {
        cb.addEventListener('change', function() {
            let id = parseInt(this.dataset.id);
            if (this.checked) {
                if (!couleurSelectedPlaylistIds.includes(id)) couleurSelectedPlaylistIds.push(id);
            } else {
                couleurSelectedPlaylistIds = couleurSelectedPlaylistIds.filter(function(x){return x !== id;});
            }
            document.getElementById('couleur_playlists_picked_count').textContent = couleurSelectedPlaylistIds.length;
        });
    });
    document.getElementById('couleur_playlists_picked_count').textContent = couleurSelectedPlaylistIds.length;
}

function loadCouleurTitresSearch() {
    let q = document.getElementById('couleur_titres_search').value.trim();
    let url = '/api/radiodj/titres?q=' + encodeURIComponent(q) + '&limit=100&t=' + Date.now();
    fetch(url)
        .then(function(r){return r.json();})
        .then(function(data){
            let body = document.getElementById('couleur_titres_search_body');
            if (!Array.isArray(data)) {
                body.innerHTML = '<tr><td colspan="7" class="text-danger small p-2">Erreur: ' + (data.error || '?') + '</td></tr>';
                return;
            }
            if (!data.length) {
                body.innerHTML = '<tr><td colspan="7" class="text-center text-muted p-2 small">Aucun résultat</td></tr>';
                return;
            }
            let html = '';
            data.forEach(function(t) {
                let checked = couleurSelectedTitresIds.includes(t.ID) ? 'checked' : '';
                let badge = t.in_azura
                    ? '<span class="badge bg-success">IN_AZURA</span>'
                    : '<span class="badge bg-secondary">à copier</span>';
                html += '<tr>'
                    + '<td><input type="checkbox" class="form-check-input couleur-titre-pick" data-id="' + t.ID + '" ' + checked + '></td>'
                    + '<td>' + t.ID + '</td>'
                    + '<td>' + escapeHtml(t.artist || '') + '</td>'
                    + '<td>' + escapeHtml(t.title || '') + '</td>'
                    + '<td class="small">' + escapeHtml(t.album || '—') + '</td>'
                    + '<td>' + (t.year || '—') + '</td>'
                    + '<td>' + badge + '</td>'
                    + '</tr>';
            });
            body.innerHTML = html;
            body.querySelectorAll('.couleur-titre-pick').forEach(function(cb) {
                cb.addEventListener('change', function() {
                    let id = parseInt(this.dataset.id);
                    if (this.checked) {
                        if (!couleurSelectedTitresIds.includes(id)) couleurSelectedTitresIds.push(id);
                    } else {
                        couleurSelectedTitresIds = couleurSelectedTitresIds.filter(function(x){return x !== id;});
                    }
                    document.getElementById('couleur_titres_count').textContent = couleurSelectedTitresIds.length;
                });
            });
            document.getElementById('couleur_titres_count').textContent = couleurSelectedTitresIds.length;
        })
        .catch(function(err){
            document.getElementById('couleur_titres_search_body').innerHTML
                = '<tr><td colspan="7" class="text-danger small p-2">Erreur réseau: ' + err + '</td></tr>';
        });
}

function ouvrirCouleurForm(id) {
    document.getElementById('couleur_form_id').value = id || '';
    document.getElementById('couleur_form_heure').value = '14:00';
    document.getElementById('couleur_form_jour').value = '0';
    document.getElementById('couleur_form_date_debut').value = '';
    document.getElementById('couleur_form_date_fin').value = '';
    document.getElementById('couleur_form_station').value = '7';
    document.getElementById('couleur_form_anti_rep').value = '7';
    document.getElementById('couleur_form_type').value = 'artist';
    document.getElementById('couleur_form_valeur').value = '';
    document.getElementById('couleur_form_valeur_genre').value = '';
    document.getElementById('couleur_form_valeur_subcat').value = '';
    document.getElementById('couleur_form_valeur_cat').value = '';
    document.getElementById('couleur_form_nb').value = '20';
    document.getElementById('couleur_form_mode').value = 'ajouter_existantes';
    document.getElementById('couleur_form_nom_template').value = '';
    document.getElementById('couleur_form_plage_debut').value = '06:00';
    document.getElementById('couleur_form_plage_fin').value = '23:00';
    document.getElementById('couleur_form_commentaire').value = '';
    document.getElementById('couleur_form_dry_run').checked = true;
    document.getElementById('couleur_form_actif').checked = true;
    couleurSelectedPlaylistIds = [];
    couleurSelectedTitresIds = [];
    document.getElementById('couleur_playlists_summary').textContent = '— Aucune playlist sélectionnée —';
    document.getElementById('couleur_titres_count').textContent = '0';
    document.getElementById('couleur_titres_search').value = '';
    document.getElementById('couleur_titres_search_body').innerHTML
        = '<tr><td colspan="7" class="text-center text-muted p-2 small">Saisissez un mot-clé puis cliquez sur 🔍</td></tr>';
    // Cacher et vider le résultat du test pool
    let testPoolResult = document.getElementById('couleur_test_pool_result');
    if (testPoolResult) { testPoolResult.style.display = 'none'; testPoolResult.innerHTML = ''; }
    updateCouleurValeurVisibility();
    updateCouleurModeHint();

    // Lancer tous les chargements de listes EN PARALLÈLE
    // (dossiers, refs RadioDJ, playlists de la station par défaut)
    let pDossiers = loadCouleurDossiers();
    let pRefs = loadCouleurRadiodjRefs();
    let pPlaylists = loadCouleurPlaylists(parseInt(document.getElementById('couleur_form_station').value));

    if (id) {
        document.getElementById('couleur_form_titre').textContent = 'Éditer créneau #' + id;
        // Pour l'édition, on charge d'abord le créneau, PUIS on attend que les
        // dropdowns soient prêts avant d'assigner les valeurs (sinon le select
        // est encore vide et l'assignation .value = X échoue silencieusement).
        fetch('/api/programmation_couleur?t=' + Date.now())
            .then(function(r) { return r.json(); })
            .then(function(data) {
                let c = data.find(function(x) { return x.id === id; });
                if (!c) return;
                // Si la station diffère de celle par défaut, recharger les playlists
                let pPlStation = (c.station_id !== parseInt(document.getElementById('couleur_form_station').value))
                    ? loadCouleurPlaylists(parseInt(c.station_id))
                    : pPlaylists;
                // Attendre que TOUS les dropdowns soient prêts avant de remplir
                return Promise.all([pDossiers, pRefs, pPlStation]).then(function() {
                    return c;
                });
            })
            .then(function(c) {
                if (!c) return;
                document.getElementById('couleur_form_heure').value = (c.heure || '14:00:00').substring(0, 5);
                document.getElementById('couleur_form_jour').value = (c.jour_semaine === null ? '0' : c.jour_semaine);
                document.getElementById('couleur_form_date_debut').value = c.date_debut || '';
                document.getElementById('couleur_form_date_fin').value = c.date_fin || '';
                document.getElementById('couleur_form_station').value = c.station_id;
                document.getElementById('couleur_form_anti_rep').value = c.anti_repetition_jours;
                document.getElementById('couleur_form_type').value = c.type_couleur;
                document.getElementById('couleur_form_valeur').value = c.valeur_couleur;
                document.getElementById('couleur_form_valeur_genre').value = c.valeur_couleur;
                document.getElementById('couleur_form_valeur_subcat').value = c.valeur_couleur;
                document.getElementById('couleur_form_valeur_cat').value = c.valeur_couleur;
                document.getElementById('couleur_form_nb').value = c.nb_titres;
                document.getElementById('couleur_form_mode').value = c.mode_playlist;
                document.getElementById('couleur_form_nom_template').value = c.nom_nouvelle_playlist || '';
                // Pour le dossier, fallback imports_push si la valeur n'est pas dans la liste
                let dossierVal = c.dossier_cible || 'imports_push';
                let selDossier = document.getElementById('couleur_form_dossier');
                let existsDossier = Array.from(selDossier.options).some(function(o){return o.value === dossierVal;});
                if (!existsDossier) {
                    let o = document.createElement('option');
                    o.value = dossierVal;
                    o.textContent = dossierVal + ' (non trouvé dans cache)';
                    selDossier.appendChild(o);
                }
                selDossier.value = dossierVal;
                document.getElementById('couleur_form_plage_debut').value = (c.plage_h_debut || '06:00:00').substring(0, 5);
                document.getElementById('couleur_form_plage_fin').value = (c.plage_h_fin || '23:00:00').substring(0, 5);
                document.getElementById('couleur_form_commentaire').value = c.commentaire || '';
                document.getElementById('couleur_form_dry_run').checked = c.dry_run;
                document.getElementById('couleur_form_actif').checked = c.actif;
                couleurSelectedPlaylistIds = (c.playlist_ids || []).slice();
                couleurSelectedTitresIds = (c.titres_ids || []).slice();
                // Si type=titre, pré-charger le sélecteur avec les IDs déjà sélectionnés
                if (c.type_couleur === 'titre' && couleurSelectedTitresIds.length) {
                    // Suggérer à l'utilisateur de relancer une recherche pour voir les titres
                    document.getElementById('couleur_titres_search').placeholder
                        = couleurSelectedTitresIds.length + ' titre(s) déjà sélectionné(s) — relancez une recherche pour les afficher';
                }
                updateCouleurValeurVisibility();
                updateCouleurModeHint();
                updateCouleurPlaylistsSummary();
                document.getElementById('couleur_titres_count').textContent = couleurSelectedTitresIds.length;
            });
    } else {
        document.getElementById('couleur_form_titre').textContent = 'Nouveau créneau';
    }
    couleurFormCard.style.display = 'block';
    couleurFormCard.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function fermerCouleurForm() {
    couleurFormCard.style.display = 'none';
}

function testerPoolDepuisForm() {
    let typeCouleur = document.getElementById('couleur_form_type').value;
    let valeurCouleur = '';
    if (typeCouleur === 'genre') {
        valeurCouleur = document.getElementById('couleur_form_valeur_genre').value.trim();
    } else if (typeCouleur === 'subcategory') {
        valeurCouleur = document.getElementById('couleur_form_valeur_subcat').value.trim();
    } else if (typeCouleur === 'category') {
        valeurCouleur = document.getElementById('couleur_form_valeur_cat').value.trim();
    } else {
        valeurCouleur = document.getElementById('couleur_form_valeur').value.trim();
    }
    let nbTitres = parseInt(document.getElementById('couleur_form_nb').value) || 20;
    let antiRep = parseInt(document.getElementById('couleur_form_anti_rep').value) || 7;

    if (typeCouleur === 'titre') {
        if (!couleurSelectedTitresIds.length) {
            alert('Type "Titres explicites" : veuillez sélectionner au moins un titre dans la recherche ci-dessous.');
            return;
        }
    } else if (!valeurCouleur) {
        alert('Veuillez renseigner la valeur du critère avant de tester le pool.');
        return;
    }

    let resultDiv = document.getElementById('couleur_test_pool_result');
    resultDiv.style.display = '';
    resultDiv.innerHTML = '<div class="alert alert-info py-2 small mb-0">⏳ Test de la requête en cours…</div>';

    let payload = {
        type_couleur: typeCouleur,
        valeur_couleur: valeurCouleur,
        nb_titres: nbTitres,
        anti_repetition_jours: antiRep,
        skip_anti_repetition: false
    };
    if (typeCouleur === 'titre') {
        payload.titres_ids = couleurSelectedTitresIds.slice();
    }
    fetch('/api/programmation_couleur/test_pool', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.status !== 'ok') {
            resultDiv.innerHTML = '<div class="alert alert-danger py-2 small mb-0">❌ Erreur : '
                + escapeHtml(data.message || 'inconnue') + '</div>';
            return;
        }
        let alertClass = 'alert-success';
        let icon = '✓';
        let msg = '';
        if (data.warning_pool_empty) {
            alertClass = 'alert-danger';
            icon = '❌';
            msg = '<strong>Pool VIDE</strong> — aucun titre ne correspond à ce critère. '
                + 'Vérifiez la valeur (faute d\'orthographe, nom exact RadioDJ, etc.).';
        } else if (data.warning_pool_insuffisant) {
            alertClass = 'alert-warning';
            icon = '⚠';
            msg = '<strong>Pool insuffisant</strong> : seulement ' + data.pool_total + ' titre(s) disponible(s) '
                + 'pour ' + nbTitres + ' demandé(s). Le tirage sera limité.';
        } else {
            msg = '<strong>Pool suffisant</strong> : ' + data.pool_total + ' titre(s) correspondent au critère, '
                + 'dont ' + data.pool_exclus_anti_repetition + ' exclu(s) par anti-répétition (' + antiRep + 'j). '
                + 'Le tirage pourra sélectionner ' + nbTitres + ' titre(s) aléatoirement.';
        }
        let sampleHtml = '';
        if (data.sample_titres && data.sample_titres.length) {
            sampleHtml = '<details class="mt-1"><summary class="small">Voir un échantillon ('
                + data.sample_titres.length + ' titre(s))</summary>'
                + '<div class="mt-1" style="max-height:200px; overflow-y:auto;">'
                + '<table class="table table-sm table-bordered mb-0 small">'
                + '<thead><tr><th>ID</th><th>Artiste</th><th>Titre</th><th>Album</th><th>Année</th><th>AzuraCast</th></tr></thead><tbody>';
            data.sample_titres.forEach(function(t) {
                let badge = t.in_azura
                    ? '<span class="badge bg-success">IN</span>'
                    : '<span class="badge bg-secondary">à copier</span>';
                sampleHtml += '<tr><td>' + t.ID + '</td><td>' + escapeHtml(t.artist || '') + '</td>'
                    + '<td>' + escapeHtml(t.title || '') + '</td>'
                    + '<td>' + escapeHtml(t.album || '—') + '</td>'
                    + '<td>' + (t.year || '—') + '</td>'
                    + '<td>' + badge + '</td></tr>';
            });
            sampleHtml += '</tbody></table></div></details>';
        }
        resultDiv.innerHTML = '<div class="alert ' + alertClass + ' py-2 small mb-0">'
            + icon + ' ' + msg + sampleHtml + '</div>';
    })
    .catch(function(err) {
        resultDiv.innerHTML = '<div class="alert alert-danger py-2 small mb-0">❌ Erreur réseau : '
            + escapeHtml(String(err)) + '</div>';
    });
}

function sauverCouleurForm() {
    let id = document.getElementById('couleur_form_id').value;
    let heure = document.getElementById('couleur_form_heure').value;
    if (!heure) { alert('Heure requise'); return; }
    let heureFull = heure + ':00';
    let typeCouleur = document.getElementById('couleur_form_type').value;
    let valeurCouleur = '';
    if (typeCouleur === 'genre') {
        valeurCouleur = document.getElementById('couleur_form_valeur_genre').value.trim();
    } else if (typeCouleur === 'subcategory') {
        valeurCouleur = document.getElementById('couleur_form_valeur_subcat').value.trim();
    } else if (typeCouleur === 'category') {
        valeurCouleur = document.getElementById('couleur_form_valeur_cat').value.trim();
    } else {
        valeurCouleur = document.getElementById('couleur_form_valeur').value.trim();
    }
    let payload = {
        heure: heureFull,
        jour_semaine: parseInt(document.getElementById('couleur_form_jour').value),
        date_debut: document.getElementById('couleur_form_date_debut').value || null,
        date_fin: document.getElementById('couleur_form_date_fin').value || null,
        station_id: parseInt(document.getElementById('couleur_form_station').value),
        anti_repetition_jours: parseInt(document.getElementById('couleur_form_anti_rep').value),
        type_couleur: typeCouleur,
        valeur_couleur: valeurCouleur,
        nb_titres: parseInt(document.getElementById('couleur_form_nb').value),
        mode_playlist: document.getElementById('couleur_form_mode').value,
        playlist_ids: couleurSelectedPlaylistIds.slice(),
        nom_nouvelle_playlist: document.getElementById('couleur_form_nom_template').value.trim() || null,
        dossier_cible: document.getElementById('couleur_form_dossier').value || 'imports_push',
        plage_h_debut: document.getElementById('couleur_form_plage_debut').value + ':00',
        plage_h_fin: document.getElementById('couleur_form_plage_fin').value + ':00',
        commentaire: document.getElementById('couleur_form_commentaire').value.trim() || null,
        dry_run: document.getElementById('couleur_form_dry_run').checked,
        actif: document.getElementById('couleur_form_actif').checked
    };
    if (typeCouleur === 'titre') {
        payload.titres_ids = couleurSelectedTitresIds.slice();
        if (!payload.titres_ids.length) {
            alert('Type "Titres explicites" : veuillez sélectionner au moins un titre');
            return;
        }
    } else if (!payload.valeur_couleur) {
        alert('Valeur requise pour le type "' + typeCouleur + '"');
        return;
    }
    let method = id ? 'PUT' : 'POST';
    let url = id ? '/api/programmation_couleur/' + id : '/api/programmation_couleur';
    fetch(url, {
        method: method,
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.status === 'ok') {
            fermerCouleurForm();
            loadCouleurGrille();
            // Phase 2 : rafraîchir aussi le dropdown de filtre historique
            // (sinon un nouveau créneau n'apparaît pas sans changer d'onglet)
            refreshCouleurHistoGrilleDropdown();
        } else {
            alert('Erreur: ' + (data.message || 'inconnue'));
        }
    })
    .catch(function(err) { alert('Erreur réseau: ' + err); });
}

function supprimerCouleur(id) {
    if (!confirm('Supprimer le créneau #' + id + ' ?\nL\'historique sera conservé pour traçabilité.')) return;
    fetch('/api/programmation_couleur/' + id, {method: 'DELETE'})
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.status === 'ok') {
                loadCouleurGrille();
                // Phase 2 : rafraîchir le dropdown de filtre historique
                refreshCouleurHistoGrilleDropdown();
            } else {
                alert('Erreur: ' + (data.message || 'inconnue'));
            }
        });
}

function lancerCouleurDryRun(id, skipAntiRep) {
    couleurCurrentDryrunId = id;
    let url = '/api/programmation_couleur/' + id + '/dry_run?t=' + Date.now();
    if (skipAntiRep) url += '&skip_anti_repetition=1';
    fetch(url)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.status !== 'ok') {
                alert('Erreur: ' + (data.message || 'inconnue'));
                return;
            }
            let c = data.creneau;
            document.getElementById('couleur_dryrun_titre').textContent =
                '👁 Dry-run créneau #' + c.id + ' — ' + c.type_couleur + '="' + c.valeur_couleur + '" @ ' + (c.heure || '').substring(0, 5);
            let warning = data.warning_pool_insuffisant
                ? '<div class="alert alert-warning p-2 small">⚠ Pool insuffisant : seulement '
                  + data.nb_selectionnes + ' titre(s) disponible(s) (demandé: ' + c.nb_titres + ')</div>'
                : '';
            couleurDryrunStats.innerHTML =
                '<strong>Pool total:</strong> ' + data.pool_total + ' titre(s) correspondant au critère. '
                + '<strong>Exclus (anti-répétition ' + c.anti_repetition_jours + 'j):</strong> '
                + (data.anti_repetition_active ? data.pool_exclus_anti_repetition + ' titre(s)' : '0 (désactivé)') + '. '
                + '<strong>Sélectionnés:</strong> ' + data.nb_selectionnes + ' / ' + c.nb_titres + '.'
                + warning;

            if (data.titres.length === 0) {
                couleurDryrunBody.innerHTML = '<tr><td colspan="7" class="text-center text-muted p-3 small">Aucun titre trouvé.</td></tr>';
            } else {
                let html = '';
                data.titres.forEach(function(t, i) {
                    let badge = t.in_azura
                        ? '<span class="badge-azura">IN_AZURA</span>'
                        : '<span class="badge bg-secondary">à copier</span>';
                    html += '<tr>'
                        + '<td>' + (i + 1) + '</td>'
                        + '<td>' + t.ID + '</td>'
                        + '<td>' + escapeHtml(t.artist) + '</td>'
                        + '<td>' + escapeHtml(t.title) + '</td>'
                        + '<td class="small">' + escapeHtml(t.album || '—') + '</td>'
                        + '<td>' + (t.year || '—') + '</td>'
                        + '<td>' + badge + '</td>'
                        + '</tr>';
                });
                couleurDryrunBody.innerHTML = html;
            }
            couleurDryrunCard.style.display = 'block';
            couleurDryrunCard.scrollIntoView({behavior: 'smooth', block: 'start'});
        })
        .catch(function(err) { alert('Erreur réseau: ' + err); });
}

function forcerPushCouleur(id, bypassDryRun) {
    let bypass = !!bypassDryRun;
    let confirmMsg = bypass
        ? ('🔥 FORCER UN PUSH RÉEL du créneau #' + id + ' maintenant ?\n\n'
           + '⚠ Cette action IGNORE le mode dry-run et va effectuer une copie SFTP + opération playlist RÉELLE.\n'
           + 'Le worker Ubuntu va traiter la tâche (peut prendre 1-2 min).\n\n'
           + 'Continuer ?')
        : ('Forcer le lancement du créneau #' + id + ' maintenant ?\n'
           + 'Le worker Ubuntu va traiter la tâche (peut prendre 1-2 min).');
    if (!confirm(confirmMsg)) return;
    let opts = {method: 'POST'};
    if (bypass) {
        opts.headers = {'Content-Type': 'application/json'};
        opts.body = JSON.stringify({bypass_dry_run: true});
    }
    fetch('/api/programmation_couleur/' + id + '/forcer_lancement', opts)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.status !== 'ok') {
                alert('Erreur: ' + (data.message || 'inconnue'));
                return;
            }
            let msg = '✓ Tâche #' + data.task_id + ' insérée dans la file d\'attente.';
            if (data.dry_run) {
                msg += '\n⚠ Le créneau est en DRY-RUN — aucun push ne sera effectué, juste un log.';
            } else if (bypass) {
                msg += '\n🔥 PUSH RÉEL forcé (bypass dry-run) — la copie SFTP va être effectuée.';
            }
            alert(msg);
            couleurLogCard.style.display = 'block';
            terminalCouleur.textContent = 'En attente du worker Ubuntu… (tâche #' + data.task_id + ')\n';
            suivreTacheCouleur(data.task_id);
            loadCouleurGrille();
        })
        .catch(function(err) { alert('Erreur réseau: ' + err); });
}

function suivreTacheCouleur(taskId) {
    if (couleurCurrentInterval) clearInterval(couleurCurrentInterval);
    // Mémoriser l'ID de la tâche en cours de suivi pour le bouton "Détails"
    couleurCurrentTaskId = taskId;
    // Réinitialiser le badge + horodatage
    const badgeEl = document.getElementById('couleur_log_status_badge');
    const elapsedEl = document.getElementById('couleur_log_elapsed');
    const startTime = Date.now();
    let hintShown = false;  // pour ne pas spammer l'hint diagnostique

    function updateBadge(statut) {
        const colors = {
            'en_attente': 'bg-warning text-dark',
            'en_cours':   'bg-info text-dark',
            'termine':    'bg-success',
            'erreur':     'bg-danger',
            'introuvable':'bg-secondary',
            'vide':       'bg-secondary',
            'inconnu':    'bg-secondary'
        };
        const labels = {
            'en_attente': '⏳ EN ATTENTE',
            'en_cours':   '🔄 EN COURS',
            'termine':    '✓ TERMINÉ',
            'erreur':     '✗ ERREUR',
            'introuvable':'? INTROUVABLE',
            'vide':       '— VIDE',
            'inconnu':    '— INCONNU'
        };
        if (badgeEl) {
            badgeEl.className = 'badge ms-2 ' + (colors[statut] || 'bg-secondary');
            badgeEl.textContent = labels[statut] || statut;
        }
    }

    function updateElapsed() {
        if (!elapsedEl) return;
        const s = Math.floor((Date.now() - startTime) / 1000);
        if (s < 60) {
            elapsedEl.textContent = '(' + s + 's)';
        } else {
            elapsedEl.textContent = '(' + Math.floor(s/60) + 'm' + (s%60).toString().padStart(2,'0') + 's)';
        }
    }

    couleurCurrentInterval = setInterval(function() {
        fetch('/lire_log_push?task_id=' + taskId)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                updateBadge(data.statut);
                updateElapsed();

                if (data.log) {
                    terminalCouleur.textContent = data.log;
                    terminalCouleur.scrollTop = terminalCouleur.scrollHeight;
                }

                // Hint diagnostique après 15s en attente
                const elapsedSec = Math.floor((Date.now() - startTime) / 1000);
                if (!hintShown && (data.statut === 'en_attente' || data.statut === 'vide')
                    && elapsedSec >= 15) {
                    hintShown = true;
                    let hint = '\n\n══════════════════════════════════════════\n'
                             + '⚠ DIAGNOSTIC — la tâche #' + taskId + ' est restée '
                             + elapsedSec + 's sans être prise en charge.\n'
                             + 'Causes probables :\n'
                             + '  1. Le worker Ubuntu (192.168.1.17) ne tourne pas → '
                             + "ssh sebastien@192.168.1.17 puis 'sudo systemctl status airvs-worker' (ou équivalent).\n"
                             + '  2. Le worker tourne mais avec une VIEILLE version de worker_ubuntu.py '
                             + "qui ne connaît pas le type_action='" + (data.type_action || '?') + "' → redéployez.\n"
                             + '  3. Le worker a planté en lisant le Sheet (credentials.json manquant, '
                             + 'gspread non installé, ID/onglet invalide…) → vérifiez les logs systemd du worker.\n'
                             + '  4. La table taches_planifiees a un lock ou la colonne type_action est mal orthographiée.\n'
                             + '👉 Cliquez sur "📋 Détails" pour voir le JSON brut de la tâche.\n'
                             + '══════════════════════════════════════════';
                    terminalCouleur.textContent = (data.log || '') + hint;
                    terminalCouleur.scrollTop = terminalCouleur.scrollHeight;
                }

                if (data.statut === 'termine' || data.statut === 'erreur') {
                    clearInterval(couleurCurrentInterval);
                    couleurCurrentInterval = null;
                    loadCouleurGrille();
                    loadCouleurHistorique();
                    loadCouleurStats();  // Refresh live des stats après chaque push
                    if (data.statut === 'erreur') {
                        // Afficher une alerte claire pour que l'utilisateur comprenne
                        // qu'il y a eu un échec (sinon le terminal peut être trompeur)
                        let extrait = (data.log || '').split('\n').filter(function(l) {
                            return /ERREUR|erreur|EXCEPTION|fatal/i.test(l);
                        }).slice(-3).join('\n');
                        alert('⚠ La tâche #' + taskId + ' s\'est terminée en ERREUR.\n\n'
                            + (extrait ? 'Derniers messages d\'erreur:\n' + extrait + '\n\n' : '')
                            + 'Consultez le terminal de log ci-dessous pour le détail complet.');
                    } else {
                        // Indiquer visuellement la fin normale
                        let badge = document.createElement('div');
                        badge.className = 'alert alert-success mt-2 py-1 small';
                        badge.textContent = '✓ Tâche #' + taskId + ' terminée avec succès.';
                        terminalCouleur.parentNode.insertBefore(badge, terminalCouleur.nextSibling);
                    }
                }
            })
            .catch(function() {});
    }, 1000);
}

function loadCouleurHistorique() {
    let jours = parseInt(document.getElementById('couleur_histo_jours').value) || 7;
    let station = document.getElementById('couleur_histo_station').value;
    let grille = document.getElementById('couleur_histo_grille').value;
    let q = (document.getElementById('couleur_histo_search').value || '').trim();
    let url = '/api/programmation_couleur/historique?jours=' + encodeURIComponent(jours)
        + '&limit=1000&t=' + Date.now();
    if (station) url += '&station_id=' + encodeURIComponent(station);
    if (grille) url += '&grille_id=' + encodeURIComponent(grille);
    if (q) url += '&q=' + encodeURIComponent(q);
    fetch(url)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!Array.isArray(data)) {
                couleurHistoriqueBody.innerHTML = '<tr><td colspan="8" class="text-danger small p-3">'
                    + (data.message || 'Erreur') + '</td></tr>';
                document.getElementById('couleur_histo_count').textContent = '—';
                couleurDernierHistorique = [];  // reset cache export
                return;
            }
            couleurDernierHistorique = data;  // Phase 2 : cache pour export CSV
            let countEl = document.getElementById('couleur_histo_count');
            if (data.length === 0) {
                couleurHistoriqueBody.innerHTML = '<tr><td colspan="8" class="text-center text-muted p-3 small">'
                    + 'Aucun lancement sur la période/filtres sélectionnés.'
                    + '</td></tr>';
                countEl.textContent = '0 lancement';
                return;
            }
            countEl.textContent = data.length + ' lancement(s) affiché(s)';
            let html = '';
            data.forEach(function(h) {
                let dt = h.date_lancement ? new Date(h.date_lancement).toLocaleString('fr-FR', {day:'2-digit', month:'2-digit', year:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '—';
                let pl = h.playlist_cible || '—';
                let mode = h.mode_playlist || '—';
                let typeLabel = {artist:'Artiste', genre:'Genre', subcategory:'Sous-cat', category:'Catégorie', keyword:'Mot-clé', titre:'Titres'}[h.type_couleur] || h.type_couleur || '';
                let grilleCell = h.grille_id
                    ? '<span title="' + escapeHtml(typeLabel + ' : ' + (h.valeur_couleur || '')) + '">' + h.grille_id + '</span>'
                    : '—';
                html += '<tr>'
                    + '<td><small>' + escapeHtml(dt) + '</small></td>'
                    + '<td>' + grilleCell + '</td>'
                    + '<td>' + (h.song_id || '—') + '</td>'
                    + '<td>' + escapeHtml(h.artist || '—') + '</td>'
                    + '<td>' + escapeHtml(h.title || '—') + '</td>'
                    + '<td><span class="badge ' + (h.station_id === 6 ? 'bg-info' : 'bg-primary') + '">S' + (h.station_id || '?') + '</span></td>'
                    + '<td><small>' + escapeHtml(mode) + '</small></td>'
                    + '<td><small>' + escapeHtml(pl) + '</small></td>'
                    + '</tr>';
            });
            couleurHistoriqueBody.innerHTML = html;
        })
        .catch(function(err) {
            couleurHistoriqueBody.innerHTML = '<tr><td colspan="8" class="text-danger small p-3">Erreur réseau: ' + err + '</td></tr>';
            document.getElementById('couleur_histo_count').textContent = '—';
            couleurDernierHistorique = [];
        });
}

// Phase 2 : export CSV de l'historique actuellement chargé (filtres appliqués)
function exportCouleurHistoriqueCSV() {
    if (!couleurDernierHistorique || !couleurDernierHistorique.length) {
        alert('Aucune donnée à exporter. Cliquez d\'abord sur 🔄 Rafraîchir.');
        return;
    }
    let lignes = [['date_lancement', 'grille_id', 'song_id', 'station_id',
                   'artist', 'title', 'album', 'year',
                   'type_couleur', 'valeur_couleur', 'mode_playlist',
                   'playlist_cible', 'tache_id']];
    couleurDernierHistorique.forEach(function(h) {
        lignes.push([
            h.date_lancement || '',
            h.grille_id || '',
            h.song_id || '',
            h.station_id || '',
            (h.artist || '').replace(/"/g, '""'),
            (h.title || '').replace(/"/g, '""'),
            (h.album || '').replace(/"/g, '""'),
            h.year || '',
            h.type_couleur || '',
            (h.valeur_couleur || '').replace(/"/g, '""'),
            h.mode_playlist || '',
            (h.playlist_cible || '').replace(/"/g, '""'),
            h.tache_id || ''
        ]);
    });
    let csv = lignes.map(function(r) {
        return r.map(function(c) { return '"' + String(c) + '"'; }).join(',');
    }).join('\r\n');
    // BOM pour Excel (UTF-8 correctement reconnu)
    let blob = new Blob(['\ufeff' + csv], {type: 'text/csv;charset=utf-8'});
    let url = URL.createObjectURL(blob);
    let a = document.createElement('a');
    let ts = new Date().toISOString().substring(0, 19).replace(/[:T]/g, '-');
    a.href = url;
    a.download = 'airvs_historique_couleur_' + ts + '.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function refreshCouleurHistoGrilleDropdown() {
    // Remplit le dropdown des créneaux (pour filtre historique) à partir de la grille
    return fetch('/api/programmation_couleur?t=' + Date.now())
        .then(function(r) { return r.json(); })
        .then(function(data) {
            let sel = document.getElementById('couleur_histo_grille');
            let curVal = sel.value;
            sel.innerHTML = '<option value="">Tous</option>';
            if (Array.isArray(data)) {
                data.forEach(function(c) {
                    let typeLabel = {artist:'Artiste', genre:'Genre', subcategory:'Sous-cat', category:'Catégorie', keyword:'Mot-clé', titre:'Titres'}[c.type_couleur] || c.type_couleur;
                    let label = '#' + c.id + ' — ' + (c.heure || '').substring(0, 5) + ' '
                        + typeLabel + ':' + (c.valeur_couleur || '').substring(0, 30);
                    let o = document.createElement('option');
                    o.value = c.id;
                    o.textContent = label;
                    sel.appendChild(o);
                });
            }
            if (curVal) sel.value = curVal;
        })
        .catch(function() {});
}

function loadCouleurStats() {
    let jours = parseInt(document.getElementById('couleur_stats_jours').value) || 7;
    let station = document.getElementById('couleur_stats_station').value;
    let url = '/api/programmation_couleur/stats?jours=' + encodeURIComponent(jours) + '&t=' + Date.now();
    if (station) url += '&station_id=' + encodeURIComponent(station);
    // Reset KPIs
    document.getElementById('couleur_kpi_creneaux_declenches').textContent = '⏳';
    document.getElementById('couleur_kpi_titres_pousses').textContent = '⏳';
    document.getElementById('couleur_kpi_titres_uniques').textContent = '⏳';
    document.getElementById('couleur_kpi_artistes').textContent = '⏳';
    document.getElementById('couleur_kpi_taux_rep').textContent = '⏳';
    document.getElementById('couleur_kpi_taux_rep_hint').textContent = '';
    document.getElementById('couleur_kpi_par_station').textContent = '⏳';
    document.getElementById('couleur_stats_top_titres_body').innerHTML
        = '<tr><td colspan="4" class="text-center text-muted p-2">Chargement…</td></tr>';
    document.getElementById('couleur_stats_par_creneau_body').innerHTML
        = '<tr><td colspan="9" class="text-center text-muted p-2">Chargement…</td></tr>';
    document.getElementById('couleur_stats_par_mode_body').textContent = 'Chargement…';
    document.getElementById('couleur_stats_top_artistes_body').textContent = 'Chargement…';
    document.getElementById('couleur_stats_dist_chart').innerHTML = '<div class="text-center text-muted p-4">Chargement…</div>';

    fetch(url)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.status !== 'ok') {
                let msg = data.message || 'Erreur';
                document.getElementById('couleur_kpi_creneaux_declenches').textContent = '❌';
                document.getElementById('couleur_kpi_titres_pousses').textContent = '❌';
                document.getElementById('couleur_kpi_titres_uniques').textContent = '❌';
                document.getElementById('couleur_stats_top_titres_body').innerHTML
                    = '<tr><td colspan="4" class="text-danger small p-2">' + escapeHtml(msg) + '</td></tr>';
                return;
            }
            // ── KPIs ──
            // KPIs clarifiés : on privilégie les nouveaux champs, avec fallback rétrocompat.
            let k = data.kpis;
            let nbCreneauxDeclenches = (k.nb_creneaux_declenches !== undefined)
                ? k.nb_creneaux_declenches : '—';
            let nbTitresPousses = (k.nb_titres_pousses !== undefined)
                ? k.nb_titres_pousses : k.nb_lancements;
            document.getElementById('couleur_kpi_creneaux_declenches').textContent = nbCreneauxDeclenches;
            document.getElementById('couleur_kpi_titres_pousses').textContent = nbTitresPousses;
            document.getElementById('couleur_kpi_titres_uniques').textContent = k.nb_titres_uniques;
            document.getElementById('couleur_kpi_artistes').textContent = k.nb_artistes_uniques;
            // Ratio titres par créneau (info utile dans le hint)
            let ratioHint = '';
            if (typeof nbTitresPousses === 'number' && typeof nbCreneauxDeclenches === 'number' && nbCreneauxDeclenches > 0) {
                let moy = (nbTitresPousses / nbCreneauxDeclenches).toFixed(1);
                ratioHint = '~' + moy + ' titres / créneau';
            }
            document.getElementById('couleur_kpi_titres_pousses_hint').textContent = ratioHint || 'total lignes';
            let txRep = k.taux_repetition_pct;
            let txRepEl = document.getElementById('couleur_kpi_taux_rep');
            txRepEl.textContent = txRep + '%';
            txRepEl.className = 'h4 mb-0 ' + (txRep < 30 ? 'text-success' : (txRep < 60 ? 'text-warning' : 'text-danger'));
            let hint = '';
            if (typeof nbTitresPousses === 'number' && nbTitresPousses > 0) {
                let repetes = nbTitresPousses - k.nb_titres_uniques;
                hint = repetes + ' titre(s) répété(s)';
            }
            document.getElementById('couleur_kpi_taux_rep_hint').textContent = hint;

            // ── Par station ──
            let stationsHtml = '';
            if (data.par_station && data.par_station.length) {
                data.par_station.forEach(function(s) {
                    let couleur = s.station_id === 6 ? 'bg-info' : 'bg-primary';
                    stationsHtml += '<span class="badge ' + couleur + ' me-1">S' + s.station_id
                        + ': ' + s.nb_lancements + ' / ' + s.nb_titres_uniques + 'u</span>';
                });
            } else {
                stationsHtml = '<span class="text-muted">—</span>';
            }
            document.getElementById('couleur_kpi_par_station').innerHTML = stationsHtml;

            // ── Top titres ──
            let topBody = document.getElementById('couleur_stats_top_titres_body');
            if (!data.top_titres || !data.top_titres.length) {
                topBody.innerHTML = '<tr><td colspan="4" class="text-center text-muted p-2">Aucun titre</td></tr>';
                document.getElementById('couleur_stats_top_titres_hint').textContent = '';
            } else {
                let maxPushes = data.top_titres[0].nb_pushes;
                document.getElementById('couleur_stats_top_titres_hint').textContent
                    = 'max: ' + maxPushes + ' pushes';
                let html = '';
                data.top_titres.slice(0, 20).forEach(function(t, i) {
                    let ratio = maxPushes > 0 ? (t.nb_pushes / maxPushes * 100) : 0;
                    let barColor = ratio > 66 ? 'bg-danger' : (ratio > 33 ? 'bg-warning' : 'bg-success');
                    let bar = '<div class="progress" style="height:4px;">'
                        + '<div class="progress-bar ' + barColor + '" style="width:' + ratio + '%;"></div>'
                        + '</div>';
                    let dt = t.dernier_push
                        ? new Date(t.dernier_push).toLocaleString('fr-FR', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'})
                        : '—';
                    html += '<tr>'
                        + '<td>' + (i + 1) + '</td>'
                        + '<td>' + escapeHtml(t.artist || '?') + ' — ' + escapeHtml(t.title || '?')
                            + '<br>' + bar + '</td>'
                        + '<td class="text-end"><strong>' + t.nb_pushes + '</strong></td>'
                        + '<td><small>' + dt + '</small></td>'
                        + '</tr>';
                });
                topBody.innerHTML = html;
            }

            // ── Distribution journalière (mini bar chart en HTML/CSS) ──
            let distChart = document.getElementById('couleur_stats_dist_chart');
            let distHint = document.getElementById('couleur_stats_dist_hint');
            if (!data.distribution_journaliere || !data.distribution_journaliere.length) {
                distChart.innerHTML = '<div class="text-center text-muted p-4">Aucune donnée sur la période</div>';
                distHint.textContent = '';
            } else {
                let maxLanc = 1;
                data.distribution_journaliere.forEach(function(d) {
                    if (d.nb_lancements > maxLanc) maxLanc = d.nb_lancements;
                });
                let barsPerRow = data.distribution_journaliere.length;
                let cellWidth = Math.max(20, Math.floor(700 / barsPerRow));
                let html = '<div style="display:flex; align-items:flex-end; gap:2px; height:180px; padding:4px; border-bottom:1px solid #dee2e6;">';
                data.distribution_journaliere.forEach(function(d) {
                    let hLanc = Math.round(d.nb_lancements / maxLanc * 100);
                    let hUniques = (d.nb_titres_uniques > 0)
                        ? Math.round(d.nb_titres_uniques / maxLanc * 100)
                        : 0;
                    let dateCourte = (d.date || '').substring(5); // MM-DD
                    html += '<div style="flex:1; min-width:' + cellWidth + 'px; position:relative; height:100%; display:flex; flex-direction:column; justify-content:flex-end; align-items:center;">'
                        + '<div title="' + dateCourte + ': ' + d.nb_lancements + ' lanc. / ' + d.nb_titres_uniques + ' uniques" '
                        + 'style="width:80%; height:' + hLanc + '%; background:#17a2b8; min-height:2px; border-radius:2px 2px 0 0;"></div>'
                        + '<div title="' + dateCourte + ': ' + d.nb_titres_uniques + ' titres uniques" '
                        + 'style="width:80%; height:' + hUniques + '%; background:#28a745; min-height:2px;"></div>'
                        + '<div class="small text-muted" style="font-size:9px; margin-top:2px;">' + dateCourte + '</div>'
                        + '</div>';
                });
                html += '</div>';
                html += '<div class="small text-muted mt-1">'
                    + '<span style="display:inline-block; width:10px; height:10px; background:#17a2b8; margin-right:3px;"></span>Lancements '
                    + '<span style="display:inline-block; width:10px; height:10px; background:#28a745; margin-right:3px;"></span>Titres uniques'
                    + '</div>';
                distChart.innerHTML = html;
                let totalLanc = 0, totalUniq = 0;
                data.distribution_journaliere.forEach(function(d) {
                    totalLanc += d.nb_lancements;
                    totalUniq += d.nb_titres_uniques;
                });
                distHint.textContent = 'Total: ' + totalLanc + ' lancements / ' + totalUniq + ' titres uniques';
            }

            // ── Par créneau ──
            let parCreneauBody = document.getElementById('couleur_stats_par_creneau_body');
            if (!data.par_creneau || !data.par_creneau.length) {
                parCreneauBody.innerHTML = '<tr><td colspan="9" class="text-center text-muted p-2">Aucun créneau actif sur la période</td></tr>';
            } else {
                let html = '';
                data.par_creneau.forEach(function(c) {
                    let txClass = c.taux_repetition_pct < 30 ? 'text-success'
                                : (c.taux_repetition_pct < 60 ? 'text-warning' : 'text-danger');
                    let dt = c.dernier_lancement
                        ? new Date(c.dernier_lancement).toLocaleString('fr-FR', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'})
                        : '—';
                    let typeLabel = {artist:'Artiste', genre:'Genre', subcategory:'Sous-cat', category:'Catégorie', keyword:'Mot-clé', titre:'Titres'}[c.type_couleur] || c.type_couleur;
                    html += '<tr>'
                        + '<td>' + (c.grille_id || '—') + '</td>'
                        + '<td><code>' + (c.heure || '').substring(0, 5) + '</code></td>'
                        + '<td><span class="badge ' + (c.station_id === 6 ? 'bg-info' : 'bg-primary') + '">S' + (c.station_id || '?') + '</span></td>'
                        + '<td><small>' + escapeHtml(typeLabel || '') + '</small></td>'
                        + '<td>' + escapeHtml(c.valeur_couleur || '') + '</td>'
                        + '<td class="text-end"><strong>' + c.nb_lancements + '</strong></td>'
                        + '<td class="text-end">' + c.nb_titres_uniques + '</td>'
                        + '<td class="text-end ' + txClass + '"><strong>' + c.taux_repetition_pct + '%</strong></td>'
                        + '<td><small>' + dt + '</small></td>'
                        + '</tr>';
                });
                parCreneauBody.innerHTML = html;
            }

            // ── Par mode ──
            let modeBody = document.getElementById('couleur_stats_par_mode_body');
            if (!data.par_mode || !data.par_mode.length) {
                modeBody.innerHTML = '<span class="text-muted">—</span>';
            } else {
                let total = 0;
                data.par_mode.forEach(function(m) { total += m.nb_lancements; });
                let html = '';
                data.par_mode.forEach(function(m) {
                    let pct = total > 0 ? Math.round(m.nb_lancements / total * 100) : 0;
                    let couleur = m.mode_playlist === 'ajouter_existantes' ? 'bg-success'
                                : (m.mode_playlist === 'creer_nouvelle' ? 'bg-warning text-dark' : 'bg-info');
                    let label = {ajouter_existantes:'Ajouter', creer_nouvelle:'Créer', remplacer:'Remplacer'}[m.mode_playlist] || m.mode_playlist;
                    html += '<div class="mb-1">'
                        + '<span class="badge ' + couleur + ' me-1">' + escapeHtml(label) + '</span>'
                        + '<small>' + m.nb_lancements + ' lanc. (' + pct + '%) / ' + m.nb_titres_uniques + ' uniques</small>'
                        + '</div>';
                });
                modeBody.innerHTML = html;
            }

            // ── Top artistes ──
            let topArtBody = document.getElementById('couleur_stats_top_artistes_body');
            if (!data.top_artistes || !data.top_artistes.length) {
                topArtBody.innerHTML = '<span class="text-muted">—</span>';
            } else {
                let html = '';
                data.top_artistes.slice(0, 5).forEach(function(a, i) {
                    html += '<div class="small mb-1">'
                        + '<span class="badge bg-secondary me-1">' + (i + 1) + '</span>'
                        + escapeHtml(a.artist || '?')
                        + ' <small class="text-muted">(' + a.nb_pushes + ' / ' + a.nb_titres_distincts + ' titres)</small>'
                        + '</div>';
                });
                topArtBody.innerHTML = html;
            }
        })
        .catch(function(err) {
            document.getElementById('couleur_kpi_creneaux_declenches').textContent = '❌';
            document.getElementById('couleur_kpi_titres_pousses').textContent = '❌';
            document.getElementById('couleur_kpi_titres_uniques').textContent = '❌';
            document.getElementById('couleur_stats_top_titres_body').innerHTML
                = '<tr><td colspan="4" class="text-danger small p-2">Erreur réseau: ' + escapeHtml(String(err)) + '</td></tr>';
        });
}

// ── Bind des boutons de l'onglet Couleur ──
document.getElementById('btn_couleur_nouveau').addEventListener('click', function() { ouvrirCouleurForm(null); });

// Charge la liste des Sheets Google (aliases depuis config.json) pour le <select>
// du modal d'import — évite les erreurs de saisie manuelle d'alias/ID.
function loadCouleurImportSheetsList() {
    let sel = document.getElementById('couleur_import_sheets_id');
    if (!sel) return;
    fetch('/api/sheets/list?t=' + Date.now())
        .then(function(r) { return r.json(); })
        .then(function(data) {
            sel.innerHTML = '';
            if (!data.sheets || data.sheets.length === 0) {
                sel.innerHTML = '<option value="">-- config.json vide/absent --</option>';
                return;
            }
            sel.innerHTML = '<option value="">-- Choisir un alias --</option>';
            data.sheets.forEach(function(s) {
                let opt = document.createElement('option');
                opt.value = s.alias;
                opt.textContent = s.alias + '  (' + s.sheet_id.substring(0, 8) + '…)';
                sel.appendChild(opt);
            });
            // Option fallback pour saisir un ID brut non listé dans config.json
            let other = document.createElement('option');
            other.value = '__OTHER__';
            other.textContent = 'Autre (ID brut)…';
            sel.appendChild(other);
        })
        .catch(function(err) {
            sel.innerHTML = '<option value="">-- Erreur chargement --</option>';
            console.error('loadCouleurImportSheetsList:', err);
        });
}
loadCouleurImportSheetsList();

// Gère l'affichage du champ "ID brut" si l'utilisateur choisit "Autre"
document.getElementById('couleur_import_sheets_id').addEventListener('change', function() {
    let wrap = document.getElementById('couleur_import_sheets_other_wrap');
    wrap.style.display = (this.value === '__OTHER__') ? 'block' : 'none';
});

document.getElementById('btn_couleur_import_sheets').addEventListener('click', function() {
    // Ouvrir le modal d'import
    let modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('couleurImportSheetsModal'));
    document.getElementById('couleur_import_result').style.display = 'none';
    // Recharger la liste à chaque ouverture (au cas où config.json ait changé)
    loadCouleurImportSheetsList();
    modal.show();
});
document.getElementById('btn_couleur_import_sheets_submit').addEventListener('click', function() {
    let selVal = document.getElementById('couleur_import_sheets_id').value.trim();
    let onglet = document.getElementById('couleur_import_onglet').value.trim();
    let dryRun = document.getElementById('couleur_import_dry_run').checked;
    let resultDiv = document.getElementById('couleur_import_result');

    // Résoudre la valeur finale de sheets_id : si "Autre" → prendre l'ID brut saisi
    let sheetsId = selVal;
    if (selVal === '__OTHER__') {
        sheetsId = (document.getElementById('couleur_import_sheets_other').value || '').trim();
    }
    if (!sheetsId || !onglet) {
        resultDiv.style.display = 'block';
        resultDiv.className = 'alert alert-warning small';
        resultDiv.textContent = 'Veuillez renseigner le Sheet et l\'onglet.';
        return;
    }
    resultDiv.style.display = 'block';
    resultDiv.className = 'alert alert-info small';
    resultDiv.textContent = '⏳ Création de la tâche d\'import…';
    fetch('/api/programmation_couleur/import_sheets', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({sheets_id: sheetsId, onglet: onglet, dry_run_import: dryRun})
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.status === 'ok') {
            // 1) Fermer le modal pour libérer la vue
            let modalEl = document.getElementById('couleurImportSheetsModal');
            let modalInst = bootstrap.Modal.getInstance(modalEl);
            if (modalInst) modalInst.hide();

            // 2) Révéler le terminal de log et suivre la tâche en direct
            couleurLogCard.style.display = 'block';
            terminalCouleur.textContent = 'En attente du worker Ubuntu… (tâche #' + data.task_id + ')\n'
                + '  Sheet : ' + sheetsId + '\n'
                + '  Onglet: ' + onglet + '\n'
                + '  Mode  : ' + (dryRun ? 'DRY-RUN' : 'ÉCRITURE') + '\n';
            couleurLogCard.scrollIntoView({behavior: 'smooth', block: 'start'});
            suivreTacheCouleur(data.task_id);

            // 3) Laisser un message transitoire dans le resultDiv au cas où l'utilisateur rouvre le modal
            resultDiv.className = 'alert alert-success small';
            resultDiv.innerHTML = '✓ Tâche #' + data.task_id + ' créée. '
                + 'Journal en direct ci-dessous.';
        } else {
            resultDiv.className = 'alert alert-danger small';
            resultDiv.textContent = '✗ ' + (data.message || 'Erreur');
        }
    })
    .catch(function(err) {
        resultDiv.className = 'alert alert-danger small';
        resultDiv.textContent = '✗ Erreur réseau: ' + err;
    });
});
document.getElementById('btn_couleur_form_close').addEventListener('click', fermerCouleurForm);
document.getElementById('btn_couleur_form_cancel').addEventListener('click', fermerCouleurForm);
document.getElementById('btn_couleur_form_save').addEventListener('click', sauverCouleurForm);
document.getElementById('btn_couleur_form_test_pool').addEventListener('click', testerPoolDepuisForm);
document.getElementById('btn_couleur_dryrun_close').addEventListener('click', function() { couleurDryrunCard.style.display = 'none'; });
document.getElementById('btn_couleur_dryrun_regenerer').addEventListener('click', function() {
    if (couleurCurrentDryrunId) lancerCouleurDryRun(couleurCurrentDryrunId, false);
});
document.getElementById('btn_couleur_dryrun_skip_anti_rep').addEventListener('click', function() {
    if (couleurCurrentDryrunId) lancerCouleurDryRun(couleurCurrentDryrunId, true);
});
document.getElementById('btn_couleur_dryrun_push').addEventListener('click', function() {
    if (couleurCurrentDryrunId) forcerPushCouleur(couleurCurrentDryrunId, false);
});
document.getElementById('btn_couleur_dryrun_push_real').addEventListener('click', function() {
    if (couleurCurrentDryrunId) forcerPushCouleur(couleurCurrentDryrunId, true);
});
document.getElementById('btn_couleur_refresh_historique').addEventListener('click', loadCouleurHistorique);
document.getElementById('btn_couleur_histo_export_csv').addEventListener('click', exportCouleurHistoriqueCSV);
document.getElementById('btn_couleur_log_close').addEventListener('click', function() {
    couleurLogCard.style.display = 'none';
    if (couleurCurrentInterval) { clearInterval(couleurCurrentInterval); couleurCurrentInterval = null; }
});
document.getElementById('btn_couleur_log_debug').addEventListener('click', function() {
    if (!couleurCurrentTaskId) {
        alert('Aucune tâche en cours de suivi.');
        return;
    }
    fetch('/api/tache_debug/' + couleurCurrentTaskId)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.status === 'ok') {
                document.getElementById('couleur_debug_json').textContent =
                    JSON.stringify(data.tache, null, 2);
                let modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('couleurDebugTacheModal'));
                modal.show();
            } else {
                alert('Erreur: ' + (data.message || 'inconnue'));
            }
        })
        .catch(function(err) { alert('Erreur réseau: ' + err); });
});

// ── Bindings pour les stats (Phase 2) ──
document.getElementById('btn_couleur_refresh_stats').addEventListener('click', loadCouleurStats);
document.getElementById('couleur_stats_jours').addEventListener('change', loadCouleurStats);
document.getElementById('couleur_stats_station').addEventListener('change', loadCouleurStats);

// ── Bindings pour les filtres historique ──
document.getElementById('couleur_histo_jours').addEventListener('change', loadCouleurHistorique);
document.getElementById('couleur_histo_station').addEventListener('change', loadCouleurHistorique);
document.getElementById('couleur_histo_grille').addEventListener('change', loadCouleurHistorique);
document.getElementById('couleur_histo_search').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') { e.preventDefault(); loadCouleurHistorique(); }
});
document.getElementById('btn_couleur_histo_reset').addEventListener('click', function() {
    document.getElementById('couleur_histo_jours').value = '7';
    document.getElementById('couleur_histo_station').value = '';
    document.getElementById('couleur_histo_grille').value = '';
    document.getElementById('couleur_histo_search').value = '';
    loadCouleurHistorique();
});

// ── Bindings spécifiques aux nouveaux sélecteurs ──
// Changement de type_couleur → afficher/masquer les champs correspondants
document.getElementById('couleur_form_type').addEventListener('change', updateCouleurValeurVisibility);
// Changement de mode playlist → avertir pour creer_nouvelle
document.getElementById('couleur_form_mode').addEventListener('change', updateCouleurModeHint);
// Changement de station → recharger les playlists (pas les dossiers, car union S6/S7)
document.getElementById('couleur_form_station').addEventListener('change', function() {
    let sid = parseInt(document.getElementById('couleur_form_station').value);
    loadCouleurPlaylists(sid).then(updateCouleurPlaylistsSummary);
});

// Picker de playlists (modal)
document.getElementById('btn_couleur_playlists_picker').addEventListener('click', function() {
    let sid = parseInt(document.getElementById('couleur_form_station').value);
    // Recharger les playlists pour être sûr d'être à jour
    loadCouleurPlaylists(sid).then(function() {
        renderCouleurPlaylistsPicker();
        // Ouvrir le modal via Bootstrap 5
        let modalEl = document.getElementById('couleurPlaylistsPickerModal');
        let modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        modal.show();
    });
});
document.getElementById('couleur_playlists_filter').addEventListener('input', renderCouleurPlaylistsPicker);
document.getElementById('btn_couleur_playlists_uncheck_all').addEventListener('click', function() {
    couleurSelectedPlaylistIds = [];
    renderCouleurPlaylistsPicker();
});
document.getElementById('btn_couleur_playlists_validate').addEventListener('click', function() {
    updateCouleurPlaylistsSummary();
    let modalEl = document.getElementById('couleurPlaylistsPickerModal');
    let modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.hide();
});

// Sélecteur de titres (recherche large)
document.getElementById('btn_couleur_titres_search').addEventListener('click', loadCouleurTitresSearch);
document.getElementById('couleur_titres_search').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') { e.preventDefault(); loadCouleurTitresSearch(); }
});
document.getElementById('btn_couleur_titres_clear').addEventListener('click', function() {
    couleurSelectedTitresIds = [];
    document.getElementById('couleur_titres_count').textContent = '0';
    document.getElementById('couleur_titres_search').value = '';
    // Décocher toutes les cases visibles
    document.querySelectorAll('.couleur-titre-pick').forEach(function(cb) { cb.checked = false; });
});

// ── Chargement initial quand l'onglet est activé ──
// (surveillance via shown.bs.tab plus haut)
document.querySelectorAll('button[data-bs-toggle="tab"], .sb-link[data-target]').forEach(function(tab) {
    tab.addEventListener('shown.bs.tab', function(e) {
        if (e.target.id === 'nav-couleur-tab') {
            loadCouleurGrille();
            loadCouleurHistorique();
            refreshCouleurHistoGrilleDropdown();
            loadCouleurStats();
        }
    });
});
})();
