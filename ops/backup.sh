#!/usr/bin/env bash
# Daily backup of the DB + all of data/, run by lunaschal-backup.timer.
#
# Two tiers, because the two halves of data/ want opposite things:
#
#   <dest>/db/YYYY-MM-DD/lunaschal.db   dated snapshots, pruned past
#                                       BACKUP_RETENTION_DAYS
#   <dest>/media/                       ONE flat mirror, additive forever
#
# The DB is small and worth having history for — you may need last Tuesday's
# copy. The media (fanfic, meetings, journal attachments) is ~6G and each file
# is immutable once written, so dated copies of it would be pure duplication.
# The media mirror therefore has no --delete and is never pruned: a fic removed
# from the library keeps its only surviving copy here. That is the point, not an
# oversight — this is an archive as much as a backup.
#
# A missing destination (drive unplugged, tablet asleep) is skipped with a
# logged warning, never a hard failure — see
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

# Flags chosen for the exFAT destination. Both were measured against the actual
# drive, and neither is as load-bearing as it looks:
#   -rt rather than -a — exFAT stores no POSIX ownership or permission bits.
#     This mount has uid/gid/fmask/dmask set, so the driver silently accepts the
#     chown/chmod and -a exits 0 too; -rt just avoids issuing metadata calls
#     that can never round-trip.
#   --modify-window=1 — insurance against FAT-family timestamp granularity.
#     Measured as NOT required here: exFAT keeps 10ms resolution (FAT32's 2s is
#     where that worry comes from), and a second run already transfers nothing
#     without it.
# The real exFAT constraint is hardlinks, which is why there is no --link-dest
# anywhere in this script — see the header.
RSYNC_FLAGS=(-rt --modify-window=1)

# Delete the dated DB directories that have aged out. The caller passes the name
# of a function that removes one snapshot, so the local and remote destinations
# share this loop without the removal command having to survive a round-trip
# through eval. backend/ops/backup.py does the date arithmetic either way, and
# only ever emits names that parsed as real YYYY-MM-DD dates.
prune_db_dirs() {
  local label="$1" existing="$2" remover="$3"
  [ -z "$existing" ] && return
  local old
  # shellcheck disable=SC2086
  while read -r old; do
    [ -n "$old" ] || continue
    log "pruning $label DB snapshot $old"
    "$remover" "$old"
  done < <(.venv/bin/python -m backend.ops.backup prune --keep-days "$RETENTION_DAYS" $existing)
}

_remove_hdd_snapshot() { rm -rf "${HDD_BASE:?}/db/$1"; }
_remove_tablet_snapshot() { ssh "$TABLET_TARGET" "rm -rf '${TABLET_BASE:?}/db/$1'"; }

backup_to_hdd() {
  local base="$1"
  HDD_BASE="$base"

  # $base is a directory *on* the drive, so it does not exist until the first
  # successful run — testing it directly would report "unplugged" and skip
  # forever. The mount point above it is what actually indicates the drive.
  local parent
  parent=$(dirname "$base")
  if [ ! -d "$parent" ]; then
    log "HDD mount point '$parent' not present — skipping (drive unplugged?)"
    return
  fi

  mkdir -p "$base/media"
  log "mirroring media to $base/media"
  rsync "${RSYNC_FLAGS[@]}" --exclude='lunaschal.db*' data/ "$base/media/"

  if [ "$DB_SNAPSHOT_OK" = 1 ]; then
    mkdir -p "$base/db/$TODAY"
    rsync "${RSYNC_FLAGS[@]}" "$STAGING/lunaschal.db" "$base/db/$TODAY/lunaschal.db"
    log "HDD DB snapshot written to $base/db/$TODAY"
  else
    log "skipping DB snapshot in $base — snapshot failed earlier"
  fi

  local existing
  existing=$(ls "$base/db" 2>/dev/null | grep -E "$DATE_RE" || true)
  prune_db_dirs "HDD" "$existing" _remove_hdd_snapshot
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
  TABLET_TARGET="$target"
  TABLET_BASE="$base"

  if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$target" true 2>/dev/null; then
    log "tablet '$host' unreachable — skipping (asleep/offline?)"
    return
  fi

  ssh "$target" "mkdir -p '$base/media'"
  log "mirroring media to $host:$base/media"
  rsync "${RSYNC_FLAGS[@]}" -e ssh --exclude='lunaschal.db*' data/ "$target:$base/media/"

  if [ "$DB_SNAPSHOT_OK" = 1 ]; then
    ssh "$target" "mkdir -p '$base/db/$TODAY'"
    rsync "${RSYNC_FLAGS[@]}" -e ssh "$STAGING/lunaschal.db" "$target:$base/db/$TODAY/lunaschal.db"
    log "tablet DB snapshot written to $host:$base/db/$TODAY"
  else
    log "skipping DB snapshot in $host:$base — snapshot failed earlier"
  fi

  local existing
  existing=$(ssh "$target" "ls '$base/db'" 2>/dev/null | grep -E "$DATE_RE" || true)
  prune_db_dirs "tablet" "$existing" _remove_tablet_snapshot
}

[ -n "$BACKUP_HDD_PATH" ] && backup_to_hdd "$BACKUP_HDD_PATH"
[ -n "$BACKUP_TABLET_HOST" ] && backup_to_tablet "$BACKUP_TABLET_USER" "$BACKUP_TABLET_HOST" "$BACKUP_TABLET_PATH"

if [ "$DB_SNAPSHOT_OK" != 1 ]; then
  log "done, but with errors — DB snapshot failed (see above); media backup still ran"
  exit 1
fi

log "done"
