#!/usr/bin/env bash
# Driftline VPS setup — Ubuntu 24.04. Run as root ONCE on a fresh server:
#   bash deploy/setup.sh
# Assumes the repo has been copied to /home/driftline/driftline (see deploy/README.md).
set -euo pipefail

REPO=/home/driftline/driftline

echo "== user =="
id -u driftline &>/dev/null || adduser --disabled-password --gecos "" driftline

echo "== packages =="
apt-get update -qq
apt-get install -y -qq git curl ufw ca-certificates

echo "== firewall: SSH only; dashboard/API stay localhost =="
ufw allow OpenSSH
ufw --force enable

echo "== node 22 =="
if ! command -v node &>/dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y -qq nodejs
fi

echo "== uv (as driftline user) =="
sudo -u driftline bash -c 'command -v ~/.local/bin/uv &>/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh'

echo "== ownership =="
chown -R driftline:driftline "$REPO"

echo "== engine deps =="
sudo -u driftline bash -c "cd $REPO/engine && ~/.local/bin/uv sync"

echo "== dashboard build =="
sudo -u driftline bash -c "cd $REPO/dashboard && npm install --no-fund --no-audit && npm run build"

echo "== .env check =="
if [ ! -f "$REPO/.env" ]; then
  echo "!! $REPO/.env is missing — copy .env.example and fill in keys, then re-run"
  exit 1
fi

echo "== systemd units =="
cp "$REPO"/deploy/driftline-*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now driftline-engine driftline-daemon driftline-dashboard

echo "== status =="
sleep 3
systemctl --no-pager --lines=3 status driftline-engine driftline-daemon driftline-dashboard || true

cat <<'EOF'

Done. From your laptop, open the dashboard through an SSH tunnel:
  ssh -N -L 3000:localhost:3000 driftline@<server-ip>
then visit http://localhost:3000

Logs:  journalctl -u driftline-engine -f
EOF
