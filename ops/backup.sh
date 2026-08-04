#!/usr/bin/env bash
# Daily full backup of the DB + all of data/ to the external HDD and the LAN
# tablet, run by lunaschal-backup.timer. Each destination gets an
# independently-restorable dated snapshot dir (rsync --link-dest against
# yesterday's, so unchanged media is hardlinked rather than duplicated across
# every day's snapshot). A missing destination (drive unplugged, tablet
# asleep) is skipped with a logged warning, never a hard failure — see
# `journalctl --user -u lunaschal-backup` for history.
#
# Config: copy ops/backup.env.example to ops/backup.env and fill in.
set -e

cd "$(dirname "$0")/.."

if [ -f ops/backup.env ]; then
  set -a; source ops/backup.env; set +a
fi

RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
TODAY=$(date +%Y-%m-%d)
DATE_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}$'

STAGING=$(mktemp -d)
trap 'rm -rf "$STAGING"' EXIT

log() { echo "$(date -Iseconds) backup: $*"; }

DB_SNAPSHOT_OK=1
log "snapshotting DB"
if ! .venv/bin/python -m backend.ops.backup snapshot data/lunaschal.db "$STAGING/lunaschal.db"; then
  log "DB snapshot failed — continuing with media-only backup (lunaschal.db will be missing from today's snapshot)"
  DB_SNAPSHOT_OK=0
fi

backup_to_hdd() {
  local base="$1"
  if [ ! -d "$base" ]; then
    log "HDD path '$base' not present — skipping (drive unplugged?)"
    return
  fi

  local prev
  prev=$(ls "$base" 2>/dev/null | grep -E "$DATE_RE" | sort | tail -1)
  local dest="$base/$TODAY"
  local link_args=()
  [ -n "$prev" ] && [ "$prev" != "$TODAY" ] && link_args=(--link-dest="$base/$prev/data")

  mkdir -p "$dest/data"
  rsync -a --delete "${link_args[@]}" --exclude='lunaschal.db*' data/ "$dest/data/"
  if [ "$DB_SNAPSHOT_OK" = 1 ]; then
    rsync -a "$STAGING/lunaschal.db" "$dest/data/lunaschal.db"
  else
    log "skipping lunaschal.db in $dest — DB snapshot failed earlier"
  fi
  log "HDD snapshot written to $dest"

  local existing
  existing=$(ls "$base" 2>/dev/null | grep -E "$DATE_RE")
  # shellcheck disable=SC2086
  .venv/bin/python -m backend.ops.backup prune --keep-days "$RETENTION_DAYS" $existing | while read -r old; do
    log "pruning HDD snapshot $old"
    rm -rf "${base:?}/$old"
  done
}

backup_to_tablet() {
  local user="$1" host="$2" base="$3"
  if [ -z "$host" ]; then
    log "no tablet host configured — skipping"
    return
  fi
  # BACKUP_TABLET_USER is optional if ~/.ssh/config already sets User for the
  # tablet's Host entry (see docs/deployment.md) — "user@host" with an empty
  # user is invalid, so fall back to plain "host" in that case.
  local target="$host"
  [ -n "$user" ] && target="$user@$host"

  if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$target" true 2>/dev/null; then
    log "tablet '$host' unreachable — skipping (asleep/offline?)"
    return
  fi

  local prev
  prev=$(ssh "$target" "mkdir -p '$base' && ls '$base'" 2>/dev/null | grep -E "$DATE_RE" | sort | tail -1)
  local dest="$base/$TODAY"
  local link_args=()
  [ -n "$prev" ] && [ "$prev" != "$TODAY" ] && link_args=(--link-dest="$base/$prev/data")

  ssh "$target" "mkdir -p '$dest/data'"
  rsync -a --delete -e ssh "${link_args[@]}" --exclude='lunaschal.db*' data/ "$target:$dest/data/"
  if [ "$DB_SNAPSHOT_OK" = 1 ]; then
    rsync -a -e ssh "$STAGING/lunaschal.db" "$target:$dest/data/lunaschal.db"
  else
    log "skipping lunaschal.db in $host:$dest — DB snapshot failed earlier"
  fi
  log "tablet snapshot written to $host:$dest"

  local existing
  existing=$(ssh "$target" "ls '$base'" 2>/dev/null | grep -E "$DATE_RE")
  # shellcheck disable=SC2086
  .venv/bin/python -m backend.ops.backup prune --keep-days "$RETENTION_DAYS" $existing | while read -r old; do
    log "pruning tablet snapshot $old"
    ssh "$target" "rm -rf '${base:?}/$old'"
  done
}

[ -n "$BACKUP_HDD_PATH" ] && backup_to_hdd "$BACKUP_HDD_PATH"
[ -n "$BACKUP_TABLET_HOST" ] && backup_to_tablet "$BACKUP_TABLET_USER" "$BACKUP_TABLET_HOST" "$BACKUP_TABLET_PATH"

if [ "$DB_SNAPSHOT_OK" != 1 ]; then
  log "done, but with errors — DB snapshot failed (see above); media backup still ran"
  exit 1
fi

log "done"
