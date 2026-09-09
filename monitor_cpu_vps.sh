#!/bin/bash
# ============================================================
# Monitoring CPU VPS OVH 1 — Spécifique Gunicorn/Flask Programmes
# ============================================================
# Usage sur le VPS :
#   nohup bash monitor_cpu_vps.sh &
#   # puis utiliser le formulaire pendant 1-2h
#   # arrêter avec : kill %1  (ou kill $(cat /tmp/monitor_cpu_vps.pid))
#   # résultats dans : /tmp/cpu_monitor_*.log
# ============================================================

LOG="/tmp/cpu_monitor_$(date +%Y%m%d_%H%M%S).log"
INTERVAL=10  # secondes entre chaque prélèvement
PIDFILE="/tmp/monitor_cpu_vps.pid"

echo $$ > "$PIDFILE"

DUREE_MIN=0
LIGNE=0
MATCH_COUNT=0
SUBMIT_COUNT=0

# Nombre de coeurs
NCORES=$(nproc)

header() {
    echo "" >> "$LOG"
    echo "================================================================" >> "$LOG"
    echo " Moniteur CPU VPS OVH1 — $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG"
    echo " Intervalle : ${INTERVAL}s | Coeurs : $NCORES | PID : $$" >> "$LOG"
    echo " Fichier log : $LOG" >> "$LOG"
    echo "================================================================" >> "$LOG"
    printf "%-6s %-6s %-7s %-7s %-7s %-7s %-9s %s\n" \
        "TIME" "MIN" "CPU%" "GUNI%" "MEM%" "LOAD" "MATCH_C" "NOTE" >> "$LOG"
    echo "------------------------------------------------------------------------" >> "$LOG"
}

snapshot() {
    local ts=$(date '+%H:%M:%S')
    local cpu_total=$(top -b -n1 | head -3 | tail -1 | awk '{print $2}' | tr -d ',')
    local mem_total=$(free | awk '/Mem/{printf "%.0f", $3/$2*100}')
    local load1=$(cat /proc/loadavg | awk '{print $1}')

    # CPU spécifique de gunicorn (tous les workers)
    local guni_pct=$(ps aux | grep '[g]unicorn' | awk '{sum+=$3} END {printf "%.1f", sum}')

    # Compter les requêtes /api/match dans les logs Gunicorn récentes
    local recent_matches=$(sudo journalctl -u airvs-programmes --since "${INTERVAL}s ago" --no-pager -q 2>/dev/null | \
        grep -c 'POST /api/match' 2>/dev/null || echo 0)
    local recent_submits=$(sudo journalctl -u airvs-programmes --since "${INTERVAL}s ago" --no-pager -q 2>/dev/null | \
        grep -c 'POST /soumettre' 2>/dev/null || echo 0)
    MATCH_COUNT=$((MATCH_COUNT + recent_matches))
    SUBMIT_COUNT=$((SUBMIT_COUNT + recent_submits))

    local note=""
    if [ "$recent_matches" -gt 0 ] && [ "$recent_submits" -gt 0 ]; then
        note="${recent_matches}M/${recent_submits}S"
    elif [ "$recent_matches" -gt 0 ]; then
        note="${recent_matches} match(es)"
    elif [ "$recent_submits" -gt 0 ]; then
        note="${recent_submits} submit(s)"
    fi

    printf "%-6s %-6s %-7s %-7s %-7s %-7s %-9s %s\n" \
        "$ts" "$DUREE_MIN" "${cpu_total}%" "${guni_pct}%" "${mem_total}%" "$load1" "$MATCH_COUNT" "$note" >> "$LOG"
}

# --- Boucle principale ---
header

trap "echo ''; echo 'Arrêt — $(date)'; echo 'Résultats : $LOG'; rm -f $PIDFILE; exit 0" INT TERM

echo "Moniteur démarré — LOG=$LOG (intervalle ${INTERVAL}s)"
echo "Utilisez le formulaire. Arrêt : kill \$(cat $PIDFILE)"
echo ""

while true; do
    snapshot
    sleep "$INTERVAL"
    LIGNE=$((LIGNE + 1))
    # Toutes les 6 lignes (1 min), afficher un résumé console
    if [ $((LIGNE % 6)) -eq 0 ]; then
        local_cpu=$(tail -1 "$LOG" | awk '{print $3}')
        local_guni=$(tail -1 "$LOG" | awk '{print $4}')
        echo "[${DUREE_MIN}min] CPU=$local_cpu  Gunicorn=$local_guni  Matches total=$MATCH_COUNT"
    fi
    DUREE_MIN=$((DUREE_MIN + INTERVAL / 60))
done