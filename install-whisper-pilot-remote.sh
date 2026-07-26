#!/usr/bin/env bash
# Build whisper.cpp + download the quantized small model for local STT.
# Idempotent; run as root on the PD VPS (174.138.108.85, Cara's host — the
# Pilot-VPS this script was first written for was retired 2026-06). Takes
# ~10 min on 1 vCPU (compile). The SERVICE it installs does NOT run as root.
#
# Supply-chain pins (strongly recommended — both default to "unpinned", which
# only warns, because a first install has nothing to pin against):
#   WHISPER_REF           exact whisper.cpp tag/commit to build
#                         (live value: git -C /opt/whisper.cpp rev-parse HEAD)
#   WHISPER_MODEL_SHA256  sha256 of the model file
#                         (live value: sha256sum /opt/whisper.cpp/models/<model>)
# Both are printed on every run so the next run can pin them.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 1
fi

WHISPER_DIR=/opt/whisper.cpp
MODEL_NAME="${WHISPER_MODEL_NAME:-ggml-small-q5_1.bin}"
MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/${MODEL_NAME}"
MODEL_PATH="$WHISPER_DIR/models/$MODEL_NAME"
BIN="$WHISPER_DIR/build/bin/whisper-cli"
BUILT_REF_STAMP="$WHISPER_DIR/build/.built-ref"

WHISPER_REF="${WHISPER_REF:-}"
WHISPER_MODEL_SHA256="${WHISPER_MODEL_SHA256:-}"

PORT="${WHISPER_SERVER_PORT:-8089}"

verify_model(){
  # $1 = file to check. With a pin: hard-fail on mismatch. Without one: warn
  # loudly and print the digest so the next run can pin it.
  local actual
  actual="$(sha256sum "$1" | awk '{print $1}')"
  if [ -n "$WHISPER_MODEL_SHA256" ]; then
    if [ "$actual" != "$WHISPER_MODEL_SHA256" ]; then
      echo "ERROR: model sha256 mismatch for $1" >&2
      echo "  expected $WHISPER_MODEL_SHA256" >&2
      echo "  actual   $actual" >&2
      return 1
    fi
    echo "model sha256 verified: $actual"
  else
    echo "WARNING: WHISPER_MODEL_SHA256 is empty -> the model was NOT verified." >&2
    echo "         Pin it on the next run: WHISPER_MODEL_SHA256=$actual" >&2
  fi
}

export DEBIAN_FRONTEND=noninteractive
apt-get update
# pkg-config + libopenblas-dev enable the BLAS-accelerated build (faster CPU
# matmul); ggml-blas's CMake requires pkg-config. coreutils carries sha256sum.
apt-get install -y --no-install-recommends build-essential cmake git ffmpeg curl \
  ca-certificates pkg-config libopenblas-dev coreutils

if [ ! -d "$WHISPER_DIR/.git" ]; then
  if [ -n "$WHISPER_REF" ]; then
    git clone https://github.com/ggml-org/whisper.cpp "$WHISPER_DIR"
  else
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$WHISPER_DIR"
  fi
fi

if [ -n "$WHISPER_REF" ]; then
  # A shallow clone from an earlier run has neither the tag nor the commit.
  git -C "$WHISPER_DIR" fetch --quiet --tags --unshallow origin 2>/dev/null \
    || git -C "$WHISPER_DIR" fetch --quiet --tags origin
  git -C "$WHISPER_DIR" checkout --quiet "$WHISPER_REF"
else
  echo "WARNING: WHISPER_REF is empty -> building UNPINNED upstream HEAD." >&2
  echo "         Pin it on the next run: WHISPER_REF=$(git -C "$WHISPER_DIR" rev-parse HEAD)" >&2
fi
HEAD_REF="$(git -C "$WHISPER_DIR" rev-parse HEAD)"
echo "whisper.cpp source at $HEAD_REF"

# BLAS-accelerated build. Rebuild if the binary is missing, not BLAS-linked, or
# was built from a different commit than the one checked out now — without the
# stamp, re-pinning to another ref would silently keep the old binary.
if [ ! -x "$BIN" ] || ! ldd "$BIN" 2>/dev/null | grep -qi blas \
   || [ "$(cat "$BUILT_REF_STAMP" 2>/dev/null || true)" != "$HEAD_REF" ]; then
  cmake -S "$WHISPER_DIR" -B "$WHISPER_DIR/build" -DCMAKE_BUILD_TYPE=Release \
        -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON \
        -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS
  cmake --build "$WHISPER_DIR/build" --config Release -j"$(nproc)"
  printf '%s\n' "$HEAD_REF" > "$BUILT_REF_STAMP"
fi

mkdir -p "$WHISPER_DIR/models"
if [ ! -f "$MODEL_PATH" ]; then
  curl -fL --retry 3 -o "$MODEL_PATH.part" "$MODEL_URL"
  verify_model "$MODEL_PATH.part" || { rm -f "$MODEL_PATH.part"; exit 1; }
  mv "$MODEL_PATH.part" "$MODEL_PATH"
else
  verify_model "$MODEL_PATH" || exit 1
fi
# Read by the service as an unprivileged (dynamic) user, so keep it readable.
chmod 0644 "$MODEL_PATH"

# smoke test: 1s of silence must transcribe (to empty/blank) without error
ffmpeg -y -f lavfi -i anullsrc=r=16000:cl=mono -t 1 /tmp/whisper-smoke.wav 2>/dev/null
"$BIN" -m "$MODEL_PATH" -f /tmp/whisper-smoke.wav -l auto -np -nt >/dev/null
rm -f /tmp/whisper-smoke.wav

# Warm whisper-server: keeps the model loaded between calls (no per-voice-note
# reload). --convert lets it ffmpeg the uploaded OGG itself. --tmp-dir /tmp is
# REQUIRED: newer whisper.cpp defaults the convert tmp dir to "." (CWD), which
# is read-only under ProtectSystem=strict -> "FFmpeg conversion failed"; /tmp is
# writable via PrivateTmp. Bound to loopback.
#
# It used to run as ROOT: a C++ multipart parser plus an ffmpeg fork on every
# upload is the last thing that should hold uid 0. DynamicUser gives it a
# transient unprivileged uid with no state of its own — the binary and the model
# under /opt are world-readable, which is all it needs. RestrictAddressFamilies
# leaves only the loopback socket it listens on (AF_INET) and AF_UNIX.
cat >/etc/systemd/system/whisper-server.service <<UNIT
[Unit]
Description=whisper.cpp warm transcription server (loopback)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
DynamicUser=yes
ExecStart=$WHISPER_DIR/build/bin/whisper-server -m $MODEL_PATH --host 127.0.0.1 --port $PORT --convert --tmp-dir /tmp -t $(nproc)
Restart=always
RestartSec=5
Nice=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
RestrictAddressFamilies=AF_INET AF_UNIX
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
