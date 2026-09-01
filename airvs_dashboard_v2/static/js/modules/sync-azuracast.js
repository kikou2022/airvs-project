// PATCH 01/09/2026 v2 — Sécurité frontend (ne pas écraser dossiers si résultat suspect)
// + Message « Scan worker en cours… » (le bouton délègue maintenant au worker qui scanne SSHFS)
// [P3-S4] Phase 3 Étape 4 — Extraction JS sync-azuracast
/**
 * sync-azuracast.js — Module AzuraCast
 * Extrait de index.html (Phase 3 Step 4)
 *
 * Dépendances (window globals) :
 *   window.escapeHtml, window.escapeAttr, window.formatDuration,
 *   window.showToast, window.copierTexteSansClipboardAPI,
 *   window.loadCategories, window.loadSubcategories  (utils.js)
 *   window.formatDurationLong, window.rechercheEnCours, window.refreshExplorateur
 */
(function() {

    // ── Aliases vers les modules externes ──
    var escapeHtml = window.escapeHtml;
    var escapeAttr = window.escapeAttr;
    var formatDuration = window.formatDuration;
    var showToast = window.showToast;
    var copierTexteSansClipboardAPI = window.copierTexteSansClipboardAPI;
    var loadCategories = window.loadCategories;
    var loadSubcategories = window.loadSubcategories;
    // formatDurationLong : défini dans le monolithe (DOMContentLoaded), avec fallback inline
    function formatDurationLong(totalSeconds) {
        if (window.formatDurationLong) return window.formatDurationLong(totalSeconds);
        if (!totalSeconds || isNaN(totalSeconds) || totalSeconds <= 0) return '0:00';
        var mins = Math.floor(totalSeconds / 60);
        var secs = Math.floor(totalSeconds % 60);
        return mins + ':' + (secs < 10 ? '0' : '') + secs;
    }
    // refreshExplorateur est appelé plus tard (après DOMContentLoaded) → accès via window au moment de l'appel

    // ── Colonnes disponibles par onglet (définit lesquelles sont pertinentes) ──
    var COLONNES_PAR_ONGLET = {
        sheets:     { id: false, annee: true, album: true, bpm: true, duree: true, note: true, poids: true, play: true, ajoute: true, azuracast: true, shazam: true },
        shazam:     { id: false, annee: true, album: true, bpm: true, duree: true, note: false, poids: false, play: false, ajoute: false, azuracast: true, shazam: true },
        animateurs: { id: false, annee: true, album: true, bpm: true, duree: true, note: false, poids: false, play: false, ajoute: false, azuracast: true, shazam: true },
        assiste:    { id: false, annee: true, album: true, bpm: true, duree: true, note: false, poids: false, play: false, ajoute: false, azuracast: true, shazam: true },
        sql:        { id: false, annee: true, album: true, bpm: true, duree: true, note: false, poids: false, play: false, ajoute: false, azuracast: true, shazam: false },
        m3u:        { id: false, annee: true, album: true, bpm: true, duree: true, note: false, poids: false, play: false, ajoute: false, azuracast: true, shazam: false }
    };

    // Libellé dynamique pour la colonne shazam selon l'onglet
    var COL_SHAZAM_LABEL = {
        sheets: 'Shazam', shazam: 'Shazam', animateurs: 'Source',
        assiste: 'Shazam', sql: 'Shazam', m3u: 'Shazam'
    };

    // ── PushBuffer : buffer par onglet (spéc Buffer par onglet) ──
    function PushBuffer(key) {
        this.key = key;
        this.selectedFiles = [];
        this.previewHTML = '';
        this.statsText = '';
        this.missingHTML = '';
        this.missingCountText = '';
        this.pushLogText = '';
        this.playlistLogText = '';
        this.pushTaskId = null;
        this.playlistTaskIds = [];
        this.playlistAccumLog = '';
        this.isPushing = false;
        this.isPlaylistPending = false;
        this.intervalPush = null;
        this.intervalPlaylist = null;
        // Initialiser colonnesVisibles depuis le profil de l'onglet
        var profil = COLONNES_PAR_ONGLET[key];
        this.colonnesVisibles = profil ? Object.assign({}, profil) : {
            id: false, annee: true, album: true, bpm: true, duree: true,
            note: false, poids: false, play: false, ajoute: false,
            azuracast: true, shazam: true
        };
    }

    var pushBuffers = {
        assiste: new PushBuffer('assiste'),
        sql: new PushBuffer('sql'),
        m3u: new PushBuffer('m3u'),
        sheets: new PushBuffer('sheets'),
        shazam: new PushBuffer('shazam'),
        animateurs: new PushBuffer('animateurs')
    };
    // Exposer globalement pour accès depuis index.html (populatePushPreviewFromSheet)
    window.pushBuffers = pushBuffers;

    function getActiveBufferKey() {
        if (getPushMode() === 'bulk') return null;
        return getPushMode();
    }

    function getActiveBuffer() {
        var key = getActiveBufferKey();
        return key ? pushBuffers[key] : null;
    }

    function getBufferKeyFromTabId(tabId) {
        var map = {
            'push-assiste-tab': 'assiste',
            'push-sql-tab': 'sql',
            'push-m3u-tab': 'm3u',
            'push-sheets-tab': 'sheets',
            'push-shazam-tab': 'shazam',
            'push-animateurs-tab': 'animateurs'
        };
        return map[tabId] || null;
    }

    // ── Toggle colonnes ──
    var TOGGLABLE_COLS = ['id','annee','album','bpm','duree','note','poids','play','ajoute','azuracast','shazam'];

    function saveColonneStateToBuffer(buffer) {
        if (!buffer) return;
        var state = {};
        document.querySelectorAll('.push-col-toggle').forEach(function(cb) {
            state[cb.dataset.col] = cb.checked;
        });
        buffer.colonnesVisibles = state;
    }

    function restoreColonneStateFromBuffer(buffer) {
        if (!buffer || !buffer.colonnesVisibles) return;
        // Mettre à jour les checkboxes du dropdown
        document.querySelectorAll('.push-col-toggle').forEach(function(cb) {
            var col = cb.dataset.col;
            if (buffer.colonnesVisibles.hasOwnProperty(col)) {
                cb.checked = buffer.colonnesVisibles[col];
            }
        });
        // Appliquer les classes sur le thead et le tbody
        _applyColonnesFromBuffer(buffer);
    }

    // Appliquer les col-hidden sur le th et les td du tbody à partir du buffer
    function _applyColonnesFromBuffer(buffer) {
        if (!buffer || !buffer.colonnesVisibles) return;
        TOGGLABLE_COLS.forEach(function(col) {
            var visible = buffer.colonnesVisibles[col] !== false;
            _toggleColElements(col, visible);
        });
    }

    // Adapter le dropdown de toggles au contexte de l'onglet actif :
    // - désactiver (disabled + unchecked) les colonnes non pertinentes
    // - renommer le label « Shazam » → « Source » pour Animateurs
    function adaptColumnToggleToTab(tabKey) {
        var profil = COLONNES_PAR_ONGLET[tabKey];
        var label = COL_SHAZAM_LABEL[tabKey] || 'Shazam';
        document.querySelectorAll('.push-col-toggle').forEach(function(cb) {
            var col = cb.dataset.col;
            if (!profil || !profil.hasOwnProperty(col)) {
                cb.disabled = true;
                cb.checked = false;
            } else {
                cb.disabled = false;
                // checked sera restauré par restoreColonneStateFromBuffer
            }
            // Renommer dynamiquement le label Shazam
            if (col === 'shazam') {
                var labelEl = cb.parentElement.querySelector('small');
                if (labelEl) labelEl.textContent = label;
            }
        });
        // Renommer aussi le <th> header
        var srcHeader = document.getElementById('push_preview_header_source');
        if (srcHeader) srcHeader.textContent = label;
    }

    function _toggleColElements(col, visible) {
        // Toggle sur le <th>
        var th = document.querySelector('th[data-col="' + col + '"]');
        if (th) {
            if (visible) th.classList.remove('col-hidden');
            else th.classList.add('col-hidden');
        }
        // Toggle sur tous les <td> de cette colonne
        document.querySelectorAll('td[data-col="' + col + '"]').forEach(function(td) {
            if (visible) td.classList.remove('col-hidden');
            else td.classList.add('col-hidden');
        });
    }

    // ── Cache des colonnes optionnelles détectées par le backend ──
    var _colonnesDisponiblesCache = null;

    // Mapping clé logique → éléments DOM associés (filtres + toggle checkboxes)
    var _COL_OPT_ELEMENTS = {
        sm_weight:  { filtre: 'push_poids_min',       toggle: 'poids' },
        sm_rating:  { filtre: 'push_note_min',        toggle: 'note' },
        play_count: { filtre: 'push_play_count_mode',  toggle: 'play' },
        date_played:{ filtre: 'push_pas_diffuse_jours',toggle: null }  // pas de colonne dédiée, filtre seulement
    };

    function _adapterColonnesDisponibles(colonnes) {
        if (!colonnes || typeof colonnes !== 'object') return;
        _colonnesDisponiblesCache = colonnes;

        // Mapping clé logique → options de tri
        var triOptions = {
            sm_weight:  { desc: 'opt_tri_poids_desc',  asc: 'opt_tri_poids_asc' },
            sm_rating:  { desc: 'opt_tri_note_desc',   asc: 'opt_tri_note_asc' },
            play_count: { desc: 'opt_tri_play_desc',   asc: 'opt_tri_play_asc' }
        };

        for (var cle in _COL_OPT_ELEMENTS) {
            var disponible = !!colonnes[cle];
            var elems = _COL_OPT_ELEMENTS[cle];
            var realName = colonnes[cle] || '';

            // Masquer/afficher le filtre associé
            if (elems.filtre) {
                var filtreEl = document.getElementById(elems.filtre);
                if (filtreEl) {
                    var wrapper = filtreEl.closest('.col-6, .col-md-6, .col-auto, div');
                    if (wrapper) wrapper.style.display = disponible ? '' : 'none';
                }
            }

            // Désactiver/activer la checkbox du toggle ⚙
            if (elems.toggle) {
                var cb = document.querySelector('.push-col-toggle[data-col="' + elems.toggle + '"]');
                if (cb) {
                    cb.disabled = !disponible;
                    cb.closest('li').style.display = disponible ? '' : 'none';
                    if (!disponible) {
                        cb.checked = false;
                        _toggleColElements(elems.toggle, false);
                    }
                }
            }

            // Activer/masquer les options de tri
            if (triOptions[cle] && disponible) {
                var descOpt = document.getElementById(triOptions[cle].desc);
                var ascOpt = document.getElementById(triOptions[cle].asc);
                if (descOpt) { descOpt.disabled = false; descOpt.value = 's.' + realName + ' DESC'; }
                if (ascOpt)  { ascOpt.disabled = false;  ascOpt.value  = 's.' + realName + ' ASC'; }
            }
        }
    }

    function initColToggle() {
        document.querySelectorAll('.push-col-toggle').forEach(function(cb) {
            cb.addEventListener('change', function() {
                var col = this.dataset.col;
                _toggleColElements(col, this.checked);
                // Sauvegarder dans le buffer actif
                saveColonneStateToBuffer(getActiveBuffer());
            });
        });
    }

    function saveBufferToDOM(buffer) {
        if (!buffer) return;
        saveColonneStateToBuffer(buffer);
        var _pbb = document.getElementById('push_preview_body');
        var _ps = document.getElementById('push_preview_stats');
        var _tp = document.getElementById('terminal_push_output');
        var _tpl = document.getElementById('terminal_playlist_output');
        var _psa = document.getElementById('push_select_all');
        var _psab = document.getElementById('push_select_all_bottom');
        var _mw = document.getElementById('push_missing_wrapper');
        var _mc = document.getElementById('push_missing_count');
        var _ml = document.getElementById('push_missing_list');
        var _lw = document.getElementById('push_log_wrapper');
        var _plw = document.getElementById('playlist_log_wrapper');
        var _pw = document.getElementById('push_preview_wrapper');

        buffer.previewHTML = _pbb ? _pbb.innerHTML : '';
        buffer.statsText = _ps ? _ps.textContent : '';
        buffer.pushLogText = _tp ? _tp.textContent : '';
        buffer.playlistLogText = _tpl ? _tpl.textContent : '';
        buffer.selectedFiles = getCurrentSelectedFilesFromCheckboxes();
        if (_psa) buffer._selectAllChecked = _psa.checked;
        if (_psab) buffer._selectAllBottomChecked = _psab.checked;
        if (_mw) buffer._missingWrapperDisplay = _mw.style.display;
        if (_mc) buffer.missingCountText = _mc.textContent;
        if (_ml) buffer.missingHTML = _ml.innerHTML;
        if (_lw) buffer._logWrapperDisplay = _lw.style.display;
        if (_plw) buffer._playlistLogWrapperDisplay = _plw.style.display;
        if (_pw) buffer._previewWrapperDisplay = _pw.style.display;

        // Sauvegarder l'etat du push en cours et pauser les intervals
        buffer.pushTaskId = currentPushTaskId;
        buffer.intervalPush = intervalPush;
        if (intervalPush) { clearInterval(intervalPush); intervalPush = null; }
        buffer.intervalPlaylist = intervalPlaylist;
        if (intervalPlaylist) { clearInterval(intervalPlaylist); intervalPlaylist = null; }
    }

    function restoreBufferFromDOM(buffer) {
        if (!buffer) return;
        var _pbb = document.getElementById('push_preview_body');
        var _ps = document.getElementById('push_preview_stats');
        var _tp = document.getElementById('terminal_push_output');
        var _tpl = document.getElementById('terminal_playlist_output');
        var _psa = document.getElementById('push_select_all');
        var _psab = document.getElementById('push_select_all_bottom');
        var _mw = document.getElementById('push_missing_wrapper');
        var _mc = document.getElementById('push_missing_count');
        var _ml = document.getElementById('push_missing_list');
        var _lw = document.getElementById('push_log_wrapper');
        var _plw = document.getElementById('playlist_log_wrapper');
        var _pw = document.getElementById('push_preview_wrapper');

        if (_pbb) _pbb.innerHTML = buffer.previewHTML || '';
        if (_ps) _ps.textContent = buffer.statsText || '';
        if (_tp) _tp.textContent = buffer.pushLogText || '';
        if (_tpl) _tpl.textContent = buffer.playlistLogText || '';
        if (_psa) _psa.checked = !!buffer._selectAllChecked;
        if (_psab) _psab.checked = !!buffer._selectAllBottomChecked;
        if (_mc) _mc.textContent = buffer.missingCountText || '';
        if (_ml) _ml.innerHTML = buffer.missingHTML || '';
        if (_mw) _mw.style.display = buffer._missingWrapperDisplay || 'none';
        if (_lw) _lw.style.display = buffer._logWrapperDisplay || 'none';
        if (_plw) _plw.style.display = buffer._playlistLogWrapperDisplay || 'none';
        if (_pw) _pw.style.display = buffer._previewWrapperDisplay || 'none';

        // Restaurer l'etat des colonnes visibles
        restoreColonneStateFromBuffer(buffer);

        // Re-attacher les evenements sur les checkboxes restaurees
        rebindPushCheckboxes();
        updatePushCount();

        // Restaurer l'etat du push en cours et reprendre les intervals
        currentPushTaskId = buffer.pushTaskId;
        currentPlaylistTaskId = null;  // sera restaure via playlistTaskIds si besoin
        pendingPlaylistTaskIds = buffer.playlistTaskIds ? buffer.playlistTaskIds.slice() : [];
        playlistAccumulatedLog = buffer.playlistAccumLog || '';

        if (buffer.isPushing && buffer.pushTaskId) {
            intervalPush = setInterval(fetchLogPush, 500);
        }
        if (buffer.isPlaylistPending && buffer.playlistTaskIds && buffer.playlistTaskIds.length > 0) {
            currentPlaylistTaskId = pendingPlaylistTaskIds.length > 0 ? pendingPlaylistTaskIds.shift() : null;
            intervalPlaylist = setInterval(fetchLogPlaylist, 500);
            surveillerFinPlaylist();
        }
    }

    function getCurrentSelectedFilesFromCheckboxes() {
        var files = [];
        document.querySelectorAll('.push-checkbox:not(:disabled):checked').forEach(function(cb) {
            files.push({
                path: cb.dataset.path,
                artist: cb.dataset.artist,
                title: cb.dataset.title,
                inAzura: cb.dataset.inAzura === 'true'
            });
        });
        return files;
    }

    function rebindPushCheckboxes() {
        // Les listeners sur .push-checkbox sont attaches via un delegue sur document (deja existant)
        // Pas besoin de re-attacher individuellement.
    }

    function updateBufferBadge(key) {
        var bar = document.getElementById('push_buffer_bar');
        var badge = document.getElementById('push_buffer_badge');
        if (!bar || !badge) return;
        if (!key) {
            bar.style.display = 'none';
            return;
        }
        var labels = {
            assiste: 'Assiste', sql: 'SQL', m3u: 'M3U',
            sheets: 'Google Sheet', shazam: 'Shazam', animateurs: 'Animateurs'
        };
        badge.textContent = labels[key] || key;
        bar.style.display = 'flex';
    }

    // ── Compatibilite window.* avec le monolithe ──
    // window.pushSelectedFiles est un getter/setter qui delegue au buffer actif
    Object.defineProperty(window, 'pushSelectedFiles', {
        get: function() { var b = getActiveBuffer(); return b ? b.selectedFiles : []; },
        set: function(v) { var b = getActiveBuffer(); if (b) b.selectedFiles = v; }
    });
    // window.pushPreviewPrefix reste une variable normale (utilisee par shazam-animateurs.js)
    window.pushPreviewPrefix = null;

    // ── Etat partage avec le monolithe (accessible via window) ──
    window.actionPlaylistsLoaded = false;
    window.pushPlaylistsLoaded = false;

// ── Synchronisation complète AzuraCast (Maintenance) ──
document.getElementById('btn_sync_azura_full').addEventListener('click', function() {
    let btn = this;
    let terminal = document.getElementById('terminal_sync_azura');
    let statusDiv = document.getElementById('sync_azura_status');
    btn.disabled = true;
    btn.innerHTML = '⏳ Envoi au worker...';
    terminal.textContent = 'Lancement de la synchronisation complète...\n';
    statusDiv.textContent = '';

    // Récupérer le choix de l'utilisateur pour les stations
    let stationChoice = document.getElementById('sync_azura_station_select').value;
    let station_ids;
    if (stationChoice === 'all') {
        // Laisser le backend résoudre "toutes les stations" (par défaut si aucun station_ids)
        station_ids = null;
    } else {
        station_ids = [parseInt(stationChoice)];
    }

    // Afficher le choix utilisateur dans le terminal
    let choixLabel = (stationChoice === 'all')
        ? 'TOUTES les stations configurées (résolution backend)'
        : 'Station ' + stationChoice + ' uniquement';
    terminal.textContent += '▸ Choix utilisateur : ' + choixLabel + '\n';
    terminal.textContent += '▸ Payload envoyé : ' + JSON.stringify(station_ids ? { station_ids: station_ids } : {}) + '\n';

    // Étape 1 : Sync badges IN_AZURACAST
    let payload1 = station_ids ? { station_ids: station_ids } : {};
    fetch('/api/sync_azura_status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload1)
    })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            // ── Diagnostic : afficher les stations configurées et résolues ──
            if (data.stations_configurees) {
                let noms = data.stations_configurees.map(function(s) {
                    return 'S' + s.id + ' (' + s.nom + ')';
                }).join(', ');
                terminal.textContent += '▸ Stations configurées côté Windows : ' + noms + '\n';
            }
            if (data.station_ids) {
                terminal.textContent += '▸ Stations résolues pour la sync : [' + data.station_ids.join(', ') + ']\n';
            }
            if (data.warning) {
                terminal.textContent += data.warning + '\n';
                console.warn('[Sync AzuraCast]', data.warning);
            }

            if (data.status === 'ok' && data.task_id) {
                btn.innerHTML = '⏳ Sync badges en cours...';
                terminal.textContent += '── Étape 1/3 : Synchronisation des badges IN_AZURACAST ──\n';
                let syncInterval = setInterval(function() {
                    fetch('/lire_log_push?task_id=' + data.task_id)
                        .then(function(r2) { return r2.json(); })
                        .then(function(logData) {
                            if (logData.log) {
                                terminal.textContent = '── Étape 1/3 : Synchronisation des badges IN_AZURACAST ──\n' + logData.log;
                            }
                            if (logData.statut === 'termine') {
                                clearInterval(syncInterval);
                                terminal.textContent += '\n✅ Badges IN_AZURACAST synchronisés.\n';
                                statusDiv.textContent = '✅ Badges synchronisés';
                                lancerEtape2Playlists(btn, terminal, statusDiv, station_ids);
                            } else if (logData.statut === 'erreur') {
                                clearInterval(syncInterval);
                                terminal.textContent += '\n❌ Erreur lors de la sync des badges.\n';
                                statusDiv.textContent = '❌ Erreur badges';
                                lancerEtape2Playlists(btn, terminal, statusDiv, station_ids);
                            }
                        });
                }, 3000);
            } else if (data.status === 'ok') {
                terminal.textContent += 'Sync badges déjà en attente.\n';
                statusDiv.textContent = '⏳ Sync badges en attente';
                lancerEtape2Playlists(btn, terminal, statusDiv, station_ids);
            } else {
                terminal.textContent += '❌ Erreur : ' + (data.message || 'Inconnue') + '\n';
                btn.disabled = false;
                btn.innerHTML = '🔄 Sync complète AzuraCast';
            }
        })
        .catch(function(err) {
            terminal.textContent += '❌ Erreur réseau : ' + err + '\n';
            btn.disabled = false;
            btn.innerHTML = '🔄 Sync complète AzuraCast';
        });
});

