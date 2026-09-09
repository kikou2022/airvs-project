// [P3-S3] Phase 3 Étape 3 — Extraction JS shazam-animateurs + pont-player
/**
 * AIRVS Dashboard — shazam-animateurs
 * Extrait de index.html (Phase 3, étape 2)
 * Gestion Shazam, Animateurs, filtres, badges, prefill, pipeline.
 */

(function() {
    'use strict';

    // Alias vers utilitaires globaux (utils.js)
    var escapeHtml = window.escapeHtml;
    var escapeAttr = window.escapeAttr;
    var formatDuration = window.formatDuration;
    var copierTexte = window.copierTexteSansClipboardAPI || null;

    // ── Push preview : déléguer au monolithe ──
    // La fonction populatePushPreviewFromShazam du monolithe est exposée
    // via window._onShazamMatchResult / window._onAnimateursMatchResult
    // On l'appelle directement — pas de wrapper intermédiaire.



// ── Helper Genre badge ──
function getGenreBadge(genre) {
    var g = (genre || '').trim();
    if (!g) return '<span class="text-muted" style="font-size:0.7rem">—</span>';
    // Couleurs par genre
    var colors = {
        'chanson': '#3b82f6', 'pop': '#ec4899', 'rock': '#f59e0b',
        'jazz': '#8b5cf6', 'classique': '#6366f1', 'electro': '#10b981',
        'rap': '#ef4444', 'r&b': '#f97316', 'reggae': '#14b8a6',
        'soul': '#a855f7', 'funk': '#eab308', 'country': '#84cc16',
        'blues': '#06b6d4', 'metal': '#dc2626', 'world': '#0d9488'
    };
    var gl = g.toLowerCase();
    var c = colors[gl] || '#6b7280';
    return '<span class="badge" style="background:' + c + ';color:#fff;font-size:0.65rem;padding:1px 5px;border-radius:3px;">' + escapeHtml(g) + '</span>';
}

// ── Helper Commentaire cell (sélectionnable, copiable, multiligne) ──
// Stocke les commentaires dans un map global pour le copier-coller
window._commentaires = window._commentaires || {};
var _commIdx = 0;
function getCommentaireCell(commentaire) {
    var c = (commentaire || '').trim();
    if (!c) return '<td class="text-center text-muted" style="font-size:0.7rem;">—</td>';
    var idx = '_comm_' + (_commIdx++);
    window._commentaires[idx] = c;
    // Preview : première ligne ou 60 premiers chars
    var lines = c.split('\n');
    var preview = lines[0].length > 60 ? lines[0].substring(0, 60) + '…' : lines[0];
    var multiLine = lines.length > 1 || c.length > 60;
    var moreLabel = multiLine ? ' <span style="color:#0ea5e9;cursor:pointer;font-weight:600;" onclick="window._toggleComment(\'' + idx + '\')" title="Voir tout le commentaire">▸</span>' : '';
    var copyBtn = '<span style="color:#94a3b8;cursor:pointer;margin-left:4px;" onclick="window._copyComment(\'' + idx + '\')" title="Copier le commentaire">⧉</span>';
    // Cellule avec texte sélectionnable
    return '<td style="font-size:0.78rem;max-width:260px;vertical-align:top;padding:4px 6px;">' +
        '<div id="comm_preview_' + idx + '" style="white-space:pre-wrap;word-break:break-word;">' + escapeHtml(preview) + moreLabel + copyBtn + '</div>' +
        '<div id="comm_full_' + idx + '" style="display:none;white-space:pre-wrap;word-break:break-word;background:#1e293b;border:1px solid #334155;border-radius:4px;padding:6px;margin-top:4px;max-height:200px;overflow-y:auto;">' + escapeHtml(c) + copyBtn + '</div>' +
        '</td>';
}
// Toggle preview ↔ full
window._toggleComment = function(idx) {
    var preview = document.getElementById('comm_preview_' + idx);
    var full = document.getElementById('comm_full_' + idx);
    if (!preview || !full) return;
    if (full.style.display === 'none') {
        full.style.display = 'block';
        preview.style.display = 'none';
    } else {
        full.style.display = 'none';
        preview.style.display = 'block';
    }
};
// Copier le commentaire dans le presse-papier
window._copyComment = function(idx) {
    var c = window._commentaires[idx];
    if (!c) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(c).then(function() {
            window._toastComment && clearTimeout(window._toastComment);
            var t = document.getElementById('comm_toast');
            if (!t) { t = document.createElement('div'); t.id = 'comm_toast'; t.style.cssText = 'position:fixed;bottom:1.5rem;right:1.5rem;background:#064e3b;color:#6ee7b7;border:1px solid #10b981;padding:6px 14px;border-radius:6px;font-size:0.82rem;z-index:9999;'; document.body.appendChild(t); }
            t.textContent = 'Commentaire copié !';
            t.style.opacity = '1';
            window._toastComment = setTimeout(function() { t.style.opacity = '0'; }, 1800);
        });
    } else {
        // Fallback : sélectionner le texte dans le bloc full
        var full = document.getElementById('comm_full_' + idx);
        if (full) { window._toggleComment(idx); var range = document.createRange(); range.selectNodeContents(full); var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range); }
    }
};
// ════════════════════════════════════
// SHAZAM : sous-onglet Pré-remplissage
// ════════════════════════════════════

// ── Helper Badge de Source Shazam / Web ──
function getShazamSourceBadge(source) {
    let src = (source || '').toLowerCase();
    let webVals = ['shazam', 'recommandation', 'saisie manuelle', 'autre', 'formulaire_programmeur'];
    if (webVals.indexOf(src) !== -1) {
        return '<span class="badge" style="background:#0ea5e9;color:#fff;font-size:0.7rem;padding:2px 6px;border-radius:4px;" title="Soumission Animateur (VPS Web)">📝 Web</span>';
    } else if (src.includes('externe')) {
        return '<span class="badge" style="background:#f59e0b;color:#000;font-size:0.7rem;padding:2px 6px;border-radius:4px;" title="Shazam Externe via MacroDroid (VPS)">🎵 Ext.</span>';
    }
    return '<span class="badge" style="background:#10b981;color:#fff;font-size:0.7rem;padding:2px 6px;border-radius:4px;" title="Reconnaissance Shazam Studio LAN">🎵 Studio</span>';
}

