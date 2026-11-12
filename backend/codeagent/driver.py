"""Run one phase of one coding task: ask, plan, or build.

A task alternates between the worker and the user. The worker runs a phase —
the agent answers a question, plans, or builds — and the task then waits on a
person: `answered`, `awaiting_plan`, `needs_input`, `done`. Each phase is its own run with its own
agent process, so a plan that sits unapproved for an evening holds neither the
worker nor a process. The ACP session resumes across runs with `session/load`
when the agent supports it; otherwise a fresh session is given what it needs
(the task and the approved plan) in its prompt. The container stays up between
phases, so the clone and anything the agent built in it are still there.

Three rules this module keeps:

- **The ask and plan phases cannot leave changes behind.** Writes are refused
  at the permission prompt, and because pi has no permission system at all,
  the working tree is reset afterwards anyway if it is dirty.
- **The result is measured, not reported.** The diff comes from the
  host-owned out.git (mirror.py), and the tests are run by this module, in the
  container, after the agent has finished — whatever the agent said about them.
- **Nothing here runs git on the host inside the agent's clone.** Git in the
  clone runs in the container (`runtime.run`); the host sees a bundle.
"""
from __future__ import annotations

import logging
import re
import subprocess
import textwrap
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from backend.codeagent import mirror, tasks, testreport
from backend.codeagent.acp import AcpClient, AcpError

logger = logging.getLogger(__name__)

# The command that speaks ACP on stdio, per agent, inside the image.
HARNESS_COMMANDS = {
    'pi': ['pi-acp'],
    'omp': ['omp', 'acp'],
    'qwen': ['qwen', '--acp'],
}

TEST_TIMEOUT = 3600
TEST_TAIL_LINES = 200
# The event carries less than the row: it is drawn in the terminal on every poll.
TEST_EVENT_CHARS = 4000
QUESTION = re.compile(r'^\s*QUESTION:\s*(.+?)\s*$', re.M)

# What runs when a repo has no agent_test_command of its own. Detection runs in
# the container, because reading the agent's files from the host is exactly
# what the host does not do.
DEFAULT_TEST_SCRIPT = (
    'if grep -q \'"test:all"\' package.json 2>/dev/null; then npm run -s test:all; '
    'elif [ -f pytest.ini ] || [ -f pyproject.toml ]; then python -m pytest -q; '
    'else echo "No test command is configured for this repository."; fi'
)

PLAN_PROMPT = """You are working in the git repository at /work, on the branch {branch}. \
Read the repository's own instructions first (CLAUDE.md or AGENTS.md at the root, if present). \
For how a library or API works, use library_search first if you have it, and web_research only \
when the library does not answer.

Task:
{prompt}

This is the planning step. Do not modify any files, do not commit, and do not run anything \
that writes to disk. Explore the code as much as you need, then reply with a concise \
implementation plan in Markdown: which files you will change and how, and which tests you \
will add or run. If the plan depends on something only the user can decide, end your reply \
with one line that starts with QUESTION: and asks it."""

ASK_PROMPT = """You are working in the git repository at /work. \
Read the repository's own instructions first (CLAUDE.md or AGENTS.md at the root, if present). \
For how a library or API works, use library_search first if you have it, and web_research only \
when the library does not answer.

The user has a question about this code:
{prompt}

This is a question, not a change. Do not modify any files, do not commit, and do not run \
anything that writes to disk. Explore the code as much as you need, then reply in Markdown: \
the answer first, then the files and functions it rests on (path and name) so the user can \
check it. Say plainly what you could not confirm."""

ASK_FOLLOWUP_PROMPT = """{text}

This is still a question, not a change. Do not modify any files. Answer in Markdown, citing \
the files and functions the answer rests on."""

PLAN_FROM_ANSWER_PROMPT = """{context}Now the user wants a change:
{request}

This is the planning step. Do not modify any files, do not commit, and do not run anything \
that writes to disk. Explore the code as much as you need, then reply with a concise \
implementation plan in Markdown: which files you will change and how, and which tests you \
will add or run. If the plan depends on something only the user can decide, end your reply \
with one line that starts with QUESTION: and asks it."""

