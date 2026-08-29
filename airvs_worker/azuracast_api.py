#!/usr/bin/env python3
"""
AzuraCast REST API Client - Module AIRVS

Ce module encapsule toutes les communications API avec AzuraCast.
Il est conçu pour fonctionner sur les machines ayant accès à Internet
(Worker Ubuntu Studio, Debian monitoring).

Les appels API ne peuvent PAS être effectués depuis la machine Windows
car elle est hors du réseau Internet. Toutes les opérations API transitent
par le système de tâches (taches_planifiees) et sont exécutées par le worker.

Endpoints AzuraCast utilisés :
  - GET    /api/station/{id}/playlists          → Lister les playlists
  - POST   /api/station/{id}/playlists          → Créer une playlist
  - GET    /api/station/{id}/playlist/{pid}     → Détails d'une playlist
  - PUT    /api/station/{id}/playlist/{pid}     → Modifier une playlist
  - DELETE /api/station/{id}/playlist/{pid}     → Supprimer une playlist
  - POST   /api/station/{id}/playlist/{pid}/import → Importer M3U/PLS
  - PUT    /api/station/{id}/playlist/{pid}/shuffle → Mélanger la playlist
  - GET    /api/station/{id}/files              → Lister les médias
  - GET    /api/station/{id}/file/{fid}         → Détails d'un média
  - PUT    /api/station/{id}/file/{fid}         → Modifier un média
"""

