#!/usr/bin/env bash
# Vega VPN 资源 + 流量统计 v0.2
# 输出 current.json (最新快照) + history.json (rolling 60 条 = 1 小时)
set -euo pipefail

OUT_DIR="${STATS_OUT_DIR:-./portal/html/stats}"
mkdir -p $OUT_DIR
CUR=$OUT_DIR/current.json
HIST=$OUT_DIR/history.json
PREV=$OUT_DIR/.prev_iface.txt   # 私有

# === 各容器 RAM/CPU
CONTAINERS_JSON=$(docker stats --no-stream --format '{{.Name}}|{{.MemUsage}}|{{.CPUPerc}}' 2>/dev/null \
  | awk -F'|' '
       BEGIN{first=1}
       /^vpn-/ {
         gsub(/MiB|GiB|kB|B/, "", $2)
         split($2, mu, " / ")
         cpu = $3; gsub(/%/, "", cpu)
         if (!first) printf ","
         printf "\"%s\":{\"mem_mb\":%s,\"cpu_pct\":%s}", $1, mu[1], cpu
         first=0
       }
       END{}')

# === 网卡瞬时速率
IFACE=eth0
NOW_RX=$(cat /sys/class/net/$IFACE/statistics/rx_bytes 2>/dev/null || echo 0)
NOW_TX=$(cat /sys/class/net/$IFACE/statistics/tx_bytes 2>/dev/null || echo 0)
NOW_TS=$(date +%s)

PREV_TS=0; PREV_RX=0; PREV_TX=0
if [[ -f $PREV ]]; then
  read PREV_TS PREV_RX PREV_TX < $PREV
fi

DT=$((NOW_TS - PREV_TS))
if [[ $DT -gt 0 && $DT -lt 600 ]]; then
  RX_BPS=$(( (NOW_RX - PREV_RX) / DT ))
  TX_BPS=$(( (NOW_TX - PREV_TX) / DT ))
else
  RX_BPS=0; TX_BPS=0
fi
echo "$NOW_TS $NOW_RX $NOW_TX" > $PREV

# === 合成 current.json
python3 - <<PYEOF
import json, datetime, os
now = $NOW_TS
containers = json.loads('{' + """$CONTAINERS_JSON""" + '}')
total_mem = sum(v.get('mem_mb', 0) for v in containers.values())
total_cpu = sum(v.get('cpu_pct', 0) for v in containers.values())
rx_mbps = round($RX_BPS * 8 / 1_000_000, 2)
tx_mbps = round($TX_BPS * 8 / 1_000_000, 2)

cur = {
  "ts": now,
  "ts_human": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
  "containers": containers,
  "vega_vpn_total": {"mem_mb": round(total_mem, 1), "cpu_pct": round(total_cpu, 2)},
  "traffic": {
    "rx_mbps": rx_mbps, "tx_mbps": tx_mbps,
    "monthly_cap_tb": 20,  # adjust to your provider's monthly cap
    "bandwidth_peak_gbps": 1,
  }
}
open("$CUR", 'w').write(json.dumps(cur, indent=2, ensure_ascii=False))

# 历史：rolling 60 条
hist = []
if os.path.exists("$HIST"):
  try: hist = json.load(open("$HIST"))
  except: hist = []
hist.append({
  "ts": now,
  "containers": {k: v for k, v in containers.items()},
  "traffic": {"rx_mbps": rx_mbps, "tx_mbps": tx_mbps}
})
hist = hist[-60:]
open("$HIST", 'w').write(json.dumps(hist, ensure_ascii=False))
print("OK")
PYEOF
