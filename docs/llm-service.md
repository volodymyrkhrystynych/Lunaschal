# One model service: priorities, a durable queue, and a GPU pause switch

## Why

Two problems, and the second is why the first was worth doing properly.

**The card is never free.** `[qwen36]` holds ~5.7 GB of a 7.8 GB card for as
long as llama-server runs. That is deliberate — reloading 22 GB costs tens of
seconds, so there is no idle timeout — but it means the GPU can never be used
for anything else. The only way out was `systemctl --user stop lunaschal-llama`
from a shell, which also kills the two CPU-only presets and leaves every
background daemon failing quietly all night.

**Nothing owned model calls.** There were seven independent request sites and
an _advisory_ gate (`backend/ai/priority.py`) that only some callers consulted.
The consequences were all in the tree already:

- `backend/ai/background.py` gave **every** job on its shared FIFO an
  `interactive` mark, so background enrichment outranked the workers that were
  politely deferring to the user.
- `briefing_scheduler` consulted nothing at all, and is the largest single
  consumer of the card in the app: a 45-minute life-wiki pass plus a briefing,
  unconditionally, every night.
- The curated-tag scan makes one model call per journal entry with no gate, no
  checkpoint and no bound.
- Journal polish, idea polish, food and workout structuring and journal
  metadata wrote no pending marker, so a failure meant the work was silently
  never done.

A pause switch bolted onto that would have been seven more conditionals. So the
switch is a property of a service instead.

## The shape

```
   P1 callers (user waiting)          P2 jobs (durable)
   chat, manual polish, ideas,        llm_jobs table
   tailoring, learning grading…              │
            │                       ┌────────┴────────┐
            │                       │   job worker    │
            │                       └────────┬────────┘
            └──────────┬─────────────────────┘
                       ▼
         ┌─────────────────────────────┐
         │  backend/ai/service.py      │  admission, ordering, preemption, pause
         │  GPU lane  │  CPU lane      │
         └─────────────────────────────┘
                       ▼
                  llama-server
```

Two layers, because a blocked caller thread cannot survive a restart no matter
what the queue is made of:

- **`backend/ai/service.py`** — the broker. In-memory, owns admission,
  ordering, preemption and the pause. Callers block on `slot()` and get a
  result or an exception. Replaces `priority.py` entirely.
- **`backend/ai/jobs.py` + the `llm_jobs` table** — the durable P2 queue.
  Replaces `background.py`'s in-memory FIFO. A job is a **row**, executed by a
  handler named in `backend/ai/job_handlers.py`.

Long-lived schedulers (research, briefing, code-wiki, job triage) do _not_ get
`llm_jobs` rows. They already keep their own DB state and re-plan each tick, so
they simply run under `service.background()` and let their own rows be the
queue.

## Priority is a property of the thread

A background job is P2 _as a whole_ — the polish, the metadata pass, the
nightly briefing — and it reaches the model through four or five layers of
feature code with no business knowing about priorities. Threading a `priority=`
kwarg down all of those would have touched some forty call sites and been wrong
the first time somebody added a fifth.

So the worker wraps the job in `service.background()` and every model call that
job makes inherits it. The default is INTERACTIVE deliberately: a call from a
thread nobody classified is a request handler, and treating an unclassified
call as background would silently defer a user who is waiting.

The classification rule is **"was the input durably saved before the model
ran?"** If yes, it is P2. That makes journal polish P2 (the entry is saved) and
the manual Polish button P1 (you pressed it and are holding the response open),
even though both end up in the same function.

Three exceptions, chosen deliberately: learning attempt grading, chat photo
pre-read and calendar category classification are P1 despite being saved first,
because a user is watching the specific thing they enrich.

## Preemption is real, not advisory

When a P1 arrives and a P2 is generating, the broker sets that P2's cancel
event. The caller stops iterating and closes the HTTP response, and
llama-server answers the client disconnect by posting a cancel task to the
**front** of its own queue — `~server_response_reader` → `stop()` in
`tools/server/server-queue.cpp`, driven by `req.is_connection_closed`.

That is the fact the whole design rests on, and it was checked against the
llama.cpp source rather than assumed: aborting the request genuinely frees the
slot, so throwing the partial generation away actually buys the GPU back within
a token instead of at the end of the generation.