function lancerEtape2Playlists(btn, terminal, statusDiv, station_ids) {
    // Étape 2 : Sync cache playlists
    terminal.textContent += '\n── Étape 2/3 : Synchronisation des playlists ──\n';
    btn.innerHTML = '⏳ Sync playlists...';
    let payload2 = station_ids ? { station_ids: station_ids } : {};
    fetch('/api/azuracast/sync_cache', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload2)
    })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.status === 'ok' && data.task_id) {
                let tentatives = 0;
                let pollInterval = setInterval(function() {
                    tentatives++;
                    fetch('/lire_log_copie?task_id=' + data.task_id)
                        .then(function(r2) { return r2.json(); })
                        .then(function(taskData) {
                            if (taskData.statut === 'termine' || taskData.statut === 'erreur' || tentatives >= 30) {
                                clearInterval(pollInterval);
                                terminal.textContent += '✅ Playlists synchronisées.\n';
                                statusDiv.textContent = '✅ Badges + Playlists OK';
                                lancerEtape3Dossiers(btn, terminal, statusDiv, station_ids);
                            }
                        })
                        .catch(function() {
                            if (tentatives >= 30) {
                                clearInterval(pollInterval);
                                lancerEtape3Dossiers(btn, terminal, statusDiv, station_ids);
                            }
                        });
                }, 2000);
            } else {
                terminal.textContent += '⚠ Erreur sync playlists, passage à l\'étape suivante.\n';
                lancerEtape3Dossiers(btn, terminal, statusDiv, station_ids);
            }
        })
        .catch(function() {
            terminal.textContent += '⚠ Erreur réseau playlists, passage à l\'étape suivante.\n';
            lancerEtape3Dossiers(btn, terminal, statusDiv, station_ids);
        });
}

function lancerEtape3Dossiers(btn, terminal, statusDiv, station_ids) {
    // Étape 3 : Rechargement des dossiers + playlists dans l'interface
    terminal.textContent += '\n── Étape 3/3 : Rafraîchissement de l\'interface ──\n';
    btn.innerHTML = '⏳ Rafraîchissement...';
    loadActionFolders();
    window.actionPlaylistsLoaded = false;
    loadActionPlaylists();
    window.pushPlaylistsLoaded = false;
    loadPushPlaylists();
    if (typeof chargerPlaylistsCache === 'function') chargerPlaylistsCache();
    // Rafraîchir l'Explorateur
    if (rechercheEnCours) {
        document.getElementById('btn_search').click();
    } else {
        window.refreshExplorateur();
    }
    terminal.textContent += '✅ Dossiers, playlists et badges mis à jour dans l\'interface.\n\n';
    terminal.textContent += '═══════════════════════════════════\n';
    terminal.textContent += '  SYNCHRONISATION COMPLÈTE TERMINÉE\n';
    terminal.textContent += '═══════════════════════════════════\n';
    statusDiv.textContent = '✅ Synchronisation complète terminée';
    btn.disabled = false;
    btn.innerHTML = '🔄 Sync complète AzuraCast';
    // Rafraîchir aussi le diagnostic (au cas où config.json a été corrigé)
    if (typeof loadSyncAzuraDiagnostic === 'function') loadSyncAzuraDiagnostic();
}

// ════════════════════════════════════
// EXPLORATEUR : Panneau d'action AzuraCast
// ════════════════════════════════════
let actionTrack = null;
let actionLogInterval = null;

