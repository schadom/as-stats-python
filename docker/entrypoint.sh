#!/bin/sh
set -e

# When started as root (the default), make sure the data volume belongs to the
# service user, then drop privileges. Started with --user, just run.
if [ "$(id -u)" = "0" ]; then
    chown -R asstats:asstats /data 2>/dev/null || true
    exec setpriv --reuid=asstats --regid=asstats --init-groups "$0" "$@"
fi

# Create the schema and the first admin account once, before both services start.
asstats init

exec "$@"