import requests
import json
import time
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class AzuraCastAPIError(Exception):
    """Exception personnalisée pour les erreurs API AzuraCast."""

    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class AzuraCastAPI:
    """
    Client REST pour l'API AzuraCast.

    Usage :
        api = AzuraCastAPI(
            base_url="https://azuracast.2026.airvs.fr/api",
            api_key="5e256834da4da4cf:1cadd3936115612949a795cd5a48e12d",
            station_id=7
        )

        # Lister les playlists
        playlists = api.list_playlists()

        # Créer une playlist
        playlist = api.create_playlist("Rotation Matinale", type="default")

        # Importer un M3U dans une playlist
        api.import_m3u(playlist['id'], m3u_content)
    """

        # Types de playlists AzuraCast valides (rolling release 2024+)
    # Note : 'scheduled' et 'custom' ont été supprimés des versions récentes.
    # Pour une playlist programmée, utiliser type='default' + schedule_items.
    # Types de playlists AzuraCast valides (OpenAPI officiel)
    # Valeurs acceptées par l'enum PlaylistTypes d'AzuraCast.
    # NE PAS utiliser 'scheduled' — il n'existe plus dans les versions récentes.
    PLAYLIST_TYPES = {
        'default': 'Playlist standard (lecture séquentielle/aléatoire)',
        'once_per_x_songs': 'Une fois toutes les X chansons',
        'once_per_x_minutes': 'Une fois toutes les X minutes',
        'once_per_hour': 'Une fois par heure',
        'custom': 'Playlist avancée (configuration manuelle)',
    }

    # Ordres de lecture possibles
    PLAYLIST_ORDERS = {
        'shuffle': 'Aléatoire',
        'sequential': 'Séquentiel',
    }

    def __init__(self, base_url, api_key, station_id, timeout=30):
        """
        Initialise le client API.

        Args:
            base_url: URL de base de l'API (ex: "https://azuracast.2026.airvs.fr/api")
            api_key: Clé API AzuraCast
            station_id: ID de la station AzuraCast
            timeout: Délai d'attente par défaut en secondes
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.station_id = station_id
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        })

    def _url(self, path):
        """Construit l'URL complète pour un endpoint donné."""
        return f"{self.base_url}/station/{self.station_id}{path}"

    def _handle_response(self, response, operation="API call"):
        """
        Gère la réponse HTTP et lève une exception en cas d'erreur.

        Returns:
            dict ou list: Données JSON de la réponse

        Raises:
            AzuraCastAPIError: Si le code HTTP n'est pas 2xx
        """
        if 200 <= response.status_code < 300:
            try:
                return response.json()
            except ValueError:
                return {'raw': response.text}

        # Erreur - extraire le message
        try:
            error_data = response.json()
            error_msg = error_data.get('message', error_data.get('error', response.text))
        except ValueError:
            error_msg = response.text[:500]

        raise AzuraCastAPIError(
            f"{operation} échoué (HTTP {response.status_code}): {error_msg}",
            status_code=response.status_code,
            response_body=error_msg
        )

    # ═══════════════════════════════════════════════
    # GESTION DES PLAYLISTS
    # ═══════════════════════════════════════════════

    def list_playlists(self):
        """
        Liste toutes les playlists de la station.

        Returns:
            list: Liste des playlists avec leurs métadonnées complètes.
                  Chaque playlist contient : id, name, type, source, order,
                  items (liste des médias), etc.
        """
        response = self.session.get(
            self._url('/playlists'),
            timeout=self.timeout
        )
        return self._handle_response(response, "Lister les playlists")
    
    def find_playlist_by_name(self, name):
        """
        Recherche une playlist par son nom exact.

        Args:
            name: Nom exact de la playlist à rechercher

        Returns:
            dict or None: La playlist trouvée, ou None si aucune correspondance
        """
        playlists = self.list_playlists()
        for pl in playlists:
            if pl.get('name', '').strip().lower() == name.strip().lower():
                return pl
        return None

    def get_playlist(self, playlist_id):
        """
        Récupère les détails complets d'une playlist.

        Args:
            playlist_id: ID de la playlist AzuraCast

        Returns:
            dict: Détails de la playlist incluant la liste des médias associés
        """
        response = self.session.get(
            self._url(f'/playlist/{playlist_id}'),
            timeout=self.timeout
        )
        return self._handle_response(response, f"Obtenir la playlist {playlist_id}")

    def create_playlist(self, name, type='default', order='shuffle',
                        is_enabled=True, include_in_requests=True,
                        avoid_duplicates=True, schedule_items=None):
        """
        Crée une nouvelle playlist dans AzuraCast.

        Args:
            name: Nom de la playlist (obligatoire)
            type: Type de playlist parmi (enum PlaylistTypes AzuraCast) :
                  - 'default' : Lecture standard
                  - 'once_per_x_songs' : Une fois toutes les X chansons
                  - 'once_per_x_minutes' : Une fois toutes les X minutes
                  - 'once_per_hour' : Une fois par heure
                  - 'custom' : Avancée (configuration manuelle)
                  ⚠️ 'scheduled' N'EST PLUS VALIDE dans les versions récentes !
            order: Ordre de lecture ('shuffle' ou 'sequential')
            is_enabled: Activer la playlist immédiatement
            include_in_requests: Inclure dans les requêtes de chansons
            avoid_duplicates: Éviter les doublons dans la lecture
            schedule_items: Liste des programmations horaires

        Returns:
            dict: Playlist créée avec son ID assigné par AzuraCast
        """
        payload = {
            'name': name,
            'type': type,
            'order': order,
            'is_enabled': is_enabled,
            'include_in_requests': include_in_requests,
            'avoid_duplicates': avoid_duplicates,
        }

        if schedule_items:
            payload['schedule_items'] = schedule_items

        response = self.session.post(
            self._url('/playlists'),
            json=payload,
            timeout=self.timeout
        )
        return self._handle_response(response, f"Créer la playlist '{name}'")

    def update_playlist(self, playlist_id, **kwargs):
        """
        Modifie une playlist existante.

        Args:
            playlist_id: ID de la playlist
            **kwargs: Champs à modifier (name, type, order, is_enabled, etc.)

        Returns:
            dict: Playlist modifiée
        """
        response = self.session.put(
            self._url(f'/playlist/{playlist_id}'),
            json=kwargs,
            timeout=self.timeout
        )
        return self._handle_response(response, f"Modifier la playlist {playlist_id}")

    def delete_playlist(self, playlist_id):
        """
        Supprime une playlist.

        Args:
            playlist_id: ID de la playlist à supprimer

        Returns:
            dict: Confirmation de suppression
        """
        response = self.session.delete(
            self._url(f'/playlist/{playlist_id}'),
            timeout=self.timeout
        )
        return self._handle_response(response, f"Supprimer la playlist {playlist_id}")

    def shuffle_playlist(self, playlist_id):
        """
        Mélange l'ordre des médias dans une playlist.

        Args:
            playlist_id: ID de la playlist

        Returns:
            dict: Confirmation du mélange
        """
        response = self.session.put(
            self._url(f'/playlist/{playlist_id}/shuffle'),
            timeout=self.timeout
        )
        return self._handle_response(response, f"Mélanger la playlist {playlist_id}")

    def import_m3u(self, playlist_id, m3u_content):
        # Encoder en bytes si c'est une chaîne
        if isinstance(m3u_content, str):
            m3u_content = m3u_content.encode('utf-8')

        files = {
            'playlist_file': ('playlist_import.m3u', m3u_content, 'audio/x-mpegurl')
        }

        # Retirer temporairement Content-Type du session pour que requests
        # puisse positionner automatiquement multipart/form-data avec boundary
        saved_ct = self.session.headers.pop('Content-Type', None)
        try:
            response = self.session.post(
                self._url(f'/playlist/{playlist_id}/import'),
                files=files,
                timeout=60
            )
        finally:
            if saved_ct:
                self.session.headers['Content-Type'] = saved_ct

        return self._handle_response(response, f"Importer M3U dans playlist {playlist_id}")

    # ═══════════════════════════════════════════════
    # GESTION DES MÉDIAS (FICHIERS)
    # ═══════════════════════════════════════════════

    def list_media(self, limit=10000, page=1):
        """
        Liste tous les fichiers média de la station.

        Args:
            limit: Nombre maximum de résultats
            page: Numéro de page

        Returns:
            dict: Pagination avec les médias (rows, total, etc.)
        """
        response = self.session.get(
            self._url('/files'),
            params={'limit': limit, 'page': page},
            timeout=60  # Peut être long si beaucoup de fichiers
        )
        return self._handle_response(response, "Lister les médias")

    def get_media(self, media_id):
        """
        Récupère les détails d'un fichier média spécifique.

        Args:
            media_id: ID du média dans AzuraCast

        Returns:
            dict: Détails du média (path, artist, title, album, etc.)
        """
        response = self.session.get(
            self._url(f'/file/{media_id}'),
            timeout=self.timeout
        )
        return self._handle_response(response, f"Obtenir le média {media_id}")

    def update_media(self, media_id, **kwargs):
        """
        Modifie les métadonnées d'un fichier média.

        Args:
            media_id: ID du média
            **kwargs: Champs à modifier (artist, title, album, genre, etc.)

        Returns:
            dict: Média modifié
        """
        response = self.session.put(
            self._url(f'/file/{media_id}'),
            json=kwargs,
            timeout=self.timeout
        )
        return self._handle_response(response, f"Modifier le média {media_id}")

    def search_media_by_path(self, partial_path):
        """
        Recherche un média par chemin partiel dans AzuraCast.
        Utile pour retrouver l'ID AzuraCast d'un fichier récemment poussé via SFTP.

        Args:
            partial_path: Portion du chemin du fichier (ex: "imports_push/titre.mp3")

        Returns:
            list: Liste des médias correspondants
        """
        all_media = self.list_media()
        rows = all_media if isinstance(all_media, list) else all_media.get('rows', [])

        matches = []
        for media in rows:
            media_path = media.get('path', '')
            if partial_path.lower() in media_path.lower():
                matches.append(media)

        return matches

    # ═══════════════════════════════════════════════
    # OPÉRATIONS COMPOSITES (HAUT NIVEAU)
    # ═══════════════════════════════════════════════

    def creer_playlist_avec_fichiers(self, nom_playlist, fichiers,
                                     dossier_cible='imports_push',
                                     type='default', order='shuffle'):
        """
        Opération composite : crée une playlist et y importe les fichiers spécifiés.

        C'est la méthode principale appelée par le worker après un push SFTP.
        Le workflow est :
        1. Créer la playlist vide via API
        2. Générer le contenu M3U avec les chemins relatifs AzuraCast
        3. Importer le M3U dans la playlist via API

        Args:
            nom_playlist: Nom de la playlist à créer
            fichiers: Liste de dicts avec clés 'path' (chemin Windows),
                      'artist', 'title', 'duration'
            dossier_cible: Dossier cible dans AzuraCast (relatif à AZURA_SSHFS_ROOT)
            type: Type de playlist AzuraCast
            order: Ordre de lecture

        Returns:
            dict: {
                'playlist': données de la playlist créée,
                'nb_importes': nombre de fichiers importés,
                'm3u_content': contenu M3U généré
            }

        Raises:
            AzuraCastAPIError: En cas d'échec de création ou d'import
        """
        # 1. Créer la playlist
        playlist = self.create_playlist(
            name=nom_playlist,
            type=type,
            order=order,
            is_enabled=True
        )

        playlist_id = playlist.get('id')
        if not playlist_id:
            raise AzuraCastAPIError("La playlist créée n'a pas d'ID valide")

        # 2. Générer le contenu M3U
        #    Les chemins dans le M3U doivent être RELATIFS à la racine média AzuraCast
        #    AZURA_SSHFS_ROOT = /home/sebastien/music_azuracast6_7
        #    Si dossier_cible = "SELECTION_MARY/MERCREDI"
        #    Le chemin dans le M3U sera : SELECTION_MARY/MERCREDI/fichier.mp3
        m3u_lines = ['#EXTM3U']

        for fich in fichiers:
            # Extraire le nom du fichier à partir du chemin Windows
            chemin = fich.get('path', '')
            # Le nom de fichier est la dernière partie du chemin
            nom_fichier = chemin.replace('/', '\\').split('\\')[-1] if chemin else 'inconnu.mp3'

            artist = fich.get('artist', 'Artiste inconnu')
            title = fich.get('title', 'Titre inconnu')
            duration = int(fich.get('duration', 0))

            # Ligne EXTINF : durée, artiste - titre
            m3u_lines.append(f'#EXTINF:{duration},{artist} - {title}')

            # Chemin relatif dans AzuraCast : dossier_cible/nom_fichier
            chemin_relatif = f"{dossier_cible}/{nom_fichier}"
            m3u_lines.append(chemin_relatif)

        m3u_content = '\n'.join(m3u_lines)

        # 3. Importer le M3U dans la playlist
        import_result = self.import_m3u(playlist_id, m3u_content)

        return {
            'playlist': playlist,
            'nb_importes': len(fichiers),
            'm3u_content': m3u_content,
            'import_result': import_result,
        }

    def synchroniser_cache_playlists(self):
        """
        Récupère toutes les playlists et leurs médias pour mise en cache
        dans la base SQL locale. Permet au dashboard Windows d'afficher
        l'état des playlists sans accès direct à l'API.

        L'API AzuraCast GET /playlists retourne directement des champs
        de synthèse (song_count, total_length) pour chaque playlist.
        On les utilise en priorité. Si absents, on tente un calcul
        à partir du champ 'items' (parfois présent selon la version).

        Returns:
            list: Liste de dicts, chaque dict contient :
                  - id: ID AzuraCast de la playlist
                  - name: Nom de la playlist
                  - type: Type de playlist
                  - source: Source (songs, requests, etc.)
                  - order: Ordre de lecture
                  - is_enabled: Activée ou non
                  - nb_tracks: Nombre de morceaux
                  - total_duration: Durée totale en secondes
        """
        playlists = self.list_playlists()

        cache_data = []
        for pl in playlists:
            # ── Priorité 1 : Champs directs de l'API AzuraCast ──
            # L'endpoint de liste fournit song_count et total_length directement
            nb_tracks = pl.get('song_count', None)
            total_duration = pl.get('total_length', None)

            # ── Priorité 2 : Calcul à partir du champ 'items' ──
            # Certaines versions d'AzuraCast incluent les items dans la réponse
            if nb_tracks is None and total_duration is None:
                nb_tracks = 0
                total_duration = 0
                items = pl.get('items', [])
                if items:
                    nb_tracks = len(items)
                    for item in items:
                        # Structure : item.media.length ou item.length
                        media = item.get('media', {})
                        if isinstance(media, dict):
                            total_duration += float(media.get('length', 0))
                        elif 'length' in item:
                            total_duration += float(item.get('length', 0))

            # Sécurité : valeurs par défaut si aucun champ trouvé
            if nb_tracks is None:
                nb_tracks = 0
            if total_duration is None:
                total_duration = 0

            # total_duration peut être un entier ou un float
            try:
                total_duration = float(total_duration)
            except (TypeError, ValueError):
                total_duration = 0
            try:
                nb_tracks = int(nb_tracks)
            except (TypeError, ValueError):
                nb_tracks = 0

            cache_data.append({
                'id': pl.get('id'),
                'name': pl.get('name', ''),
                'type': pl.get('type', 'default'),
                'source': pl.get('source', 'songs'),
                'order': pl.get('order', 'shuffle'),
                'is_enabled': pl.get('is_enabled', True),
                'nb_tracks': nb_tracks,
                'total_duration': total_duration,
            })

        return cache_data

    # ═══════════════════════════════════════════════
    # PODCASTS (Évolution 1 — Piges → Podcasts, Phase 1D)
    # ═══════════════════════════════════════════════
    # NOTE : cette section a été validée sur la structure connue de l'API
    # AzuraCast v4 (endpoints /station/{id}/podcast*). Azuracast #2 pouvant
    # être une version différente d'Azuracast #1, il est recommandé de
    # tester ces méthodes une première fois isolément (cf. section "Test
    # podcasts" en bas de fichier) avant de les utiliser en prod dans le
    # worker — voir le risque documenté dans SPECIFICATION_EVOLUTIONS.md § 2.8.

    def list_podcasts(self):
        """Liste les podcasts définis sur la station."""
        response = self.session.get(self._url('/podcasts'), timeout=self.timeout)
        return self._handle_response(response, "Lister les podcasts")

    def get_podcast(self, podcast_id):
        """Détails d'un podcast (titre, description, catégories, etc.)."""
        response = self.session.get(self._url(f'/podcast/{podcast_id}'), timeout=self.timeout)
        return self._handle_response(response, f"Obtenir le podcast {podcast_id}")

    def list_podcast_episodes(self, podcast_id):
        """Liste les épisodes d'un podcast donné."""
        response = self.session.get(
            self._url(f'/podcast/{podcast_id}/episodes'), timeout=self.timeout
        )
        return self._handle_response(response, f"Lister les épisodes du podcast {podcast_id}")

    def get_podcast_episode(self, podcast_id, episode_id):
        """Détails d'un épisode unique, incluant l'objet 'media' (id, length,
        length_text, path) s'il est présent — 'media': None sinon.
        Utilisé pour VÉRIFIER qu'un upload média a réellement été ingéré,
        car POST .../media renvoie {"success": true} même quand AzuraCast
        échoue en interne à traiter le fichier (cf. upload_podcast_episode_media)."""
        response = self.session.get(
            self._url(f'/podcast/{podcast_id}/episode/{episode_id}'), timeout=self.timeout
        )
        return self._handle_response(response, f"Obtenir l'épisode {episode_id}")

    def _publish_at_to_timestamp(self, publish_at):
        """Normalise publish_at en timestamp Unix (int), seul format accepté
        par AzuraCast pour PodcastEpisode::publish_at (confirmé par erreur 500
        'must be one of int, null' sur une string ISO). Accepte en entrée soit
        un int/float déjà en timestamp, soit une chaîne ISO 8601
        ("2026-07-15T15:00:00Z" ou "2026-07-15T15:00:00")."""
        if publish_at is None:
            return None
        if isinstance(publish_at, (int, float)):
            return int(publish_at)
        if isinstance(publish_at, str):
            s = publish_at.strip().replace('Z', '')
            try:
                dt = datetime.strptime(s, '%Y-%m-%dT%H:%M:%S')
            except ValueError:
                dt = datetime.strptime(s[:16], '%Y-%m-%dT%H:%M')
            return int(dt.replace(tzinfo=timezone.utc).timestamp())
        raise ValueError(f"Format de publish_at non supporté : {publish_at!r}")

    def create_podcast_episode(self, podcast_id, title, description='',
                                episode_number=None, season_number=None,
                                publish_at=None, explicit=False):
        """
        Crée les métadonnées d'un épisode de podcast (sans média — l'audio
        s'uploade ensuite séparément via upload_podcast_episode_media()).

        Args:
            podcast_id: ID du podcast cible (station Azuracast #2)
            title: Titre de l'épisode (obligatoire)
            description: Description / notes de l'épisode
            episode_number: Numéro d'épisode (optionnel)
            season_number: Numéro de saison (optionnel — AzuraCast exige une
                        valeur, donc 1 est envoyé par défaut si non fourni)
            publish_at: Date de publication — timestamp Unix (int) ou chaîne
                        ISO 8601 (convertie automatiquement en timestamp,
                        seul format accepté par l'API AzuraCast)
            explicit: Contenu explicite (True/False)

        Returns:
            dict: Épisode créé, incluant son 'id'
        """
        payload = {
            'title': title,
            'description': description or '',
            'explicit': bool(explicit),
            # AzuraCast a une propriété PHP typée sans valeur par défaut sur
            # season_number : l'omettre fait planter la création d'épisode
            # en HTTP 500 ("Typed property ...PodcastEpisode::$season_number
            # must not be accessed before initialization"), confirmé par
            # test manuel. On envoie donc toujours une valeur, 1 par défaut.
            'season_number': season_number if season_number is not None else 1,
        }
        if episode_number is not None:
            payload['episode_number'] = episode_number
        ts = self._publish_at_to_timestamp(publish_at)
        if ts is not None:
            payload['publish_at'] = ts

        response = self.session.post(
            self._url(f'/podcast/{podcast_id}/episodes'),
            json=payload, timeout=self.timeout
        )
        return self._handle_response(response, f"Créer l'épisode du podcast {podcast_id}")

    def upload_podcast_episode_media(self, podcast_id, episode_id, file_path, timeout=None):
        """
        Upload le fichier audio d'un épisode (multipart/form-data), PUIS
        VÉRIFIE que le fichier a réellement été ingéré côté AzuraCast.

        IMPORTANT — piège constaté : l'endpoint POST .../media répond
        {"success": true, "message": "Enregistrement mis à jour avec succès."}
        même quand AzuraCast échoue en interne à traiter le fichier audio
        (ex : MP3 brut Icecast/Liquidsoap à la structure de frame abîmée).
        Ce message générique NE PROUVE PAS que le média a été attaché — seul
        un GET de contrôle de l'épisode (champ 'has_media' / 'media') le
        confirme. On fait donc systématiquement ce contrôle ici et on lève
        une erreur explicite si le média n'apparaît pas, plutôt que de
        remonter un faux succès à l'appelant.

        Détail technique : contrairement aux autres méthodes de cette classe,
        l'upload lui-même n'utilise PAS self.session (qui force
        Content-Type: application/json) — requests doit fixer lui-même le
        Content-Type multipart avec le bon boundary. On repart donc d'une
        requête directe avec uniquement le header Authorization.

        Args:
            podcast_id: ID du podcast
            episode_id: ID de l'épisode (créé via create_podcast_episode)
            file_path: Chemin local du MP3 à uploader
            timeout: Timeout spécifique (par défaut : max(self.timeout, 300) —
                     les MP3 de pige peuvent peser plusieurs dizaines de Mo)

        Returns:
            dict: l'objet 'media' de l'épisode tel que renvoyé par AzuraCast
                  (contient 'id', 'length', 'length_text', 'path', ...)

        Raises:
            AzuraCastAPIError: si l'upload HTTP échoue, OU si l'API répond
                un succès générique mais que le média n'est pas réellement
                attaché à l'épisode (has_media reste False).
        """
        url = self._url(f'/podcast/{podcast_id}/episode/{episode_id}/media')
        headers = {'Authorization': f'Bearer {self.api_key}', 'Accept': 'application/json'}
        with open(file_path, 'rb') as f:
            files = {'file': (os.path.basename(file_path), f, 'audio/mpeg')}
            response = requests.post(
                url, headers=headers, files=files,
                timeout=timeout or max(self.timeout, 300)
            )
        upload_body = self._handle_response(response, f"Upload du média de l'épisode {episode_id}")

        # Cas particulier détecté en prod : quand le fichier dépasse la
        # limite serveur (NGINX_CLIENT_MAX_BODY_SIZE / PHP_MAX_FILE_SIZE,
        # 50M par défaut sur AzuraCast — cf. https://www.azuracast.com/docs/
        # getting-started/settings/#the-azuracastenv-file), PHP répond quand
        # même en 2xx avec un corps contenant un avertissement PHP brut +
        # une FlowUploadException "No file uploaded". Ce n'est pas un
        # problème de fichier — inutile de retenter, on le signale
        # immédiatement avec un message actionnable.
        corps_brut = str(upload_body)
        if 'exceeds the limit' in corps_brut or 'FlowUploadException' in corps_brut:
            taille_fichier = os.path.getsize(file_path)
            raise AzuraCastAPIError(
                f"Upload du média de l'épisode {episode_id} refusé : le "
                f"fichier ({taille_fichier / 1024 / 1024:.1f} Mo) dépasse la "
                f"limite d'upload configurée côté serveur AzuraCast "
                f"(NGINX_CLIENT_MAX_BODY_SIZE / PHP_MAX_FILE_SIZE, 50 Mo par "
                f"défaut). Augmenter ces valeurs dans azuracast.env côté "
                f"serveur puis redémarrer les conteneurs Docker. "
                f"Réponse brute : {corps_brut[:300]}"
            )

        # Contrôle réel : l'endpoint d'upload ne renvoie pas l'objet média,
        # seul un GET de l'épisode le confirme. AzuraCast traite le fichier
        # (analyse audio, waveform, durée...) de façon probablement
        # asynchrone pour les fichiers volumineux (épisodes d'1h+) : on
        # retente donc plusieurs fois avec un court délai avant de conclure
        # à un échec, plutôt que de juger sur un unique contrôle immédiat.
        media = None
        tentatives = 6
        delai = 3
        for tentative in range(1, tentatives + 1):
            episode = self.get_podcast_episode(podcast_id, episode_id)
            media = episode.get('media')
            if episode.get('has_media') and media:
                break
            if tentative < tentatives:
                time.sleep(delai)
                delai = min(delai * 2, 30)
        else:
            media = None

        if not media:
            raise AzuraCastAPIError(
                f"Upload du média de l'épisode {episode_id} : réponse "
                f"'{upload_body}' reçue de l'API, mais AzuraCast n'a "
                f"toujours pas attaché de fichier après {tentatives} "
                f"contrôles (has_media=False) — le MP3 source est "
                f"probablement mal formé et non ingérable tel quel."
            )
        return media

    def upload_podcast_episode_art(self, podcast_id, episode_id, file_path, timeout=None):
        """Upload l'artwork (image de couverture) d'un épisode (multipart/form-data)."""
        url = self._url(f'/podcast/{podcast_id}/episode/{episode_id}/art')
        headers = {'Authorization': f'Bearer {self.api_key}', 'Accept': 'application/json'}
        ext = os.path.splitext(file_path)[1].lower()
        mime = 'image/png' if ext == '.png' else 'image/jpeg'
        with open(file_path, 'rb') as f:
            files = {'file': (os.path.basename(file_path), f, mime)}
            response = requests.post(
                url, headers=headers, files=files, timeout=timeout or self.timeout
            )
        return self._handle_response(response, f"Upload de l'artwork de l'épisode {episode_id}")

    def publish_podcast_episode(self, podcast_id, episode_id):
        """
        Force la publication immédiate d'un épisode en mettant publish_at à
        maintenant. Selon les versions d'AzuraCast, l'épisode peut déjà être
        visible automatiquement dès que publish_at <= maintenant (auquel cas
        cet appel est redondant mais sans danger — PUT idempotent).
        """
        payload = {'publish_at': int(datetime.now(timezone.utc).timestamp())}
        response = self.session.put(
            self._url(f'/podcast/{podcast_id}/episode/{episode_id}'),
            json=payload, timeout=self.timeout
        )
        return self._handle_response(response, f"Publier l'épisode {episode_id}")


