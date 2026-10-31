#!/bin/sh
set -e

# When started as root (the default), make sure the data volume belongs to the
# service user, then drop privileges. Started with --user, just run.
if [ "$(id -u)" = "0" ]; then
    chown -R asstats:asstats /data 2>/dev/null || true
    exec setpriv --reuid=asstats --regid=asstats --init-groups "$0" "$@"
fi

# Create or migrate the schema before the service starts. This is safe when web and
# collector run as separate containers and start together: setup is serialized by a
# lock in the database. Only the web side creates the first admin account, so its
# password ends up in the web container's log.
case "$1 $2" in
    "asstats collector") asstats init --schema-only ;;
    *) asstats init ;;
esac

exec "$@"
