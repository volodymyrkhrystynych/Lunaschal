"""The APNs alert payload. Pure.

Apple refuses a payload over 4 KB, so the body is cut to fit rather than the
send failing — a reminder with a shortened sentence is better than none.
`thread-id` groups a sender's notifications into one stack on the lock screen,
and `interruption-level: active` is the ordinary level, which a Focus
mode is free to hold back; nothing here is ever time-sensitive.
"""
import json

MAX_BYTES = 4096


def alert(title: str, body: str, *, category: str = '', data: dict | None = None, root: dict | None = None,
          thread_id: str = 'coach', relevance: float = 0.5) -> dict:
    """`data` is the coach's, under its own key. `root` goes beside `aps`
    instead, which is where the Code tab's taskId is read from."""
    aps = {
        'alert': {'title': title, 'body': body},
        'sound': 'default',
        'interruption-level': 'active',
        'relevance-score': max(0.0, min(relevance, 1.0)),
    }
    if category:
        aps['category'] = category
    if thread_id:
        aps['thread-id'] = thread_id
    payload = {'aps': aps, **root} if root is not None else {'aps': aps, 'coach': data or {}}
    body = body.encode()
    while len(encode(payload)) > MAX_BYTES and body:
        # Cut by bytes (the limit is bytes), a little past the overflow since
        # escaping makes the encoded body longer than the raw one.
        body = body[:max(0, len(body) - (len(encode(payload)) - MAX_BYTES) - 16)]
        payload['aps']['alert']['body'] = body.decode(errors='ignore') + '…'
    return payload


def encode(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
