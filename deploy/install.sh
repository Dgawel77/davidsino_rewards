#!/usr/bin/env bash
#
# Install Davidsino on a Raspberry Pi (or any Debian box) as a systemd service.
#
#   curl -fsSL <raw-url>/deploy/install.sh | bash
#   ...or clone the repo and run:  bash deploy/install.sh
#
# Idempotent: safe to re-run to upgrade. It never overwrites .env or the
# database, so PINs, card IDs and balances survive an upgrade.
set -euo pipefail

REPO="${REPO:-https://github.com/Dgawel77/davidsino_rewards.git}"
BRANCH="${BRANCH:-feature/slots-and-payments}"
APP_DIR="${APP_DIR:-$HOME/davidsino}"
DATA_DIR="${DATA_DIR:-$HOME/davidsino-data}"
PORT="${PORT:-8000}"
SERVICE="davidsino"
# --public example.com puts nginx and a Let's Encrypt certificate in front.
PUBLIC_DOMAIN=""
while [ $# -gt 0 ]; do
    case "$1" in
        --public) PUBLIC_DOMAIN="${2:-}"; shift 2 ;;
        --port)   PORT="${2:-8000}"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# Refuse to fight another service for a port rather than half-starting and
# leaving both broken. This box may already be running something.
if ss -lntp 2>/dev/null | grep -q ":$PORT "; then
    if ! systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
        echo "Port $PORT is already taken by something else:"
        ss -lntp 2>/dev/null | grep ":$PORT " || true
        echo "Re-run with a different port, e.g.:  bash $0 --port 8010"
        exit 1
    fi
fi

say "Checking prerequisites"
MISSING=()
for pkg in python3 python3-venv git sqlite3; do
    case "$pkg" in
        python3-venv) python3 -m venv --help >/dev/null 2>&1 || MISSING+=("$pkg") ;;
        *) command -v "$pkg" >/dev/null 2>&1 || MISSING+=("$pkg") ;;
    esac
