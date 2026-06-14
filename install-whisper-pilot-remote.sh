#!/usr/bin/env bash
# Build whisper.cpp + download the quantized small model for local STT.
# Idempotent; run as root on Pilot-VPS. Takes ~10 min on 1 vCPU (compile).
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 1
fi

WHISPER_DIR=/opt/whisper.cpp
MODEL_NAME="${WHISPER_MODEL_NAME:-ggml-small-q5_1.bin}"
MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/${MODEL_NAME}"
BIN="$WHISPER_DIR/build/bin/whisper-cli"

PORT="${WHISPER_SERVER_PORT:-8089}"

export DEBIAN_FRONTEND=noninteractive
apt-get update
# pkg-config + libopenblas-dev enable the BLAS-accelerated build (faster CPU
# matmul); ggml-blas's CMake requires pkg-config.
apt-get install -y --no-install-recommends build-essential cmake git ffmpeg curl \
  ca-certificates pkg-config libopenblas-dev

if [ ! -d "$WHISPER_DIR/.git" ]; then
  git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$WHISPER_DIR"
fi

# BLAS-accelerated build. Rebuild if the binary is missing or not BLAS-linked.
if [ ! -x "$BIN" ] || ! ldd "$BIN" 2>/dev/null | grep -qi blas; then
  cmake -S "$WHISPER_DIR" -B "$WHISPER_DIR/build" -DCMAKE_BUILD_TYPE=Release \
        -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON \
        -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS
  cmake --build "$WHISPER_DIR/build" --config Release -j"$(nproc)"
fi

mkdir -p "$WHISPER_DIR/models"
if [ ! -f "$WHISPER_DIR/models/$MODEL_NAME" ]; then
  curl -fL --retry 3 -o "$WHISPER_DIR/models/$MODEL_NAME.part" "$MODEL_URL"
  mv "$WHISPER_DIR/models/$MODEL_NAME.part" "$WHISPER_DIR/models/$MODEL_NAME"
fi

# smoke test: 1s of silence must transcribe (to empty/blank) without error
ffmpeg -y -f lavfi -i anullsrc=r=16000:cl=mono -t 1 /tmp/whisper-smoke.wav 2>/dev/null
"$BIN" -m "$WHISPER_DIR/models/$MODEL_NAME" -f /tmp/whisper-smoke.wav -l auto -np -nt >/dev/null
rm -f /tmp/whisper-smoke.wav

# Warm whisper-server: keeps the model loaded between calls (no per-voice-note
# reload). --convert lets it ffmpeg the uploaded OGG itself. Bound to loopback.
cat >/etc/systemd/system/whisper-server.service <<UNIT
[Unit]
Description=whisper.cpp warm transcription server (loopback)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$WHISPER_DIR/build/bin/whisper-server -m $WHISPER_DIR/models/$MODEL_NAME --host 127.0.0.1 --port $PORT --convert -t 1
Restart=always
RestartSec=5
Nice=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now whisper-server.service

echo "whisper.cpp ready: $BIN (BLAS: $(ldd "$BIN" | grep -qi blas && echo yes || echo no))"
echo "whisper-server: 127.0.0.1:$PORT (systemctl status whisper-server)"
echo "Set in /etc/tg-ingest-agent.env for the warm path:"
echo "  STT_MODE=local_server"
echo "  WHISPER_SERVER_URL=http://127.0.0.1:$PORT"
