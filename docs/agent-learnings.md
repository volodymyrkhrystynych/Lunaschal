# Agent learnings → Learning cards

A way to keep learning from the bugs and design mistakes you work through with
a coding agent, without the agent's help turning into the only place that
knowledge lives.

The agent keeps a **daily learnings file**: one Markdown file per day, one
section per lesson, written in generic terms. The Learning tab's **Import**
view turns those files into cards:

1. **Get the files in.** Either upload/paste them in Learning → Import, or set
   an **inbox folder** on the server (same view) — it is rescanned every time
   the Learning tab opens, and "Scan now" does it on demand. Files are only
   read, never moved or deleted.
2. **Each new lesson is checked against your deck.** Lessons are deduplicated
   on their own text, so re-importing a file that grew during the day adds
   only the new sections. Then the local model is shown the few existing
   cards (live or still queued) nearest the lesson by embedding and asked
   whether one of them already teaches it. If so the lesson is marked
   **Already covered**, with the card and the reason; "Make cards anyway"
   overrides that.
3. **Otherwise it becomes scenario cards** in the approval queue, tagged
   `#lesson`, in the destination folder chosen in the Import view. The cards
   describe a situation and ask what could go wrong — they never name the
   principle, because recognising _when_ it applies is the skill.

Code: `backend/learning/lessons.py` (parsing, import, inbox, the job),
`backend/ai/learning_lessons.py` (the two model calls),
`src/components/Learning/Lessons.tsx`.

## Using it at work

The instructions below keep lessons to general principles with no company,
product or code details — the kind of knowledge you'd carry out in your head
anyway. Even so, check your employer's policy on AI tools and on what may
leave a work machine. The safest route is the one that needs no tooling: let
the agent write the file locally (or write it yourself — putting a lesson in
your own words is part of how it sticks), read it over, and paste it into the
Import view from a personal device.

## The snippet

Paste this into the agent's instructions (`~/.claude/CLAUDE.md` for every
project, or a repository's `CLAUDE.md` / `AGENTS.md`). If your inbox folder
isn't `~/learnings`, change the path in it. The same text is in the Import
view, with a Copy button.

````markdown
## Learnings log

When we resolve something worth remembering — a bug whose cause wasn't
obvious, a design mistake caught late, a principle that would have prevented
the problem — append a lesson to today's learnings file,
`~/learnings/YYYY-MM-DD.md`. Create the folder and the file if needed, and
start a new file with a `# YYYY-MM-DD` heading. Don't ask first; tell me in
one line that you logged it.

Each lesson is one section in exactly this shape:

```markdown
## <the principle, as one imperative sentence>

- **What happened:** the symptom, in a sentence or two.
- **Failure it prevents:** what goes wrong without it.
- **When it doesn't apply:** the honest exception, if there is one.
```

Rules:

- Only lessons that transfer to other projects: engineering principles,
  language or library behaviour, debugging heuristics, architecture
  trade-offs. Facts about this codebase belong in its own docs, not here.
- Keep it generic. No company, product or internal service names, no file
  paths, no code copied from this project, no credentials or customer data.
  Describe the situation in ordinary terms.
- A few per day at most. Skip routine fixes (typos, missing imports) and
  anything the first paragraph of the relevant docs would have told me.
- Only append. Never edit or remove an earlier lesson.
````
