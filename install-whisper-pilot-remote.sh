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

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends build-essential cmake git ffmpeg curl ca-certificates

if [ ! -d "$WHISPER_DIR/.git" ]; then
  git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$WHISPER_DIR"
fi

if [ ! -x "$BIN" ]; then
  cmake -S "$WHISPER_DIR" -B "$WHISPER_DIR/build" -DCMAKE_BUILD_TYPE=Release \
        -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON
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

echo "whisper.cpp ready: $BIN"
echo "model: $WHISPER_DIR/models/$MODEL_NAME"
echo "Now set in /etc/tg-ingest-agent.env:"
echo "  STT_MODE=local"
echo "  WHISPER_BIN=$BIN"
echo "  WHISPER_MODEL=$WHISPER_DIR/models/$MODEL_NAME"
