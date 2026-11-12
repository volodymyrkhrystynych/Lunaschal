# backend/codeagent/

The Code tab: an off-the-shelf coding agent (pi / omp / Qwen Code) in a sealed container, driven over
ACP, with its model calls routed through the app's model service. Design record and setup:
[docs/code-agent.md](../../docs/code-agent.md). The invariants, each of which is tested:

- **The host never runs git in `work/`.** The agent controls `.git/config` and `.git/hooks`; git runs
  what they name. Git in the clone runs in the container (`runtime.run`); the host only reads a bundle
  from `docker exec` stdout into `out.git` (`mirror.import_bundle`) and diffs/pushes from there. The one
  exception is `create_workspace` and the bake-off's `prepare`, which run before any agent has touched
  the clone. `test_codeagent_mirror.py` plants an `fsmonitor` and a hook and checks neither fires.
- **Pushes write `refs/heads/agent/<ULID>` and nothing else.** The refspec is built in `mirror.push`,
  never passed in; the token goes through `GIT_CONFIG_*` env vars. Pushing is a user action
  (`worker.push_task`), never something the driver does.
- **The container is `--network none`, read-only, capability-free, and never gets the Docker socket.**
  `container.run_args` is pure so `test_codeagent_container.py` can read the policy off the argv.
- **Every model call goes through `service.slot` at P2** (`proxy.Generator`), one request per slot —
  `ENTRY_TTL` is 30 minutes, so a slot must never be held for a session. Responses are buffered so a
  preemption replay cannot duplicate output.
- **The lookup endpoint (`/lunaschal/tools`, `lookups.py`) is a boundary, not a convenience.** The
  agent owns `$HOME`, so it can rewrite the pi extension or curl the socket directly. So only offered
  names are served, arguments and results are clipped, and each task has a budget. The web goes
  through the shared `ResearchTools` gate (library first) and through Tavily on the code agent's own
  key (`web.search_override`), at P2. Nothing in the container gains network.
- **Agent configs track the server**: pi's and omp's `contextWindow` (and Qwen Code's
  `contextWindowSize`) equal `[qwen36]`'s `ctx-size`, and all three compact at `llm.COMPACT_AT` of it,
  which must leave `proxy.MIN_MAX_TOKENS` free. `test_codeagent_container.py` holds all of it.
- **The ask and plan phases leave no changes**: write permissions refused, then `reset --hard` if
  dirty anyway. A task is either a question (`phase='ask'`, ends `answered`) or a change (starts at
  `plan`); `worker.plan_from_answer` turns an answered question into a plan in the same session, and
  `driver._from_answer` (a summary but no plan yet) is how the prompt knows it grew out of one.
- **Tests are run by the driver after the agent finishes**; `test_exit` is ours, not the agent's claim.
  So the agent is told that command up front and gets that run's result (failures first, from
  `testreport.py`, since the kept tail loses them) at the start of its next follow-up, and the
  clients hide the verdict while a task is active — see "result is measured" in the design record.
- **`tasks.set_status` is the only way a task changes state**, so the push listener cannot be bypassed.
  In-flight states (`answering`/`planning`/`running`/`testing`) become `interrupted` at startup; `queued` survives a
  restart, and the seeder must never write an in-flight state.

`toolfix.py` and `proxy.assemble`/`to_chunks` are pure; `acp.py` is tested against
`backend/tests/fixtures/fake_acp_agent.py`, and `driver.py` against that fake agent plus a local
stand-in for Docker (`test_codeagent_driver.py`). Nothing here has run against real Docker or the real
model yet — see the status line in the design record.