function getAnimSourceBadge(source) {
    let src = (source || '').toLowerCase();
    if (!src) return '<span class="text-muted" style="font-size:0.75rem">—</span>';
    // Sources VPS formulaire (table airvs_animateurs)
    if (src.includes('emission'))
        return '<span class="badge" style="background:#8b5cf6;color:#fff;font-size:0.7rem;padding:2px 6px;border-radius:4px;" title="Titre d\'émission">🎙 Emission</span>';
    if (src.includes('chronique'))
        return '<span class="badge" style="background:#ec4899;color:#fff;font-size:0.7rem;padding:2px 6px;border-radius:4px;" title="Chronique">📰 Chronique</span>';
    if (src.includes('discothèque') || src.includes('discotheque'))
        return '<span class="badge" style="background:#f97316;color:#fff;font-size:0.7rem;padding:2px 6px;border-radius:4px;" title="Titre Discothèque">💿 Disco.</span>';
    if (src.includes('shazam'))
        return '<span class="badge" style="background:#6366f1;color:#fff;font-size:0.7rem;padding:2px 6px;border-radius:4px;" title="Reconnaissance Shazam">♫ Shazam</span>';
    if (src.includes('recommandation'))
        return '<span class="badge" style="background:#10b981;color:#fff;font-size:0.7rem;padding:2px 6px;border-radius:4px;" title="Recommandation">★ Reco</span>';
    if (src.includes('saisie') || src.includes('manuelle'))
        return '<span class="badge" style="background:#f59e0b;color:#000;font-size:0.7rem;padding:2px 6px;border-radius:4px;" title="Saisie manuelle">✏ Saisie</span>';
    // Anciennes valeurs (avant Phase 2)
    if (src.includes('formulaire') || src.includes('programmeur'))
        return '<span class="badge" style="background:#0ea5e9;color:#fff;font-size:0.7rem;padding:2px 6px;border-radius:4px;" title="Soumission via formulaire VPS">📝 Formulaire</span>';
    if (src.includes('autre'))
        return '<span class="badge" style="background:#6b7280;color:#fff;font-size:0.7rem;padding:2px 6px;border-radius:4px;" title="Autre">Autre</span>';
    // Valeur inconnue
    return '<span class="badge" style="background:#6b7280;color:#fff;font-size:0.7rem;padding:2px 6px;border-radius:4px;" title="' + escapeHtml(source) + '">' + escapeHtml(source) + '</span>';
}

// ── Helper Badge Statut (R6+R15) ──
function getStatutBadge(statut, rowId, prefix, statutVps) {
    // statut = colonne matching (matche/rejete/nouveau/pousse_azura) — si elle existe
    // statutVps = colonne statut_vps (pending/synced) — fallback
    var s = (statut || '').toLowerCase();
    // Si pas de statut matching, déduire depuis match_song_id ou statut_vps
    if (!s && statutVps) {
        s = statutVps.toLowerCase();  // 'pending' ou 'synced'
    }
    if (!s) s = 'nouveau';
    var tbl = (prefix === 'animateurs') ? 'animateurs' : 'shazam';
    if (s === 'matche' || s === 'matché')
        return '<span class="badge" style="background:#10b981;color:#fff;font-size:0.65rem;padding:1px 5px;border-radius:3px;" title="Matché dans RadioDJ">✓ Match</span>';
    if (s === 'pousse_azura' || s === 'poussé_azura' || s === 'pousse' || s === 'poussé')
        return '<span class="badge" style="background:#6366f1;color:#fff;font-size:0.65rem;padding:1px 5px;border-radius:3px;" title="Poussé dans Azuracast">☁ Poussé</span>';
    if (s === 'rejete' || s === 'rejeté' || s === 'rejete_corrigé' || s === 'rejete_corrige')
        return '<span class="badge" style="background:#ef4444;color:#fff;font-size:0.65rem;padding:1px 5px;border-radius:3px;" title="Rejeté" data-row-id="' + rowId + '" data-table="' + tbl + '">✗ Rejeté</span>';
    // nouveau ou vide
    return '<span class="badge" style="background:#6b7280;color:#fff;font-size:0.65rem;padding:1px 5px;border-radius:3px;" title="Nouveau — pas encore matché">● Nouveau</span>';
}

// ── Bouton Rejeter/Corriger pour les entrées non matchées (R15) ──
function getRejetActionsCell(statut, rowId, prefix, artiste, titre) {
    var s = (statut || 'nouveau').toLowerCase();
    var tbl = (prefix === 'animateurs') ? 'animateurs' : 'shazam';
    // Si déjà rejeté, on ne montre que "Corriger"
    if (s === 'rejete' || s === 'rejeté' || s === 'rejete_corrigé' || s === 'rejete_corrige') {
        return '<td class="text-center">' +
            '<button class="btn btn-sm btn-outline-warning" style="font-size:0.65rem;padding:1px 4px;" ' +
            'onclick="window._shazamClick.call(this,&#39;corriger&#39;,&#39;' + tbl + '&#39;,' + rowId + ')" ' +
            'title="Corriger cet entrée">✎ Corriger</button></td>';
    }
    // Si matché ou poussé, pas d'action de rejet
    if (s === 'matche' || s === 'matché' || s === 'pousse_azura' || s === 'poussé_azura' || s === 'pousse' || s === 'poussé') {
        return '<td class="text-center text-muted" style="font-size:0.65rem;">—</td>';
    }
    // Nouveau : on peut rejeter
    return '<td class="text-center">' +
        '<button class="btn btn-sm btn-outline-danger" style="font-size:0.65rem;padding:1px 4px;" ' +
        'onclick="window._shazamClick.call(this,&#39;rejeter&#39;,&#39;' + tbl + '&#39;,' + rowId + ')" ' +
        'title="Rejeter ce titre">🚫</button> ' +
        '<button class="btn btn-sm btn-outline-warning" style="font-size:0.65rem;padding:1px 4px;" ' +
        'onclick="window._shazamClick.call(this,&#39;corriger&#39;,&#39;' + tbl + '&#39;,' + rowId + ')" ' +
        'title="Corriger artiste/titre">✎</button></td>';
}


// ── Helper Badge Animateur (colore + cliquable) ──
var ANIMATEUR_COLORS = {
    'Vince': '#3b82f6', 'Mary': '#ec4899', 'Laurent': '#f59e0b', 'Paps': '#10b981'
};
var ANIMATEUR_LIST = ['Vince', 'Mary', 'Laurent', 'Paps'];

// ── Liste dynamique d'animateurs (mise à jour via API) ──
var _dynamicAnimateurList = null;  // null = pas encore chargé

