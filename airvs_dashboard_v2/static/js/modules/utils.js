// [P3-S1] Phase 3 Étape 1-2 — Extraction modules JS initiaux (utils, grille-editoriale, navigation)
/**
 * AIRVS Dashboard — Fonctions utilitaires partagées
 * Chargé en premier dans index.html (avant les modules fonctionnels).
 *
 * Contient :
 *  - formatDuration / formatDurationLong
 *  - escapeAttr / escapeHtml
 *  - showToast
 *  - copierTexteSansClipboardAPI
 *  - loadCategories / loadSubcategories
 *  - apiFetch (wrapper sécurisé)
 */

/* global bootstrap */

// ════════════════════════════════════
// Formatage durées
// ════════════════════════════════════
window.formatDuration = function(totalSeconds) {
    if (!totalSeconds || isNaN(totalSeconds) || totalSeconds <= 0) return '0:00';
    var mins = Math.floor(totalSeconds / 60);
    var secs = Math.floor(totalSeconds % 60);
    return mins + ':' + (secs < 10 ? '0' : '') + secs;
};

window.formatDurationLong = function(totalSeconds) {
    if (!totalSeconds || isNaN(totalSeconds) || totalSeconds <= 0) return '0:00';
    var hrs = Math.floor(totalSeconds / 3600);
    var mins = Math.floor((totalSeconds % 3600) / 60);
    var secs = Math.floor(totalSeconds % 60);
    if (hrs > 0) {
        return hrs + 'h ' + mins + 'm';
    }
    return mins + ':' + (secs < 10 ? '0' : '') + secs;
};

// Appliquer le formatage sur les cellules .duree-cell au chargement
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.duree-cell').forEach(function(el) {
        var sec = parseFloat(el.innerText);
        if (!isNaN(sec) && sec > 0) {
            el.innerText = window.formatDuration(sec);
        } else {
            el.innerText = '0:00';
        }
    });
});

// ════════════════════════════════════
// Échappement HTML
// ════════════════════════════════════
window.escapeAttr = function(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
};

window.escapeHtml = function(str) {
    return window.escapeAttr(str);
};

// ════════════════════════════════════
// Alert notifications (style original grille)
// ════════════════════════════════════
window.showToast = function(msg, type) {
    type = type || 'success';
    var existing = document.querySelector('.airvs-toast');
    if (existing) existing.remove();

    var div = document.createElement('div');
    div.className = 'airvs-toast';
    var bgClass = type === 'error' ? 'danger' : type === 'warning' ? 'warning' : 'success';
    div.innerHTML = '<div class="alert alert-' + bgClass + ' alert-dismissible py-2 mb-0 shadow">'
        + msg + '<button type="button" class="btn-close btn-close-sm" data-bs-dismiss="alert"></button></div>';
    document.body.appendChild(div);

    // Auto-suppression après 4 secondes
    setTimeout(function() { if (div.parentNode) div.remove(); }, 4000);
};

// ════════════════════════════════════
// Copie texte (fallback hors HTTPS)
// ════════════════════════════════════
window.copierTexteSansClipboardAPI = function(texte, btn) {
    var ta = document.createElement('textarea');
    ta.value = texte;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    if (btn) {
        var original = btn.textContent;
        btn.textContent = ok ? '✅' : '✗';
        setTimeout(function() { btn.textContent = original; }, 1500);
    }
    return ok;
};

// ════════════════════════════════════
// Catégories / Sous-catégories Google Sheets
// ════════════════════════════════════
window.loadCategories = function(selectElementId, onChangeCallback) {
    var selectEl = document.getElementById(selectElementId);
    fetch('/api/categories')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            selectEl.innerHTML = '<option value="">-- Choisir --</option>';
            data.forEach(function(c) {
                selectEl.innerHTML += '<option value="' + c.ID + '">' + c.name + '</option>';
            });
            if (onChangeCallback) {
                selectEl.onchange = function() { onChangeCallback(selectEl.value); };
            }
        });
};

window.loadSubcategories = function(catId, selectElementId) {
    var selectEl = document.getElementById(selectElementId);
    if (!catId) {
        selectEl.disabled = true;
        selectEl.innerHTML = '<option value="">-- Attente --</option>';
        return;
    }
    selectEl.disabled = false;
    selectEl.innerHTML = '<option value="">-- Chargement --</option>';
    fetch('/api/subcategories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category_id: catId })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        selectEl.innerHTML = '<option value="">-- Choisir --</option>';
        data.forEach(function(sc) {
            selectEl.innerHTML += '<option value="' + sc.ID + '">' + sc.name + '</option>';
        });
    });
};

// ════════════════════════════════════
// Wrapper fetch sécurisé
// Détecte les réponses HTML (page login, erreur 500), les sessions expirées,
// et les coupures réseau avant de tenter JSON.parse.
// ════════════════════════════════════
window.apiFetch = async function(url, options) {
    var r = await fetch(url, options);
    var ct = r.headers.get('content-type') || '';
    if (r.status === 401) throw new Error('Session expirée — rechargez la page');
    if (r.status === 0) throw new Error('Réseau indisponible (connexion perdue ?)');
    if (ct.includes('text/html')) throw new Error('Réponse inattendue (HTML au lieu de JSON)');
    try {
        return await r.json();
    } catch (e) {
        var text = await r.text().catch(function() { return ''; });
        throw new Error('Réponse non-JSON : ' + (text.substring(0, 80) || 'vide'));
    }
};
