#!/usr/bin/env bash
# Record the swarm board during a fresh race: .data/demo/arsune-swarm.mp4 (and .png).
# Needs the Compose stack running, uv with Playwright Chromium (uv run playwright install
# chromium), and an ffmpeg with libx264. It restarts the Compose swarm service.
# Other ports or project: COMPOSE_PROJECT_NAME=arsune-judge ARSUNE_UI_PORT=3640 scripts/record_demo.sh
set -euo pipefail
cd "$(dirname "$0")/.."
out=.data/demo

ffmpeg_bin=""
for bin in ${FFMPEG:-} $(which -a ffmpeg 2>/dev/null); do
  if "$bin" -hide_banner -encoders 2>/dev/null | grep libx264 >/dev/null; then
    ffmpeg_bin=$bin
    break
  fi
done
if [ -z "$ffmpeg_bin" ]; then
  echo "No ffmpeg with libx264 found. Set FFMPEG=/path/to/ffmpeg." >&2
  exit 1
fi

uv run python scripts/record_demo.py --out "$out"
"$ffmpeg_bin" -y -hide_banner -loglevel error -i "$out/arsune-swarm.webm" \
  -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p -movflags +faststart \
  "$out/arsune-swarm.mp4"
echo "Video: $out/arsune-swarm.mp4"