function populateAnimateurDropdowns() {
    // Fusionner les animateurs des deux tables : shazam + animateurs
    var p1 = fetch('/api/shazam/animateurs')
        .then(function(r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) { return (data.animateurs || []); })
        .catch(function() { return []; });

    var p2 = fetch('/api/animateurs/animateurs')
        .then(function(r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) { return (data.animateurs || []); })
        .catch(function() { return []; });

    Promise.all([p1, p2]).then(function(results) {
        var merged = [];
        var seen = {};
        results.forEach(function(list) {
            list.forEach(function(name) {
                var key = name.toLowerCase();
                if (!seen[key]) {
                    seen[key] = true;
                    merged.push(name);
                }
            });
        });
        merged.sort(function(a, b) { return a.localeCompare(b); });

        if (merged.length > 0) {
            ANIMATEUR_LIST = merged;
            _dynamicAnimateurList = merged;
        }
        _fillAnimateurSelects();
    });
}

function _fillAnimateurSelects() {
    var selects = [
        document.getElementById('shazam_animateur_select'),
        document.getElementById('animateurs_animateur_select')
    ];
    selects.forEach(function(sel) {
        if (!sel) return;
        var current = sel.value;
        sel.innerHTML = '<option value="">Tous</option>';
        ANIMATEUR_LIST.forEach(function(name) {
            var opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            sel.appendChild(opt);
        });
        sel.value = current;  // Restaurer sélection si valide
    });
}

function getAnimateurColor(name) {
    return ANIMATEUR_COLORS[name] || '#6b7280';
}

function getAnimateurBadge(animateur, rowId, prefix) {
    var name = (animateur || '').trim();
    if (!name) return '<span class="text-muted small">—</span>';
    var color = getAnimateurColor(name);
    var tbl = (prefix === 'animateurs') ? 'animateurs' : 'shazam';
    return '<span class="anim-badge" style="background:' + color + ';"' +
        ' data-row-id="' + rowId + '"' +
        ' data-table="' + tbl + '"' +
        ' onclick="window._shazamClick.call(this,&#39;editAnim&#39;,&#39;' + tbl + '&#39;,' + rowId + ')"' +
        ' title="Cliquer pour changer l\'animateur">' + escapeHtml(name) + '</span>';
}

// ── Dropdown de selection animateur ──
var _activeAnimDD = null;

function showAnimateurDropdown(rowId, currentAnim, anchorEl, table) {
    hideAnimateurDropdown();
    var dd = document.createElement('div');
    dd.className = 'anim-dd';
    dd.id = '_anim_dd';
    var _table = table || 'shazam';
    ANIMATEUR_LIST.forEach(function(name) {
        var item = document.createElement('button');
        item.type = 'button';
        item.className = 'anim-dd-item';
        item.innerHTML = '<span class="anim-dd-dot" style="background:' + getAnimateurColor(name) + ';"></span>' +
            '<span>' + escapeHtml(name) + '</span>';
        item.onclick = function(e) {
            e.stopPropagation();
            saveAnimateur(rowId, name, _table);
        };
        dd.appendChild(item);
    });
    document.body.appendChild(dd);
    var rect = anchorEl.getBoundingClientRect();
    dd.style.top = (rect.bottom + 4) + 'px';
    dd.style.left = rect.left + 'px';
    _activeAnimDD = dd;
}

function hideAnimateurDropdown() {
    if (_activeAnimDD && _activeAnimDD.parentNode) {
        _activeAnimDD.parentNode.removeChild(_activeAnimDD);
    }
    _activeAnimDD = null;
}

document.addEventListener('click', function(e) {
    if (_activeAnimDD && !_activeAnimDD.contains(e.target)) {
        hideAnimateurDropdown();
    }
});

