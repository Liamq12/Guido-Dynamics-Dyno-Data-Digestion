#!/usr/bin/env bash

SCRIPT="main.py"
LAUNCHER="runMeLinux.sh"           # ← the second script that launches your python correctly
BRANCH="FSAEServer"
INTERVAL=30

start_app() {
    bash "$LAUNCHER" &
    APP_PID=$!
    echo "Started via $LAUNCHER (PID $APP_PID)"
}

stop_app() {
    if kill -0 "$APP_PID" 2>/dev/null; then
        echo "Stopping PID $APP_PID..."
        kill "$APP_PID"
        wait "$APP_PID" 2>/dev/null
    fi
}

git fetch origin "$BRANCH" --quiet
LAST_HASH=$(git rev-parse "origin/$BRANCH")
start_app

while true; do
    sleep "$INTERVAL"

    git fetch origin "$BRANCH" --quiet
    NEW_HASH=$(git rev-parse "origin/$BRANCH")

    if [ "$NEW_HASH" != "$LAST_HASH" ]; then
        echo "Change detected ($LAST_HASH → $NEW_HASH)"
        stop_app
        git pull origin "$BRANCH" --quiet
        LAST_HASH="$NEW_HASH"
        start_app
    fi
done
