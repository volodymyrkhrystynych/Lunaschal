# Torrent tab

Paste magnet links, the server downloads them, and every packet the swarm sees
leaves through ProtonVPN. Reachable from the phone over Tailscale like the rest
of Lunaschal.

## Why the traffic cannot leak

The client is **qBittorrent in a container that has no network stack of its
own**. `torrent/docker-compose.yml` gives it `network_mode: "service:gluetun"`,
so it shares the gluetun container's namespace — and in that namespace the only
route is the WireGuard tunnel, behind gluetun's own firewall. There is no
`enp42s0`, no `tailscale0`, no default route. If the tunnel drops, the client has
no path to the internet at all.

That is the important property: the kill switch is **structural**, not a
setting. Nothing in the app can toggle it off, a reconnect race cannot bypass
it, and a misconfigured client cannot route around it. `backend/torrent/vpn.py`
and the banner in the UI _report_ on the tunnel; they do not enforce anything.

Three layers, and only the middle one is ours:

```
phone ──Tailscale──► Lunaschal :5000 ──loopback──► qBittorrent ──WireGuard──► peers
        (host)         (host)        127.0.0.1:8081   (netns)       (ProtonVPN)
```

Tailscale is untouched by any of this. It runs on the host and carries only your
own traffic to Lunaschal. Lunaschal reaches the client over host loopback — a
hop that never leaves the machine, so it is neither tunnelled nor leaked. Your
home IP is never exposed to a swarm, and your Proton exit IP is never exposed to
Tailscale.

## Why a container and not a network namespace

A hand-rolled `ip netns` + `wg-quick` setup is the other way to get the same
isolation, and it was rejected for three reasons specific to this machine:

- **`ip netns add` needs root**, and every other Lunaschal unit is a
  `systemd --user` unit owned by `volodya`. A root system unit would be the only
  one, with its own lifecycle and its own failure modes.
- **`protonvpn-cli` does not run here.** The AUR build installed into
  `/usr/lib/python3.13/site-packages` while the system is on Python 3.14, so
  every entry point raises `PackageNotFoundError`; its log also shows an
  unresolved SecretService keyring failure. gluetun speaks WireGuard to Proton
  directly and needs none of it.
- **There is no WireGuard tooling and no config to reuse.** `wg`/`wg-quick` are
  not installed and `/etc/wireguard` does not exist — Proton's own design here
  is a NetworkManager plugin, not `wg-quick`. A netns approach would mean
  installing tooling _and_ obtaining a config Proton does not store on disk.

Meanwhile Docker is already running and already part of Lunaschal
(`searxng/lunaschal-searxng.service`), so the container route adds no new
operational surface. `torrent/` mirrors `searxng/` deliberately.

## Setup

1. Generate a WireGuard config at `account.proton.me/u/0/vpn/WireGuard` with
   **NAT-PMP (Port Forwarding)** ticked. Copy the `PrivateKey`. Platform is
   irrelevant (pick GNU/Linux) and **so is the server you choose** — gluetun
   discards the endpoint from the file and picks its own, and
   `PORT_FORWARD_ONLY=on` is what actually restricts it to P2P /
   port-forwarding servers. The only thing read out of that file is the one
   `PrivateKey` line, and the features you tick are baked into it at
   generation time — forget NAT-PMP and you regenerate rather than toggle.
   Leave NetShield off: it is DNS-based and gluetun runs its own DNS.
2. `cp torrent/.env.example torrent/.env` and paste it in. The file is gitignored
   (the bare `.env` line in the root `.gitignore` matches at any depth).
3. `ln -sf ~/workspace/Lunaschal/torrent/lunaschal-torrent.service ~/.config/systemd/user/`
   then `systemctl --user daemon-reload && systemctl --user enable --now lunaschal-torrent`
4. Read qBittorrent's generated temporary password out of its log
   (`docker logs torrent-qbittorrent-1 2>&1 | grep -i 'temporary password'`), set a
   real one
   in its WebUI, and put the credentials in **Settings → Torrents**.

Verify before trusting it:

```bash
curl -s 127.0.0.1:8000/v1/publicip/ip     # must be a Proton IP, not yours
curl -s 127.0.0.1:8000/v1/portforward     # a port, not 0 — proves NAT-PMP took
docker exec torrent-qbittorrent-1 wget -qO- ifconfig.me   # the same Proton IP
docker stop torrent-gluetun-1             # the client loses all connectivity
```

If gluetun logs that no server was found, that is `SERVER_COUNTRIES` combined
with `PORT_FORWARD_ONLY=on` matching nothing — widen it in `torrent/.env`.

## Four details that will otherwise cost an afternoon

- **The published port is 8081, and both sides of the mapping must be the same
  number.** qBittorrent's usual 8080 is llama-server's port on this machine
  (`llama/start-llama.sh`), so Docker refuses to bind it and the stack dies
  before the tunnel is even attempted. And it has to be `8081:8081`, not
  `8081:8080`: qBittorrent validates the `Host` header against its own
  configured port, so a mismatched pair answers every request with a 401 that
  reads exactly like a wrong password.