// Ouvrir le panneau d'action au clic sur un bouton d'action
document.addEventListener('click', function(e) {
    let btn = e.target.closest('.action-btn');
    if (!btn) return;
    actionTrack = {
        ID: btn.dataset.id,
        artist: btn.dataset.artist,
        title: btn.dataset.title,
        path: btn.dataset.path,
        inAzura: btn.dataset.inAzura === 'true'
    };
    document.getElementById('action_track_label').textContent = actionTrack.artist + ' — ' + actionTrack.title;
    if (actionTrack.inAzura) {
        document.getElementById('action_track_badge').innerHTML = '<span class="badge-azura">IN_AZURACAST</span>';
        try {
            let tabTrigger = new bootstrap.Tab(document.querySelector('#actionTabs button[data-bs-target="#action-playlist"]'));
            tabTrigger.show();
        } catch(_e) { /* bootstrap.Tab indisponible, l'onglet par défaut reste actif */ }
    } else {
        document.getElementById('action_track_badge').textContent = '(pas encore dans AzuraCast)';
        try {
            let tabTrigger = new bootstrap.Tab(document.querySelector('#actionTabs button[data-bs-target="#action-copie"]'));
            tabTrigger.show();
        } catch(_e) { /* bootstrap.Tab indisponible, l'onglet par défaut reste actif */ }
    }
    document.getElementById('explorer_action_panel').style.display = 'block';
    document.getElementById('explorer_action_panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    // Charger les dossiers et playlists si pas encore fait
    loadActionFolders();
    loadActionPlaylists();
});

// Fermer le panneau
document.getElementById('btn_close_action_panel').addEventListener('click', function() {
    document.getElementById('explorer_action_panel').style.display = 'none';
});

// Charger les dossiers AzuraCast dans les select
// Récupère l'ID de station actuellement sélectionné dans le header du Pont
function getPushStationId() {
    let sel = document.getElementById('push_station_cible');
    if (!sel) return 7;  // Fallback
    return parseInt(sel.value) || 7;
}

// Récupère l'ID de station sélectionné dans le panneau "Action AzuraCast" (explorateur).
// Indépendant du sélecteur du Pont RadioDJ → l'utilisateur peut cibler une station
// différente dans chaque section (explorateur vs pont).
function getActionStationId() {
    let sel = document.getElementById('action_station_cible');
    if (!sel) return getPushStationId();  // Fallback : utiliser la station du Pont
    return parseInt(sel.value) || 7;
}

function loadActionFolders() {
    let sid = getActionStationId();
    fetch('/api/azuracast/folders?station_id=' + sid)
        .then(function(r) { return r.json(); })
        .then(function(folders) {
            if (!Array.isArray(folders)) return;
            // Sécurité (310826) : ne pas remplacer la liste si le résultat est suspect
            var existingCount = 0;
            ['action_dossier_cible', 'action_playlist_dossier'].forEach(function(id) {
                var el = document.getElementById(id);
                if (el) existingCount = Math.max(existingCount, el.options.length);
            });
            if (folders.length <= 1 && existingCount > 3) {
                console.warn('[loadActionFolders] Résultat suspect (' + folders.length + ' vs ' + existingCount + ' existants) — liste conservée.');
                return;
            }
            let html = '<option value="">-- Sélectionner un dossier --</option>';
            // Grouper par niveau pour affichage indenté
            folders.forEach(function(f) {
                let depth = f.split('/').length - 1;
                let indent = '\u00A0\u00A0'.repeat(depth);
                let arrow = depth > 0 ? '└ ' : '';
                html += '<option value="' + f + '">' + indent + arrow + f + '</option>';
            });
            html += '<option value="__new_folder__">+ Nouveau dossier…</option>';
            // Remplir UNIQUEMENT les selects du panneau Action AzuraCast (explorateur)
            ['action_dossier_cible', 'action_playlist_dossier'].forEach(function(id) {
                var el = document.getElementById(id);
                if (el) el.innerHTML = html;
            });
            document.getElementById('action_folders_hint').textContent = folders.length + ' dossier(s) connu(s) — union S6/S7 (station cible : ' + sid + ')';
            // Sélectionner "imports_push" par défaut si présent
            var defaut = 'imports_push';
            ['action_dossier_cible', 'action_playlist_dossier'].forEach(function(id) {
                var el = document.getElementById(id);
                if (el) {
                    var opts = Array.from(el.options).map(function(o) { return o.value; });
                    if (opts.indexOf(defaut) >= 0) el.value = defaut;
                }
            });
        })
        .catch(function(err) {
            console.warn('[loadActionFolders] Erreur réseau/API:', err);
        });
}

// Charge les dossiers AzuraCast POUR LE PONT RadioDJ (indépendant du panneau Action).
// Met à jour uniquement push_dossier_cible en utilisant la station du Pont.
function loadPushFolders() {
    let sid = getPushStationId();
    fetch('/api/azuracast/folders?station_id=' + sid)
        .then(function(r) { return r.json(); })
        .then(function(folders) {
            if (!Array.isArray(folders)) return;
            // Sécurité (310826) : ne pas remplacer si résultat suspect
            var el = document.getElementById('push_dossier_cible');
            if (folders.length <= 1 && el && el.options.length > 3) {
                console.warn('[loadPushFolders] Résultat suspect (' + folders.length + ' vs ' + el.options.length + ' existants) — liste conservée.');
                return;
            }
            let html = '<option value="">-- Sélectionner un dossier --</option>';
            folders.forEach(function(f) {
                let depth = f.split('/').length - 1;
                let indent = '\u00A0\u00A0'.repeat(depth);
                let arrow = depth > 0 ? '└ ' : '';
                html += '<option value="' + f + '">' + indent + arrow + f + '</option>';
            });
            html += '<option value="__new_folder__">+ Nouveau dossier…</option>';
            if (el) {
                el.innerHTML = html;
                var opts = Array.from(el.options).map(function(o) { return o.value; });
                if (opts.indexOf('imports_push') >= 0) el.value = 'imports_push';
            }
        })
        .catch(function(err) {
            console.warn('[loadPushFolders] Erreur réseau/API:', err);
        });
}

// Afficher/masquer le champ "Nouveau dossier" selon la sélection
function bindNewFolderToggle(selectId, colId, inputId) {
    var sel = document.getElementById(selectId);
    if (!sel) return;
    sel.addEventListener('change', function() {
        var col = document.getElementById(colId);
        var inp = document.getElementById(inputId);
        if (this.value === '__new_folder__') {
            if (col) col.style.display = '';
            if (inp) inp.focus();
        } else {
            if (col) col.style.display = 'none';
            if (inp) inp.value = '';
        }
    });
}
bindNewFolderToggle('action_dossier_cible', 'action_new_folder_col', 'action_new_folder_name');
bindNewFolderToggle('action_playlist_dossier', 'action_playlist_new_folder_col', 'action_playlist_new_folder_name');
bindNewFolderToggle('push_dossier_cible', 'push_new_folder_row', 'push_new_folder_name');

// Récupérer le dossier effectif (existant ou nouveau saisi)
function getDossierCible(selectId, inputId) {
    var sel = document.getElementById(selectId);
    var inp = document.getElementById(inputId);
    if (sel && sel.value === '__new_folder__' && inp) {
        return inp.value.trim() || '';
    }
    return sel ? sel.value.trim() : '';
}

// Charger les playlists AzuraCast dans les dropdowns (3 selects action + 3 selects push)
function loadActionPlaylists() {
    if (window.actionPlaylistsLoaded) return;
    let sid = getActionStationId();
    fetch('/api/azuracast/playlists?station_id=' + sid)
        .then(function(r) { return r.json(); })
        .then(function(playlists) {
            if (!Array.isArray(playlists)) return;
            let html = '<option value="">-- Sélectionner --</option>';
            playlists.forEach(function(pl) {
                html += '<option value="' + pl.id + '">' + pl.name + ' (' + pl.nb_tracks + ' titres)</option>';
            });
            html += '<option value="__new__">+ Créer une nouvelle playlist</option>';
            document.getElementById('action_playlist_select_1').innerHTML = html;
            // Les selects 2 et 3 n'ont pas l'option "nouvelle"
            let html2 = '<option value="">-- Aucune --</option>';
            playlists.forEach(function(pl) {
                html2 += '<option value="' + pl.id + '">' + pl.name + ' (' + pl.nb_tracks + ' titres)</option>';
            });
            document.getElementById('action_playlist_select_2').innerHTML = html2;
            document.getElementById('action_playlist_select_3').innerHTML = html2;
            window.actionPlaylistsLoaded = true;
        });
}

// Collecter les IDs de playlists sélectionnées (action rapide)
function getActionPlaylistIds() {
    let ids = [];
    let v1 = document.getElementById('action_playlist_select_1').value;
    let v2 = document.getElementById('action_playlist_select_2').value;
    let v3 = document.getElementById('action_playlist_select_3').value;
    if (v1 && v1 !== '__new__') ids.push(parseInt(v1));
    if (v2 && v2 !== '__new__') ids.push(parseInt(v2));
    if (v3 && v3 !== '__new__') ids.push(parseInt(v3));
    return ids;
}

// Quand on choisit "Créer nouvelle playlist"
document.getElementById('action_playlist_select_1').addEventListener('change', function() {
    if (this.value === '__new__') {
        document.getElementById('action_new_playlist_name').focus();
    }
});

// Action : Copier vers AzuraCast
document.getElementById('btn_action_copier').addEventListener('click', function() {
    if (!actionTrack) return;
    let dossier = getDossierCible('action_dossier_cible', 'action_new_folder_name') || 'imports_push';
    // Si déjà IN_AZURACAST, pas besoin de copier
    if (actionTrack.inAzura) {
        alert('Ce titre est déjà dans AzuraCast. Utilisez le bouton ➕ pour l\'ajouter à une playlist.');
        return;
    }
    fetch('/lancer_push_azura', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            fichiers: [{ path: actionTrack.path, artist: actionTrack.artist, title: actionTrack.title }],
            dossier_cible: dossier,
            creer_playlist: false,
            station_id: getActionStationId()
        })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.task_id) {
            surveillerActionLog(data.task_id);
        } else if (data.message) {
            alert('Erreur : ' + data.message);
        }
    });
});

// Action : Ajouter à une ou plusieurs playlists existantes
document.getElementById('btn_action_ajouter_playlist').addEventListener('click', function() {
    if (!actionTrack) return;
    let playlistIds = getActionPlaylistIds();
    let newPlaylistName = document.getElementById('action_new_playlist_name').value.trim();
    let dossier = getDossierCible('action_playlist_dossier', 'action_playlist_new_folder_name') || 'imports_push';

    if (playlistIds.length === 0 && !newPlaylistName) {
        alert('Sélectionnez au moins une playlist ou saisissez un nouveau nom.');
        return;
    }
    if (!actionTrack.inAzura) {
        if (confirm('Ce titre n\'est pas encore dans AzuraCast.\n\nVoulez-vous le copier vers AzuraCast ET l\'ajouter aux playlists en une seule opération ?')) {
            document.getElementById('btn_action_copier_et_playlist').click();
        }
        return;
    }

    // Titre déjà dans AzuraCast → ajouter aux playlists sélectionnées
    let fichiers_deja_azura = [{ path: actionTrack.path, artist: actionTrack.artist, title: actionTrack.title }];

    if (newPlaylistName && playlistIds.length === 0) {
        // Créer une nouvelle playlist + ajouter le titre
        let payload = {
            dossier_cible: dossier,
            creer_playlist: true,
            nom_playlist: newPlaylistName,
            playlist_type: 'default',
            playlist_order: 'shuffle',
            fichiers: [],
            fichiers_deja_azura: fichiers_deja_azura,
            station_id: getActionStationId()
        };
        fetch('/lancer_push_azura', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            let plIds = data.playlist_task_ids || (data.playlist_task_id ? [data.playlist_task_id] : []);
            if (data.task_id || plIds.length > 0) {
                surveillerActionLog(data.task_id, plIds);
            }
        });
    } else {
        // Ajouter à une ou plusieurs playlists existantes
        let payload = {
            dossier_cible: dossier,
            creer_playlist: true,
            playlist_ids: playlistIds,
            nom_playlist: newPlaylistName || '',
            playlist_type: 'default',
            playlist_order: 'shuffle',
            fichiers: [],
            fichiers_deja_azura: fichiers_deja_azura,
            station_id: getActionStationId()
        };
        fetch('/lancer_push_azura', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            let plIds = data.playlist_task_ids || (data.playlist_task_id ? [data.playlist_task_id] : []);
            if (data.task_id || plIds.length > 0) {
                surveillerActionLog(data.task_id, plIds);
            }
        });
    }
});

// Action : Copier + Ajouter à playlists
document.getElementById('btn_action_copier_et_playlist').addEventListener('click', function() {
    if (!actionTrack) return;
    let playlistIds = getActionPlaylistIds();
    let newPlaylistName = document.getElementById('action_new_playlist_name').value.trim();
    let dossier = getDossierCible('action_playlist_dossier', 'action_playlist_new_folder_name') || 'imports_push';

    if (playlistIds.length === 0 && !newPlaylistName) {
        alert('Sélectionnez au moins une playlist ou saisissez un nouveau nom.');
        return;
    }

    let payload = {
        dossier_cible: dossier,
        creer_playlist: true,
        playlist_ids: playlistIds,
        nom_playlist: newPlaylistName || '',
        playlist_type: 'default',
        playlist_order: 'shuffle',
        station_id: getActionStationId()
    };

    if (actionTrack.inAzura) {
        payload.fichiers = [];
        payload.fichiers_deja_azura = [{ path: actionTrack.path, artist: actionTrack.artist, title: actionTrack.title }];
    } else {
        payload.fichiers = [{ path: actionTrack.path, artist: actionTrack.artist, title: actionTrack.title }];
        payload.fichiers_deja_azura = [];
    }

    fetch('/lancer_push_azura', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        let plIds = data.playlist_task_ids || (data.playlist_task_id ? [data.playlist_task_id] : []);
        if (data.task_id || plIds.length > 0) {
            surveillerActionLog(data.task_id, plIds);
        } else if (data.message) {
            alert('Erreur : ' + data.message);
        }
    });
});

// Surveiller le journal d'action
// playlistTaskIds : tableau d'IDs de tâches playlist à surveiller séquentiellement
function surveillerActionLog(taskId, playlistTaskIds) {
    document.getElementById('action_log_wrapper').style.display = 'block';
    let terminal = document.getElementById('terminal_action_output');

    // Normaliser en tableau
    if (!Array.isArray(playlistTaskIds)) {
        playlistTaskIds = playlistTaskIds ? [playlistTaskIds] : [];
    }

    // Si pas de tâche de copie mais des tâches playlist → surveiller directement les playlists
    if (!taskId && playlistTaskIds.length > 0) {
        let nbPl = playlistTaskIds.length;
        terminal.textContent = 'Titre déjà dans AzuraCast. Ajout à ' + nbPl + ' playlist(s) en cours...';
        surveillerPlaylistActionLogChain(playlistTaskIds, '');
        return;
    }

    terminal.textContent = 'En attente du worker Ubuntu...';
    if (actionLogInterval) clearInterval(actionLogInterval);

    actionLogInterval = setInterval(function() {
        fetch('/lire_log_push?task_id=' + taskId)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                document.getElementById('terminal_action_output').textContent = data.log || '...';
                if (data.statut === 'termine' || data.statut === 'erreur') {
                    if (data.statut === 'termine' && actionTrack) {
                        // Ne marquer IN_AZURACAST que si la copie a réussi (au moins 1 copié, 0 erreur)
                        let logText = data.log || '';
                        let matchCopie = logText.match(/(\d+)\s+copié\(s\)/);
                        let matchErreur = logText.match(/(\d+)\s+erreur\(s\)/);
                        let nbCopies = matchCopie ? parseInt(matchCopie[1]) : 0;
                        let nbErreurs = matchErreur ? parseInt(matchErreur[1]) : 0;
                        if (nbCopies > 0 && nbErreurs === 0) {
                            actionTrack.inAzura = true;
                            document.getElementById('action_track_badge').innerHTML = '<span class="badge-azura">IN_AZURACAST</span>';
                        }
                    }
                    if (playlistTaskIds.length > 0) {
                        // Surveiller les tâches playlist séquentiellement
                        let prefixCopie = data.log || '';
                        if (prefixCopie) prefixCopie += '\n\n── Playlists ──\n';
                        surveillerPlaylistActionLogChain(playlistTaskIds, prefixCopie);
                    }
                    clearInterval(actionLogInterval);
                    // Auto-sync : rafraîchir les playlists, dossiers et cache après chaque opération
                    window.actionPlaylistsLoaded = false;
                    window.pushPlaylistsLoaded = false;
                    loadActionFolders();
                    loadActionPlaylists();
                    loadPushPlaylists();
                    if (typeof chargerPlaylistsCache === 'function') chargerPlaylistsCache();
                }
            });
    }, 2000);
}

// Chaîne la surveillance de plusieurs tâches playlist, une par une
// remainingIds : tableau d'IDs restant à surveiller
// prefixLog : texte à afficher avant le log de la playlist courante
function surveillerPlaylistActionLogChain(remainingIds, prefixLog) {
    if (remainingIds.length === 0) return;

    let currentId = remainingIds[0];
    let nextIds = remainingIds.slice(1);
    let accumulatedLog = prefixLog || '';
    let terminal = document.getElementById('terminal_action_output');

    let interval = setInterval(function() {
        fetch('/lire_log_playlist?task_id=' + currentId)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                terminal.textContent = accumulatedLog + (data.log || '...');
                terminal.scrollTop = terminal.scrollHeight;
                if (data.statut === 'termine' || data.statut === 'erreur') {
                    clearInterval(interval);
                    // Archiver le log de cette playlist
                    accumulatedLog += (data.log || '') + '\n';
                    if (nextIds.length > 0) {
                        // Passer à la playlist suivante
                        accumulatedLog += '\n── Playlist suivante (tâche #' + nextIds[0] + ') ──\n';
                        surveillerPlaylistActionLogChain(nextIds, accumulatedLog);
                    } else {
                        // Toutes les playlists sont terminées
                        terminal.textContent = accumulatedLog;
                        terminal.scrollTop = terminal.scrollHeight;
                    }
                    // Auto-sync après chaque playlist terminée
                    window.actionPlaylistsLoaded = false;
                    window.pushPlaylistsLoaded = false;
                    loadActionPlaylists();
                    loadPushPlaylists();
                    loadActionFolders();
                    if (typeof chargerPlaylistsCache === 'function') chargerPlaylistsCache();
                }
            });
    }, 2000);
}

// Fonction conservée pour compatibilité (appel unique de playlist)
function surveillerPlaylistActionLog(taskId) {
    surveillerPlaylistActionLogChain([taskId], '');
}

// ── Évolution majeure : charger la liste des stations depuis config.json ──
function loadStationsList() {
    fetch('/api/azuracast/stations?t=' + Date.now())
        .then(function(r) { return r.json(); })
        .then(function(data) {
            // Construire le HTML des options une seule fois
            let optionsHtml = '';
            if (!data.stations || data.stations.length === 0) {
                optionsHtml = '<option value="7">Station 7 (fallback)</option>';
            } else {
                let defaultId = data.default_station_id || 7;
                data.stations.forEach(function(s) {
                    let selected = (s.id === defaultId) ? ' selected' : '';
                    optionsHtml += '<option value="' + s.id + '"' + selected + '>'
                                 + s.nom + '</option>';
                });
            }
            // Appliquer aux deux sélecteurs : Pont RadioDJ ET Action AzuraCast (explorateur)
            let pushSel = document.getElementById('push_station_cible');
            let actionSel = document.getElementById('action_station_cible');
            if (pushSel) pushSel.innerHTML = optionsHtml;
            if (actionSel) actionSel.innerHTML = optionsHtml;
            // Déclencher le rechargement des playlists/dossiers pour la station par défaut
            reloadPlaylistsAndFoldersForStation();
        })
        .catch(function(err) {
            console.error('loadStationsList:', err);
            ['push_station_cible', 'action_station_cible'].forEach(function(id) {
                var el = document.getElementById(id);
                if (el) el.innerHTML = '<option value="7">Erreur chargement stations</option>';
            });
        });
}

