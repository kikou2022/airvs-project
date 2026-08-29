/* ========================================================================== */
/*  MODULE DE GESTION DE LA PROMOTION DES PODCASTS - AIRVS DASHBOARD          */
/* ========================================================================== */

const AIRVS_PODCAST_PROMO = {
    modalInstance: null,

    /**
     * Formate une date (ISO, string SQL/Timestamp ou nom de fichier pige)
     * au format lisible français : JJ/MM/AAAA à HH:mm
     */
    formatDateFR: function (rawDate) {
        if (!rawDate) return 'ND';

        // Gestion si la date provient d'un nom de fichier pige (ex: PIGE_2026-07-21_18-00.mp3)
        if (typeof rawDate === 'string' && rawDate.includes('PIGE_')) {
            const match = rawDate.match(/(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})/);
            if (match) {
                const [, datePart, hh, mm] = match;
                const [yyyy, month, dd] = datePart.split('-');
                return `${dd}/${month}/${yyyy} à ${hh}:${mm}`;
            }
        }

        // Nettoyage de la chaîne SQL "YYYY-MM-DD HH:mm:ss" vers format ISO "YYYY-MM-DDTHH:mm:ss"
        const cleanStr = (typeof rawDate === 'string') ? rawDate.replace(' ', 'T') : rawDate;
        const d = new Date(cleanStr);

        // Fallback si la date est invalide
        if (isNaN(d.getTime())) return rawDate;

        const dateFormatted = d.toLocaleDateString('fr-FR', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric'
        });

        const timeFormatted = d.toLocaleTimeString('fr-FR', {
            hour: '2-digit',
            minute: '2-digit'
        });

        return `${dateFormatted} à ${timeFormatted}`;
    },

    /**
     * Initialisation du module au chargement de la page
     */
    init: function () {
        console.log("📻 [AIRVS] Initialisation du module Podcast Promotion...");
        
        // Initialiser la modale Bootstrap si le DOM est prêt
        const modalEl = document.getElementById('modalPodcastPromo');
        if (modalEl) {
            this.modalInstance = new bootstrap.Modal(modalEl);
        }

        // Pré-remplir les dates par défaut (Début : Maintenant, Fin : +7 Jours)
        this.resetFormDates();

        // Charger les campagnes actives au démarrage
        this.loadPromotionsList();
    },

    /**
     * Réinitialise les dates par défaut dans le formulaire
     */
    resetFormDates: function () {
        const now = new Date();
        const nextWeek = new Date();
        nextWeek.setDate(now.getDate() + 7);

        const formatDate = (d) => d.toISOString().slice(0, 16);

        const startEl = document.getElementById('promoStartDate');
        const endEl = document.getElementById('promoEndDate');

        if (startEl) startEl.value = formatDate(now);
        if (endEl) endEl.value = formatDate(nextWeek);
    },

    /**
     * Ouvre la modale et charge la liste des podcasts disponibles depuis le SQL/Flask
     */
    openModal: function () {
        this.resetFormDates();
        this.loadAvailablePodcasts();
        if (this.modalInstance) {
            this.modalInstance.show();
        }
    },

    /**
     * Requête AJAX pour récupérer la liste des podcasts enregistrés dans la BDD locale
     */
    loadAvailablePodcasts: function () {
        const selectEl = document.getElementById('promoPodcastSelect');
        if (!selectEl) return;

        selectEl.innerHTML = '<option value="">Chargement en cours...</option>';

        fetch('/api/podcasts/list')
            .then(res => res.json())
            .then(data => {
                selectEl.innerHTML = '<option value="">-- Choisir un podcast --</option>';
                if (data.status === 'success' && Array.isArray(data.podcasts)) {
                    data.podcasts.forEach(podcast => {
                        const opt = document.createElement('option');
                        opt.value = podcast.id;
                        
                        // Formatage de la date du podcast dans le sélecteur
                        const formattedDate = this.formatDateFR(podcast.date);
                        opt.textContent = `[${podcast.emission || 'Podcast'}] ${podcast.title} (${formattedDate})`;
                        opt.dataset.jingleUrl = podcast.jingle_url || '';
                        selectEl.appendChild(opt);
                    });
                } else {
                    selectEl.innerHTML = '<option value="">Aucun podcast disponible</option>';
                }
            })
            .catch(err => {
                console.error("Erreur lors de la récupération des podcasts:", err);
                selectEl.innerHTML = '<option value="">Erreur de chargement</option>';
                showNotification("Impossible de charger la liste des podcasts", "danger");
            });
    },

    /**
     * Callback déclenché au changement de sélection de podcast
     */
    onSelectChange: function () {
        const selectEl = document.getElementById('promoPodcastSelect');
        const selectedOption = selectEl.options[selectEl.selectedIndex];
        const jingleUrl = selectedOption ? selectedOption.dataset.jingleUrl : null;

        const audioSelect = document.getElementById('promoAudioSelect');
        if (audioSelect && jingleUrl) {
            // Ajouter dynamiquement le jingle spécifique si disponible
            let opt = audioSelect.querySelector('option[data-custom="true"]');
            if (!opt) {
                opt = document.createElement('option');
                opt.dataset.custom = "true";
                audioSelect.appendChild(opt);
            }
            opt.value = jingleUrl;
            opt.textContent = `Spot dédié : ${selectedOption.textContent.substring(0, 30)}...`;
            audioSelect.value = jingleUrl;
        }
    },

    /**
     * Soumission du formulaire de promotion vers le backend Flask
     */
    submitPromo: function (event) {
        event.preventDefault();

        const btnSubmit = document.getElementById('btnSubmitPromo');
        const originalBtnHtml = btnSubmit ? btnSubmit.innerHTML : '';
        if (btnSubmit) {
            btnSubmit.disabled = true;
            btnSubmit.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Programmation...';
        }

        const payload = {
            podcast_id: document.getElementById('promoPodcastSelect').value,
            promo_type: document.getElementById('promoType').value,
            audio_target: document.getElementById('promoAudioSelect').value,
            start_date: document.getElementById('promoStartDate').value,
            end_date: document.getElementById('promoEndDate').value,
            interval_minutes: parseInt(document.getElementById('promoInterval').value, 10),
            playlist_target: document.getElementById('promoPlaylistTarget').value,
            priority: parseInt(document.getElementById('promoPriorityRange').value, 10)
        };

        fetch('/api/podcasts/promote', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                showNotification("Promotion programmée et synchronisée avec AzuraCast !", "success");
                if (this.modalInstance) this.modalInstance.hide();
                this.loadPromotionsList();
            } else {
                showNotification("Erreur : " + (data.message || "Échec de la programmation"), "danger");
            }
        })
        .catch(err => {
            console.error("Erreur lors de la programmation de la promotion:", err);
            showNotification("Erreur réseau ou serveur Flask inaccessible", "danger");
        })
        .finally(() => {
            if (btnSubmit) {
                btnSubmit.disabled = false;
                btnSubmit.innerHTML = originalBtnHtml;
            }
        });
    },

    /**
     * Récupère et affiche la liste des promotions actives dans le tableau
     */
    loadPromotionsList: function () {
        const tbody = document.getElementById('tbodyPodcastPromotions');
        if (!tbody) return;

        fetch('/api/podcasts/promotions')
            .then(res => res.json())
            .then(data => {
                tbody.innerHTML = '';
                if (data.status === 'success' && Array.isArray(data.promotions) && data.promotions.length > 0) {
                    data.promotions.forEach(promo => {
                        const tr = document.createElement('tr');
                        tr.innerHTML = `
                            <td>
                                <strong class="text-light">${escapeHtml(promo.podcast_title)}</strong>
                                <br><small class="text-muted">${escapeHtml(promo.emission_name || 'Émission')}</small>
                            </td>
                            <td><span class="badge bg-secondary">${escapeHtml(promo.promo_type)}</span></td>
                            <td>
                                <small class="text-info">${this.formatDateFR(promo.start_date)}</small><br>
                                <small class="text-muted">au ${this.formatDateFR(promo.end_date)}</small>
                            </td>
                            <td>Toutes les ${promo.interval_minutes} min</td>
                            <td>
                                <span class="badge bg-${promo.active ? 'success' : 'warning'}">
                                    ${promo.active ? 'Actif' : 'En pause'}
                                </span>
                            </td>
                            <td class="text-end">
                                <button class="btn btn-sm btn-outline-warning me-1" onclick="AIRVS_PODCAST_PROMO.toggleStatus(${promo.id}, ${!promo.active})">
                                    <i class="bi bi-${promo.active ? 'pause-fill' : 'play-fill'}"></i>
                                </button>
                                <button class="btn btn-sm btn-outline-danger" onclick="AIRVS_PODCAST_PROMO.deletePromo(${promo.id})">
                                    <i class="bi bi-trash-fill"></i>
                                </button>
                            </td>
                        `;
                        tbody.appendChild(tr);
                    });
                } else {
                    tbody.innerHTML = `
                        <tr>
                            <td colspan="6" class="text-center text-muted py-3">
                                <i class="bi bi-inbox me-2"></i>Aucune promotion de podcast en cours.
                            </td>
                        </tr>`;
                }
            })
            .catch(err => {
                console.error("Erreur chargement des promotions:", err);
                tbody.innerHTML = `<tr><td colspan="6" class="text-center text-danger py-3">Erreur de chargement des promotions</td></tr>`;
            });
    },

    /**
     * Activer / Mettre en pause une promotion
     */
    toggleStatus: function (promoId, newState) {
        fetch(`/api/podcasts/promotions/${promoId}/toggle`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ active: newState })
        })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                showNotification(`Promotion ${newState ? 'activée' : 'mise en pause'}`, "info");
                this.loadPromotionsList();
            }
        })
        .catch(err => console.error("Erreur bascule statut promo:", err));
    },

    /**
     * Supprimer une promotion
     */
    deletePromo: function (promoId) {
        if (!confirm("Voulez-vous vraiment supprimer cette campagne de promotion ?")) return;

        fetch(`/api/podcasts/promotions/${promoId}/delete`, { method: 'DELETE' })
            .then(res => res.json())
            .then(data => {
                if (data.status === 'success') {
                    showNotification("Promotion supprimée avec succès", "success");
                    this.loadPromotionsList();
                }
            })
            .catch(err => console.error("Erreur suppression promo:", err));
    }
};

// Fonctions utilitaires globales raccordées aux évènements HTML
function openPodcastPromoModal() {
    AIRVS_PODCAST_PROMO.openModal();
}

function onPodcastPromoSelectChange() {
    AIRVS_PODCAST_PROMO.onSelectChange();
}

function submitPodcastPromo(event) {
    AIRVS_PODCAST_PROMO.submitPromo(event);
}

function refreshPodcastPromotions() {
    AIRVS_PODCAST_PROMO.loadPromotionsList();
}

// Sécurisation basique des chaînes HTML contre les injections XSS
function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// Auto-initialisation au chargement de la page
document.addEventListener('DOMContentLoaded', function () {
    AIRVS_PODCAST_PROMO.init();
});