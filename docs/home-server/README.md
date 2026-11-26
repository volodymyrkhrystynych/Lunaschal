# Home server move

Moving Lunaschal off Tailscale and off the desktop, with a media server for other people later.

_Written 2026-11-25 against `main` at `d9973f7`. A plan, not a record of built work: nothing here exists yet except the dashboard mockup, which uses sample data._

## Plan at a glance

```
Internet ──443──▶ Pi: gateway (Caddy)
                    ├─ luna.<domain>   client certificate required ─▶ Surface: Luna
                    └─ media.<domain>  only if guests need TVs ─────▶ Surface: Jellyfin
                  Pi: WireGuard (default for guests) ───────────────▶ Jellyfin only

Surface (always on) ──Wake-on-LAN + model calls──▶ Desktop (asleep by default)
```

| Machine                             | Role                                                                                                    |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------- |
| **Raspberry Pi** (old, about 1 GB)  | Public gateway only, if its OS still gets security updates                                              |
| **Surface Pro** (4 GB, always home) | Arch Linux, screen always on: Luna (Flask, SQLite, media, schedulers, job queue), later Jellyfin. No AI |
| **Desktop**                         | llama-server, transcription, Code agent containers; suspends when idle                                  |
| **iPhone, iPad, laptop**            | Clients, each with its own certificate. The Watch needs one passed to it                                |

## Decisions

**You decided:**

- Get off Tailscale and self-host, with strong protection for outside access.
- Use Wake-on-LAN, so you never have to remember to turn the desktop on.
- Let other people use the media server eventually, with at most 5 streams.
- Use the USB Ethernet adapter. The plan puts it on the Surface.

**Working assumption from you:** no AI on the Surface.

**Recommended, not yet confirmed by you:**

- Run Luna on the Surface, with the Pi as a separate gateway.
- Use client certificates (mTLS) for Luna. The alternative is WireGuard, which needs no Swift changes but is still a VPN to connect.
- Harden login before anything is reachable from the internet (see Security).
- Keep guests off Luna; they use Jellyfin, over WireGuard unless they need TVs.
- Store media as 1080p H.264/AAC at 5–6 Mbps and leave transcoding off.

## 1. Security

### Fix first: how a request counts as local

Network mode exempts local requests from login. That rule needs tightening before anything is exposed, and with a gateway in front, every request can look local. The details are tracked privately until the fix ships.

Known consequences to handle:

- The desktop window may have to log in like any other device.
- `stt/listener.py` authenticates with a shared password header and would be calling another machine after the move. It needs a replacement, such as a per-device token.
- Local health checks, smoke tests and scripts may rely on the exemption. Check each one.

### Other login work

- Rate limits and lockout; constant-time comparison.
- Real TOTP. The current 6-digit code is static.
- Server-side sessions that can be revoked. Tokens last 30 days today.
- `Secure` cookies (currently `secure=False`) and HSTS.
- Refuse to start in network mode with the default `JWT_SECRET`.

### Gateway

- One certificate per device, so a lost device is revoked alone. Keep `strict_sni_host` on.
- The Surface's firewall accepts Luna traffic only from the Pi. LAN clients should use the gateway too, so they get the same checks.
- Return 404 to outside requests for backup, logs, repos and Code-agent routes. **Trade-off:** you lose those on the phone away from home.
- The native app must import a `.p12` into its own Keychain and answer the certificate request, including in background uploads.

### Dropping Tailscale breaks more than access

`ops/run-prod.sh` uses Tailscale certificates and refuses to start without one. `main.py`, `vite.config.ts`, start scripts, the browser extension and Apple files also mention Tailscale. Review them before turning it off.

`docs/external-access.md` is out of date: it puts Caddy on the same machine as Flask, which this plan avoids.

## 2. Surface: Luna server

### Works with the desktop asleep

Journal, calendar, tasks, food and spending logging, photos, saved recordings, reading, sync, location, Health data and text search. Also non-AI night jobs: newspapers, email and backups.

