# monitor_cpu_vps.sh — Monitoring CPU VPS OVH1

> Script de surveillance léère dédié à la mesure de l'impact CPU du formulaire
> programmes AIRVS (Gunicorn/Flask) sur le VPS OVH1 (54.37.38.117).

---

## Pourquoi ce script ?

Après les optimisations G1 (anti-clic multiple) et G3 (réduction matching 5→3 passes)
déployées sur `app_vps_ovh_1.py`, il faut vérifier que le CPU du VPS redescend.

Ce script prélève **toutes les 10 secondes** :

| Métrique | Source | Intérêt |
|---|---|---|
| `CPU%` | `top -b -n1` | Charge totale du VPS — permet de voir si les 70-80% baissent |
| `GUNI%` | `ps aux` filtré sur `gunicorn` | **La métrique clé** : CPU consommé par le formulaire uniquement |
| `MEM%` | `free` | Vérifier que les optimisations ne créent pas de fuite mémoire |
| `LOAD` | `/proc/loadavg` | Charge moyenne 1 min — à comparer aux 2 vCPU |
| `MATCH_C` | `journalctl` filtré `POST /api/match` | Nombre cumulé de recherches de matching |
| `NOTE` | `journalctl` filtré `POST /soumettre` | Activité par intervalle (matchs et soumissions) |

---

## Prérequis

- Accès SSH au VPS OVH1 (`ubuntu@54.37.38.117`) avec clé configurée
- Droit `sudo` pour lire les journaux systemd (`journalctl -u airvs-programmes`)
- Aucune dépendance supplémentaire (bash, top, free, ps sont standards)

---

## Installation — tout se fait sur le VPS

Se connecter au VPS, puis coller le bloc ci-dessous dans le terminal.
Cela crée le script directement sur le VPS, sans rien copier depuis ton PC :

```bash
ssh ubuntu@54.37.38.117
```

