"""Telling the user about their mail: a chat message, then a push to the phones
that switched email notifications on.

The message is committed before any push, for the reason backend/coach/deliver.py
gives: the chat is the record, and the fallback when there is no phone to reach.
"""
import logging

from backend.coach.deliver import post_message
from backend.db.connection import get_db
from backend.push import notify
from backend.push.payload import alert

logger = logging.getLogger(__name__)

PROVIDER_LABELS = {'gmail': 'Gmail', 'outlook': 'Outlook', 'imap': 'Email'}


def push(title: str, body: str, *, route: str, email_id: str | None = None,
         collapse_id: str, now: int | None = None, client=None) -> dict:
    root = {'kind': 'email', 'route': route}
    if email_id:
        root['emailId'] = email_id
    try:
        return notify.send(alert(title, body, root=root, thread_id='email'),
                           collapse_id=collapse_id, audience='email', now=now, client=client)
    except Exception as exc:
        logger.exception('Email push failed after the message was saved')
        return {'pushed': 0, 'errors': [str(exc)[:200]], 'configured': True}


def disconnected(account_id: str, now: int, client=None) -> str | None:
    """Once per outage, from a template: this must arrive with the GPU paused.
    Returns the message id, or None if this outage was already announced."""
    db = get_db()
    # The claim, so a manual sync and the scheduler cannot both announce it.
    cur = db.execute('UPDATE email_accounts SET auth_notified_at=? WHERE id=? '
                     'AND auth_error_at IS NOT NULL AND auth_notified_at IS NULL', (now, account_id))
    if not cur.rowcount:
        db.commit()
        return None
    row = db.execute('SELECT provider, email_address FROM email_accounts WHERE id=?', (account_id,)).fetchone()
    label = PROVIDER_LABELS.get(row['provider'], 'Email')
    try:
        _, message_id = post_message(
            f"{label} disconnected: {row['email_address']} needs you to sign in again. "
            'New mail is not arriving until you do; whatever came in meanwhile is fetched when you reconnect.',
            {'email': {'kind': 'disconnected', 'provider': row['provider'], 'address': row['email_address']}}, now)
    except Exception:
        # Raising would take the other accounts' syncs down with it. Give the
        # claim back, so the next failed sync of this account announces it.
        logger.exception('Announcing a disconnected email account failed')
        try:
            db.execute('UPDATE email_accounts SET auth_notified_at=NULL WHERE id=?', (account_id,))
            db.commit()
        except Exception:
            logger.exception('Could not release the announcement claim')
        return None
    push(f'{label} disconnected', 'Sign in again to keep receiving mail.', route='reconnect',
         collapse_id=f'email-auth-{account_id}', now=now, client=client)
    return message_id
