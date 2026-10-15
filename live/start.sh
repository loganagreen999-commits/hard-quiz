#!/usr/bin/env bash
# Start a live Know-It-All game and open the host screen in the Windows browser.
cd "$(dirname "$0")"
PORT=${PORT:-8099}
if ss -ltnH "sport = :$PORT" | grep -q .; then
  echo "Port $PORT is already in use (another server running?). Close it and try again."; exit 1
fi
export PORT HOSTKEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(8))')
python3 server.py & SP=$!
trap 'kill $SP 2>/dev/null' EXIT INT TERM
sleep 1
cmd.exe /c start "" "http://localhost:$PORT/host?k=$HOSTKEY" >/dev/null 2>&1
echo "  Host screen opened in your browser. Close this window to end the game."
wait $SP