- **`FIREWALL_OUTBOUND_SUBNETS` is mandatory**, and the single most confusing
  thing to omit. gluetun drops everything that is not the tunnel, including the
  replies to the published WebUI port — those arrive from the Docker bridge
  gateway, not from loopback. Omit it and the WebUI is unreachable in a way that
  looks like a container that failed to start. The compose file pins the
  network's subnet (`10.13.37.0/24`) rather than letting Compose auto-assign, so
  the firewall rule cannot drift out of step.
- **`PUID`/`PGID` must be 1000.** `/media/expansion` is exFAT mounted
  `uid=1000,gid=1000` and stores no POSIX ownership of its own.
- **Two different auth paths, on purpose.** gluetun's port-forward hook runs
  _inside_ the shared namespace, so its call to `127.0.0.1:8081` genuinely is
  localhost and qBittorrent's localhost-bypass covers it. Lunaschal's calls
  arrive from the bridge, are not localhost, and authenticate with stored
  credentials. Only gluetun and qBittorrent live in that namespace, so the
  bypass grants nothing else.

Worth knowing alongside these: qBittorrent 5.x answers a successful
`/auth/login` with **204 and an empty body**, where older builds answered `200
"Ok."`. A client that accepts only `"Ok."` rejects a valid login — see
`backend/torrent/client.py`.

## Port forwarding

ProtonVPN hands out a **different random port on every reconnect**. Without
syncing it into the client you stay unconnectable to peers who cannot initiate,
and seeding barely works — a failure that reads as "the VPN is slow" rather than
as a misconfiguration. `torrent/qbt-port-sync.sh` runs as gluetun's
`VPN_PORT_FORWARDING_UP_COMMAND` and pushes the new port into `listen_port`,
retrying while qBittorrent's WebUI comes up (gluetun usually wins that race).

## The two URLs in Settings are read-only

Settings → Torrents _shows_ the WebUI and control-server addresses but will
not let you edit them, and `PATCH /api/settings/ai` ignores them. They are not
settings: they are where `torrent/docker-compose.yml` publishes. An editable
field there is a false affordance — it looks like the fix for a port conflict
while being unable to move what Docker binds, which is a genuinely confusing
half hour. Change a port in the compose file and restart the stack.

## What the database stores, and what it deliberately does not

**qBittorrent owns every fact about a torrent** — progress, speeds, category,
share limits, file locations — and it survives a Lunaschal restart on its own.
The `torrents` table holds only what qBittorrent has no concept of: the note you
wrote and how long to keep the download. Nothing is mirrored, so nothing can
drift; asking for a torrent's ratio limit always asks the client.

Two consequences that look like oversights against the rest of the codebase:

- **No in-memory progress registry** (unlike `backend/fanfic/download.py`). The
  UI polls the client, which is the source of truth.
- **No `_reset_stale_torrents()`** in `connection.py`. The other long-running
  features track work _this process_ is doing, which a restart orphans. This
  work happens in a container that outlives Flask, so there is no in-flight
  state of ours to strand.

`completed_at` is the one thing copied out of the client rather than read live,
because retention is measured from it and qBittorrent loses `completion_on`
whenever its config is rebuilt.

## Storage, and what exFAT costs

Downloads go to `/media/expansion/torrents` (7.1T free) rather than the root
disk, which is at 90%. `TORRENT_ROOT` on the Lunaschal side and
`TORRENT_DOWNLOAD_DIR` in `torrent/.env` must name the same directory — it is
bind-mounted into the container as `/downloads`, and
`backend/torrent/storage.py` translates between the two names.

exFAT constraints, handled rather than discovered:

- **No sparse files**, so preallocation must be off or every torrent writes its
  full size up front.
- **No hardlinks**, so no hardlink-based post-processing.
- **`" * / : < > ? \ |` are rejected outright**, and torrent names are full of
  colons and question marks. `sanitize_for_exfat()` collapses each to an
  underscore (rather than dropping it, so two names differing only in
  punctuation don't collide) when we choose a save path ourselves.

`ops/backup.sh` excludes `torrents/`: production downloads live outside `data/`
anyway, but the dev default is `./data/torrents` and would otherwise be rsynced
into the nightly snapshot.

## Retention

Off unless a torrent opts in — deleting downloads on a timer is not a reasonable
default, and it cannot be undone by re-running anything. When set, the sweep runs
daily in an **08:00–09:00** window (after the jobs file purge, so a large delete
never overlaps a backup or a model-using pass) and deletes the files along with
the torrent; a retention policy that keeps the bytes has not freed anything.
`backend/torrent/retention.py` is pure and tested at the boundary day.

## Serving files back

`GET /api/torrents/<hash>/files/<index>/download` uses `send_file(...,
conditional=True)`, so range requests work and a video seeks and streams from
the phone over Tailscale rather than having to download whole. It is the one
place a client-reported path reaches the filesystem, so
`resolve_download_path()` resolves symlinks _before_ checking containment — a
torrent can ship a symlink, and a string comparison would pass it.
