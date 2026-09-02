#!/usr/bin/env bash
# Provision Superio on a Debian VM alongside whatever else is running there.
#
# Deliberately self-contained under /opt/superio with its own systemd units and
# its own port, so it cannot collide with an existing web stack. Nothing here
# touches ports 80 or 443.
set -euo pipefail

REPO="https://github.com/TheSupermanish/superio-trading-agent.git"
ROOT=/opt/superio
DASH_PORT=8088
API_PORT=8090

echo "== packages =="
sudo apt-get update -qq
sudo apt-get install -y -qq git curl jq >/dev/null

echo "== alpaca CLI =="
if ! command -v alpaca >/dev/null 2>&1; then
  # Release assets are named cli_<version>_linux_<arch>.tar.gz, so resolve the
  # current tag rather than guessing a "latest download" filename.
  CLI_TAG=$(curl -fsSL https://api.github.com/repos/alpacahq/cli/releases/latest | jq -r .tag_name)
  CLI_VER="${CLI_TAG#v}"
  case "$(dpkg --print-architecture)" in
    amd64) CLI_ARCH=amd64 ;;
    arm64) CLI_ARCH=arm64 ;;
    *) echo "unsupported architecture" >&2; exit 1 ;;
  esac
  curl -fsSL "https://github.com/alpacahq/cli/releases/download/${CLI_TAG}/cli_${CLI_VER}_linux_${CLI_ARCH}.tar.gz" \
    -o /tmp/alpaca.tgz
  sudo tar -xzf /tmp/alpaca.tgz -C /usr/local/bin alpaca
  sudo chmod +x /usr/local/bin/alpaca
fi
alpaca version || true

echo "== uv =="
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  sudo ln -sf "$HOME/.local/bin/uv" /usr/local/bin/uv
  sudo ln -sf "$HOME/.local/bin/uvx" /usr/local/bin/uvx
fi

echo "== code =="
sudo mkdir -p "$ROOT"
sudo chown -R "$USER":"$USER" "$ROOT"
if [ -d "$ROOT/.git" ]; then
  git -C "$ROOT" fetch --quiet origin && git -C "$ROOT" reset --hard --quiet origin/main
else
  git clone --quiet "$REPO" "$ROOT"
fi

echo "== python env =="
cd "$ROOT"
# Debian 12 ships Python 3.11 and this project needs 3.12; uv fetches a
# managed interpreter rather than us touching the system Python.
uv python install 3.12 >/dev/null 2>&1 || true
uv venv --python 3.12 --clear --quiet
VIRTUAL_ENV="$ROOT/.venv" uv pip install --quiet -e .
VIRTUAL_ENV="$ROOT/.venv" uv pip install --quiet google-genai mcp
mkdir -p "$ROOT/logs" "$ROOT/data"

echo "== dashboard =="
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - >/dev/null 2>&1
  sudo apt-get install -y -qq nodejs >/dev/null
fi
cd "$ROOT/dashboard"
npm ci --silent --no-audit --no-fund
NEXT_PUBLIC_BASE_PATH="" npm run build --silent
cd "$ROOT"

echo "== systemd =="
# main/test2/test3 are wired to real paper accounts. The diary units share the
# main account's read-only keys to read the same live chain, and cannot place
# an order: their variants are outside LIVE_VARIANTS, so Settings.dry_run is
# pinned true no matter what the environment says.
for spec in main:barbell test2:convex_tilt test3:income_only \
            diary-levered:levered diary-vrp:vrp_router \
            diary-fat:fat_credit diary-gamma:long_gamma; do
  prof="${spec%%:*}"; var="${spec##*:}"
  keys="$prof"
  case "$prof" in diary-*) keys=main ;; esac
  sudo tee /etc/systemd/system/superio-$prof.service >/dev/null <<UNIT
[Unit]
Description=Superio trading agent ($prof / $var)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$ROOT
Environment=ALPACA_PROFILE=$keys
Environment=STRATEGY_VARIANT=$var
Environment=PATH=/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
# The billing guard shells out to gcloud, which bills its API call to whatever
# project is configured. Pin it so the check does not depend on ambient state.
Environment=CLOUDSDK_CORE_PROJECT=gemini-cli-manish-mac-mini
ExecStart=$ROOT/.venv/bin/python -m engine.loop --interval 300
Restart=always
RestartSec=30
StandardOutput=append:$ROOT/logs/$prof.log
StandardError=append:$ROOT/logs/$prof.log
MemoryMax=600M

[Install]
WantedBy=multi-user.target
UNIT
done

# The fleet API. Read-only over the journals, broker-free, and it serves the
# exported dashboard from the same origin so the browser needs no CORS dance.
sudo tee /etc/systemd/system/superio-api.service >/dev/null <<UNIT
[Unit]
Description=Superio fleet API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
ExecStart=$ROOT/.venv/bin/python $ROOT/api/app.py --port $API_PORT
Restart=always
RestartSec=10
StandardOutput=append:$ROOT/logs/api.log
StandardError=append:$ROOT/logs/api.log
MemoryMax=300M

[Install]
WantedBy=multi-user.target
UNIT

sudo tee /etc/systemd/system/superio-dashboard.service >/dev/null <<UNIT
[Unit]
Description=Superio dashboard (static)
After=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$ROOT
ExecStart=/usr/bin/python3 $ROOT/scripts/serve.py $ROOT/dashboard/out $DASH_PORT
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

# Refresh the snapshot the dashboard reads, straight into the served directory.
sudo tee /etc/systemd/system/superio-snapshot.service >/dev/null <<UNIT
[Unit]
Description=Superio dashboard snapshot refresh

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$ROOT
Environment=PATH=/usr/local/bin:/usr/bin:/bin
ExecStart=/bin/bash $ROOT/scripts/vm-snapshot.sh
UNIT

sudo tee /etc/systemd/system/superio-snapshot.timer >/dev/null <<UNIT
[Unit]
Description=Refresh the Superio snapshot every minute

[Timer]
OnBootSec=90
OnUnitActiveSec=60

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now superio-main superio-test2 superio-test3 \
  superio-dashboard superio-api superio-snapshot.timer >/dev/null 2>&1
echo "== done =="
systemctl --no-pager --plain list-units 'superio-*' | head -12
