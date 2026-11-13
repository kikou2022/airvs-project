// ═══════════════════════════════════════════════════════════════════════════
// rotation.js — Phase 21.3 : onglet Rotation éditoriale (v1)
//
// Conformité maquette validée (maquette_rotation_editoriale.html) + theme
// natif clair/sombre (Phase 20.2) + sidebar sections (Phase 20.3).
// Le dashboard .39 n'atteint pas l'API AzuraCast (machine isolée) : tout le
// calcul passe par des tâches worker (ROTATION_PREVIEW / ROTATION_EXECUTE /
// MAPPING_BOOTSTRAP) créées via blueprints/rotation.py, puis polling.
// ═══════════════════════════════════════════════════════════════════════════
(function() {
    'use strict';

    var _pollTimer = null;
    var _selection = {};   // {song_id: true}
    var _config = null;

    function $(id) { return document.getElementById(id); }

    function _esc(t) {
        if (t === null || t === undefined) return '';
        return String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function _logConsole(msg) {
        try { console.log('[ROTATION]', msg); } catch (e) {}
    }

    // ── Fetch wrapper ──
    function _get(url) {
        return fetch(url, { cache: 'no-store' })
            .then(function(r) { return r.json(); });
    }
    function _post(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {})
        }).then(function(r) { return r.json(); });
    }

    // ═════════════════════════════════════════════════════════
    // KPIs (mapping + miroir) + config
    // ═════════════════════════════════════════════════════════

    function chargerConfig() {
        return _get('/api/rotation/config').then(function(cfg) {
            _config = cfg;
            var hard = $('rot-mode-hard-label');
            if (hard) {
                var radio = hard.querySelector('input');
                if (radio) {
                    radio.disabled = !cfg.hard_autorise;
                    if (cfg.hard_autorise) {
                        hard.title = 'Hard : suppression physique du fichier (coordination S6/S7 : fallback soft si partagé)';
                    }
                }
            }
            return cfg;
        }).catch(function() { return null; });
    }

    function chargerMappingStats() {
        _get('/api/rotation/mapping/stats').then(function(s) {
            var el = $('rot-mapping-stats');
            if (!el) return;
            if (s.erreur) {
                el.innerHTML = '<span class="text-warning">⚠ ' + _esc(s.erreur) + '</span>';
                return;
            }
            $('rot-kpi-mapping').textContent = s.taux_couverture_pct + ' %';
            $('rot-kpi-mapping-sub').textContent =
                s.total_actifs + ' / ' + s.total_songs_radiodj + ' songs';
            var lignes = s.par_methode.map(function(m) {
                return '<tr><td>' + _esc(m.methode_matching) + '</td><td>' +
                       m.total + '</td><td>' + m.actifs + '</td><td>' +
                       _esc(m.confiance_moyenne) + '</td></tr>';
            }).join('');
            el.innerHTML =
                '<table class="table table-sm table-hover mb-2">' +
                '<thead><tr><th>Méthode</th><th>Total</th><th>Actifs</th><th>Confiance</th></tr></thead>' +
                '<tbody>' + (lignes || '<tr><td colspan="4" class="text-muted">cache vide — lancer le bootstrap</td></tr>') +
                '</tbody></table>' +
                '<span class="text-muted">Taux de couverture : ' +
                s.taux_couverture_pct + ' % des songs RadioDJ ont une correspondance.</span>';
        }).catch(function() {});
    }

    function chargerMirrorState() {
        _get('/api/rotation/mirror/state').then(function(s) {
            var el = $('rot-mirror-state');
            if (!el) return;
            if (s.erreur) {
                el.innerHTML = '<span class="text-warning">⚠ ' + _esc(s.erreur) + '</span>';
                $('rot-kpi-miroir').textContent = '—';
                $('rot-kpi-miroir-sub').textContent = 'worker 21.3 non déployé';
                return;
            }
            var statuts = {
                ok: ['text-success', '✓ OK'],
                erreur_ssh: ['text-danger', '✗ erreur SSH'],
                erreur_script: ['text-danger', '✗ erreur script'],
                non_configure: ['text-warning', '⚠ non configuré'],
                en_cours: ['text-info', '⏳ en cours'],
                null: ['text-muted', '—']
            };
            var st = statuts[s.last_statut] || statuts['null'];
            $('rot-kpi-miroir').innerHTML =
                '<span class="' + st[0] + '">' + st[1] + '</span>';
            $('rot-kpi-miroir-sub').textContent =
                (s.last_regen ? 'regen : ' + s.last_regen : 'jamais') +
                ' · ' + s.nb_regen_ok + ' OK' +
                (s.pending ? ' · régénération demandée' : '');
            el.innerHTML =
                '<div>Pending : <strong>' + (s.pending ? 'oui' : 'non') + '</strong>' +
                (s.last_raison ? ' — dernière demande : ' + _esc(s.last_raison) : '') + '</div>' +
                '<div>Dernière regen : ' + _esc(s.last_regen || 'jamais') +
                ' — statut : <span class="' + st[0] + '">' + st[1] + '</span></div>' +
                (s.last_message ? '<div class="text-muted">' + _esc(s.last_message) + '</div>' : '');
        }).catch(function() {});
    }

    function chargerHistorique() {
        _get('/api/rotation/history?limit=25').then(function(h) {
            var el = $('rot-history');
            if (!el) return;
            if (h.erreur && !h.operations) {
                el.innerHTML = '<div class="p-3 small text-warning">⚠ ' + _esc(h.erreur) + '</div>';
                return;
            }
            if (!h.operations.length) {
                el.innerHTML = '<div class="p-3 small text-muted">Aucune opération journalisée.</div>';
                return;
            }
            var rows = h.operations.map(function(o) {
                var d = (o.date_op || '').replace('T', ' ').slice(0, 19);
                var cible = [];
                if (o.song_id_radiodj) cible.push('song ' + o.song_id_radiodj);
                if (o.media_id_azura) cible.push('media ' + o.media_id_azura);
                var det = o.details && typeof o.details === 'object'
                    ? Object.keys(o.details).map(function(k) {
                          return k + '=' + _esc(JSON.stringify(o.details[k]).slice(0, 60));
                      }).join(', ') : '';
                return '<tr>' +
                    '<td class="text-muted small">' + _esc(d) + '</td>' +
                    '<td><span class="badge bg-secondary">' + _esc(o.type_op) + '</span></td>' +
                    '<td class="small">' + (o.station_id ? 'S' + o.station_id : '—') + '</td>' +
                    '<td class="small">' + _esc(cible.join(' · ') || '—') + '</td>' +
                    '<td class="small text-muted">' + _esc(o.triggered_by) +
                    (o.trigger_source ? ' (' + _esc(o.trigger_source) + ')' : '') + '</td>' +
                    '<td class="small text-muted">' + det + '</td>' +
                    '</tr>';
            }).join('');
            el.innerHTML =
                '<table class="table table-sm table-hover mb-0">' +
                '<thead><tr><th>Date</th><th>Type</th><th>Station</th><th>Cible</th>' +
                '<th>Déclencheur</th><th>Détails</th></tr></thead>' +
                '<tbody>' + rows + '</tbody></table>';
        }).catch(function() {});
    }

    function chargerLegende() {
        var el = $('rot-legende');
        if (!el || !window.AzuraFlags) return;
        var flags = ['', 'AZURA_PUSHED_S6_S7', 'AZURA_ACTIVE_S6_S7',
                     'AZURA_RETIRED_S6_S7', 'AZURA_REMOVED_S6_S7',
                     'AZURA_ACTIVE_S7', 'AZURA_RETIRED_S7'];
        var html = flags.map(function(f) {
            return '<span>' + window.AzuraFlags.getBadgeHTML(f) +
                   (f ? '' : ' <span class="text-muted small">(vide)</span>') + '</span>';
        }).join('');
        el.innerHTML = html +
            '<p class="small text-muted mb-0 w-100">Les flags sont suffixés par station : ' +
            '<code>_S6</code>, <code>_S7</code>, ou <code>_S6_S7</code> (combiné).</p>';
    }

    // ═════════════════════════════════════════════════════════
    // Paramètres (boîte ⚙️)
    // ═════════════════════════════════════════════════════════

    function toggleConfigBox() {
        var box = $('rot-config-box');
        if (!box) return;
        if (box.classList.contains('d-none')) {
            if (!box.dataset.filled && _config) {
                box.innerHTML =
                    '<div class="row g-2">' +
                    _champNum('ratio_rotation', 'Ratio mensuel (0-1)', _config.ratio_rotation, 0, 1, 0.01) +
                    _champNum('duree_protection_jours', 'Protection nouveaux (j)', _config.duree_protection_jours, 7, 365, 1) +
                    _champNum('seuil_play_count_min', 'Seuil play_count min', _config.seuil_play_count_min, 0, 100, 1) +
                    _champNum('seuil_anciennete_jours', 'Seuil ancienneté date_played (j)', _config.seuil_anciennete_jours, 7, 365, 1) +
                    _champNum('nb_max_candidats', 'Plafond candidats affichés', _config.nb_max_candidats, 50, 5000, 50) +
                    '</div>' +
                    '<div class="text-muted mt-2">Ces paramètres s’appliquent à la prochaine preview ' +
                    '(surcharge ponctuelle des défauts du config.json worker). ' +
                    'Dossiers protégés et catégories restent pilotés côté worker.</div>';
                box.dataset.filled = '1';
            }
            box.classList.remove('d-none');
        } else {
            box.classList.add('d-none');
        }
    }

    function _champNum(id, label, val, min, max, step) {
        return '<div class="col-md-4">' +
            '<label class="form-label mb-0 small">' + _esc(label) + '</label>' +
            '<input type="number" class="form-control form-control-sm" id="rot-p-' + id +
            '" value="' + _esc(val) + '" min="' + min + '" max="' + max + '" step="' + step + '">' +
            '</div>';
    }

    function lireParams() {
        var params = {};
        ['ratio_rotation', 'duree_protection_jours', 'seuil_play_count_min',
         'seuil_anciennete_jours', 'nb_max_candidats'].forEach(function(c) {
            var input = $('rot-p-' + c);
            if (input && input.value !== '' && input.value !== null) {
                var v = parseFloat(input.value);
                if (!isNaN(v)) params[c] = v;
            }
        });
        return params;
    }

    // ═════════════════════════════════════════════════════════
    // Preview + polling + rendu du tableau
    // ═════════════════════════════════════════════════════════

    function lancerPreview() {
        var station = parseInt(($('rot-station') || {}).value || '7', 10);
        $('rot-btn-preview').disabled = true;
        $('rot-preview-status').textContent = 'Création de la tâche…';
        _selection = {};
        majSelection();
        _post('/api/rotation/preview', {
            station_id: station,
            params: lireParams()
        }).then(function(r) {
            if (r.erreur) {
                $('rot-preview-status').innerHTML =
                    '<span class="text-danger">⚠ ' + _esc(r.erreur) + '</span>';
                $('rot-btn-preview').disabled = false;
                return;
            }
            $('rot-preview-status').textContent =
                'Tâche #' + r.task_id + ' créée — calcul en cours sur le worker ' +
                '(list_media + scoring, ~1-3 min)…';
            pollerPreview(r.task_id);
        }).catch(function(e) {
            $('rot-preview-status').innerHTML = '<span class="text-danger">⚠ ' + _esc(e) + '</span>';
            $('rot-btn-preview').disabled = false;
        });
    }

    function pollerPreview(taskId) {
        if (_pollTimer) clearTimeout(_pollTimer);
        var essais = 0;
        function tick() {
            essais++;
            _get('/api/rotation/preview/' + taskId).then(function(r) {
                $('rot-log').textContent = r.log_tail || '—';
                if (r.erreur && r.statut !== 'pret') {
                    $('rot-preview-status').innerHTML =
                        '<span class="text-danger">⚠ ' + _esc(r.erreur) + '</span>';
                    $('rot-btn-preview').disabled = false;
                    return;
                }
                if (r.statut === 'pret') {
                    $('rot-preview-status').innerHTML =
                        '<span class="text-success">✓ Preview prête</span> — ' +
                        r.nb_candidats + ' candidat(s) / ' + r.nb_groupes + ' groupe(s) — ' +
                        'générée ' + _esc(r.date_preview || '') +
                        ' <span class="text-muted">(station S' + r.station_id + ')</span>';
                    $('rot-kpi-candidats').textContent = r.nb_candidats;
                    $('rot-kpi-candidats-sub').textContent =
                        'preview S' + r.station_id + ' du ' +
                        (r.date_preview || '').slice(0, 16);
                    if (r.config_effective && r.config_effective.note_hard) {
                        $('rot-preview-status').innerHTML +=
                            '<div class="text-muted small">' +
                            _esc(r.config_effective.note_hard) + '</div>';
                    }
                    rendreTablePreview(r.groupes || []);
                    $('rot-btn-preview').disabled = false;
                    chargerMappingStats();   // la preview a alimenté le cache
                    chargerHistorique();
                } else if (r.statut === 'erreur') {
                    $('rot-preview-status').innerHTML =
                        '<span class="text-danger">✗ Erreur worker (voir log)</span>';
                    $('rot-btn-preview').disabled = false;
                } else if (essais < 200) {
                    _pollTimer = setTimeout(tick, 3000);
                } else {
                    $('rot-preview-status').innerHTML =
                        '<span class="text-warning">⚠ délai dépassé (tâche #' + taskId + ')</span>';
                    $('rot-btn-preview').disabled = false;
                }
            }).catch(function() {
                if (essais < 200) _pollTimer = setTimeout(tick, 3000);
            });
        }
        tick();
    }

    function _classeScore(s) {
        return s >= 70 ? 'score-high' : (s >= 40 ? 'score-mid' : 'score-low');
    }

    function rendreTablePreview(groupes) {
        var cible = $('rot-preview-table');
        if (!groupes.length) {
            cible.innerHTML =
                '<div class="text-muted small p-3">Aucun candidat au retrait ' +
                'pour ces paramètres (catalogue sain ou tout est protégé).</div>';
            return;
        }
        var html = '<table class="table table-sm table-hover mb-0">' +
            '<thead><tr>' +
            '<th style="width:36px"><input type="checkbox" id="rot-check-all" title="Tout sélectionner"></th>' +
            '<th style="width:60px">Score</th><th>Titre / Artiste</th>' +
            '<th style="width:140px">État actuel</th>' +
            '<th style="width:80px">Weight</th><th style="width:90px">Play count</th>' +
            '<th style="width:90px">Dernière diff.</th>' +
            '<th>Raisons</th></tr></thead><tbody>';
        groupes.forEach(function(g, gi) {
            html += '<tr class="rot-group-head">' +
                '<td><input type="checkbox" class="rot-check-group" data-gi="' + gi + '"></td>' +
                '<td class="' + _classeScore(g.score_groupe) + '">' + g.score_groupe + '</td>' +
                '<td><strong>' + _esc(g.artist) + '</strong> — ' + _esc(g.title) +
                (g.nb_versions > 1 ? ' <span class="badge bg-info text-dark">' +
                    g.nb_versions + ' versions</span>' : '') + '</td>' +
                '<td colspan="6" class="text-muted small">' +
                g.raisons_groupe.map(function(r) {
                    return '<span class="rot-reason">' + _esc(r) + '</span>';
                }).join(' ') + '</td></tr>';
            (g.versions || []).forEach(function(v) {
                var badge = window.AzuraFlags ? window.AzuraFlags.getBadgeHTML(v.flag) : _esc(v.flag);
                html += '<tr data-song="' + v.song_id + '">' +
                    '<td><input type="checkbox" class="rot-check-song" data-song="' + v.song_id + '"></td>' +
                    '<td class="' + _classeScore(v.score) + '">' + v.score + '</td>' +
                    '<td class="ps-4 small">' + _esc(v.artist) + ' — ' + _esc(v.title) +
                    (v.media_trouve ? '' :
                        ' <span class="badge bg-warning text-dark" title="Pas de correspondance AzuraCast (mapping)">⚠ non résolu</span>') +
                    '</td>' +
                    '<td>' + badge + '</td>' +
                    '<td class="small">' + _esc(v.weight) + '</td>' +
                    '<td class="small">' + _esc(v.play_count) + '</td>' +
                    '<td class="small text-muted">' +
                    (v.anciennete_jours === null || v.anciennete_jours === undefined
                        ? 'jamais' : 'il y a ' + v.anciennete_jours + 'j') + '</td>' +
                    '<td class="small">' +
                    (v.raisons || []).map(function(r) {
                        return '<span class="rot-reason">' + _esc(r) + '</span>';
                    }).join(' ') + '</td></tr>';
            });
        });
        html += '</tbody></table>';
        cible.innerHTML = html;

        // Wiring des cases
        cible.querySelectorAll('.rot-check-song').forEach(function(cb) {
            cb.addEventListener('change', function() {
                var sid = parseInt(cb.dataset.song, 10);
                if (cb.checked) _selection[sid] = true;
                else delete _selection[sid];
                majSelection();
            });
        });
        cible.querySelectorAll('.rot-check-group').forEach(function(cb) {
            cb.addEventListener('change', function() {
                var gi = parseInt(cb.dataset.gi, 10);
                var ligne = cible.querySelectorAll('.rot-group-head')[gi];
                if (!ligne) return;
                var tr = ligne.nextElementSibling;
                while (tr && !tr.classList.contains('rot-group-head')) {
                    var cbSong = tr.querySelector('.rot-check-song');
                    if (cbSong) {
                        cbSong.checked = cb.checked;
                        var sid = parseInt(cbSong.dataset.song, 10);
                        if (cb.checked) _selection[sid] = true;
                        else delete _selection[sid];
                    }
                    tr = tr.nextElementSibling;
                }
                majSelection();
            });
        });
        var checkAll = $('rot-check-all');
        if (checkAll) {
            checkAll.addEventListener('change', function() {
                cible.querySelectorAll('.rot-check-song').forEach(function(cb) {
                    cb.checked = checkAll.checked;
                    var sid = parseInt(cb.dataset.song, 10);
                    if (checkAll.checked) _selection[sid] = true;
                    else delete _selection[sid];
                });
                majSelection();
            });
        }
    }

    function majSelection() {
        var n = Object.keys(_selection).length;
        $('rot-selection-info').textContent = n + ' sélectionné' + (n > 1 ? 's' : '');
        $('rot-btn-execute').disabled = (n === 0);
    }

    // ═════════════════════════════════════════════════════════
    // Exécution
    // ═════════════════════════════════════════════════════════

    function executerRotation() {
        var ids = Object.keys(_selection).map(function(k) { return parseInt(k, 10); });
        if (!ids.length) return;
        var mode = (document.querySelector('input[name="rot-mode"]:checked') || {}).value || 'soft';
        var station = parseInt(($('rot-station') || {}).value || '7', 10);
        if (mode === 'hard' && !confirm(
                'Mode HARD : suppression physique des fichiers côté AzuraCast.\n' +
                'Les médias partagés S6/S7 retomberont automatiquement en SOFT.\n\n' +
                'Confirmer la suppression sur la station ' + station + ' (' + ids.length + ' titre(s)) ?')) {
            return;
        }
        if (!confirm('Exécuter la rotation ' + mode.toUpperCase() + ' sur ' +
                     ids.length + ' titre(s) — station ' + station + ' ?')) {
            return;
        }
        $('rot-btn-execute').disabled = true;
        $('rot-preview-status').innerHTML = 'Création de la tâche d’exécution…';
        _post('/api/rotation/execute', {
            station_id: station,
            song_ids: ids,
            mode: mode
        }).then(function(r) {
            if (r.erreur) {
                $('rot-preview-status').innerHTML =
                    '<span class="text-danger">⚠ ' + _esc(r.erreur) + '</span>' +
                    (r.mode_force ? ' <span class="text-muted">(le worker retombera en soft)</span>' : '');
                majSelection();
                return;
            }
            $('rot-preview-status').textContent =
                'Tâche #' + r.task_id + ' créée (' + r.mode + ', ' + r.nb_titres +
                ' titre(s)) — exécution en cours…';
            pollerTache(r.task_id, function(t) {
                $('rot-log').textContent = t.log_tail || '—';
            }, function(t) {
                $('rot-preview-status').innerHTML =
                    '<span class="' + (t.statut === 'termine' ? 'text-success' : 'text-danger') +
                    '">' + (t.statut === 'termine' ? '✓ Rotation terminée' : '✗ Erreur') +
                    '</span> — voir log ci-dessous. Le miroir sera régénéré automatiquement.';
                _selection = {};
                majSelection();
                chargerMirrorState();
                chargerHistorique();
                chargerMappingStats();
            });
        }).catch(function(e) {
            $('rot-preview-status').innerHTML = '<span class="text-danger">⚠ ' + _esc(e) + '</span>';
            majSelection();
        });
    }

    // ═════════════════════════════════════════════════════════
    // Bootstrap mapping
    // ═════════════════════════════════════════════════════════

    function lancerBootstrap() {
        $('rot-btn-bootstrap').disabled = true;
        $('rot-bootstrap-status').textContent = 'Création de la tâche…';
        _post('/api/rotation/mapping/bootstrap', {}).then(function(r) {
            if (r.erreur) {
                $('rot-bootstrap-status').innerHTML =
                    '<span class="text-danger">⚠ ' + _esc(r.erreur) + '</span>';
                $('rot-btn-bootstrap').disabled = false;
                return;
            }
            $('rot-bootstrap-status').textContent =
                'Tâche #' + r.task_id + ' — remplissage en cours (~1-2 min par station)…';
            pollerTache(r.task_id, function(t) {
                $('rot-log').textContent = t.log_tail || '—';
            }, function() {
                $('rot-bootstrap-status').innerHTML =
                    '<span class="text-success">✓ Bootstrap terminé</span>';
                $('rot-btn-bootstrap').disabled = false;
                chargerMappingStats();
                chargerHistorique();
            });
        }).catch(function(e) {
            $('rot-bootstrap-status').innerHTML = '<span class="text-danger">⚠ ' + _esc(e) + '</span>';
            $('rot-btn-bootstrap').disabled = false;
        });
    }

    function pollerTache(taskId, onTick, onDone) {
        var essais = 0;
        function tick() {
            essais++;
            _get('/api/rotation/task/' + taskId).then(function(t) {
                if (onTick) onTick(t);
                if (t.statut === 'termine' || t.statut === 'erreur') {
                    if (onDone) onDone(t);
                } else if (essais < 600) {
                    // Hotfix C : 600 essais x 3 s = 30 min (avant : 200 = 10 min,
                    // abandon SILENCIEUX — le bootstrap peut rester en_attente
                    // plus de 10 min, le poller mourait avant l'exécution).
                    setTimeout(tick, 3000);
                } else {
                    var _msg = '<span class="text-warning">⚠ délai dépassé '
                        + '(tâche #' + taskId + ') — la tâche continue côté worker, '
                        + 'relancez l\'onglet pour suivre son log</span>';
                    var _el = $('rot-bootstrap-status');
                    if (_el) _el.innerHTML = _msg;
                    var _el2 = $('rot-preview-status');
                    if (_el2) _el2.innerHTML = _msg;
                    var _b1 = $('rot-btn-bootstrap');
                    if (_b1) _b1.disabled = false;
                    var _b2 = $('rot-btn-preview');
                    if (_b2) _b2.disabled = false;
                }
            }).catch(function() {
                if (essais < 600) setTimeout(tick, 3000);
            });
        }
        tick();
    }

    function chargerDerniereTache() {
        // Hotfix C : remplit la carte « Log de la dernière tâche worker » à
        // l'ouverture/rafraîchissement de l'onglet (endpoint /task/last).
        _get('/api/rotation/task/last').then(function(t) {
            if (!t || !t.task_id) return;   // aucune tâche rotation/mapping connue
            var _el = $('rot-log');
            if (_el) _el.textContent = t.log_tail || '—';
        }).catch(function() { /* silencieux : carte log non critique */ });
    }

    // ═════════════════════════════════════════════════════════
    // Init
    // ═════════════════════════════════════════════════════════

    function init() {
        var pane = $('nav-rotation');
        if (!pane) return;  // onglet absent (déploiement partiel)

        $('rot-btn-config').addEventListener('click', toggleConfigBox);
        $('rot-btn-preview').addEventListener('click', lancerPreview);
        $('rot-btn-execute').addEventListener('click', executerRotation);
        $('rot-btn-bootstrap').addEventListener('click', lancerBootstrap);
        $('rot-btn-history').addEventListener('click', chargerHistorique);
        var selStation = $('rot-station');
        if (selStation) {
            selStation.addEventListener('change', function() {
                $('rot-kpi-medias-sub').textContent = 'station ' + selStation.value;
            });
        }

        chargerConfig();
        chargerMappingStats();
        chargerMirrorState();
        chargerHistorique();
        chargerLegende();
        chargerDerniereTache();   // Hotfix C : carte log dès l'ouverture

        // Refresh des KPIs à chaque affichage de l'onglet
        var btnTab = $('nav-rotation-tab');
        if (btnTab) {
            btnTab.addEventListener('click', function() {
                setTimeout(function() {
                    chargerMappingStats();
                    chargerMirrorState();
                    chargerHistorique();
                    chargerDerniereTache();   // Hotfix C
                }, 150);
            });
        }
        _logConsole('onglet Rotation éditoriale initialisé (v2, hotfix C)');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
