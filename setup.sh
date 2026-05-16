#!/usr/bin/env bash
set -euo pipefail

# vega-vpn-portal -- First-time setup
# Usage: ./setup.sh

echo "=== vega-vpn-portal Setup ==="
echo ""

# -- Check prerequisites -------------------------------------------------------

for cmd in docker openssl curl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Error: '$cmd' is required but not found."; exit 1; }
done

if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  echo "Error: 'docker compose' plugin or 'docker-compose' is required."
  exit 1
fi

echo "[ok] Prerequisites satisfied."
echo ""

# -- .env ----------------------------------------------------------------------

if [ ! -f .env ]; then
  cp .env.example .env
  echo "[ok] Created .env from .env.example"
else
  echo "[skip] .env already exists"
fi

# -- Prompt for domain ---------------------------------------------------------

echo ""
read -rp "Enter your public domain (e.g. vpn.example.com): " DOMAIN
if [ -z "$DOMAIN" ]; then
  echo "Error: domain cannot be empty."
  exit 1
fi
sed -i "s|PORTAL_BASE_URL=.*|PORTAL_BASE_URL=https://${DOMAIN}|" .env
echo "[ok] PORTAL_BASE_URL set to https://${DOMAIN}"

# -- Generate and write secrets ------------------------------------------------
# Each secret is generated, then written via a helper to avoid pattern-matching issues.

write_env_line() {
  # Usage: write_env_line KEY VALUE
  local key="$1" val="$2"
  sed -i "s|^${key}=.*|${key}=${val}|" .env
}

SVC_JWT="$(openssl rand -hex 32)"
write_env_line "MARZBAN_JWT_SECRET_KEY" "${SVC_JWT}"
echo "[ok] Generated MARZBAN_JWT_SECRET_KEY"

ADMIN_PW="$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)"
write_env_line "MARZBAN_SUDO_PASSWORD" "${ADMIN_PW}"
echo "[ok] Generated MARZBAN_SUDO_PASSWORD"

CADDY_TK="$(openssl rand -hex 24)"
write_env_line "CADDY_ADMIN_PASS_TOKEN" "${CADDY_TK}"
echo "[ok] Generated CADDY_ADMIN_PASS_TOKEN"

echo ""
echo "--- Keys still requiring manual generation ---"
echo "  REALITY_PRIVATE_KEY / REALITY_SHORT_ID:"
echo "    docker run --rm teddysun/xray xray x25519"
echo "  SS_PASSWORD:"
echo "    openssl rand -base64 32"
echo "  YOUR_SERVER_IP: edit .env manually"
echo ""
echo "Edit .env and fill in the above values before continuing."
read -rp "Press Enter when ready, or Ctrl-C to abort..."

# -- Propagate to marzban/.env -------------------------------------------------

if [ ! -f marzban/.env ]; then
  if [ -f marzban/.env.example ]; then
    cp marzban/.env.example marzban/.env
  fi
  grep "^MARZBAN_" .env >> marzban/.env 2>/dev/null || true
  echo "[ok] Created marzban/.env"
fi

# -- Print Caddy config snippet ------------------------------------------------

echo ""
echo "======================================================================"
echo " Add the following block to your Caddyfile."
echo " Replace YOUR_CADDY_TOKEN with the CADDY_ADMIN_PASS_TOKEN from .env"
echo " (run: grep CADDY_ADMIN_PASS_TOKEN .env)"
echo "======================================================================"
echo ""
printf '  %s {\n' "${DOMAIN}"
printf '      handle / {\n'
printf '          reverse_proxy 127.0.0.1:3100\n'
printf '      }\n'
printf '      handle /api/* {\n'
printf '          reverse_proxy 127.0.0.1:8800\n'
printf '      }\n'
printf '      handle /dashboard/* {\n'
printf '          reverse_proxy 127.0.0.1:8000\n'
printf '      }\n'
printf '      handle /sub/* {\n'
printf '          reverse_proxy 127.0.0.1:8000\n'
printf '      }\n'
printf '      handle /setup* {\n'
printf '          @pass query pass=YOUR_CADDY_TOKEN\n'
printf '          handle @pass {\n'
printf '              reverse_proxy 127.0.0.1:3100\n'
printf '          }\n'
printf '          handle {\n'
printf '              redir https://%s/ 302\n' "${DOMAIN}"
printf '          }\n'
printf '      }\n'
printf '  }\n'
echo ""
echo "======================================================================"
echo ""
read -rp "After updating Caddyfile and running 'caddy reload', press Enter to start containers..."

# -- Start containers ----------------------------------------------------------

echo ""
echo "Starting containers..."
$COMPOSE up -d --build

# -- Wait for Marzban ----------------------------------------------------------

echo "Waiting for Marzban to become healthy..."
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    echo "[ok] Marzban is up"
    break
  fi
  sleep 2
  if [ "$i" -eq 30 ]; then
    echo "Warning: Marzban did not respond within 60s."
    echo "  Check logs: docker compose logs marzban"
  fi
done

# -- Create Marzban admin account ----------------------------------------------

echo ""
echo "Creating Marzban admin account..."
ADMIN_USER="$(grep '^MARZBAN_SUDO_USERNAME=' .env | cut -d= -f2)"
docker exec vpn-marzban marzban-cli admin create \
  --username "${ADMIN_USER:-admin}" \
  --password "${ADMIN_PW}" \
  --sudo 2>/dev/null || echo "(admin may already exist -- safe to ignore)"

# -- Done ----------------------------------------------------------------------

echo ""
echo "=== Setup complete! ==="
echo ""
echo "  Portal:     https://${DOMAIN}/"
echo "  Dashboard:  https://${DOMAIN}/dashboard/"
echo "  Admin user: ${ADMIN_USER:-admin}"
echo "  Admin pass: ${ADMIN_PW}"
echo ""
echo "  Save the admin password -- it is shown only once."
echo "  Run '${COMPOSE} ps' to verify all three containers are running."
echo "  Using Claude Code? CLAUDE.md has architecture details."
