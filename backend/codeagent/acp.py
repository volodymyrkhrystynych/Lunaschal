"""A minimal Agent Client Protocol client: JSON-RPC 2.0, one message per line,
over the agent process's stdin/stdout.

Lunaschal plays the part an editor plays for Zed's agents. It advertises *no*
file-system or terminal capability, so the agent uses its own tools inside the
container rather than asking the host to read or write anything — the host
side of this connection never touches a path.

What the agent sends us:

- `session/update` notifications — message and thought chunks, tool calls and
  their updates, plans. Handed to `on_update` as they arrive.
- `session/request_permission` requests — answered by `on_permission`, which
  returns an optionId (or None to cancel). Run on its own thread, so a slow
  decision never stalls the reader.
- anything else it asks for is answered "method not found", which is the
  correct reply for a capability we did not advertise.
"""
from __future__ import annotations

import collections
import json
import logging
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1


class AcpError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class AcpClosed(AcpError):
    pass


class AcpClient:
    def __init__(self, proc, on_update: Callable[[dict], None],
                 on_permission: Callable[[dict], str | None]):
        self.proc = proc
        self.on_update = on_update
        self.on_permission = on_permission
        self._next_id = 1
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: dict[int, dict] = {}
        self._closed = threading.Event()
        self.stderr_tail: collections.deque = collections.deque(maxlen=50)
        self.capabilities: dict = {}
        threading.Thread(target=self._read, daemon=True, name='acp-reader').start()
        if getattr(proc, 'stderr', None) is not None:
            threading.Thread(target=self._drain_stderr, daemon=True, name='acp-stderr').start()

    # ------------------------------------------------------------ transport

    def _send(self, msg: dict) -> None:
        data = (json.dumps(msg) + '\n').encode()
        with self._write_lock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (BrokenPipeError, ValueError, OSError) as e:
                raise AcpClosed(f'the agent process is gone: {e}') from e

    def _read(self) -> None:
        try:
            for raw in self.proc.stdout:
                line = raw.decode(errors='replace').strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    # Agents print banners; only JSON lines are protocol.
                    logger.debug('acp: non-JSON line from agent: %s', line[:200])
                    continue
                self._dispatch(msg)
        finally:
            self._closed.set()
            with self._lock:
                for waiter in self._pending.values():
                    waiter['event'].set()

    def _drain_stderr(self) -> None:
        for raw in self.proc.stderr:
            self.stderr_tail.append(raw.decode(errors='replace').rstrip())

    def _dispatch(self, msg: dict) -> None:
        if 'id' in msg and ('result' in msg or 'error' in msg) and 'method' not in msg:
            with self._lock:
                waiter = self._pending.get(msg['id'])
            if waiter:
                waiter['msg'] = msg
                waiter['event'].set()
            return
        method = msg.get('method')
        if method == 'session/update':
            try:
                self.on_update((msg.get('params') or {}).get('update') or {})
            except Exception:
                logger.exception('acp: update handler failed')
            return
        if 'id' not in msg:
            return  # an unknown notification
        if method == 'session/request_permission':
            threading.Thread(target=self._answer_permission, args=(msg,), daemon=True).start()
            return
        self._send({'jsonrpc': '2.0', 'id': msg['id'],
                    'error': {'code': -32601, 'message': f'{method} is not supported'}})

    def _answer_permission(self, msg: dict) -> None:
        try:
            option = self.on_permission(msg.get('params') or {})
        except Exception:
            logger.exception('acp: permission handler failed')
            option = None
        outcome = ({'outcome': 'selected', 'optionId': option} if option
                   else {'outcome': 'cancelled'})
        try:
            self._send({'jsonrpc': '2.0', 'id': msg['id'], 'result': {'outcome': outcome}})
        except AcpClosed:
            pass

    # ---------------------------------------------------------------- calls

    def request(self, method: str, params: dict, timeout: float | None = None) -> Any:
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            waiter = {'event': threading.Event(), 'msg': None}
            self._pending[req_id] = waiter
        try:
            if self._closed.is_set():
                raise AcpClosed(self._closed_message())
            self._send({'jsonrpc': '2.0', 'id': req_id, 'method': method, 'params': params})
            if not waiter['event'].wait(timeout):
                raise AcpError(f'{method} timed out after {timeout:.0f}s')
            msg = waiter['msg']
            if msg is None:
                raise AcpClosed(self._closed_message())
            if 'error' in msg:
                err = msg['error'] or {}
                raise AcpError(err.get('message') or 'agent error', err.get('code'))
            return msg.get('result')
        finally:
            with self._lock:
                self._pending.pop(req_id, None)

    def notify(self, method: str, params: dict) -> None:
        self._send({'jsonrpc': '2.0', 'method': method, 'params': params})

    def _closed_message(self) -> str:
        tail = ' | '.join(list(self.stderr_tail)[-3:])
        return f'the agent exited{": " + tail if tail else ""}'

    # ------------------------------------------------------------ protocol

    def initialize(self) -> dict:
        result = self.request('initialize', {
            'protocolVersion': PROTOCOL_VERSION,
            'clientCapabilities': {
                'fs': {'readTextFile': False, 'writeTextFile': False},
                'terminal': False,
            },
        }, timeout=120) or {}
        self.capabilities = result.get('agentCapabilities') or {}
        return result

    @property
    def can_load_session(self) -> bool:
        return bool(self.capabilities.get('loadSession'))

    def new_session(self, cwd: str = '/work') -> str:
        result = self.request('session/new', {'cwd': cwd, 'mcpServers': []}, timeout=120) or {}
        return result['sessionId']

    def load_session(self, session_id: str, cwd: str = '/work') -> None:
        self.request('session/load', {'sessionId': session_id, 'cwd': cwd, 'mcpServers': []},
                     timeout=600)

    def prompt(self, session_id: str, text: str, timeout: float | None = None) -> str:
        """Run one turn to completion. Returns the stop reason."""
        result = self.request('session/prompt', {
            'sessionId': session_id, 'prompt': [{'type': 'text', 'text': text}],
        }, timeout=timeout) or {}
        return result.get('stopReason') or 'end_turn'

    def cancel(self, session_id: str) -> None:
        try:
            self.notify('session/cancel', {'sessionId': session_id})
        except AcpClosed:
            pass

    def close(self) -> None:
        for stream in (self.proc.stdin,):
            try:
                stream.close()
            except Exception:
                pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