// Recharge les playlists et dossiers pour la station actuellement sélectionnée
// (utilisé au chargement initial pour initialiser toutes les UI d'un coup)
function reloadPlaylistsAndFoldersForStation() {
    // Forcer le rechargement même si déjà "loaded"
    window.actionPlaylistsLoaded = false;
    window.pushPlaylistsLoaded = false;
    loadActionFolders();
    loadActionPlaylists();
    loadPushFolders();
    loadPushPlaylists();
    if (typeof chargerPlaylistsCache === 'function') chargerPlaylistsCache();
}

// Recharge UNIQUEMENT la partie "Action AzuraCast" (explorateur) quand l'utilisateur
// change la station dans ce panneau. Le Pont RadioDJ reste sur sa propre station.
function reloadActionPanelForStation() {
    window.actionPlaylistsLoaded = false;
    loadActionFolders();
    loadActionPlaylists();
}

// Recharge UNIQUEMENT la partie "Pont RadioDJ → AzuraCast" quand l'utilisateur
// change la station dans le Pont. L'Action AzuraCast reste sur sa propre station.
function reloadPushPanelForStation() {
    window.pushPlaylistsLoaded = false;
    loadPushFolders();
    loadPushPlaylists();
    if (typeof chargerPlaylistsCache === 'function') chargerPlaylistsCache();
}

// Écouteur : quand on change de station cible dans le Pont, recharger UNIQUEMENT
// les playlists + dossiers du Pont (sans perturber l'Action AzuraCast de l'explorateur)
(function bindStationChangeListener() {
    let sel = document.getElementById('push_station_cible');
    if (!sel) return;
    sel.addEventListener('change', function() {
        reloadPushPanelForStation();
    });
})();

// Écouteur : quand on change de station cible dans le panneau Action AzuraCast (explorateur),
// recharger UNIQUEMENT les dossiers + playlists de ce panneau — sans impacter le Pont.
(function bindActionStationChangeListener() {
    let sel = document.getElementById('action_station_cible');
    if (!sel) return;
    sel.addEventListener('change', function() {
        reloadActionPanelForStation();
    });
})();

loadStationsList();

// ── Diagnostic multi-station : affiche les stations détectées côté backend ──
function loadSyncAzuraDiagnostic() {
    let diagEl = document.getElementById('sync_azura_stations_diag');
    if (!diagEl) return;
    fetch('/api/azuracast/diagnostic?t=' + Date.now())
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.status !== 'ok') {
                diagEl.innerHTML = '<span class="text-danger">⚠ Erreur diagnostic : ' + (data.message || '?') + '</span>';
                return;
            }
            let nb = data.nombre_stations;
            let noms = (data.stations_detectees || [])
                .map(function(s) { return 'S' + s.id; })
                .join(', ');
            let cfgExists = data.config_json_exists;
            let cfgPath = data.config_json_path;
            let azuraOk = data.azura_section_present;

            if (!cfgExists) {
                diagEl.innerHTML =
                    '<span class="text-danger">❌ config.json INTROUVABLE</span> ' +
                    '<span class="text-muted">(' + cfgPath + ')</span><br>' +
                    '<span class="text-warning">→ Le backend retombe sur le fallback [7] seul.</span>';
                return;
            }
            if (!azuraOk) {
                diagEl.innerHTML =
                    '<span class="text-warning">⚠ config.json présent mais SANS section "azuracast"</span><br>' +
                    '<span class="text-muted">Clés trouvées : ' + (data.config_keys.join(', ') || 'aucune') + '</span><br>' +
                    '<span class="text-warning">→ Ajoute la section "azuracast.stations" avec les stations 6 et 7.</span>';
                return;
            }
            if (nb < 2) {
                diagEl.innerHTML =
                    '<span class="text-warning">⚠ ' + nb + ' station(s) détectée(s) : ' + noms + '</span><br>' +
                    '<span class="text-muted">Station 6 manquante — vérifie config.json côté Windows.</span>';
                return;
            }
            // Cas nominal
            diagEl.innerHTML =
                '<span class="text-success">✓ ' + nb + ' stations détectées : ' + noms + '</span>';
        })
        .catch(function(err) {
            diagEl.innerHTML = '<span class="text-danger">⚠ Erreur réseau diagnostic : ' + err + '</span>';
        });
}
loadSyncAzuraDiagnostic();

// ══════════════════════════════════════════════════════
// AZURACAST : CACHE PLAYLISTS (LECTURE DEPUIS SQL)
// ══════════════════════════════════════════════════════

var azuraPlaylistsBody = document.getElementById('azura_playlists_body');
var azuraCacheDate = document.getElementById('azura_playlists_cache_date');

