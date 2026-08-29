// [P3-S1] Phase 3 Étape 1-2 — Extraction modules JS initiaux (utils, grille-editoriale, navigation)
/**
 * AIRVS Dashboard — navigation
 * Extrait automatiquement de index.html (L5146–L5352)
 */

(function() {
    'use strict';

    var sidebar = document.getElementById('airvsSidebar');
    var sbNav = document.getElementById('sbNav');
    var overlay = document.getElementById('sbOverlay');
    var toggleBtn = document.getElementById('sbToggle');
    var tabContent = document.getElementById('mainTabContent');

    // ── Core: manually show/hide a section pane ──
    var MAIN_PANE_IDS = [
        'nav-explorateur','nav-pool','nav-atelier','nav-import',
        'nav-couleur','nav-grille-editoriale','nav-maintenance',
        'nav-audience','nav-piges'
    ];
    function activateSection(targetSelector, triggerBtnId) {
        // Hide ALL 9 main panes by ID — works regardless of DOM nesting
        for (var k = 0; k < MAIN_PANE_IDS.length; k++) {
            var p = document.getElementById(MAIN_PANE_IDS[k]);
            if (p) {
                p.classList.remove('active', 'show');
                p.style.display = 'none';
            }
        }
        // Show the target pane
        var target = document.querySelector(targetSelector);
        if (target) {
            target.classList.add('active', 'show');
            target.style.display = 'block';
        }
        // Fire shown.bs.tab so existing JS listeners work
        if (triggerBtnId) {
            var btn = document.getElementById(triggerBtnId);
            if (btn) {
                var evt = new CustomEvent('shown.bs.tab', { bubbles: true });
                btn.dispatchEvent(evt);
            }
        }
    }

    // ── Mobile toggle ──
    function closeMobile() {
        sidebar.classList.remove('open');
        overlay.classList.remove('open');
    }
    toggleBtn.addEventListener('click', function() {
        sidebar.classList.toggle('open');
        overlay.classList.toggle('open');
    });
    overlay.addEventListener('click', closeMobile);

    // ── Sub-menu toggle ──
    sbNav.querySelectorAll(':scope > li > .sb-link[data-submenu]').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            var subId = this.getAttribute('data-submenu');
            var sub = document.getElementById(subId);
            if (sub) {
                sub.classList.toggle('open');
                this.setAttribute('aria-expanded', sub.classList.contains('open'));
            }
        });
    });

    // ── Top-level sidebar navigation ──
    sbNav.querySelectorAll(':scope > li > .sb-link[data-target]').forEach(function(btn) {
        btn.addEventListener('click', function() {
            var targetSelector = this.getAttribute('data-target');
            var btnId = this.id;
            activateSection(targetSelector, btnId);
            // Update sidebar active state
            sbNav.querySelectorAll('.sb-link').forEach(function(l) { l.classList.remove('active'); });
            this.classList.add('active');
            // Close sub-menus (except if this button has one)
            sbNav.querySelectorAll('.sb-sub').forEach(function(s) { s.classList.remove('open'); });
            var subId = this.getAttribute('data-submenu');
            if (subId) {
                var sub = document.getElementById(subId);
                if (sub) { sub.classList.add('open'); this.setAttribute('aria-expanded','true'); }
            }
            closeMobile();
        });
    });

    // ── Sub-menu items (pills + scroll targets) ──
    sbNav.querySelectorAll('.sb-sub .sb-link').forEach(function(btn) {
        btn.addEventListener('click', function() {
            var parentId = this.getAttribute('data-parent');
            var pillId = this.getAttribute('data-pill');
            var scrollId = this.getAttribute('data-scroll');
            console.log('[SB-DEBUG] sub-link click, pillId=', pillId, 'parentId=', parentId, 'scrollId=', scrollId);
            // 1) Ensure parent section is visible
            if (parentId) {
                var parentPane = document.querySelector(parentId);
                if (parentPane && !parentPane.classList.contains('active')) {
                    var parentBtn = sbNav.querySelector(':scope > li > .sb-link[data-target="' + parentId + '"]');
                    if (parentBtn) {
                        activateSection(parentId, parentBtn.id);
                        var subId = parentBtn.getAttribute('data-submenu');
                        if (subId) {
                            var sub = document.getElementById(subId);
                            if (sub) { sub.classList.add('open'); parentBtn.setAttribute('aria-expanded','true'); }
                        }
                    }
                }
            }
            // 2) Activate sub-pill in Pont card (these ARE in proper Bootstrap pill structure)
            if (pillId) {
                setTimeout(function() {
                    var pillBtn = document.getElementById(pillId);
                    console.log('[SB-DEBUG] pill activation, pillId=', pillId, 'pillBtn=', !!pillBtn);
                    if (pillBtn) {
                        try {
                            bootstrap.Tab.getOrCreateInstance(pillBtn).show();
                            console.log('[SB-DEBUG] bootstrap.Tab.show() OK for', pillId);
                        } catch(err) {
                            console.error('[SB-DEBUG] bootstrap.Tab.show() ERROR for', pillId, err);
                        }
                    }
                }, 80);
            }
            // 3) Scroll to element
            if (scrollId) {
                setTimeout(function() {
                    var el = document.getElementById(scrollId);
                    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }, 120);
            }
            // 4) Update sidebar active state
            sbNav.querySelectorAll('.sb-link').forEach(function(l) { l.classList.remove('active'); });
            this.classList.add('active');
            if (parentId) {
                var parentLink = sbNav.querySelector(':scope > li > .sb-link[data-target="' + parentId + '"]');
                if (parentLink) parentLink.classList.add('active');
            }
            closeMobile();
        });
    });

    // ── Shazam/Animateurs global click dispatcher (for inline onclick) ──
    window._shazamClick = function(action, prefix, extra) {
        var fn = window._shazamFns;
        if (!fn) { console.warn('[SHAZAM] _shazamFns not ready yet'); return; }
        switch(action) {
            case 'refresh': fn.loadList(prefix); break;
            case 'prefill': fn.prefill(prefix); break;
            case 'pipeline': fn.pipeline(prefix); break;
            case 'filter':
                var grp = document.getElementById(prefix + '_filter_group');
                if (grp) grp.querySelectorAll('button').forEach(function(b){b.classList.remove('active')});
                if (this && this.classList) this.classList.add('active');
                fn.setFilter(prefix, extra);
                fn.updateModeUI(fn.getFilter(prefix), prefix);
                fn.renderTable(fn.getData(prefix) || [], {}, prefix);
                break;
            case 'closeLog':
                var card = document.getElementById(prefix + '_pipeline_log_card');
                if (card) card.style.display = 'none';
                break;
            case 'editAnim':
                var fn2 = window._shazamFns;
                if (fn2 && fn2.showAnimDD) fn2.showAnimDD(extra, this);
                break;
            case 'hideAnimDD':
                var fn3 = window._shazamFns;
                if (fn3 && fn3.hideAnimDD) fn3.hideAnimDD();
                break;
            case 'syncVps':
                var fn4 = window._shazamFns;
                if (fn4 && fn4.syncVps) fn4.syncVps(prefix);
                break;
        }
    };

    // ── Auto-load Shazam/Animateurs when sub-tab is shown ──
    document.querySelectorAll('#pushTabs button[data-shazam-mode]').forEach(function(btn) {
        btn.addEventListener('shown.bs.tab', function() {
            var fn = window._shazamFns;
            if (fn) fn.setModeFromTab(this);
        });
    });

    // ── Shazam/Animateurs file input change handler (delegation) ──
    var shazamFileHandler = function(prefix) {
        return function(e) {
            var file = e.target.files[0];
            if (file) {
                var fn = window._shazamFns;
                if (fn) fn.csvImport(prefix, file);
            }
        };
    };
    var shazamInput = document.getElementById('shazam_csv_file');
    if (shazamInput) shazamInput.addEventListener('change', shazamFileHandler('shazam'));
    var animInput = document.getElementById('animateurs_csv_file');
    if (animInput) animInput.addEventListener('change', shazamFileHandler('animateurs'));

    // ── Init: hide all panes except Explorateur on load ──
    for (var k = 0; k < MAIN_PANE_IDS.length; k++) {
        var p = document.getElementById(MAIN_PANE_IDS[k]);
        if (p && MAIN_PANE_IDS[k] !== 'nav-explorateur') {
            p.style.display = 'none';
        }
    }

})();