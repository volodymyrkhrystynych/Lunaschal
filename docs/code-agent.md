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
| Version pinned | 0.73.1 (+ `pi-acp` 0.0.34) | 18.12.2 (Bun 1.4.3) | 0.25.2 |
| Driven over | ACP via the `pi-acp` adapter | native ACP (`omp acp`) | native ACP (`qwen --acp`) |
| Prompt size | minimal — cheapest at 450 tok/s prefill | larger: many tools, subagents | largest (Gemini CLI fork) |
| Edits | retyped text | hash-anchored lines: fewer output tokens | retyped text |
| Permissions | none (the container is the sandbox) | over ACP | approval modes |
| Reports with this model | positive (similar 3080 Ti setup) | positive after tuning | none found |

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
- `kv-unified` means the agent's context comes out of the same 190k pool as chat, so the agent configs
  cap it at 96k.
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
   4AG98Q33RQ) and set `APNS_KEY_PATH`, `APNS_KEY_ID`, `APNS_TEAM_ID` (`4AG98Q33RQ`) and `APNS_TOPIC`
   (`com.lunaschal.mobile`) in the server's environment. Until then the phone registers its token, the
   server sends nothing, and the Code tab works without push.

## Files

- Backend: `backend/codeagent/` (`mirror`, `container`, `proxy`, `toolfix`, `acp`, `driver`, `tasks`,
  `worker`, `github`, `run`), `backend/push/` (`apns`, `notify`), `backend/routes/code_agent.py`,
  `backend/routes/push.py`. Tables: `agent_tasks`, `agent_task_events`, `agent_approvals`,
  `push_devices`.
- Image: `agent/Dockerfile`, `agent/entrypoint.sh`, `agent/config/`, `requirements-agent.txt`.
- iOS: `LunaschalCore/CodeAgent.swift` (models, labels, the unified-diff parser, polling, push payload),
  `CodeAgentAPI.swift`; `App/CodeModel.swift`, `CodeViews.swift`, `CodeFixture.swift` (`-codeFixture`).
- Web: `src/components/Chat/CodeTasks.tsx`, `src/lib/codeTasks.ts`.
