// [P3-S4] Phase 3 Étape 4 — Extraction JS stats-audience
/**
 * AIRVS Dashboard — stats-audience
 * Extrait de index.html (Phase 3, étape 4)
 * Onglet Audience : stats agrégées temps réel (Icecast + Azuracast #1 + #2),
 * historique graphique/tableau, records.
 */

(function audienceModule() {
    'use strict';

    let audienceChart = null;
    let audienceLoaded = false;
    let audienceRefreshTimer = null;

    async function fetchAudienceRealtime() {
        try {
            const r = await fetch('/api/audience/realtime');
            const data = await r.json();
            if (data.error) { console.warn('Audience realtime:', data.error); return; }
            updateAudienceRealtime(data);
        } catch (e) {
            console.error('Erreur fetch audience realtime:', e);
        }
    }

    function updateAudienceRealtime(data) {
        const latest = data.latest || {};
        document.getElementById('audience-total').textContent = latest.total_listeners ?? '—';

        if (data.last_poll) {
            const d = new Date(data.last_poll);
            document.getElementById('audience-last-update').textContent =
                'Mis à jour : ' + d.toLocaleTimeString('fr-FR');
        }

        document.getElementById('audience-icecast').textContent = latest.icecast_total_listeners ?? '—';

        document.getElementById('audience-az1').textContent = latest.azuracast1_listeners ?? '—';
        document.getElementById('audience-az1-status').innerHTML = latest.azuracast1_listeners > 0
            ? '<span class="text-success">● Actif</span>'
            : '<span class="text-danger">● Inactif</span>';
        document.getElementById('audience-az1-now-playing').textContent = latest.azuracast1_now_playing || '';

        document.getElementById('audience-az2').textContent = latest.azuracast2_listeners ?? '—';
        document.getElementById('audience-az2-status').innerHTML = latest.azuracast2_listeners > 0
            ? '<span class="text-success">● Actif</span>'
            : '<span class="text-danger">● Inactif</span>';
        document.getElementById('audience-az2-now-playing').textContent = latest.azuracast2_now_playing || '';

        const records = data.records || [];
        const recordsBox = document.getElementById('audience-records');
        if (records.length > 0) {
            recordsBox.innerHTML = records.slice(0, 5).map(r => {
                const d = new Date(r.record_date);
                return `<div><strong>${r.total_listeners}</strong> auditeurs — ${d.toLocaleDateString('fr-FR')} ${d.toLocaleTimeString('fr-FR')}</div>`;
            }).join('');
        } else {
            recordsBox.innerHTML = '<em class="text-muted">Aucun record enregistré pour le moment</em>';
        }
    }

    async function fetchAudienceHistory(hours) {
        try {
            const r = await fetch(`/api/audience/history?hours=${hours}`);
            const data = await r.json();
            if (data.error) { console.warn('Audience history:', data.error); return; }
            renderAudienceHistory(data.points || []);
        } catch (e) {
            console.error('Erreur fetch audience history:', e);
        }
    }

    function renderAudienceHistory(points) {
        // Chart.js n'est pas vendorisé dans ce projet (pas de CDN) — si tu veux
        // le graphique, télécharge chart.js (+ chartjs-adapter-date-fns) dans
        // static/js/ et ajoute les <script> correspondants dans <head>, puis
        // décommente le bloc ci-dessous. En attendant, repli sur un tableau simple.
        if (typeof Chart === 'undefined') {
            renderAudienceHistoryTable(points);
            return;
        }
        const ctx = document.getElementById('audienceHistoryChart').getContext('2d');
        const labels = points.map(p => new Date(p.timestamp));
        if (audienceChart) audienceChart.destroy();
        audienceChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    { label: 'Icecast', data: points.map(p => p.icecast_total_listeners), borderColor: '#3b82f6', tension: 0.3, pointRadius: 0 },
                    { label: 'Azuracast #1', data: points.map(p => p.azuracast1_listeners), borderColor: '#10b981', tension: 0.3, pointRadius: 0 },
                    { label: 'Azuracast #2', data: points.map(p => p.azuracast2_listeners), borderColor: '#f59e0b', tension: 0.3, pointRadius: 0 },
                    { label: 'Total', data: points.map(p => p.total_listeners), borderColor: '#000', borderWidth: 3, tension: 0.3, pointRadius: 0 }
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                scales: { x: { type: 'time', time: { displayFormats: { hour: 'HH:mm', day: 'dd/MM' } } }, y: { beginAtZero: true } },
                plugins: { legend: { position: 'top' } }
            }
        });
    }

    function renderAudienceHistoryTable(points) {
        const container = document.getElementById('audienceHistoryChart').parentElement;
        const derniers = points.slice(-20).reverse();
        if (derniers.length === 0) {
            container.innerHTML = '<em class="text-muted">Pas encore de données historiques</em>';
            return;
        }
        let html = '<table class="table table-sm"><thead><tr>' +
            '<th>Heure</th><th>Icecast</th><th>Azuracast #1</th><th>Azuracast #2</th><th>Total</th>' +
            '</tr></thead><tbody>';
        derniers.forEach(p => {
            const d = new Date(p.timestamp);
            html += `<tr><td>${d.toLocaleString('fr-FR')}</td><td>${p.icecast_total_listeners}</td>` +
                `<td>${p.azuracast1_listeners}</td><td>${p.azuracast2_listeners}</td><td><strong>${p.total_listeners}</strong></td></tr>`;
        });
        html += '</tbody></table>';
        container.innerHTML = html;
    }

    document.querySelectorAll('.audience-hist-tab').forEach(function(tab) {
        tab.addEventListener('click', function() {
            document.querySelectorAll('.audience-hist-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            fetchAudienceHistory(parseInt(tab.dataset.hours));
        });
    });

    document.querySelectorAll('button[data-bs-toggle="tab"], .sb-link[data-target]').forEach(function(tab) {
        tab.addEventListener('shown.bs.tab', function(e) {
            if (e.target.id === 'nav-audience-tab') {
                fetchAudienceRealtime();
                fetchAudienceHistory(24);
                if (!audienceLoaded) {
                    audienceLoaded = true;
                    audienceRefreshTimer = setInterval(fetchAudienceRealtime, 30000);
                }
            } else if (audienceRefreshTimer) {
                // Bug corrigé : ce timer tournait indéfiniment même après avoir
                // quitté l'onglet Audience, empilant une tâche AUDIENCE_GET_DATA
                // toutes les 30s dans la file du worker (qui traite tout en
                // séquentiel) — ça pouvait faire expirer d'autres requêtes
                // (ex: PIGE_LIST) bloquées derrière cette file.
                clearInterval(audienceRefreshTimer);
                audienceRefreshTimer = null;
                audienceLoaded = false;
            }
        });
    });
})();