function saveAnimateur(rowId, animateur, table) {
    hideAnimateurDropdown();
    table = table || 'shazam';
    var prefix = (table === 'animateurs') ? 'animateurs' : 'shazam';
    var url = '/api/' + table + '/' + rowId + '/animateur?animateur=' + encodeURIComponent(animateur);
    fetch(url, {method: 'PUT'})
        .then(function(r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function() {
            var fn = window._shazamFns;
            if (fn) fn.loadList(prefix);
        })
        .catch(function(err) {
            console.error('[ANIM] save error', err);
            alert('Erreur sauvegarde animateur: ' + err.message);
        });
}

let allShazamRowsData = { shazam: [], animateurs: [] };
let currentShazamFilterByPrefix = {
    shazam: 'all',
    animateurs: 'all'
};
var currentAnimFilterByPrefix = { shazam: '', animateurs: '' };
var currentStatutFilterByPrefix = { shazam: '', animateurs: '' };

var _prefillController = null;   // AbortController pour le fetch en cours
var _prefillCancelled = false;   // Flag d'annulation


function getAnimFilter(prefix) {
    return currentAnimFilterByPrefix[prefix] || '';
}

function setAnimFilter(value, prefix) {
    currentAnimFilterByPrefix[prefix] = value || '';
    renderShazamListTable(allShazamRowsData[prefix] || [], {}, prefix);
    updateShazamModeUI(getShazamFilter(prefix), prefix);
}

function getStatutFilter(prefix) {
    return currentStatutFilterByPrefix[prefix] || '';
}

function setStatutFilter(value, prefix) {
    currentStatutFilterByPrefix[prefix] = value || '';
    renderShazamListTable(allShazamRowsData[prefix] || [], {}, prefix);
    updateShazamModeUI(getShazamFilter(prefix), prefix);
}


function getShazamFilter(prefix) {
    return currentShazamFilterByPrefix[prefix] || 'all';
}

function setShazamFilter(prefix, mode) {
    currentShazamFilterByPrefix[prefix] = mode || 'all';
}

function getShazamElement(prefix, name) {
    return document.getElementById(prefix + '_' + name);
}

function getShazamButton(prefix, action) {
    return document.getElementById('btn_' + prefix + '_' + action);
}

function getShazamModeDisplayName(mode) {
    if (mode === 'web') return 'Animateurs';
    if (mode === 'shazam_studio') return 'Studio';
    if (mode === 'shazam_ext') return 'Ext.';
    if (mode === 'shazam') return 'Shazam';
    return 'Tous';
}

function getShazamPrefillButtonText(mode, prefix) {
    if (prefix === 'animateurs') {
        return '🔍 Synchro VPS et Matching RadioDJ';
    }
    return '🔍 Matching RadioDJ';
}

function updateShazamFilterGroups(mode, prefix) {
    let filterGroup = getShazamElement(prefix, 'filter_group');
    if (!filterGroup) return;
    filterGroup.querySelectorAll('button').forEach(function(btn) {
        btn.classList.toggle('active', btn.dataset.shazamFilter === mode);
    });
}

function updateShazamModeUI(mode, prefix) {
    let modeName = getShazamModeDisplayName(mode);
    let titleEl = getShazamElement(prefix, 'mode_title');
    let labelEl = getShazamElement(prefix, 'mode_label');
    let btnPrefill = getShazamButton(prefix, 'prefill');

    if (titleEl) {
        titleEl.innerHTML = prefix === 'animateurs'
            ? '<b>📝 Animateurs</b> — Soumissions VPS → Matching RadioDJ → Pré-remplissage push'
            : '<b>🎵 Shazam</b> — Titres Shazam → Matching RadioDJ → Pré-remplissage push';
    }
    if (labelEl) {
        if (prefix === 'shazam') {
            var animF = getAnimFilter(prefix);
            var txt = 'Mode : ' + modeName;
            if (animF) txt += ' | Animateur : ' + animF;
            labelEl.textContent = txt;
        } else {
            var animF2 = getAnimFilter(prefix);
            var txt2 = animF2 ? 'Animateur : ' + animF2 : 'Tous les animateurs';
            labelEl.textContent = txt2;
        }
    }
    if (btnPrefill) {
        btnPrefill.textContent = getShazamPrefillButtonText(mode, prefix);
    }

    updateShazamFilterGroups(mode, prefix);
}

function renderShazamListTable(rows, matchedSet, prefix = 'shazam') {
    matchedSet = matchedSet || {};
    let tbody = getShazamElement(prefix, 'list_body');
    let activeFilter = getShazamFilter(prefix);
    let animFilter = getAnimFilter(prefix);
    let statutFilter = getStatutFilter(prefix);
    let isAnimateursTab = (prefix === 'animateurs');
    // +2 colonnes : statut + actions rejet/correction
    let colCount = isAnimateursTab ? 12 : 8;

    let filtered = (rows || []).filter(function(r) {
        let src = (r.source || '').toLowerCase();
        let isWeb = ['shazam', 'recommandation', 'saisie manuelle', 'autre', 'formulaire_programmeur'].indexOf(src) !== -1;
        let isExt = src.includes('externe');

        // Filtre source
        if (activeFilter === 'shazam_studio') return !isWeb && !isExt;
        if (activeFilter === 'shazam_ext') return isExt;
        if (activeFilter === 'web') return isWeb;
        return true; // 'all' ou autres
    });

    // Filtre animateur
    if (animFilter) {
        filtered = filtered.filter(function(r) {
            return (r.animateur || '') === animFilter;
        });
    }

    // Filtre statut (R6)
    if (statutFilter) {
        filtered = filtered.filter(function(r) {
            var s = (r.statut || 'nouveau').toLowerCase();
            if (statutFilter === 'nouveau') return s === 'nouveau' || !s;
            if (statutFilter === 'matche') return s === 'matche' || s === 'matché';
            if (statutFilter === 'rejete') return s === 'rejete' || s === 'rejeté' || s === 'rejete_corrigé' || s === 'rejete_corrige';
            if (statutFilter === 'pousse') return s === 'pousse_azura' || s === 'poussé_azura' || s === 'pousse' || s === 'poussé';
            return true;
        });
    }

    let infoEl = getShazamElement(prefix, 'filter_info');
    if (infoEl) {
        infoEl.textContent = filtered.length + ' / ' + (rows ? rows.length : 0) + ' affiché(s)';
    }

    if (!tbody) return;
    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="' + colCount + '" class="text-center text-muted p-2 small">' +
            'Aucune entrée dans ce filtre.</td></tr>';
        return;
    }

    tbody.innerHTML = filtered.map(function(r) {
        let key = (r.artiste || '').toLowerCase().trim() + '||' +
                  (r.titre || '').toLowerCase().trim();
        let songId = matchedSet[key];
        let matchCell = songId
            ? '<span class="text-success fw-bold" title="ID ' + songId + '">✓</span>'
            : (matchedSet && Object.keys(matchedSet).length > 0)
            ? '<span class="text-danger fw-bold" title="Non trouvé dans RadioDJ">✗</span>'
            : '<span class="text-muted" title="Pas encore matché">?</span>';

        let animBadge = '<td class="text-center">' + getAnimateurBadge(r.animateur, r.id, prefix) + '</td>';
        let srcBadge = '<td class="text-center">' + getShazamSourceBadge(r.source) + '</td>';
        let animSrcBadge = '<td class="text-center">' + getAnimSourceBadge(r.source) + '</td>';
        // R6 : badge statut
        let statutCell = '<td class="text-center">' + getStatutBadge(r.statut, r.id, prefix, r.statut_vps) + '</td>';
        // R15 : actions rejet/correction
        let actionCell = getRejetActionsCell(r.statut, r.id, prefix, r.artiste, r.titre);

        if (isAnimateursTab) {
            // Ordre des colonnes : Animateur | Source | Artiste | Titre | Genre | Commentaire | Date | Match | Statut | Actions | Video | Edit
            let dateVal = r.date_soumission || r.date_reconnaissance || '';
            let genreCell = '<td class="text-center">' + getGenreBadge(r.genre) + '</td>';
            let commentCell = getCommentaireCell(r.commentaire);
            let videoCell = r.video_url
                ? '<td class="text-center"><a href="' + escapeAttr(r.video_url) + '" target="_blank" rel="noopener" title="Voir la vidéo"><span style="color:#ef4444">▶</span></a></td>'
                : '<td class="text-center text-muted" style="font-size:0.75rem">—</td>';
            let editCell = r.vps_id
                ? '<td class="text-center"><a href="https://programmes.airvs.fr/?edit=' + r.vps_id + '" target="_blank" rel="noopener" title="Modifier sur le VPS" style="color:#0ea5e9;font-weight:bold">✎</a></td>'
                : '<td class="text-center text-muted" style="font-size:0.75rem">—</td>';
            return '<tr>' +
                animBadge +
                animSrcBadge +
                '<td class="small">' + escapeHtml(r.artiste || '') + '</td>' +
                '<td class="small">' + escapeHtml(r.titre || '') + '</td>' +
                genreCell +
                commentCell +
                '<td class="small text-muted">' + dateVal + '</td>' +
                '<td class="text-center">' + matchCell + '</td>' +
                statutCell +
                actionCell +
                videoCell +
                editCell +
                '</tr>';
        } else {
            return '<tr>' +
                srcBadge +
                animBadge +
                '<td class="small">' + escapeHtml(r.artiste || '') + '</td>' +
                '<td class="small">' + escapeHtml(r.titre || '') + '</td>' +
                '<td class="small text-muted">' + (r.date_reconnaissance || '') + '</td>' +
                '<td class="text-center">' + matchCell + '</td>' +
                statutCell +
                actionCell +
                '</tr>';
        }
    }).join('');
}


