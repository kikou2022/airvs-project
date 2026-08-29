// [P3-S1] Phase 3 Étape 1-2 — Extraction modules JS initiaux (utils, grille-editoriale, navigation)
/**
 * AIRVS Dashboard — grille-editoriale
 * Extrait de index.html (Phase 3, étape 1)
 */

(function() {
    'use strict';

    // Alias vers utilitaires globaux (utils.js)
    var apiFetch = window.apiFetch;
    var showToast = window.showToast;

    // ── Constantes ──
    const JOURS_MAP = {1:'lundi',2:'mardi',3:'mercredi',4:'jeudi',5:'vendredi',6:'samedi',7:'dimanche'};
    const JOURS_LABELS = {1:'Lundi',2:'Mardi',3:'Mercredi',4:'Jeudi',5:'Vendredi',6:'Samedi',7:'Dimanche'};
    const JOURS_COURTS = {1:'Lun',2:'Mar',3:'Mer',4:'Jeu',5:'Ven',6:'Sam',7:'Dim'};
    const GRID_START = 6, TOTAL_HOURS = 19; // 06:00 → 01:00 (19h)
    const DISPLAY_HOURS = [6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,0];
    const H = 50; // hauteur par heure en px

    // Palette de couleurs (sera écrasée par l'API si dispo)
    let PALETTE = [
        {name:"Vert AIRVS",hex:"#1DB954"},{name:"Rouge Outre-Manche",hex:"#E8405E"},
        {name:"Violet Mary",hex:"#A855F7"},{name:"Bleu Baleine",hex:"#3B82F6"},
        {name:"Bleu Atlantique",hex:"#0EA5E9"},{name:"Orange Europe",hex:"#F59E0B"},
        {name:"Rouge Metal",hex:"#EF4444"},{name:"Rose Funk",hex:"#EC4899"},
        {name:"Cyan Jazz",hex:"#06B6D4"},{name:"Violet Cinéma",hex:"#8B5CF6"}
    ];

    let grilleData = null;

    // ── Utilitaires locaux ──
    const $ = id => document.getElementById(id);

    function timeToPercent(t) {
        const [h, m] = t.split(':').map(Number);
        let mins = h * 60 + m;
        if (mins < GRID_START * 60) mins += 24 * 60;
        return ((mins - GRID_START * 60) / (TOTAL_HOURS * 60)) * 100;
    }

    function todayJour() {
        const jsDay = new Date().getDay();
        return [6, 0, 1, 2, 3, 4, 5][jsDay] + 1;
    }

    function dbIdFromId(id) {
        return id ? parseInt(id.replace('bloc-', '').replace('sub-', '')) : null;
    }

    // ── Charger la grille ──
    async function chargerGrille() {
        try {
            const json = await apiFetch('/api/grille_editoriale/data?t=' + Date.now());
            if (json.status !== 'ok') throw new Error(json.message);
            grilleData = json.data;
            if (grilleData.meta && grilleData.meta.palette) {
                PALETTE = grilleData.meta.palette;
            }
            $('versionLabel').textContent = grilleData.meta.label;
            renderGrille();
            renderPalette();
        } catch (e) {
            console.error('Erreur chargement grille:', e);
            showToast('Erreur chargement: ' + e.message, 'error');
        }
    }
    window.chargerGrille = chargerGrille;

    // ── Rendu de la grille ──
    function renderGrille() {
        const grid = $('grilleGrid');
        if (!grilleData) return;
        const schedule = grilleData.schedule;
        const today = todayJour();
        let html = '';

        html += '<div class="ge-corner">HEURE</div>';

        for (let j = 1; j <= 7; j++) {
            const isToday = j === today;
            html += `<div class="ge-day-header ${isToday ? 'ge-today-header' : ''}" 
                          onclick="ouvrirModalBloc(null, ${j})" title="Cliquer pour ajouter un bloc">
                          ${JOURS_LABELS[j]}</div>`;
        }

        html += '<div>';
        DISPLAY_HOURS.forEach(h => {
            const label = h < 10 ? '0' + h + ':00' : h + ':00';
            html += `<div class="ge-time-label">${label}</div>`;
        });
        html += '</div>';

        for (let j = 1; j <= 7; j++) {
            const cle = JOURS_MAP[j];
            const dayData = schedule[cle];
            const blocks = dayData ? dayData.blocks : [];

            html += `<div class="ge-day-col">`;
            html += `<div style="position:relative; height:${TOTAL_HOURS * H}px;">`;

            for (let h = 0; h <= TOTAL_HOURS; h++) {
                html += `<div class="ge-hour-line" style="top:${(h / TOTAL_HOURS) * 100}%;${h >= 17 ? ' border-top:1px dashed rgba(0,0,0,0.15);' : ''}"></div>`;
            }

            blocks.forEach(block => {
                const top = timeToPercent(block.start);
                let height = timeToPercent(block.end) - top;
                if (height <= 0) height = (timeToPercent('01:00') - top);
                const bg = block.color + '18';
                const typeColors = {
                    focus: { bg: '#e3f2fd', color: '#1565c0' },
                    emission: { bg: '#f3e5f5', color: '#7b1fa2' },
                    chronique: { bg: '#e8f5e9', color: '#2e7d32' },
                    interview: { bg: '#fce4ec', color: '#c62828' },
                    meteo: { bg: '#e0f7fa', color: '#00838f' },
                    infos: { bg: '#fff8e1', color: '#f57f17' },
                    special: { bg: '#fff3e0', color: '#e65100' }
                };
                const tc = typeColors[block.type] || typeColors.focus;

                html += `<div class="ge-block" 
                    style="top:${top}%; height:${height}%; background:${bg}; border-left-color:${block.color};"
                    onclick="ouvrirModalBloc('${block.id}')" title="${block.title}\n${block.start}-${block.end}">
                    <div class="ge-gb-title">${block.title}</div>
                    <span class="gb-type" style="background:${tc.bg}; color:${tc.color};">${block.type}</span>
                    ${block.host ? '<div class="ge-gb-host"><i class="bi bi-person"></i> ' + block.host + '</div>' : ''}
                </div>`;
            });

            html += '</div></div>';
        }

        html += '<div class="ge-corner" style="background:#1a1a2e; font-size:0.7rem; padding:0.3rem;">🌙 Nuit</div>';
        for (let j = 1; j <= 7; j++) {
            html += `<div style="background:linear-gradient(180deg,#1a1a2e,#0d0d1a); color:#6b7280; text-align:center; padding:0.5rem 0.2rem; font-size:0.75rem; border-left:1px solid rgba(255,255,255,0.05);">
                01:00 – 06:00<br><span style="font-size:0.65rem; opacity:0.6;">Programme nuit</span>
            </div>`;
        }

        grid.innerHTML = html;
    }

    // ── Palette de couleurs ──
    function renderPalette() {
        const container = $('colorPalette');
        container.innerHTML = PALETTE.map((c, i) =>
            `<div class="color-swatch" style="background:${c.hex};" 
                  title="${c.name}" data-color="${c.hex}" 
                  onclick="selectColor(this, '${c.hex}')"></div>`
        ).join('');
    }

    let selectedColor = '#1DB954';
    function selectColor(el, hex) {
        document.querySelectorAll('.color-swatch').forEach(s => s.classList.remove('selected'));
        if (el) el.classList.add('selected');
        selectedColor = hex;
        $('blocCouleurCustom').value = hex;
    }
    window.selectColor = selectColor;

    $('blocCouleurCustom').addEventListener('input', function() {
        selectedColor = this.value;
        document.querySelectorAll('.color-swatch').forEach(s => s.classList.remove('selected'));
    });

    // ── Gestion type libre ──
    window.onTypeChange = function() {
        $('blocTypeLibre').style.display = $('blocType').value === 'libre' ? 'block' : 'none';
    };

    // ── Gestion presets jours ──
    window.appliquerPresetJour = function(preset) {
        const map = { tous: [1,2,3,4,5,6,7], semaine: [1,2,3,4,5], saufven: [1,2,3,4,6,7], weekend: [6,7] };
        for (let j = 1; j <= 7; j++) {
            document.getElementById('jc' + j).checked = (map[preset] || []).includes(j);
        }
    };

    function getJoursSelectionnes() {
        const jours = [];
        for (let j = 1; j <= 7; j++) {
            if (document.getElementById('jc' + j).checked) jours.push(j);
        }
        return jours;
    }

    function setJoursSelectionnes(liste) {
        for (let j = 1; j <= 7; j++) {
            document.getElementById('jc' + j).checked = liste.includes(j);
        }
    }

    // ── Modal Bloc (Création / Édition) ──
    window.ouvrirModalBloc = function(blocId, prefillJour) {
        const modal = bootstrap.Modal.getOrCreateInstance($('modalBloc'));
        const isEdit = blocId !== null && blocId !== undefined;

        $('modalBlocTitle').textContent = isEdit ? 'Modifier le bloc' : 'Nouveau bloc';
        $('btnSupprimerBloc').style.display = isEdit ? 'inline-block' : 'none';

        const jourBoxes = document.querySelectorAll('#jourCheckboxes input');
        const presetsBtns = document.querySelectorAll('#jourPresets button');

        if (isEdit && grilleData) {
            let bloc = null;
            let blocJourNum = null;
            for (const [num, cle] of Object.entries(JOURS_MAP)) {
                const dayBlocks = grilleData.schedule[cle]?.blocks || [];
                bloc = dayBlocks.find(b => b.id === blocId);
                if (bloc) { blocJourNum = parseInt(num); break; }
            }
            if (!bloc) { showToast('Bloc introuvable', 'error'); return; }

            $('blocDbId').value = bloc.db_id || dbIdFromId(blocId);
            $('blocTitre').value = bloc.title;
            $('blocDebut').value = bloc.start;
            $('blocFin').value = bloc.end;
            $('blocAnimateur').value = bloc.host || '';
            $('blocDescription').value = bloc.description || '';
            selectColor(null, bloc.color);

            const typeVal = bloc.type;
            const select = $('blocType');
            if ([...select.options].some(o => o.value === typeVal)) {
                select.value = typeVal;
                $('blocTypeLibre').style.display = 'none';
            } else if (typeVal) {
                select.value = 'libre';
                $('blocTypeLibre').value = typeVal;
                $('blocTypeLibre').style.display = 'block';
            }

            setJoursSelectionnes([blocJourNum]);
            jourBoxes.forEach(cb => cb.disabled = true);
            presetsBtns.forEach(b => b.disabled = true);

        } else {
            $('formBloc').reset();
            $('blocDbId').value = '';
            $('blocDebut').value = '06:00';
            $('blocFin').value = '23:00';
            $('blocTypeLibre').style.display = 'none';
            selectColor(null, '#1DB954');

            if (prefillJour) {
                setJoursSelectionnes([prefillJour]);
            } else {
                setJoursSelectionnes([1]);
            }
            jourBoxes.forEach(cb => cb.disabled = false);
            presetsBtns.forEach(b => b.disabled = false);
        }

        modal.show();
    };

    // ── Sauvegarder un bloc ──
    window.sauvegarderBloc = async function() {
        const dbId = $('blocDbId').value;
        const isEdit = !!dbId;

        let typeValeur = $('blocType').value;
        if (typeValeur === 'libre') {
            typeValeur = $('blocTypeLibre').value.trim();
            if (!typeValeur) { showToast('Précisez le nom du type personnalisé', 'warning'); return; }
        }

        const payload = {
            heure_debut: $('blocDebut').value,
            heure_fin: $('blocFin').value,
            titre: $('blocTitre').value.trim(),
            type: typeValeur,
            animateur: $('blocAnimateur').value.trim(),
            description: $('blocDescription').value.trim(),
            couleur: selectedColor
        };

        if (!payload.titre) { showToast('Le titre est requis', 'warning'); return; }
        if (payload.heure_debut === payload.heure_fin) { showToast('Les heures de début et fin ne peuvent pas être identiques', 'warning'); return; }

        try {
            if (isEdit) {
                const jours = getJoursSelectionnes();
                payload.jour_semaine = jours[0] || 1;

                const json = await apiFetch(`/api/grille_editoriale/bloc/${dbId}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                if (json.status === 'ok') {
                    showToast(json.message, 'success');
                    bootstrap.Modal.getInstance($('modalBloc')).hide();
                    chargerGrille();
                } else {
                    showToast(json.message, 'error');
                }
            } else {
                const jours = getJoursSelectionnes();
                if (!jours.length) { showToast('Sélectionnez au moins un jour', 'warning'); return; }

                let ok = 0, erreurs = [];
                for (const j of jours) {
                    const p = { ...payload, jour_semaine: j };
                    try {
                        const json = await apiFetch('/api/grille_editoriale/bloc', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(p)
                        });
                        if (json.status === 'ok') ok++;
                        else erreurs.push(JOURS_LABELS[j] + ' : ' + json.message);
                    } catch (e) {
                        erreurs.push(JOURS_LABELS[j] + ' : ' + e.message);
                    }
                }
                if (ok > 0) {
                    bootstrap.Modal.getInstance($('modalBloc')).hide();
                    chargerGrille();
                    let msg = `${ok} bloc(s) créé(s)`;
                    if (erreurs.length) msg += ` — ${erreurs.length} erreur(s)`;
                    showToast(msg, erreurs.length ? 'warning' : 'success');
                } else {
                    showToast(erreurs.join(' | '), 'error');
                }
            }
        } catch (e) {
            showToast('Erreur: ' + e.message, 'error');
        }
    };

    // ── Supprimer un bloc ──
    window.supprimerBloc = async function() {
        const dbId = $('blocDbId').value;
        if (!dbId) return;
        if (!confirm('Supprimer ce bloc et ses sous-blocs ?')) return;

        try {
            const json = await apiFetch(`/api/grille_editoriale/bloc/${dbId}`, { method: 'DELETE' });
            if (json.status === 'ok') {
                showToast(json.message, 'success');
                bootstrap.Modal.getInstance($('modalBloc')).hide();
                chargerGrille();
            } else {
                showToast(json.message, 'error');
            }
        } catch (e) {
            showToast('Erreur: ' + e.message, 'error');
        }
    };

    // ── Export ──
    window.exporterGrille = async function() {
        try {
            const json = await apiFetch('/api/grille_editoriale/export?t=' + Date.now());
            $('exportJsonBox').textContent = JSON.stringify(json, null, 2);
            bootstrap.Modal.getOrCreateInstance($('modalExport')).show();
        } catch (e) {
            showToast('Erreur export: ' + e.message, 'error');
        }
    };

    window.telechargerExport = function() {
        const text = $('exportJsonBox').textContent;
        const blob = new Blob([text], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'grille.json';
        a.click();
        URL.revokeObjectURL(url);
    };

    // ── Validation ──
    window.validerGrille = async function() {
        try {
            const json = await apiFetch('/api/grille_editoriale/valider?t=' + Date.now());
            const container = $('validationResult');
            if (json.valide) {
                container.innerHTML = `
                    <div class="alert alert-success mb-3">
                        <h5 class="alert-heading"><i class="bi bi-check-circle-fill"></i> Grille valide</h5>
                        <p class="mb-0">${json.nb_blocs} bloc(s) configuré(s). Aucun chevauchement détecté.</p>
                    </div>`;
            } else {
                let html = `<div class="alert alert-danger mb-3"><h5 class="alert-heading">Erreurs</h5><ul class="mb-0">`;
                json.erreurs.forEach(e => html += `<li>${e}</li>`);
                html += '</ul></div>';
                if (json.avertissements.length) {
                    html += '<div class="alert alert-warning"><h6>Avertissements</h6><ul>';
                    json.avertissements.forEach(a => html += `<li>${a}</li>`);
                    html += '</ul></div>';
                }
                container.innerHTML = html;
            }
            bootstrap.Modal.getOrCreateInstance($('modalValidation')).show();
        } catch (e) {
            showToast('Erreur validation: ' + e.message, 'error');
        }
    };

    // ── Copier un jour ──
    window.ouvrirModalCopierJour = function() {
        bootstrap.Modal.getOrCreateInstance($('modalCopierJour')).show();
    };

    window.copierJour = async function() {
        const src = parseInt($('copierSrc').value);
        const dst = parseInt($('copierDst').value);
        if (src === dst) { showToast('Les jours doivent être différents', 'warning'); return; }

        try {
            const json = await apiFetch('/api/grille_editoriale/copier_jour', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ jour_source: src, jour_destination: dst })
            });
            if (json.status === 'ok') {
                showToast(json.message, 'success');
                bootstrap.Modal.getInstance($('modalCopierJour')).hide();
                chargerGrille();
            } else {
                showToast(json.message, 'error');
            }
        } catch (e) {
            showToast('Erreur: ' + e.message, 'error');
        }
    };

    // ── Versions ──
    async function ouvrirModalVersions() {
        const modal = bootstrap.Modal.getOrCreateInstance($('modalVersions'));
        try {
            const json = await apiFetch('/api/grille_editoriale/versions?t=' + Date.now());
            if (json.status !== 'ok') throw new Error(json.message);
            let html = '<div class="list-group">';
            json.versions.forEach(v => {
                const activeBadge = v.actif ? '<span class="badge bg-success">Active</span>' : '';
                html += `<div class="list-group-item d-flex justify-content-between align-items-center">
                    <div>
                        <strong>${v.label}</strong>
                        <small class="text-muted ms-2">${v.date_creation || ''}</small>
                    
                        ${activeBadge}
                    </div>
                    <div>
                        ${!v.actif ? `<button class="btn btn-sm btn-outline-success me-1" onclick="activerVersion(${v.id})"><i class="bi bi-check-circle"></i> Activer</button>` : ''}
                    </div>
                </div>`;
            });
            html += '</div>';
            $('versionsList').innerHTML = html;
        } catch (e) {
            $('versionsList').innerHTML = `<div class="alert alert-danger">${e.message}</div>`;
        }
        modal.show();
    }
    window.ouvrirModalVersions = ouvrirModalVersions;

    window.creerVersion = async function() {
        const label = $('nouvelleVersion').value.trim();
        if (!label) { showToast('Nom requis', 'warning'); return; }
        try {
            const json = await apiFetch('/api/grille_editoriale/version', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ label })
            });
            if (json.status === 'ok') {
                showToast(json.message, 'success');
                $('nouvelleVersion').value = '';
                ouvrirModalVersions();
            } else {
                showToast(json.message, 'error');
            }
        } catch (e) {
            showToast('Erreur: ' + e.message, 'error');
        }
    };

    window.activerVersion = async function(versionId) {
        try {
            const json = await apiFetch(`/api/grille_editoriale/version/activer/${versionId}`, { method: 'PUT' });
            if (json.status === 'ok') {
                showToast(json.message, 'success');
                const modalEl = $('modalVersions');
                const modalInstance = bootstrap.Modal.getInstance(modalEl);
                if (modalInstance) modalInstance.hide();
                chargerGrille();
            } else {
                showToast(json.message, 'error');
            }
        } catch (e) {
            showToast('Erreur: ' + e.message, 'error');
        }
    };

    // ── Import ──
    window.ouvrirModalImport = function() {
        $('importJsonInput').value = '';
        bootstrap.Modal.getOrCreateInstance($('modalImport')).show();
    };

    window.importerGrille = async function() {
        const text = $('importJsonInput').value.trim();
        if (!text) { showToast('Collez un JSON valide', 'warning'); return; }
        try {
            const data = JSON.parse(text);
            if (!data.schedule) throw new Error('Clé "schedule" manquante');
        } catch (e) {
            showToast('JSON invalide: ' + e.message, 'error');
            return;
        }
        if (!confirm('Cela remplacera intégralement la grille active. Continuer ?')) return;

        try {
            const json = await apiFetch('/api/grille_editoriale/import', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: text
            });
            if (json.status === 'ok') {
                showToast(json.message, 'success');
                bootstrap.Modal.getInstance($('modalImport')).hide();
                chargerGrille();
            } else {
                showToast(json.message, 'error');
            }
        } catch (e) {
            showToast('Erreur: ' + e.message, 'error');
        }
    };

    // ── Publier ──
    window.publierGrille = async function() {
        try {
            const json = await apiFetch('/api/grille_editoriale/publier', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'}
            });
            if (json.status === 'ok') {
                showToast(json.message, 'success');
            } else {
                showToast('Erreur publication: ' + json.message, 'error');
            }
        } catch (e) {
            showToast('Erreur publication: ' + e.message, 'error');
        }
    };

    // ── Init ──
    chargerGrille();

})();