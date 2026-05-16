# VPN Portal Stack

Self-hosted VPN stack: Marzban multi-protocol server + Animal Island themed user portal + custom FastAPI signup service.

## Architecture

| Component | Image / Runtime | Listen |
|---|---|---|
| **Marzban** | `gozargah/marzban` (pinned digest) | host network: `:8000` (API), `:8443` (VLESS Reality), `:2083` (Shadowsocks-2022) |
| **Portal** | `nginx:1.27-alpine` | `127.0.0.1:3100` |
| **Signup service** | FastAPI (built from `./signup-service`) | `127.0.0.1:3200` |
| **Caddy** | host install | `:443` — reverse proxies all public traffic |
| **Stats daemon** | Python 3 on host | writes JSON to `portal/html/stats/` |

## Quick start

```bash
cp .env.example .env
# Edit .env — fill in all CHANGE_ME / REPLACE_ME values
# Then populate marzban/.env from the same values

# Generate Reality keypair:
docker run --rm teddysun/xray xray x25519

# Edit marzban/xray/xray_config.json:
#   - Replace YOUR_SERVER_IP with your server's public IP
#   - Replace REPLACE_ME_REALITY_PRIVATE_KEY with the generated private key
#   - Replace REPLACE_ME_REALITY_SHORT_ID with a random 8-byte hex string

# Copy and edit Caddy config:
#   Replace REPLACE_ME_ADMIN_PASS in caddy-vpn.conf (same value as CADDY_ADMIN_PASS_TOKEN in .env)
#   Replace vpn.example.com with your domain

docker compose up -d
```

## Ports

| Port | Protocol | Purpose |
|---|---|---|
| 443 | TCP | Caddy -> portal + subscription API |
| 8443 | TCP | VLESS Reality (public) |
| 2083 | TCP+UDP | Shadowsocks-2022 (public) |
| 2087 | TCP | Trojan (public) |
| 2089 | TCP | VMess WS (public) |
| 127.0.0.1:8000 | TCP | Marzban backend (Caddy-proxied) |
| 127.0.0.1:3100 | TCP | Animal Island portal (Caddy-proxied) |
| 127.0.0.1:3200 | TCP | Signup service (Caddy-proxied) |

## Management

```bash
docker compose logs -f marzban       # Marzban logs
docker compose logs -f signup-service
docker compose restart               # restart all
bash backup.sh                       # manual backup
bash portal/html/downloads/refresh.sh  # refresh client mirrors
```

## Security notes

- VPN ports are public by design; all other ports are loopback-only
- Admin UI (`/dashboard/`, `/setup.html`) is protected by a Caddy token cookie gate
- Subscription URLs contain a per-user opaque token; guessing the host does not expose individual subscriptions
- SQLite data lives in `/var/lib/marzban/` (Docker volume)
