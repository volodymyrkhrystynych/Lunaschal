# Deployment & backups

Two `systemd --user` pieces run on the desktop machine that also serves as the
Lunaschal server:

- **`lunaschal.service`** — the production app (built `dist/` + the PyWebView
  window), restarted automatically whenever new code lands.
- **`lunaschal-deploy.timer`** — polls `origin/main` every 5 minutes; if it's
  ahead and the local checkout is a clean `main` (never a feature branch, never
  a dirty tree — this machine is also used for day-to-day development), pulls,
  reinstalls changed dependencies, rebuilds, and restarts `lunaschal.service`.
- **`lunaschal-backup.timer`** — daily at 3am. Dated DB snapshots plus a
  single additive mirror of the media, to the external HDD and the tablet.

All the scripts and unit files live in `ops/`; the pure decision logic (should
we deploy, which snapshots to prune) lives in `backend/ops/` and is covered by
`backend/tests/test_ops_deploy.py` / `test_ops_backup.py`.

## Prerequisites

- `rsync` on both the desktop and the tablet (`sudo pacman -S rsync` on Arch —
  not installed by default).
- A Tailscale cert already set up for network mode (see `start-server.sh`'s
  header comment: `sudo tailscale cert --cert-file=./certs/<name>.crt
--key-file=./certs/<name>.key <magicdns-name>`) — `ops/run-prod.sh` reuses
  the same `certs/*.crt`/`*.key` lookup.
- `npm run build` has been run at least once (`dist/` must exist before
  `lunaschal.service` can start).

## 1. Install the production service

```bash
mkdir -p ~/.config/systemd/user
ln -sf "$PWD/ops/lunaschal.service" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lunaschal
```

This keeps Flask serving `dist/` on `:5000` with `NETWORK_MODE=1`, so the
Pocket 2 / phone / tablet can all reach it, and starts at login
(`WantedBy=default.target`). Add `loginctl enable-linger volodya` if it should
also come up at boot without anyone signing in.

**It runs headless** — `main.py --headless`, no PyWebView window. This is
deliberate: the windowed path returns from `webview.start()` when the window is
closed and exits 0, which `Restart=on-failure` treated as a clean shutdown, so
closing the window took the LAN server down and systemd declined to restart it.
The unit is now `Restart=always`, and a server's lifetime no longer depends on
a window nobody meant to be load-bearing.

To open the UI:

```bash
./ops/open-window.sh     # desktop window against the running server
```

It's a plain client (`main.py --server-url`) — closing it stops nothing. Any
browser pointed at `https://<tailscale-host>:5000` works identically.

**No port conflict with dev.** Dev Flask moved to `:5001` (Vite still `:5173`),
so `start.sh` / `start-server.sh` can run while production stays up, and their
stale-process sweep deliberately skips `:5000`.

## 2. Install the auto-deploy watcher

```bash
ln -sf "$PWD/ops/lunaschal-deploy.service" ~/.config/systemd/user/
ln -sf "$PWD/ops/lunaschal-deploy.timer" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lunaschal-deploy.timer
```

Check it's working: `journalctl --user -u lunaschal-deploy -f`. Each tick logs
its decision (`deploy`, `skip-branch`, `skip-dirty`, `up-to-date`, or `ahead`)
— a `skip-*` result while you're mid-feature-branch is expected and correct,
not an error.

`ahead` means `origin/main` is already an ancestor of `HEAD`: a local commit on
`main` you haven't pushed. It's reported separately from `up-to-date` because
the two shas _do_ differ, and treating that as a deploy would pull nothing and
then still run the unconditional rebuild and restart at the end of this
script — tearing down the production window every 5 minutes until you push.

`deploy-check.sh` also refuses to pull (and exits non-zero) if `origin` isn't
the exact repo URL it was built for — it auto-executes whatever it pulls
(`npm ci` / `pip install` / `npm run build`), so it never trusts a remote
that's been repointed, accidentally or otherwise. If you ever fork or rename
the repo, update `EXPECTED_REMOTE` in `ops/deploy-check.sh` to match.

## 3. Set up the backup destinations

