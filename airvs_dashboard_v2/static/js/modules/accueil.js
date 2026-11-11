// ═══════════════════════════════════════════════════════════════════════
// accueil.js — Phase 20.3 — Onglet Accueil : vue d'ensemble du dashboard
// ═══════════════════════════════════════════════════════════════════════
// Alimente les KPIs de l'onglet Accueil UNIQUEMENT via des endpoints déjà
// exposés par le dashboard (aucun appel direct à AzuraCast depuis le
// navigateur — le Windows .39 n'a pas internet) :
//   • /api/nowplaying/<station_id>  → titre en cours + auditeurs (cache DB,
//     alimenté par le worker Ubuntu toutes les 10s — Phase 20.1)
//   • /api/manquants/stats          → manquants en attente + importés 7j
//   • /api/worker_version           → version du worker Ubuntu (table
//     airvs_worker_info)
//   • /api/latest                   → 30 derniers titres synchronisés
//     (top 10 affiché)
// Met aussi à jour la pastille d'état worker de la sidebar (sb-dot-worker).
// Rafraîchissement : à l'affichage de l'onglet + toutes les 60s si visible.
// ═══════════════════════════════════════════════════════════════════════
(function() {
    'use strict';

    var STATION_ID_DEFAULT = 7;   // même station par défaut que dashboard-header.js
    var REFRESH_MS = 60000;       // rafraîchissement KPIs toutes les 60s

    var pane = document.getElementById('nav-accueil');
    if (!pane) return;

    // ── Utilitaires ────────────────────────────────────────────────────────
    function esc(s) {
        if (s === null || s === undefined) return '';
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function getJson(url) {
        return fetch(url).then(function(r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        });
    }

    function setWorkerDot(ok) {
        var d = document.getElementById('sb-dot-worker');
        if (!d) return;
        d.classList.remove('ok', 'ko');
        d.classList.add(ok ? 'ok' : 'ko');
    }

    // ── KPI 1 : En antenne (nowplaying via cache DB worker) ───────────────
    function loadNowplaying() {
        var t = document.getElementById('acc-now-title');
        var a = document.getElementById('acc-now-artist');
        var l = document.getElementById('acc-now-listeners');
        getJson('/api/nowplaying/' + STATION_ID_DEFAULT + '?t=' + Date.now())
            .then(function(d) {
                var np = (d && d.now_playing) || {};
                var song = np.song || {};
                if (t) t.textContent = song.title || '—';
                if (a) a.textContent = song.artist || '';
                if (l) l.textContent = (d.listeners && d.listeners.current) || 0;
            })
            .catch(function() {
                if (t) t.textContent = '(antenne indisponible)';
                if (a) a.textContent = '';
            });
    }

    // ── KPI 2 + 3 : Manquants en attente / Importés 7 jours ───────────────
    function loadManquants() {
        var m = document.getElementById('acc-manquants');
        var i = document.getElementById('acc-importes');
        getJson('/api/manquants/stats?t=' + Date.now())
            .then(function(s) {
                if (m) m.textContent = (s.en_attente !== undefined) ? s.en_attente : '—';
                if (i) i.textContent = (s.importes_7j !== undefined) ? s.importes_7j : '—';
            })
            .catch(function() {
                if (m) m.textContent = '—';
                if (i) i.textContent = '—';
            });
    }

    // ── KPI 4 : Worker Ubuntu (+ pastille sidebar) ────────────────────────
    function loadWorker() {
        var v = document.getElementById('acc-worker');
        var st = document.getElementById('acc-worker-status');
        getJson('/api/worker_version?t=' + Date.now())
            .then(function(d) {
                if (v) v.textContent = d.version_actuelle || '?';
                if (st) { st.textContent = 'connecté'; st.className = 'badge bg-success'; }
                setWorkerDot(true);
            })
            .catch(function() {
                if (v) v.textContent = '—';
                if (st) { st.textContent = 'injoignable'; st.className = 'badge bg-danger'; }
                setWorkerDot(false);
            });
    }

    // ── Derniers titres synchronisés (top 10 de /api/latest) ──────────────
    function loadLatest() {
        var box = document.getElementById('acc-latest-list');
        if (!box) return;
        getJson('/api/latest?t=' + Date.now())
            .then(function(items) {
                if (!Array.isArray(items) || items.length === 0) {
                    box.innerHTML = '<span class="text-muted">Aucun titre synchronisé</span>';
                    return;
                }
                var html = '';
                items.slice(0, 10).forEach(function(t) {
                    html += '<div class="d-flex justify-content-between align-items-center py-1 border-bottom">'
                          + '<span class="text-truncate me-2">' + esc((t.artist || '?') + ' — ' + (t.title || '')) + '</span>'
                          + '<span class="text-muted flex-shrink-0" style="font-size:0.75em">#' + esc(t.ID || '') + '</span>'
                          + '</div>';
                });
                box.innerHTML = html;
            })
            .catch(function() {
                box.innerHTML = '<span class="text-muted">Chargement impossible</span>';
            });
    }

    function refreshAll() {
        loadNowplaying();
        loadManquants();
        loadWorker();
        loadLatest();
    }

    // ── Raccourcis : data-goto-nav (+ data-goto-pill optionnel) ───────────
    // Réutilise le mécanisme de navigation.js : clic sur le bouton sidebar
    // (activateSection), puis activation du pill Bootstrap si demandé.
    document.querySelectorAll('#nav-accueil [data-goto-nav]').forEach(function(btn) {
        btn.addEventListener('click', function() {
            var navBtn = document.getElementById(this.getAttribute('data-goto-nav'));
            if (!navBtn) return;
            navBtn.click();
            var pillId = this.getAttribute('data-goto-pill');
            if (pillId) {
                setTimeout(function() {
                    var pillBtn = document.getElementById(pillId);
                    if (pillBtn) {
                        try { bootstrap.Tab.getOrCreateInstance(pillBtn).show(); } catch (e) { /* pill absent */ }
                    }
                }, 150);
            }
        });
    });

    // ── Rafraîchissement : à l'affichage + périodique si onglet visible ───
    var accueilTab = document.getElementById('nav-accueil-tab');
    if (accueilTab) accueilTab.addEventListener('shown.bs.tab', refreshAll);
    setInterval(function() {
        if (pane.style.display !== 'none' && pane.classList.contains('show')) {
            refreshAll();
        }
    }, REFRESH_MS);

    // Chargement initial (l'onglet Accueil est celui par défaut — Phase 20.3)
    refreshAll();

})();
