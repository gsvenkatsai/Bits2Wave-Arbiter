#!/usr/bin/env bash
set -e

PORT="${PORT:-8000}"
echo "[RENDER] Deploying Project ARBITER on port ${PORT}..."

# 1. Initialize SQLite Database Schema
python -c "from arbiter.shared.database import init_database; init_database('./arbiter_production.db')"

# 2. Start Uvicorn API Server in the background
python -m uvicorn arbiter.api.server:app --host 0.0.0.0 --port "${PORT}" &
SERVER_PID=$!

cleanup() {
    echo "[RENDER] Gracefully shutting down services..."
    kill "$SERVER_PID" 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

# 3. Wait for API server /api/v1/health probe using pure Python
echo "[RENDER] Waiting for API server health probe on port ${PORT}..."
python -c "
import urllib.request, time, sys
port = sys.argv[1]
url = f'http://127.0.0.1:{port}/api/v1/health'
for i in range(30):
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            if resp.status == 200:
                print('[RENDER] API server is ready and healthy!')
                sys.exit(0)
    except Exception:
        time.sleep(1)
print('[RENDER] Warning: Health probe timeout, proceeding anyway.')
sys.exit(0)
" "${PORT}"

# 4. Start Simulation Engine in continuous loop mode streaming frames to the API
echo "[RENDER] Starting Simulation Engine (loop mode, scripted twin, real queue)..."
python -m arbiter.simulator.engine \
    --scenario 2am_collision \
    --stream-ws \
    --api-url "http://127.0.0.1:${PORT}" \
    --loop \
    --pace 1.0 \
    --twin scripted \
    --use-real-queue &

# Wait on Uvicorn server to keep container alive
wait "$SERVER_PID"