function chargerPlaylistsCache() {
    let sid = getPushStationId();
    fetch('/api/azuracast/playlists?t=' + Date.now() + '&station_id=' + sid)
        .then(function(r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(playlists) {
            // Diagnostic : vérifier si la réponse contient station_id
            let missingStationId = Array.isArray(playlists) && playlists.length > 0
                && !playlists[0].hasOwnProperty('station_id');
            if (missingStationId) {
                console.warn('[AzuraCast] Cache playlists sans colonne station_id — '
                           + 'relancez une "Sync complète AzuraCast" depuis l\'onglet Maintenance '
                           + 'pour déclencher la migration SQL.');
            }
            // Garde : l'API peut retourner un objet erreur au lieu d'un tableau
            if (!Array.isArray(playlists)) {
                let detail = '';
                if (playlists && playlists.error) detail = playlists.error;
                else if (playlists && playlists.message) detail = playlists.message;
                console.warn('[AzuraCast] Réponse playlists inattendue :', playlists);
                azuraPlaylistsBody.innerHTML = '<tr><td colspan="8" class="text-center text-muted p-2 small">'
                    + 'Erreur serveur : ' + (detail || 'réponse inattendue') + '</td></tr>';
                return;
            }
            // Mettre à jour le badge de filtre dans le titre
            let filterBadge = document.getElementById('azura_playlists_station_filter');
            if (filterBadge) {
                filterBadge.textContent = 'Station ' + sid;
                filterBadge.className = 'badge ms-1';
                // Couleur distincte par station
                if (sid === 6) {
                    filterBadge.classList.add('bg-warning', 'text-dark');
                } else if (sid === 7) {
                    filterBadge.classList.add('bg-primary');
                } else {
                    filterBadge.classList.add('bg-secondary');
                }
            }

            if (!playlists || playlists.length === 0) {
                azuraPlaylistsBody.innerHTML = '<tr><td colspan="8" class="text-center text-muted p-2 small">' +
                    'Aucune playlist en cache pour la station ' + sid + '. Lancez une "Sync complète" depuis l\'onglet Maintenance.</td></tr>';
                azuraCacheDate.textContent = 'Cache vide (station ' + sid + ')';
                return;
            }

            azuraPlaylistsBody.innerHTML = '';
            let dateStr = '';

            playlists.forEach(function(pl) {
                if (pl.date_sync && !dateStr) dateStr = pl.date_sync;

                let typeLabel = {
                    'default': 'Standard',
                    'scheduled': 'Programmée',
                    'once_per_x_songs': '1/X titres',
                    'once_per_x_minutes': '1/X min',
                    'custom': 'Custom'
                }[pl.type] || pl.type;

                let orderLabel = pl.order === 'shuffle' ? 'Aléa.' : 'Séq.';

                // Badge de station (S6 = orange, S7 = bleu)
                let stationBadge;
                if (pl.station_id === 6) {
                    stationBadge = '<span class="badge bg-warning text-dark" title="Station 6">S6</span>';
                } else if (pl.station_id === 7) {
                    stationBadge = '<span class="badge bg-primary" title="Station 7">S7</span>';
                } else if (pl.station_id) {
                    stationBadge = '<span class="badge bg-secondary" title="Station ' + pl.station_id + '">S' + pl.station_id + '</span>';
                } else {
                    // station_id absent = cache pré-multi-station ou migration pas encore faite
                    stationBadge = '<span class="badge bg-light text-muted border" title="Cache pré-multi-station — relancez une Sync complète" '
                                 + 'style="cursor:help">? <small>(re-sync)</small></span>';
                }

                let row = '<tr>'
                    + '<td class="small">' + pl.id + '</td>'
                    + '<td class="small fw-bold">' + pl.name + '</td>'
                    + '<td class="text-center">' + stationBadge + '</td>'
                    + '<td class="small">' + typeLabel + '</td>'
                    + '<td class="small">' + orderLabel + '</td>'
                    + '<td class="small text-center">' + pl.nb_tracks + '</td>'
                    + '<td class="small text-center">' + formatDurationLong(pl.total_duration) + '</td>'
                    + '<td class="text-center">' + (pl.is_enabled
                        ? '<span class="badge bg-success">ON</span>'
                        : '<span class="badge bg-secondary">OFF</span>') + '</td>'
                    + '</tr>';
                azuraPlaylistsBody.innerHTML += row;
            });

            azuraCacheDate.textContent = dateStr
                ? 'Sync: ' + dateStr.substring(0, 16)
                : 'Date inconnue';
        })
        .catch(function(err) {
            let msg = 'Erreur de chargement du cache.';
            if (err && err.message) msg += ' (' + err.message + ')';
            azuraPlaylistsBody.innerHTML = '<tr><td colspan="8" class="text-center text-danger p-2 small">' +
                msg + '</td></tr>';
        });
}

// ── Charger les playlists existantes dans le <select> du Pont ──
function loadPushPlaylists() {
    let sid = getPushStationId();
    fetch('/api/azuracast/playlists?t=' + Date.now() + '&station_id=' + sid)
        .then(function(r) { return r.json(); })
        .then(function(playlists) {
            let sel1 = document.getElementById('push_playlist_select');
            let sel2 = document.getElementById('push_playlist_select_2');
            let sel3 = document.getElementById('push_playlist_select_3');
            if (!sel1) return;
            // Select 1 : avec option "aucune" et "nouvelle"
            let html1 = '<option value="">-- Aucune playlist (copie uniquement) --</option>';
            if (Array.isArray(playlists) && playlists.length > 0) {
                playlists.forEach(function(pl) {
                    html1 += '<option value="' + pl.id + '">' + pl.name + ' (' + pl.nb_tracks + ' titres)</option>';
                });
                html1 += '<option value="__new__">+ Créer une nouvelle playlist</option>';
            } else {
                html1 += '<option value="__new__">+ Créer une nouvelle playlist</option>';
            }
            sel1.innerHTML = html1;
            // Selects 2 et 3 : option "aucune" seulement
            let html23 = '<option value="">-- Aucune --</option>';
            if (Array.isArray(playlists)) {
                playlists.forEach(function(pl) {
                    html23 += '<option value="' + pl.id + '">' + pl.name + ' (' + pl.nb_tracks + ' titres)</option>';
                });
            }
            if (sel2) sel2.innerHTML = html23;
            if (sel3) sel3.innerHTML = html23;
            window.pushPlaylistsLoaded = true;
        })
        .catch(function() {
            let sel1 = document.getElementById('push_playlist_select');
            if (sel1) sel1.innerHTML = '<option value="">-- Erreur de chargement --</option><option value="__new__">+ Créer une nouvelle playlist</option>';
        });
}

// Collecter les IDs de playlists sélectionnées (Push)
function getPushPlaylistIds() {
    let ids = [];
    let v1 = document.getElementById('push_playlist_select').value;
    let v2 = document.getElementById('push_playlist_select_2').value;
    let v3 = document.getElementById('push_playlist_select_3').value;
    if (v1 && v1 !== '__new__') ids.push(parseInt(v1));
    if (v2 && v2 !== '__new__') ids.push(parseInt(v2));
    if (v3 && v3 !== '__new__') ids.push(parseInt(v3));
    return ids;
}

// Quand on choisit "Créer nouvelle playlist" dans le Pont
document.getElementById('push_playlist_select').addEventListener('change', function() {
    let typeCol = document.getElementById('push_new_playlist_type_col');
    let orderCol = document.getElementById('push_new_playlist_order_col');
    let nameInput = document.getElementById('push_new_playlist_name');
    if (this.value === '__new__') {
        if (typeCol) typeCol.style.display = '';
        if (orderCol) orderCol.style.display = '';
        if (nameInput) nameInput.focus();
    } else {
        if (typeCol) typeCol.style.display = 'none';
        if (orderCol) orderCol.style.display = 'none';
        if (this.value) {
            if (nameInput) nameInput.value = '';
        }
    }
});

// Quand on saisit un nouveau nom de playlist → afficher type+order
document.getElementById('push_new_playlist_name').addEventListener('input', function() {
    let typeCol = document.getElementById('push_new_playlist_type_col');
    let orderCol = document.getElementById('push_new_playlist_order_col');
    if (this.value.trim()) {
        if (typeCol) typeCol.style.display = '';
        if (orderCol) orderCol.style.display = '';
    } else if (document.getElementById('push_playlist_select').value !== '__new__') {
        if (typeCol) typeCol.style.display = 'none';
        if (orderCol) orderCol.style.display = 'none';
    }
});

// Charger le cache et les dossiers au démarrage
chargerPlaylistsCache();
loadActionFolders();
loadPushPlaylists();

// ══════════════════════════════════════════════════════
// PONT RADIODJ → AZURACAST (AVEC CRÉATION PLAYLIST)
// ══════════════════════════════════════════════════════
var pushPreviewBody = document.getElementById('push_preview_body');
var pushPreviewWrapper = document.getElementById('push_preview_wrapper');
var pushLogWrapper = document.getElementById('push_log_wrapper');
var playlistLogWrapper = document.getElementById('playlist_log_wrapper');
var pushStats = document.getElementById('push_preview_stats');
var pushCount = document.getElementById('push_count');
var btnPreview = document.getElementById('btn_push_preview');
var btnEnvoyer = document.getElementById('btn_push_envoyer');
var terminalPush = document.getElementById('terminal_push_output');
var terminalPlaylist = document.getElementById('terminal_playlist_output');
var pushSelectAll = document.getElementById('push_select_all');
var pushSelectAllBottom = document.getElementById('push_select_all_bottom');

var pushCancelled = false;  // true quand l'utilisateur a demandé l'annulation

let currentPushTaskId = null;
let currentPlaylistTaskId = null;       // ID de la tâche playlist EN COURS de surveillance
let pendingPlaylistTaskIds = [];        // File d'attente des tâches playlist restantes
let playlistAccumulatedLog = '';        // Log cumulé de toutes les tâches playlist
let intervalPush = null;
let intervalPlaylist = null;

// ── Parsing M3U côté navigateur ──
let m3uPaths = [];

document.getElementById('push_m3u_fichier').addEventListener('change', function(e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function(ev) {
        const lines = ev.target.result.split('\n');
        m3uPaths = [];
        lines.forEach(function(line) {
            line = line.trim();
            if (line && !line.startsWith('#')) {
                m3uPaths.push(line);
            }
        });
        document.getElementById('push_m3u_info').textContent =
            m3uPaths.length + " chemin(s) détecté(s) dans " + file.name;
    };
    reader.readAsText(file);
});

// Charger catégories/sous-catégories une fois le DOM et le monolithe prêts
// (loadCategories est exporté par le monolithe via window.loadCategories)
document.addEventListener('DOMContentLoaded', function() {
    if (window.loadCategories) {
        window.loadCategories('push_category_select', function(catId) {
            window.loadSubcategories(catId, 'push_subcategory_select');
        });
    }
    initColToggle();
});

function getPushMode() {
    var active = document.querySelector('#pushTabContent .tab-pane.active');
    if (!active) return 'assiste';
    if (active.id === 'push-bulk') return 'bulk';
    if (active.id === 'push-sql') return 'sql';
    if (active.id === 'push-m3u') return 'm3u';
    if (active.id === 'push-sheets') return 'sheets';
    if (active.id === 'push-manquants') return null;
    if (active.id === 'push-shazam') return 'shazam';
    if (active.id === 'push-animateurs') return 'animateurs';
    return 'assiste';
}

function updatePushCount() {
    btnEnvoyer.disabled = window.pushSelectedFiles.length === 0;
    pushCount.textContent = window.pushSelectedFiles.length + " fichier(s) sélectionné(s).";
}

function synchroniserPushSelectAll(source) {
    pushSelectAll.checked = source.checked;
    pushSelectAllBottom.checked = source.checked;
    const checkboxes = document.querySelectorAll('.push-checkbox:not(:disabled)');
    window.pushSelectedFiles = [];
    checkboxes.forEach(function(cb) {
        cb.checked = source.checked;
        if (source.checked) {
            window.pushSelectedFiles.push({
                path: cb.dataset.path,
                artist: cb.dataset.artist,
                title: cb.dataset.title,
                inAzura: cb.dataset.inAzura === 'true'
            });
        }
    });
    updatePushCount();
}

// ── Helper partagé : rend la section manquants et sauvegarde dans le buffer ──
function _renderPushMissingSection(normalizedMissing, buffer) {
    var missingWrapper = document.getElementById('push_missing_wrapper');
    var missingCount = document.getElementById('push_missing_count');
    var missingList = document.getElementById('push_missing_list');
    var nbManquants = normalizedMissing.length;

    if (nbManquants > 0) {
        missingWrapper.style.display = 'block';
        missingCount.textContent = nbManquants + ' titre(s) absent(s) de la base RadioDJ — non copiables vers AzuraCast.';
        missingList.innerHTML = normalizedMissing.map(function(item) {
            var texte = escapeHtml(item.display);
            return '<div class="d-flex align-items-center justify-content-between py-1 push-missing-line">' +
                   '<span class="push-missing-text" style="user-select: text; cursor: text;" ' +
                   'ondblclick="(function(el){var r=document.createRange();r.selectNodeContents(el);' +
                   'var s=window.getSelection();s.removeAllRanges();s.addRange(r);})(this)">' +
                   texte + '</span>' +
                   '<button class="btn btn-sm btn-link text-muted p-0 ms-2 btn-copy-missing-line" ' +
                   'data-text="' + escapeHtml(item.display) + '" title="Copier">📋</button>' +
                   '</div>';
        }).join('');
        missingList.querySelectorAll('.btn-copy-missing-line').forEach(function(btn) {
            btn.addEventListener('click', function() {
                copierTexteSansClipboardAPI(btn.dataset.text, btn);
            });
        });
    } else {
        missingWrapper.style.display = 'none';
        if (missingList) missingList.innerHTML = '';
    }
    buffer.missingHTML = missingList ? missingList.innerHTML : '';
    buffer.missingCountText = missingCount ? missingCount.textContent : '';
    buffer._missingWrapperDisplay = missingWrapper ? missingWrapper.style.display : 'none';
}

// ── Peupler le push preview depuis résultats Shazam (appelé par shazam-animateurs.js) ──
function populatePushPreviewFromShazam(found, notFound, apiData) {
    var buffer = pushBuffers.shazam;
    var statusEl = document.getElementById('shazam_prefill_status');

    // Guard : ne pas écraser le DOM si l'utilisateur a quitté l'onglet Shazam
    if (getPushMode() !== 'shazam') {
        buffer._pendingResult = { found: found, notFound: notFound, apiData: apiData };
        return;
    }

    pushPreviewWrapper.style.display = 'block';
    pushPreviewBody.innerHTML = '';
    buffer.selectedFiles = [];
    pushSelectAll.checked = false;
    pushSelectAllBottom.checked = false;

    // S'assurer que la zone partagée est visible
    var _sp = document.getElementById('push_shared_params');
    if (_sp) _sp.style.display = '';

    var normalizedMissing = (notFound || []).map(function(item) {
        if (typeof item === 'string') {
            return {artist: '', title: '', annee: '', album: '', display: item};
        }
        return {
            artist: item.artist || '', title: item.title || '',
            annee: '', album: '',
            display: item.display || ((item.artist || '') + ' — ' + (item.title || ''))
        };
    });

    var nbTrouves = found ? found.length : 0;
    var nbManquants = normalizedMissing.length;
    var nbInAzura = 0;

    if (found && found.length > 0) {
        found.forEach(function(t) {
            var inAzura = (t.comments === 'IN_AZURACAST');
            if (inAzura) nbInAzura++;
            var statusIcon = '<span class="text-success" title="Match Shazam">✓</span>';
            var azuraBadge = inAzura
                ? '<span class="badge-azura" style="font-size:0.7em">IN_AZURACAST</span>' : '';
            var shazamSrc = (t._shazam_source || '');
            var src2 = shazamSrc.toLowerCase();
            var shazamBadge = (src2.includes('formulaire') || src2.includes('web') || src2.includes('programmeur') || src2.includes('animateur'))
                ? '<span class="badge" style="background:#0ea5e9;color:#fff;font-size:0.7em;padding:2px 6px;border-radius:4px;">VIA_WEB</span>'
                : (src2.includes('externe'))
                ? '<span class="badge" style="background:#f59e0b;color:#000;font-size:0.7em;padding:2px 6px;border-radius:4px;">SHAZAM_EXT</span>'
                : '<span class="badge-shazam badge-shazam-sm">VIA_SHAZAM</span>';
            var anneeCell = (t.year || '')
                ? '<span class="text-muted">' + escapeHtml(String(t.year)) + '</span>'
                : '<span class="text-muted">—</span>';
            var albumCell = (t.album || '')
                ? '<span class="text-muted">' + escapeHtml(t.album) + '</span>'
                : '<span class="text-muted">—</span>';
            var row = '<tr>'
                + '<td data-col="check"><input class="form-check-input push-checkbox" type="checkbox" '
                + 'value="' + (t.ID || '') + '" data-path="' + (t.path || '') + '" '
                + 'data-artist="' + escapeAttr(t.artist || '') + '" '
                + 'data-title="' + escapeAttr(t.title || '') + '" '
                + 'data-in-azura="' + (inAzura ? 'true' : 'false') + '"></td>'
                + '<td data-col="status" class="text-center">' + statusIcon + '</td>'
                + '<td data-col="artist" class="small"><button class="btn btn-sm btn-outline-secondary play-btn me-1" '
                + 'data-id="' + (t.ID || '') + '" data-artist="' + escapeAttr(t.artist || '') + '" '
                + 'data-title="' + escapeAttr(t.title || '') + '" title="Écouter">▶</button>'
                + (t.artist || '?') + ' - ' + (t.title || '?') + '</td>'
                + '<td data-col="annee" class="small text-center">' + anneeCell + '</td>'
                + '<td data-col="album" class="small">' + albumCell + '</td>'
                + '<td data-col="bpm" class="small text-center">' + (t.bpm || 0) + '</td>'
                + '<td data-col="duree" class="small text-center">' + formatDuration(t.duration) + '</td>'
                + '<td data-col="azuracast" class="text-center">' + azuraBadge + '</td>'
                + '<td data-col="shazam" class="text-center">' + shazamBadge + '</td>'
                + '</tr>';
            pushPreviewBody.innerHTML += row;
        });
        pushSelectAll.checked = true;
        pushSelectAllBottom.checked = true;
        synchroniserPushSelectAll(pushSelectAll);
    }

    // Statut riche : total entrées + trouvés + absents
    var statsText = (apiData && apiData.total_shazam ? apiData.total_shazam + ' entrées · ' : '') +
        nbTrouves + ' trouvé(s)';
    if (nbInAzura > 0) statsText += ' · ' + nbInAzura + ' déjà dans AzuraCast';
    if (nbManquants > 0) statsText += ' · ' + nbManquants + ' absent(s) de RadioDJ';
    if (apiData && apiData.cancelled) {
        statsText = '[Annulé] ' + (apiData.processed || '?') + '/' + (apiData.total_shazam || '?') + ' traités — ' + statsText;
    }
    pushStats.textContent = statsText;
    if (statusEl) statusEl.textContent = statsText;

    buffer.previewHTML = pushPreviewBody.innerHTML;
    buffer.statsText = statsText;
    buffer._previewWrapperDisplay = 'block';

    _renderPushMissingSection(normalizedMissing, buffer);
    _applyColonnesFromBuffer(buffer);
    adaptColumnToggleToTab('shazam');

    setTimeout(function() {
        pushPreviewWrapper.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }, 100);
}


// ── Peupler le push preview depuis résultats Animateurs (appelé par shazam-animateurs.js) ──
function populatePushPreviewFromAnimateurs(found, notFound, apiData) {
    var buffer = pushBuffers.animateurs;
    var statusEl = document.getElementById('animateurs_prefill_status');

    // Guard : ne pas écraser le DOM si l'utilisateur a quitté l'onglet Animateurs
    if (getPushMode() !== 'animateurs') {
        buffer._pendingResult = { found: found, notFound: notFound, apiData: apiData };
        return;
    }

    pushPreviewWrapper.style.display = 'block';
    pushPreviewBody.innerHTML = '';
    buffer.selectedFiles = [];
    pushSelectAll.checked = false;
    pushSelectAllBottom.checked = false;

    // S'assurer que la zone partagée est visible
    var _sp = document.getElementById('push_shared_params');
    if (_sp) _sp.style.display = '';

    var normalizedMissing = (notFound || []).map(function(item) {
        if (typeof item === 'string') {
            return {artist: '', title: '', annee: '', album: '', display: item};
        }
        return {
            artist: item.artist || '', title: item.title || '',
            annee: '', album: '',
            display: item.display || ((item.artist || '') + ' — ' + (item.title || ''))
        };
    });

    var nbTrouves = found ? found.length : 0;
    var nbManquants = normalizedMissing.length;
    var nbInAzura = 0;

    if (found && found.length > 0) {
        found.forEach(function(t) {
            var inAzura = (t.comments === 'IN_AZURACAST');
            if (inAzura) nbInAzura++;
            var statusIcon = '<span class="text-success" title="Match Animateur">✓</span>';
            var azuraBadge = inAzura
                ? '<span class="badge-azura" style="font-size:0.7em">IN_AZURACAST</span>' : '';
            var shazamSrc = (t._shazam_source || '');
            var shazamBadge = (function(src) {
                var s = (src || '').toLowerCase();
                if (!s) return '<span class="text-muted" style="font-size:0.75rem">—</span>';
                if (s.includes('formulaire') || s.includes('programmeur'))
                    return '<span class="badge" style="background:#0ea5e9;color:#fff;font-size:0.7rem;padding:2px 6px;border-radius:4px;">📝 Formulaire</span>';
                if (s.includes('shazam'))
                    return '<span class="badge" style="background:#6366f1;color:#fff;font-size:0.7rem;padding:2px 6px;border-radius:4px;">♫ Shazam</span>';
                if (s.includes('recommandation'))
                    return '<span class="badge" style="background:#10b981;color:#fff;font-size:0.7rem;padding:2px 6px;border-radius:4px;">★ Reco</span>';
                if (s.includes('saisie') || s.includes('manuelle'))
                    return '<span class="badge" style="background:#f59e0b;color:#000;font-size:0.7rem;padding:2px 6px;border-radius:4px;">✏ Saisie</span>';
                return '<span class="badge" style="background:#6b7280;color:#fff;font-size:0.7rem;padding:2px 6px;border-radius:4px;">Autre</span>';
            })(shazamSrc);
            var anneeCell = (t.year || '')
                ? '<span class="text-muted">' + escapeHtml(String(t.year)) + '</span>'
                : '<span class="text-muted">—</span>';
            var albumCell = (t.album || '')
                ? '<span class="text-muted">' + escapeHtml(t.album) + '</span>'
                : '<span class="text-muted">—</span>';
            var row = '<tr>'
                + '<td data-col="check"><input class="form-check-input push-checkbox" type="checkbox" '
                + 'value="' + (t.ID || '') + '" data-path="' + (t.path || '') + '" '
                + 'data-artist="' + escapeAttr(t.artist || '') + '" '
                + 'data-title="' + escapeAttr(t.title || '') + '" '
                + 'data-in-azura="' + (inAzura ? 'true' : 'false') + '"></td>'
                + '<td data-col="status" class="text-center">' + statusIcon + '</td>'
                + '<td data-col="artist" class="small"><button class="btn btn-sm btn-outline-secondary play-btn me-1" '
                + 'data-id="' + (t.ID || '') + '" data-artist="' + escapeAttr(t.artist || '') + '" '
                + 'data-title="' + escapeAttr(t.title || '') + '" title="Écouter">▶</button>'
                + (t.artist || '?') + ' - ' + (t.title || '?') + '</td>'
                + '<td data-col="annee" class="small text-center">' + anneeCell + '</td>'
                + '<td data-col="album" class="small">' + albumCell + '</td>'
                + '<td data-col="bpm" class="small text-center">' + (t.bpm || 0) + '</td>'
                + '<td data-col="duree" class="small text-center">' + formatDuration(t.duration) + '</td>'
                + '<td data-col="azuracast" class="text-center">' + azuraBadge + '</td>'
                + '<td data-col="shazam" class="text-center">' + shazamBadge + '</td>'
                + '</tr>';
            pushPreviewBody.innerHTML += row;
        });
        pushSelectAll.checked = true;
        pushSelectAllBottom.checked = true;
        synchroniserPushSelectAll(pushSelectAll);
    }

    // Statut riche : total entrées + matchés + absents + info sync VPS
    var statsText = (apiData && apiData.total_shazam ? apiData.total_shazam + ' entrées · ' : '') +
        nbTrouves + ' matché(s)';
    if (nbInAzura > 0) statsText += ' · ' + nbInAzura + ' déjà dans AzuraCast';
    if (nbManquants > 0) statsText += ' · ' + nbManquants + ' absent(s) de RadioDJ';
    if (apiData && apiData.cancelled) {
        statsText = '[Annulé] ' + (apiData.processed || '?') + '/' + (apiData.total_shazam || '?') + ' traités — ' + statsText;
    }
    if (apiData && apiData.sync_logs && apiData.sync_logs.length > 0) {
        statsText = '[Sync VPS OK] ' + statsText;
        console.log('[Animateurs] sync_logs:', apiData.sync_logs);
    }
    pushStats.textContent = statsText;
    if (statusEl) statusEl.textContent = statsText;

    buffer.previewHTML = pushPreviewBody.innerHTML;
    buffer.statsText = statsText;
    buffer._previewWrapperDisplay = 'block';

    _renderPushMissingSection(normalizedMissing, buffer);
    _applyColonnesFromBuffer(buffer);
    adaptColumnToggleToTab('animateurs');

    setTimeout(function() {
        pushPreviewWrapper.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }, 100);
}
window._onShazamMatchResult = populatePushPreviewFromShazam;
window._onAnimateursMatchResult = populatePushPreviewFromAnimateurs;
window.getPushMode = getPushMode;
console.log('[INIT-DEBUG] window._onShazamMatchResult + window._onAnimateursMatchResult + window.getPushMode assignés OK');

pushSelectAll.addEventListener('change', function() {
    synchroniserPushSelectAll(pushSelectAll);
});

pushSelectAllBottom.addEventListener('change', function() {
    synchroniserPushSelectAll(pushSelectAllBottom);
});

document.addEventListener('change', function(e) {
    if (!e.target.classList.contains('push-checkbox')) return;
    if (e.target.disabled) return;

    if (e.target.checked) {
        window.pushSelectedFiles.push({
            path: e.target.dataset.path,
            artist: e.target.dataset.artist,
            title: e.target.dataset.title,
            inAzura: e.target.dataset.inAzura === 'true'
        });
    } else {
        window.pushSelectedFiles = window.pushSelectedFiles.filter(function(f) {
            return f.path !== e.target.dataset.path;
        });
    }

    const total = document.querySelectorAll('.push-checkbox:not(:disabled)').length;
    const checked = document.querySelectorAll('.push-checkbox:not(:disabled):checked').length;
    const allChecked = total > 0 && checked === total;
    pushSelectAll.checked = allChecked;
    pushSelectAllBottom.checked = allChecked;
    updatePushCount();
});

// ── Prévisualisation ──
btnPreview.addEventListener('click', function() {
    let mode = getPushMode();

    // Le mode Google Sheet et Shazam ont leur propre bouton d'analyse
    if (mode === 'sheets') {
        document.getElementById('btn_push_sheets_preview').click();
        return;
    }
    if (mode === 'shazam') {
        document.getElementById('btn_shazam_prefill').click();
        return;
    }
    if (mode === 'animateurs') {
        document.getElementById('btn_animateurs_prefill').click();
        return;
    }

    let payload = { mode: mode };

    if (mode === 'assiste') {
        payload.artiste = document.getElementById('push_artiste').value;
        payload.titre = document.getElementById('push_titre').value;
        // Évolution 2b : champ Album (filtrage sur s.album)
        payload.album = document.getElementById('push_album').value;
        payload.bpm_min = document.getElementById('push_bpm_min').value;
        payload.bpm_max = document.getElementById('push_bpm_max').value;
        payload.annee_min = document.getElementById('push_annee_min').value;
        payload.annee_max = document.getElementById('push_annee_max').value;
        payload.limite = document.getElementById('push_limite').value || 10;
        payload.ordre = document.getElementById('push_ordre').value;
        payload.category_id = document.getElementById('push_category_select').value;
        payload.subcategory_id = document.getElementById('push_subcategory_select').value;
        payload.genre = document.getElementById('push_genre').value;
        payload.exclure_bpm_zero = document.getElementById('push_exclure_bpm_zero').checked;
        payload.date_added_apres = document.getElementById('push_date_added_apres').value;
        payload.poids_min = document.getElementById('push_poids_min').value;
        payload.note_min = document.getElementById('push_note_min').value;
        payload.play_count_mode = document.getElementById('push_play_count_mode').value;
        payload.pas_diffuse_jours = document.getElementById('push_pas_diffuse_jours').value;
    } else if (mode === 'sql') {
        payload.requete = document.getElementById('push_sql_textarea').value;
    } else if (mode === 'm3u') {
        if (m3uPaths.length > 0) {
            payload.chemins_m3u = m3uPaths;
        } else {
            payload.chemin_m3u = document.getElementById('push_m3u_chemin').value;
            if (!payload.chemin_m3u) {
                btnPreview.disabled = false;
                btnPreview.textContent = "🔍 Prévisualiser";
                alert("Sélectionnez un fichier M3U ou indiquez un chemin.");
                return;
            }
        }
    }

    btnPreview.disabled = true;
    btnPreview.textContent = "Analyse...";

    fetch('/api/push_preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        btnPreview.disabled = false;
        btnPreview.textContent = "🔍 Prévisualiser";

        if (data.error) {
            pushPreviewWrapper.style.display = 'none';
            alert("Erreur : " + data.error);
            return;
        }

        // ── Gérer colonnes_disponibles : adapter filtres et toggle ──
        _adapterColonnesDisponibles(data.colonnes_disponibles);

        pushPreviewWrapper.style.display = 'block';
        pushPreviewBody.innerHTML = '';
        var activeBuffer = getActiveBuffer();
        if (activeBuffer) activeBuffer.selectedFiles = [];
        pushSelectAll.checked = false;
        pushSelectAllBottom.checked = false;
        document.getElementById('push_missing_wrapper').style.display = 'none';

        let nbDispo = 0, nbAbsent = 0;
        data.titres.forEach(function(t) {
            let absent = t._absent || false;
            if (absent) { nbAbsent++; } else { nbDispo++; }

            let statusIcon = absent
                ? '<span class="text-danger" title="Absent du disque">✗</span>'
                : '<span class="text-success" title="Disponible">✓</span>';

            let disabledAttr = absent ? 'disabled' : '';
            let rowClass = absent ? 'table-secondary' : '';

            let azuraBadge = (t.comments === 'IN_AZURACAST')
                ? '<span class="badge-azura" style="font-size:0.7em">IN_AZURACAST</span>'
                : '';
            let shazamBadge = !!t.in_shazam
                ? '<span class="badge-shazam badge-shazam-sm">VIA_SHAZAM</span>'
                : '';

            let inAzuraFlag = (t.comments === 'IN_AZURACAST') ? 'true' : 'false';

            // ── Évolution 2b : afficher Année et Album depuis la DB (mode assisté)
            //    Pour le mode Google Sheet, c'est populatePushPreviewFromSheet()
            //    qui gère l'affichage (priorité _sheet_annee/_sheet_album).
            let anneeDisplay = (t.year || '').toString();
            let albumDisplay = (t.album || '').toString();
            let anneeCell = anneeDisplay
                ? '<span class="text-muted">' + escapeHtml(anneeDisplay) + '</span>'
                : '<span class="text-muted">—</span>';
            let albumCell = albumDisplay
                ? '<span class="text-muted">' + escapeHtml(albumDisplay) + '</span>'
                : '<span class="text-muted">—</span>';

            // ── Nouvelles colonnes : note, poids, play, ajouté, ID ──
            let ratingVal = (t.sm_rating || 0);
            let ratingStars = ratingVal > 0 ? '\u2605'.repeat(ratingVal) : '<span class="text-muted">0</span>';
            let weightVal = (t.sm_weight != null && t.sm_weight !== '') ? parseFloat(t.sm_weight).toFixed(1) : '<span class="text-muted">—</span>';
            let playVal = (t.play_count || 0);
            let playClass = playVal === 0 ? 'text-muted fw-bold' : 'text-muted';
            let dateAddedVal = '';
            if (t.date_added) {
                try {
                    let d = new Date(t.date_added);
                    if (!isNaN(d.getTime())) {
                        dateAddedVal = ('0' + d.getDate()).slice(-2) + '/' + ('0' + (d.getMonth()+1)).slice(-2) + '/' + d.getFullYear();
                    }
                } catch(e) {}
            }
            let dateAddedCell = dateAddedVal
                ? '<span class="text-muted" style="font-size:0.75em">' + dateAddedVal + '</span>'
                : '<span class="text-muted">—</span>';

            // La visibilité des colonnes est gérée uniquement par le toggle ⚙
            // (non par le mode). Toutes les colonnes sont rendues avec data-col
            // et le toggle applique/retire .col-hidden uniformément.

            let row = '<tr class="' + rowClass + '">'
                + '<td data-col="check"><input class="form-check-input push-checkbox" type="checkbox" '
                + 'value="' + (t.ID || '') + '" data-path="' + t.path + '" '
                + 'data-artist="' + (t.artist || '') + '" '
                + 'data-title="' + (t.title || '') + '" '
                + 'data-in-azura="' + inAzuraFlag + '" ' + disabledAttr + '></td>'
                + '<td data-col="status" class="text-center">' + statusIcon + '</td>'
                + '<td data-col="artist" class="small"><button class="btn btn-sm btn-outline-secondary play-btn me-1" '
                + 'data-id="' + (t.ID || '') + '" data-artist="' + (t.artist || '') + '" '
                + 'data-title="' + (t.title || '') + '" title="\u00c9couter">\u25b6</button>'
                + (t.artist || '?') + ' - ' + (t.title || '?') + '</td>'
                + '<td data-col="id" class="small text-center">' + (t.ID || '') + '</td>'
                + '<td data-col="annee" class="small text-center">' + anneeCell + '</td>'
                + '<td data-col="album" class="small">' + albumCell + '</td>'
                + '<td data-col="bpm" class="small text-center">' + (t.bpm || 0) + '</td>'
                + '<td data-col="duree" class="small text-center">' + formatDuration(t.duration) + '</td>'
                + '<td data-col="note" class="small text-center">' + ratingStars + '</td>'
                + '<td data-col="poids" class="small text-center"><span class="text-muted">' + weightVal + '</span></td>'
                + '<td data-col="play" class="small text-center ' + playClass + '">' + playVal + '</td>'
                + '<td data-col="ajoute" class="small text-center">' + dateAddedCell + '</td>'
                + '<td data-col="azuracast" class="text-center">' + azuraBadge + '</td>'
                + '<td data-col="shazam" class="text-center">' + shazamBadge + '</td>'
                + '</tr>';
            pushPreviewBody.innerHTML += row;
        });

        pushStats.textContent = nbDispo + " disponible(s), " + nbAbsent + " absent(s) du disque.";

        // Appliquer la visibilité des colonnes depuis le buffer actif
        if (activeBuffer) {
            restoreColonneStateFromBuffer(activeBuffer);
        } else {
            // Pas de buffer : appliquer l'état par défaut des checkboxes du toggle
            document.querySelectorAll('.push-col-toggle:not(:disabled)').forEach(function(cb) {
                _toggleColElements(cb.dataset.col, cb.checked);
            });
        }

        // Sauvegarder dans le buffer actif
        if (activeBuffer) {
            activeBuffer.previewHTML = pushPreviewBody.innerHTML;
            activeBuffer.statsText = pushStats.textContent;
            activeBuffer._previewWrapperDisplay = 'block';
        }

        pushSelectAll.checked = true;
        pushSelectAllBottom.checked = true;
        synchroniserPushSelectAll(pushSelectAll);
    })
    .catch(function() {
        btnPreview.disabled = false;
        btnPreview.textContent = "🔍 Prévisualiser";
        alert("Erreur de communication.");
    });
});

