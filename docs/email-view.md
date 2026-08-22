# Email view — design plan

Status: Gmail, Outlook, and generic IMAP connectors are implemented (`backend/email/gmail_client.py`, `backend/email/outlook_client.py` + `backend/email/imap_client.py`, dispatched by `backend/email/sync.py`). Complements the higher-level "Email ingestion" section in [ROADMAP.md](./ROADMAP.md).

## Goal

A dedicated email view inside Lunaschal that pulls messages out of cloud providers, stores them locally, and uses the existing local AI layer to sort, summarize, and surface actionable items.

## Scope now

- Fetch and store email from **Gmail** and **Outlook** via OAuth, plus a generic **IMAP** fallback.
- Display a thread list with sorting, filtering, and search.
- AI-assisted job-search tracking:
  - Count **job applications sent**, **rejections**, and **interview/next-step requests**.
  - Flag messages that look like **next steps** (interview invites, take-home tests, recruiter calls).
  - Suggest which emails should become **todos** or **calendar events**.
- Offline access after the initial fetch.

## Scope later

- Ad / newsletter filtering tuned to what is actually interesting.
- Full spam / promotion auto-archive.
- Two-way sync and sending.

## Providers and auth

| Provider | Protocol              | Notes                                                                                                                                                                                                |
| -------- | --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Gmail    | Gmail API + OAuth 2.0 | `backend/email/gmail_client.py`. History API for incremental sync; tokens stored locally in SQLite, refresh automatically.                                                                           |
| Outlook  | IMAP + OAuth 2.0      | `backend/email/outlook_client.py` (Microsoft identity platform token dance) + `backend/email/imap_client.py` — chosen over Graph so it shares the IMAP sync engine with the generic connector below. |
| Generic  | IMAP                  | `backend/email/imap_client.py`. Password or app-token, any host/port — Fastmail, Yahoo, a custom domain.                                                                                             |

One connected account per provider slot (max 3 total) — reconnecting a provider with a different address is rejected until the existing one is disconnected.

## Data model (as implemented)

Simpler than originally proposed below — no separate threads/labels tables; `backend/db/schema.sql`:

- `email_accounts` — `provider` (`gmail`/`outlook`/`imap`), OAuth tokens (gmail/outlook) or `imap_host`/`imap_port`/`imap_username`/`imap_password` (imap), sync cursor (`history_id` for gmail, `uid_validity`/`uid_next` for outlook/imap).
- `emails` — flat per-message row: `account_id`, `provider_message_id` (the provider's own native id), subject/sender/body text+HTML, `received_at`, AI `category`/`job_status`.
- `email_images` — content-addressed cache for HTML images, keyed by a hash of the original URL (never fetched by the browser directly — see `backend/email/sanitize.py`).

## Sync model (as implemented)

- No day-bounded window for any provider: first connect is a complete mailbox mirror (Gmail: full `messages.list`; Outlook/IMAP: `UID SEARCH ALL`), since already-synced messages are skipped cheaply on any re-list.
- Incremental: Gmail's History API (`history_id`); Outlook/IMAP's `UID SEARCH <uid_next>:*` against `uid_validity`/`uid_next`. A cursor going stale (Gmail history expiring, or IMAP `UIDVALIDITY` changing) falls back to a full re-list automatically.
- No raw `.eml` files — sanitized HTML/text and extracted metadata live directly in the `emails` row; images referenced by HTML are fetched separately into `email_images`.
- No `is_deleted`/soft-delete tracking yet — deletions on the server aren't currently reflected locally.

## View and sorting

- **Inbox**: thread list, newest first, grouped by day.
- **Filters**: unread, starred, has attachments, has AI action, job-search related, needs reply.
- **Search**: FTS over subject, sender, and body text; optional semantic search later.
- **Thread detail**: message list, sender cards, quoted text collapsed, inline attachments.

## AI job-search features

Run after a message is saved (same background pattern as `backend/ai/journal.py`):

1. **Classify** each message into one or more:
   - `application_acknowledgement`
   - `rejection`
   - `interview_request`
   - `offer`
   - `recruter_outreach`
   - `general`
2. **Extract structured data**:
   - Company, role, date, next action, deadline.
3. **Counters**:
   - Total applications in the last N days.
   - Rejections.
   - Active next steps.
4. **Action suggestions**:
   - Create a todo from a request.
   - Create a calendar event from an interview invite.

Use `backend/ai/chat_json` with a JSON schema so the local LLM returns constrained fields.

## Ad filtering (later)

Hold this for a second phase. The first useful version is "show me job stuff and hide everything else into an archive by default". Later, train or prompt a classifier that learns which newsletters and promotions are interesting.

## Security and privacy

- Tokens live in the local SQLite DB and are never sent anywhere except the email provider.
- Raw `.eml` files stay in `./data/emails/`.
- Network mode must not expose the email view until auth is fully locked down.

## Open questions

- Do we keep HTML bodies or render only a sanitized text view?
- Should job-search counters be per-week, per-month, or user-configurable?
- How aggressive should the initial sync window be for large inboxes?

## Next steps

Done: `email_*` tables and migrations, `backend/routes/email.py` (multi-provider), the React view at `src/components/Email/`, and the job-search AI prompt/schema in `backend/ai/email.py`. Remaining, per "Scope later" above: ad/newsletter filtering, spam auto-archive, two-way sync and sending.