// ── Charger la liste Shazam & Web (aperçu dans le tableau local) ──
function loadShazamList(prefix = 'shazam') {
    // Peupler les dropdowns d'animateurs (une seule fois)
    if (!window._dynamicAnimateurList) {
        populateAnimateurDropdowns();
    }

    // Phase 2 : l'onglet Animateurs lit sa propre table dédiée
    var url = (prefix === 'animateurs')
        ? '/api/animateurs/list?limit=150'
        : '/api/shazam/list?limit=150';

    fetch(url)
        .then(function(r) { return r.json(); })
        .then(function(rows) {
            let countEl = getShazamElement(prefix, 'total_count');
            let lastSyncEl = getShazamElement(prefix, 'last_sync');

            if (!Array.isArray(rows) || rows.length === 0) {
                allShazamRowsData[prefix] = [];
                renderShazamListTable([], {}, prefix);
                if (countEl) countEl.textContent = '0 entrée';
                if (lastSyncEl) lastSyncEl.textContent = '—';
                return;
            }

            let filtered = rows;

            // Pour l'onglet Shazam, exclure les sources web résiduelles
            if (prefix === 'shazam') {
                var _WEB_SOURCES = ['shazam', 'recommandation', 'saisie manuelle', 'autre', 'formulaire_programmeur'];
                filtered = rows.filter(function(r) {
                    let src = (r.source || '').toLowerCase();
                    return !_WEB_SOURCES.some(function(v) { return src === v; });
                });
            }

            allShazamRowsData[prefix] = filtered;

            if (countEl) countEl.textContent = filtered.length + ' entrée' + (filtered.length > 1 ? 's' : '');
            var lastDate = (filtered[0] && (filtered[0].date_soumission || filtered[0].date_reconnaissance)) || '—';
            if (lastSyncEl) lastSyncEl.textContent = lastDate;

            renderShazamListTable(filtered, {}, prefix);
        })
        .catch(function() {
            let listBody = getShazamElement(prefix, 'list_body');
            if (listBody) {
                var colN = (prefix === 'animateurs') ? 8 : 6;
                listBody.innerHTML =
                    '<tr><td colspan="' + colN + '" class="text-center text-danger p-2 small">Erreur de chargement.</td></tr>';
            }
        });
}

// ── Helpers Shazam / Animateurs ──
function setShazamModeFromTab(tabButton) {
    if (!tabButton || !tabButton.dataset.shazamMode) return;
    let prefix = tabButton.dataset.panePrefix || 'shazam';
    let mode = tabButton.dataset.shazamMode || 'all';
    // Les data-attributes des onglets valent 'shazam'/'web' mais nos
    // filtres shazam utilisent 'all'/'shazam_studio'/'shazam_ext'
    if (mode === 'shazam') mode = 'all';
    if (mode === 'web') mode = 'all';
    setShazamFilter(prefix, mode);
    setAnimFilter('', prefix);
    updateShazamModeUI(getShazamFilter(prefix), prefix);
    loadShazamList(prefix);
}

// ── Helpers bouton Annuler pour prefill/pipeline ──
function _insertCancelBtn(anchorEl, prefix) {
    _removeCancelBtn(anchorEl);
    var cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-sm btn-outline-danger';
    cancelBtn.textContent = '✕ Annuler';
    cancelBtn.id = prefix + '_prefill_cancel';
    cancelBtn.onclick = function(e) {
        e.preventDefault();
        if (_prefillController) {
            _prefillController.abort();
        }
        // Poser le flag d'annulation backend
        fetch('/api/shazam/prefill/cancel', { method: 'POST' }).catch(function() {});
    };
    anchorEl.parentNode.insertBefore(cancelBtn, anchorEl.nextSibling);
}

function _removeCancelBtn(anchorEl) {
    if (!anchorEl || !anchorEl.parentNode) return;
    var existing = anchorEl.parentNode.querySelector('[id$="_prefill_cancel"]');
    if (existing) existing.remove();
}

function handleShazamPrefill(prefix) {
    let btn = getShazamButton(prefix, 'prefill');
    let statusEl = getShazamElement(prefix, 'prefill_status');
    if (!btn || !statusEl) return;

    // Si un prefill est en cours, l'annuler
    if (_prefillController) {
        _prefillController.abort();
        _prefillController = null;
        btn.disabled = false;
        btn.textContent = getShazamPrefillButtonText(getShazamFilter(prefix), prefix);
        statusEl.textContent = 'Annulé.';
        _removeCancelBtn(btn);
        return;
    }

    let currentFilter = getShazamFilter(prefix);
    let isAnimateurs = (prefix === 'animateurs');
    btn.disabled = true;
    btn.textContent = isAnimateurs ? '⏳ Synchro VPS + Matching...' : '⏳ Matching en cours...';
    statusEl.textContent = 'Recherche des correspondances dans RadioDJ...';

    _insertCancelBtn(btn, prefix);

    let params = new URLSearchParams();
    let srcFilter = getShazamFilter(prefix);
    let animFilterVal = getAnimFilter(prefix);

    if (isAnimateurs) {
        params.set('sync_vps', '1');
        params.set('mode', 'web');
    } else {
        if (srcFilter === 'shazam_studio') params.set('mode', 'shazam_studio');
        else if (srcFilter === 'shazam_ext') params.set('mode', 'shazam_ext');
    }
    if (animFilterVal) params.set('animateur', animFilterVal);

    let url = '/api/shazam/prefill?' + params.toString();

    _prefillController = new AbortController();

    fetch(url, { signal: _prefillController.signal })
        .then(function(r) {
            if (r.status === 401 || r.status === 302) {
                throw new Error('Session expirée — rechargez la page');
            }
            return r.json();
        })
        .then(function(data) {
            _prefillController = null;
            _removeCancelBtn(btn);
            btn.disabled = false;
            btn.textContent = getShazamPrefillButtonText(currentFilter, prefix);

            if (data.error) {
                statusEl.textContent = 'Erreur : ' + data.error;
                return;
            }

            updateShazamListWithMatch(data.found, data.not_found, prefix);
            if (prefix === 'animateurs') {
                if (window._onAnimateursMatchResult) window._onAnimateursMatchResult(data.found, data.not_found, data);
            } else {
                if (window._onShazamMatchResult) window._onShazamMatchResult(data.found, data.not_found, data);
            }
            // Le statut résultat est géré par la fonction populate (pushStats + statusEl)
        })
        .catch(function(err) {
            _prefillController = null;
            _removeCancelBtn(btn);
            btn.disabled = false;
            btn.textContent = getShazamPrefillButtonText(currentFilter, prefix);
            if (err.name === 'AbortError') {
                statusEl.textContent = 'Annulé par l\u2019utilisateur.';
            } else {
                statusEl.textContent = '❌ ' + (err.message || 'Erreur réseau');
            }
        });
}

