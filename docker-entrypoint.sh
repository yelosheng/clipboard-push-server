#!/bin/sh
set -e

# A bind-mounted ./data is created by Docker as root, but the server runs
# unprivileged, so the very first thing it does is fail with
#   sqlite3.OperationalError: unable to open database file
# Hand the directory over while we still have the rights to, then drop them.
if [ "$(id -u)" = "0" ]; then
    chown -R appuser:appuser /app/data 2>/dev/null || true
    exec setpriv --reuid=appuser --regid=appuser --init-groups "$@"
fi

# Already unprivileged (someone set `user:` in compose) -- nothing to hand over.
exec "$@"