### Waits for the desktop

Chat, transcription, captions, polish, tags, embeddings, AI grading and the overnight briefing.

### Needs work

- **Transcription** runs inside Flask. Move it to a service on the desktop under the same queue rules.
- **Desktop-only features** need a small helper there: gaming lock, VRAM view, llama logs, meeting recording, and probably the Code agent. Otherwise the gaming lock would watch the Surface.
- **Storage:** database on the Surface's internal disk, never a network share. Large media on an external ext4 drive. Decide where archive videos, Knowledge files and torrents live.
- **Backups** move too. Secrets (`.env`, APNs key) must be copied over safely.

### Hardware notes

- Wired through the USB adapter, with its IP reserved by the adapter's MAC address.
- With one USB port, use a powered USB 3 hub for the adapter and drive.
- Cap the battery charge if the firmware allows, since it is always plugged in.

## 3. Desktop: sleep and Wake-on-LAN

| What happens                                          | Desktop                         |
| ----------------------------------------------------- | ------------------------------- |
| Chat opened, message sent, chat dictation, AI grading | Wakes                           |
| Briefing due (about 04:50)                            | Surface wakes it                |
| Recording, photo or journal entry saved               | Queues; no wake                 |
| Queued work waiting about 2 hours, daytime only       | Wakes once and clears the queue |
| Idle about 20 minutes                                 | Suspends                        |

The times are starting guesses.

- **Waking needs an explicit trigger.** Calls count as "someone is waiting" unless marked as background, so waking on every such call is too broad.
- **Pause wins:** if inference is paused, nothing wakes the desktop.
- Keep it awake during use, games, model calls, Code-agent tasks, SSH, or with a "keep awake" switch.
- **Torrents:** constant seeding would stop it sleeping. Ignore seeding or move torrents to the Surface.
- **Night jobs** have fixed windows (titles 02:00–03:00, repo work 03:00–05:00, briefing 05:00–07:00) and do not use `llm_jobs`. Run titles, then the briefing, then repo work if time allows. A missed briefing should be reported.

### Required either way: an "unreachable" state

Today a job is deferred only when inference is paused or preempted. Any other exception, including a failed connection, is recorded as an error and not retried (`backend/ai/jobs.py`, lines 201–205). A handler that swallows the exception can be marked done without doing the work. Extend the deferral mark in `backend/ai/service.py`.

### Network and setup

- llama-server binds to 127.0.0.1 and has no real API key. Give it a key and a firewall rule for the Surface only.
- Wired Ethernet; BIOS Wake-on-LAN on, ErP off, restore on AC power loss.
- Make `ethtool -s <iface> wol g` permanent.
- Suspend rather than shut down. Check that the model survives resume.

## 4. Jellyfin for guests (last)

- Never give guests Luna access or a Luna certificate.
- Container, own user, read-only media, no access to Luna's data. Firewalled from Luna, the desktop and the LAN.
- Separate library folder holding only what you mean to share.
- One non-admin account per person, one stream each; no delete, transcoding or public signup.
- Keep admin LAN-only. If public, add login limits and fail2ban and update promptly.
- Share only media you have the right to share, and check that the ISP allows hosting.

### Capacity for 5 remote streams (estimates)

| Quality             | Total      | Upload plan to have |
| ------------------- | ---------- | ------------------- |
| 720p, 3–4 Mbps      | 15–20 Mbps | 30 Mbps             |
| **1080p, 5–6 Mbps** | 25–30 Mbps | **40 Mbps**         |
| 1080p, 8 Mbps       | 40 Mbps    | 60 Mbps             |

- Upload is the likely limit. Surface, drive and adapter should cope; test the Pi with five streams.
- Encode files to the target bitrate in advance. **Do not set a Jellyfin bitrate cap with transcoding off:** files over it may not play.
- Prioritize Luna at the router.
- Consider a used Intel mini PC if guests are regular or on TVs with mixed formats.

## 5. Wall dashboard

