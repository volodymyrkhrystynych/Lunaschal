# Deployment & backups

Two `systemd --user` pieces run on the desktop machine that also serves as the
Lunaschal server:

- **`lunaschal.service`** — the production app (built `dist/` + the PyWebView
  window), restarted automatically whenever new code lands.
- **`lunaschal-deploy.timer`** — polls `origin/main` every 5 minutes; if it's
  ahead and the local checkout is a clean `main` (never a feature branch, never
  a dirty tree — this machine is also used for day-to-day development), pulls,
  reinstalls changed dependencies, rebuilds, and restarts `lunaschal.service`.
- **`lunaschal-backup.timer`** — daily at 3am, snapshots the DB + all of
  `data/` to the external HDD and the tablet.

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

This opens the Lunaschal window automatically at the next graphical login
(`WantedBy=graphical-session.target`) and keeps Flask serving `dist/` with
`NETWORK_MODE=1`, so the Pocket 2 / phone / tablet can all reach it.

**Port conflict note:** `start.sh`/`start-server.sh` (interactive dev mode)
and `lunaschal.service` (production) both bind `:5000`. Before a manual dev
session, run `systemctl --user stop lunaschal`; `systemctl --user start
lunaschal` again when done. This is a manual step by design — keeping the
service simple was preferred over auto-detecting a conflict.

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

Mount the 4TB drive at a **stable** path — a `/etc/fstab` entry keyed by
`UUID=` (`lsblk -f` to find it), or a udev rule, rather than relying on
whatever device/label the kernel assigns on reconnect:

```
UUID=xxxx-xxxx  /mnt/backup-hdd  ext4  defaults,nofail  0  2
```

`nofail` matters — the backup script already tolerates the drive being
unplugged, but the boot itself shouldn't stall waiting for it.

### Tablet (Surface Pro, Arch Linux)

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

```bash
cp ops/backup.env.example ops/backup.env
```

Fill in `BACKUP_HDD_PATH` (the fstab mount point above), `BACKUP_TABLET_HOST`
/ `BACKUP_TABLET_USER` / `BACKUP_TABLET_PATH`, and `BACKUP_RETENTION_DAYS`
(default 14). This file is gitignored — same convention as `.env`.

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

Confirm a `YYYY-MM-DD/data/` directory shows up under both `BACKUP_HDD_PATH`
and on the tablet, containing `lunaschal.db` plus the rest of `data/`
(fanfic, meetings, journal attachments, etc.). Each day's directory is a full,
independently restorable snapshot — restoring means copying that directory's
`data/` back over the live `./data/` (with the app stopped first).

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
