"""A client that stalls mid-TLS-handshake must not hang the server for everyone.

Production went unreachable for hours because werkzeug's stock server
handshakes inside accept(): one silent TCP connection blocked the accept loop.
"""
import socket
import ssl
import subprocess
import threading

import pytest

from backend.serving import HandshakeInWorkerServer


def _app(environ, start_response):
    start_response('200 OK', [('Content-Type', 'text/plain')])
    return [environ['wsgi.url_scheme'].encode()]


@pytest.fixture
def cert(tmp_path):
    crt, key = tmp_path / 'c.pem', tmp_path / 'k.pem'
    subprocess.run(
        ['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
         '-keyout', str(key), '-out', str(crt), '-days', '1',
         '-subj', '/CN=localhost'],
        check=True, capture_output=True)
    return str(crt), str(key)


@pytest.fixture
def server(cert):
    srv = HandshakeInWorkerServer('127.0.0.1', 0, _app, ssl_context=cert,
                                  handshake_timeout=0.5)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _get(port: int) -> bytes:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection(('127.0.0.1', port), timeout=3) as raw:
        with ctx.wrap_socket(raw) as s:
            s.sendall(b'GET / HTTP/1.0\r\n\r\n')
            chunks = []
            while data := s.recv(4096):
                chunks.append(data)
            return b''.join(chunks)


def test_serves_https(server):
    response = _get(server.server_address[1])
    assert response.startswith(b'HTTP/1.1 200')
    # wsgi.url_scheme still reports https with the socket wrapped by us.
    assert b'https' in response.split(b'\r\n\r\n', 1)[1]


def test_stalled_handshake_does_not_block_other_clients(server):
    port = server.server_address[1]
    # Opens TCP and never sends a ClientHello.
    stalled = socket.create_connection(('127.0.0.1', port))
    try:
        assert _get(port).startswith(b'HTTP/1.1 200')
    finally:
        stalled.close()


def test_stalled_handshake_is_dropped_after_timeout(server):
    stalled = socket.create_connection(('127.0.0.1', server.server_address[1]))
    stalled.settimeout(3)
    try:
        # The server closes it after handshake_timeout: recv sees EOF.
        assert stalled.recv(1) == b''
    finally:
        stalled.close()