function handleShazamPipeline(prefix) {
    let btn = getShazamButton(prefix, 'pipeline');
    let terminal = getShazamElement(prefix, 'pipeline_terminal');
    let logCard = getShazamElement(prefix, 'pipeline_log_card');
    let statusEl = getShazamElement(prefix, 'prefill_status');
    if (!btn || !terminal || !logCard || !statusEl) return;

    btn.disabled = true;
    btn.textContent = '⏳ Envoi...';
    logCard.style.display = 'block';
    terminal.textContent = '⏳ Déclenchement du pipeline Shazam...';
    statusEl.textContent = 'Pipeline en cours...';

    let sheetsIdEl = getShazamElement(prefix, 'pipeline_sheets_id');
    let sheetsId = sheetsIdEl ? sheetsIdEl.value || '' : '';

    let pipelineData = { sheets_id: sheetsId };
    let srcF = getShazamFilter(prefix);
    let animF = getAnimFilter(prefix);

    if (prefix === 'animateurs') {
        pipelineData.mode = 'web';
    } else {
        if (srcF === 'shazam_studio') pipelineData.mode = 'shazam_studio';
        else if (srcF === 'shazam_ext') pipelineData.mode = 'shazam_ext';
    }
    if (animF) pipelineData.animateur = animF;

    fetch('/api/shazam/pipeline/trigger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(pipelineData)
    })
    .then(function(r) {
        if (r.status === 401 || r.status === 302) {
            throw new Error('Session expirée — rechargez la page');
        }
        return r.json();
    })
    .then(function(d) {
        btn.disabled = false;
        btn.textContent = '▶ Pipeline';

        if (d.status === 'ok') {
            terminal.textContent = (d.logs || []).join('\n');
            updateShazamListWithMatch(d.found || [], d.not_found || [], prefix);
            if (d.found || d.not_found) {
                if (prefix === "animateurs") {
                    if (window._onAnimateursMatchResult) window._onAnimateursMatchResult(d.found || [], d.not_found || [], d);
                } else {
                    if (window._onShazamMatchResult) window._onShazamMatchResult(d.found || [], d.not_found || [], d);
                }
            }
            // Le statut résultat est géré par la fonction populate (pushStats + statusEl)
        } else {
            terminal.textContent = '❌ ' + (d.message || 'Erreur') + '\n' +
                (d.logs || []).join('\n');
            statusEl.textContent = '❌ ' + (d.message || 'Erreur');
        }
    })
    .catch(function(err) {
        btn.disabled = false;
        btn.textContent = '▶ Pipeline';
        terminal.textContent = '❌ ' + (err.message || 'Erreur réseau');
        statusEl.textContent = '❌ ' + (err.message || 'Erreur réseau');
    });
}

function handleShazamCsvImport(prefix, file) {
    let statusEl = getShazamElement(prefix, 'prefill_status');
    if (!file || !statusEl) return;

    let input = getShazamElement(prefix, 'csv_file');
    if (!input) return;
    let btnLabel = input.parentElement;
    let originalText = btnLabel.textContent.trim();

    btnLabel.textContent = originalText + ' ⏳...';
    statusEl.textContent = 'Import en cours de ' + file.name + '...';

    let formData = new FormData();
    formData.append('file', file);

    fetch('/api/shazam/import_csv', {
        method: 'POST',
        body: formData
    })
    .then(function(r) {
        if (r.status === 401 || r.status === 302) throw new Error('Session expirée');
        return r.json();
    })
    .then(function(d) {
        btnLabel.textContent = originalText;
        if (d.status === 'ok') {
            statusEl.textContent = '✅ ' + d.message;
            let refreshBtn = getShazamButton(prefix, 'list_refresh');
            if (refreshBtn) refreshBtn.click();
            if (d.found || d.not_found) {
                if (prefix === 'animateurs') {
                    if (window._onAnimateursMatchResult) window._onAnimateursMatchResult(d.found || [], d.not_found || []);
                } else {
                    if (window._onShazamMatchResult) window._onShazamMatchResult(d.found || [], d.not_found || []);
                }
            }
        } else {
            statusEl.textContent = '❌ ' + (d.message || 'Erreur');
        }
    })
    .catch(function(err) {
        btnLabel.textContent = originalText;
        statusEl.textContent = '❌ ' + (err.message || 'Erreur réseau');
    });
}