### External HDD

The drive in use is the 7.3 TB exFAT `Expansion`, auto-mounted by udisks at
`/run/media/volodya/Expansion`. That path is keyed to the filesystem label, so
it's stable across reconnects, and `BACKUP_HDD_PATH` points one level inside it.

`backup.sh` decides the drive is present by testing the **mount point** —
the parent of `BACKUP_HDD_PATH` — not the configured directory itself, which
doesn't exist until the first successful run.

If you ever want backups to run without a desktop session having mounted the
drive (udisks mounts on login/plug, so a headless 3am run could find nothing),
give it an `/etc/fstab` entry keyed by `UUID=` (`lsblk -f` to find it) and
repoint `BACKUP_HDD_PATH`:

```
UUID=xxxx-xxxx  /mnt/backup-hdd  exfat  defaults,nofail,uid=1000,gid=1000  0  2
```

`nofail` matters — the backup script already tolerates the drive being
unplugged, but the boot itself shouldn't stall waiting for it.

### Tablet (optional, currently unconfigured)

There is no second destination set up: the tailnet has no Linux machine running
`sshd` (`kozak-1` refuses port 22, and the iPad/iPhone can't host this). Leave
`BACKUP_TABLET_HOST` blank and `backup.sh` skips the tablet entirely. To add one
later:

```bash
# On the tablet:
sudo systemctl enable --now sshd
tailscale up   # if not already on the tailnet

# On the desktop:
ssh-keygen -t ed25519 -f ~/.ssh/lunaschal_backup -N ""
ssh-copy-id -i ~/.ssh/lunaschal_backup.pub <tablet-user>@<tablet-tailscale-hostname>
```

Add an entry to `~/.ssh/config` on the desktop so `ops/backup.sh`'s plain
`ssh`/`rsync -e ssh` calls pick up the key without extra flags:

```
Host <tablet-tailscale-hostname>
  User <tablet-user>
  IdentityFile ~/.ssh/lunaschal_backup
```

Use the tablet's Tailscale MagicDNS hostname (`tailscale status` on either
machine), not its LAN IP — it stays reachable whether you're home or away,
matching how `TAILSCALE_HOSTNAME` is already used for network-mode HTTPS.

### Configure `ops/backup.env`

Only the tablet settings below still matter here; the HDD destination and
retention are set in Settings → Backup (this file's `BACKUP_HDD_PATH` is used
only to seed a database that has never had one).

```bash
cp ops/backup.env.example ops/backup.env
```

Fill in `BACKUP_HDD_PATH` (a directory inside the mount point above) and
`BACKUP_RETENTION_DAYS` (default 14, governs the `db/` tier only). Leave
`BACKUP_TABLET_HOST` / `BACKUP_TABLET_USER` / `BACKUP_TABLET_PATH` blank unless
you've set up a second machine. This file is gitignored — same convention
as `.env`.

### Install the backup timer

```bash
ln -sf "$PWD/ops/lunaschal-backup.service" ~/.config/systemd/user/
ln -sf "$PWD/ops/lunaschal-backup.timer" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lunaschal-backup.timer
```

### Verify once, by hand

```bash
./ops/backup.sh
journalctl --user -u lunaschal-backup
```

### Watch it from the app: Settings → Backup

`systemctl status` is **not** a sufficient check on this job. `backup.sh`
deliberately skips (rather than fails) when the destination is missing, so a
destination that has gone away permanently exits 0 exactly like one that is
unplugged for a night. That is not hypothetical: when this drive moved from a
udisks auto-mount to an fstab one, `BACKUP_HDD_PATH` was left pointing at the
old path and the job reported `Result=success` every night for nineteen days
while backing up nothing.

Settings → Backup watches the evidence on the drive instead — how old the
newest dated snapshot is — and reports `unconfigured` / `unreachable` /
`readonly` / `permissions` / `empty` / `stale` / `ok`.

