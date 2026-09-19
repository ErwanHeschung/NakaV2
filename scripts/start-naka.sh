#!/usr/bin/env bash
# Start the whole backend. Used both by hand and by the Windows logon task.
#
# Without an autostart the tool quietly falls out of use: anything that needs
# two terminal commands before it can be spoken to does not get spoken to.
set -euo pipefail

cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
export TQDM_DISABLE=1

mkdir -p logs

docker compose up -d llm

if pgrep -f "bin/uvicorn server.main" >/dev/null; then
  echo "naka server already running"
else
  setsid uv run uvicorn server.main:app --host 0.0.0.0 --port 8000 \
    >> logs/naka.log 2>&1 < /dev/null &
  disown
  echo "naka server starting — tail logs/naka.log"
fi
