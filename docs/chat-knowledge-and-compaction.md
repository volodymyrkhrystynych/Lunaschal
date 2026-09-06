# Chat knowledge tools and context compaction

**Status: implemented on `feat/offline-knowledge-library`.** The offline
Knowledge reader and its search/read tools run on the main chat agent, web
research stays behind the delegate, and long conversations have rolling and
New Chat compaction. The final threshold values remain deliberately tunable
until the exact local quant has been evaluated across context lengths.

The Knowledge tab's Wikipedia-first scope, embedded-libzim reader, retrieval
pipeline, known limitations, and deferred library roadmap are documented in
[`knowledge-tab.md`](./knowledge-tab.md).

## Decision

The main chat agent should directly own the tools whose results it needs to
judge:

- `local_knowledge_search`
- `local_knowledge_read`
- the existing life-history and life-wiki recall tools
- the existing proposal and reversible-write tools

The research delegate should retain:

- `web_search`
- `web_fetch`
- `deep_research`

The main agent searches the local library with two to four complementary query
variants in one call, chooses from the merged and title-ranked candidates,
reads promising results, and decides whether the evidence is sufficient. It
delegates only when the local evidence is absent, contradictory, too old for
the question, or not specific enough. The variants include the clean
entity/title, the full question, and plausible interpretations; they must not
smuggle a guessed answer into retrieval.

“Skill” describes the instructions governing this behavior. Searching and
reading the archive are tools: the model cannot gain filesystem or ZIM access
from prompt instructions alone.

## Why change the current design

Today the main agent hands one rewritten task to the delegate. The delegate can
search and read the offline library, but only its closing summary crosses into
the main agent's answer prompt. The main agent therefore does not see the actual
result list or article passage and cannot independently decide whether the
delegate found enough evidence. On a follow-up turn it retains the prose answer,
not the underlying article evidence.

That separation is useful for large, noisy web research. It is counterproductive
for the local library, where search is cheap, trusted, offline, and central to
the conversation. Moving local tools onto the main agent preserves the user's
exact question, earlier disambiguation, result snippets, article identity, and
relevant text in one reasoning process.

The target flow is:

```text
user question
    |
    v
main agent searches local library
    |
    v
main agent reads one or more promising articles
    |
    +-- evidence sufficient ------> answer with local sources
    |
    +-- evidence insufficient ----> delegate web research
                                      |
                                      v
                              cited compact summary
                                      |
                                      v
                                  main answer
```

Questions that are inherently current, such as today's weather or a current
office-holder, may go directly to the web delegate. “Local first” must not turn
an archive snapshot into pretend-live data.

## Evidence sufficiency

The final-answering agent, not the research delegate, owns the sufficiency
decision. Local evidence is sufficient when it:

1. addresses the entity and interpretation in the user's question;
2. directly supports the material claim rather than merely mentioning related
   words;
3. is current enough for the kind of claim being made;
4. is not materially contradicted by another local source; and
5. contains enough detail to answer without filling gaps from a guess.

If a title is ambiguous, the agent should use the conversation to disambiguate
it or say what interpretation it used. For example, an article about a film is
not sufficient evidence for a question that may concern its source novel.

The web delegate should be used when any check fails. Its summary must identify
what the local evidence was missing, report the facts found on the web, and
return sources. The final answer should distinguish local and web evidence when
that distinction matters.

## Main-agent tool loop

The current chat decision turn is one round. That is enough to make a proposal,
but not enough for normal retrieval: useful local lookup usually requires
`search -> inspect results -> read article -> assess`, and sometimes another
search.

The main chat therefore needs a bounded multi-step gathering phase before its
streamed answer. It must use the existing reusable loop in
`backend/research/agent.py`, parameterized with the chat toolbox and dispatch,
rather than introducing a second implementation of tool-call parsing,
checkpoints, deadlines, or finish-reason handling.

The gathering phase should retain the useful local tool exchange for the answer
turn. Proposal tools must preserve their present semantics: proposals remain
confirmation cards, `ask_user` stages nothing, and the explicitly reversible
writes remain visible in the step trace. A custom caller-supplied system prompt
continues to disable tools for voice and other callers that have nowhere to
display those interactions.

The web delegate remains one tool available to this loop. Its internal
transcript does not cross the boundary; its cited closing summary does. The
delegate no longer needs the local knowledge tools once the migration is
complete.

## What remains in conversational context

“The main agent sees the evidence” should not mean “every byte returned by every
tool is replayed forever.” A long article full of irrelevant material makes
later recall worse even when it fits inside the nominal context window.

During the current turn, the answer prompt receives the capped result snippets
and article text actually inspected. After the turn, the conversation retains a
compact evidence record:

```json
{
  "kind": "offline_knowledge",
  "archiveId": "simplewiki-2026-06",
  "path": "A/Charlie_and_the_Chocolate_Factory",
  "title": "Charlie and the Chocolate Factory",
  "query": "Charlie and the Chocolate Factory publication year",
  "claims": ["The novel was first published in 1964."],
  "excerpt": "A short passage directly supporting the retained claim."
}
```