# ═══════════════════════════════════════════════════
# FONCTION UTILITAIRE : Génération de M3U
# ═══════════════════════════════════════════════════

def generer_m3u_pour_azuracast(fichiers, dossier_cible='imports_push'):
    """
    Génère un contenu M3U compatible AzuraCast à partir d'une liste de fichiers.

    Cette fonction utilitaire peut être utilisée sans instance AzuraCastAPI
    pour pré-générer le M3U avant de le passer à l'API.

    Args:
        fichiers: Liste de dicts avec clés 'path', 'artist', 'title', 'duration'
        dossier_cible: Dossier cible dans AzuraCast (chemin relatif)

    Returns:
        str: Contenu M3U complet
    """
    lines = ['#EXTM3U']

    for fich in fichiers:
        chemin = fich.get('path', '')
        nom_fichier = chemin.replace('/', '\\').split('\\')[-1] if chemin else 'inconnu.mp3'

        artist = fich.get('artist', 'Artiste inconnu')
        title = fich.get('title', 'Titre inconnu')
        duration = int(fich.get('duration', 0))

        lines.append(f'#EXTINF:{duration},{artist} - {title}')
        chemin_relatif = f"{dossier_cible}/{nom_fichier}"
        lines.append(chemin_relatif)

    return '\n'.join(lines)


