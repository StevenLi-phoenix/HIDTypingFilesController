#!/bin/sh
# Wait for the target machine to enumerate the keyboard, then type the payload.
# Run it in a shell that is NOT the target window (e.g. the serial console),
# then switch focus to the target editor during the countdown.
#
#   sudo sh go.sh [payload] [delay]
set -e
cd "$(dirname "$0")"
PAYLOAD="${1:-payload.txt}"
DELAY="${2:-20}"
echo "payload: $PAYLOAD   delay: ${DELAY}s after link up"
exec python3 main_lite.py "$PAYLOAD" --wait --delay "$DELAY"
