# Évolutions AIRVS — 10 septembre 2026

## Évolution 1 : Conversion FLAC → MP3 320k dans le pipeline d'import

### Fichiers modifiés
- `pipeline_import.py` — 2 nouveaux endpoints + 3 nouveaux paramètres pipeline

### Nouveaux endpoints

| Méthode | Route | Description |
|---------|-------|-------------|
| `POST` | `/api/pipeline/flac-convert` | Convertit un fichier FLAC en MP3 via ffmpeg |
| `POST` | `/api/pipeline/flac-detect` | Détecte les fichiers FLAC dans un dossier source |

### Nouveaux paramètres pipeline (`/api/pipeline/launch`)

| Paramètre | Type | Défaut | Description |
|-----------|------|--------|-------------|
| `convert_flac` | bool | `true` | Activer la conversion FLAC → MP3 avant le pipeline |
| `flac_bitrate` | int | `320` | Bitrate cible en kbps (320, 256, 192) |
| `flac_delete_source` | bool | `false` | Supprimer le FLAC source après conversion réussie |

### Pipeline mis à jour

```
Fichiers soumis → [Détection format] → [FLAC → MP3 320k si nécessaire] → Dédoublonnage → Normalisation → Import DB
```

### UI ajoutée (index.html — onglet Import & Validation)
- Checkbox « Convertir FLAC → MP3 avant pipeline » (cochée par défaut)
- Sélecteur bitrate : 320 / 256 / 192 kbps
- Checkbox « Supprimer le FLAC source après conversion »

---

## Évolution 2 : Pré-remplissage de la grille éditoriale via API AzuraCast

### Fichiers modifiés
- `azuracast.py` — 1 nouvel endpoint + 3 helpers
- `index.html` — bouton « Remplir depuis AzuraCast » + fonction JS

### Nouvel endpoint

| Méthode | Route | Auth | Description |
|---------|-------|------|-------------|
| `GET` | `/api/azuracast/grille_editoriale?station_id=7` | Oui | Retourne les blocs horaires pré-remplissables |

### Logique de pré-remplissage

Pour chaque tranche horaire :
- **1 seule playlist** → `mode: "auto"` → pré-remplissage automatique du bloc
- **>1 playlists avec poids** → `mode: "multi"` → badge « 3 playlists (poids 50/30/20) — saisie manuelle »

### Réponse exemple

```json
{
  "station_id": 7,
  "blocs": [
    {
      "tranche": "06h00-09h00",
      "start_time": 600,
      "end_time": 900,
      "days": ["Lun", "Mar", "Mer", "Jeu", "Ven"],
      "mode": "auto",
      "playlists": [{"id": 12, "name": "Matinales", "weight": 1, "num_songs": 150}],
      "badge": null
    },
    {
      "tranche": "09h00-12h00",
      "start_time": 900,
      "end_time": 1200,
      "days": ["Lun", "Mar", "Mer", "Jeu", "Ven"],
      "mode": "multi",
      "playlists": [...],
      "badge": "3 playlists (poids 50/30/20) — saisie manuelle"
    }
  ],
  "total_blocs": 2,
  "auto_count": 1,
  "multi_count": 1
}
```

### Helpers ajoutés (azuracast.py)

| Fonction | Description |
|----------|-------------|
| `_azura_api_get_station(base_url, api_key, path)` | Appel API AzuraCast avec URL configurable |
| `_hhmm_to_minutes(hhmm)` | Convertit 900 → 540 minutes |
| `_jours_noms(days_list)` | Convertit [1,2,3] → ["Lun","Mar","Mer"] |

### UI ajoutée (index.html — onglet Grille éditoriale)
- Bouton « Remplir depuis AzuraCast » avec sélecteur de station (6/7)
- Confirmation avec résumé des blocs auto/multi
- Intégration via `window._remplirBlocsDepuisAzura()` (à implémenter dans grille-editoriale.js)

---

## Dépendance requise
- **ffmpeg** doit être installé sur le worker Ubuntu pour la conversion FLAC → MP3
  ```bash
  sudo apt install ffmpeg
  ```
