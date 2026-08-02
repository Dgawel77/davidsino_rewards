#!/usr/bin/env bash
# Run Davidsino Rewards locally with no Docker and no Postgres.
#
#   ./run-local.sh            # http://localhost:8000, data in ./davidsino.db
#   PORT=8080 ./run-local.sh
#
# Deps land in ./.localdeps so nothing touches system Python (Ubuntu blocks
# system-wide pip installs under PEP 668).
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
DEPS="$PWD/.localdeps"

if [ ! -d "$DEPS" ]; then
    echo "Installing dependencies into .localdeps (one time)..."
    if ! python3 -m pip --version >/dev/null 2>&1; then
        echo "pip is unavailable. Install it with: sudo apt install python3-pip python3-venv"
        exit 1
    fi
    python3 -m pip install --quiet --target "$DEPS" -r requirements.txt
fi

export PYTHONPATH="$DEPS${PYTHONPATH:+:$PYTHONPATH}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///$PWD/davidsino.db}"
export ADMIN_PIN="${ADMIN_PIN:-1234}"
export WORKER_PIN="${WORKER_PIN:-5678}"

# Load .env if present so deposit addresses are picked up.
[ -f .env ] && set -a && . ./.env && set +a

echo "Davidsino Rewards -> http://localhost:$PORT"
echo "  dealer PIN $ADMIN_PIN | worker PIN $WORKER_PIN | db $DATABASE_URL"
# --ws none: the system 'websockets' package can shadow uvicorn's expected version.
exec python3 -m uvicorn main:app --host 0.0.0.0 --port "$PORT" --ws none --reload