**This is why every request is issued as a stream, including the blocking
ones.** A blocking `create()` sits inside the SDK with no way for another
thread to interrupt it, so a background call could not be preempted however
much the broker wanted to. The blocking helpers stream, accumulate, and return
the shapes they always did — no caller can tell.

### Starvation

Cancel-and-requeue means a P2 could be killed repeatedly on a busy day and
never finish. Three things prevent that:

- **Small units.** A queue item is one model call, never "the nightly pass".
  Losing one costs seconds. The existing per-article and per-turn DB commits
  already give this granularity, and the service must not coarsen it.
- **A cancel counter.** `llm_jobs.cancels` is incremented on each preemption,
  and past `MAX_CANCELS` (3) the job runs non-preemptibly.
- **Agent loops keep their checkpoint** for cancellation, and their completed
  turns are already durable, so a preemption costs one turn.

## Two lanes

`[qwen36]` is the only preset on the GPU (`n-gpu-layers = 999`).
`[gemma4-12b-omni]` and `[embed]` set it to 0. They contend for different
things, so they queue separately: a photo caption never waits behind a nightly
briefing, and the pause leaves captioning and embeddings working.

This is not free — the CPU presets still share the 8 threads holding qwen36's
routed experts — so **freeing the GPU is not freeing the machine**. The lanes
are about queueing, not isolation.

The lane is derived from the resolved **alias**, never from the feature,
because `images.py`'s `_repoint_vision_at_qwen36` can put the vision path on
the chat model — at which point it is GPU work and must be gated like it. A
future preset placed on the card has to join `_gpu_aliases()`.

One consequence worth knowing: `describe_audio` is **hybrid**. Its per-window
passes run on the CPU alias but the final reduce goes to `qwen36`, so it
straddles both lanes.

## The pause switch

`POST /api/settings/inference/pause` writes `settings.inference_paused` **first**,
then POSTs `/models/unload` to the router. The order is the whole trick: the
router loads on demand, so between an unload and the gate taking effect any
queued call naming the alias would pull 22 GB straight back onto the card — and
the unload would look like it had silently failed.

Both halves are required. The unload frees the card; the broker is what keeps
it free.

A failed unload is **not** an error. llama-server being down, or the model
already being unloaded, does not change the state that matters — the flag. The
response carries `unloaded`/`unloadError` so the panel can say so.

Resume deliberately does **not** POST `/models/load`. The next real request
reloads it lazily; spending tens of seconds pulling 22 GB onto the card because
somebody pressed a button is a surprise, not a service.

The flag lives on `settings` rather than in memory because a pause is measured
in hours: a gaming evening outlives at least one Flask reload, and coming back
from a restart with the model quietly reloaded is exactly the failure the
switch exists to prevent.

### While paused

|             |                                                                               |
| ----------- | ----------------------------------------------------------------------------- |
| GPU-lane P1 | `InferencePaused` → 503, with a banner offering Resume                        |
| GPU-lane P2 | row stays `pending`; schedulers skip their tick                               |
| CPU lane    | unaffected — captions, audio description, embeddings                          |
| STT         | **untouched.** Parakeet is CPU and 0 VRAM; `WHISPER_DEVICE` defaults to `cpu` |

**Recording keeps working, and that is tested.** `backend/ai/journal.py` turns
every model failure into `PolishUnavailable`, and the voice-draft pipeline
already saved raw text on that — so a clip recorded during a pause still
becomes an entry with its transcript. What changed is that the missed polish is
now _queued_ instead of dropped, so "it'll run when you turn this back on" is
true for dictation too.

Note that an overridden `WHISPER_DEVICE=cuda` (the `stt/service.py` default,
though `backend/routes/stt.py` defaults to `cpu`) is a separate GPU consumer
this switch does not release.

## Three ways a job can end, and only one is a failure

- `InferencePaused` — the GPU is off. The row stays `pending` and the worker
  stands down. This is the queue working.
- `Preempted` — an interactive call took the lane. The row stays `pending` with
  `cancels` incremented.
