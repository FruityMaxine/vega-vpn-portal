#!/usr/bin/env python3
"""Vega VPN 资源统计 long-running daemon · 直读 cgroup v2 + /proc 网络
每 1 秒采样一次,写 current.json + history.json (rolling 300 条 = 5 min)

v0.6 扩展:
- 每秒额外拉 Marzban /api/users,记录每 user used_traffic 历史
- 写 user-traffic.json (per-user rolling 300)
- 写 traffic-total.json (项目级 monthly + lifetime 累计字节)
"""
import json
import os
import subprocess
import time
import urllib.request
import urllib.parse
import datetime
from pathlib import Path

OUT_DIR = Path(os.environ.get('STATS_OUT_DIR', './portal/html/stats'))
OUT_DIR.mkdir(parents=True, exist_ok=True)
CUR = OUT_DIR / 'current.json'
HIST = OUT_DIR / 'history.json'
USER_TRAFFIC = OUT_DIR / 'user-traffic.json'
TRAFFIC_TOTAL = OUT_DIR / 'traffic-total.json'

CONTAINERS = ['vpn-marzban', 'vpn-portal', 'vpn-signup']
IFACE = 'eth0'
HIST_MAX = 300   # 5 分钟 @ 1s
MZ_BASE = 'http://127.0.0.1:8000'
MZ_USER = 'admin'
MZ_PASS = os.environ.get('MARZBAN_SUDO_PASSWORD', '')
MZ_POLL_EVERY = 5  # Marzban user 拉取频率(秒) — 不必每秒

def cgroup_path(container):
    cid = subprocess.run(['docker', 'inspect', '--format', '{{.Id}}', container],
                         capture_output=True, text=True, timeout=3).stdout.strip()
    if not cid:
        return None
    return Path(f'/sys/fs/cgroup/system.slice/docker-{cid}.scope')

def read_mem(cgp):
    try:
        return int((cgp / 'memory.current').read_text().strip())
    except: return 0

def read_cpu_usec(cgp):
    try:
        for line in (cgp / 'cpu.stat').read_text().splitlines():
            if line.startswith('usage_usec'):
                return int(line.split()[1])
    except: pass
    return 0

def read_iface(name):
    base = Path(f'/sys/class/net/{name}/statistics')
    try:
        return int((base / 'rx_bytes').read_text()), int((base / 'tx_bytes').read_text())
    except: return 0, 0

CGPS = {}
PREV_CPU = {}
PREV_NET = {'ts': 0, 'rx': 0, 'tx': 0}
CORES = os.cpu_count() or 1

# Marzban
MZ_TOKEN = {'token': None, 'exp': 0}
MZ_USERS_CACHE = {'ts': 0, 'users': []}  # 最近一次拉到的 users
# per-user history: { username: [{ts, used_bytes}, ...] }
USER_HIST = {}
if USER_TRAFFIC.exists():
    try:
        USER_HIST = json.loads(USER_TRAFFIC.read_text()).get('users', {})
    except: USER_HIST = {}

# 项目级累计
TRAFFIC_STATE = {'monthly_bytes': 0, 'lifetime_bytes': 0, 'month': None, 'prev_rx': None, 'prev_tx': None}
if TRAFFIC_TOTAL.exists():
    try:
        d = json.loads(TRAFFIC_TOTAL.read_text())
        TRAFFIC_STATE['monthly_bytes'] = int(d.get('monthly_bytes', 0))
        TRAFFIC_STATE['lifetime_bytes'] = int(d.get('lifetime_bytes', 0))
        TRAFFIC_STATE['month'] = d.get('month')
    except: pass


