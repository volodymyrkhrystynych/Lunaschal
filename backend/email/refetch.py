"""Re-fetch message bodies that were stored before `emails.body_html` existed.

`body_html` was added to the schema after this mailbox had already been synced,
and the column defaults to empty — so every message synced before that carries a
plain-text body and nothing else. For most mail that is a cosmetic loss. For
some of it the HTML part is the only place the content ever was: Indeed's
application confirmations send a plain-text part reading, in full, "Your
application has been submitted. Good luck!", while the employer's name appears
only in the HTML. A thousand of those are unattributable from local data alone,
and no better parser can recover what was never stored.

The provider still has the originals, so this walks the rows with an empty
`body_html` and fills them in from `provider_message_id`.

Three properties, all of which exist because this runs over thousands of
messages against someone's real mail account:

**It resumes.** Progress is the data itself — a row with a non-empty body_html
is done — so there is no cursor to corrupt and an interrupted run simply
continues. Nothing is re-fetched twice.

**It updates, never inserts.** `_store_parsed_message` is the sync path and
takes ON CONFLICT DO NOTHING, which would silently do nothing here since the row
already exists. This writes the two body columns of a known id and touches no
other column: `category` and `job_status` are the classifier's output over the
text, `received_at` orders the mailbox, and re-deriving any of them from a
re-fetch would be a second opinion nobody asked for.

**It paces itself.** Gmail allows 250 quota units per second per user and
`messages.get` costs 5, so the ceiling is 50 messages a second. The default here
is far below that. The limit worth respecting is not the quota — a thousand
messages is 5,000 units against a billion-a-day budget — but that this is a
background job on someone's live account, and there is no reason for it to be
the loudest thing talking to it.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import requests

from backend.db.connection import get_db
from backend.email import gmail_client, images
from backend.email.sanitize import sanitize_email_html

logger = logging.getLogger(__name__)

# Gmail allows 250 quota units per second per user and `messages.get` costs 5,
# so 50 messages a second is the ceiling, not a guideline — asking for more
# does not go faster, it returns 429s. The default sits just under it.
#
# Rate is a request, not a promise: `_Throttle` paces to whatever is asked for,
# and `_with_retry` backs off when the provider disagrees. A caller that asks
# for 200/s therefore gets 50/s and a run that finishes, rather than a run that
# fails a fifth of the way through.
GMAIL_MAX_PER_SECOND = 50.0
DEFAULT_RATE_PER_SECOND = 40.0

# How many times one message is retried before it counts as failed. Rate limits
# are transient by definition, so the backoff is worth more than the attempt.
MAX_ATTEMPTS = 5

# A run has to end somewhere. The caller passes what it wants; this is the
# ceiling that stops an unbounded sweep from walking 39,000 messages because a
# filter was wrong.
DEFAULT_LIMIT = 2000


class _Throttle:
    """Paces calls to a target rate, measured from the last call rather than by
    sleeping a fixed amount.

    A flat `sleep(1/rate)` per iteration adds the request's own latency on top,
    so a nominal 40/s becomes half that against a real network. Sleeping only
    for the remainder of the interval keeps the actual rate near the target.
    """

    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second if per_second > 0 else 0.0
        self._next = 0.0

    def wait(self) -> None:
        if not self.interval:
            return
        now = time.monotonic()
        if self._next and now < self._next:
            time.sleep(self._next - now)
        self._next = max(now, self._next) + self.interval


def _retry_after_seconds(error, attempt: int) -> float:
    """Exponential backoff, unless the provider named a delay itself."""
    named = getattr(error, 'retry_after', None)
    if named:
        try:
            return max(0.0, float(named))
        except (TypeError, ValueError):
            pass
    return min(30.0, 0.5 * (2 ** attempt))


def _with_retry(call, *, attempts: int = MAX_ATTEMPTS):
    """Run `call`, retrying the failures that mean "later", not "no".

    429 is a rate limit and 5xx is the provider having a bad moment. A read
    timeout or a dropped connection is the same category and was the one this
    missed at first: a live run of 25 messages produced two read timeouts, and
    over four thousand they accumulate into a run that looks half-broken.
    Because GmailApiError subclasses requests.HTTPError, catching HTTPError's
    siblings needs saying explicitly.

    Everything else — 404 above all — is an answer, and retrying it is just a
    slower way to get the same one.
    """
    last = None
    for attempt in range(attempts):
        try:
            return call()
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
            time.sleep(_retry_after_seconds(e, attempt))
        except gmail_client.GmailApiError as e:
            status = getattr(e, 'status_code', None)
            if status != 429 and not (status and 500 <= status < 600):
                raise
            last = e
            time.sleep(_retry_after_seconds(e, attempt))
    raise last


@dataclass
class RefetchProgress:
    """In-memory progress for one run, in the shape the other long jobs use."""
    total: int = 0
    done: int = 0
    filled: int = 0
    gone: int = 0
    failed: int = 0
    error: str = ''
    finished: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            'total': self.total, 'done': self.done, 'filled': self.filled,
            'gone': self.gone, 'failed': self.failed, 'error': self.error,
            'finished': self.finished, 'errors': self.errors[:5],
        }


def candidates(db, *, category: str | None = None, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Rows whose HTML body was never stored, newest first.

    Newest first because the value of a re-fetch decays: a confirmation from
    last month is likelier to still matter than one from two years ago, and a
    run that is cut short should have spent its budget on the recent end.
    """
    sql = (
        "SELECT e.id, e.provider_message_id, e.account_id"
        " FROM emails e"
        " WHERE (e.body_html IS NULL OR e.body_html = '')"
    )
    params: list = []
    if category:
        sql += ' AND e.category = ?'
        params.append(category)
    sql += ' ORDER BY e.received_at DESC LIMIT ?'
    params.append(limit)
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def count_missing(db, *, category: str | None = None) -> int:
    sql = "SELECT COUNT(*) c FROM emails WHERE (body_html IS NULL OR body_html = '')"
    params: list = []
    if category:
        sql += ' AND category = ?'
        params.append(category)
    return db.execute(sql, params).fetchone()['c']