# ═══════════════════════════════════════════════════
# INSTANCIATION PAR STATION (multi-station via config.json)
# ═══════════════════════════════════════════════════

# Valeurs par défaut (fallback si config.json absent/corrompu).
# Conservées pour rétrocompatibilité avec les déploiements existants.
_DEFAULT_BASE_URL = "https://azuracast.2026.airvs.fr/api"
_DEFAULT_API_KEY  = "5e256834da4da4cf:1cadd3936115612949a795cd5a48e12d"
_DEFAULT_STATION_ID = 7
_CONFIG_JSON_PATH = "config.json"  # À côté du script Python (worker_ubuntu.py)


def _charger_config_azuracast():
    """Lit config.json et retourne la section 'azuracast'.
    Retourne {} si fichier absent/corrompu (fallback sur constantes par défaut)."""
    import os
    try:
        if not os.path.exists(_CONFIG_JSON_PATH):
            return {}
        with open(_CONFIG_JSON_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
        azura = config.get("azuracast") if isinstance(config, dict) else None
        return azura if isinstance(azura, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Erreur lecture {_CONFIG_JSON_PATH} : {e}")
        return {}


def get_api_for_station(station_id=None):
    """
    Retourne une instance AzuraCastAPI configurée pour la station demandée.

    Args:
        station_id: ID de la station AzuraCast (ex: 6 ou 7).
                    Si None, utilise la première station déclarée dans config.json
                    (ou _DEFAULT_STATION_ID en fallback).

    Lit les paramètres depuis config.json (section 'azuracast') :
      - base_url : URL de base de l'API (ex: "https://azuracast.2026.airvs.fr")
      - api_key  : clé API (commune à toutes les stations d'une même installation)
      - stations : liste des stations connues [{id, nom}, ...]

    Fallback sur les constantes par défaut si config.json est absent.
    """
    azura = _charger_config_azuracast()

    base_url = azura.get("base_url", _DEFAULT_BASE_URL)
    # S'assurer que base_url finit par /api (l'ancienne convention)
    if not base_url.endswith("/api"):
        base_url = base_url.rstrip("/") + "/api"

    api_key = azura.get("api_key", _DEFAULT_API_KEY)

    # Résolution de station_id
    if station_id is None:
        # Première station de config.json, sinon fallback
        stations = azura.get("stations", [])
        if isinstance(stations, list) and stations and "id" in stations[0]:
            station_id = int(stations[0]["id"])
        else:
            station_id = _DEFAULT_STATION_ID
    else:
        station_id = int(station_id)

    return AzuraCastAPI(
        base_url=base_url,
        api_key=api_key,
        station_id=station_id
    )


def get_default_api():
    """
    Rétrocompatibilité : retourne l'API pour la station par défaut (première
    station de config.json, ou station 7 en fallback).
    Préférez get_api_for_station(station_id) dans tout nouveau code.
    """
    return get_api_for_station(None)


# ═══════════════════════════════════════════════════
# AZURACAST #2 — Instance dédiée aux podcasts (Évolution 1)
# ═══════════════════════════════════════════════════
# Azuracast #2 (web.podcast.2026.airvs.fr) est une INSTALLATION DISTINCTE
# d'Azuracast #1 (pas juste une autre station de la même installation) :
# base_url, api_key et station_id différents. Même mécanisme de config que
# Azuracast #1 (section dédiée dans config.json, à côté de worker_ubuntu.py) :
#
#   {
#     "azuracast": { ... existant, Azuracast #1 ... },
#     "azuracast2": {
#       "base_url": "https://web.podcast.2026.airvs.fr",
#       "api_key": "<clé_api_azuracast2>",
#       "station_id": 7
#     }
#   }
#
# Une variable d'environnement AZURACAST2_API_KEY reste supportée en
# fallback (utile pour un test ponctuel sans toucher à config.json), mais
# config.json est la source de vérité — cohérent avec Azuracast #1.
_AZURACAST2_BASE_URL_DEFAULT = "https://web.podcast.2026.airvs.fr/api"
_AZURACAST2_STATION_ID_DEFAULT = 7


def _charger_config_azuracast2():
    """Lit config.json et retourne la section 'azuracast2'. {} si absente."""
    import os
    try:
        if not os.path.exists(_CONFIG_JSON_PATH):
            return {}
        with open(_CONFIG_JSON_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
        azura2 = config.get("azuracast2") if isinstance(config, dict) else None
        return azura2 if isinstance(azura2, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Erreur lecture {_CONFIG_JSON_PATH} (section azuracast2) : {e}")
        return {}


def get_api_for_azuracast2(station_id=None):
    """
    Retourne une instance AzuraCastAPI configurée pour Azuracast #2
    (installation dédiée aux podcasts, cf. Évolution 1 — Piges → Podcasts).

    Ordre de résolution de la clé API et de l'URL (comme get_api_for_station) :
      1. config.json, section "azuracast2"
      2. Variable d'environnement AZURACAST2_API_KEY / AZURACAST2_API_URL (fallback)
    """
    azura2 = _charger_config_azuracast2()

    api_key = azura2.get("api_key") or os.environ.get("AZURACAST2_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Clé API Azuracast #2 introuvable — ajouter la section \"azuracast2\" "
            "dans config.json (voir docstring), ou à défaut définir la variable "
            "d'environnement AZURACAST2_API_KEY"
        )

    base_url = azura2.get("base_url") or os.environ.get("AZURACAST2_API_URL", _AZURACAST2_BASE_URL_DEFAULT)
    if not base_url.endswith("/api"):
        base_url = base_url.rstrip("/") + "/api"

    resolved_station_id = station_id
    if resolved_station_id is None:
        resolved_station_id = azura2.get("station_id") or os.environ.get(
            "AZURACAST2_STATION_ID", _AZURACAST2_STATION_ID_DEFAULT
        )

    return AzuraCastAPI(base_url=base_url, api_key=api_key, station_id=int(resolved_station_id))


def lister_stations_config():
    """Retourne la liste des stations déclarées dans config.json.
    Format : [{'id': 6, 'nom': 'Station 6'}, {'id': 7, 'nom': 'Station 7'}]
    Fallback : [{'id': 7, 'nom': 'Station 7'}] si config absent."""
    azura = _charger_config_azuracast()
    stations_raw = azura.get("stations", [])
    if not isinstance(stations_raw, list) or not stations_raw:
        return [{"id": _DEFAULT_STATION_ID, "nom": f"Station {_DEFAULT_STATION_ID}"}]
    stations = []
    for s in stations_raw:
        if isinstance(s, dict) and "id" in s:
            stations.append({
                "id": int(s["id"]),
                "nom": s.get("nom", f"Station {s['id']}")
            })
    return stations if stations else [{"id": _DEFAULT_STATION_ID, "nom": f"Station {_DEFAULT_STATION_ID}"}]


# ═══════════════════════════════════════════════════
# TEST AUTONOME
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    print("=== Test du client API AzuraCast ===\n")

    api = get_default_api()

    try:
        # Test 1 : Lister les playlists
        print("1. Listing des playlists...")
        playlists = api.list_playlists()
        print(f"   {len(playlists)} playlist(s) trouvée(s)")
        for pl in playlists[:5]:
            print(f"   - [{pl.get('id')}] {pl.get('name')} ({pl.get('type')}, {len(pl.get('items', []))} morceaux)")

        # Test 2 : Lister les médias (limité)
        print("\n2. Listing des médias (10 premiers)...")
        media = api.list_media(limit=10)
        rows = media if isinstance(media, list) else media.get('rows', [])
        print(f"   {len(rows)} média(s) récupéré(s)")
        for m in rows[:5]:
            print(f"   - [{m.get('unique_id')}] {m.get('artist', '?')} - {m.get('title', '?')} ({m.get('path', '')})")

        # Test 3 : Synchroniser le cache
        print("\n3. Synchronisation du cache...")
        cache = api.synchroniser_cache_playlists()
        print(f"   {len(cache)} playlist(s) en cache")
        for c in cache[:5]:
            print(f"   - [{c['id']}] {c['name']} ({c['nb_tracks']} morceaux, {c['total_duration']:.0f}s)")

    except AzuraCastAPIError as e:
        print(f"\nERREUR API : {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nERREUR INATTENDUE : {e}")
        sys.exit(1)

    print("\n=== Tous les tests réussis ===")