# What "Plan this" means when the user typed nothing with it.
PLAN_FROM_ANSWER_DEFAULT = 'Make the change our conversation points to.'

# A fresh session has lost the conversation the plan grows out of.
ANSWER_CONTEXT = """You are working in the git repository at /work, on the branch {branch}. \
Earlier, the user asked:
{questions}

and the latest answer was:
{answer}

"""

REVISE_PROMPT = """The user reviewed your plan and wants changes:

{text}

Do not modify any files yet. Reply with the revised plan in full."""

BUILD_PROMPT = """{context}The user approved this plan:

{plan}

Implement it now in /work. Run the relevant tests and fix what fails. When you are done, \
commit your work with git (clear commit messages) and finish with a short summary of what \
changed and how the tests went. If you are blocked on a decision only the user can make, \
stop and end your reply with one line that starts with QUESTION: and asks it.

{check}"""

FOLLOWUP_PROMPT = """{last_check}{text}

When you are done, commit any changes and finish with a short summary. If you need a \
decision from the user, end your reply with one line that starts with QUESTION:.

{check}"""

# Said in every build turn, because the user sees Lunaschal's run of this
# command, not the agent's own: an agent that only ran the tests near its
# change reported "all tests pass" four times over a suite that was failing.
CHECK_NOTE = """When you finish, Lunaschal itself runs this in /work, and its result — not \
yours — is what the user sees as "tests passed" or "tests failed":

{script}

Run that same command before you say the tests pass. Do not change the test scripts, \
package.json or the test configuration to make it pass; if it cannot run in this \
container, say so in your summary."""

# The driver's own most recent result. Without it a follow-up like "the tests
# still fail" reaches an agent that has never seen that run and, honestly,
# cannot find it. Read from the last `test` event rather than the row: the row
# is the *current* verdict and is cleared when a turn starts, while the event
# outlives a turn that was cancelled or ended on a question.
LAST_CHECK = """The last time Lunaschal ran the test command itself (at the end of an \
earlier turn), it {verdict} (exit {exit}).{detail}

The user says:
"""

FRESH_CONTEXT = """You are working in the git repository at /work. Your earlier session \
could not be resumed; `git log` and `git status` show the work so far.

The task:
{prompt}

"""


class Runtime(Protocol):
    def start(self, task_id: str) -> None: ...
    def spawn(self, task_id: str, command: list[str]) -> subprocess.Popen: ...
    def run(self, task_id: str, command: list[str], timeout: int = 120,
            max_bytes: int | None = None) -> subprocess.CompletedProcess: ...
    def stop(self, task_id: str) -> None: ...


