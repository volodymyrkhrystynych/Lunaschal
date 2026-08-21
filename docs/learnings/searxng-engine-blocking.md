# Which SearXNG engines still answer a self-hosted instance

> Measured 2026-08-20 on the desktop, from its plain residential egress IP,
> SearXNG `2025.12.1+ab8224c93` in Docker. Engine blocking moves; **re-measure
> before believing this table**. The method at the bottom is one shell command.

Web search for the Ideas research agent and the chat delegate goes through a
self-hosted SearXNG (`searxng/`, `backend/research/web.py::_search_searxng`).
It stopped returning anything at all — not an error, not a timeout, just
`{"results": [], "unresponsive_engines": [...]}` on every query.

## What was actually happening

SearXNG enables four engines for the `general` category by default. All four
refuse this instance:

| Engine     | What comes back                                                             |
| ---------- | --------------------------------------------------------------------------- |
| google     | The `/httpservice/retry/enablejs` JS interstitial — **parses as 0 results** |
| duckduckgo | `CAPTCHA` (`suspended_time=0`)                                              |
| brave      | `Too many request` (`suspended_time=3600`)                                  |
| startpage  | redirect to `/sp/captcha` (`suspended_time=86400`)                          |

Google is the one worth staring at: it fails **silently**. The engine is an
xpath scraper, the interstitial is valid HTML with no result nodes, so it
reports zero results rather than an error and never appears in
`unresponsive_engines`. From the app's side a Google-only block is
indistinguishable from a query nobody has written about.

**This is client fingerprinting, not a bad exit IP.** The host and the
container egress from the same residential address and no VPN is in the path —
deliberately, see the comment at the top of `searxng/docker-compose.yml`. The
engines are recognising SearXNG's httpx client, and no configuration on our
side changes their minds. Adding a commercial VPN makes it strictly worse: the
shared exit IPs get blocked or CAPTCHA'd within minutes, which is what the
compose comment already records from the Proton test.

## What answers

Probing all 21 plausible `general` engines one query at a time
(`q=fsrs spaced repetition algorithm`):

| Answers            | Refuses                                                   |
| ------------------ | --------------------------------------------------------- |
| bing (10)          | google (silent), duckduckgo (CAPTCHA), brave (429)        |
| yandex (10)        | startpage (CAPTCHA), mojeek (403), qwant (403), yep (403) |
| seznam (10)        | ask / stract / rightdao (5xx), wiby (conn), mwmbl (crash) |
| yahoo (7)          | presearch (timeout at the stock 3 s), crowdview (0)       |
| encyclosearch (15) |                                                           |
| searchmysite (10)  |                                                           |

`searxng/settings.yml` turns the first four on and the four defaults off. That
is four indexes — Bing's (bing, yahoo), Yandex's, Seznam's — so no single
block empties the agent's search again. A live fan-out query went from 0
results to **39–40 with an empty `unresponsive_engines`**.

`encyclosearch` and `searchmysite` answer readily but index the indie and
encyclopedia web; they bury a general query in low-relevance hits, so they are
documented rather than enabled.

`outgoing.request_timeout` is raised 3 s → 6 s in the same file. Nothing here is
user-facing — the research agent is already waiting on a 25 tok/s model — so a
few seconds is a cheap price for engines that are merely slow.

## The consequence for `web.py`

`_dispatch` retried a soft block on the 5/15/30 s backoff it shares with the
fanfic downloader. But **`Suspended:` means SearXNG will not make the request
at all** — for an hour after Brave's 429, a day after Startpage's CAPTCHA. The
three retries bought 50 seconds of the identical empty payload, per search, for
as long as the suspension lasted.

So `SearchSoftBlocked` now carries `retryable`. It is False only when _every_
failed engine is already suspended, and that case opens the circuit breaker
immediately instead of spending the retries first — the suspension outlives the
300 s cooldown anyway. A fresh CAPTCHA (DuckDuckGo's `suspended_time=0` keeps
the engine in rotation) or a plain timeout beside it still retries: there, a
later attempt can genuinely answer differently.

## Re-measuring

One engine, one query, no app involved:

```bash
curl -s 'http://localhost:8888/search?q=test&format=json&engines=bing' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['results']), d['unresponsive_engines'])"
```

`0 []` is the Google-shaped silent block; `0 [["x", "..."]]` names its own
reason. `docker logs searxng-searxng-1` has the traceback and the
`suspended_time` behind each one. Flip an engine back in
`searxng/settings.yml` the day it answers again — `docker compose restart` is
what applies it.