// ── Utilitaire : réinitialiser le bouton Envoyer ──
function _resetBtnEnvoyer() {
    btnEnvoyer.disabled = false;
    btnEnvoyer.textContent = '🚀 Envoyer';
    btnEnvoyer.classList.remove('btn-danger');
    btnEnvoyer.classList.add('btn-primary');
    pushCancelled = false;
}

// ── Annulation de push en cours ──
function _annulerPush() {
    if (!currentPushTaskId && !pendingPlaylistTaskIds.length) return;
    var taskIdToCancel = currentPushTaskId;
    if (!taskIdToCancel && pendingPlaylistTaskIds.length > 0) {
        // Pas de tâche SFTP, mais des tâches playlist en attente
        // On annule la première tâche playlist comme proxy
        taskIdToCancel = currentPlaylistTaskId || pendingPlaylistTaskIds[0];
    }
    if (!taskIdToCancel) return;
    pushCancelled = true;
    btnEnvoyer.disabled = true;
    btnEnvoyer.textContent = 'Annulation...';
    fetch('/api/push/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: taskIdToCancel })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.ok) {
            // Le polling détectera "=== ANNULÉ ===" dans le log et réactivera le bouton
            btnEnvoyer.textContent = 'Annulation demandée...';
        } else {
            _resetBtnEnvoyer();
            alert('Erreur : ' + (data.error || 'impossible d\'annuler'));
            pushCancelled = false;
        }
    })
    .catch(function() {
        _resetBtnEnvoyer();
        alert('Erreur de communication.');
        pushCancelled = false;
    });
}

// ── Raccourci Echap pour annuler un push en cours ──
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && pushCancelled === false) {
        var ab = getActiveBuffer();
        if (ab && ab.isPushing) {
            e.preventDefault();
            _annulerPush();
        }
    }
});

