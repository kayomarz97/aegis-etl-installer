#!/usr/bin/env sh
# Ollama container entrypoint.
# Starts ollama serve in the background, waits for it to be ready,
# pulls any missing models, then foregrounds the server process so
# the container stays alive and SIGTERM propagates correctly.
set -e

# Use a local variable for the health-check URL only.
# Do NOT set/export OLLAMA_HOST — ollama serve inherits env vars, and if
# OLLAMA_HOST=http://localhost:11434 it binds only to 127.0.0.1, making the
# server unreachable from other containers via Docker's internal network.
# Ollama's default is 0.0.0.0:11434, which is what we want.
_CHECK_URL="http://localhost:11434/api/version"
MAX_WAIT=120   # seconds to wait for ollama serve to become ready
PULL_RETRIES=3

# 1. Start ollama serve in the background.
ollama serve &
OLLAMA_PID=$!

# 2. Wait for ollama serve to accept connections.
echo "[ollama-entrypoint] Waiting for ollama serve to be ready..."
i=0
until curl -s -o /dev/null "http://127.0.0.1:11434/api/version" 2>/dev/null || \
      curl -s -o /dev/null "http://[::1]:11434/api/version" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -ge "$MAX_WAIT" ]; then
        echo "[ollama-entrypoint] ERROR: ollama serve did not become ready after ${MAX_WAIT}s"
        exit 1
    fi
    sleep 1
done
echo "[ollama-entrypoint] ollama serve is ready."

# 3. Check which models are already present.
PRESENT=$(ollama list 2>/dev/null || true)

pull_if_missing() {
    model="$1"
    if echo "$PRESENT" | grep -qF "$model"; then
        echo "[ollama-entrypoint] Model already present: $model"
    else
        echo "[ollama-entrypoint] Pulling model: $model"
        attempt=0
        while [ "$attempt" -lt "$PULL_RETRIES" ]; do
            attempt=$((attempt + 1))
            if ollama pull "$model"; then
                echo "[ollama-entrypoint] Pull succeeded: $model"
                return 0
            fi
            echo "[ollama-entrypoint] Pull attempt $attempt failed for $model — retrying..."
            sleep 5
        done
        echo "[ollama-entrypoint] ERROR: Failed to pull $model after $PULL_RETRIES attempts"
        exit 1
    fi
}

# OLLAMA_MODEL and EMBEDDING_MODEL are injected via docker-compose environment.
pull_if_missing "${OLLAMA_MODEL:-gemma4:e2b}"
pull_if_missing "${EMBEDDING_MODEL:-embeddinggemma}"

echo "[ollama-entrypoint] All models ready. Handing off to ollama serve (PID ${OLLAMA_PID})."

# 4. Forward SIGTERM/SIGINT to ollama serve so Docker stop is graceful.
# Without this trap, sh receives the signal but never forwards it to the
# child, and Docker is forced to SIGKILL after the stop timeout.
trap 'kill "$OLLAMA_PID"' TERM INT

# 5. Bring the background process to the foreground.
# `wait` blocks until ollama serve exits, keeping the container alive.
wait "$OLLAMA_PID"