- Anything else — `status='error'` with the message, and it stops there.
  Deliberately no automatic retry: a failing job retried in a loop is how a
  broken model call becomes a busy loop against llama-server.

### The first two are detected twice, and the second way is the one that fires

Both of those exceptions mean _run this again later_. Both are also ordinary
`Exception`s, travelling up through code written long before either existed —
and ten of the fourteen job handlers wrap their model call in
`except Exception` on purpose, so that a failed enrichment can never break the
row it was enriching. Those catches swallow the retry signal.

That is not a hypothetical. An evening's screenshots were captioned with the
GPU paused; each caption was recorded as a permanent failure on the attachment,
each handler returned normally, and the worker marked every job `done`. Turning
the switch back on found an empty queue.

The obvious fix — deriving the two from `BaseException`, the way
`KeyboardInterrupt` is — is the wrong one. Flask's `full_dispatch_request`
catches `Exception` and nothing wider, so `@app.errorhandler(InferencePaused)`
would stop firing and every paused interactive route would answer a Werkzeug
500 instead of the 503 carrying the flag the UI reads to offer Resume.

So the signal also travels **beside** the exception. `slot()` records `'paused'`
or `'preempted'` on a thread-local as it raises; `process_one` clears it before
calling a handler and reads it back after the handler returns normally,
requeueing on it. A handler can swallow the exception. It cannot swallow the
mark. `deferred_reason()` in `backend/ai/service.py` is the whole mechanism.

Raising is still the contract. `backend/ai/images.py` already re-raised both,
and the two callers that record a per-row status now do too — the journal
attachment path and the chat photo read, which share a vision alias that
`_repoint_vision_at_qwen36` puts on the GPU lane. Their rows are left alone
while the job waits, so they read `idle` after an upload and `running` after a
retry rather than carrying an error the user has to clear. The mark is the
backstop underneath that: for the callers written before either exception
existed, and for the next one nobody remembers to guard.

## Seeing what it did — Settings → Logs

Everything above makes a call that _doesn't_ happen a normal outcome. Before
the service there was one reason a model call went missing (it broke); there
are now five, and from the outside they are identical:

| It didn't run because…            | Where that shows up                                                    |
| --------------------------------- | ---------------------------------------------------------------------- |
| the GPU is paused                 | a `refused` event                                                      |
| an interactive call took the lane | a `preempt` event, naming what took it                                 |
| it queued behind something else   | a `call` event with a large `waited`                                   |
| it failed                         | a `call` event with `outcome='error'`, or an `llm_jobs` row in `error` |
| its handler was renamed away      | a `pending` row whose `kind` is in no registry                         |

So the service keeps its own log, and the panel reads **two stores, because the
two questions have different lifetimes**:

- **Events** — `backend/ai/service.py`'s ring buffer, capped at `EVENT_LIMIT`
  and held in memory. Answers "what is happening now, and why is that call
  slow": every admitted slot records its lane, priority, how long it _waited_
  and how long it _ran_ as separate numbers, because a nine-second reply looks
  identical whether the model was slow or the lane was busy, and only those two
  apart say which. Not a table: a row per model call would be thousands of
  SQLite writes a day to answer a question only ever asked live.
- **Jobs** — the `llm_jobs` rows, which outlive a restart. Answers "why is
  yesterday's entry still unpolished", which is the question that actually gets
  asked, and which a ring buffer cannot answer a day later.

Every event is _also_ emitted through `logging`, so the same story reaches
journald and survives a restart. The buffer is the copy readable from the phone:
production's journal has these lines interleaved with everything else the app
prints, and a dev run has no `systemd --user` journal at all.

`GET /api/settings/inference/activity` returns both halves plus the counters
since startup (`calls`, `preempted`, `refused`, `errors`, `leaked` — `leaked`
should stay at zero) and the handler registry, so an orphaned `kind` is named
rather than sitting `pending` forever with no explanation.

The panel lives in **Settings → Logs**, above the journal viewer, not in the
pause switch above the tabs: that switch is a control and stays a switch and a
sentence, and this is a debugging tool that belongs where somebody debugging
already looks. It is collapsed by default and its query is gated on being open —
polling a log nobody opened is a request every three seconds all day.