Mockup: [dashboard-mockup.html](dashboard-mockup.html), sample data only. Open it in a browser; `+`/`-` change the text size, `m` cycles the modes.

**You decided:** a Surface-only web app, separate from the regular one and exempt from iOS-first. Weather gets the most space, with some to-dos and past performance. Landscape (the Surface is taped to the wall), manually adjustable text size, default 20. Touch stays off because of phantom touch.

**Recommended:** display-only with its own read-only token; weather always for the saved Home, never posting a location; no weight, spending, journal, selfies or email on screen; Chromium under `cage`. Show the last data and its age if Luna is down.

- Phantom touch can be a sign of a swelling battery. Inspect it, and check that the mount does not trap heat.
- Disable touch with a udev rule, since an X11 setting will not apply under `cage`.
- New backend work: multi-day forecast and one combined read-only endpoint.
- Budget 0.5–1 GB RAM for the browser. Measure before adding Jellyfin.
- Update Arch deliberately, after a backup.

## 6. If the Pi is unusable

Skip a Pi 1 or Zero, or anything without security updates. Then the Surface runs the gateway too, as separate users or containers. Luna binds to loopback, the local-request fix must already be in, mTLS stays on, and guests use WireGuard only. Do not put Jellyfin on the gateway Pi.

## Build order

1. Tighten the local-request rule. Proposed branch: `fix/auth-local-requests`.
2. Add the unreachable state. Proposed branch: `feat/inference-unreachable`.
3. Test Wake-on-LAN on the real desktop.
4. Add waking and auto-suspend, with status in the iOS app first.
5. Move Luna to the Surface and split out transcription, still on Tailscale.
6. Harden login, add the Pi gateway and mTLS, then drop Tailscale.
7. Add Jellyfin.

Moving Luna before changing public access avoids debugging both at once.

## Open questions

- [ ] Any signs of battery swelling on the Surface?
- [ ] Is text size 20 readable from where you stand?
- [ ] OK to create the two branches?
- [ ] mTLS or WireGuard for your own devices?
- [ ] Which Pi and OS? `cat /proc/device-tree/model; cat /etc/os-release`
- [ ] Which Surface Pro, and how big is its disk?
- [ ] How big is Luna's `data/`?
- [ ] Is the adapter gigabit, and which chip? `lsusb`
- [ ] Can the desktop be wired?
- [ ] What is the wired upload speed?
- [ ] Does the ISP use CGNAT? If so, port forwarding will not work.
- [ ] Which domain? Put Luna on a subdomain from the start.
- [ ] Are all five streams remote, and are any on TVs?
- [ ] Are the admin and Code-agent routes blocked away from home?

## Tests before committing

- [ ] From the Surface: suspend the desktop, then `wakeonlan <MAC>`. Repeat after an hour and after a full shutdown.
- [ ] Time the first chat reply after resume.
- [ ] Surface on Linux for a few days: ping log and `iperf3`.
- [ ] Five streams through the Pi.

## How we got here

1. **Leaving Tailscale:** proposed a Pi gateway with mTLS; found login work that must come first.
2. **Surface as Luna's home:** feasible, but job handling must survive an unreachable model server.
3. **No AI on the Surface:** fits 4 GB; transcription must move to the desktop.
4. **Waking:** considered on-demand Wake-on-LAN, then a timed nightly wake. You chose Wake-on-LAN, which now covers the briefing too.
5. **Cost:** sleeping 7 hours a night saves roughly 15 kWh a month (assuming 70 W idle). The main gains are Luna's uptime and separating the desktop from the public internet. Leaving Luna on the desktop behind the Pi is the much smaller job.
6. **Guests:** Jellyfin, kept separate. The Pi could run it but is better as the gateway.
7. **Surface networking:** Wi-Fi reliability was the main worry; your Ethernet adapter addresses it.
8. **Five streams:** fine without conversion; upload speed decides. Corrected the earlier bitrate-cap advice.
9. **Dashboard:** the Surface's screen is always on, so it is not headless as first assumed. It becomes a wall display.
