#!/usr/bin/env bash
SCRIPT="main.py"
LAUNCHER="runMeLinux.sh"
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

cleanup() {
    echo ""
    echo "Ctrl+C detected. Shutting down..."
    stop_app
    echo "Killing ssh-agent..."
<<<<<<< HEAD
    sudo pkill -f "main.py"
=======
    pkill -f "main.py"
>>>>>>> 558eee3 (watcher)
    echo "Done."
    exit 0
}

trap cleanup SIGINT SIGTERM

eval "$(ssh-agent -s)"
ssh-add ~/.ssh/Liam

git fetch origin "$BRANCH" --quiet
LAST_HASH=$(git rev-parse "origin/$BRANCH")
start_app

while true; do
    sleep "$INTERVAL" &
    SLEEP_PID=$!
    wait "$SLEEP_PID"

    git fetch origin "$BRANCH" --quiet
    NEW_HASH=$(git rev-parse "origin/$BRANCH")

    if [ "$NEW_HASH" != "$LAST_HASH" ]; then
        echo "Change detected ($LAST_HASH → $NEW_HASH)"
        stop_app
        git stash
        git pull origin "$BRANCH" --quiet
        LAST_HASH="$NEW_HASH"
        start_app
    fi
done