def mz_token():
    now = time.time()
    if MZ_TOKEN['token'] and now < MZ_TOKEN['exp'] - 60:
        return MZ_TOKEN['token']
    data = urllib.parse.urlencode({'username': MZ_USER, 'password': MZ_PASS}).encode()
    req = urllib.request.Request(f'{MZ_BASE}/api/admin/token', data=data,
                                 headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(req, timeout=4) as r:
        d = json.loads(r.read())
    MZ_TOKEN['token'] = d['access_token']
    MZ_TOKEN['exp'] = now + 23 * 3600
    return MZ_TOKEN['token']


def mz_users():
    t = mz_token()
    req = urllib.request.Request(f'{MZ_BASE}/api/users', headers={'Authorization': f'Bearer {t}'})
    with urllib.request.urlopen(req, timeout=5) as r:
        d = json.loads(r.read())
    return d.get('users', [])


def refresh_cgps():
    for c in CONTAINERS:
        if c not in CGPS or not CGPS[c] or not (CGPS[c] / 'memory.current').exists():
            CGPS[c] = cgroup_path(c)


def sample():
    now = time.time()
    refresh_cgps()
    out = {}
    for c, cgp in CGPS.items():
        if not cgp:
            out[c] = {'mem_mb': 0, 'cpu_pct': 0}
            continue
        mem = read_mem(cgp)
        cpu = read_cpu_usec(cgp)
        prev_cpu, prev_ts = PREV_CPU.get(c, (cpu, now))
        dt = max(now - prev_ts, 0.01)
        d_cpu = max(cpu - prev_cpu, 0)
        cpu_pct = (d_cpu / 1_000_000) / (dt * CORES) * 100
        PREV_CPU[c] = (cpu, now)
        out[c] = {'mem_mb': round(mem / 1024 / 1024, 1), 'cpu_pct': round(cpu_pct, 2)}

    rx, tx = read_iface(IFACE)
    dt = max(now - PREV_NET['ts'], 0.01) if PREV_NET['ts'] else 0
    rx_mbps = (rx - PREV_NET['rx']) * 8 / 1_000_000 / dt if dt > 0 and dt < 60 else 0
    tx_mbps = (tx - PREV_NET['tx']) * 8 / 1_000_000 / dt if dt > 0 and dt < 60 else 0

    # 累计项目级流量(以 iface 增量为口径)
    if PREV_NET['ts'] and 0 < dt < 60:
        d_rx = max(rx - PREV_NET['rx'], 0)
        d_tx = max(tx - PREV_NET['tx'], 0)
        delta = d_rx + d_tx
        cur_month = datetime.datetime.now().strftime('%Y-%m')
        if TRAFFIC_STATE['month'] != cur_month:
            TRAFFIC_STATE['month'] = cur_month
            TRAFFIC_STATE['monthly_bytes'] = 0
        TRAFFIC_STATE['monthly_bytes'] += delta
        TRAFFIC_STATE['lifetime_bytes'] += delta

    PREV_NET.update({'ts': now, 'rx': rx, 'tx': tx})

    total_mem = sum(v['mem_mb'] for v in out.values())
    total_cpu = sum(v['cpu_pct'] for v in out.values())

    return {
        'ts': int(now),
        'ts_human': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'containers': out,
        'vega_vpn_total': {'mem_mb': round(total_mem, 1), 'cpu_pct': round(total_cpu, 2)},
        'traffic': {
            'rx_mbps': round(rx_mbps, 2),
            'tx_mbps': round(tx_mbps, 2),
            'monthly_cap_tb': 20,
            'bandwidth_peak_gbps': 1,
            'monthly_bytes': TRAFFIC_STATE['monthly_bytes'],
            'lifetime_bytes': TRAFFIC_STATE['lifetime_bytes'],
        }
    }


def poll_marzban(now):
    """每 MZ_POLL_EVERY 秒拉一次,更新 USER_HIST"""
    if now - MZ_USERS_CACHE['ts'] < MZ_POLL_EVERY:
        return
    try:
        users = mz_users()
        MZ_USERS_CACHE['ts'] = now
        MZ_USERS_CACHE['users'] = users
        ts_int = int(now)
        seen = set()
        for u in users:
            name = u.get('username')
            if not name:
                continue
            seen.add(name)
            used = int(u.get('used_traffic') or 0)
            arr = USER_HIST.setdefault(name, [])
            arr.append({'ts': ts_int, 'used': used})
            del arr[:-HIST_MAX]
        # 删 Marzban 中已不存在的 user 的历史
        for stale in [n for n in USER_HIST.keys() if n not in seen]:
            del USER_HIST[stale]
    except Exception as e:
        import sys
        print(f'[mz poll err] {e}', file=sys.stderr, flush=True)


def main():
    history = []
    if HIST.exists():
        try: history = json.loads(HIST.read_text())[-HIST_MAX:]
        except: history = []
    sample()
    time.sleep(1)
    write_user_counter = 0
    while True:
        try:
            data = sample()
            CUR.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            history.append({
                'ts': data['ts'],
                'containers': data['containers'],
                'traffic': {'rx_mbps': data['traffic']['rx_mbps'], 'tx_mbps': data['traffic']['tx_mbps']}
            })
            history = history[-HIST_MAX:]
            HIST.write_text(json.dumps(history, ensure_ascii=False))

            poll_marzban(time.time())
            write_user_counter += 1
            if write_user_counter >= 5:  # 每 5 秒落一次盘减 IO
                USER_TRAFFIC.write_text(json.dumps({
                    'users': USER_HIST,
                    'samples': sum(len(v) for v in USER_HIST.values()),
                    'updated_at': int(time.time()),
                }, ensure_ascii=False))
                TRAFFIC_TOTAL.write_text(json.dumps({
                    'monthly_bytes': TRAFFIC_STATE['monthly_bytes'],
                    'lifetime_bytes': TRAFFIC_STATE['lifetime_bytes'],
                    'month': TRAFFIC_STATE['month'],
                    'updated_at': int(time.time()),
                }, ensure_ascii=False))
                write_user_counter = 0
        except Exception as e:
            import sys; print(f'[err] {e}', file=sys.stderr, flush=True)
        time.sleep(1)


if __name__ == '__main__':
    main()
