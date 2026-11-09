"""The nightly pass that turns the user's own record into a wiki about them.

Runs immediately before the morning briefing, in the same thread, so the
briefing writes from a standing picture instead of re-deriving the user from raw
rows every morning. See backend/briefing_scheduler.py for why that is a sequence
and not two daemons.

**It extracts and renders; it never revises prose.** Continuous LLM rewriting of
stored memory is a documented failure mode — distortions accumulate that the
model cannot itself detect, each rewrite compounding the last one's loss. So:

1. **Extract** — one call over the window's digest produces short facts, each
   citing the row it came from. Additive only.
2. **Reconcile** — a fact contradicted by a newer one is *superseded*, which
   writes a pointer and leaves both rows. Nothing is edited, nothing deleted.
3. **Render** — the article's prose is regenerated from its current fact list,
   never from its previous prose. The Nth render reads N facts, not N-1 renders.

Two budgets, both because this shares a two-hour window with the briefing:
`MAX_ARTICLES_PER_RUN` bounds how many articles are re-rendered, and `deadline`
stops the whole thing. Running out of time is not a failure — it is the pass
doing less, and the briefing still running. That is `backend/delegate/limits.py`'s
rule applied to a background job.

One rule inherited from research_job: never hold a transaction across a model
call. get_db() hands out one process-global connection, so a commit() in any
Flask request handler would commit whatever this thread had pending. Write,
commit, then call the model.
"""
import difflib
import logging
import time

from backend.ai import life_wiki as prompts
from backend.day_boundary import day_key_for
from backend.db.connection import get_db
from backend.lifewiki import digest as digest_mod
from backend.lifewiki import facts as facts_mod
from backend.research import wiki

logger = logging.getLogger(__name__)

# How many articles get re-rendered in one run. Each is a model call on a local
# model inside a shared window; the wiki fills in over a week rather than in one
# night, exactly as the code wiki does.
MAX_ARTICLES_PER_RUN = 6

# Wall-clock budget for one run, in seconds. The briefing window is two hours
# and the briefing itself is the thing that must not be missed, so this leaves
# it well over an hour. Running out is not a failure: the pass does less and the
# briefing still runs.
DEFAULT_BUDGET_SECONDS = 45 * 60

# How many days back one run reads. More than one so a pass missed while the
# machine was off is not simply lost, and because two mentions three days apart
# are what a habit looks like. Overlap is safe: `add_fact` moves `last_seen` on
# a fact it has already recorded rather than writing it twice.
WINDOW_DAYS = 3

# How alike two slugs must be before a proposed new article is treated as the
# existing one instead. Emergent topics are what the user asked for, and this is
# the price: without it `gym-routine`, `my-workouts` and `training` become three
# articles that disagree.
SLUG_MATCH_RATIO = 0.82