// ── Envoi vers AzuraCast (AVEC option playlist) ──
btnEnvoyer.addEventListener('click', function() {
    // Si un push est en cours, le bouton sert à annuler
    var activeBuffer = getActiveBuffer();
    if (activeBuffer && activeBuffer.isPushing && !pushCancelled) {
        _annulerPush();
        return;
    }
    if (pushCancelled) return;  // Ignorer les clics pendant l'annulation

    var selectedFiles = activeBuffer ? activeBuffer.selectedFiles : window.pushSelectedFiles;
    if (selectedFiles.length === 0) return alert("Aucun fichier sélectionné dans cet onglet !");

    var dossierCible = getDossierCible('push_dossier_cible', 'push_new_folder_name');
    var playlistIds = getPushPlaylistIds();
    var newPlaylistName = document.getElementById('push_new_playlist_name').value.trim();
    var playlistType = document.getElementById('push_playlist_type').value;
    var playlistOrder = document.getElementById('push_playlist_order').value;

    // Déterminer l'action playlist : existante(s), nouvelle, ou aucune
    var creerPlaylist = (playlistIds.length > 0) || !!newPlaylistName;
    var nomPlaylist = newPlaylistName || '';

    var msg = "Envoyer vers AzuraCast dans le dossier : " + (dossierCible || "imports_push") + " ?";
    var fichiersACopier = selectedFiles.filter(function(f) { return !f.inAzura; });
    var fichiersDejaAzura = selectedFiles.filter(function(f) { return f.inAzura; });
    if (fichiersACopier.length > 0) msg += "\n\n📡 " + fichiersACopier.length + " fichier(s) à copier par SFTP";
    if (fichiersDejaAzura.length > 0) msg += "\n✅ " + fichiersDejaAzura.length + " fichier(s) déjà dans AzuraCast (ajout playlist uniquement)";
    if (creerPlaylist && playlistIds.length > 0) {
        msg += "\n\n🎵 + Ajouter à " + playlistIds.length + " playlist(s) existante(s) (IDs: " + playlistIds.join(', ') + ")";
    }
    if (nomPlaylist) {
        msg += "\n🎵 + Créer la playlist : " + nomPlaylist;
    }
    if (!creerPlaylist) {
        msg += "\n\n🎵 Aucune playlist (copie uniquement)";
    }

    if (!confirm(msg)) return;

    // Marquer le buffer comme en cours d'envoi
    if (activeBuffer) activeBuffer.isPushing = true;

    pushLogWrapper.style.display = 'block';
    terminalPush.textContent = "Envoi au Worker Ubuntu...\n";
    btnEnvoyer.disabled = false;  // reste cliquable pour pouvoir annuler
    btnEnvoyer.textContent = "✕ Annuler l'envoi";
    btnEnvoyer.classList.add('btn-danger');
    btnEnvoyer.classList.remove('btn-primary', 'btn-success');

    // Séparer les fichiers à copier de ceux déjà dans AzuraCast (déjà calculés ci-dessus)
    fetch('/lancer_push_azura', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            fichiers: fichiersACopier,
            fichiers_deja_azura: fichiersDejaAzura,
            dossier_cible: dossierCible,
            creer_playlist: creerPlaylist,
            nom_playlist: nomPlaylist,
            playlist_type: playlistType,
            playlist_order: playlistOrder,
            playlist_ids: playlistIds,
            station_id: getPushStationId()
        })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.status === "error") {
            alert(data.message);
            if (activeBuffer) activeBuffer.isPushing = false;
            _resetBtnEnvoyer();
            return;
        }
        currentPushTaskId = data.task_id;
        if (activeBuffer) activeBuffer.pushTaskId = data.task_id;

        // Si une ou plusieurs tâches playlist ont été créées
        var playlistTaskIds = data.playlist_task_ids || (data.playlist_task_id ? [data.playlist_task_id] : []);
        if (playlistTaskIds.length > 0) {
            // File d'attente : on surveille les playlists séquentiellement
            pendingPlaylistTaskIds = playlistTaskIds.slice();   // copie du tableau
            currentPlaylistTaskId = pendingPlaylistTaskIds.shift();  // prendre la 1ère
            playlistAccumulatedLog = '';
            if (activeBuffer) {
                activeBuffer.playlistTaskIds = playlistTaskIds.slice();
                activeBuffer.isPlaylistPending = true;
            }
            playlistLogWrapper.style.display = 'block';
            if (currentPushTaskId) {
                terminalPlaylist.textContent = "En attente de la copie SFTP avant ajout à " + playlistTaskIds.length + " playlist(s)...\n";
            } else {
                // Pas de copie SFTP nécessaire (tous déjà IN_AZURACAST) → playlist directement
                if (playlistTaskIds.length > 1) {
                    terminalPlaylist.textContent = "Titre(s) déjà dans AzuraCast. Ajout à " + playlistTaskIds.length + " playlists en cours...\n";
                } else {
                    terminalPlaylist.textContent = "Titre déjà dans AzuraCast. Ajout à la playlist en cours...\n";
                }
                if (intervalPlaylist) clearInterval(intervalPlaylist);
                intervalPlaylist = setInterval(fetchLogPlaylist, 500);
                surveillerFinPlaylist();
            }
        }

        if (currentPushTaskId) {
            // Il y a une tâche de copie SFTP à surveiller
            pushLogWrapper.style.display = 'block';
            if (intervalPush) clearInterval(intervalPush);
            intervalPush = setInterval(fetchLogPush, 500);
            surveillerFinPush();
            if (data.nb_deja_azura) {
                terminalPush.textContent = fichiersDejaAzura.length + " titre(s) déjà dans AzuraCast → ajout direct à la playlist.\n" +
                    fichiersACopier.length + " titre(s) à copier par SFTP...\n";
            }
        } else if (playlistTaskIds.length === 0) {
            // Aucune tâche créée (ne devrait pas arriver)
            _resetBtnEnvoyer();
        }
    })
    .catch(function() {
        alert("Erreur de communication.");
        if (activeBuffer) activeBuffer.isPushing = false;
        _resetBtnEnvoyer();
    });
});

function fetchLogPush() {
    var url = '/lire_log_push?t=' + Date.now();
    if (currentPushTaskId) url += '&task_id=' + currentPushTaskId;
    fetch(url)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data && data.log) {
                terminalPush.textContent = data.log;
                terminalPush.scrollTop = terminalPush.scrollHeight;
                var ab = getActiveBuffer();
                if (ab) ab.pushLogText = data.log;
            }
        })
        .catch(function() {});
}

function fetchLogPlaylist() {
    var url = '/lire_log_playlist?t=' + Date.now();
    if (currentPlaylistTaskId) url += '&task_id=' + currentPlaylistTaskId;
    fetch(url)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data && data.log) {
                terminalPush.textContent = data.log;
                terminalPush.scrollTop = terminalPush.scrollHeight;
                var ab = getActiveBuffer();
                if (ab) ab.pushLogText = data.log;
            }
        })
        .catch(function() {});
}

function fetchLogPlaylist() {
    var url = '/lire_log_playlist?t=' + Date.now();
    if (currentPlaylistTaskId) url += '&task_id=' + currentPlaylistTaskId;
    fetch(url)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data && data.log) {
                terminalPlaylist.textContent = data.log;
                terminalPlaylist.scrollTop = terminalPlaylist.scrollHeight;
                var ab = getActiveBuffer();
                if (ab) ab.playlistLogText = data.log;
            }
        })
        .catch(function() {});
}

function surveillerFinPush() {
    let c = setInterval(function() {
        let t = terminalPush.textContent;
        if (t.includes("=== TERMINÉ ===") || t.includes("ERREUR FATALE") || t.includes("=== ANNULÉ ===")) {
            clearInterval(c);
            clearInterval(intervalPush);
            var _wasCancelled = t.includes("=== ANNULÉ ===");

            // Si annulé, vider les tâches playlist en attente pour ne pas les lancer
            if (_wasCancelled) {
                pendingPlaylistTaskIds = [];
                currentPlaylistTaskId = null;
            }

            // Auto-sync : rafraîchir les dossiers et playlists après copie
            loadActionFolders();
            window.pushPlaylistsLoaded = false;
            loadPushPlaylists();
            window.actionPlaylistsLoaded = false;

            // Si une tâche playlist est en attente, commencer à la surveiller
            if (!_wasCancelled && currentPlaylistTaskId) {
                let nbRestantes = pendingPlaylistTaskIds.length + 1;  // celle en cours + la file
                terminalPlaylist.textContent = "La copie SFTP est terminée. Ajout à " + nbRestantes + " playlist(s) en cours...\n";
                if (intervalPlaylist) clearInterval(intervalPlaylist);
                intervalPlaylist = setInterval(fetchLogPlaylist, 500);
                surveillerFinPlaylist();
            } else {
                // Aucune playlist à surveiller → réactiver le bouton
                var _buf = getActiveBuffer();
                if (_buf) _buf.isPushing = false;
                _resetBtnEnvoyer();
            }
        }
    }, 2000);
}

function surveillerFinPlaylist() {
    var c = setInterval(function() {
        var t = terminalPlaylist.textContent;
        if (t.includes("=== TERMINÉ PLAYLIST AZURACAST ===") ||
            t.includes("=== TERMINÉ PLAYLIST AZURACAST (ERREUR) ===") ||
            t.includes("=== TERMINÉ IMPORT M3U ===") ||
            t.includes("=== TERMINÉ IMPORT M3U (ÉCHEC) ===") ||
            t.includes("=== TERMINÉ IMPORT M3U (ERREUR) ===") ||
            t.includes("ERREUR API") ||
            t.includes("=== ANNULÉ ===")) {
            clearInterval(c);
            clearInterval(intervalPlaylist);
            var _wasCancelled = t.includes("=== ANNULÉ ===");

            // Si annulé, arrêter la chaîne de playlists
            if (_wasCancelled) {
                pendingPlaylistTaskIds = [];
                playlistAccumulatedLog += terminalPlaylist.textContent + '\n';
                terminalPlaylist.textContent = playlistAccumulatedLog;
                terminalPlaylist.scrollTop = terminalPlaylist.scrollHeight;
                var _buf2 = getActiveBuffer();
                if (_buf2) {
                    _buf2.isPushing = false;
                    _buf2.isPlaylistPending = false;
                    _buf2.playlistAccumLog = playlistAccumulatedLog;
                }
                _resetBtnEnvoyer();
                return;
            }

            // Archiver le log de cette tâche playlist dans le log cumulé
            playlistAccumulatedLog += terminalPlaylist.textContent + '\n';

            // Y a-t-il d'autres tâches playlist en attente ?
            if (pendingPlaylistTaskIds.length > 0) {
                // Passer à la playlist suivante
                currentPlaylistTaskId = pendingPlaylistTaskIds.shift();
                terminalPlaylist.textContent = playlistAccumulatedLog +
                    '\n── Playlist suivante (tâche #' + currentPlaylistTaskId + ') ──\n' +
                    'Ajout en cours...\n';
                if (intervalPlaylist) clearInterval(intervalPlaylist);
                intervalPlaylist = setInterval(fetchLogPlaylist, 500);
                surveillerFinPlaylist();  // chaîner la surveillance
            } else {
                // Toutes les playlists sont terminées → afficher le résumé cumulé
                terminalPlaylist.textContent = playlistAccumulatedLog;
                terminalPlaylist.scrollTop = terminalPlaylist.scrollHeight;

                // Réactiver le bouton Envoyer
                var _buf2 = getActiveBuffer();
                if (_buf2) {
                    _buf2.isPushing = false;
                    _buf2.isPlaylistPending = false;
                    _buf2.playlistAccumLog = playlistAccumulatedLog;
                }
                _resetBtnEnvoyer();

                // Auto-sync : rafraîchir les playlists, dossiers et cache après playlist
                window.actionPlaylistsLoaded = false;
                window.pushPlaylistsLoaded = false;
                loadActionPlaylists();
                loadPushPlaylists();
                loadActionFolders();
                setTimeout(chargerPlaylistsCache, 3000);
            }
        }
    }, 2000);
}