def _gmail_account(db, account_id: str):
    return db.execute(
        "SELECT * FROM email_accounts WHERE id=? AND provider='gmail'", (account_id,)
    ).fetchone()


def _oauth_settings(db) -> dict | None:
    row = db.execute(
        'SELECT google_oauth_client_id, google_oauth_client_secret FROM settings LIMIT 1'
    ).fetchone()
    if not row or not row['google_oauth_client_id']:
        return None
    return dict(row)


def fill_one(db, row: dict, access_token: str) -> str:
    """Re-fetch one message and write its bodies back.

    Returns 'filled', 'gone' or 'empty'. A 404 is 'gone' and is not an error:
    over a mailbox this size some messages have been deleted or auto-purged
    since they were synced, exactly as `_insert_message` already assumes.
    """
    try:
        raw = _with_retry(
            lambda: gmail_client.get_message(access_token, row['provider_message_id'])
        )
    except gmail_client.GmailApiError as e:
        if e.status_code == 404:
            return 'gone'
        raise

    parsed = gmail_client.parse_message(raw)
    body_html, image_refs = sanitize_email_html(parsed.get('bodyHtml') or '')
    if not body_html:
        # The message genuinely has no HTML part. Recording that would be
        # indistinguishable from "not fetched yet", so it stays as it is and
        # the row is simply counted — the alternative is re-fetching it on
        # every future run forever.
        return 'empty'

    # Only the HTML is written unconditionally. body_text is filled in solely
    # when it is empty, because the stored one is what the classifier already
    # read — replacing it would silently invalidate a `category` and
    # `job_status` that were derived from different words.
    db.execute(
        "UPDATE emails SET body_html=?,"
        " body_text=CASE WHEN body_text IS NULL OR body_text='' THEN ? ELSE body_text END"
        ' WHERE id=?',
        (body_html, parsed.get('bodyText') or '', row['id']),
    )
    if image_refs:
        images.queue_images(db, image_refs)
    db.commit()
    return 'filled'


def run(
    *,
    category: str | None = None,
    limit: int = DEFAULT_LIMIT,
    rate_per_second: float = DEFAULT_RATE_PER_SECOND,
    progress: RefetchProgress | None = None,
) -> dict:
    """Walk the missing-body rows and fill what the provider still has.

    Only Gmail accounts are handled. IMAP messages were stored from the full
    RFC822 source in the first place, so their HTML is already whatever the
    message contained — there is nothing to go back for.
    """
    db = get_db()
    progress = progress or RefetchProgress()

    settings = _oauth_settings(db)
    if not settings:
        progress.error = 'Google OAuth is not configured.'
        progress.finished = True
        return progress.as_dict()

    rows = candidates(db, category=category, limit=limit)
    progress.total = len(rows)
    throttle = _Throttle(min(rate_per_second, GMAIL_MAX_PER_SECOND))

    tokens: dict[str, str] = {}
    for row in rows:
        account_id = row['account_id']
        if account_id not in tokens:
            account = _gmail_account(db, account_id)
            if account is None:
                # An IMAP or Outlook account: nothing to re-fetch from here.
                progress.done += 1
                continue
            tokens[account_id] = gmail_client.get_valid_access_token(
                db, dict(account),
                settings['google_oauth_client_id'],
                settings['google_oauth_client_secret'],
            )

        throttle.wait()
        try:
            outcome = fill_one(db, row, tokens[account_id])
        except Exception as e:  # noqa: BLE001 — one bad message must not end the run
            progress.failed += 1
            message = f'{row["provider_message_id"]}: {e}'
            progress.errors.append(message)
            logger.warning('refetch failed for %s', message)
        else:
            if outcome == 'filled':
                progress.filled += 1
            elif outcome == 'gone':
                progress.gone += 1

        progress.done += 1

    progress.finished = True
    return progress.as_dict()