`readonly` and `permissions` are deliberately separate states for what
`os.access()` reports identically. A read-only _mount_ (`ST_RDONLY`) needs an
fsck and a remount; a read-write mount this user cannot write to is an
ownership problem. On exFAT — which stores no POSIX ownership, so the driver
synthesizes it from mount options — that means the fstab entry is missing
`uid=`/`gid=` and everything is owned by root. udisks sets `uid=` to the
mounting user automatically, so moving a drive from an auto-mount to a
hand-written fstab line silently makes it unwritable for the user the backup
timer runs as:

````
UUID=00E7-4937  /media/expansion  exfat  defaults,nofail,uid=1000,gid=1000,x-systemd.automount  0  0
``` It also refuses to describe a clean
systemd exit as a success when nothing actually landed, shows free space and
the retention window, and has a **Back up now** button that runs `ops/backup.sh`
and shows its log inline. A broken backup pops its section open on load; a
healthy one stays collapsed like every other settings group.

### Settings owns the destination

The destination and the retention window live in the `settings` table and are
edited in Settings → Backup, with a folder picker that browses the server's
filesystem (`GET /api/backup/browse` — a server path is what rsync needs, and a
browser file input cannot produce one). `ops/backup.sh` reads them back out with

```bash
.venv/bin/python -m backend.ops.backup config --get hdd-path
.venv/bin/python -m backend.ops.backup config --get retention-days
````

so the path the panel shows is by construction the path the job writes to. A
locked or missing database falls back to `backup.env` rather than aborting the
night's run.

`ops/backup.env` is now only for the tablet destination, plus a fallback
`BACKUP_HDD_PATH` for a database that has never had one set. The migration that
adds the column seeds it from that file, so upgrading an existing install does
not quietly unconfigure a working backup.

Logic in `backend/ops/backup_status.py` and `backend/routes/backup.py`; tests in
`backend/tests/test_ops_backup_status.py` and `test_routes_backup.py`.

Confirm this layout appears under `BACKUP_HDD_PATH`:

```
db/YYYY-MM-DD/lunaschal.db   dated snapshots, pruned past BACKUP_RETENTION_DAYS
media/                       one flat mirror of the rest of data/
```

To restore, stop the app, copy `media/` back over `./data/`, then drop the
chosen `db/<date>/lunaschal.db` in as `./data/lunaschal.db`.

### Why the two tiers differ

The DB is ~600 MB and changes constantly, so history is worth paying for — you
may want last Tuesday's copy. The media (fanfic, meetings, journal attachments)
is ~6 GB of files that are immutable once written, so dated copies of it would
be pure duplication.

So **`media/` has no `--delete` and is never pruned.** A fic deleted from the
library keeps its only surviving copy there — the mirror is an archive as much
as a backup, and nothing in the retention setting can remove from it.

The original design instead kept a full dated snapshot of everything, deduped
with `rsync --link-dest`. That was dropped because **the backup drive is exFAT,
which does not support hardlinks** (`ln` returns `Operation not permitted`), so
`--link-dest` silently degrades to full copies — 14 complete copies of the
media. That is the one measured, load-bearing constraint.

The two rsync flags are weaker than they look, and both were checked against
the real drive rather than assumed:

- **`-rt`, not `-a`** — exFAT stores no POSIX ownership or permission bits.
  This mount sets `uid`/`gid`/`fmask`/`dmask`, so the driver silently accepts
  the chown/chmod and **`-a` exits 0 too**; `-rt` merely avoids issuing metadata
  calls that can never round-trip.
- **`--modify-window=1`** — insurance against FAT-family timestamp granularity,
  and **not actually required here**: a second run transfers zero files without
  it. exFAT keeps 10 ms resolution; the 2-second worry comes from FAT32.

## How the pieces fit together

```
origin/main (GitHub)
      │  poll every 5 min
      ▼
lunaschal-deploy.timer ──► ops/deploy-check.sh ──► git pull, npm ci / pip install, npm run build
                                   │
                                   ▼
                     systemctl --user restart lunaschal.service
                                   │
                                   ▼
                          ops/run-prod.sh ──► main.py (serves dist/, opens the window)

data/lunaschal.db + data/*  ──►  ops/backup.sh (daily, 3am)  ──►  external HDD
                                                              └─►  tablet (SSH, Tailscale)
```
