// [P3-S5] Phase 3 Étape 5 — Extraction JS pool-tiers (validation, stats, auto-check, sheets)
/**
 * AIRVS Dashboard - pool-tiers
 * Extrait de index.html (Phase 3, etape 5a)
 * Onglet Pool Tiers : validation, stats, auto-check.
 */

    // Alias vers utilitaires globaux (utils.js)
    var escapeHtml = window.escapeHtml || null;

// ════════════════════════════════════════════════════════════════
// ONGLET 6 — POOL TIERS (2026.06.24-B)
// ════════════════════════════════════════════════════════════════
(function poolTiersModule() {
    'use strict';

    // ── État interne ──
    let poolOffset = 0;
    const POOL_LIMIT = 50;

    // ── Helpers ──
    function $(id) { return document.getElementById(id); }
    function fmtDate(s) {
        if (!s) return '—';
        const d = new Date(s);
        if (isNaN(d.getTime())) return s;
        return d.toLocaleString('fr-FR', {
            day:'2-digit', month:'2-digit', year:'numeric',
            hour:'2-digit', minute:'2-digit'
        });
    }

    // ── 1. Déclenchement validation + polling de statut ──
    let poolPollTimer = null;
    let poolPollStart = 0;

    function setFormMsg(cls, html) {
        const msg = $('pool_form_msg');
        msg.style.display = 'block';
        msg.className = 'alert ' + cls + ' py-2 small';
        msg.innerHTML = html;
    }

    function stopPoolPoll(finalMsg) {
        if (poolPollTimer) { clearInterval(poolPollTimer); poolPollTimer = null; }
        const btn = $('pool_btn_valider');
        btn.disabled = false;
        btn.textContent = '🔄 Lancer';
        if (finalMsg) {
            // Recharger les listes après un court délai
            setTimeout(() => { loadStats(); loadDerniersPools(); loadPoolsDropdown(); loadResultats(); }, 1000);
        }
    }

    function pollTaskStatus(taskId) {
        const elapsed = Math.floor((Date.now() - poolPollStart) / 1000);
        // Time out après 300s (5 min) — la validation peut être lente
        // (matching flou multi-passes sur 100-300 titres)
        if (elapsed > 300) {
            setFormMsg('alert-warning',
                '⏱ Tâche #' + taskId + ' toujours en cours après 5 min. ' +
                'C\'est anormalement long — vérifiez les logs : <code>journalctl -u airvs-worker -f</code><br>' +
                '<small>La tâche continue en arrière-plan côté worker. Le tableau des résultats se remplira dès qu\'elle passera à <code>termine</code>. ' +
                'Cliquez sur ↻ dans la carte Statistiques pour rafraîchir manuellement.</small>');
            stopPoolPoll(false);
            return;
        }

        fetch('/api/tache_debug/' + taskId + '?t=' + Date.now())
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'ok' || !data.tache) {
                setFormMsg('alert-danger', '❌ Impossible de récupérer le statut de la tâche #' + taskId);
                stopPoolPoll(false);
                return;
            }
            const t = data.tache;
            const statut = t.statut;
            const log = (t.log_resultat || '').trim();
            const logShort = log ? log.split('\n').slice(-3).join('\n') : '';

            if (statut === 'en_attente') {
                let hint = '';
                if (elapsed > 8) {
                    hint = '<br><strong class="text-danger">⚠ Diagnostic :</strong> la tâche est restée <code>en_attente</code> plus de 8s. ' +
                           'Le worker Ubuntu ne la prend pas en charge. Causes probables :<br>' +
                           '• Le worker n\'a pas été redéployé (il ne connaît pas <code>VALIDATION_POOL_SHEETS</code>) — vérifiez le badge en haut à droite.<br>' +
                           '• Le service systemd est à l\'arrêt : <code>sudo systemctl status airvs-worker</code><br>' +
                           '• Le module <code>validation_pool_multiformat.py</code> manque — le worker plante à l\'import.';
                }
                setFormMsg('alert-info',
                    '⏳ Tâche #' + taskId + ' <span class="badge bg-secondary">en_attente</span> (' + elapsed + 's)' + hint +
                    (logShort ? '<br><small class="text-muted">Derniers logs :</small><pre class="small mb-0">' + escapeHtml(logShort) + '</pre>' : ''));
            }
            else if (statut === 'en_cours') {
                let hint = '';
                if (elapsed > 30 && elapsed <= 60) {
                    hint = '<br><small class="text-info">⏳ Validation en cours — normal pour 100-300 titres (matching flou multi-passes, ~0,5 s par titre).</small>';
                } else if (elapsed > 60) {
                    hint = '<br><small class="text-warning">⏳ Plus d\'1 min — patientez encore, le worker traite les titres un par un.</small>';
                }
                setFormMsg('alert-info',
                    '⚙️ Tâche #' + taskId + ' <span class="badge bg-primary">en_cours</span> (' + elapsed + 's)' + hint +
                    (logShort ? '<br><small class="text-muted">Derniers logs :</small><pre class="small mb-0">' + escapeHtml(logShort) + '</pre>' : ''));
            }
            else if (statut === 'termine') {
                setFormMsg('alert-success',
                    '✅ Tâche #' + taskId + ' <span class="badge bg-success">terminée</span> (' + elapsed + 's)' +
                    (logShort ? '<br><small class="text-muted">Derniers logs :</small><pre class="small mb-0">' + escapeHtml(logShort) + '</pre>' : ''));
                stopPoolPoll(true);
            }
            else if (statut === 'erreur') {
                setFormMsg('alert-danger',
                    '❌ Tâche #' + taskId + ' <span class="badge bg-danger">ERREUR</span> (' + elapsed + 's)' +
                    (log ? '<br><small class="text-muted">Log complet :</small><pre class="small mb-0" style="max-height:200px; overflow:auto;">' + escapeHtml(log) + '</pre>' : ''));
                stopPoolPoll(false);
            }
            else {
                setFormMsg('alert-warning', '❓ Tâche #' + taskId + ' statut inconnu : <code>' + escapeHtml(statut) + '</code>');
                stopPoolPoll(false);
            }
        })
        .catch(err => {
            console.error('[pool poll]', err);
        });
    }

    function initFormValider() {
        const form = $('pool_form');
        if (!form) return;
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            const body = {
                sheets_id: $('pool_sheets_id').value.trim(),
                onglet: $('pool_onglet').value,
                semaine_filter: $('pool_semaine').value.trim(),
                dry_run: $('pool_dry_run').checked
            };
            setFormMsg('alert-info', '⏳ Création de la tâche…');
            const btn = $('pool_btn_valider');
            btn.disabled = true;
            btn.textContent = '⏳ Envoi…';

            fetch('/api/pool/valider', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify(body)
            })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'ok') {
                    const taskId = data.task_id;
                    setFormMsg('alert-info',
                        '✅ Tâche #' + taskId + ' créée. Sheet <code>' + escapeHtml(body.sheets_id) + '</code> onglet <code>' +
                        escapeHtml(body.onglet) + '</code>.<br>' + escapeHtml(data.message) + '<br>' +
                        '<small class="text-muted">Suivi du statut en temps réel…</small>');
                    // Démarrer le polling
                    poolPollStart = Date.now();
                    if (poolPollTimer) clearInterval(poolPollTimer);
                    poolPollTimer = setInterval(() => pollTaskStatus(taskId), 2000);
                    // Premier appel immédiat
                    pollTaskStatus(taskId);
                } else {
                    btn.disabled = false;
                    btn.textContent = '🔄 Lancer';
                    setFormMsg('alert-danger', '❌ Erreur : ' + (data.message || 'inconnue'));
                }
            })
            .catch(err => {
                btn.disabled = false;
                btn.textContent = '🔄 Lancer';
                setFormMsg('alert-danger', '❌ Erreur réseau : ' + err);
            });
        });

        // Présets
        document.querySelectorAll('.pool-preset').forEach(btn => {
            btn.addEventListener('click', function() {
                $('pool_sheets_id').value = this.dataset.alias;
                $('pool_onglet').value = this.dataset.onglet;
            });
        });

        // Pré-sélection du mois courant dans le <select> Onglet
        (function preselectPoolMonth() {
            const sel = $('pool_onglet');
            if (!sel) return;
            const MONTHS = ['JANVIER','FEVRIER','MARS','AVRIL','MAI','JUIN',
                            'JUILLET','AOUT','SEPTEMBRE','OCTOBRE','NOVEMBRE','DECEMBRE'];
            const currentMonth = MONTHS[new Date().getMonth()];
            for (let i = 0; i < sel.options.length; i++) {
                if (sel.options[i].value === currentMonth) {
                    sel.selectedIndex = i;
                    break;
                }
            }
        })();
    }

    // ── 2. Stats + par source + par semaine ──
    function loadStats() {
        fetch('/api/pool/stats?t=' + Date.now())
        .then(r => r.json())
        .then(data => {
            if (data.error) return;
            $('pool_stat_total').textContent  = data.total  ?? '—';
            $('pool_stat_valide').textContent = data.valide ?? '—';
            $('pool_stat_rejete').textContent = data.rejete ?? '—';
            $('pool_stat_doublon').textContent= data.doublon?? '—';

            const srcTbody = $('pool_stats_source_tbody');
            if (!data.par_source || data.par_source.length === 0) {
                srcTbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">—</td></tr>';
            } else {
                srcTbody.innerHTML = data.par_source.map(r =>
                    '<tr><td>' + escapeHtml(r.source) + '</td>' +
                    '<td class="text-end">' + r.total + '</td>' +
                    '<td class="text-end text-success">' + (r.valide || 0) + '</td>' +
                    '<td class="text-end text-danger">' + (r.rejete || 0) + '</td></tr>'
                ).join('');
            }

            const semTbody = $('pool_stats_semaine_tbody');
            if (!data.par_semaine || data.par_semaine.length === 0) {
                semTbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">—</td></tr>';
            } else {
                semTbody.innerHTML = data.par_semaine.map(r =>
                    '<tr><td>' + escapeHtml(r.semaine) + '</td>' +
                    '<td class="text-end">' + r.total + '</td>' +
                    '<td class="text-end text-success">' + (r.valide || 0) + '</td>' +
                    '<td class="text-end text-danger">' + (r.rejete || 0) + '</td></tr>'
                ).join('');
            }
        })
        .catch(err => console.error('[pool stats]', err));
    }

    // ── 3. Derniers pools (clic filtrant) ──
    function loadDerniersPools() {
        fetch('/api/pool/stats?t=' + Date.now())
        .then(r => r.json())
        .then(data => {
            const tbody = $('pool_derniers_tbody');
            if (!data.derniers_pools || data.derniers_pools.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">Aucun pool pour l\'instant — lancez une validation.</td></tr>';
                return;
            }
            tbody.innerHTML = data.derniers_pools.map(p =>
                '<tr style="cursor:pointer;" data-id-pool="' + escapeHtml(p.id_pool) + '">' +
                '<td><code>' + escapeHtml(p.id_pool) + '</code></td>' +
                '<td>' + escapeHtml(p.sheet_alias) + '</td>' +
                '<td><span class="badge bg-secondary">' + escapeHtml(p.source) + '</span></td>' +
                '<td class="text-end">' + (p.total || 0) + '</td>' +
                '<td class="text-end text-success">' + (p.valide || 0) + '</td>' +
                '<td class="text-end text-danger">' + (p.rejete || 0) + '</td>' +
                '<td><small>' + fmtDate(p.derniere_validation) + '</small></td>' +
                '</tr>'
            ).join('');
            // Clic → filtre le tableau de résultats
            tbody.querySelectorAll('tr').forEach(tr => {
                tr.addEventListener('click', function() {
                    const idPool = this.dataset.idPool;
                    $('pool_filter_id_pool').value = idPool;
                    poolOffset = 0;
                    loadResultats();
                });
            });
        })
        .catch(err => console.error('[pool derniers]', err));
    }

    // ── 4. Dropdown id_pool dans filtres ──
    function loadPoolsDropdown() {
        fetch('/api/pools/list?t=' + Date.now())
        .then(r => r.json())
        .then(list => {
            const sel = $('pool_filter_id_pool');
            const current = sel.value;
            sel.innerHTML = '<option value="">— Tous les pools —</option>';
            list.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.id_pool;
                opt.textContent = `${p.id_pool}  (OK:${p.nb_valide||0} / KO:${p.nb_rejete||0})`;
                sel.appendChild(opt);
            });
            sel.value = current;
        })
        .catch(err => console.error('[pool dropdown]', err));
    }

    // ── 5. Résultats paginés + filtres ──
    function loadResultats() {
        const params = new URLSearchParams();
        const idPool    = $('pool_filter_id_pool').value;
        const source    = $('pool_filter_source').value;
        const statut    = $('pool_filter_statut').value;
        const semaine   = $('pool_filter_semaine').value.trim();
        const recherche = $('pool_filter_recherche').value.trim();
        if (idPool)    params.set('id_pool', idPool);
        if (source)    params.set('source', source);
        if (statut)    params.set('statut', statut);
        if (semaine)   params.set('semaine', semaine);
        if (recherche) params.set('recherche', recherche);
        params.set('limit', POOL_LIMIT);
        params.set('offset', poolOffset);

        fetch('/api/pool/resultats?' + params.toString())
        .then(r => r.json())
        .then(data => {
            const tbody = $('pool_resultats_tbody');
            if (!data.rows || data.rows.length === 0) {
                tbody.innerHTML = '<tr><td colspan="12" class="text-center text-muted">Aucun résultat — ajustez les filtres ou lancez une validation.</td></tr>';
            } else {
                tbody.innerHTML = data.rows.map(r => {
                    const statutBadge = r.statut === 'valide'
                        ? '<span class="badge bg-success">✓</span>'
                        : (r.statut === 'doublon'
                            ? '<span class="badge bg-warning">⧉</span>'
                            : '<span class="badge bg-danger">✗</span>');
                    return '<tr>' +
                        '<td>' + statutBadge + '</td>' +
                        '<td><small>' + escapeHtml(r.source) + '</small></td>' +
                        '<td>' + escapeHtml(r.semaine) + '</td>' +
                        '<td>' + escapeHtml(r.jour) + '</td>' +
                        '<td>' + escapeHtml(r.artiste_saisi) + '</td>' +
                        '<td>' + escapeHtml(r.titre_saisi) + '</td>' +
                        '<td>' + (r.annee_saisie || '') + '</td>' +
                        '<td>' + (r.song_id ? '<a href="#" class="song-link" data-id="' + r.song_id + '">' + r.song_id + '</a>' : '—') + '</td>' +
                        '<td>' + (r.score_match != null ? r.score_match : '—') + '</td>' +
                        '<td><small class="text-muted">' + escapeHtml(r.motif_rejet || '') + '</small></td>' +
                        '<td><small>' + escapeHtml(r.feuille) + ' / L' + (r.ligne || '?') + '</small></td>' +
                        '<td><small>' + fmtDate(r.date_validation) + '</small></td>' +
                        '</tr>';
                }).join('');
            }
            // Pagination
            const total = data.total || 0;
            const from  = total === 0 ? 0 : poolOffset + 1;
            const to    = Math.min(poolOffset + POOL_LIMIT, total);
            $('pool_pagination_info').textContent =
                `${from}-${to} / ${total} (page ${Math.floor(poolOffset/POOL_LIMIT)+1})`;
            $('pool_btn_prev').disabled = (poolOffset === 0);
            $('pool_btn_next').disabled = (to >= total);
        })
        .catch(err => console.error('[pool resultats]', err));
    }

    function initFiltresResultats() {
        $('pool_btn_filter').addEventListener('click', function() {
            poolOffset = 0;
            loadResultats();
        });
        $('pool_btn_reset_filter').addEventListener('click', function() {
            $('pool_filter_id_pool').value = '';
            $('pool_filter_source').value = '';
            $('pool_filter_statut').value = '';
            $('pool_filter_semaine').value = '';
            $('pool_filter_recherche').value = '';
            poolOffset = 0;
            loadResultats();
        });
        $('pool_btn_prev').addEventListener('click', function() {
            if (poolOffset > 0) {
                poolOffset = Math.max(0, poolOffset - POOL_LIMIT);
                loadResultats();
            }
        });
        $('pool_btn_next').addEventListener('click', function() {
            poolOffset += POOL_LIMIT;
            loadResultats();
        });
        // Export CSV — ouvre directement l'URL
        $('pool_btn_export').addEventListener('click', function(e) {
            e.preventDefault();
            const params = new URLSearchParams();
            const idPool = $('pool_filter_id_pool').value;
            const source = $('pool_filter_source').value;
            const statut = $('pool_filter_statut').value;
            const semaine = $('pool_filter_semaine').value.trim();
            const recherche = $('pool_filter_recherche').value.trim();
            if (idPool)    params.set('id_pool', idPool);
            if (source)    params.set('source', source);
            if (statut)    params.set('statut', statut);
            if (semaine)   params.set('semaine', semaine);
            if (recherche) params.set('recherche', recherche);
            window.location.href = '/api/pool/export?' + params.toString();
        });
    }

    // ── 6. Auto-validations ──
    function loadAutoValidations() {
        fetch('/api/pool/auto/list?t=' + Date.now())
        .then(r => r.json())
        .then(data => {
            const tbody = $('pool_auto_tbody');
            if (!data || data.status !== 'ok' || !data.items || data.items.length === 0) {
                tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted">Aucune auto-validation configurée.</td></tr>';
                return;
            }
            const list = data.items;
            const joursFr = ['Lun','Mar','Mer','Jeu','Ven','Sam','Dim'];
            tbody.innerHTML = list.map(a =>
                '<tr>' +
                '<td>' + a.id + '</td>' +
                '<td><code>' + escapeHtml(a.sheet_alias) + '</code></td>' +
                '<td>' + escapeHtml(a.onglet) + '</td>' +
                '<td><span class="badge ' + (a.frequence === 'hebdo' ? 'bg-info' : 'bg-secondary') + '">' +
                    escapeHtml(a.frequence) + '</span></td>' +
                '<td>' + escapeHtml((a.heure || '').slice(0,5)) + '</td>' +
                '<td>' + (a.jour_semaine ? joursFr[a.jour_semaine-1] || a.jour_semaine : '—') + '</td>' +
                '<td><div class="form-check form-switch">' +
                    '<input class="form-check-input pool-auto-toggle" type="checkbox" ' +
                    (a.actif == 1 ? 'checked' : '') + ' data-id="' + a.id + '"></div></td>' +
                '<td><small>' + fmtDate(a.derniere_execution) + '</small></td>' +
                '<td><button class="btn btn-sm btn-outline-danger pool-auto-delete" data-id="' + a.id + '">🗑</button></td>' +
                '</tr>'
            ).join('');

            // Toggle actif
            tbody.querySelectorAll('.pool-auto-toggle').forEach(chk => {
                chk.addEventListener('change', function() {
                    fetch('/api/pool/auto/toggle', {
                        method: 'POST',
                        headers: {'Content-Type':'application/json'},
                        body: JSON.stringify({id: parseInt(this.dataset.id), actif: this.checked})
                    }).then(r => r.json()).then(d => {
                        if (d.status !== 'ok') {
                            alert('Erreur toggle : ' + (d.message || 'inconnue'));
                            loadAutoValidations();
                        }
                    });
                });
            });
            // Delete
            tbody.querySelectorAll('.pool-auto-delete').forEach(btn => {
                btn.addEventListener('click', function() {
                    if (!confirm('Supprimer cette auto-validation ?')) return;
                    fetch('/api/pool/auto/delete', {
                        method: 'POST',
                        headers: {'Content-Type':'application/json'},
                        body: JSON.stringify({id: parseInt(this.dataset.id)})
                    }).then(r => r.json()).then(d => {
                        if (d.status === 'ok') loadAutoValidations();
                        else alert('Erreur suppression : ' + (d.message || 'inconnue'));
                    });
                });
            });
        })
        .catch(err => console.error('[pool auto list]', err));
    }

    function initFormAuto() {
        const freq = $('pool_auto_frequence');
        const jourWrap = $('pool_auto_jour_wrap');
        freq.addEventListener('change', function() {
            jourWrap.style.display = (this.value === 'hebdo') ? 'block' : 'none';
        });

        $('pool_auto_form').addEventListener('submit', function(e) {
            e.preventDefault();
            const body = {
                sheet_alias: $('pool_auto_alias').value.trim(),
                onglet:      $('pool_auto_onglet').value.trim(),
                frequence:   freq.value,
                heure:       $('pool_auto_heure').value + ':00',
                jour_semaine: freq.value === 'hebdo' ? parseInt($('pool_auto_jour').value) : null,
                actif: true
            };
            if (!body.sheet_alias || !body.onglet) {
                alert('Sheet alias et onglet sont requis.');
                return;
            }
            fetch('/api/pool/auto/upsert', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify(body)
            })
            .then(r => r.json())
            .then(d => {
                if (d.status === 'ok') {
                    $('pool_auto_alias').value = '';
                    $('pool_auto_onglet').value = '';
                    loadAutoValidations();
                } else {
                    alert('Erreur : ' + (d.message || 'inconnue'));
                }
            })
            .catch(err => alert('Erreur réseau : ' + err));
        });
    }

    // ── Auto-check Google Sheets ──

    let autoCheckAvailableSheets = [];

    function loadAutoCheckConfig() {
        fetch('/api/auto_check/config?t=' + Date.now())
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'ok') return;
            const cfg = data.config;
            $('auto_check_actif').checked = !!cfg.actif;
            $('auto_check_frequence').value = cfg.frequence || '2x_jour';
            updateHoraireFields(cfg.frequence, cfg.horaires || ['03:00','15:00']);
            renderSheetCheckboxes(cfg.sheets_surveilles || []);
            $('auto_check_last_exec').textContent = cfg.derniere_execution ? fmtDate(cfg.derniere_execution) : '—';
            $('auto_check_next_exec').textContent = cfg.prochaine_execution ? fmtDate(cfg.prochaine_execution) : '—';
            updateFrequenceDisplay(cfg.frequence);
        })
        .catch(err => console.error('[auto_check config]', err));
    }

    function loadAutoCheckSheets() {
        fetch('/api/sheets/list?t=' + Date.now())
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'ok') return;
            autoCheckAvailableSheets = data.sheets || [];
            // Re-render checkboxes si déjà chargé
            loadAutoCheckConfig();
            // Populate Sheet alias dropdowns
            populateSheetAliasSelects(autoCheckAvailableSheets);
        })
        .catch(err => console.error('[auto_check sheets]', err));

    // ── Populate Sheet alias dropdowns (Carte 1 + Carte 5) ──
    function populateSheetAliasSelects(sheets) {
        // Populate pool_sheets_id (Carte 1)
        const sel1 = $('pool_sheets_id');
        if (sel1 && sel1.tagName === 'SELECT') {
            const prev1 = sel1.value;
            sel1.innerHTML = '<option value="">-- Choisir un Sheet --</option>';
            sheets.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.alias;
                opt.textContent = s.alias + (s.nom ? ' (' + s.nom + ')' : '');
                sel1.appendChild(opt);
            });
            // Restore previous selection if still available
            if (prev1 && Array.from(sel1.options).some(o => o.value === prev1)) {
                sel1.value = prev1;
            }
        }
        // Populate pool_auto_alias (Carte 5)
        const sel2 = $('pool_auto_alias');
        if (sel2 && sel2.tagName === 'SELECT') {
            const prev2 = sel2.value;
            sel2.innerHTML = '<option value="">-- Choisir un Sheet --</option>';
            sheets.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.alias;
                opt.textContent = s.alias + (s.nom ? ' (' + s.nom + ')' : '');
                sel2.appendChild(opt);
            });
            if (prev2 && Array.from(sel2.options).some(o => o.value === prev2)) {
                sel2.value = prev2;
            }
        }
    }
    }

    function renderSheetCheckboxes(selected) {
        const container = $('auto_check_sheets_container');
        if (!autoCheckAvailableSheets.length) {
            container.innerHTML = '<small class="text-muted">Aucun Sheet configuré</small>';
            return;
        }
        container.innerHTML = autoCheckAvailableSheets.map(s => {
            const checked = selected.includes(s.alias) ? 'checked' : '';
            return '<div class="form-check form-check-inline">'
                + '<input class="form-check-input auto-check-sheet-cb" type="checkbox" '
                + 'id="auto_check_sheet_' + s.alias + '" value="' + s.alias + '" ' + checked + '>'
                + '<label class="form-check-label small" for="auto_check_sheet_' + s.alias + '">'
                + escapeHtml(s.alias) + '</label></div>';
        }).join('');
    }

    function updateFrequenceDisplay(freq) {
        const h3wrap = $('auto_check_horaire_3_wrap');
        if (freq === '3x_jour' || freq === 'personnalise') {
            h3wrap.style.display = 'flex';
        } else {
            h3wrap.style.display = 'none';
        }
    }

    function updateHoraireFields(freq, horaires) {
        $('auto_check_horaire_1').value = horaires[0] || '03:00';
        $('auto_check_horaire_2').value = horaires[1] || '15:00';
        $('auto_check_horaire_3').value = horaires[2] || '21:00';
        updateFrequenceDisplay(freq);
    }

    function getHorairesFromForm() {
        const freq = $('auto_check_frequence').value;
        const horaires = [$('auto_check_horaire_1').value];
        if (freq === '2x_jour' || freq === '3x_jour' || freq === 'personnalise') {
            horaires.push($('auto_check_horaire_2').value);
        }
        if (freq === '3x_jour' || freq === 'personnalise') {
            horaires.push($('auto_check_horaire_3').value);
        }
        return horaires.filter(h => h);
    }

    function getSelectedSheets() {
        const cbs = document.querySelectorAll('.auto-check-sheet-cb:checked');
        return Array.from(cbs).map(cb => cb.value);
    }

    function initAutoCheckForm() {
        // Fréquence change
        $('auto_check_frequence').addEventListener('change', function() {
            updateFrequenceDisplay(this.value);
        });

        // Save config
        $('auto_check_form').addEventListener('submit', function(e) {
            e.preventDefault();
            const body = {
                actif: $('auto_check_actif').checked,
                frequence: $('auto_check_frequence').value,
                horaires: getHorairesFromForm(),
                sheets_surveilles: getSelectedSheets()
            };
            fetch('/api/auto_check/config', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify(body)
            })
            .then(r => r.json())
            .then(d => {
                if (d.status === 'ok') {
                    loadAutoCheckConfig();
                    const toast = document.createElement('div');
                    toast.className = 'alert alert-success alert-dismissible fade show position-fixed';
                    toast.style.cssText = 'top:20px;right:20px;z-index:9999;max-width:350px;';
                    toast.innerHTML = '✅ Configuration auto-check sauvegardée'
                        + '<button type="button" class="btn-close" data-bs-dismiss="alert"></button>';
                    document.body.appendChild(toast);
                    setTimeout(() => toast.remove(), 3000);
                } else {
                    alert('Erreur : ' + (d.message || 'inconnue'));
                }
            })
            .catch(err => alert('Erreur réseau : ' + err));
        });

        // Trigger manuel
        $('auto_check_btn_trigger').addEventListener('click', function() {
            const btn = this;
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Lancement…';
            fetch('/api/auto_check/trigger', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({})
            })
            .then(r => r.json())
            .then(d => {
                btn.disabled = false;
                btn.innerHTML = '▶ Lancer maintenant';
                if (d.status === 'ok') {
                    $('auto_check_log_card').style.display = 'block';
                    $('auto_check_terminal').textContent = '⏳ Vérification en cours… Tâche #' + d.task_id;
                    surveillerAutoCheckTask(d.task_id);
                } else {
                    alert('Erreur : ' + (d.message || 'inconnue'));
                }
            })
            .catch(err => {
                btn.disabled = false;
                btn.innerHTML = '▶ Lancer maintenant';
                alert('Erreur réseau : ' + err);
            });
        });

        // Close terminal
        $('auto_check_log_close').addEventListener('click', function() {
            $('auto_check_log_card').style.display = 'none';
        });

        // Mark all read
        $('auto_check_btn_mark_read').addEventListener('click', function() {
            fetch('/api/auto_check/notifications/mark_read', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({all: true})
            })
            .then(r => r.json())
            .then(d => {
                if (d.status === 'ok') {
                    loadAutoCheckNotifications();
                    updateNotifBadge(0);
                }
            });
        });

        // Refresh
        $('pool_btn_refresh_autocheck').addEventListener('click', function() {
            loadAutoCheckConfig();
            loadAutoCheckNotifications();
        });
    }

    let autoCheckPollInterval = null;

    function surveillerAutoCheckTask(taskId) {
        if (autoCheckPollInterval) clearInterval(autoCheckPollInterval);
        autoCheckPollInterval = setInterval(function() {
            fetch('/lire_log_push?task_id=' + taskId)
            .then(r => r.json())
            .then(data => {
                const terminal = $('auto_check_terminal');
                if (data.log && data.log.trim()) {
                    terminal.textContent = data.log;
                }
                if (data.statut === 'termine') {
                    clearInterval(autoCheckPollInterval);
                    autoCheckPollInterval = null;
                    terminal.textContent += '\n\n✅ Vérification terminée.';
                    loadAutoCheckConfig();
                    loadAutoCheckNotifications();
                } else if (data.statut === 'erreur') {
                    clearInterval(autoCheckPollInterval);
                    autoCheckPollInterval = null;
                    terminal.textContent += '\n\n❌ Erreur lors de la vérification.';
                }
            })
            .catch(() => {});
        }, 1000);
    }

    function loadAutoCheckNotifications() {
        fetch('/api/auto_check/notifications?t=' + Date.now())
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'ok') return;
            const notifs = data.notifications || [];
            const tbody = $('auto_check_notif_tbody');
            const wrap = $('auto_check_notifications_wrap');

            updateNotifBadge(data.nb_non_lues || 0);

            if (notifs.length === 0) {
                wrap.style.display = 'none';
                return;
            }
            wrap.style.display = 'block';
            tbody.innerHTML = notifs.map(n =>
                '<tr class="' + (n.lu ? '' : 'table-warning') + '">'
                + '<td><small>' + fmtDate(n.date_creation) + '</small></td>'
                + '<td><code>' + escapeHtml(n.sheet_alias) + '</code></td>'
                + '<td><small>' + escapeHtml(n.message) + '</small></td>'
                + '<td>' + (n.lu ? '✓' : '🔴') + '</td>'
                + '</tr>'
            ).join('');
        })
        .catch(err => console.error('[auto_check notifs]', err));
    }

    function updateNotifBadge(count) {
        const badge = $('auto_check_notif_badge');
        if (count > 0) {
            badge.textContent = count;
            badge.style.display = 'inline';
        } else {
            badge.style.display = 'none';
        }
    }

    // ── Init au changement d'onglet ──
    const poolTab = document.getElementById('nav-pool-tab');
    if (poolTab) {
        poolTab.addEventListener('shown.bs.tab', function() {
            loadStats();
            loadDerniersPools();
            loadPoolsDropdown();
            loadAutoValidations();
            loadAutoCheckSheets();
            populateSheetAliasSelects(autoCheckAvailableSheets);
            loadAutoCheckNotifications();
            if ($('pool_resultats_tbody').children.length === 1
                && $('pool_resultats_tbody').children[0].children.length === 1) {
                // tableau encore vierge → on charge la 1ère page
                loadResultats();
            }
        });
    }

    // Init permanent
    initFormValider();
    initFiltresResultats();
    initFormAuto();
    initAutoCheckForm();
    loadAutoCheckSheets();
    $('pool_btn_refresh_stats').addEventListener('click', () => { loadStats(); loadDerniersPools(); });
    $('pool_btn_refresh_auto').addEventListener('click', loadAutoValidations);

    // ── Maintenance schéma SQL ──
    $('pool_btn_migrer').addEventListener('click', async function() {
        const btn = $('pool_btn_migrer');
        const msgBox = $('pool_migrer_msg');
        if (!confirm("Lancer la migration / réparation du schéma Pool ?\n"
                   + "C'est idempotent — sans danger si déjà à jour.")) return;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Migration...';
        msgBox.style.display = 'block';
        msgBox.className = 'mt-2 alert alert-info py-2';
        msgBox.innerHTML = '⏳ Migration en cours...';
        try {
            const resp = await fetch('/api/pool/migrer', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: '{}'
            });
            const data = await resp.json();
            if (resp.ok && data.status !== 'error') {
                const actionsHtml = (data.actions || [])
                    .map(a => `<div class="small">• ${a}</div>`).join('');
                const errsHtml = (data.erreurs || [])
                    .map(e => `<div class="small text-danger">⚠ ${e}</div>`).join('');
                msgBox.className = 'mt-2 alert py-2 ' +
                    (data.status === 'ok' ? 'alert-success' :
                     data.status === 'partiel' ? 'alert-warning' : 'alert-danger');
                msgBox.innerHTML =
                    `<div class="fw-bold">✅ Migration terminée (statut: ${data.status})</div>` +
                    actionsHtml + errsHtml;
                // Recharger les stats si on est sur l'onglet Pool
                loadStats();
                loadPoolsDropdown();
            } else {
                msgBox.className = 'mt-2 alert alert-danger py-2';
                msgBox.innerHTML = `<div class="fw-bold">❌ Échec</div>` +
                    `<div class="small">${(data.erreurs || ['Erreur inconnue']).join('<br>')}</div>`;
            }
        } catch (e) {
            msgBox.className = 'mt-2 alert alert-danger py-2';
            msgBox.innerHTML = `<div class="fw-bold">❌ Erreur réseau</div><div class="small">${e}</div>`;
        } finally {
            btn.disabled = false;
            btn.innerHTML = '🔧 Réparer / migrer';
        }
    });
})();
