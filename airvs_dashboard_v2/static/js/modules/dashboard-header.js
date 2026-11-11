// ═══════════════════════════════════════════════════════════════════════
// dashboard-header.js — Phase 20.1 — Topbar antenne live (AIRVS Dashboard)
// ═══════════════════════════════════════════════════════════════════════
// Module autonome (IIFE) qui gère la topbar persistante du dashboard :
//   • Poll /api/nowplaying/<station_id> toutes les 5s (lecture cache DB)
//   • Met à jour : artwork, titre en cours, elapsed/duration, barre de
//     progression, playlist source, titre suivant, compteur auditeurs,
//     statut live/auto
//   • Horloge live (update chaque seconde) dans la sidebar (sb-clock-time
//     + sb-clock-date)
//   • Toggle thème clair/sombre (localStorage `airvs_theme`)
//   • Lien AIRVS cliquable → window.location.href = '/'
//
// IMPORTANT : Aucun appel direct à l'API AzuraCast depuis le navigateur
// (Windows .39 n'a pas internet). Toutes les données viennent du cache DB
// alimenté par le worker Ubuntu toutes les 10s.
//
// Dépendances : aucune (module autonome, vanilla JS).
// ═══════════════════════════════════════════════════════════════════════
(function() {
    'use strict';

    // ── Configuration ──────────────────────────────────────────────────────
    var POLL_INTERVAL_MS = 5000;       // Poll nowplaying toutes les 5s
    var CLOCK_INTERVAL_MS = 1000;     // Horloge update chaque seconde
    var STATION_ID_DEFAULT = 7;       // Station par défaut (config.json)
    var CACHE_AGE_MAX_SEC = 60;       // >60s = antenne indisponible
    var THEME_KEY = 'airvs_theme';     // localStorage key

    // ── État interne ────────────────────────────────────────────────────────
    var currentStationId = STATION_ID_DEFAULT;
    var pollTimer = null;
    var clockTimer = null;
    var lastElapsed = 0;
    var lastFetchedAt = 0;
    var progressTimer = null;          // Animation progression entre 2 polls

    // ── Raccourcis DOM (résolus au démarrage) ──────────────────────────────
    var el = {};

    function resolveDom() {
        el.artwork       = document.getElementById('tb-artwork');
        el.title         = document.getElementById('tb-title');
        el.artist        = document.getElementById('tb-artist');
        el.elapsed      = document.getElementById('tb-elapsed');
        el.duration      = document.getElementById('tb-duration');
        el.progress      = document.getElementById('tb-progress');
        el.playlist       = document.getElementById('tb-playlist');
        el.nextTitle      = document.getElementById('tb-next-title');
        el.listeners       = document.getElementById('tb-listeners');
        el.liveBadge      = document.getElementById('tb-live-badge');
        el.stationSelector = document.getElementById('tb-station-selector');
        el.themeToggle     = document.getElementById('tb-theme-toggle');
        el.sbClockTime     = document.getElementById('sb-clock-time');
        el.sbClockDate     = document.getElementById('sb-clock-date');
        el.sbHeader        = document.getElementById('sb-header');
    }

    // ── Utilitaires ────────────────────────────────────────────────────────
    function escapeHtml(s) {
        if (s === null || s === undefined) return '';
        if (window.escapeHtml) return window.escapeHtml(String(s));
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function formatTime(totalSeconds) {
        var s = parseInt(totalSeconds, 10);
        if (isNaN(s) || s < 0) s = 0;
        var m = Math.floor(s / 60);
        var sec = s % 60;
        return m + ':' + (sec < 10 ? '0' : '') + sec;
    }

    function pad2(n) {
        n = parseInt(n, 10);
        return (n < 10 ? '0' : '') + n;
    }

    // ── Horloge (sidebar) ─────────────────────────────────────────────────
    function updateClock() {
        if (!el.sbClockTime) return;
        var now = new Date();
        var hh = pad2(now.getHours());
        var mm = pad2(now.getMinutes());
        var ss = pad2(now.getSeconds());
        el.sbClockTime.textContent = hh + ':' + mm + ':' + ss;

        if (el.sbClockDate) {
            var jours = ['Dimanche', 'Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi'];
            var mois = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin',
                        'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];
            var j = jours[now.getDay()];
            var d = now.getDate();
            var mo = mois[now.getMonth()];
            el.sbClockDate.textContent = j + ' ' + d + ' ' + mo;
        }
    }

    function startClock() {
        updateClock();
        if (clockTimer) clearInterval(clockTimer);
        clockTimer = setInterval(updateClock, CLOCK_INTERVAL_MS);
    }

    // ── Thème clair/sombre ────────────────────────────────────────────────
    function applyTheme(theme) {
        if (theme === 'dark') {
            document.documentElement.setAttribute('data-theme', 'dark');
            document.body.classList.add('airvs-theme-dark');
            if (el.themeToggle) el.themeToggle.textContent = '☀';
            if (el.themeToggle) el.themeToggle.title = 'Passer en thème clair';
        } else {
            document.documentElement.removeAttribute('data-theme');
            document.body.classList.remove('airvs-theme-dark');
            if (el.themeToggle) el.themeToggle.textContent = '🌙';
            if (el.themeToggle) el.themeToggle.title = 'Passer en thème sombre';
        }
    }

    function toggleTheme() {
        var current = document.documentElement.getAttribute('data-theme');
        var next = (current === 'dark') ? 'light' : 'dark';
        try {
            localStorage.setItem(THEME_KEY, next);
        } catch (e) {
            // localStorage peut être bloqué (sandbox) — on applique quand même
        }
        applyTheme(next);
    }

    function initTheme() {
        var saved = null;
        try {
            saved = localStorage.getItem(THEME_KEY);
        } catch (e) {}
        // Phase 20.1 fix : si l'anti-FOUC a déjà posé data-theme, ne pas écraser
        // (sinon un localStorage='light' d'un test précédent remettrait tout en blanc)
        var alreadySet = document.documentElement.getAttribute('data-theme');
        if (alreadySet) {
            // L'anti-FOUC a déjà appliqué le thème — juste synchroniser le bouton
            if (el.themeToggle) {
                el.themeToggle.textContent = (alreadySet === 'dark') ? '☀' : '🌙';
                el.themeToggle.title = (alreadySet === 'dark') ? 'Passer en thème clair' : 'Passer en thème sombre';
                el.themeToggle.addEventListener('click', toggleTheme);
            }
        } else {
            applyTheme(saved || 'dark');
            if (el.themeToggle) {
                el.themeToggle.addEventListener('click', toggleTheme);
            }
        }
    }

    // ── Rendu topbar ───────────────────────────────────────────────────────
    function setUnavailable(message) {
        if (el.artwork) el.artwork.src = '';
        if (el.artwork) el.artwork.style.visibility = 'hidden';
        if (el.title) el.title.textContent = message || '(antenne indisponible)';
        if (el.artist) el.artist.textContent = '';
        if (el.elapsed) el.elapsed.textContent = '--:--';
        if (el.duration) el.duration.textContent = '--:--';
        if (el.progress) el.progress.style.width = '0%';
        if (el.playlist) el.playlist.textContent = '';
        if (el.nextTitle) el.nextTitle.textContent = '—';
        if (el.listeners) el.listeners.textContent = '—';
        if (el.liveBadge) {
            el.liveBadge.textContent = 'AUTO';
            el.liveBadge.className = 'topbar-live badge-auto';
        }
        stopProgressAnimation();
    }

    function updateNowPlaying(data) {
        if (!data || data.cached === false || !data.now_playing) {
            setUnavailable(data && data.error ? data.error : null);
            return;
        }

        // Cache trop vieux → antenne potentiellement indisponible
        if (typeof data.cache_age_seconds === 'number' &&
            data.cache_age_seconds > CACHE_AGE_MAX_SEC) {
            setUnavailable('(cache > 60s — antenne indisponible)');
            return;
        }

        var np = data.now_playing || {};
        var song = np.song || {};
        var elapsed = parseInt(np.elapsed, 10) || 0;
        var duration = parseInt(np.duration, 10) || 0;

        // Artwork
        if (el.artwork) {
            var art = song.art || '';
            if (art) {
                el.artwork.src = art;
                el.artwork.style.visibility = 'visible';
                el.artwork.onerror = function() {
                    this.style.visibility = 'hidden';
                };
            } else {
                el.artwork.src = '';
                el.artwork.style.visibility = 'hidden';
            }
        }

        // Titre / artiste en cours
        if (el.title)   el.title.textContent   = song.title || '(titre inconnu)';
        if (el.artist)  el.artist.textContent  = song.artist || '';

        // Durée / progression
        if (el.elapsed)  el.elapsed.textContent  = formatTime(elapsed);
        if (el.duration) el.duration.textContent = formatTime(duration);
        if (el.progress && duration > 0) {
            var pct = Math.min(100, Math.max(0, (elapsed / duration) * 100));
            el.progress.style.width = pct + '%';
        } else if (el.progress) {
            el.progress.style.width = '0%';
        }

        // Playlist source
        if (el.playlist) {
            var pl = np.playlist || '';
            el.playlist.textContent = pl ? ('📂 ' + pl) : '';
            el.playlist.title = pl || '';
        }

        // Titre suivant
        if (el.nextTitle) {
            var pn = data.playing_next || {};
            var pnSong = pn.song || {};
            var nextTxt = '';
            if (pnSong.artist && pnSong.title) {
                nextTxt = pnSong.artist + ' — ' + pnSong.title;
            } else if (pnSong.title) {
                nextTxt = pnSong.title;
            } else if (pnSong.artist) {
                nextTxt = pnSong.artist;
            }
            el.nextTitle.textContent = nextTxt || '—';
            el.nextTitle.title = nextTxt || '';
        }

        // Auditeurs
        if (el.listeners) {
            var lis = data.listeners || {};
            var cur = parseInt(lis.current, 10);
            var txt = isNaN(cur) ? '—' : String(cur);
            if (typeof lis.total === 'number' && lis.total > cur) {
                txt += ' / ' + lis.total;
            }
            el.listeners.textContent = txt;
        }

        // Statut live / auto
        if (el.liveBadge) {
            var live = data.live || {};
            if (live.is_live) {
                el.liveBadge.textContent = '● LIVE';
                el.liveBadge.className = 'topbar-live badge-live';
                if (live.streamer_name) {
                    el.liveBadge.title = 'Direct : ' + live.streamer_name;
                }
            } else {
                el.liveBadge.textContent = 'AUTO';
                el.liveBadge.className = 'topbar-live badge-auto';
                el.liveBadge.title = 'Diffusion automatique (playlist)';
            }
        }

        // Mémoriser pour l'animation progression entre 2 polls
        lastElapsed = elapsed;
        lastFetchedAt = Date.now();
        startProgressAnimation(duration);
    }

    // ── Animation progression entre 2 polls (lissé chaque seconde) ─────────
    function startProgressAnimation(duration) {
        stopProgressAnimation();
        if (!duration || duration <= 0) return;
        progressTimer = setInterval(function() {
            if (!el.progress || !el.elapsed) return;
            var elapsed = lastElapsed + Math.floor((Date.now() - lastFetchedAt) / 1000);
            if (elapsed > duration) elapsed = duration;
            var pct = Math.min(100, Math.max(0, (elapsed / duration) * 100));
            el.progress.style.width = pct + '%';
            el.elapsed.textContent = formatTime(elapsed);
        }, 1000);
    }

    function stopProgressAnimation() {
        if (progressTimer) {
            clearInterval(progressTimer);
            progressTimer = null;
        }
    }

    // ── Poll API ──────────────────────────────────────────────────────────
    function fetchNowPlaying() {
        var url = '/api/nowplaying/' + encodeURIComponent(currentStationId);
        fetch(url, {
            method: 'GET',
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' }
        }).then(function(resp) {
            if (!resp.ok) {
                throw new Error('HTTP ' + resp.status);
            }
            return resp.json();
        }).then(function(data) {
            updateNowPlaying(data);
        }).catch(function(err) {
            // Erreur réseau ou serveur — on garde le dernier état connu,
            // pas de reset immédiat pour éviter le clignotement.
            console.warn('[TOPBAR] poll nowplaying échoué :', err);
        });
    }

    function startPolling() {
        // Premier fetch immédiat pour ne pas attendre 5s
        fetchNowPlaying();
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = setInterval(fetchNowPlaying, POLL_INTERVAL_MS);
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
        stopProgressAnimation();
    }

    // ── Sélecteur de station ──────────────────────────────────────────────
    function initStationSelector() {
        if (!el.stationSelector) return;
        el.stationSelector.addEventListener('change', function() {
            var val = parseInt(this.value, 10);
            if (!isNaN(val) && val > 0) {
                currentStationId = val;
                // Re-fetch immédiat avec la nouvelle station
                fetchNowPlaying();
            }
        });
        // Si une valeur est déjà sélectionnée au chargement, l'utiliser
        if (el.stationSelector.value) {
            var v = parseInt(el.stationSelector.value, 10);
            if (!isNaN(v) && v > 0) currentStationId = v;
        }
    }

    // ── Header sidebar cliquable (AIRVS → /) ──────────────────────────────
    function initSidebarHeaderClick() {
        if (!el.sbHeader) return;
        el.sbHeader.style.cursor = 'pointer';
        el.sbHeader.addEventListener('click', function(e) {
            // Ne pas interférer avec les liens/boutons enfants (si ajoutés plus tard)
            if (e.target.tagName === 'A' || e.target.tagName === 'BUTTON') return;
            window.location.href = '/';
        });
    }

    // ── Démarrage ─────────────────────────────────────────────────────────
    function init() {
        resolveDom();
        initTheme();
        initStationSelector();
        initSidebarHeaderClick();
        startClock();
        startPolling();

        // Pause polling quand l'onglet n'est pas visible (économie ressources)
        document.addEventListener('visibilitychange', function() {
            if (document.hidden) {
                stopPolling();
            } else {
                startPolling();
            }
        });

        console.log('[TOPBAR] dashboard-header.js initialisé (station=' +
                    currentStationId + ', poll=' + POLL_INTERVAL_MS + 'ms)');
    }

    // ── API publique (debug) ───────────────────────────────────────────────
    window.AIRVSTopbar = {
        refresh: fetchNowPlaying,
        setStation: function(sid) {
            var v = parseInt(sid, 10);
            if (!isNaN(v) && v > 0) {
                currentStationId = v;
                if (el.stationSelector) el.stationSelector.value = String(v);
                fetchNowPlaying();
            }
        },
        getStation: function() { return currentStationId; },
        toggleTheme: toggleTheme
    };

    // ── Auto-init au DOM ready ────────────────────────────────────────────
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
