#!/usr/bin/env python3
"""
AIRVS - Module de validation Pool multi-format
================================================

Ce module étend le Worker AIRVS pour permettre à des tiers (Vince, Laurent,
Mary...) de proposer des titres via leurs **Google Sheets existantes** (sur
Google Drive, accessibles via gspread + credentials.json), sans leur imposer
un nouveau format de document.

FONCTIONNEMENT GOOGLE DRIVE :
  - Le worker ouvre la Sheet via `gspread.authorize(creds).open_by_key(sheet_id)`
  - Pour chaque feuille (worksheet) du classeur, on appelle :
        valeurs = ws.get_all_values()   # List[List[str]] — 1 liste par ligne
  - Ce module ne dépend PAS d'openpyxl en production. Il consomme directement
    les List[List[str]] renvoyés par gspread.
  - Le test CLI local (sur fichier .xlsx) reste possible via openpyxl, mais
    c'est uniquement pour le débogage hors production.

Trois profils de tiers sont auto-détectés :

  PROFIL_VINCE   : SEMAINE | JOUR | ARTISTE | TITRE 1 | ANNÉE | INFOS
                   (tiers occasionnel, jour variable)
  PROFIL_LAURENT : SEMAINE | JOUR | ARTISTE | TITRE 1 | ANNÉE | ALBUM | INFOS
                   (tiers quotidien, marqueur "OK" dans SEMAINE possible)
  PROFIL_MARY    : SEMAINE | ARTISTE | TITRE 1 | [TITRE 2] | [TITRE 3] | ANNÉE | [ALBUM] | INFOS
                   (tiers jour fixe, plusieurs titres par ligne possibles)

Intégration worker (3 emplacements obligatoires — voir bug 2026.06.23-A) :
  - SELECT IN(...) dans _extraire_taches_avancees
  - dispatch elif dans executer_tache
  - Endpoint Flask dans app.py
"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constantes publiques
# ---------------------------------------------------------------------------

PROFIL_VINCE = "vince"
PROFIL_LAURENT = "laurent"
PROFIL_MARY = "mary"
PROFIL_INCONNU = "inconnu"

TYPE_ACTION_VALIDATION_POOL = "VALIDATION_POOL_SHEETS"


# ---------------------------------------------------------------------------
# Auto-détection du format
# ---------------------------------------------------------------------------

@dataclass
class FormatDetected:
    """Résultat de l'auto-détection du format d'une feuille Google Sheet."""
    profil: str
    colonnes: Dict[str, int]  # nom logique -> index 0-based (compatible List[str])
    colonnes_brutes: List[str]
    feuille: str

    def has(self, name: str) -> bool:
        return name in self.colonnes

    def idx(self, name: str) -> int:
        return self.colonnes[name]


# Mapping normalisé des variantes d'en-tête rencontrées dans les 3 fichiers réels
# + variantes courantes (espaces, accents, casse).
_ALIAS_COLONNES = {
    "semaine": "SEMAINE",
    "semaine ": "SEMAINE",
    " semaine": "SEMAINE",
    "jour": "JOUR",
    "artiste": "ARTISTE",
    "artiste ": "ARTISTE",
    "titre 1": "TITRE_1",
    "titre1": "TITRE_1",
    "titre 1 ": "TITRE_1",
    " titre 1": "TITRE_1",
    "titre 2": "TITRE_2",
    "titre2": "TITRE_2",
    "titre 2 ": "TITRE_2",
    "titre 3": "TITRE_3",
    "titre3": "TITRE_3",
    "titre 3 ": "TITRE_3",
    "année": "ANNEE",
    "annee": "ANNEE",
    "année ": "ANNEE",
    "album": "ALBUM",
    "album ": "ALBUM",
    "infos / anecdotes": "INFOS",
    "infos/anecdotes": "INFOS",
    "infos": "INFOS",
    "infos / anecdotes ": "INFOS",
}


def _normaliser_en_tete(valeur: Any) -> str:
    if valeur is None:
        return ""
    s = str(valeur).strip().lower()
    # Normaliser les espaces multiples
    s = re.sub(r"\s+", " ", s)
    return s


def detecter_format(feuille_nom: str, en_tetes: List[Any]) -> FormatDetected:
    """
    Détecte le profil (vince / laurent / mary) à partir de la liste d'en-têtes
    de la première ligne non vide de la feuille.

    Paramètres :
      - feuille_nom : nom de la feuille (ex "JUIN")
      - en_tetes    : liste des valeurs de la ligne d'en-tête.
                      Accepte aussi bien List[str] (gspread get_all_values()[0])
                      que les cellules openpyxl (pour tests locaux).

    Règles :
      - Si JOUR présent                       → vince OU laurent
          - Si ALBUM présent                  → laurent
          - Sinon                             → vince
      - Si JOUR absent + TITRE 2 ou 3 présent → mary
      - Sinon                                → inconnu (fallback vince)
    """
    mapping: Dict[str, int] = {}
    brut: List[str] = []
    for i, h in enumerate(en_tetes):  # index 0-based (compatible List[str])
        key = _normaliser_en_tete(h)
        brut.append(str(h) if h is not None else "")
        if not key:
            continue
        canon = _ALIAS_COLONNES.get(key)
        if canon and canon not in mapping:
            mapping[canon] = i  # 0-based

    has_jour = "JOUR" in mapping
    has_album = "ALBUM" in mapping
    has_titre2 = "TITRE_2" in mapping
    has_titre3 = "TITRE_3" in mapping

    if has_jour:
        profil = PROFIL_LAURENT if has_album else PROFIL_VINCE
    elif has_titre2 or has_titre3:
        profil = PROFIL_MARY
    else:
        # Fallback conservateur : vince (format le plus simple)
        profil = PROFIL_VINCE

    return FormatDetected(
        profil=profil,
        colonnes=mapping,
        colonnes_brutes=brut,
        feuille=feuille_nom,
    )


# ---------------------------------------------------------------------------
# Extraction des titres
# ---------------------------------------------------------------------------

@dataclass
class TitreExtrait:
    artiste: str
    titre: str
    annee: Optional[int]
    album: Optional[str]
    semaine: Optional[str]
    jour: Optional[str]
    feuille: str
    ligne: int  # 1-based (ligne dans la Sheet d'origine, en-tête = ligne 1)
    infos: Optional[str] = None


_JOURS_VALIDES = {"L", "MA", "ME", "J", "V", "S", "D"}


def _cellule_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _cellule_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def _est_ligne_bruit(semaine: str, jour: str, artiste: str, titre: str) -> bool:
    """Filtre les lignes non pertinentes : en-têtes répétés, liens, séparateurs."""
    if not artiste and not titre:
        return True
    # Lien vers la base musicale (cas Vince / Laurent)
    if "lien vers" in (semaine + " " + artiste).lower():
        return True
    # Marqueur de semaine purement décoratif
    if artiste.lower().startswith("c'est par ici"):
        return True
    return False


def extraire_titres_depuis_valeurs(
    valeurs: List[List[Any]],
    fmt: FormatDetected,
    semaine_filtrer: Optional[str] = None,
) -> List[TitreExtrait]:
    """
    Parcourt les valeurs renvoyées par `gspread.worksheet.get_all_values()`
    et extrait tous les TitreExtrait valides.

    Paramètres :
      - valeurs         : List[List[Any]] (1 liste par ligne, 1re ligne = en-têtes)
      - fmt             : FormatDetected (issu de detecter_format sur valeurs[0])
      - semaine_filtrer : si fourni (ex "23"), ne renvoie que les titres de cette
                          semaine. Pour le profil laurent, "OK" est un marqueur
                          de validation humaine (accepté comme semaine courante
                          si semaine_filtrer=None).

    Notes :
      - Cette fonction ne dépend PAS d'openpyxl. Elle accepte n'importe quelle
        structure List[List] (gspread, csv, etc.).
      - Pour le profil mary, TITRE 2 et TITRE 3 sont extraits comme titres
        séparés (un artiste peut proposer plusieurs titres sur la même ligne).
    """
    titres: List[TitreExtrait] = []
    if not valeurs:
        return titres

    cols = fmt.colonnes
    col_semaine = cols.get("SEMAINE")
    col_jour = cols.get("JOUR")
    col_artiste = cols.get("ARTISTE")
    col_titre1 = cols.get("TITRE_1")
    col_titre2 = cols.get("TITRE_2")
    col_titre3 = cols.get("TITRE_3")
    col_annee = cols.get("ANNEE")
    col_album = cols.get("ALBUM")
    col_infos = cols.get("INFOS")

    # On commence à l'index 1 (index 0 = en-têtes)
    for row_idx_0based, row in enumerate(valeurs[1:], start=2):  # row_idx = 2,3,4... (1-based dans la Sheet)
        # Sécuriser la longueur de la ligne
        def _get(col_idx: Optional[int]) -> Any:
            if col_idx is None or col_idx >= len(row):
                return None
            return row[col_idx]

        semaine = _cellule_str(_get(col_semaine))
        jour = _cellule_str(_get(col_jour))
        artiste = _cellule_str(_get(col_artiste))

        if _est_ligne_bruit(semaine, jour, artiste, ""):
            continue

        # Filtrage par semaine (si demandé)
        if semaine_filtrer is not None:
            sem_norm = str(semaine).strip().lower()
            sem_filt_norm = str(semaine_filtrer).strip().lower()
            if sem_norm != sem_filt_norm:
                # Cas spécial laurent : "OK" valide tout si semaine_filtrer=None
                # (si semaine_filtrer est fourni, on filtre strictement)
                continue

        # Extraction des 1 à 3 titres par ligne
        titres_ligne: List[str] = []
        if col_titre1 is not None:
            t1 = _cellule_str(_get(col_titre1))
            if t1:
                titres_ligne.append(t1)
        if col_titre2 is not None:
            t2 = _cellule_str(_get(col_titre2))
            if t2:
                titres_ligne.append(t2)
        if col_titre3 is not None:
            t3 = _cellule_str(_get(col_titre3))
            if t3:
                titres_ligne.append(t3)

        if not titres_ligne:
            continue

        annee = _cellule_int(_get(col_annee))
        album = _cellule_str(_get(col_album))
        album = album or None
        infos = _cellule_str(_get(col_infos))
        infos = infos or None

        # Validation du jour (si présent)
        jour_val = jour if jour in _JOURS_VALIDES else (jour or None)

        # SEMAINE peut être un entier (numéro), "OK", ou du texte
        sem_val: Optional[str]
        if not semaine:
            sem_val = None
        elif semaine.upper() == "OK":
            sem_val = "OK"
        else:
            sem_val = semaine

        for titre in titres_ligne:
            titres.append(TitreExtrait(
                artiste=artiste,
                titre=titre,
                annee=annee,
                album=album,
                semaine=sem_val,
                jour=jour_val,
                feuille=fmt.feuille,
                ligne=row_idx_0based,
                infos=infos,
            ))

    return titres


def extraire_titres_feuille(
    ws: Any,
    fmt: FormatDetected,
    semaine_filtrer: Optional[str] = None,
) -> List[TitreExtrait]:
    """
    Wrapper qui accepte un worksheet **gspread** ou **openpyxl**.

    - gspread : appelle `ws.get_all_values()` (production)
    - openpyxl : itère sur les cellules (tests locaux uniquement)

    En production, préférer `extraire_titres_depuis_valeurs()` directement
    après avoir récupéré les valeurs via gspread.
    """
    # Détection du type de worksheet
    if hasattr(ws, 'get_all_values') and callable(ws.get_all_values):
        # gspread
        valeurs = ws.get_all_values()
        return extraire_titres_depuis_valeurs(valeurs, fmt, semaine_filtrer)
    else:
        # openpyxl (tests locaux) — convertir en List[List]
        valeurs = []
        for row in ws.iter_rows(values_only=True):
            valeurs.append(list(row))
        return extraire_titres_depuis_valeurs(valeurs, fmt, semaine_filtrer)


# ---------------------------------------------------------------------------
# Validation contre songs (moteur de matching flou — réutilisation du module push)
# ---------------------------------------------------------------------------

@dataclass
class ResultatValidationPool:
    titre_extrait: TitreExtrait
    statut: str  # 'valide' | 'rejete' | 'doublon'
    song_id: Optional[int] = None
    score: Optional[int] = None
    motif: Optional[str] = None


def valider_titres_contre_songs(
    titres: List[TitreExtrait],
    conn_params: Dict[str, Any],
    pool_id: int,
    source: str,  # nom du tiers (vince / laurent / mary)
) -> List[ResultatValidationPool]:
    """
    Valide chaque titre extrait contre la table `songs` en utilisant le moteur
    de matching flou à 5 étapes (strict → principal → nu → normalisé → mots-clés).

    Réutilise 95% du code de `executer_prefill_sheets` (mode Push). La seule
    différence est la destination : au lieu de pousser vers AzuraCast, on persiste
    dans `airvs_pool_titres`.

    TODO IMPLEMENTATION : à brancher sur le moteur existant `executer_prefill_sheets`
    en factorisant la fonction de matching dans `_match_titre_songs(artiste, titre, annee)`.
    """
    logger.info(
        "valider_titres_contre_songs: %d titres à valider pour pool_id=%d source=%s",
        len(titres), pool_id, source,
    )
    return []


# ---------------------------------------------------------------------------
# Helper : parcourir toutes les feuilles d'un classeur Google Sheet
# ---------------------------------------------------------------------------

def extraire_titres_classeur_gspread(
    gc_client: Any,
    sheet_id: str,
    feuilles_a_parcourir: Optional[List[str]] = None,
    semaine_filtrer: Optional[str] = None,
    log_callback=None,
) -> List[TitreExtrait]:
    """
    Ouvre un classeur Google Sheet par son ID et parcourt toutes ses feuilles
    (ou une sélection) pour extraire les titres.

    Paramètres :
      - gc_client            : client gspread autorisé (gspread.authorize(creds))
      - sheet_id             : ID du Google Sheet (ex "1pItBk1boYtu...")
      - feuilles_a_parcourir : si None, parcourt toutes les feuilles du classeur.
                               Sinon, ne parcourt que les feuilles nommées.
      - semaine_filtrer      : si fourni, filtre par semaine sur chaque feuille.
      - log_callback         : fonction (str) -> None pour logger le progress.

    Retourne la liste agrégée de TitreExtrait de toutes les feuilles.
    """
    def _log(msg: str):
        if log_callback:
            log_callback(msg)
        else:
            logger.info(msg)

    try:
        classeur = gc_client.open_by_key(sheet_id)
    except Exception as e:
        _log(f"❌ Impossible d'ouvrir le Google Sheet {sheet_id} : {e}")
        return []

    worksheets = classeur.worksheets()
    if feuilles_a_parcourir:
        worksheets = [ws for ws in worksheets if ws.title in feuilles_a_parcourir]

    _log(f"📂 Classeur ouvert : {len(worksheets)} feuille(s) à parcourir")

    titres_tous: List[TitreExtrait] = []
    for ws in worksheets:
        try:
            valeurs = ws.get_all_values()
            if not valeurs:
                _log(f"  • {ws.title} : vide, ignorée")
                continue
            fmt = detecter_format(ws.title, valeurs[0])
            titres_feuille = extraire_titres_depuis_valeurs(valeurs, fmt, semaine_filtrer)
            _log(f"  • {ws.title} : profil={fmt.profil}, {len(titres_feuille)} titre(s) extrait(s)")
            titres_tous.extend(titres_feuille)
        except Exception as e:
            _log(f"  • {ws.title} : ERREUR {e}")

    _log(f"✅ Total : {len(titres_tous)} titre(s) extrait(s) depuis {len(worksheets)} feuille(s)")
    return titres_tous


# ---------------------------------------------------------------------------
# Diagnostic rapide (CLI — utilise openpyxl pour tests locaux hors production)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    try:
        import openpyxl
    except ImportError:
        print("Pour les tests locaux : pip install openpyxl")
        sys.exit(1)
    from pathlib import Path

    if len(sys.argv) < 2:
        print("Usage: python validation_pool_multiformat.py <fichier.xlsx> [semaine]")
        print("")
        print("NOTE : ce test CLI utilise openpyxl pour lire un fichier .xlsx LOCAL.")
        print("       En production, le worker utilise gspread (Google Drive).")
        print("       Voir extraire_titres_classeur_gspread() pour le chemin production.")
        sys.exit(1)

    path = Path(sys.argv[1])
    semaine = sys.argv[2] if len(sys.argv) > 2 else None

    wb = openpyxl.load_workbook(path, data_only=True)
    total = 0
    for sn in wb.sheetnames:
        ws = wb[sn]
        # Lire la première ligne non vide comme en-tête
        en_tetes = []
        for r in range(1, min(5, ws.max_row + 1)):
            row_vals = [ws.cell(r, c).value for c in range(1, min(ws.max_column, 12) + 1)]
            if any(v is not None and str(v).strip() for v in row_vals):
                en_tetes = row_vals
                break
        fmt = detecter_format(sn, en_tetes)
        print(f"\n=== Feuille: {sn} ===")
        print(f"  Profil détecté : {fmt.profil}")
        print(f"  Colonnes      : {fmt.colonnes}")
        titres = extraire_titres_feuille(ws, fmt, semaine_filtrer=semaine)
        print(f"  Titres extraits : {len(titres)}")
        for t in titres[:10]:
            print(f"    - [{t.feuille}:L{t.ligne}] {t.artiste} - {t.titre} ({t.annee})")
        if len(titres) > 10:
            print(f"    ... et {len(titres)-10} autres")
        total += len(titres)
    print(f"\nTotal extraits : {total}")
