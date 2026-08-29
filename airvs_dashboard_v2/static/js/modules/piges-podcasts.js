// [P3-S5] Phase 3 Étape 5 — Extraction JS piges-podcasts (piges, lectures, promotion podcast)
/**
 * AIRVS Dashboard - piges-podcasts
 * Extrait de index.html (Phase 3, etape 5b)
 * Onglet Piges & Podcasts.
 */

// ════════════════════════════════════════════════════════════════
// ONGLET PIGES & PODCASTS — Évolution 1, Phase 1B
// ════════════════════════════════════════════════════════════════
(function pigesModule() {
    'use strict';

    // Base publique de streaming des piges (VPS OVH 1, accessible directement
    // depuis le navigateur du client — le dashboard Flask n'a pas d'accès Internet).
    const PIGE_HTTP_BASE = 'https://pige.airvs.fr';

    let pigesDatesLoaded = false;

    function formatDuration(sec) {
        if (sec === null || sec === undefined) return '—';
        const h = Math.floor(sec / 3600);
        const m = Math.floor((sec % 3600) / 60);
        const s = Math.floor(sec % 60);
        return h > 0
            ? `${h}h${String(m).padStart(2, '0')}m${String(s).padStart(2, '0')}s`
            : `${m}m${String(s).padStart(2, '0')}s`;
    }

    function extraireHeure(filename) {
        // Format observé : "10h00_00_+0100.mp3" -> "10:00"
        const m = filename.match(/^(\d{1,2})h(\d{2})/);
        return m ? `${m[1].padStart(2, '0')}:${m[2]}` : filename;
    }

    async function chargerDates() {
        try {
            const r = await fetch('/api/piges/dates');
            const data = await r.json();
            const select = document.getElementById('piges-date-select');
            select.innerHTML = '';
            (data.dates || []).forEach((d, i) => {
                const opt = document.createElement('option');
                opt.value = d;
                opt.textContent = i === 0 ? `${d} (aujourd'hui)` : d;
                select.appendChild(opt);
            });
            if (data.dates && data.dates.length > 0) {
                chargerFichiers(data.dates[0]);
            }
        } catch (e) {
            console.error('Erreur chargement dates piges:', e);
        }
    }

    async function chargerFichiers(dateStr) {
        const tbody = document.getElementById('piges-files-tbody');
        const statusEl = document.getElementById('piges-list-status');
        tbody.innerHTML = '<tr><td colspan="5" class="text-muted">Chargement...</td></tr>';
        statusEl.textContent = '';

        try {
            const r = await fetch(`/api/piges/list/${encodeURIComponent(dateStr)}`);
            const data = await r.json();

            if (data.error) {
                tbody.innerHTML = `<tr><td colspan="5" class="text-danger">Erreur : ${data.error}</td></tr>`;
                return;
            }

            const files = data.files || [];
            if (files.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" class="text-muted">Aucun fichier pour cette date.</td></tr>';
                statusEl.textContent = '';
                return;
            }

            statusEl.textContent = `${data.total_files} fichier(s) — ${data.total_size_human || data.total_size || ''} au total`;

            tbody.innerHTML = files.map(f => `
                <tr>
                    <td>${extraireHeure(f.name)}</td>
                    <td><code class="small">${f.name}</code></td>
                    <td>${f.size_human || f.size || '—'}</td>
                    <td>${formatDuration(f.duration_sec)}</td>
                    <td>
                        <button class="btn btn-sm btn-outline-primary btn-piges-ecouter"
                                data-date="${dateStr}" data-file="${f.name}">▶ Écouter</button>
                        <button class="btn btn-sm btn-outline-success btn-piges-promouvoir"
                                data-date="${dateStr}" data-file="${f.name}">🎙️ Promouvoir</button>
                    </td>
                </tr>
            `).join('');

            tbody.querySelectorAll('.btn-piges-ecouter').forEach(btn => {
                btn.addEventListener('click', function() {
                    const d = btn.dataset.date;
                    const f = btn.dataset.file;
                    const url = `${PIGE_HTTP_BASE}/${d}/${f}`;
                    const card = document.getElementById('piges-player-card');
                    const audio = document.getElementById('piges-player-audio');
                    document.getElementById('piges-player-title').textContent = `${f} — ${d}`;
                    audio.src = url;
                    card.style.display = '';
                    audio.play().catch(() => {});
                });
            });

            tbody.querySelectorAll('.btn-piges-promouvoir').forEach(btn => {
                btn.addEventListener('click', function() {
                    ouvrirModalPromotion(btn.dataset.date, btn.dataset.file);
                });
            });
        } catch (e) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-danger">Erreur de chargement.</td></tr>';
            console.error('Erreur chargement fichiers piges:', e);
        }
    }

    // ─── Phase 1E : Modal de promotion ───
    let pp_currentDate = null;
    let pp_currentFile = null;
    let pp_artworkWindowsPath = null;
    let pp_podcastsLoaded = false;
    let pp_statusPollTimer = null;

    async function chargerPodcastsSiBesoin() {
        if (pp_podcastsLoaded) return;
        const select = document.getElementById('pp-podcast-select');
        try {
            const r = await fetch('/api/podcasts/list');
            const data = await r.json();
            if (data.error) {
                select.innerHTML = `<option value="">Erreur : ${data.error}</option>`;
                return;
            }
            const podcasts = data.podcasts || [];
            select.innerHTML = podcasts.length
                ? podcasts.map(p => `<option value="${p.id}">${p.title || p.name || ('Podcast #' + p.id)}</option>`).join('')
                : '<option value="">Aucun podcast trouvé sur Azuracast #2</option>';
            pp_podcastsLoaded = true;
        } catch (e) {
            select.innerHTML = '<option value="">Erreur de chargement des podcasts</option>';
            console.error('Erreur chargement podcasts:', e);
        }
    }

    function ouvrirModalPromotion(dateStr, fileName) {
        pp_currentDate = dateStr;
        pp_currentFile = fileName;
        pp_artworkWindowsPath = null;

        document.getElementById('pp-source-label').textContent = `${dateStr}/${fileName}`;
        document.getElementById('pp-extract-enabled').checked = false;
        document.getElementById('pp-extract-fields').style.display = 'none';
        document.getElementById('pp-extract-start').value = '00:00:00';
        document.getElementById('pp-extract-duration').value = '00:10:00';
        document.getElementById('pp-title').value = '';
        document.getElementById('pp-description').value = '';
        document.getElementById('pp-episode-number').value = '';
        document.getElementById('pp-season-number').value = '';
        document.getElementById('pp-category').value = 'Music';
        document.getElementById('pp-explicit').checked = false;
        document.getElementById('pp-artwork-file').value = '';
        document.getElementById('pp-artwork-preview').style.display = 'none';
        document.getElementById('pp-submit-status').innerHTML = '';

        // Date de publication par défaut : maintenant
        const now = new Date();
        now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
        document.getElementById('pp-publish-date').value = now.toISOString().slice(0, 16);

        chargerPodcastsSiBesoin();

        const modalEl = document.getElementById('pigesPromoteModal');
        bootstrap.Modal.getOrCreateInstance(modalEl).show();
    }

    document.getElementById('pp-extract-enabled').addEventListener('change', function() {
        document.getElementById('pp-extract-fields').style.display = this.checked ? '' : 'none';
    });

    document.getElementById('pp-artwork-file').addEventListener('change', async function() {
        const file = this.files[0];
        const preview = document.getElementById('pp-artwork-preview');
        const statusEl = document.getElementById('pp-submit-status');
        if (!file) { preview.style.display = 'none'; return; }

        if (file.size > 2 * 1024 * 1024) {
            statusEl.innerHTML = '<span class="text-danger">Image trop lourde (max 2 Mo)</span>';
            this.value = '';
            return;
        }

        preview.src = URL.createObjectURL(file);
        preview.style.display = '';

        const formData = new FormData();
        formData.append('file', file);
        statusEl.innerHTML = '<span class="text-muted">Upload de l\'artwork...</span>';
        try {
            const r = await fetch('/api/piges/upload_artwork', { method: 'POST', body: formData });
            const data = await r.json();
            if (data.error) {
                statusEl.innerHTML = `<span class="text-danger">Erreur artwork : ${data.error}</span>`;
                pp_artworkWindowsPath = null;
            } else {
                pp_artworkWindowsPath = data.artwork_windows_path;
                statusEl.innerHTML = '<span class="text-success">Artwork prêt ✓</span>';
            }
        } catch (e) {
            statusEl.innerHTML = '<span class="text-danger">Erreur upload artwork</span>';
            console.error(e);
        }
    });

    document.getElementById('pp-submit-btn').addEventListener('click', async function() {
        const statusEl = document.getElementById('pp-submit-status');
        const title = document.getElementById('pp-title').value.trim();
        const podcastId = document.getElementById('pp-podcast-select').value;
        const publishDate = document.getElementById('pp-publish-date').value;

        if (!title) { statusEl.innerHTML = '<span class="text-danger">Le titre est obligatoire</span>'; return; }
        if (!podcastId) { statusEl.innerHTML = '<span class="text-danger">Sélectionne un podcast</span>'; return; }
        if (!publishDate) { statusEl.innerHTML = '<span class="text-danger">Date de publication requise</span>'; return; }

        // Le <input type="datetime-local"> renvoie une chaîne SANS fuseau,
        // que le navigateur interprète comme l'heure LOCALE de l'utilisateur.
        // new Date(str) la parse donc correctement en heure locale, et
        // toISOString() la convertit en UTC réel — sans ce passage, la
        // valeur était envoyée telle quelle et traitée comme si elle était
        // déjà en UTC côté serveur, avec un décalage égal au fuseau horaire
        // (l'épisode restait "non publié" car publish_at semblait futur).
        const publishDateUtcIso = new Date(publishDate).toISOString();

        const payload = {
            source_date: pp_currentDate,
            source_file: pp_currentFile,
            podcast_id: podcastId,
            title: title,
            description: document.getElementById('pp-description').value,
            episode_number: document.getElementById('pp-episode-number').value
                ? parseInt(document.getElementById('pp-episode-number').value, 10) : null,
            season_number: document.getElementById('pp-season-number').value
                ? parseInt(document.getElementById('pp-season-number').value, 10) : null,
            publish_date: publishDateUtcIso,
            category: document.getElementById('pp-category').value,
            explicit: document.getElementById('pp-explicit').checked,
            extract: {
                enabled: document.getElementById('pp-extract-enabled').checked,
                start_time: document.getElementById('pp-extract-start').value,
                duration: document.getElementById('pp-extract-duration').value,
            },
            artwork_windows_path: pp_artworkWindowsPath,
        };

        this.disabled = true;
        statusEl.innerHTML = '<span class="text-muted">Envoi de la demande de promotion...</span>';

        try {
            const r = await fetch('/api/piges/promote', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await r.json();
            if (data.error) {
                statusEl.innerHTML = `<span class="text-danger">Erreur : ${data.error}</span>`;
                this.disabled = false;
                return;
            }
            statusEl.innerHTML = '<span class="text-info">⏳ Promotion en cours (upload MP3, création épisode)...</span>';
            pollStatutPromotion(data.task_id);
            chargerPromotionsRecentes();
        } catch (e) {
            statusEl.innerHTML = '<span class="text-danger">Erreur réseau lors de la promotion</span>';
            console.error(e);
            this.disabled = false;
        }
    });

    function pollStatutPromotion(taskId) {
        if (pp_statusPollTimer) clearInterval(pp_statusPollTimer);
        const statusEl = document.getElementById('pp-submit-status');
        const submitBtn = document.getElementById('pp-submit-btn');
        let tentatives = 0;

        pp_statusPollTimer = setInterval(async () => {
            tentatives++;
            if (tentatives > 100) {  // ~5 min à 3s d'intervalle
                clearInterval(pp_statusPollTimer);
                statusEl.innerHTML = '<span class="text-warning">Toujours en cours après 5 min — vérifie les promotions récentes plus tard.</span>';
                submitBtn.disabled = false;
                return;
            }
            try {
                const r = await fetch(`/api/piges/promote/status/${taskId}`);
                const data = await r.json();
                if (data.error) return;

                if (data.status === 'termine') {
                    clearInterval(pp_statusPollTimer);
                    statusEl.innerHTML = `<span class="text-success">✓ Publié : <a href="${data.episode_url}" target="_blank">${data.episode_url}</a></span>`;
                    submitBtn.disabled = false;
                    chargerPromotionsRecentes();
                    // Fermeture auto après un court délai, pour laisser le temps
                    // de lire/cliquer le lien avant que le modal ne disparaisse.
                    setTimeout(() => {
                        const modalEl = document.getElementById('pigesPromoteModal');
                        const instance = bootstrap.Modal.getOrCreateInstance(modalEl);
                        instance.hide();
                    }, 3000);
                } else if (data.status === 'erreur') {
                    clearInterval(pp_statusPollTimer);
                    statusEl.innerHTML = `<span class="text-danger">✗ Échec : ${data.error_message || 'erreur inconnue'}</span>`;
                    submitBtn.disabled = false;
                    chargerPromotionsRecentes();
                }
                // sinon (en_attente / en_cours) : on continue de poller silencieusement
            } catch (e) {
                console.error('Erreur poll statut promotion:', e);
            }
        }, 3000);
    }

    async function chargerPromotionsRecentes() {
        const container = document.getElementById('piges-promotions-recent');
        try {
            const r = await fetch('/api/piges/promotions/recent?limit=10');
            const data = await r.json();
            const promos = data.promotions || [];
            if (promos.length === 0) {
                container.innerHTML = '<span class="text-muted">Aucune promotion pour le moment.</span>';
                return;
            }
            const icones = { termine: '✓', erreur: '✗', en_cours: '⏳', en_attente: '⏳' };
            const couleurs = { termine: 'text-success', erreur: 'text-danger', en_cours: 'text-info', en_attente: 'text-muted' };
            const supprimable = { termine: true, erreur: true, en_cours: false, en_attente: false };
            container.innerHTML = promos.map(p => {
                const icone = icones[p.status] || '•';
                const couleur = couleurs[p.status] || '';
                const date = p.created_at ? new Date(p.created_at).toLocaleString('fr-FR') : '';
                let ligne = `<div class="${couleur} d-flex justify-content-between align-items-start py-1">`;
                ligne += `<div>${date} ${icone} "${p.title}"`;
                if (p.status === 'termine' && p.episode_url) {
                    ligne += ` — <a href="${p.episode_url}" target="_blank">voir l'épisode</a>`;
                } else if (p.status === 'erreur' && p.error_message) {
                    ligne += ` — ${p.error_message}`;
                } else {
                    ligne += ' — en cours';
                }
                ligne += '</div>';
                if (supprimable[p.status]) {
                    ligne += `<button class="btn btn-sm btn-link text-muted p-0 ms-2 btn-piges-promo-delete" data-id="${p.id}" title="Supprimer cette entrée">🗑️</button>`;
                }
                ligne += '</div>';
                return ligne;
            }).join('');

            container.querySelectorAll('.btn-piges-promo-delete').forEach(btn => {
                btn.addEventListener('click', async function() {
                    if (!confirm('Supprimer cette entrée de l\'historique ? (n\'affecte pas l\'épisode déjà publié)')) return;
                    try {
                        const r = await fetch(`/api/piges/promotions/${btn.dataset.id}`, { method: 'DELETE' });
                        const data = await r.json();
                        if (data.error) { alert(`Erreur : ${data.error}`); return; }
                        chargerPromotionsRecentes();
                    } catch (e) {
                        console.error('Erreur suppression promotion:', e);
                    }
                });
            });
        } catch (e) {
            container.innerHTML = '<span class="text-danger">Erreur de chargement</span>';
            console.error('Erreur chargement promotions récentes:', e);
        }
    }

    document.getElementById('btn-piges-purge-errors').addEventListener('click', async function() {
        if (!confirm('Supprimer toutes les promotions en erreur de l\'historique ?')) return;
        try {
            const r = await fetch('/api/piges/promotions/purge_errors', { method: 'POST' });
            const data = await r.json();
            if (data.error) { alert(`Erreur : ${data.error}`); return; }
            chargerPromotionsRecentes();
        } catch (e) {
            console.error('Erreur purge promotions en erreur:', e);
        }
    });

    document.getElementById('piges-date-select').addEventListener('change', function() {
        chargerFichiers(this.value);
    });

    document.getElementById('btn-piges-refresh').addEventListener('click', function() {
        const select = document.getElementById('piges-date-select');
        if (select.value) chargerFichiers(select.value);
    });

    document.querySelectorAll('button[data-bs-toggle="tab"], .sb-link[data-target]').forEach(function(tab) {
        tab.addEventListener('shown.bs.tab', function(e) {
            if (e.target.id === 'nav-piges-tab' && !pigesDatesLoaded) {
                pigesDatesLoaded = true;
                chargerDates();
                chargerPromotionsRecentes();
            }
        });
    });
})();
