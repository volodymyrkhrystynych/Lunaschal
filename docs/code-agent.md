# The Code tab: a local coding agent driven from the phone

Status: **Stage 1 (core), Stage 3 (iOS + web) and the server half of Stage 4 (APNs) are built and tested
off-device. Not yet run end to end:** the container image has not been built, no agent has run against
the real model, and push needs its server key (setup at the bottom). Stage 2, the head-to-head test that
picks the agent, is tooling ready to run (`agent/bakeoff/bakeoff.py`).

## What it is

Chat → **Code** on the iPhone (and the desktop). Describe a change; an off-the-shelf coding agent —
**pi**, **omp** or **Qwen Code**, whichever wins the head-to-head — explores the repo in a sealed
container and writes a plan. You approve it (or reply to revise it), get a push when it has a question
or is done, review the diff and the test run on the phone, and tap **Push & open PR**.

There is **one agent**. Lunaschal is not a second one: it is the agent's client (ACP over stdio, the
role Zed plays for its agents) and its model proxy. The Chat delegate (`backend/delegate/`) is
unrelated and untouched — chat and the coding agent only share the GPU.

```
iPhone Code page ──/api/code──▶ Flask: routes + worker + ACP driver ──docker exec -i──▶ container (the agent)
                                     ▲                                                  │ http://127.0.0.1:8080
                                     └── proxy on the task's Unix socket ◀── socat ─────┘ (--network none)
                                          │ service.slot(GPU, P2, 'code_agent')
                                          ▼
                                     llama-server
```

## Why it is shaped like this

**Asynchronous because the model is slow.** ~34 tok/s generation, ~450 tok/s prefill: a real task is
tens of minutes. So a task is a row that moves between the worker and a person —
`queued → planning → awaiting_plan → (approve) → queued → running → testing → done` — and every state
a person waits on (`awaiting_plan`, `needs_input`, `done`, `error`, `interrupted`, plus `stuck` from
the proxy) is a push notification. **Each phase is its own worker run with its own agent process**: a
plan left unapproved overnight holds neither the worker nor a process. The ACP session resumes across
runs with `session/load` where the agent supports it; otherwise the next session's prompt carries the
task and the approved plan. The container stays up between phases.

**One task at a time.** The GPU lane has two slots and chat needs one.

**The proxy, not the agent, talks to llama-server.** Every model request the agent makes goes through
`backend/ai/service.py` at background priority, so a chat message preempts it, the GPU pause stops it,
and Settings → Logs shows it (label `code_agent`). Three things happen there the agent never sees:

- *Preemption becomes a slow reply.* The upstream request is closed (freeing the server slot), re-queued
  behind the chat call, and sent again unchanged. Responses are therefore always **buffered** — a replay
  must not duplicate tokens — and a streaming agent gets SSE `: keepalive` comments meanwhile.
