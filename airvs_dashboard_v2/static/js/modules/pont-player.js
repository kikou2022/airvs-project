// [P3-S3] Phase 3 Étape 3 — Extraction JS shazam-animateurs + pont-player
/**
 * pont-player.js — Module Pont AzuraCast / RadioDJ
 * Gère : contrôle du player service, archives M3U, flux actif,
 *         sélecteur de flux, spectre audio (Web Audio API),
 *         lecteur audio barre fixe.
 *
 * Aucune fonction exposée sur window : tout est interne (addEventListener).
 */
(function() {
    'use strict';

    // ── Références DOM ──
    var archiveContent = document.getElementById('flux_archive_content');
    var actifContent = document.getElementById('flux_actif_content');
    var archiveCount = document.getElementById('flux_archive_count');
    var actifCount = document.getElementById('flux_actif_count');
    var lecteurCard = document.getElementById('lecteur_audio_card');
    var audioPlayer = document.getElementById('global_audio_player');
    var lecteurTitre = document.getElementById('lecteur_titre');
    var lecteurClose = document.getElementById('lecteur_close');

    // ════════════════════════════════════
    // PONT AZURACAST / RADIODJ — PLAYER
    // ════════════════════════════════════

    // ── Status du player ──
    function fetchPlayerStatus() {
        fetch('/api/airvs_player/status?t=' + Date.now())
            .then(function(r) { return r.json(); })
            .then(function(data) {
                let dot = document.getElementById('player_status_dot');
                let txt = document.getElementById('player_status_text');
                let uptime = document.getElementById('player_uptime');
                let titreEncours = document.getElementById('player_titre_en_cours');

                if (data.erreur) {
                    dot.style.background = '#dc3545';
                    txt.textContent = 'Inaccessible';
                    uptime.textContent = data.erreur;
                    titreEncours.textContent = '—';
                } else if (data.service_actif) {
                    dot.style.background = '#198754';
                    txt.textContent = 'En lecture';
                    uptime.textContent = data.service_depuis ? 'Depuis : ' + data.service_depuis : '';
                    titreEncours.textContent = data.titre_en_cours || '(aucun titre détecté)';
                } else {
                    dot.style.background = '#ffc107';
                    txt.textContent = 'Arrêté';
                    uptime.textContent = data.service_statut || '';
                    titreEncours.textContent = '—';
                }
            })
            .catch(function() {
                document.getElementById('player_status_dot').style.background = '#6c757d';
                document.getElementById('player_status_text').textContent = 'Erreur SSH';
            });
    }

    // ── Journal du player ──
    function fetchPlayerJournal() {
        fetch('/api/airvs_player/journal?lignes=50&t=' + Date.now())
            .then(function(r) { return r.json(); })
            .then(function(data) {
                let terminal = document.getElementById('terminal_player_journal');
                // On affiche TOUJOURS data.journal même s'il est "vide" visuellement,
                // car le backend y met maintenant un message explicite quand stdout est vide.
                // Le fallback ci-dessous ne se déclenche que si data lui-même est cassé.
                let contenu = data.journal;
                if (!contenu) {
                    contenu = '[Réponse inattendue du serveur]\n' +
                              'status=' + (data.status || '?') + '\n' +
                              'Le backend n\'a renvoyé aucun champ "journal".';
                }
                // Préfixe visuel pour distinguer erreur / ok
                if (data.status === 'error') {
                    terminal.innerHTML = '<span style="color:#ff6b6b;">⚠ ' +
                                         contenu.replace(/</g, '&lt;').replace(/\n/g, '<br>') +
                                         '</span>';
                } else {
                    terminal.textContent = contenu;
                }
                terminal.scrollTop = terminal.scrollHeight;
            })
            .catch(function(err) {
                document.getElementById('terminal_player_journal').textContent =
                    'Erreur réseau lors de la récupération du journal : ' + err;
            });
    }

    document.getElementById('btn_player_journal_refresh').addEventListener('click', function() {
        document.getElementById('terminal_player_journal').textContent = 'Chargement...';
        fetchPlayerJournal();
    });

    // ── Boutons de contrôle du service ──
    function playerAction(action, btn) {
        let originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '⏳...';
        fetch('/api/airvs_player/' + action, { method: 'POST' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                btn.innerHTML = originalText;
                btn.disabled = false;
                // Petit délai avant de refresh le status
                setTimeout(function() {
                    fetchPlayerStatus();
                    fetchPlayerJournal();
                }, 1500);
                alert(data.message);
            })
            .catch(function() {
                btn.innerHTML = originalText;
                btn.disabled = false;
                alert("Erreur de communication avec Debian 3.");
            });
    }

    document.getElementById('btn_player_start').addEventListener('click', function() {
        playerAction('start', this);
    });
    document.getElementById('btn_player_stop').addEventListener('click', function() {
        if (confirm("Arrêter le player sur Debian 3 ?")) {
            playerAction('stop', this);
        }
    });
    document.getElementById('btn_player_restart').addEventListener('click', function() {
        if (confirm("Redémarrer le player sur Debian 3 ?")) {
            playerAction('restart', this);
        }
    });

    // ── Archives M3U ──
    function fetchArchivesList() {
        fetch('/api/airvs_archives?t=' + Date.now())
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.status !== 'ok') return;
                let sel = document.getElementById('archive_date_select');
                let html = '<option value="">-- Choisir une date (' + data.archives.length + ' archives) --</option>';
                data.archives.forEach(function(a) {
                    html += '<option value="' + a.date + '">' + a.date + ' (' + a.nb_titres + ' titres, ' + a.taille_ko + ' Ko)</option>';
                });
                sel.innerHTML = html;
                // Sélectionner la date du jour par défaut (heure locale, pas UTC)
                let now = new Date();
                let today = now.getFullYear() + '-'
                    + String(now.getMonth() + 1).padStart(2, '0') + '-'
                    + String(now.getDate()).padStart(2, '0');
                if (data.archives.some(function(a) { return a.date === today; })) {
                    sel.value = today;
                    loadArchiveContent(today);
                }
            });
    }

    function loadArchiveContent(dateStr) {
        if (!dateStr) {
            archiveContent.textContent = 'Sélectionnez une date...';
            archiveCount.textContent = '0 titres';
            return;
        }
        fetch('/api/airvs_archive/' + dateStr + '?t=' + Date.now())
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.status === 'ok') {
                    archiveContent.textContent = data.contenu || 'Archive vide.';
                    archiveContent.scrollTop = archiveContent.scrollHeight;
                    archiveCount.textContent = data.nb_titres + ' titres';
                } else {
                    archiveContent.textContent = data.message || 'Erreur';
                    archiveCount.textContent = '0 titres';
                }
            })
            .catch(function() {
                archiveContent.textContent = 'Erreur de chargement.';
            });
    }

    document.getElementById('archive_date_select').addEventListener('change', function() {
        loadArchiveContent(this.value);
    });

    document.getElementById('btn_archives_refresh').addEventListener('click', function() {
        fetchArchivesList();
    });

    // ── Bouton « Archive du jour » intelligent ──
    // Le fichier archive_{today}.m3u est généré par le service airvs-radio
    // (Debian 3) ou une tâche planifiée, pas par le dashboard.
    // Si le fichier n'existe pas encore, on affiche un message explicatif
    // au lieu de naviguer vers une page 404 brute.
    document.getElementById('btn_export_archive_today').addEventListener('click', function() {
        var btn = this;
        btn.disabled = true;
        var originalText = btn.innerHTML;
        btn.innerHTML = '⏳ Vérification…';

        fetch('/api/flux_status?t=' + Date.now())
            .then(function(r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function(data) {
                btn.disabled = false;
                btn.innerHTML = originalText;

                if (data.statut === 'ok' && data.nb_titres_archive > 0) {
                    // L'archive du jour existe, lancer le téléchargement
                    window.location.href = '/export_archive';
                } else {
                    // Archive du jour non encore générée
                    var msg = '⏳ Archive du jour non encore disponible.\n\n';
                    if (data.nb_titres_actif > 0) {
                        msg += 'Le flux actif contient ' + data.nb_titres_actif + ' titre(s).\n';
                        msg += 'L\'archive journalière est générée automatiquement\n';
                        msg += 'par le service airvs-radio (généralement en fin de journée).\n\n';
                        msg += '→ Utilisez le bouton « Flux actif .m3u » pour télécharger\n   le fichier live en attendant.';
                    } else {
                        msg += 'Aucun titre en archive ni en flux actif pour le moment.\n';
                        msg += 'Le service airvs-radio est peut-être arrêté.';
                    }
                    alert(msg);
                }
            })
            .catch(function() {
                btn.disabled = false;
                btn.innerHTML = originalText;
                alert('Erreur de communication avec le serveur.');
            });
    });

    // ── Flux actif (M3U live) ──
    function fetchFluxStatus() {
        fetch('/api/flux_status?t=' + Date.now())
            .then(function(res) { return res.json(); })
            .then(function(data) {
                if (data.statut === "ok") {
                    actifContent.textContent = data.actif || "Flux vide.";
                } else {
                    actifContent.textContent = data.message || "Erreur";
                }
                actifCount.textContent = data.nb_titres_actif + " titres dans la file RadioDJ";
            })
            .catch(function() {
                actifContent.textContent = "Impossible de contacter le pont.";
            });
    }

    // ════════════════════════════════════
    // SÉLECTEUR DE FLUX
    // ════════════════════════════════════

    let currentFluxConfig = null;

    // ── Charger la config des flux ──
    function fetchFluxConfig() {
        fetch('/api/flux_config?t=' + Date.now())
            .then(function(r) { return r.json(); })
            .then(function(data) {
                let sel = document.getElementById('flux_select');
                if (data.status !== 'ok') {
                    // Erreur (JSON corrompu ou autre)
                    sel.innerHTML = '<option value="">-- Erreur config --</option>';
                    console.error('Erreur flux_config :', data.message);
                    if (data.error_type === 'json_decode') {
                        alert('⚠️ Le fichier flux_radio.json local est invalide !\n\n'
                              + 'Erreur : ' + data.message + '\n\n'
                              + 'Suggestion : ' + (data.suggestion || ''));
                    }
                    return;
                }
                currentFluxConfig = data.config;
                let html = '';
                let fluxList = data.config.flux_disponibles || [];
                fluxList.forEach(function(f) {
                    let actif = f.id === data.config.flux_actif ? ' [ACTIF]' : '';
                    html += '<option value="' + f.id + '"' + (f.id === data.config.flux_actif ? ' selected' : '') + '>'
                            + f.nom + ' (' + (f.bitrate ? f.bitrate + 'k - ' : '') + f.codec + ')' + actif + '</option>';
                });
                if (fluxList.length === 0) {
                    html = '<option value="">-- Aucun flux configuré --</option>';
                    console.warn('Aucun flux dans flux_radio.json local. Ajoutez-en via le formulaire.');
                }
                sel.innerHTML = html;
                document.getElementById('btn_flux_switch').disabled = (fluxList.length === 0);
            })
            .catch(function(err) {
                console.error('Erreur fetchFluxConfig :', err);
            });
    }

    // ── Changer de flux actif ──
    document.getElementById('btn_flux_switch').addEventListener('click', function() {
        let sel = document.getElementById('flux_select');
        let newFluxId = sel.value;
        if (!newFluxId) return;
        if (currentFluxConfig && newFluxId === currentFluxConfig.flux_actif) {
            alert('Ce flux est déjà actif.');
            return;
        }
        if (!confirm('Changer le flux du player vers : ' + sel.options[sel.selectedIndex].text + ' ?\nLe service sera redémarré.')) return;
        let btn = this;
        btn.disabled = true;
        btn.innerHTML = '⏳ Changement...';
        fetch('/api/flux_config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ flux_actif: newFluxId })
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            btn.disabled = false;
            btn.innerHTML = '🔄 Changer de flux';
            alert(data.message);
            fetchFluxConfig();
            setTimeout(function() {
                fetchPlayerStatus();
                fetchPlayerJournal();
            }, 2000);
        })
        .catch(function() {
            btn.disabled = false;
            btn.innerHTML = '🔄 Changer de flux';
            alert('Erreur de communication.');
        });
    });

    // ── Ajouter un flux ──
    document.getElementById('btn_flux_add_confirm').addEventListener('click', function() {
        let id = document.getElementById('flux_add_id').value.trim();
        let nom = document.getElementById('flux_add_nom').value.trim();
        let url = document.getElementById('flux_add_url').value.trim();
        let codec = document.getElementById('flux_add_codec').value;
        let bitrate = parseInt(document.getElementById('flux_add_bitrate').value) || 0;
        let detail = document.getElementById('flux_add_detail').value.trim();
        if (!id || !nom || !url) {
            alert('ID, nom et URL sont requis.');
            return;
        }
        let payload = { id: id, nom: nom, url: url, codec: codec };
        if (bitrate > 0) payload.bitrate = bitrate;
        if (detail) payload.detail = detail;
        fetch('/api/flux_config/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.status === 'ok') {
                alert(data.message);
                document.getElementById('flux_add_id').value = '';
                document.getElementById('flux_add_nom').value = '';
                document.getElementById('flux_add_url').value = '';
                document.getElementById('flux_add_bitrate').value = '';
                document.getElementById('flux_add_detail').value = '';
                document.getElementById('flux_add_form').classList.remove('show');
                fetchFluxConfig();
            } else {
                alert('Erreur : ' + data.message);
            }
        })
        .catch(function() {
            alert('Erreur de communication.');
        });
    });

    // ── Vérifier CORS Icecast ──
    document.getElementById('btn_flux_cors_check').addEventListener('click', function() {
        let sel = document.getElementById('flux_select');
        let fluxId = sel.value;
        if (!fluxId) {
            alert('Sélectionnez d\'abord un flux.');
            return;
        }
        let btn = this;
        btn.disabled = true;
        btn.innerHTML = '⏳ Vérification...';
        fetch('/api/flux_cors_check?flux=' + encodeURIComponent(fluxId) + '&t=' + Date.now())
            .then(function(r) { return r.json(); })
            .then(function(data) {
                btn.disabled = false;
                btn.innerHTML = '🔒 Vérifier CORS';
                if (data.status !== 'ok') {
                    alert('Erreur : ' + data.message);
                    return;
                }
                let msg = 'Flux : ' + data.flux_id + '\n';
                msg += 'URL : ' + data.url + '\n';
                msg += 'CORS : ' + (data.cors_ok ? '✅ Autorisé (' + data.cors_header + ')' : '❌ Non configuré') + '\n';
                msg += 'Content-Type : ' + data.content_type + '\n\n';
                if (data.cors_ok) {
                    msg += 'Le spectre audio fonctionnera en accès direct.';
                } else {
                    msg += 'Le spectre utilisera le proxy Flask via Debian 3 (automatique).\n';
                    msg += 'Pour configurer CORS sur Icecast, ajoutez dans icecast.xml :\n';
                    msg += '<http-headers>\n  <header name="Access-Control-Allow-Origin" value="*" />\n</http-headers>';
                }
                alert(msg);
            })
            .catch(function() {
                btn.disabled = false;
                btn.innerHTML = '🔒 Vérifier CORS';
                alert('Erreur de communication.');
            });
    });

    // ── Valider le JSON sur Debian 3 ──
    document.getElementById('btn_flux_validate_remote').addEventListener('click', function() {
        let btn = this;
        btn.disabled = true;
        btn.innerHTML = '⏳...';
        fetch('/api/flux_config/validate_remote?t=' + Date.now())
            .then(function(r) { return r.json(); })
            .then(function(data) {
                btn.disabled = false;
                btn.innerHTML = '✓ Valider';
                if (data.valide) {
                    alert('✅ ' + data.message);
                } else {
                    alert('❌ ' + data.message + '\n\n' + (data.suggestion || ''));
                }
            })
            .catch(function() {
                btn.disabled = false;
                btn.innerHTML = '✓ Valider';
                alert('Erreur de communication.');
            });
    });

    // ── Synchroniser le JSON local vers Debian 3 ──
    document.getElementById('btn_flux_sync_remote').addEventListener('click', function() {
        if (!confirm('Synchroniser le flux_radio.json LOCAL (Windows) vers Debian 3 ?\n'
                    + 'Le service airvs-radio sera redémarré.')) return;
        let btn = this;
        btn.disabled = true;
        btn.innerHTML = '⏳...';
        fetch('/api/flux_config/sync_remote', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                btn.disabled = false;
                btn.innerHTML = '⇄ Sync';
                alert(data.message);
                if (data.status === 'ok') {
                    setTimeout(function() {
                        fetchPlayerStatus();
                        fetchPlayerJournal();
                    }, 2000);
                }
            })
            .catch(function() {
                btn.disabled = false;
                btn.innerHTML = '⇄ Sync';
                alert('Erreur de communication.');
            });
    });

    // ════════════════════════════════════
    // VISUALISEUR SPECTRE AUDIO (Web Audio API)
    // ════════════════════════════════════

    let audioCtx = null;
    let analyserNode = null;
    let spectrumAnimId = null;
    let spectrumActive = false;
    let spectrumMediaSourceCreated = false;  // Un seul createMediaElementSource par élément

    const spectrumCanvas = document.getElementById('spectrum_canvas');
    const spectrumCtx = spectrumCanvas.getContext('2d');
    const spectrumAudio = document.getElementById('spectrum_audio');
    const spectrumPlaceholder = document.getElementById('spectrum_placeholder');
    const btnSpectrumToggle = document.getElementById('btn_spectrum_toggle');

    // Ajuster la résolution du canvas à la taille réelle
    function resizeSpectrumCanvas() {
        let rect = spectrumCanvas.parentElement.getBoundingClientRect();
        spectrumCanvas.width = Math.floor(rect.width);
        spectrumCanvas.height = Math.floor(rect.height);
    }
    window.addEventListener('resize', function() {
        if (spectrumActive) resizeSpectrumCanvas();
    });

    // Palette de couleurs pour le spectre (dégradé bleu → cyan → vert)
    function spectrumBarColor(ratio) {
        // ratio = 0 (basses fréquences) à 1 (hautes fréquences)
        let r, g, b;
        if (ratio < 0.5) {
            // Bleu → Cyan
            let t = ratio * 2;
            r = Math.floor(20 * (1 - t));
            g = Math.floor(100 + 155 * t);
            b = Math.floor(200 + 55 * t);
        } else {
            // Cyan → Vert
            let t = (ratio - 0.5) * 2;
            r = Math.floor(20 + 30 * t);
            g = Math.floor(255 - 55 * t);
            b = Math.floor(255 - 200 * t);
        }
        return 'rgb(' + r + ',' + g + ',' + b + ')';
    }

    function drawSpectrum() {
        if (!analyserNode || !spectrumActive) return;
        spectrumAnimId = requestAnimationFrame(drawSpectrum);

        let bufferLength = analyserNode.frequencyBinCount;
        let dataArray = new Uint8Array(bufferLength);
        analyserNode.getByteFrequencyData(dataArray);

        let width = spectrumCanvas.width;
        let height = spectrumCanvas.height;

        // Fond sombre
        spectrumCtx.fillStyle = '#0a0a1a';
        spectrumCtx.fillRect(0, 0, width, height);

        // Nombre de barres à afficher (64 pour un bel equalizer)
        let numBars = 64;
        let step = Math.floor(bufferLength / numBars);
        let barWidth = Math.max(1, Math.floor(width / numBars) - 1);
        let barGap = 1;

        for (let i = 0; i < numBars; i++) {
            // Moyenner les fréquences dans cette bande
            let sum = 0;
            for (let j = 0; j < step; j++) {
                sum += dataArray[i * step + j];
            }
            let avg = sum / step;
            let barHeight = Math.max(1, (avg / 255) * height * 0.9);

            let x = i * (barWidth + barGap);
            let ratio = i / numBars;

            // Dégradé vertical dans chaque barre
            let gradient = spectrumCtx.createLinearGradient(x, height, x, height - barHeight);
            gradient.addColorStop(0, spectrumBarColor(ratio));
            gradient.addColorStop(0.7, spectrumBarColor(ratio));
            gradient.addColorStop(1, 'rgba(255,255,255,0.8)');
            spectrumCtx.fillStyle = gradient;
            spectrumCtx.fillRect(x, height - barHeight, barWidth, barHeight);

            // Reflet subtil en bas
            spectrumCtx.fillStyle = 'rgba(255,255,255,0.03)';
            spectrumCtx.fillRect(x, height - 2, barWidth, 2);
        }
    }

    btnSpectrumToggle.addEventListener('click', function() {
        if (spectrumActive) {
            // Arrêter
            spectrumActive = false;
            if (spectrumAnimId) cancelAnimationFrame(spectrumAnimId);
            spectrumAudio.pause();
            spectrumAudio.src = '';
            spectrumPlaceholder.style.display = '';
            btnSpectrumToggle.innerHTML = '🎧 Écouter';
            btnSpectrumToggle.classList.remove('btn-info');
            btnSpectrumToggle.classList.add('btn-outline-info');
            // Effacer le canvas
            spectrumCtx.fillStyle = '#0a0a1a';
            spectrumCtx.fillRect(0, 0, spectrumCanvas.width, spectrumCanvas.height);
            return;
        }

        // Récupérer l'URL du flux à écouter
        if (!currentFluxConfig || !currentFluxConfig.flux_disponibles || currentFluxConfig.flux_disponibles.length === 0) {
            alert('Aucun flux configuré.\n\n'
                  + 'Ajoutez un flux via le formulaire "➕ Ajouter un flux" ci-contre, '
                  + 'puis cliquez sur ⇄ Sync pour l\'envoyer vers Debian 3.');
            return;
        }

        // Priorité : flux sélectionné dans le dropdown > flux actif > premier flux disponible
        let selFluxId = document.getElementById('flux_select').value;
        let fluxActif = null;

        if (selFluxId) {
            fluxActif = currentFluxConfig.flux_disponibles.find(function(f) {
                return f.id === selFluxId;
            });
        }
        if (!fluxActif && currentFluxConfig.flux_actif) {
            fluxActif = currentFluxConfig.flux_disponibles.find(function(f) {
                return f.id === currentFluxConfig.flux_actif;
            });
        }
        if (!fluxActif) {
            // Fallback : premier flux disponible
            fluxActif = currentFluxConfig.flux_disponibles[0];
        }

        // Démarrer la lecture
        resizeSpectrumCanvas();
        spectrumPlaceholder.style.display = 'none';
        btnSpectrumToggle.innerHTML = '⏹ Arrêter';
        btnSpectrumToggle.classList.remove('btn-outline-info');
        btnSpectrumToggle.classList.add('btn-info');

        // Essayer d'abord l'URL directe (CORS doit être autorisé sur Icecast)
        // Si échec, basculer vers le proxy Flask
        let directUrl = fluxActif.url;
        let proxyUrl = '/api/stream_proxy/' + encodeURIComponent(fluxActif.id);

        function connectSpectrumAudio() {
            // Créer le contexte audio si nécessaire
            if (!audioCtx) {
                audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            }
            if (audioCtx.state === 'suspended') {
                audioCtx.resume();
            }

            // Ne créer le MediaElementSource qu'une seule fois par élément audio
            if (!spectrumMediaSourceCreated) {
                let source = audioCtx.createMediaElementSource(spectrumAudio);
                analyserNode = audioCtx.createAnalyser();
                analyserNode.fftSize = 256;
                analyserNode.smoothingTimeConstant = 0.8;
                source.connect(analyserNode);
                analyserNode.connect(audioCtx.destination);
                spectrumMediaSourceCreated = true;
            }

            spectrumActive = true;
            drawSpectrum();
        }

        // Tentative 1 : via le proxy Flask (passe par Debian 3 → VPS OVH)
        // C'est le chemin prioritaire car le navigateur est sur Windows (pas d'internet)
        spectrumAudio.removeAttribute('crossorigin');
        spectrumAudio.src = proxyUrl;
        spectrumAudio.play().then(function() {
            connectSpectrumAudio();
        }).catch(function(err) {
            // Tentative 2 : accès direct au flux Icecast (si le navigateur a internet par un autre chemin)
            console.warn('Proxy échoué (' + err.message + '), tentative accès direct Icecast...');
            spectrumAudio.setAttribute('crossorigin', 'anonymous');
            spectrumAudio.src = directUrl;
            spectrumAudio.play().then(function() {
                connectSpectrumAudio();
            }).catch(function(err2) {
                alert('Impossible de lire le flux (proxy + direct) : ' + err2.message
                      + '\n\nVérifiez que Debian 3 est accessible en SSH et que le service Icecast fonctionne.');
                spectrumPlaceholder.style.display = '';
                btnSpectrumToggle.innerHTML = '🎧 Écouter';
                btnSpectrumToggle.classList.remove('btn-info');
                btnSpectrumToggle.classList.add('btn-outline-info');
            });
        });
    });

    // ════════════════════════════════════
    // LECTEUR AUDIO — Barre fixe basse
    // ════════════════════════════════════
    function ajusterPaddingLecteur() {
        if (lecteurCard.style.display !== 'none') {
            document.body.style.paddingBottom = '60px';
        } else {
            document.body.style.paddingBottom = '';
        }
    }

    if (lecteurCard && audioPlayer && lecteurClose) {
        document.addEventListener('click', function(e) {
            if (e.target.classList.contains('play-btn')) {
                lecteurTitre.textContent = e.target.dataset.artist + " - " + e.target.dataset.title;
                lecteurCard.style.display = 'block';
                audioPlayer.src = '/ecouter/' + e.target.dataset.id;
                audioPlayer.play().catch(function() {});
                ajusterPaddingLecteur();
            }
        });

        lecteurClose.addEventListener('click', function() {
            audioPlayer.pause();
            audioPlayer.src = '';
            lecteurCard.style.display = 'none';
            ajusterPaddingLecteur();
        });
    }

    // ════════════════════════════════════
    // INITIALISATION DU PONT
    // ════════════════════════════════════
    setInterval(fetchPlayerStatus, 15000);
    setInterval(fetchFluxStatus, 10000);
    fetchPlayerStatus();
    fetchPlayerJournal();
    fetchArchivesList();
    fetchFluxStatus();
    fetchFluxConfig();

})();
