#!/bin/sh
set -eu

case "${BACKUP_INTERVAL_SECONDS:-}" in *[!0-9]*|'') echo "Invalid backup interval" >&2; exit 1;; esac
case "${BACKUP_RETENTION_DAYS:-}" in *[!0-9]*|'') echo "Invalid backup retention" >&2; exit 1;; esac
[ "$BACKUP_INTERVAL_SECONDS" -ge 60 ] || { echo "Backup interval must be at least 60 seconds" >&2; exit 1; }
[ "$BACKUP_RETENTION_DAYS" -ge 1 ] || { echo "Backup retention must be at least one day" >&2; exit 1; }

while :; do
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    temporary="/backups/.policy_app-${stamp}.dump.incomplete"
    finished="/backups/policy_app-${stamp}.dump"
    if pg_dump --format=custom --no-owner --no-acl --file="$temporary"; then
        mv "$temporary" "$finished"
        echo "Database backup ready: $finished"
        find /backups -maxdepth 1 -type f -name 'policy_app-*.dump' -mtime "+${BACKUP_RETENTION_DAYS}" -delete
        sleep "$BACKUP_INTERVAL_SECONDS"
    else
        rm -f "$temporary"
        echo "Database backup failed; will retry in five minutes" >&2
        sleep 300
    fi
done