- *Tool calls written inside the thinking block are repaired* (`toolfix.py`). Qwen 3.5/3.6 sometimes opens
  `<tool_call>` before closing its reasoning (llama.cpp #20837, #22684); llama-server then files the call
  as reasoning and the agent sees a turn that "finished" having done nothing. Turning thinking off
  prevents it (that is what the app's own tool loop does), but Qwen needs thinking for anything
  non-trivial, so the proxy **promotes** a complete, schema-valid call to a real `tool_calls` entry,
  **pokes** the model to re-emit a broken one (at most twice), and otherwise records **stuck**.
- *A floor on `max_tokens`* (32k): at 8k, file writes were cut off and saved half-written
  ([doug.sh's omp tuning notes](https://doug.sh/posts/tuning-a-local-coding-agent-oh-my-pi/)).
- *Qwen's coding sampler* (temperature 0.6, top-p 0.95, top-k 20, min-p 0, presence 0), filled in
  where the agent sent none. The `[qwen36]` preset runs Qwen's general-thinking sampler (1.0) because
  it serves every `chat_json` call in the app; without this the agent would write code at 1.0.

Every `call` event also records llama-server's `prompt_n` / `cache_n` as `prefilled` / `cached`, and
the terminal shows them ("Model: 4s · 1.2k new, 148k cached"). Qwen3.6 is a hybrid model, and
llama.cpp is known to throw its prompt cache away on hybrids (#22746, #21831). At 150k tokens that is
minutes of prefill, which otherwise just looks like a slow model. If it happens on the server, the fix
is `cache-ram` / `ctx-checkpoints` in `llama/presets.ini`.

## Lookups: the library, the repo's wiki, and the web

The container has no network, so the agent's lookups go to the host through its model socket:
`GET/POST /lunaschal/tools[/<name>]` on the proxy, called by a pi extension
(`agent/config/pi/extensions/lunaschal.js`). The host runs them with the app's own toolboxes
(`backend/codeagent/lookups.py`):

| Tool | Backed by | Offered when |
|---|---|---|
| `library_search` / `library_read` | the offline library: Kiwix plus the docs packages | always |
| `web_research` | the Chat tab's research delegate, searching through **Tavily on the code agent's own key** | Settings → Research → Code agent is on (default) and a key is saved |
| `repo_wiki_search` / `repo_wiki_read` | the repo's nightly code-wiki notes plus research notes | the repo has notes |
| `code_map` | the repo's graphify graph (of the mirror, so main, not the agent's edits) | the repo has a graph |

Rules:
- **The endpoint is the boundary, not the extension.** The agent owns `$HOME` and can curl the
  socket. So names are checked against what the task was offered, arguments and results are clipped,
  and each task has a budget of 200 lookups and 20 web runs.
- **Library first.** The library tools and the delegate share one `ResearchTools`, so the Chat tab's
  gate holds across the whole task: the web is refused until the library has been searched (and read,
  if it found anything). An inherently current question can skip it (`current: true`).
- **The web researcher's own model turns run at P2** and yield to chat like the agent's.
- **Tavily is selected per call stack** (`web.search_override`), so the app's Brave/SearXNG quota is
  neither spent nor needed. Pages are still fetched by the host's SSRF-guarded fetcher. The search
  circuit breaker is shared with the app's provider, so a Tavily outage also pauses the app's search
  for the cooldown.
- **Slow lookups send whitespace keepalives** before their JSON: Node's `fetch` gives up on a
  response that is silent for five minutes, and a web run can take longer.

Web access here is a deliberate exception to "unattended work gets no web"
(`backend/research/CLAUDE.md`), made at the user's request.

## Isolation

The container (`backend/codeagent/container.py`) runs with `--network none`, a read-only root, a
sized `/tmp` tmpfs, `--cap-drop ALL`, `no-new-privileges`, the host user's uid, and pid/memory/CPU
limits. Exactly three mounts: the task's clone (`/work`), its `$HOME`, and the socket directory.
**Never the Docker socket.** The model is reached through the proxy's Unix socket, bind-mounted in and
bridged to `127.0.0.1:8080` by socat — the only way out of the container, and it only reaches the
proxy. The socket path is also what identifies the task: there is one proxy per task.

Everything the agent might need from the network is in the image (`agent/Dockerfile`): Python 3.13
with `requirements-agent.txt` (the app's requirements minus the desktop window and speech models — a
test keeps the two in step), the frontend's `node_modules`, and the three agents pinned to versions at
least two weeks old. A new dependency means a rebuild; the worker rebuilds when the hash of the image
inputs changes.

**The host never runs git inside the agent's clone.** The agent can write all of `/work`, including
`.git/config` and `.git/hooks`, and git executes things named there (`core.fsmonitor` on any status,
hooks on push, `diff.external` on diff). So the agent's commits leave the container as a **bundle on
`docker exec`'s stdout** — not even its path is the agent's to choose — and are fetched into a
host-owned bare repo, `out.git`, whose base objects come from the mirror through `alternates`. The
review diff and the push both come from `out.git`. The workspace itself is cloned on the host *before*
the agent exists, with `--no-hardlinks` (shared inodes would let the container corrupt the mirror) and
with `origin` removed.

The plan phase cannot leave changes behind: write permissions are refused at the ACP prompt, and
because pi has no permission system, the tree is `reset --hard` afterwards if dirty.

The **result is measured, not reported**: after the agent finishes, Lunaschal runs the repo's test
command itself (`repos.agent_test_command`, default `npm run test:all` when the repo has it) and that
exit code is what the phone and the PR show.

## Keeping the agent off `main` (free, on a public repo)

1. **Structural**: the container has no credentials and no remote. Pushing happens on the host, from a
   tap, and `mirror.push` builds exactly one refspec, `refs/heads/agent/<ULID>`, refusing anything else
   before git runs. The token travels as `GIT_CONFIG_*` env vars, never argv or a config file.
2. **GitHub ruleset on `main`** (rulesets are free on public repos): require a pull request, block force
   pushes and deletion. You are on the bypass list.
3. **A machine account** pushes and opens the PR. It is a collaborator but *not* on the bypass list,
   so GitHub itself refuses its pushes to `main` even if layer 1 had a bug. Your own token would bypass
   layer 2, which is why this is not your token.

## Choosing the agent

| | pi | omp | Qwen Code |
|---|---|---|---|
| Version pinned | 0.87.1 (+ `pi-acp` 0.0.34) | 18.12.2 (Bun 1.4.3) | 0.25.2 |
| Driven over | ACP via the `pi-acp` adapter | native ACP (`omp acp`) | native ACP (`qwen --acp`) |
| Prompt size | minimal — cheapest at 450 tok/s prefill | larger: many tools, subagents | largest (Gemini CLI fork) |
| Edits | retyped text | hash-anchored lines: fewer output tokens | retyped text |
| Permissions | none (the container is the sandbox) | over ACP | approval modes |
| Reports with this model | positive (similar 3080 Ti setup) | positive after tuning | none found |

**pi and `pi-acp` are pinned as a pair.** pi-acp asks pi for RPC commands older pis lack, and pi
answers an unknown command with an error that carries no request id, so pi-acp waits on it forever. With
pi 0.73.1 (missing `get_available_thinking_levels`) every pi task died at `session/new timed out after
120s`. pi-acp 0.0.34 needs pi ≥ 0.81, which is published as `@earendil-works/pi-coding-agent` (the
`@mariozechner` name stopped at 0.73) and needs Node ≥ 22.19. `test_codeagent_container.py` holds the
Dockerfile to that; recheck the minimum in pi-acp's README whenever either is bumped.

**pi's config for this model** (`agent/config/pi/`; each line is checked against pi 0.87.1's docs and
source, and the important ones are pinned by `test_codeagent_container.py`):

- `compat.thinkingFormat: "qwen-chat-template"` sends `chat_template_kwargs.enable_thinking` and
  `preserve_thinking`. Without it pi sends OpenAI's `reasoning_effort`, which llama-server ignores,
  so the thinking level did nothing. `preserve_thinking` is what Qwen's model card recommends for
  agents. It also keeps the prompt prefix identical turn to turn, which the prompt cache needs.
- `thinkingTokenBudgetField: "thinking_budget_tokens"` plus `thinkingBudgets` (medium = 8k) cap a
  turn's reasoning, which shares `max_tokens` with the answer. A llama.cpp build that predates
  per-request budgets ignores the field.
- `contextWindow` is the server's whole `ctx-size` (190k). `kv-unified` lets one slot address the
  whole pool. `compaction.reserveTokens: 32768` (the proxy's `max_tokens` floor) means a session tops
  out near 157k, which also leaves room for a chat arriving meanwhile.
- `defaultTools` adds pi's own `grep`/`find`/`ls`: structured, truncated output is easier on a 3B-active
  model than raw shell output.
- `httpIdleTimeoutMs: 0`: the proxy's keepalives already cover a long call. A client timeout would
  only make pi retry, and the GPU would do the work twice.

`agent/entrypoint.sh` refreshes `models.json`, `settings.json` and the extension on every start
(everything else is seeded once), so a task created before an image change still gets them.

The configs in `agent/config/` follow each project's documented format, but **only the head-to-head run
proves them**. Run it on the server:

```sh
.venv/bin/python agent/bakeoff/bakeoff.py --repo lunaschal --commits 5 --harnesses pi,omp,qwen
```

It replays recent small fixes from this repo — each agent starts from the parent commit with only the
commit's tests applied and the commit message as the task — and scores whether the tests pass, wall
time, tokens, and how often the proxy had to rescue a tool call. Set the winner in
`settings.code_agent_harness`. Open questions it should also settle: whether llama-server honours a
per-request reasoning budget; whether pinning a slot (`id_slot`) beats llama-server's own
prompt-similarity slot reuse; how werkzeug behaves serving the proxy on a Unix socket under hour-long
keepalive streams.

A one-off check, also on the server: `python -m backend.codeagent.run --repo lunaschal --harness pi --approve "…"`.

## Limits

- **No iOS builds in the container** (it is Linux). The PR's CI covers it: Actions macOS runners are free
  on public repos, and `apple.yml` already builds the app.
- `kv-unified` means the agent's context comes out of the same 190k pool as chat. The agent may use
  all of it. Compaction keeps it under ~157k, and a chat preempts the agent's request. **Not yet
  measured:** whether llama-server evicts an idle agent slot's cache when a long chat needs the room,
  and how much re-prefill the agent's next turn then pays. The `prefilled`/`cached` numbers on each
  model call are how to tell.
- The CLI (`backend.codeagent.run`) shares the GPU properly only within its own process: admission is
  in-memory, so a chat in the server process does not preempt it. The Code tab is the real path.

## One-time setup (manual)

1. **Machine account**: create a second GitHub account, invite it to the repo with write access, create
   a *classic* PAT with `public_repo` (a fine-grained token cannot act on another user's repo), and
   store it in `settings.github_agent_token`.
2. **Ruleset** on `main`: require a pull request, block force pushes, block deletion; bypass list = you.
3. **Docker** on the server (already there for the torrent stack). The worker builds the image on first
   use, or build it by hand: `docker build -t lunaschal-agent -f agent/Dockerfile .`
4. **Push notifications**: the App ID has Push enabled and the app is signed with
   `aps-environment: production`. What remains is the server's side: create an APNs auth key (team
   4AG98Q33RQ) and set `APNS_KEY_PATH` and `APNS_KEY_ID` in the server's environment (`APNS_TEAM_ID` defaults to
   `4AG98Q33RQ`, `APNS_TOPIC` to `com.lunaschal.mobile`). Until then the phone registers its token, the
   server sends nothing, and the Code tab works without push.

## Files

- Backend: `backend/codeagent/` (`mirror`, `container`, `proxy`, `toolfix`, `lookups`, `acp`, `driver`,
  `tasks`, `worker`, `github`, `run`), `backend/push/` (`apns`, `notify`), `backend/routes/code_agent.py`,
  `backend/routes/push.py`. Tables: `agent_tasks`, `agent_task_events`, `agent_approvals`,
  `push_devices`.
- Image: `agent/Dockerfile`, `agent/entrypoint.sh`, `agent/config/` (including the pi lookup extension,
  tested by `agent/tests/`), `requirements-agent.txt`.
- iOS: `LunaschalCore/CodeAgent.swift` (models, labels, the unified-diff parser, polling, push payload),
  `CodeAgentAPI.swift`; `App/CodeModel.swift`, `CodeViews.swift`, `CodeFixture.swift` (`-codeFixture`).
- Web: `src/components/Chat/CodeTasks.tsx`, `src/lib/codeTasks.ts`.
