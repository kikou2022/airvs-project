/**
 * airvs_dashboard_v2/static/js/modules/azura_flags.js
 *
 * Helpers pour la gestion des flags AZURA_*_S6/S7 dans radiodb.songs.comments.
 *
 * Compatible avec l'ancien format 'IN_AZURACAST' et le nouveau format
 * 'AZURA_PUSHED_S*', 'AZURA_ACTIVE_S*', 'AZURA_RETIRED_S*', 'AZURA_REMOVED_S*'.
 *
 * À utiliser partout où l'on doit savoir si un titre est dans AzuraCast.
 *
 * Migration Phase 0 (2026-11) :
 * - L'ancien format 'IN_AZURACAST' reste accepté en lecture (legacy)
 * - Les nouvelles écritures utilisent le format AZURA_*_S6_S7
 * - Cron de resync (Phase 6) mettra à jour les flags en arrière-plan
 *
 * Usage :
 *   // Test simple
 *   if (AzuraFlags.isInAzuracast(t.comments)) { ... }
 *
 *   // État détaillé
 *   var state = AzuraFlags.getState(t.comments);
 *   // → { flag: 'AZURA_ACTIVE_S7', inAzura: true, state: 'ACTIVE', stations: [7] }
 *
 *   // Badge HTML
 *   var badge = AzuraFlags.getBadgeHTML(t.comments);
 *   // → '<span class="badge-azura-active" title="AZURA_ACTIVE_S7">ACTIF S7</span>'
 *
 *   // Construction d'un flag
 *   var flag = AzuraFlags.formatFlag('ACTIVE', [6, 7]);
 *   // → 'AZURA_ACTIVE_S6_S7'
 */

window.AzuraFlags = (function() {
    'use strict';

    /**
     * Retourne true si le titre est présent dans AzuraCast (diffusable).
     *
     * Compatible avec :
     * - Ancien format : 'IN_AZURACAST'
     * - Nouveau format : 'AZURA_PUSHED_S*', 'AZURA_ACTIVE_S*' (pas REMOVED)
     */
    function isInAzuracast(comments) {
        if (!comments) return false;
        var s = String(comments).trim();
        if (s === 'IN_AZURACAST') return true;
        if (s.indexOf('AZURA_') === 0) {
            // AZURA_REMOVED_* signifie que le fichier n'est plus dans AzuraCast
            if (s.indexOf('AZURA_REMOVED') === 0) return false;
            return true;
        }
        return false;
    }

    /**
     * Retourne l'état AzuraCast détaillé d'un titre.
     *
     * Returns:
     *   {
     *     flag: string,         // valeur exacte de comments
     *     inAzura: boolean,      // true si présent dans AzuraCast
     *     state: string|null,   // 'PUSHED', 'ACTIVE', 'RETIRED', 'REMOVED' ou null
     *     stations: number[],   // [6], [7], [6,7] ou []
     *     legacy: boolean       // true si ancien format 'IN_AZURACAST'
     *   }
     */
    function getState(comments) {
        if (!comments) {
            return { flag: '', inAzura: false, state: null, stations: [], legacy: false };
        }
        var s = String(comments).trim();

        // Ancien format
        if (s === 'IN_AZURACAST') {
            return {
                flag: s,
                inAzura: true,
                state: 'PUSHED',  // approximation
                stations: [6, 7], // on suppose les deux stations
                legacy: true
            };
        }

        // Nouveau format : AZURA_<STATE>_S6_S7 etc.
        if (s.indexOf('AZURA_') !== 0) {
            return { flag: s, inAzura: false, state: null, stations: [], legacy: false };
        }

        var parts = s.split('_');
        if (parts.length < 3) {
            return { flag: s, inAzura: false, state: null, stations: [], legacy: false };
        }

        var state = parts[1];
        var stationsStr = parts.slice(2).join('_');

        var stations = [];
        if (stationsStr.indexOf('S6') !== -1) stations.push(6);
        if (stationsStr.indexOf('S7') !== -1) stations.push(7);

        var inAzura = (state === 'PUSHED' || state === 'ACTIVE' || state === 'RETIRED');

        return {
            flag: s,
            inAzura: inAzura,
            state: state,
            stations: stations,
            legacy: false
        };
    }

    /**
     * Retourne le HTML du badge à afficher selon l'état.
     * Badge cohérent avec le système de couleurs défini dans style.css.
     */
    function getBadgeHTML(comments) {
        var st = getState(comments);
        if (!st.flag) return '';
        if (st.legacy) {
            return '<span class="badge-azura" title="Présent dans AzuraCast (format legacy)">IN_AZURACAST</span>';
        }

        var labels = {
            'PUSHED':  { text: 'POUSSÉ',   cls: 'badge-azura badge-azura-push',    desc: 'Poussé vers AzuraCast — pas encore en playlist' },
            'ACTIVE':  { text: 'ACTIF',    cls: 'badge-azura badge-azura-active',  desc: 'Actif en playlist AzuraCast' },
            'RETIRED': { text: 'RETRAIT',  cls: 'badge-azura badge-azura-retired', desc: 'Retiré des playlists AzuraCast — fichier conservé' },
            'REMOVED': { text: 'SUPPRIMÉ', cls: 'badge-azura badge-azura-removed', desc: 'Supprimé d\'AzuraCast' }
        };

        var info = labels[st.state];
        if (!info) return '';

        var stationsTxt = st.stations.map(function(s) { return 'S' + s; }).join('+');
        var stationsLabel = st.stations.length > 1 
            ? 'stations ' + st.stations.join(' et ')
            : 'station ' + st.stations[0];
        var titleText = info.desc + ' (' + stationsLabel + ')';
        
        return '<span class="' + info.cls + '" title="' + _escapeHtml(titleText) + '">' +
               info.text + ' ' + stationsTxt + '</span>';
    }

    function _escapeHtml(text) {
        if (!text) return '';
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    /**
     * Construit un flag AZURA_*_S* à partir d'un état et d'une liste de stations.
     *
     * Args:
     *   state: 'PUSHED', 'ACTIVE', 'RETIRED', 'REMOVED'
     *   stations: liste de station IDs (ex: [6, 7])
     *
     * Returns: 'AZURA_ACTIVE_S6_S7' (stations triées et uniques)
     */
    function formatFlag(state, stations) {
        var validStates = ['PUSHED', 'ACTIVE', 'RETIRED', 'REMOVED'];
        if (validStates.indexOf(state) === -1) {
            console.error('[AzuraFlags] État invalide:', state);
            return '';
        }

        // Trier et dédoublonner
        var sorted = stations.slice().sort().filter(function(v, i, a) {
            return a.indexOf(v) === i;
        });
        var stationsStr = sorted.map(function(s) { return 'S' + s; }).join('_');
        return 'AZURA_' + state + '_' + stationsStr;
    }

    /**
     * Retourne le flag par défaut à utiliser après un push réussi.
     * Phase 0 (migration) : on utilise AZURA_PUSHED_S* (prudent).
     */
    function getDefaultFlagAfterPush(stationId) {
        if (stationId !== 6 && stationId !== 7) {
            console.error('[AzuraFlags] Station ID invalide:', stationId);
            return '';
        }
        return 'AZURA_PUSHED_S' + stationId;
    }

    return {
        isInAzuracast: isInAzuracast,
        getState: getState,
        getBadgeHTML: getBadgeHTML,
        formatFlag: formatFlag,
        getDefaultFlagAfterPush: getDefaultFlagAfterPush
    };
})();
