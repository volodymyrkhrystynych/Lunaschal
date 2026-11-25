"""Whether an account's stored sign-in still works.

A sync can fail for many reasons and only one of them needs the user: the
provider refusing the credential itself. Google ends a test app's refresh token
seven days after it was issued, so for this app that is a weekly event rather
than an edge case. Everything else (timeouts, rate limits, a 5xx) is left to
the next poll.
"""
import time

from backend.email import alerts, gmail_client, imap_client, outlook_client

# What IMAP servers say about a refused login, as opposed to an unreachable one.
_IMAP_REFUSALS = ('authenticationfailed', 'authenticate failed', 'login failed', 'invalid credentials')


def is_auth_failure(exc: Exception) -> bool:
    if isinstance(exc, (gmail_client.GmailApiError, outlook_client.OutlookApiError)):
        return exc.reason == 'invalid_grant'
    if isinstance(exc, imap_client.ImapError):
        text = str(exc).lower()
        return any(phrase in text for phrase in _IMAP_REFUSALS)
    return False


def is_stale_access_token(exc: Exception) -> bool:
    """A 401 from the Gmail API says the hour-long access token is bad, which
    says nothing yet about the refresh token behind it."""
    return isinstance(exc, gmail_client.GmailApiError) and exc.status_code == 401


_SAME_CREDENTIAL = 'id=? AND refresh_token IS ? AND imap_password IS ?'


def _credential(account_row) -> tuple:
    return account_row['id'], account_row['refresh_token'], account_row['imap_password']


def drop_access_token(db, account_row) -> None:
    """The next sync asks for a new one, and finds out whether it can have it."""
    db.execute(f'UPDATE email_accounts SET access_token=NULL WHERE {_SAME_CREDENTIAL}',
               _credential(account_row))
    db.commit()


def record_auth_failure(db, account_row, exc: Exception) -> bool:
    """Mark the account and tell the user. False if the credential that failed
    is no longer the stored one: a sync that started before a reconnect must
    not mark the new connection dead."""
    now = int(time.time())
    cur = db.execute(
        'UPDATE email_accounts SET auth_error=?, auth_error_at=COALESCE(auth_error_at, ?), updated_at=? '
        f'WHERE sync_enabled=1 AND {_SAME_CREDENTIAL}',
        (str(exc)[:300], now, now, *_credential(account_row)))
    db.commit()
    if not cur.rowcount:
        return False
    alerts.disconnected(account_row['id'], now)
    return True


def clear_auth_failure(db, account_row) -> None:
    db.execute('UPDATE email_accounts SET auth_error=NULL, auth_error_at=NULL, auth_notified_at=NULL '
               'WHERE id=? AND auth_error_at IS NOT NULL', (account_row['id'],))
    db.commit()


def needs_reconnect(account_row) -> bool:
    return bool(account_row['sync_enabled']) and account_row['auth_error_at'] is not None