// ── Mettre à jour le tableau local Shazam avec les résultats de match ──
function updateShazamListWithMatch(found, notFound, prefix = 'shazam') {
    let matchedSet = {};
    (found || []).forEach(function(t) {
        let key = (t.artist || '').toLowerCase().trim() + '||' +
                  (t.title || '').toLowerCase().trim();
        matchedSet[key] = t.ID;
    });

    renderShazamListTable(allShazamRowsData[prefix] || [], matchedSet, prefix);
}
// ── Sync VPS : forcer la synchronisation OVH immediat ──
function syncVpsNow(prefix) {
    // Identifier le bouton ⬇ VPS pour feedback visuel (dans le card-header du pane courant)
    var paneId = (prefix === 'shazam') ? 'push-shazam' : 'push-animateurs';
    var pane = document.getElementById(paneId);
    var vpsBtn = null;
    if (pane) {
        var infoBtns = pane.querySelectorAll('.card-header .btn-outline-info');
        for (var i = 0; i < infoBtns.length; i++) {
            if (infoBtns[i].textContent.trim().indexOf('VPS') !== -1) { vpsBtn = infoBtns[i]; break; }
        }
    }
    var statusEl = getShazamElement(prefix, 'filter_info');
    if (statusEl) statusEl.textContent = '⬇ Récupération VPS en cours...';
    if (vpsBtn) { vpsBtn.disabled = true; vpsBtn.textContent = '⬇ ...'; }
    fetch('/api/sync/vps/now', { method: 'POST' })
        .then(function(r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function(data) {
            var logCount = (data.logs || []).length;
            if (statusEl) statusEl.textContent = '⬇ ' + logCount + ' opération(s) récupérée(s)';
            if (vpsBtn) { vpsBtn.disabled = false; vpsBtn.textContent = '⬇ VPS'; }
            // Forcer le rechargement de la liste (nouveaux possibles)
            window._dynamicAnimateurList = null;
            loadShazamList(prefix);
        })
        .catch(function(err) {
            if (statusEl) statusEl.textContent = '❌ Erreur VPS: ' + err.message;
            if (vpsBtn) { vpsBtn.disabled = false; vpsBtn.textContent = '⬇ VPS'; }
            console.error('[SYNC-VPS]', err);
        });
}

    // ── Pipeline Sheets loaders ──
    function loadShazamPipelineSheetsList() {
        let sel = document.getElementById('shazam_pipeline_sheets_id');
        if (!sel) return;
        fetch('/api/sheets/list?t=' + Date.now())
            .then(function(r) { return r.json(); })
            .then(function(data) {
                sel.innerHTML = '';
                if (!data.sheets || data.sheets.length === 0) {
                    sel.innerHTML = '<option value="">-- aucun Sheet --</option>';
                    return;
                }
                sel.innerHTML = '<option value="">— Pas de sync Sheet —</option>';
                data.sheets.forEach(function(s) {
                    let opt = document.createElement('option');
                    opt.value = s.alias;
                    opt.textContent = s.alias;
                    sel.appendChild(opt);
                });
            })
            .catch(function(err) {
                sel.innerHTML = '<option value="">-- Erreur --</option>';
                console.error('loadShazamPipelineSheetsList:', err);
            });
    }

    function loadAnimateursPipelineSheetsList() {
        let sel = document.getElementById('animateurs_pipeline_sheets_id');
        if (!sel) return;
        fetch('/api/sheets/list?t=' + Date.now())
            .then(function(r) { return r.json(); })
            .then(function(data) {
                sel.innerHTML = '';
                if (!data.sheets || data.sheets.length === 0) {
                    sel.innerHTML = '<option value="">-- aucun Sheet --</option>';
                    return;
                }
                sel.innerHTML = '<option value="">— Pas de sync Sheet —</option>';
                data.sheets.forEach(function(s) {
                    let opt = document.createElement('option');
                    opt.value = s.alias;
                    opt.textContent = s.alias;
                    sel.appendChild(opt);
                });
            })
            .catch(function(err) {
                sel.innerHTML = '<option value="">-- Erreur --</option>';
                console.error('loadAnimateursPipelineSheetsList:', err);
            });
    }
    loadShazamPipelineSheetsList();
    loadAnimateursPipelineSheetsList();

    // ── CSV file input handlers (manquants dans le code original) ──
    ['shazam', 'animateurs'].forEach(function(prefix) {
        var input = document.getElementById(prefix + '_csv_file');
        if (input) {
            input.addEventListener('change', function() {
                if (this.files && this.files[0]) {
                    handleShazamCsvImport(prefix, this.files[0]);
                    this.value = '';
                }
            });
        }
    });


// ════════════════════════════════════
// R15 : Modal Rejeter / Corriger / Suggestions
// ════════════════════════════════════

function _closeRejetModal() {
    var m = document.getElementById('_rejet_modal');
    if (m) m.remove();
}

function _closeCorrectionModal() {
    var m = document.getElementById('_correction_modal');
    if (m) m.remove();
}

// ── Rejeter un manquant ──
function rejeterManquant(rowId, table) {
    _closeRejetModal();
    table = table || 'shazam';
    var modalHtml = '<div id="_rejet_modal" style="position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.5);">' +
        '<div style="background:#fff;border-radius:8px;padding:20px;min-width:360px;max-width:500px;box-shadow:0 4px 24px rgba(0,0,0,0.3);">' +
        '<h5 style="margin:0 0 12px 0;color:#ef4444;">🚫 Rejeter ce titre</h5>' +
        '<p style="font-size:0.85rem;color:#6b7280;margin:0 0 8px 0;">Ce titre sera marqué comme rejeté et ne sera plus proposé au matching.</p>' +
        '<label style="font-size:0.8rem;font-weight:bold;display:block;margin-bottom:4px;">Motif de rejet (optionnel) :</label>' +
        '<textarea id="_rejet_motif" rows="2" style="width:100%;border:1px solid #d1d5db;border-radius:4px;padding:6px;font-size:0.8rem;resize:vertical;" placeholder="Ex. : doublon, erreur de reconnaissance..."></textarea>' +
        '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px;">' +
        '<button onclick="window._shazamFns._closeRejetModal()" style="padding:6px 14px;border:1px solid #d1d5db;border-radius:4px;background:#fff;cursor:pointer;font-size:0.8rem;">Annuler</button>' +
        '<button onclick="window._shazamFns._doRejeter(' + rowId + ',\'' + table + '\')" style="padding:6px 14px;border:none;border-radius:4px;background:#ef4444;color:#fff;cursor:pointer;font-size:0.8rem;font-weight:bold;">Rejeter</button>' +
        '</div></div></div>';
    document.body.insertAdjacentHTML('beforeend', modalHtml);
}

function _doRejeter(rowId, table) {
    var motifEl = document.getElementById('_rejet_motif');
    var motif = motifEl ? motifEl.value.trim() : '';
    _closeRejetModal();
    var prefix = (table === 'animateurs') ? 'animateurs' : 'shazam';
    fetch('/api/manquants/rejeter', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ row_id: rowId, table: table, motif: motif })
    })
    .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
    })
    .then(function(data) {
        if (data.error) {
            alert('Erreur : ' + data.error);
            return;
        }
        // Recharger la liste
        var fn = window._shazamFns;
        if (fn) fn.loadList(prefix);
    })
    .catch(function(err) {
        alert('Erreur rejet : ' + err.message);
    });
}

// ── Corriger un manquant (rejete_corrigé + nouvelle forme) ──
function corrigerManquant(rowId, table) {
    _closeCorrectionModal();
    table = table || 'shazam';
    // Trouver la ligne dans les données locales pour pré-remplir
    var prefix = (table === 'animateurs') ? 'animateurs' : 'shazam';
    var data = allShazamRowsData[prefix] || [];
    var row = data.find(function(r) { return r.id == rowId; });
    var currentArtiste = row ? (row.artiste || '') : '';
    var currentTitre = row ? (row.titre || '') : '';

    var modalHtml = '<div id="_correction_modal" style="position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.5);">' +
        '<div style="background:#fff;border-radius:8px;padding:20px;min-width:400px;max-width:560px;box-shadow:0 4px 24px rgba(0,0,0,0.3);">' +
        '<h5 style="margin:0 0 12px 0;color:#f59e0b;">✎ Corriger artiste / titre</h5>' +
        '<p style="font-size:0.85rem;color:#6b7280;margin:0 0 8px 0;">L\'entrée originale sera marquée rejetée. Une nouvelle entrée corrigée sera créée et matchée si possible.</p>' +
        '<label style="font-size:0.8rem;font-weight:bold;display:block;margin-bottom:4px;">Artiste corrigé :</label>' +
        '<input id="_corr_artiste" type="text" value="' + escapeAttr(currentArtiste) + '" style="width:100%;border:1px solid #d1d5db;border-radius:4px;padding:6px;font-size:0.85rem;margin-bottom:8px;" oninput="window._shazamFns._loadSuggestions()">' +
        '<div id="_corr_suggestions_art" style="font-size:0.75rem;color:#6366f1;min-height:18px;margin-bottom:8px;"></div>' +
        '<label style="font-size:0.8rem;font-weight:bold;display:block;margin-bottom:4px;">Titre corrigé :</label>' +
        '<input id="_corr_titre" type="text" value="' + escapeAttr(currentTitre) + '" style="width:100%;border:1px solid #d1d5db;border-radius:4px;padding:6px;font-size:0.85rem;margin-bottom:8px;">' +
        '<label style="font-size:0.8rem;font-weight:bold;display:block;margin-bottom:4px;">Motif (optionnel) :</label>' +
        '<input id="_corr_motif" type="text" style="width:100%;border:1px solid #d1d5db;border-radius:4px;padding:6px;font-size:0.85rem;margin-bottom:8px;" placeholder="Correction orthographe, mauvaise reconnaissance...">' +
        '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px;">' +
        '<button onclick="window._shazamFns._closeCorrectionModal()" style="padding:6px 14px;border:1px solid #d1d5db;border-radius:4px;background:#fff;cursor:pointer;font-size:0.8rem;">Annuler</button>' +
        '<button onclick="window._shazamFns._doCorriger(' + rowId + ',\'' + table + '\')" style="padding:6px 14px;border:none;border-radius:4px;background:#f59e0b;color:#000;cursor:pointer;font-size:0.8rem;font-weight:bold;">Corriger & Matcher</button>' +
        '</div></div></div>';
    document.body.insertAdjacentHTML('beforeend', modalHtml);
}