// ════════════════════════════════════════════════════════════════
// ═══ BULK SCHEDULE AZURACAST ════════════════════════════════════
// ════════════════════════════════════════════════════════════════
    // Variables d'état
    let bulkPlaylists = [];
    let lastPreview = null;

    // Charger les stations dans le select au chargement de la page
    function loadBulkStations() {
        fetch('/api/azuracast/stations')
            .then(r => r.json())
            .then(data => {
                if (data.status === 'ok') {
                    const sel = document.getElementById('bulk_station_select');
                    sel.innerHTML = '<option value="">-- Choisir --</option>';
                    (data.stations || []).forEach(s => {
                        sel.innerHTML += `<option value="${s.id}">${s.name} (ID ${s.id})</option>`;
                    });
                    if (data.default_station_id) sel.value = data.default_station_id;
                }
            })
            .catch(e => console.error('loadBulkStations:', e));
    }

    // Charger les playlists d'une station — réutilise le cache SQL existant
    // (route /api/azuracast/playlists déjà utilisée par les autres onglets)
    function loadBulkPlaylists() {
        const stationId = document.getElementById('bulk_station_select').value;
        if (!stationId) { alert('Sélectionnez une station d\'abord'); return; }

        document.getElementById('bulk_load_playlists').disabled = true;
        document.getElementById('bulk_load_playlists').textContent = '⏳ Chargement…';

        // Utiliser la route existante qui lit le cache SQL local (instantané)
        fetch(`/api/azuracast/playlists?station_id=${stationId}`)
            .then(r => r.json())
            .then(data => {
                // data est directement un tableau de playlists (pas un objet {status, playlists})
                const playlists = Array.isArray(data) ? data : (data.playlists || []);
                bulkPlaylists = playlists;
                document.getElementById('bulk_playlists_count').textContent =
                    `${bulkPlaylists.length} playlist(s) chargée(s)`;

                // Remplir le dropdown du mode insert
                const insertSel = document.getElementById('bulk_insert_playlist');
                insertSel.innerHTML = '<option value="">-- Choisir --</option>';
                bulkPlaylists.forEach(p => {
                    insertSel.innerHTML += `<option value="${p.id}">${p.name} (ID ${p.id})</option>`;
                });

                // Remplir les checkboxes du mode shift
                const shiftDiv = document.getElementById('bulk_shift_playlists');
                shiftDiv.innerHTML = '';
                bulkPlaylists.forEach(p => {
                    shiftDiv.innerHTML += `
                        <div class="form-check">
                            <input class="form-check-input" type="checkbox" value="${p.id}" id="bulk_shift_${p.id}">
                            <label class="form-check-label small" for="bulk_shift_${p.id}">
                                ${p.name} (ID ${p.id})
                            </label>
                        </div>`;
                });
            })
            .catch(e => { console.error(e); alert('Erreur réseau: ' + e.message); })
            .finally(() => {
                document.getElementById('bulk_load_playlists').disabled = false;
                document.getElementById('bulk_load_playlists').textContent = '🔄 Charger les playlists';
            });
    }

    // Détecter le mode actif et le mapper vers le nom attendu par le worker
    function getActiveMode() {
        const activeTab = document.querySelector('#bulkModeContent .tab-pane.active');
        if (!activeTab) return null;
        const shortMode = activeTab.id.replace('bulk-mode-', '');
        // Mapping : insert → insert_event, shift → shift, json → json_override
        const modeMap = { 'insert': 'insert_event', 'shift': 'shift', 'json': 'json_override' };
        return modeMap[shortMode] || shortMode;
    }

    // Construire le payload selon le mode
    function buildPreviewPayload() {
        const stationId = parseInt(document.getElementById('bulk_station_select').value);
        const mode = getActiveMode();
        if (!stationId) { alert('Sélectionnez une station'); return null; }

        const payload = { station_id: stationId, mode: mode };

        if (mode === 'insert_event') {
            const playlistId = parseInt(document.getElementById('bulk_insert_playlist').value);
            const startTime = document.getElementById('bulk_insert_start').value;
            const duration = parseInt(document.getElementById('bulk_insert_duration').value);
            const dayVal = document.getElementById('bulk_insert_day').value;
            if (!playlistId || !startTime || !duration) {
                alert('Playlist, heure de début et durée requis'); return null;
            }
            payload.insert = { playlist_id: playlistId, start_time: startTime, duration_minutes: duration };
            if (dayVal) payload.insert.day = parseInt(dayVal);
        } else if (mode === 'shift') {
            const delta = parseInt(document.getElementById('bulk_shift_delta').value);
            const checked = Array.from(document.querySelectorAll('#bulk_shift_playlists input:checked'))
                .map(c => parseInt(c.value));
            if (!delta || checked.length === 0) {
                alert('Delta et au moins une playlist requis'); return null;
            }
            payload.shift = { playlist_ids: checked, delta_minutes: delta };
        } else if (mode === 'json_override') {
            const idsRaw = document.getElementById('bulk_json_ids').value.trim();
            const jsonRaw = document.getElementById('bulk_json_config').value.trim();
            if (!idsRaw || !jsonRaw) {
                alert('IDs et configuration JSON requis'); return null;
            }
            let scheduleConfig;
            try { scheduleConfig = JSON.parse(jsonRaw); }
            catch (e) { alert('JSON invalide: ' + e.message); return null; }
            const ids = idsRaw.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n));
            if (ids.length === 0) { alert('Aucun ID valide'); return null; }
            payload.json_override = { playlist_ids: ids, schedule_config: scheduleConfig };
        }

        return payload;
    }

    // Prévisualiser
    function previewBulk() {
        const payload = buildPreviewPayload();
        if (!payload) return;

        document.getElementById('bulk_preview_btn').disabled = true;
        document.getElementById('bulk_preview_btn').textContent = '⏳ Calcul…';

        fetch('/api/azuracast/bulk_schedule/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'ok') {
                lastPreview = data;
                displayResults(data);
                document.getElementById('bulk_apply_btn').disabled = false;
            } else {
                alert('Erreur: ' + (data.message || 'inconnue'));
            }
        })
        .catch(e => { console.error(e); alert('Erreur réseau: ' + e.message); })
        .finally(() => {
            document.getElementById('bulk_preview_btn').disabled = false;
            document.getElementById('bulk_preview_btn').textContent = '👁️ Prévisualiser (dry-run)';
        });
    }

    // Afficher les résultats de la prévisualisation
    function displayResults(data) {
        const div = document.getElementById('bulk_results');
        const content = document.getElementById('bulk_results_content');
        div.style.display = 'block';

        let html = `<div class="alert ${data.total_affected > 0 ? 'alert-warning' : 'alert-success'} py-2 small">`;
        html += `<b>${data.total_affected}</b> playlist(s) affectée(s). Cochez/décochez celles à modifier réellement.`;
        html += '</div>';

        // Playlist à insérer (mode insert_event)
        if (data.insert_playlist) {
            html += '<div class="card border-success mb-2"><div class="card-header bg-success text-white py-1 small">';
            html += `<b>➕ Playlist à insérer :</b> ${data.insert_playlist.name} (ID ${data.insert_playlist.playlist_id})`;
            html += '</div><div class="card-body py-1 small">';
            html += `<span>Début: <code>${data.insert_playlist.start_time}</code> — Fin: <code>${data.insert_playlist.end_time}</code></span>`;
            html += '</div></div>';
        }

        // Playlists affectées avec checkboxes
        if (data.planned_updates && data.planned_updates.length > 0) {
            html += '<div class="table-responsive"><table class="table table-sm table-bordered">';
            html += '<thead><tr><th width="40px"><input type="checkbox" id="bulk_select_all" checked></th><th>Playlist</th><th>Items</th><th>Changements</th></tr></thead><tbody>';
            data.planned_updates.forEach((u, i) => {
                html += `<tr><td class="text-center"><input type="checkbox" class="bulk-item-check" data-index="${i}" data-playlist-id="${u.playlist_id}" checked></td>`;
                html += `<td><b>${u.name}</b><br><small class="text-muted">ID ${u.playlist_id}</small></td>`;
                html += `<td><small>${u.old_items || '?'} → ${u.new_items_count || u.changes || '?'}</small></td>`;
                html += `<td><span class="badge bg-warning">${u.changes || '?'}</span></td>`;
                html += '</tr>';
            });
            html += '</tbody></table></div>';
        }

        content.innerHTML = html;

        // "Select all" checkbox handler
        const selectAll = document.getElementById('bulk_select_all');
        if (selectAll) {
            selectAll.addEventListener('change', function() {
                document.querySelectorAll('.bulk-item-check').forEach(cb => {
                    cb.checked = this.checked;
                });
            });
        }
    }

    // Appliquer
    function applyBulk() {
        if (!lastPreview) { alert('Prévisualisez d\'abord'); return; }

        // Récupérer les IDs cochés
        const checkedIds = Array.from(document.querySelectorAll('.bulk-item-check:checked'))
            .map(cb => parseInt(cb.dataset.playlistId));
        if (checkedIds.length === 0) { alert('Cochez au moins une playlist à modifier'); return; }

        if (!confirm(`Confirmer l'application sur ${checkedIds.length} playlist(s) ? Un backup sera créé.`)) return;

        document.getElementById('bulk_apply_btn').disabled = true;
        document.getElementById('bulk_apply_btn').textContent = '⏳ Application…';

        const stationId = parseInt(document.getElementById('bulk_station_select').value);
        const mode = getActiveMode();

        // L'apply recalcule côté worker — on renvoie les mêmes paramètres que le preview
        // + la liste des IDs sélectionnés pour filtrer
        const payload = buildPreviewPayload();
        if (!payload) return;
        payload.sub_mode = 'apply';
        payload.selected_playlist_ids = checkedIds;

        fetch('/api/azuracast/bulk_schedule/apply', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'ok' || data.status === 'partial') {
                let msg = `✅ ${data.message}`;
                if (data.backup_path) msg += `\n\nBackup: ${data.backup_path}`;
                if (data.failures && data.failures.length > 0) {
                    msg += `\n\nÉchecs: ${data.failures.map(f => `#${f.playlist_id}: ${f.error}`).join(', ')}`;
                }
                alert(msg);
                // Recharger les playlists pour refléter les changements
                loadBulkPlaylists();
                document.getElementById('bulk_apply_btn').disabled = true;
            } else {
                alert('Erreur: ' + (data.message || 'inconnue'));
            }
        })
        .catch(e => { console.error(e); alert('Erreur réseau: ' + e.message); })
        .finally(() => {
            document.getElementById('bulk_apply_btn').disabled = false;
            document.getElementById('bulk_apply_btn').textContent = '⚡ Appliquer';
        });
    }

    // Event listeners (inline — already inside DOMContentLoaded)
    (function() {
        loadBulkStations();
        document.getElementById('bulk_load_playlists').addEventListener('click', loadBulkPlaylists);
        document.getElementById('bulk_preview_btn').addEventListener('click', previewBulk);
        document.getElementById('bulk_apply_btn').addEventListener('click', applyBulk);

        // Gestion des changements d'onglets du Pont RadioDJ
        document.querySelectorAll('#pushTabs button[data-bs-toggle="pill"]').forEach(function(pill) {
            pill.addEventListener('shown.bs.tab', function(e) {
                // 1. Sauvegarder le buffer quitté
                var oldKey = e.relatedTarget ? getBufferKeyFromTabId(e.relatedTarget.id) : null;
                if (oldKey && pushBuffers[oldKey]) {
                    saveBufferToDOM(pushBuffers[oldKey]);
                }

                // 2. Masquer les consoles si un push était en cours dans l'ancien onglet
                //    (les intervals sont déjà pausés par saveBufferToDOM)

                // 3. Restaurer le buffer cible
                var newKey = getBufferKeyFromTabId(e.target.id);
                if (newKey && pushBuffers[newKey]) {
                    // Si des résultats sont en attente (arrivés hors onglet),
                    // les rendre via la fonction de peuplement dédiée au lieu de restoreBufferFromDOM
                    if (newKey === 'sheets' && pushBuffers.sheets._pendingSheetResult) {
                        var pending = pushBuffers.sheets._pendingSheetResult;
                        delete pushBuffers.sheets._pendingSheetResult;
                        if (typeof window.populatePushPreviewFromSheet === 'function') {
                            window.populatePushPreviewFromSheet(pending.found, pending.not_found);
                        }
                    } else if (newKey === 'shazam' && pushBuffers.shazam._pendingResult) {
                        var pending = pushBuffers.shazam._pendingResult;
                        delete pushBuffers.shazam._pendingResult;
                        populatePushPreviewFromShazam(pending.found, pending.notFound, pending.apiData);
                    } else if (newKey === 'animateurs' && pushBuffers.animateurs._pendingResult) {
                        var pending = pushBuffers.animateurs._pendingResult;
                        delete pushBuffers.animateurs._pendingResult;
                        populatePushPreviewFromAnimateurs(pending.found, pending.notFound, pending.apiData);
                    } else {
                        restoreBufferFromDOM(pushBuffers[newKey]);
                    }
                }

                // 4. Adapter les toggles de colonnes au nouvel onglet
                adaptColumnToggleToTab(newKey);

                // 5. Masquer/afficher les paramètres partagés
                var sharedParams = document.getElementById('push_shared_params');
                if (sharedParams) {
                    var _hide = e.target.id === 'push-bulk-tab' || e.target.id === 'push-manquants-tab';
                    sharedParams.style.display = _hide ? 'none' : '';
                }

                // 6. Mettre à jour le badge de buffer actif
                updateBufferBadge(newKey);
            });
        });
    })();

    // ── Bouton Inverser sélection ──
    (function() {
        var btn = document.getElementById('btn_push_invert_selection');
        if (!btn) return;
        btn.addEventListener('click', function() {
            var checkboxes = document.querySelectorAll('.push-checkbox:not(:disabled)');
            var buffer = getActiveBuffer();
            if (!buffer) return;
            checkboxes.forEach(function(cb) {
                cb.checked = !cb.checked;
            });
            // Reconstruire selectedFiles depuis les checkboxes cochées
            buffer.selectedFiles = [];
            checkboxes.forEach(function(cb) {
                if (cb.checked) {
                    buffer.selectedFiles.push({
                        path: cb.dataset.path,
                        artist: cb.dataset.artist,
                        title: cb.dataset.title,
                        inAzura: cb.dataset.inAzura === 'true'
                    });
                }
            });
            // Mettre à jour l'état des select-all
            var total = checkboxes.length;
            var checked = document.querySelectorAll('.push-checkbox:not(:disabled):checked').length;
            var allChecked = total > 0 && checked === total;
            pushSelectAll.checked = allChecked;
            pushSelectAllBottom.checked = allChecked;
            updatePushCount();
        });
    })();

    // ── Bouton Vider le buffer ──
    (function() {
        var btn = document.getElementById('btn_push_clear_buffer');
        if (!btn) return;
        btn.addEventListener('click', function() {
            var buffer = getActiveBuffer();
            if (!buffer) return;
            buffer.selectedFiles = [];
            buffer.previewHTML = '';
            buffer.statsText = '';
            buffer.missingHTML = '';
            buffer.pushLogText = '';
            buffer.playlistLogText = '';
            buffer.pushTaskId = null;
            buffer.playlistTaskIds = [];
            buffer.playlistAccumLog = '';
            buffer.isPushing = false;
            buffer.isPlaylistPending = false;
            buffer._selectAllChecked = false;
            buffer._selectAllBottomChecked = false;
            buffer._previewWrapperDisplay = 'none';
            buffer._missingWrapperDisplay = 'none';
            buffer._logWrapperDisplay = 'none';
            buffer._playlistLogWrapperDisplay = 'none';
            // Vider le DOM
            pushPreviewBody.innerHTML = '';
            pushStats.textContent = '';
            terminalPush.textContent = '';
            terminalPlaylist.textContent = '';
            pushPreviewWrapper.style.display = 'none';
            var _mw = document.getElementById('push_missing_wrapper');
            if (_mw) { _mw.style.display = 'none'; }
            var _ml = document.getElementById('push_missing_list');
            if (_ml) { _ml.innerHTML = ''; }
            pushLogWrapper.style.display = 'none';
            playlistLogWrapper.style.display = 'none';
            pushSelectAll.checked = false;
            pushSelectAllBottom.checked = false;
            updatePushCount();
        });
    })();

    // ── Bouton Rafraîchir les dossiers AzuraCast ──
    (function() {
        // Lier le bouton du panneau Action (explorateur)
        var btnAction = document.getElementById('btn_refresh_action_folders');
        if (btnAction) {
            btnAction.addEventListener('click', function() {
                _refreshAzuraFolders(this);
            });
        }
        // Lier le bouton du panneau Pont (push)
        var btnPush = document.getElementById('btn_refresh_push_folders');
        if (btnPush) {
            btnPush.addEventListener('click', function() {
                _refreshAzuraFolders(this);
            });
        }
    })();

    /** Rafraîchit le cache des dossiers AzuraCast et recharge les selects.
     *  Appelé par les boutons « Rafraîchir les dossiers » (Action + Pont).
     *  [PATCH 01/09/2026 v2] Délègue au worker qui scanne API + SSHFS (quelques secondes). */
    function _refreshAzuraFolders(btnEl) {
        var originalText = btnEl.textContent;
        btnEl.disabled = true;
        btnEl.textContent = 'Scan en cours…';

        fetch('/api/azuracast/refresh-folders', {method: 'POST'})
            .then(function(r) { return r.json(); })
            .then(function(data) {
                btnEl.disabled = false;
                btnEl.textContent = originalText;
                if (data.status === 'ok') {
                    // Recharger les deux séries de dossiers
                    loadActionFolders();
                    loadPushFolders();
                    // Toast de succès avec source
                    if (window.showToast) {
                        var source = data.source || '';
                        var msg = data.count + ' dossier(s)';
                        if (source) msg += ' (' + source + ')';
                        window.showToast(msg, 'success');
                    }
                } else {
                    if (window.showToast) {
                        window.showToast('Erreur : ' + (data.message || 'inconnue'), 'danger');
                    }
                }
            })
            .catch(function(err) {
                btnEl.disabled = false;
                btnEl.textContent = originalText;
                if (window.showToast) {
                    window.showToast('Erreur réseau : ' + err.message, 'danger');
                }
            });
    }

    // ── Exports vers window (utilisés par le monolithe ou d'autres modules) ──
    window._onShazamMatchResult = populatePushPreviewFromShazam;
    window._onAnimateursMatchResult = populatePushPreviewFromAnimateurs;
    window._applyColonnesFromBuffer = _applyColonnesFromBuffer;
    window._adaptColumnToggleToTab = adaptColumnToggleToTab;
    window.loadActionFolders = loadActionFolders;
    window.loadActionPlaylists = loadActionPlaylists;
    window.loadPushPlaylists = loadPushPlaylists;
    window.loadPushFolders = loadPushFolders;
    window.chargerPlaylistsCache = chargerPlaylistsCache;
    window.synchroniserPushSelectAll = synchroniserPushSelectAll;
    window.pushPreviewWrapper = pushPreviewWrapper;
    window.pushPreviewBody = pushPreviewBody;
    window.pushSelectAll = pushSelectAll;
    window.pushSelectAllBottom = pushSelectAllBottom;
    window.pushStats = pushStats;

    // Fonction utilitaire pour le monolithe : réinitialiser les playlists AzuraCast
    window.resetAzuraPlaylists = function() {
        window.actionPlaylistsLoaded = false;
        window.pushPlaylistsLoaded = false;
        loadActionFolders();
        loadActionPlaylists();
        loadPushPlaylists();
        chargerPlaylistsCache();
    };

    console.log('[sync-azuracast.js] Module chargé.');
})();