class DockerRuntime:
    """The production runtime: everything happens in the task's container."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root

    def start(self, task_id: str) -> None:
        from backend.codeagent import container
        if not container.is_running(task_id):
            container.start(task_id, container.ensure_image(self.repo_root))

    def spawn(self, task_id: str, command: list[str]) -> subprocess.Popen:
        from backend.codeagent import container
        return container.spawn(task_id, command)

    def run(self, task_id, command, timeout=120, max_bytes=None):
        from backend.codeagent import container
        return container.run_in(task_id, command, timeout=timeout, max_bytes=max_bytes)

    def stop(self, task_id: str) -> None:
        from backend.codeagent import container
        container.remove(task_id)


@dataclass
class _Turn:
    """What one prompt produced, assembled from session/update notifications."""
    task_id: str
    message: list[str]
    thought: list[str]
    last_text: list[str]   # message text since the last tool call: the reply
    # True while session/load replays history: that is the old conversation
    # coming back, not new events.
    muted: bool = False

    def flush(self) -> None:
        if self.thought:
            tasks.append_event(self.task_id, 'thought', {'text': ''.join(self.thought)[-8000:]})
            self.thought.clear()
        if self.message:
            tasks.append_event(self.task_id, 'message', {'text': ''.join(self.message)})
            self.message.clear()

    def on_update(self, upd: dict) -> None:
        if self.muted:
            return
        kind = upd.get('sessionUpdate')
        text = ((upd.get('content') or {}).get('text') or '') if isinstance(
            upd.get('content'), dict) else ''
        if kind == 'agent_message_chunk':
            if self.thought:
                tasks.append_event(self.task_id, 'thought',
                                   {'text': ''.join(self.thought)[-8000:]})
                self.thought.clear()
            self.message.append(text)
            self.last_text.append(text)
            return
        if kind == 'agent_thought_chunk':
            self.thought.append(text)
            return
        self.flush()
        if kind == 'tool_call':
            self.last_text.clear()
            tasks.append_event(self.task_id, 'tool_call', {
                'id': upd.get('toolCallId'), 'title': upd.get('title'),
                'kind': upd.get('kind'), 'status': upd.get('status')})
        elif kind == 'tool_call_update':
            tasks.append_event(self.task_id, 'tool_result', {
                'id': upd.get('toolCallId'), 'status': upd.get('status'),
                'title': upd.get('title')})
        elif kind == 'plan':
            tasks.append_event(self.task_id, 'plan', {'entries': upd.get('entries') or []})

    @property
    def reply(self) -> str:
        return ''.join(self.last_text).strip()


def _choose(options: list[dict], allow: bool) -> str | None:
    want = ('allow_once', 'allow_always') if allow else ('reject_once', 'reject_always')
    for kind in want:
        for opt in options:
            if opt.get('kind') == kind:
                return opt.get('optionId')
    return None if not allow else (options[0].get('optionId') if options else None)


WRITE_KINDS = frozenset({'edit', 'delete', 'move'})

# The status a task shows while each phase runs.
IN_FLIGHT = {'ask': 'answering', 'plan': 'planning', 'build': 'running'}


def _from_answer(task: dict) -> bool:
    """A plan that grows out of an answered question, not yet written. Only an
    ask leaves a summary before there is a plan — a build writes it after."""
    return bool(task.get('summary')) and not task.get('plan')


def _asked(thread: dict | None, task: dict) -> str:
    """Every question of an ask task, the first and each follow-up, in order."""
    questions = (thread or {}).get('questions') or [task['prompt']]
    return '\n\nThen:\n'.join(questions)


def _task_text(task: dict, thread: dict | None) -> str:
    """What the task is, for a fresh session. A task that grew out of a question
    keeps the question as its prompt, so the change is put first."""
    if not thread or not thread['planned']:
        return task['prompt']
    request = thread['request'] or 'The change the answer to this question pointed to.'
    return f'{request}\n\nThis grew out of a question about the code:\n{_asked(thread, task)}'


def question_in(text: str) -> str | None:
    found = QUESTION.findall(text or '')
    return found[-1] if found else None


class Driver:
    def __init__(self, runtime: Runtime, proxy_factory: Callable | None = None,
                 on_stuck: Callable[[dict], None] | None = None):
        self.runtime = runtime
        if proxy_factory is None:
            from backend.codeagent.proxy import ProxyServer
            proxy_factory = ProxyServer
        self.proxy_factory = proxy_factory
        self.on_stuck = on_stuck

    # ------------------------------------------------------------ the run

    def run(self, task_id: str, cancel: threading.Event | None = None) -> dict:
        cancel = cancel or threading.Event()
        task = tasks.get(task_id)
        phase = task['phase']
        # A new build turn makes any earlier verdict stale: whatever it ends in —
        # done, a question, a cancel, an error — the code is no longer what was
        # tested. The run itself stays readable in its `test` event.
        tasks.set_status(task_id, IN_FLIGHT[phase],
                         **({'test_exit': None, 'test_output': None} if phase == 'build' else {}))
        proxy = client = None
        try:
            task = self._ensure_workspace(task)
            self.runtime.start(task_id)
            on_event = self._proxy_event(task_id)
            proxy = self.proxy_factory(mirror.sock_dir(task_id) / 'model.sock', on_event,
                                       lookups=self._lookups(task, on_event)).start()
            client, turn = self._connect(task)
            turn.muted = True
            session_id, fresh = self._session(client, task)
            turn.muted = False
            # One read per turn: the command the agent is told to run is the
            # one it is judged by, even if the setting changes mid-turn.
            script = self._test_script(task)
            text = self._prompt_for(task, fresh, script)
            tasks.update(task_id, pending_input=None)
            stop = self._prompt(client, session_id, text, cancel)
            turn.flush()
            if cancel.is_set():
                return tasks.set_status(task_id, 'cancelled')
            if stop not in ('end_turn', None):
                tasks.append_event(task_id, 'status', {'note': f'agent stopped: {stop}'})
            if phase == 'ask':
                return self._finish_answer(task, turn.reply)
            if phase == 'plan':
                return self._finish_plan(task, turn.reply)
            return self._finish_build(task, turn.reply, script)
        except Exception as e:
            if cancel.is_set():
                return tasks.set_status(task_id, 'cancelled')
            logger.exception('code task %s failed', task_id)
            return tasks.set_status(task_id, 'error', error=_describe(e, client))
        finally:
            if client is not None:
                client.close()
            if proxy is not None:
                proxy.stop()

    def _ensure_workspace(self, task: dict) -> dict:
        if task.get('baseSha') and mirror.work_dir(task['id']).exists():
            return task
        from backend.repos import registry
        repo = registry.get_repo(task['repoId'])
        if not repo:
            raise RuntimeError('The repository this task was for is no longer registered')
        mirror_path = mirror.sync_mirror(repo['slug'], repo['remoteUrl'])
        base_branch = task.get('baseBranch') or repo.get('branch') or ''
        base_sha = mirror.create_workspace(task['id'], mirror_path, base_branch)
        base_branch = base_branch or mirror.default_branch(mirror_path)
        tasks.update(task['id'], base_sha=base_sha, base_branch=base_branch)
        return tasks.get(task['id'])

    def _connect(self, task: dict):
        cmd = HARNESS_COMMANDS[task['harness']]
        proc = self.runtime.spawn(task['id'], cmd)
        turn = _Turn(task['id'], [], [], [])
        build = task['phase'] == 'build'

        def on_permission(params: dict) -> str | None:
            call = params.get('toolCall') or {}
            allow = build or call.get('kind') not in WRITE_KINDS
            choice = _choose(params.get('options') or [], allow)
            tasks.append_event(task['id'], 'permission', {
                'title': call.get('title'), 'kind': call.get('kind'),
                'decision': 'allowed' if allow else 'refused'})
            return choice

        client = AcpClient(proc, turn.on_update, on_permission)
        client.initialize()
        return client, turn

    def _session(self, client: AcpClient, task: dict) -> tuple[str, bool]:
        if task.get('sessionId') and client.can_load_session:
            try:
                client.load_session(task['sessionId'])
                return task['sessionId'], False
            except AcpError as e:
                tasks.append_event(task['id'], 'status',
                                   {'note': f'could not resume the session: {e}'})
        session_id = client.new_session('/work')
        tasks.update(task['id'], session_id=session_id)
        return session_id, True

    def _prompt_for(self, task: dict, fresh: bool, script: str | None = None) -> str:
        pending = task.get('pendingInput')
        thread = tasks.question_thread(task['id'])
        context = FRESH_CONTEXT.format(prompt=_task_text(task, thread)) if fresh else ''
        if task['phase'] == 'ask':
            if pending and not fresh:
                return ASK_FOLLOWUP_PROMPT.format(text=pending)
            base = ASK_PROMPT.format(prompt=task['prompt'])
            if not pending:
                return base
            # A fresh session has lost the conversation, so it gets the
            # follow-ups between and the answer it is being asked about.
            earlier = [q for q in (thread or {}).get('questions', [])[1:] if q != pending]
            between = ''.join(f'\n\nThe user then asked:\n{q}' for q in earlier)
            answer = task.get('summary') or '(the answer was not recorded)'
            return (base + between + f'\n\nYou answered that already:\n{answer}'
                    f'\n\nThe user then asked:\n{pending}')
        if task['phase'] == 'plan' and _from_answer(task):
            # pending is empty after an interrupted run; the thread keeps the request.
            request = pending or (thread or {}).get('request') or PLAN_FROM_ANSWER_DEFAULT
            context = ANSWER_CONTEXT.format(
                branch=mirror.branch_for(task['id']),
                questions=_asked(thread, task), answer=task['summary']) if fresh else ''
            return PLAN_FROM_ANSWER_PROMPT.format(context=context, request=request)
        if task['phase'] == 'plan':
            if pending and not fresh:
                return REVISE_PROMPT.format(text=pending)
            base = PLAN_PROMPT.format(branch=mirror.branch_for(task['id']),
                                      prompt=_task_text(task, thread))
            return base + (f'\n\nThe user has reviewed an earlier plan and said:\n{pending}'
                           if pending else '')
        script = script if script is not None else self._test_script(task)
        check = CHECK_NOTE.format(script=textwrap.indent(script, '    '))
        if pending:
            followup = FOLLOWUP_PROMPT.format(text=pending, check=check,
                                              last_check=_last_check(task))
            return (context + followup) if fresh else followup
        return BUILD_PROMPT.format(context=context, plan=task.get('plan') or '(no plan recorded)',
                                   check=check)

    def _prompt(self, client: AcpClient, session_id: str, text: str,
                cancel: threading.Event) -> str:
        done = threading.Event()

        def watch():
            while not done.wait(0.5):
                if cancel.is_set():
                    client.cancel(session_id)
                    if not done.wait(30):
                        client.close()
                    return
        threading.Thread(target=watch, daemon=True, name='codeagent-cancel').start()
        try:
            return client.prompt(session_id, text)
        finally:
            done.set()

    def _lookups(self, task: dict, on_event):
        """The task's library/wiki/web lookups, or None — a lookup toolbox that
        cannot be built costs the agent its lookups, never the task."""
        try:
            from backend.codeagent.lookups import Lookups
            return Lookups(task.get('repoId'), on_event)
        except Exception:
            logger.exception('code task %s: lookups unavailable', task['id'])
            return None

    def _proxy_event(self, task_id: str):
        def on_event(kind: str, payload: dict) -> None:
            tasks.append_event(task_id, kind, payload)
            if payload.get('action') == 'stuck' and self.on_stuck:
                try:
                    self.on_stuck(tasks.get(task_id))
                except Exception:
                    logger.exception('stuck notification failed')
        return on_event

    # -------------------------------------------------------- finishing

    def _dirty(self, task_id: str) -> bool:
        proc = self.runtime.run(task_id, ['git', '-C', '/work', 'status', '--porcelain'])
        return bool((proc.stdout or b'').strip())

    def _revert(self, task_id: str, step: str) -> None:
        """Undo whatever a read-only step wrote. pi has no permission prompt to
        refuse; this is what holds the line."""
        if self._dirty(task_id):
            self.runtime.run(task_id, ['git', '-C', '/work', 'reset', '--hard', '--quiet'])
            self.runtime.run(task_id, ['git', '-C', '/work', 'clean', '-fdq'])
            tasks.append_event(task_id, 'status',
                               {'note': f'the {step} step changed files; they were reverted'})

    def _finish_answer(self, task: dict, reply: str) -> dict:
        self._revert(task['id'], 'question')
        return tasks.set_status(task['id'], 'answered',
                                summary=reply or '(the agent did not write an answer)')

    def _finish_plan(self, task: dict, reply: str) -> dict:
        task_id = task['id']
        self._revert(task_id, 'plan')
        plan = reply or '(the agent did not write a plan)'
        tasks.open_approval(task_id, 'plan', {'plan': plan, 'question': question_in(plan)})
        return tasks.set_status(task_id, 'awaiting_plan', plan=plan)

    def _export(self, task: dict) -> dict | None:
        """Commit anything left uncommitted, bundle it out, import it, diff it."""
        task_id = task['id']
        if self._dirty(task_id):
            self.runtime.run(task_id, ['git', '-C', '/work', 'add', '-A'])
            self.runtime.run(task_id, ['git', '-C', '/work', 'commit', '-q', '-m',
                                       f"{task['title']}\n\nUncommitted work, committed by Lunaschal."])
        proc = self.runtime.run(task_id, mirror.bundle_command(task['baseSha']),
                                max_bytes=mirror.MAX_BUNDLE_BYTES)
        if proc.returncode != 0:
            err = (proc.stderr or b'').decode(errors='replace')
            if 'empty bundle' in err.lower():
                return None
            raise RuntimeError(f'could not export the work: {err.strip()[-300:]}')
        bundle = mirror.task_dir(task_id) / 'export.bundle'
        bundle.write_bytes(proc.stdout)
        head = mirror.import_bundle(task_id, bundle)
        result = mirror.diff(task_id, task['baseSha'])
        result['head'] = head
        return result

    def _finish_build(self, task: dict, reply: str, script: str | None = None) -> dict:
        task_id = task['id']
        result = self._export(task)
        fields = {'summary': reply or None}
        if result:
            fields.update(head_sha=result['head'], diff_stat=result['files'])
        question = question_in(reply)
        if question:
            tasks.open_approval(task_id, 'question', {'question': question, 'reply': reply})
            # No test run this turn; run() already cleared the old verdict.
            return tasks.set_status(task_id, 'needs_input', **fields)
        tasks.set_status(task_id, 'testing', **fields)
        exit_code, output = self._run_tests(task, script)
        tasks.append_event(task_id, 'test', {
            'exit': exit_code, 'tail': testreport.clip_report(output, TEST_EVENT_CHARS)})
        return tasks.set_status(task_id, 'done', test_exit=exit_code, test_output=output)

    def _test_script(self, task: dict) -> str:
        from backend.db.connection import get_db
        row = get_db().execute('SELECT agent_test_command FROM repos WHERE id=?',
                               (task['repoId'],)).fetchone()
        return (row['agent_test_command'] if row else '') or DEFAULT_TEST_SCRIPT

    def _run_tests(self, task: dict, script: str | None = None) -> tuple[int, str]:
        """Run the repo's test command; return its exit and a report of it —
        what failed first, then the end of the output."""
        script = script if script is not None else self._test_script(task)
        # Merged in the shell so each line stays in the batch that wrote it:
        # captured apart, all of stderr landed under whichever batch came last.
        proc = self.runtime.run(task['id'], ['sh', '-c', f'{{ {script}\n}} 2>&1'],
                                timeout=TEST_TIMEOUT)
        text = (proc.stdout or b'').decode(errors='replace') + \
            (proc.stderr or b'').decode(errors='replace')
        return proc.returncode, testreport.report(text, TEST_TAIL_LINES)


def _last_check(task: dict) -> str:
    """LAST_CHECK from the task's most recent test run, or '' if it has none."""
    event = tasks.last_event(task['id'], 'test')
    if event is None or event['payload'].get('exit') is None:
        return ''
    exit_code = event['payload']['exit']
    if exit_code == 0:
        return LAST_CHECK.format(verdict='passed', exit=0, detail='')
    output = event['payload'].get('tail') or ''
    detail = f'\n\n{output}' if output.strip() else ''
    return LAST_CHECK.format(verdict='FAILED', exit=exit_code, detail=detail)


def _describe(e: Exception, client: AcpClient | None) -> str:
    msg = str(e) or type(e).__name__
    if client is not None and client.stderr_tail and 'exited' not in msg:
        msg += ' — ' + ' | '.join(list(client.stderr_tail)[-2:])
    return msg[:500]