function _doCorriger(rowId, table) {
    var artEl = document.getElementById('_corr_artiste');
    var titEl = document.getElementById('_corr_titre');
    var motifEl = document.getElementById('_corr_motif');
    var newArt = artEl ? artEl.value.trim() : '';
    var newTit = titEl ? titEl.value.trim() : '';
    var motif = motifEl ? motifEl.value.trim() : '';
    if (!newArt || !newTit) {
        alert('Artiste et titre corrigés sont obligatoires.');
        return;
    }
    _closeCorrectionModal();
    var prefix = (table === 'animateurs') ? 'animateurs' : 'shazam';
    fetch('/api/manquants/corriger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            row_id: rowId,
            table: table,
            correction_artiste: newArt,
            correction_titre: newTit,
            motif: motif
        })
    })
    .then(function(r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
    })
    .then(function(data) {
        if (data.error) {
            alert('Erreur : ' + data.error);
            return;
        }
        // Recharger la liste
        var fn = window._shazamFns;
        if (fn) fn.loadList(prefix);
    })
    .catch(function(err) {
        alert('Erreur correction : ' + err.message);
    });
}

// ── Auto-suggestions fuzzy (R18) ──
var _suggestTimer = null;
function _loadSuggestions() {
    if (_suggestTimer) clearTimeout(_suggestTimer);
    _suggestTimer = setTimeout(function() {
        var artEl = document.getElementById('_corr_artiste');
        var q = artEl ? artEl.value.trim() : '';
        var sugEl = document.getElementById('_corr_suggestions_art');
        if (!sugEl) return;
        if (q.length < 2) { sugEl.textContent = ''; return; }
        fetch('/api/shazam/suggestions?q=' + encodeURIComponent(q) + '&limit=5')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var suggestions = data.suggestions || [];
                if (suggestions.length === 0) {
                    sugEl.textContent = '— aucune suggestion —';
                    return;
                }
                sugEl.innerHTML = 'Suggestions : ' + suggestions.map(function(s) {
                    return '<span style="cursor:pointer;text-decoration:underline;margin-right:8px;" onclick="document.getElementById(\'_corr_artiste\').value=\'' + escapeAttr(s) + '\'">' + escapeHtml(s) + '</span>';
                }).join('');
            })
            .catch(function() { sugEl.textContent = ''; });
    }, 300);
}

    // ── Dispatcher _shazamClick (pour les onclick inline dans le HTML) ──
    window._shazamClick = function(action, prefix, extra) {
        switch (action) {
            case 'refresh': loadShazamList(prefix); break;
            case 'syncVps': syncVpsNow(prefix); break;
            case 'pipeline': handleShazamPipeline(prefix); break;
            case 'prefill': handleShazamPrefill(prefix); break;
            case 'filter': setShazamFilter(prefix, extra); setAnimFilter('', prefix); break;
            case 'statutFilter': setStatutFilter(extra || '', prefix); break;
            case 'closeLog':
                var logCard = getShazamElement(prefix, 'pipeline_log_card');
                if (logCard) logCard.style.display = 'none';
                break;
            case 'editAnim': {
                var rowId2 = parseInt(this.dataset.rowId);
                var tbl2 = this.dataset.table || 'shazam';
                showAnimateurDropdown(rowId2, '', this, tbl2);
                break;
            }
            case 'rejeter': {
                var rejId = parseInt(extra);
                var rejTbl = prefix || 'shazam';
                rejeterManquant(rejId, rejTbl);
                break;
            }
            case 'corriger': {
                var corrId = parseInt(extra);
                var corrTbl = prefix || 'shazam';
                corrigerManquant(corrId, corrTbl);
                break;
            }
            default: console.warn('[shazamClick] Action inconnue:', action);
        }
    };

    // ── Exposer les fonctions globalement ──
    window._shazamFns = {
        loadList: loadShazamList,
        prefill: handleShazamPrefill,
        pipeline: handleShazamPipeline,
        csvImport: handleShazamCsvImport,
        setFilter: setShazamFilter,
        getFilter: getShazamFilter,
        setAnimFilter: setAnimFilter,
        getAnimFilter: getAnimFilter,
        setStatutFilter: setStatutFilter,
        getStatutFilter: getStatutFilter,
        populateAnimDropdowns: populateAnimateurDropdowns,
        updateModeUI: updateShazamModeUI,
        setModeFromTab: setShazamModeFromTab,
        renderTable: renderShazamListTable,
        getData: function(prefix) { return allShazamRowsData[prefix]; },
        showAnimDD: function(rowId, anchorEl, prefix) {
            var tbl = (prefix === 'animateurs') ? 'animateurs' : 'shazam';
            var data = allShazamRowsData[tbl] || [];
            var row = data.find(function(r) { return r.id == rowId; });
            if (row) showAnimateurDropdown(rowId, row.animateur, anchorEl, tbl);
        },
        hideAnimDD: hideAnimateurDropdown,
        syncVps: syncVpsNow,
        rejeter: rejeterManquant,
        corriger: corrigerManquant,
        _closeRejetModal: _closeRejetModal,
        _closeCorrectionModal: _closeCorrectionModal,
        _doRejeter: _doRejeter,
        _doCorriger: _doCorriger,
        _loadSuggestions: _loadSuggestions
    };

    window._dynamicAnimateurList = _dynamicAnimateurList;
    console.log('[INIT-DEBUG] shazam-animateurs.js loaded OK');

})();
