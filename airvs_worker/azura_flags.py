"""
airvs_dashboard_v2/utils/azura_flags.py

Helpers pour la gestion des flags AZURA_*_S6/S7 dans radiodb.songs.comments.

Compatible avec l'ancien format 'IN_AZURACAST' et le nouveau format
'AZURA_PUSHED_S*', 'AZURA_ACTIVE_S*', 'AZURA_RETIRED_S*', 'AZURA_REMOVED_S*'.

À utiliser partout où l'on doit savoir si un titre est dans AzuraCast.

Migration Phase 0 (2026-11) :
- L'ancien format 'IN_AZURACAST' reste accepté en lecture (legacy)
- Les nouvelles écritures utilisent le format AZURA_*_S6_S7
- Cron de resync (Phase 6) mettra à jour les flags en arrière-plan
"""


def is_in_azuracast(comments) -> bool:
    """
    Retourne True si le titre est présent dans AzuraCast (diffusable).

    Compatible avec :
    - Ancien format : 'IN_AZURACAST'
    - Nouveau format : 'AZURA_PUSHED_S*', 'AZURA_ACTIVE_S*', 'AZURA_RETIRED_S*'
      (mais PAS 'AZURA_REMOVED_*' car le fichier n'existe plus)

    >>> is_in_azuracast('IN_AZURACAST')
    True
    >>> is_in_azuracast('AZURA_ACTIVE_S6_S7')
    True
    >>> is_in_azuracast('AZURA_REMOVED_S7')
    False
    >>> is_in_azuracast('')
    False
    >>> is_in_azuracast(None)
    False
    """
    if not comments:
        return False
    s = str(comments).strip()
    if s == 'IN_AZURACAST':
        return True
    if s.startswith('AZURA_'):
        # AZURA_REMOVED_* signifie que le fichier n'est plus dans AzuraCast
        if s.startswith('AZURA_REMOVED'):
            return False
        return True
    return False


def get_azura_state(comments) -> dict:
    """
    Retourne l'état AzuraCast détaillé d'un titre.

    Returns:
        {
            'flag': str,         # valeur exacte de comments
            'in_azura': bool,    # True si présent dans AzuraCast (diffusable)
            'state': str|None,   # 'PUSHED', 'ACTIVE', 'RETIRED', 'REMOVED' ou None
            'stations': list,    # [6], [7], [6,7] ou []
            'legacy': bool,      # True si ancien format 'IN_AZURACAST'
        }

    >>> get_azura_state('IN_AZURACAST')['state']
    'PUSHED'
    >>> get_azura_state('AZURA_ACTIVE_S7')['stations']
    [7]
    >>> get_azura_state('AZURA_REMOVED_S6_S7')['in_azura']
    False
    """
    if not comments:
        return {
            'flag': '', 'in_azura': False, 'state': None,
            'stations': [], 'legacy': False
        }

    s = str(comments).strip()

    # Ancien format
    if s == 'IN_AZURACAST':
        return {
            'flag': s,
            'in_azura': True,
            'state': 'PUSHED',  # approximation (on ne sait pas s'il est actif)
            'stations': [6, 7],  # on suppose les deux stations
            'legacy': True
        }

    # Nouveau format : AZURA_<STATE>_S6_S7 ou AZURA_<STATE>_S6 ou AZURA_<STATE>_S7
    if not s.startswith('AZURA_'):
        return {
            'flag': s, 'in_azura': False, 'state': None,
            'stations': [], 'legacy': False
        }

    parts = s.split('_')
    # parts[0] = 'AZURA'
    # parts[1] = state ('PUSHED', 'ACTIVE', 'RETIRED', 'REMOVED')
    # parts[2:] = stations (['S6'], ['S7'], ['S6', 'S7'])

    if len(parts) < 3:
        return {
            'flag': s, 'in_azura': False, 'state': None,
            'stations': [], 'legacy': False
        }

    state = parts[1]
    stations_str = '_'.join(parts[2:])  # 'S6', 'S7', 'S6_S7'

    stations = []
    if 'S6' in stations_str:
        stations.append(6)
    if 'S7' in stations_str:
        stations.append(7)

    in_azura = state in ('PUSHED', 'ACTIVE', 'RETIRED')  # pas REMOVED

    return {
        'flag': s,
        'in_azura': in_azura,
        'state': state,
        'stations': stations,
        'legacy': False
    }


def format_azura_flag(state: str, stations: list) -> str:
    """
    Construit un flag AZURA_*_S* à partir d'un état et d'une liste de stations.

    Args:
        state: 'PUSHED', 'ACTIVE', 'RETIRED', 'REMOVED'
        stations: liste de station IDs (ex: [6, 7])

    Returns:
        'AZURA_ACTIVE_S6_S7' (stations triées et uniques)

    >>> format_azura_flag('ACTIVE', [7, 6])
    'AZURA_ACTIVE_S6_S7'
    >>> format_azura_flag('PUSHED', [7])
    'AZURA_PUSHED_S7'
    """
    if state not in ('PUSHED', 'ACTIVE', 'RETIRED', 'REMOVED'):
        raise ValueError(f"État AZURA invalide: {state}")

    sorted_stations = sorted(set(stations))
    stations_str = '_'.join(f'S{s}' for s in sorted_stations)
    return f'AZURA_{state}_{stations_str}'


def get_default_flag_after_push(station_id: int) -> str:
    """
    Retourne le flag par défaut à utiliser après un push réussi vers AzuraCast.

    Phase 0 (migration) : on utilise AZURA_PUSHED_S* (prudent, sans affirmation ACTIVE).
    Le cron de resync (Phase 6) affinera vers AZURA_ACTIVE_S* après vérification API.

    Args:
        station_id: 6 ou 7

    Returns:
        'AZURA_PUSHED_S6' ou 'AZURA_PUSHED_S7'
    """
    if station_id not in (6, 7):
        raise ValueError(f"Station ID invalide: {station_id} (doit être 6 ou 7)")
    return f'AZURA_PUSHED_S{station_id}'


def get_default_flag_after_push_multi(stations: list) -> str:
    """
    Retourne le flag par défaut après un push réussi vers plusieurs stations.

    Args:
        stations: liste de station IDs (ex: [6, 7])

    Returns:
        'AZURA_PUSHED_S6_S7' (trié)
    """
    return format_azura_flag('PUSHED', stations)


if __name__ == '__main__':
    # Tests rapides
    import doctest
    doctest.testmod(verbose=True)