done
if [ ${#MISSING[@]} -gt 0 ]; then
    echo "Installing: ${MISSING[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y "${MISSING[@]}"
fi

say "Fetching the code into $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
    git -C "$APP_DIR" checkout -q "$BRANCH"
    git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
    git clone --depth 1 --branch "$BRANCH" "$REPO" "$APP_DIR"
fi

say "Installing Python dependencies"
[ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
# psycopg2 is only needed for Postgres and builds slowly on a Pi; SQLite needs
# none of it, so install everything else and let that one be optional.
grep -v '^psycopg2' "$APP_DIR/requirements.txt" > /tmp/davidsino-reqs.txt
"$APP_DIR/venv/bin/pip" install --quiet -r /tmp/davidsino-reqs.txt

say "Preparing $DATA_DIR"
mkdir -p "$DATA_DIR"

# .env holds the secrets and is written exactly once, so re-running the
# installer never rotates a PIN or invalidates everyone's cards.
if [ ! -f "$DATA_DIR/.env" ]; then
    gen() { python3 -c "import secrets;print(secrets.token_hex(8))"; }
    pin() { python3 -c "import secrets;print(''.join(secrets.choice('0123456789') for _ in range(6)))"; }
    cat > "$DATA_DIR/.env" <<EOF
# Written once by deploy/install.sh. Edit freely; it is never overwritten.
DATABASE_URL=sqlite:///$DATA_DIR/davidsino.db
ADMIN_PIN=$(pin)
WORKER_PIN=$(pin)
MAX_PLAYERS=3
SESSION_TTL_HOURS=12

# The card ID is the password. Replace these with your real RFID UIDs when you
# want the physical cards to work — on a fresh database.
SEED_CARD_DAVID=$(gen)
SEED_CARD_ALEX=$(gen)
SEED_CARD_GUEST=$(gen)

# Deposit destinations. Left blank on purpose: the Funds tab then shows nothing,
# which is what you want unless a dealer is confirming real transfers by hand.
BTC_ADDRESS=
ETH_ADDRESS=
EOF
    chmod 600 "$DATA_DIR/.env"
    echo "Wrote $DATA_DIR/.env with fresh PINs and card IDs."
else
    echo "Keeping the existing $DATA_DIR/.env"
fi

say "Seeding the roster"
( cd "$APP_DIR" && set -a && . "$DATA_DIR/.env" && set +a && "$APP_DIR/venv/bin/python" seed_players.py )

say "Installing the systemd service"
sudo tee "/etc/systemd/system/$SERVICE.service" >/dev/null <<EOF
[Unit]
Description=Davidsino Rewards
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$DATA_DIR/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/venv/bin/uvicorn main:app --host 0.0.0.0 --port $PORT --ws none
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

say "Installing the nightly backup"
# The database is one file. Snapshotting it with sqlite3 .backup is safe while
# the app is running, which a plain cp is not.
sudo tee "/etc/systemd/system/$SERVICE-backup.service" >/dev/null <<EOF
[Unit]
Description=Davidsino database backup

[Service]
Type=oneshot
User=$USER
ExecStart=/bin/bash -c 'mkdir -p $DATA_DIR/backups && \
  sqlite3 $DATA_DIR/davidsino.db ".backup $DATA_DIR/backups/davidsino-\$(date +%%Y%%m%%d-%%H%%M).db" && \
  ls -1t $DATA_DIR/backups/*.db | tail -n +15 | xargs -r rm --'
EOF
sudo tee "/etc/systemd/system/$SERVICE-backup.timer" >/dev/null <<EOF
[Unit]
Description=Nightly Davidsino backup

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE.service" "$SERVICE-backup.timer"

if [ -n "$PUBLIC_DOMAIN" ]; then
    say "Publishing $PUBLIC_DOMAIN to the open internet"
    sudo apt-get install -y -qq nginx certbot python3-certbot-nginx

    # Only this one vhost is added; anything already served on this box keeps
    # its own config untouched.
    sudo tee "/etc/nginx/sites-available/$SERVICE" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $PUBLIC_DOMAIN;

    # The app is small and single-purpose; nothing here needs a big body.
    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
}
EOF
    sudo ln -sf "/etc/nginx/sites-available/$SERVICE" "/etc/nginx/sites-enabled/$SERVICE"
    sudo nginx -t && sudo systemctl reload nginx

    echo
    echo "Requesting a certificate. This needs $PUBLIC_DOMAIN to already point"
    echo "at this machine's public IP, or it will fail."
    sudo certbot --nginx -d "$PUBLIC_DOMAIN" --non-interactive --agree-tos \
        --register-unsafely-without-email --redirect || {
        echo
        echo "Certificate request failed — usually DNS not pointing here yet."
        echo "Fix the A record, then:  sudo certbot --nginx -d $PUBLIC_DOMAIN"
    }

    # Bind the app to loopback once nginx fronts it, so the raw port is not
    # also reachable from outside.
    sudo sed -i "s|--host 0.0.0.0|--host 127.0.0.1|" "/etc/systemd/system/$SERVICE.service"
    sudo systemctl daemon-reload && sudo systemctl restart "$SERVICE"
fi

say "Done"
sleep 2
systemctl --no-pager --lines=0 status "$SERVICE.service" | head -5 || true
IP=$(tailscale ip -4 2>/dev/null | head -1 || hostname -I | awk '{print $1}')
echo
echo "  Playing at:  http://${IP}:${PORT}"
echo "  Cards:       grep SEED_CARD $DATA_DIR/.env"
echo "  Dealer PIN:  grep ADMIN_PIN $DATA_DIR/.env"
echo "  Backups:     $DATA_DIR/backups  (nightly, 14 kept)"
echo
echo "  Logs:        journalctl -u $SERVICE -f"
echo "  Upgrade:     re-run this script"
echo
