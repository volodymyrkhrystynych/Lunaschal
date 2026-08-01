# Email view — design plan

Status: planning. Complements the higher-level "Email ingestion" section in [ROADMAP.md](./ROADMAP.md).

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

| Provider | Protocol                            | Notes                                                                                |
| -------- | ----------------------------------- | ------------------------------------------------------------------------------------ |
| Gmail    | IMAP + OAuth 2.0                    | Use the Gmail IMAP endpoint; tokens stored locally in SQLite, refresh automatically. |
| Outlook  | Microsoft Graph or IMAP + OAuth 2.0 | Graph is richer, IMAP is simpler to start with.                                      |
| Generic  | IMAP/SMTP                           | Password or app-token; lowest-common-denominator provider support.                   |

## Data model (proposed)

- `email_accounts` — provider, tokens, last sync cursor.
- `email_threads` — subject, participants, `last_message_at`, `unread_count`, `account_id`.
- `email_messages` — `thread_id`, `account_id`, `message_id`, headers, body text/HTML, `received_at`, `is_read`, `is_starred`, `raw_path`.
- `email_labels` — account labels, mapping to local categories.
- `email_message_labels` — many-to-many.
- `email_actions` — AI-extracted actions: `todo`, `event`, `job_application`, `rejection`, `next_step`.

## Sync model

- First run: fetch the last N days (configurable, default 90), save messages locally, build threads.
- Incremental: use IMAP `UIDVALIDITY` / `UIDNEXT` or provider-specific sync tokens.
- Store the full raw message on disk (`./data/emails/<account>/<uid>.eml`) and the indexed text/blobs in SQLite.
- Deletions on the server are marked `is_deleted` locally rather than hard-deleted.

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

1. Add `email_*` tables to `backend/db/schema.sql` and migration helpers.
2. Implement a minimal `backend/routes/email.py` for a single IMAP account.
3. Add the React view at `src/components/Email/`.
4. Add the job-search AI prompt and schema in `backend/ai/email.py`.
