"""The HTTP server production runs under, with TLS handshakes off the accept loop.

Werkzeug wraps the *listening* socket in TLS, so `accept()` performs the
handshake — on the one thread that accepts connections, with no timeout. A
client that opens a TCP connection and then never finishes the handshake (a
phone or iPad Safari tab suspended mid-connect) leaves that thread blocked in
`recv` forever: the process stays alive and `active` under systemd, every later
connection queues in the kernel backlog until it fills, and the server is gone
for everyone until someone restarts it. That is what took production down on
2026-10-01.

So the listening socket is wrapped with `do_handshake_on_connect=False`, which
makes `accept()` return as soon as TCP is up, and the handshake runs in the
connection's own worker thread under `HANDSHAKE_TIMEOUT`. A client that stalls
now costs one short-lived thread instead of the whole server.
"""
from __future__ import annotations

import ssl

from werkzeug.serving import ThreadedWSGIServer, load_ssl_context

# Long enough for a phone on a poor link; short enough that a stalled client
# releases its thread quickly. Applies to the handshake only — after it the
# socket goes back to blocking, since SSE streams legitimately idle for minutes.
HANDSHAKE_TIMEOUT = 15.0


class HandshakeInWorkerServer(ThreadedWSGIServer):
    def __init__(self, host, port, app, ssl_context=None,
                 handshake_timeout: float = HANDSHAKE_TIMEOUT, **kwargs):
        # Let werkzeug bind a plain socket, then wrap it ourselves: its own
        # wrap is the one that handshakes inside accept().
        super().__init__(host, port, app, ssl_context=None, **kwargs)
        self.handshake_timeout = handshake_timeout
        if ssl_context is not None:
            if isinstance(ssl_context, tuple):
                ssl_context = load_ssl_context(*ssl_context)
            self.socket = ssl_context.wrap_socket(
                self.socket, server_side=True, do_handshake_on_connect=False)
            # werkzeug reads this for wsgi.url_scheme and SSL error handling.
            self.ssl_context = ssl_context

    def process_request_thread(self, request, client_address):
        # Runs in the per-connection thread, so a slow handshake blocks only it.
        if self.ssl_context is not None:
            try:
                request.settimeout(self.handshake_timeout)
                request.do_handshake()
                request.settimeout(None)
            except OSError:  # covers ssl.SSLError and socket timeouts
                self.shutdown_request(request)
                return
        super().process_request_thread(request, client_address)


def serve(app, host: str, port: int, ssl_context=None) -> None:
    server = HandshakeInWorkerServer(host, port, app, ssl_context=ssl_context)
    server.log_startup()
    server.serve_forever()