Puis sur le VPS, exécuter ce bloc complet (copier/coller d'un coup) :

```bash
cat > /tmp/monitor_cpu_vps.sh << 'SCRIPT'
#!/bin/bash
# Monitoring CPU VPS OVH1 — Gunicorn/Flask Programmes
# Usage: nohup bash /tmp/monitor_cpu_vps.sh &
# Arrêt: kill $(cat /tmp/monitor_cpu_vps.pid)

LOG="/tmp/cpu_monitor_$(date +%Y%m%d_%H%M%S).log"
INTERVAL=10
PIDFILE="/tmp/monitor_cpu_vps.pid"

echo $$ > "$PIDFILE"

DUREE_MIN=0
LIGNE=0
MATCH_COUNT=0
SUBMIT_COUNT=0
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
    local guni_pct=$(ps aux | grep '[g]unicorn' | awk '{sum+=$3} END {printf "%.1f", sum}')
    local recent_matches=$(sudo journalctl -u airvs-programmes --since "${INTERVAL}s ago" --no-pager -q 2>/dev/null | grep -c 'POST /api/match' 2>/dev/null || echo 0)
    local recent_submits=$(sudo journalctl -u airvs-programmes --since "${INTERVAL}s ago" --no-pager -q 2>/dev/null | grep -c 'POST /soumettre' 2>/dev/null || echo 0)
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

header
trap "echo ''; echo 'Arrêt — $(date)'; echo 'Résultats : $LOG'; rm -f $PIDFILE; exit 0" INT TERM
echo "Moniteur démarré — LOG=$LOG (intervalle ${INTERVAL}s)"
echo "Utilisez le formulaire. Arrêt : kill \$(cat $PIDFILE)"
echo ""

while true; do
    snapshot
    sleep "$INTERVAL"
    LIGNE=$((LIGNE + 1))
    if [ $((LIGNE % 6)) -eq 0 ]; then
        local_cpu=$(tail -1 "$LOG" | awk '{print $3}')
        local_guni=$(tail -1 "$LOG" | awk '{print $4}')
        echo "[${DUREE_MIN}min] CPU=$local_cpu  Gunicorn=$local_guni  Matches total=$MATCH_COUNT"
    fi
    DUREE_MIN=$((DUREE_MIN + INTERVAL / 60))
done
SCRIPT
chmod +x /tmp/monitor_cpu_vps.sh
echo "Script installé : /tmp/monitor_cpu_vps.sh"
```

---

## Utilisation

### Démarrer le monitoring

```bash
nohup bash /tmp/monitor_cpu_vps.sh &
```

Le terminal affiche un résumé chaque **minute** :

```
Moniteur démarré — LOG=/tmp/cpu_monitor_20260831_143000.log (intervalle 10s)
Utilisez le formulaire. Arrêt : kill $(cat /tmp/monitor_cpu_vps.pid)

[1min] CPU=72%  Gunicorn=3.2%  Matches total=5
[2min] CPU=68%  Gunicorn=1.1%  Matches total=8
[3min] CPU=74%  Gunicorn=0.0%  Matches total=8
```

Tu peux fermer le terminal SSH — le script continue grâce à `nohup`.

### Pendant le monitoring

1. Ouvrir le formulaire `https://programmes.airvs.fr` dans ton navigateur
2. Utiliser le formulaire **normalement** pendant **1 à 2 heures** :
   - Saisir des artistes/titres (chaque saisie déclenche un match)
   - Enregistrer quelques soumissions
   - Tester des recherches variées (titres connus, inconnus, avec/sans feat)
3. Plus tu utilises le formulaire, plus les données seront représentatives

### Arrêter le monitoring

```bash
kill $(cat /tmp/monitor_cpu_vps.pid)
```

Ou si le PID fichier n'existe plus :

```bash
pkill -f monitor_cpu_vps.sh
```

### Récupérer les résultats

```bash
# Depuis ton PC local :
scp ubuntu@54.37.38.117:/tmp/cpu_monitor_*.log ./
```

Ouvrir le fichier `.log` avec un éditeur de texte ou un tableur.

---

## Compréhension du fichier de résultat

Exemple de contenu du fichier log :

```
================================================================
 Moniteur CPU VPS OVH1 — 2026-08-31 14:30:00
 Intervalle : 10s | Coeurs : 2 | PID : 12345
 Fichier log : /tmp/cpu_monitor_20260831_143000.log
================================================================
TIME   MIN    CPU%    GUNI%   MEM%    LOAD    MATCH_C   NOTE
------------------------------------------------------------------------
14:30:00   0     72%     0.0%    61%     1.40    0         
14:30:10   0     71%     3.2%    61%     1.35    1         1 match(es)
14:30:20   0     69%     1.1%    61%     1.30    2         1 match(es)
14:30:30   1     70%     0.0%    61%     1.32    2         
14:30:40   1     68%     5.4%    62%     1.45    4         2M/1S
```

### Lecture des colonnes

| Colonne | Description |
|---|---|
| `TIME` | Heure du prélèvement (toutes les 10s) |
| `MIN` | Minutes écoulées depuis le démarrage |
| `CPU%` | **CPU total** du VPS (tous processus confondus) |
| `GUNI%` | **CPU Gunicorn** — la consommation du formulaire uniquement |
| `MEM%` | Pourcentage de RAM utilisée |
| `LOAD` | Charge moyenne sur 1 minute (sur 2 vCPU, >2 = saturé) |
| `MATCH_C` | Nombre **cumulé** de recherches de matching depuis le début |
| `NOTE` | Activité détectée durant les 10 dernières secondes : `M` = match, `S` = soumission |

### Analyse rapide

- **GUNI% < 5%** en pic lors d'un match → les optimisations G3 sont efficaces
- **GUNI% > 15%** en pic lors d'un match → le matching reste coûteux, envisager G5 (cache) ou G6 (délégation worker)
- **CPU% global < 60%** au repos → le VPS respire, les autres services (Liquidsoap, Icecast) tournent normalement
- **CPU% global > 80%** au repos → le problème ne vient pas du formulaire, c'est un autre service (vérifier avec `top -b -n1 | head -20`)

---

## Paramétrage

Pour modifier l'intervalle de prélèvement, éditer la variable au début du script :

```bash
INTERVAL=10  # secondes entre chaque prélèvement (défaut : 10)
```

| Valeur | Usage recommandé |
|---|---|
| `5` | Test court et précis (15-30 min) — plus de données, log plus volumineux |
| `10` | **Recommandé** — bon compromis précision/volume |
| `30` | Monitoring longue durée (4h+) — log léger |
| `60` | Surveillance de fond sur une journée complète |

---

## Dépannage

### Le script ne démarre pas

```bash
bash --version
ls -la /tmp/monitor_cpu_vps.sh
bash -x /tmp/monitor_cpu_vps.sh
```

### Pas de données MATCH_C

Les matchs sont comptés via les journaux systemd. Vérifier :

```bash
sudo journalctl -u airvs-programmes --since "1 min ago" --no-pager
sudo systemctl is-active airvs-programmes
```

### GUNI% reste à 0.0%

C'est normal **entre les recherches**. Gunicorn ne consomme du CPU que pendant les requêtes.
Pour vérifier qu'un match produit bien de la charge, saisir un titre dans le formulaire
et observer la ligne suivante dans le résumé console.

### Plusieurs instances du script tournent

```bash
pkill -f monitor_cpu_vps.sh
rm -f /tmp/monitor_cpu_vps.pid
nohup bash /tmp/monitor_cpu_vps.sh &
```

---

## Commandes complémentaires utiles

Pendant ou après le monitoring, ces commandes aident à affiner l'analyse :

```bash
# Top 10 des processus par CPU (instantané)
ssh ubuntu@54.37.38.117 "top -b -n1 -o %CPU | head -15"

# CPU Gunicorn en temps réel (surveillance continue)
ssh ubuntu@54.37.38.117 "watch -n 2 'ps aux | grep gunicorn | grep -v grep'"

# Dernières requêtes du formulaire
ssh ubuntu@54.37.38.117 "sudo journalctl -u airvs-programmes --since '10 min ago' --no-pager | grep -E 'POST|GET'"

# Temps de réponse moyen du formulaire
ssh ubuntu@54.37.38.117 "curl -sk -o /dev/null -w 'HTTP %{http_code} — %{time_total}s\\n' https://programmes.airvs.fr/login"

# Nombre de workers Gunicorn et leur PID
ssh ubuntu@54.37.38.117 "ps aux | grep '[g]unicorn'"

# Statut du service
ssh ubuntu@54.37.38.117 "sudo systemctl status airvs-programmes --no-pager -l | head -20"
```