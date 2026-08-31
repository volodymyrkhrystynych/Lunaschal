# The life wiki (`backend/lifewiki/`)

Two things live here: **read tools** the chat agent uses on its own turn, and the
**nightly pass** that synthesizes what the user records into a wiki about them.
They share a module because the pass uses the same searches the agent does.

## Why it is shaped like this

The design was checked against the published work before it was built, and the
research changed it twice.

- **Synthesis earns its place.** The Generative Agents ablation found reflection
  load-bearing — without it agents "degenerate from coherent multi-day planning
  to repetitive, context-free responses within 48 simulated hours." Letta's
  sleep-time compute reports ~5x less test-time compute for equal accuracy,
  an argument that is _stronger_ here than on an API: generation at ~34 tok/s is
  the scarcest resource in this app, and 05:00 is free.
- **But continuous LLM rewriting of stored memory corrupts it.** "Useful
  Memories Become Faulty When Continuously Updated by LLMs" finds that repeated
  model updates accumulate distortions the system cannot itself detect. An
  earlier draft had the pass do `article + new data -> revised article`, nightly,
  forever. That is the indicted mechanism, and it is gone.
- **Facts beat prose as the retrieval unit** (LongMemEval: decomposition to
  rounds or extracted user-facts beats whole-session summarization).
- **No embeddings.** At personal scale a vector store adds more retrieval noise
  and latency than it removes; FTS5 and slug matching are the right size. This
  also matches the existing `research/wiki.py` stance, for different reasons.

## The consequences, which are the invariants

- **`life_facts` is the memory; the article's prose is a derived view.** The
  render reads the _fact list_, never the article's previous content. The Nth
  render therefore reads N facts, not N-1 renders — there is no chain to
  compound along. `render_article` is where this is enforced.
- **Every fact cites the row it came from.** `digest.py` puts a `[journal:01J…]`
  id on every line precisely so the model can produce one; a fact whose citation
  does not parse is **dropped**, not stored uncited. An uncitable fact cannot be
  checked by the user or re-derived, which is the whole contract.
- **Nothing is edited or deleted by the pass.** A contradicted fact is
  _superseded_ — a pointer, both rows kept — so a wrong supersession is visible
  and reversible. Only the user deletes.
- **Locked is frozen.** A fact the user checked is never superseded; a locked
  article keeps its prose while facts accrue underneath it.
- **The wiki is a derived cache, never the system of record.** Journal, chats,
  food and the rest are never mutated by any of this, so
  `job.rebuild_article(slug)` can throw the derived facts away and re-extract
  from the rows the old ones cited. That is the ground-truth verification the
  drift research asks for, and it is exposed as a button.

## Things that will bite

- **`row_to_dict` camelCases.** A fact row is `sourceKind`/`sourceId`, not
  `source_kind`/`source_id`. Reading the snake_case names in `rebuild_article`
  produced an empty citation list — a rebuild that silently did nothing rather
  than one that failed.
- **The window overlaps between runs** (`WINDOW_DAYS = 3`), deliberately: a pass
  missed while the machine was off is not lost, and two mentions three days
  apart are what a habit looks like. `facts.add_fact` moves `last_seen` on a
  fact it already holds rather than writing a second row, which is what makes
  the overlap safe.
- **Emergent topics need the dedupe gate.** The user chose emergent slugs over a
  fixed spine, and the price is `gym-routine` / `my-workouts` / `training` as
  three articles that disagree. `job.resolve_slug` guards it in three cheap
  steps: exact slug, `difflib` ratio over existing slugs (`SLUG_MATCH_RATIO`),
  then an FTS hit on the title.
- **The assistant's own replies are never in the digest.** Building a standing
  fact from its own prose is the shortest path to a confident invention.
- **Notebook `diary/` is excluded.** Those become journal entries via
  `backend/notebook_diary_scheduler.py`, and counting both would let one day's
  thought be two pieces of evidence for the same fact.
- **A rebuild filters extraction back to the article it is rebuilding.** One
  journal entry legitimately feeds several articles; sweeping all of them onto
  the one being rebuilt is a category error. If nothing survives the filter the
  rebuild aborts rather than emptying the article on one model call.

## Scheduling

`backend/briefing_scheduler.py` calls `run_life_wiki_pass()` then
`run_briefing()`, **in one thread, in that order** — the briefing reads the wiki,
and two independently scheduled daemons would eventually have it reading a wiki
mid-rewrite. The pass carries a wall-clock budget (`DEFAULT_BUDGET_SECONDS`) and
its failure is caught: half a wiki plus a briefing beats a complete wiki and no
briefing. It needs no new window and no new llama slot.