def _out_of_time(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def resolve_slug(proposed: str, title: str | None = None) -> dict | None:
    """The existing life article a proposed slug should land on, or None for new.

    Two guards: the slug as given, then a close-enough spelling of an existing
    slug — tried against both the proposed slug and the slugified proposed
    title, since the model may reword one without the other
    ("gym-routine" titled "Health and training").

    **There was a third, an FTS search on the title, and it was actively
    harmful.** `fts_match_query` builds a prefix-OR, so "Food and eating"
    matched an existing "Health and training" on the word *and* — a fact about
    the user's cooking was filed under their gym habits, and the article it
    should have started never existed. Character similarity has no such failure:
    the same pair scores 0.53 where a real near-duplicate
    ("health-and-trainings") scores 0.97.

    No embeddings either — at this corpus size a vector store adds more
    retrieval noise than the recall it buys.
    """
    candidates = [wiki.slugify(c) for c in (proposed, title) if c]
    candidates = [c for c in candidates if c]
    if not candidates:
        return None

    for candidate in candidates:
        exact = wiki.get_article(candidate, None, kind=wiki.LIFE_KIND)
        if exact:
            return exact

    existing = wiki.list_articles(wiki.WIKI_INDEX_MAX, repo_id=None,
                                  kind=wiki.LIFE_KIND)
    best, best_ratio = None, 0.0
    for article in existing:
        for candidate in candidates:
            ratio = difflib.SequenceMatcher(None, candidate, article['slug']).ratio()
            if ratio > best_ratio:
                best, best_ratio = article, ratio
    return best if best and best_ratio >= SLUG_MATCH_RATIO else None


def _ensure_article(slug: str, title: str | None, now: int) -> dict:
    """Find the article a fact belongs to, creating an empty one if it is new.

    Created empty and rendered later in the same run: the fact has to have
    somewhere to live before the render can read the fact list back.
    """
    existing = resolve_slug(slug, title)
    if existing:
        return existing
    return wiki.upsert_article(
        wiki.slugify(slug or title or 'about-the-user'),
        (title or slug or 'About the user').strip(),
        '', '', repo_id=None, kind=wiki.LIFE_KIND, author='agent',
        note='created by the life-wiki pass', now=now,
    )


def record_facts(raw_facts: list[dict], now: int) -> tuple[dict[str, int], dict[str, str]]:
    """Write extracted facts.

    Returns ({article_id: facts written}, {source_id: article_id}) — the second
    is what tells the observation fold which article a note ended up in, without
    re-resolving every citation against the database a second time.

    A fact whose citation does not parse is dropped rather than stored uncited:
    the user cannot check it and `rebuild_article` cannot re-derive it, which is
    the entire contract the fact table exists to keep.
    """
    touched: dict[str, int] = {}
    by_source: dict[str, str] = {}
    for raw in raw_facts:
        statement = (raw.get('statement') or '').strip()
        citation = prompts.parse_citation(raw.get('source') or '')
        if not statement or citation is None:
            logger.info('Dropping uncited life fact: %r', statement[:80])
            continue
        source_kind, source_id = citation

        article = _ensure_article(raw.get('slug') or '', raw.get('title'), now)
        try:
            fact = facts_mod.add_fact(
                article['id'], statement,
                source_kind=source_kind, source_id=source_id, now=now,
            )
        except ValueError:
            continue
        if fact is not None:
            touched[article['id']] = touched.get(article['id'], 0) + 1
            by_source[source_id] = article['id']
    return touched, by_source


def _fold_observations(raw_facts: list[dict], touched_by_source: dict, now: int) -> int:
    """Mark the observations that made it into a fact as filed.

    Only the ones actually cited. An observation the pass read and chose not to
    record stays pending — it may become durable next week, and silently
    dropping it would make `remember` a write into nothing.
    """
    db = get_db()
    folded = 0
    for raw in raw_facts:
        citation = prompts.parse_citation(raw.get('source') or '')
        if citation is None or citation[0] != 'observation':
            continue
        article_id = touched_by_source.get(citation[1])
        cur = db.execute(
            'UPDATE assistant_observations SET folded_at=?, article_id=?'
            ' WHERE id=? AND folded_at IS NULL',
            (now, article_id, citation[1]),
        )
        folded += cur.rowcount
    db.commit()
    return folded


def render_article(article: dict, now: int) -> dict | None:
    """Regenerate one article's prose from its current facts.

    Deliberately never shown its own previous content — that is the whole
    mechanism this design exists to avoid.
    """
    live = facts_mod.live_facts(article['id'])
    if not live:
        return None
    written = prompts.write_article(
        article['slug'], article.get('title'),
        facts_mod.format_facts(live, with_ids=True),
    )
    if not written:
        return None

    # Supersessions first, then the render, so the article that gets written
    # reflects the fact list as it now stands.
    by_id = {f['id']: f for f in live}
    for fact_id in (written.get('supersedes') or []):
        if fact_id in by_id:
            facts_mod.supersede(fact_id, None)

    try:
        return wiki.upsert_article(
            article['slug'], written.get('title') or article['title'],
            written.get('summary') or '', written.get('content') or '',
            repo_id=None, kind=wiki.LIFE_KIND, author='agent',
            note=f'rendered from {len(live)} facts', now=now,
        )
    except wiki.ArticleLocked:
        # The user took the article over. Facts keep accruing underneath it —
        # they are what a later unlock or rebuild would render from — but their
        # prose is theirs now.
        logger.info('Life article %s is locked; facts recorded, prose left alone',
                    article['slug'])
        return None


def run_life_wiki_pass(now: int | None = None, deadline: float | None = None,
                       days: int = WINDOW_DAYS) -> dict:
    """One pass. Returns a summary dict; never raises for an ordinary failure."""
    from backend.ai.provider import is_ai_configured

    now = now if now is not None else int(time.time())
    result = {'facts': 0, 'articles': 0, 'observationsFolded': 0, 'timedOut': False}
    if not is_ai_configured():
        return result

    window = digest_mod.gather(day_key_for(now), days)
    if digest_mod.is_empty(window):
        return result

    index = wiki.list_articles(wiki.WIKI_INDEX_MAX, repo_id=None, kind=wiki.LIFE_KIND)
    index_lines = '\n'.join(
        f'- {a["slug"]}: {a["title"]} — {a.get("summary") or ""}' for a in index
    )

    raw_facts = prompts.extract_facts(digest_mod.render(window), index_lines)
    if not raw_facts:
        return result

    touched, by_source = record_facts(raw_facts, now)
    result['facts'] = sum(touched.values())
    result['observationsFolded'] = _fold_observations(raw_facts, by_source, now)

    # Most facts first: the article that learned the most is the one whose prose
    # is furthest from its facts.
    ordered = sorted(touched.items(), key=lambda kv: kv[1], reverse=True)
    for article_id, _count in ordered[:MAX_ARTICLES_PER_RUN]:
        if _out_of_time(deadline):
            result['timedOut'] = True
            logger.info('Life-wiki pass hit its deadline; %d articles rendered',
                        result['articles'])
            break
        article = wiki.get_article_by_id(article_id)
        if article is None:
            continue
        if render_article(article, now):
            result['articles'] += 1

    return result


def rebuild_article(slug: str, now: int | None = None) -> dict | None:
    """Discard everything derived about an article and re-extract from source.

    The ground-truth verification the drift research asks for, and what makes
    "the wiki is a derived cache, never the system of record" a fact rather than
    a slogan: the journal entries, messages and meals a fact cites are never
    touched by any of this, so the article can always be thrown away and built
    again from them.

    Locked facts survive — a rebuild corrects the machine's drift, not the
    user's corrections to it. A fact whose source row is gone (a deleted entry,
    a folded observation) is not carried over: a rebuild keeps only what it can
    still verify.
    """
    now = now if now is not None else int(time.time())
    article = wiki.get_article(slug, None, kind=wiki.LIFE_KIND)
    if article is None:
        return None

    # camelCase keys: `row_to_dict` renames the columns on the way out, and
    # reading `source_id` here silently produced an empty citation list — a
    # rebuild that quietly did nothing rather than one that failed.
    citations = [
        (f['sourceKind'], f['sourceId'])
        for f in facts_mod.all_facts(article['id'])
        if f.get('sourceId') and not f['locked']
    ]
    if not citations:
        return None

    window = digest_mod.for_sources(citations)
    if digest_mod.is_empty(window):
        return None

    # Only this article in the index, so the extraction targets it — and then
    # filtered to it anyway. One journal entry can legitimately feed several
    # articles, and a rebuild of `health-and-training` must not sweep a fact
    # about the user's work into it just because both cite the same morning.
    index_line = f'- {article["slug"]}: {article["title"]} — {article.get("summary") or ""}'
    raw_facts = prompts.extract_facts(digest_mod.render(window), index_line)
    if not raw_facts:
        return None

    mine = []
    for raw in raw_facts:
        citation = prompts.parse_citation(raw.get('source') or '')
        statement = (raw.get('statement') or '').strip()
        if citation is None or not statement:
            continue
        target = resolve_slug(raw.get('slug') or '', raw.get('title'))
        if target is None or target['id'] != article['id']:
            continue
        mine.append((statement, citation))

    if not mine:
        # Everything it found belongs elsewhere. Clearing would leave the
        # article empty on the strength of one model call, which is a worse
        # outcome than leaving the drift in place for another night.
        return None

    facts_mod.clear_derived(article['id'])
    for statement, (source_kind, source_id) in mine:
        try:
            facts_mod.add_fact(article['id'], statement, source_kind=source_kind,
                               source_id=source_id, now=now)
        except ValueError:
            continue

    return render_article(article, now)