Evidence records live in assistant-message metadata. Compaction checkpoints
live in `chat_compactions`; each carries the exact covered message IDs and a
stable archive/article identity so the full page can be reopened later without
storing it in every subsequent prompt. Evidence used for an answer renders as a
source in the UI.

Recent evidence records can be included automatically in the active context.
Older evidence remains searchable and is rehydrated only when a later question
needs it. Raw messages and raw tool results are never deleted by compaction.

## Context budget

The local Qwen3.6 server is configured for approximately 190,000 tokens, inside
the model's native 262,144-token window. That is capacity, not a promise that a
190,000-token transcript is equally easy to reason over. Prompt instructions,
tool schemas, tool results, reasoning, and the generated response all need
headroom.

Initial operating thresholds for this deployment:

| Occupied input | Policy                                                              |
| -------------- | ------------------------------------------------------------------- |
| Below 120K     | Keep the active segment and compact evidence records normally.      |
| 120K–150K      | Compact older conversational sections before the next answer.       |
| 150K–170K      | Compact aggressively and rehydrate only directly relevant evidence. |
| Above 170K     | Refuse further prompt growth until compaction succeeds.             |

These are engineering defaults, not model constants. Token counts should come
from the serving model's tokenizer when practical; a conservative estimate is
acceptable as a fallback. The thresholds should ultimately derive from the
configured context size while reserving at least 20,000 tokens for system/tool
overhead and generation.

## Rolling compaction

Compaction cannot run only when the user presses **New chat**. One uninterrupted
conversation can cross the safe operating range by itself, so the backend must
also compact automatically when the active prompt reaches its soft threshold.

A compacted section should preserve:

- user facts and preferences relevant beyond the moment;
- decisions and conclusions;
- open questions and unresolved disagreements;
- promises, tasks, and proposed actions, including their status;
- named entities and disambiguations;
- claims learned from research;
- source identities and the smallest useful supporting excerpts; and
- corrections to earlier statements.

It should discard greetings, duplicated wording, abandoned reasoning, rendered
tool plumbing, and article text unrelated to a retained claim. Recent turns
remain verbatim so the agent keeps conversational texture and immediate
references.

Compaction is a derived view, never a rewrite of the source conversation. Store
the covered message IDs and the model/prompt version used to create it. A newer
compaction may supersede an older one, but the original messages remain in
SQLite and available to `search_conversations`.

## New Chat as a compaction boundary

Currently **New chat** inserts a break, and the browser sends only the segment
after the most recent break. The old messages remain visible and saved. Under
this design, pressing it also closes the outgoing segment and creates a
structured chapter summary.

The new segment may receive a short handoff containing the outgoing segment's
durable facts, decisions, open threads, and evidence references. It must not
receive a compressed retelling of every casual exchange; otherwise **New chat**
would no longer clear the conversational subject in any meaningful sense.

The UI should distinguish two intentions:

- **New chat**: close and compact the segment, carrying only durable handoff
  context.
- **Clean slate**: start a segment with no handoff, while leaving the old
  transcript saved and searchable.

Creating the break must not depend on successful model inference. If compaction
times out or the LLM server is unavailable, **New chat** still succeeds, records
the segment as awaiting compaction, and a later background pass can retry it.
This requires a durable checkpoint rather than an in-memory job.

## Source-of-truth and safety rules

- The original `messages` rows remain the authoritative transcript.
- A compaction summary is derived data and identifies exactly which messages it
  covers.
- An evidence record points back to an archive and article path; its excerpt is
  not treated as the complete article.
- The main agent must never claim that local evidence is current when the
  question requires live information.
- Web pages and their large intermediate outputs stay isolated inside the
  delegate; only its cited summary crosses back.
- Compaction does not write to the user's standing Memory document. Promotion
  into durable user memory remains an explicit, separately governed action.
- A failed search, read, web delegation, or compaction is visible to the agent
  and user rather than silently replaced with an unsupported answer.

## Migration sequence

1. **Done:** add direct local knowledge tools to a bounded main-chat gathering
   loop.
2. **Done:** remove local knowledge tools from the web delegate; retain web
   search, fetch, and deep research there.
3. **Done:** persist compact local evidence records and make them available on
   follow-up turns.
4. **Done:** add token accounting and rolling compaction with durable
   checkpoints.
5. **Done:** make **New chat** finalize a chapter summary and add an explicit
   clean-slate path.
6. **Remaining calibration:** test the exact local model at increasing context
   lengths and tune the default thresholds from observed recall and reasoning
   behavior.

Each stage should be independently usable. Direct local retrieval should not be
blocked on building the full compaction system, and compaction should not be
allowed to make the saved transcript less complete than it is today.

## Verification

Automated coverage should demonstrate at least:

- local search and read happen on the main agent's transcript;
- a sufficient local result does not invoke the web delegate;
- missing, stale, or contradictory local evidence can invoke it;
- a follow-up can use retained evidence or reopen its exact article;
- raw web-fetch output never enters later chat context;
- compaction stays below its target budget while preserving required facts,
  corrections, open threads, and citations;
- compacted messages remain available through conversation search;
- **New chat** works while the LLM server is unavailable and queues a retry;
- clean slate includes no handoff; and
- no production-port, scheduler, proposal, or custom-system-prompt invariants
  regress.
