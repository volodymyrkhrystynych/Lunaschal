"""The nightly pass: extract, reconcile, render — and never revise prose.

The design under test exists because of a documented failure mode: an LLM asked
to revise its own prose night after night accumulates distortions it cannot
detect. So the tests that matter here are the drift ones — a fact keeps its
citation through a render, a contradiction supersedes rather than edits, a
locked fact survives, and a rebuild re-derives from source.
"""
import time

import pytest
from ulid import ULID

from backend.db.connection import get_db
from backend.day_boundary import day_bounds, day_key_for
from backend.lifewiki import facts as facts_mod
from backend.lifewiki import job
from backend.research import wiki


@pytest.fixture(autouse=True)
def ai_on(monkeypatch):
    monkeypatch.setattr('backend.ai.provider.is_ai_configured', lambda: True)


def _journal(db, content, when=None, entry_id=None):
    when = when or int(time.time())
    entry_id = entry_id or str(ULID())
    db.execute(
        'INSERT INTO journal_entries(id, content, created_at, updated_at)'
        ' VALUES (?,?,?,?)',
        (entry_id, content, when, when),
    )
    db.commit()
    return entry_id


def _fake_extract(monkeypatch, facts):
    monkeypatch.setattr(job.prompts, 'extract_facts', lambda *a, **k: facts)


def _fake_write(monkeypatch, **overrides):
    def write(slug, existing_title, facts_text):
        return {
            'title': overrides.get('title', 'Health and training'),
            'summary': overrides.get('summary', 'How they train.'),
            'content': overrides.get('content', f'Rendered from:\n{facts_text}'),
            'supersedes': overrides.get('supersedes', []),
        }
    monkeypatch.setattr(job.prompts, 'write_article', write)
    return write


# --- the pass ----------------------------------------------------------


def test_a_pass_writes_facts_and_renders_an_article(client, monkeypatch):
    db = get_db()
    entry = _journal(db, 'Went to the gym again, third time this week')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-training', 'title': 'Health and training',
        'statement': 'Trains three times a week.', 'source': f'journal:{entry}',
    }])
    _fake_write(monkeypatch)

    result = job.run_life_wiki_pass()

    assert result['facts'] == 1 and result['articles'] == 1
    article = wiki.get_article('health-and-training', None, kind=wiki.LIFE_KIND)
    assert article['summary'] == 'How they train.'
    assert facts_mod.live_facts(article['id'])[0]['sourceId'] == entry


def test_the_render_reads_the_facts_and_not_the_previous_prose(client, monkeypatch):
    """The whole design. The Nth render must read N facts, not N-1 renders —
    otherwise each rewrite compounds the last one's loss."""
    db = get_db()
    entry = _journal(db, 'Gym again')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-training', 'statement': 'Trains three times a week.',
        'source': f'journal:{entry}',
    }])

    seen = {}

    def write(slug, existing_title, facts_text):
        seen['facts_text'] = facts_text
        return {'title': 'Health and training', 'summary': 's',
                'content': 'DISTINCTIVE PROSE THE SECOND RENDER MUST NOT SEE'}

    monkeypatch.setattr(job.prompts, 'write_article', write)
    job.run_life_wiki_pass()
    job.run_life_wiki_pass()

    assert 'Trains three times a week.' in seen['facts_text']
    assert 'DISTINCTIVE PROSE' not in seen['facts_text']


def test_a_fact_without_a_usable_citation_is_dropped(client, monkeypatch):
    """An uncited fact cannot be checked by the user or re-derived by a rebuild,
    which is the entire contract the fact table keeps."""
    db = get_db()
    _journal(db, 'Something')
    _fake_extract(monkeypatch, [
        {'slug': 'a', 'statement': 'Invented.', 'source': 'made-it-up'},
        {'slug': 'a', 'statement': 'Also invented.', 'source': ''},
    ])
    _fake_write(monkeypatch)

    assert job.run_life_wiki_pass()['facts'] == 0


def test_the_model_naming_a_fact_it_already_wrote_does_not_double_the_article(
    client, monkeypatch
):
    """The window overlaps between runs on purpose, so this is the normal case
    rather than a malfunction."""
    db = get_db()
    entry = _journal(db, 'Gym again')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-training', 'statement': 'Trains three times a week.',
        'source': f'journal:{entry}',
    }])
    _fake_write(monkeypatch)

    job.run_life_wiki_pass()
    job.run_life_wiki_pass()

    article = wiki.get_article('health-and-training', None, kind=wiki.LIFE_KIND)
    assert len(facts_mod.live_facts(article['id'])) == 1


def test_a_contradiction_supersedes_rather_than_edits(client, monkeypatch):
    db = get_db()
    old_entry = _journal(db, 'Signed up at Movati')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-training', 'statement': 'Their gym is Movati.',
        'source': f'journal:{old_entry}',
    }])
    _fake_write(monkeypatch)
    job.run_life_wiki_pass()

    article = wiki.get_article('health-and-training', None, kind=wiki.LIFE_KIND)
    stale = facts_mod.live_facts(article['id'])[0]

    new_entry = _journal(db, 'Switched to GoodLife')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-training', 'statement': 'Their gym is GoodLife.',
        'source': f'journal:{new_entry}',
    }])
    monkeypatch.setattr(
        job.prompts, 'write_article',
        lambda slug, title, facts_text: {
            'title': 'Health and training', 'summary': 's', 'content': 'c',
            'supersedes': [stale['id']],
        },
    )
    job.run_life_wiki_pass()

    assert [f['statement'] for f in facts_mod.live_facts(article['id'])] == [
        'Their gym is GoodLife.'
    ]
    # Both rows still there — a wrong supersession has to be reversible.
    assert len(facts_mod.all_facts(article['id'])) == 2


def test_a_locked_fact_survives_the_pass_trying_to_supersede_it(client, monkeypatch):
    db = get_db()
    entry = _journal(db, 'Signed up at Movati')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-training', 'statement': 'Their gym is Movati.',
        'source': f'journal:{entry}',
    }])
    _fake_write(monkeypatch)
    job.run_life_wiki_pass()

    article = wiki.get_article('health-and-training', None, kind=wiki.LIFE_KIND)
    fact = facts_mod.live_facts(article['id'])[0]
    facts_mod.set_locked(fact['id'], True)

    monkeypatch.setattr(
        job.prompts, 'write_article',
        lambda slug, title, facts_text: {
            'title': 'Health and training', 'summary': 's', 'content': 'c',
            'supersedes': [fact['id']],
        },
    )
    job.run_life_wiki_pass()

    assert facts_mod.get_fact(fact['id'])['supersededBy'] is None


def test_a_locked_article_keeps_accruing_facts_but_keeps_its_prose(client, monkeypatch):
    db = get_db()
    entry = _journal(db, 'Gym again')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-training', 'statement': 'Trains three times a week.',
        'source': f'journal:{entry}',
    }])
    _fake_write(monkeypatch, content='WRITTEN BY THE PASS')
    job.run_life_wiki_pass()

    article = wiki.get_article('health-and-training', None, kind=wiki.LIFE_KIND)
    wiki.upsert_article(article['slug'], article['title'], 'mine', 'MY OWN WORDS',
                        kind=wiki.LIFE_KIND, author='user')
    db.execute('UPDATE wiki_articles SET locked=1 WHERE id=?', (article['id'],))
    db.commit()

    second = _journal(db, 'Ran on Sunday')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-training', 'statement': 'Runs on Sundays.',
        'source': f'journal:{second}',
    }])
    job.run_life_wiki_pass()

    assert wiki.get_article('health-and-training', None,
                            kind=wiki.LIFE_KIND)['content'] == 'MY OWN WORDS'
    assert len(facts_mod.live_facts(article['id'])) == 2


def test_a_quiet_window_writes_nothing(client, monkeypatch):
    """Most days add nothing that will still matter in a month. A pass that
    invents something to justify itself is the failure this design guards
    against."""
    _journal(get_db(), 'A quiet day')
    _fake_extract(monkeypatch, [])
    _fake_write(monkeypatch)

    assert job.run_life_wiki_pass() == {
        'facts': 0, 'articles': 0, 'observationsFolded': 0, 'timedOut': False
    }
    assert wiki.list_articles(kind=wiki.LIFE_KIND) == []


def test_an_empty_database_makes_no_model_call_at_all(client, monkeypatch):
    def explode(*a, **k):
        raise AssertionError('should not have called the model')

    monkeypatch.setattr(job.prompts, 'extract_facts', explode)
    assert job.run_life_wiki_pass()['facts'] == 0


def test_the_deadline_stops_rendering_and_says_so(client, monkeypatch):
    db = get_db()
    facts = []
    for i in range(3):
        entry = _journal(db, f'Thing {i}')
        facts.append({'slug': f'topic-{i}', 'title': f'Topic {i}',
                      'statement': f'Fact {i}.', 'source': f'journal:{entry}'})
    _fake_extract(monkeypatch, facts)
    _fake_write(monkeypatch)

    # Already expired: facts are written, no article is rendered, and the pass
    # reports the truth rather than an error.
    result = job.run_life_wiki_pass(deadline=time.monotonic() - 1)

    assert result['facts'] == 3
    assert result['articles'] == 0
    assert result['timedOut'] is True


# --- the dedupe gate ---------------------------------------------------


def test_a_near_duplicate_slug_lands_on_the_existing_article(client, monkeypatch):
    """Emergent topics are what makes `gym-routine`, `my-workouts` and
    `training` three articles that disagree. This is the guard against it."""
    db = get_db()
    first = _journal(db, 'Gym')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-training', 'title': 'Health and training',
        'statement': 'Trains three times a week.', 'source': f'journal:{first}',
    }])
    _fake_write(monkeypatch)
    job.run_life_wiki_pass()

    second = _journal(db, 'Gym again')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-trainings', 'title': 'Health and training',
        'statement': 'Runs on Sundays.', 'source': f'journal:{second}',
    }])
    job.run_life_wiki_pass()

    assert len(wiki.list_articles(kind=wiki.LIFE_KIND)) == 1


def test_a_genuinely_new_topic_gets_its_own_article(client, monkeypatch):
    db = get_db()
    first = _journal(db, 'Gym')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-training', 'title': 'Health and training',
        'statement': 'Trains three times a week.', 'source': f'journal:{first}',
    }])
    _fake_write(monkeypatch)
    job.run_life_wiki_pass()

    second = _journal(db, 'Reading about carburettors')
    _fake_extract(monkeypatch, [{
        'slug': 'interests', 'title': 'Interests',
        'statement': 'Is learning about engines.', 'source': f'journal:{second}',
    }])
    job.run_life_wiki_pass()

    assert len(wiki.list_articles(kind=wiki.LIFE_KIND)) == 2


def test_resolve_slug_ignores_the_code_and_research_wikis(client):
    """Life articles share the unscoped space with research notes. A life fact
    landing on a research article would be a category error the user would have
    no way to see."""
    wiki.upsert_article('scheduling', 'Scheduling', '', '', kind='research')
    assert job.resolve_slug('scheduling') is None


# --- observations ------------------------------------------------------


def test_a_cited_observation_is_marked_filed(client, monkeypatch):
    from backend import observations

    noted = observations.add_observation('Trains on Tuesdays')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-training', 'statement': 'Trains on Tuesdays.',
        'source': f'observation:{noted["id"]}',
    }])
    _fake_write(monkeypatch)

    assert job.run_life_wiki_pass()['observationsFolded'] == 1
    assert observations.pending() == []


def test_an_observation_the_pass_ignored_stays_pending(client, monkeypatch):
    """It may become durable next week. Dropping it silently would make
    `remember` a write into nothing."""
    from backend import observations

    observations.add_observation('Mentioned someone called Dave once')
    _fake_extract(monkeypatch, [])
    _fake_write(monkeypatch)

    job.run_life_wiki_pass()
    assert len(observations.pending()) == 1


# --- rebuild -----------------------------------------------------------


def test_rebuild_re_derives_the_fact_set_from_the_source_rows(client, monkeypatch):
    """The ground-truth verification: the sources are never mutated, so the
    article can always be thrown away and built again from them."""
    db = get_db()
    entry = _journal(db, 'Went to GoodLife, third time this week')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-training', 'title': 'Health and training',
        'statement': 'Trains three times a week.', 'source': f'journal:{entry}',
    }])
    _fake_write(monkeypatch)
    job.run_life_wiki_pass()

    article = wiki.get_article('health-and-training', None, kind=wiki.LIFE_KIND)
    # Drift: a fact nothing in the record supports.
    facts_mod.add_fact(article['id'], 'Hates exercise.',
                       source_kind='journal', source_id=entry)
    assert len(facts_mod.live_facts(article['id'])) == 2

    job.rebuild_article('health-and-training')

    assert [f['statement'] for f in facts_mod.live_facts(article['id'])] == [
        'Trains three times a week.'
    ]


def test_rebuild_keeps_what_the_user_locked(client, monkeypatch):
    db = get_db()
    entry = _journal(db, 'Went to GoodLife')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-training', 'title': 'Health and training',
        'statement': 'Trains three times a week.', 'source': f'journal:{entry}',
    }])
    _fake_write(monkeypatch)
    job.run_life_wiki_pass()

    article = wiki.get_article('health-and-training', None, kind=wiki.LIFE_KIND)
    mine = facts_mod.add_fact(article['id'], 'Their gym is GoodLife.',
                              source_kind='journal', source_id=entry)
    facts_mod.set_locked(mine['id'], True)

    job.rebuild_article('health-and-training')

    statements = {f['statement'] for f in facts_mod.live_facts(article['id'])}
    assert 'Their gym is GoodLife.' in statements


def test_rebuild_does_not_empty_an_article_when_the_facts_belong_elsewhere(
    client, monkeypatch
):
    """One journal entry can feed several articles. Clearing on the strength of
    one model call is worse than leaving the drift for another night."""
    db = get_db()
    entry = _journal(db, 'Gym, then read about carburettors')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-training', 'title': 'Health and training',
        'statement': 'Trains three times a week.', 'source': f'journal:{entry}',
    }])
    _fake_write(monkeypatch)
    job.run_life_wiki_pass()

    article = wiki.get_article('health-and-training', None, kind=wiki.LIFE_KIND)
    _fake_extract(monkeypatch, [{
        'slug': 'interests', 'title': 'Interests',
        'statement': 'Is learning about engines.', 'source': f'journal:{entry}',
    }])
    job.rebuild_article('health-and-training')

    assert len(facts_mod.live_facts(article['id'])) == 1


def test_rebuilding_an_unknown_article_is_a_no_op(client):
    assert job.rebuild_article('nothing-here') is None


# --- what reads the wiki afterwards ------------------------------------


def test_the_briefing_runs_even_when_the_wiki_pass_explodes(client, monkeypatch):
    """The briefing is what the user wakes up to. A picture one night out of
    date is a far smaller loss than no plan for the day."""
    from backend import briefing_scheduler

    def explode(*a, **k):
        raise RuntimeError('llama-server died')

    ran = {}
    monkeypatch.setattr('backend.lifewiki.job.run_life_wiki_pass', explode)
    monkeypatch.setattr(briefing_scheduler, 'run_briefing',
                        lambda: ran.setdefault('briefing', True))

    briefing_scheduler.run_nightly()
    assert ran['briefing'] is True


def test_the_wiki_pass_runs_before_the_briefing(client, monkeypatch):
    """The whole reason they share a thread: the briefing reads what the pass
    writes, so a wiki mid-rewrite is the failure two daemons would produce."""
    from backend import briefing_scheduler

    order = []
    monkeypatch.setattr('backend.lifewiki.job.run_life_wiki_pass',
                        lambda **k: order.append('wiki') or {'facts': 0, 'articles': 0,
                                                             'observationsFolded': 0})
    monkeypatch.setattr(briefing_scheduler, 'run_briefing',
                        lambda: order.append('briefing'))

    briefing_scheduler.run_nightly()
    assert order == ['wiki', 'briefing']


def test_the_briefing_is_shown_what_the_wiki_knows(client, monkeypatch):
    from backend.ai.briefing import build_briefing_prompt, gather_briefing_context

    wiki.upsert_article('health-and-training', 'Health and training',
                        'Trains three times a week at GoodLife.', 'body',
                        kind=wiki.LIFE_KIND)

    prompt = build_briefing_prompt(gather_briefing_context())
    assert 'Trains three times a week at GoodLife.' in prompt


def test_a_context_built_before_the_wiki_existed_still_renders(client):
    """An old fixture, or a caller assembling one by hand, should render a
    briefing without the block rather than raise on the way to the model."""
    from backend.ai.briefing import build_briefing_prompt

    prompt = build_briefing_prompt({
        'now': 1_700_000_000, 'today': '2026-03-04', 'goals': '',
        'journal': [], 'daily_tasks': [], 'todos': [], 'calendar': [],
        'learning_due': 0,
    })
    assert 'No recent journal' in prompt


def test_the_chat_prompt_carries_the_index_but_not_the_bodies(client):
    """Paying for synthesis overnight is only worth it if the result is cheap to
    consult at turn time; inlining every article spends that straight back."""
    from backend.ai.chat import build_chat_system_prompt

    wiki.upsert_article('health-and-training', 'Health and training',
                        'How they train.', 'A LONG BODY THAT MUST NOT BE INLINED',
                        kind=wiki.LIFE_KIND)

    prompt = build_chat_system_prompt()
    assert 'health-and-training' in prompt
    assert 'How they train.' in prompt
    assert 'A LONG BODY' not in prompt


def test_the_chat_agent_is_never_shown_a_code_note(client):
    """Life articles share the unscoped space with research notes; the Ideas
    agent's wiki and the chat's must not leak into each other."""
    from backend.ai.chat import build_chat_system_prompt

    wiki.upsert_article('scheduling', 'Scheduling', 'How schedulers work.', 'body',
                        kind='research')
    assert 'How schedulers work.' not in build_chat_system_prompt()


def test_the_ideas_agent_is_never_shown_a_life_note(client):
    """The mirror image, and the one that matters more: there is no reading of
    "how do other people solve this" that wants the user's eating habits."""
    wiki.upsert_article('food-and-eating', 'Food and eating', 'What they eat.',
                        'body', kind=wiki.LIFE_KIND)

    text, event = wiki.WikiTools().run_tool('wiki_list', {})
    assert 'food-and-eating' not in text
    assert event['count'] == 0


def test_a_different_topic_sharing_a_stopword_gets_its_own_article(client, monkeypatch):
    """The dedupe gate used to fall back to an FTS search on the title, and
    `fts_match_query` builds a prefix-OR — so "Food and eating" matched an
    existing "Health and training" on the word *and*, and a fact about cooking
    was filed under the gym."""
    db = get_db()
    first = _journal(db, 'Gym')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-training', 'title': 'Health and training',
        'statement': 'Trains three times a week.', 'source': f'journal:{first}',
    }])
    _fake_write(monkeypatch)
    job.run_life_wiki_pass()

    second = _journal(db, 'Made ramen again')
    _fake_extract(monkeypatch, [{
        'slug': 'food-and-eating', 'title': 'Food and eating',
        'statement': 'Makes ramen often.', 'source': f'journal:{second}',
    }])
    job.run_life_wiki_pass()

    assert {a['slug'] for a in wiki.list_articles(kind=wiki.LIFE_KIND)} == {
        'health-and-training', 'food-and-eating'
    }


def test_a_reworded_slug_under_the_same_title_still_lands_on_it(client, monkeypatch):
    """The model may reword the slug without the title. Matching on both is what
    keeps that from starting a second article about the same thing."""
    db = get_db()
    first = _journal(db, 'Gym')
    _fake_extract(monkeypatch, [{
        'slug': 'health-and-training', 'title': 'Health and training',
        'statement': 'Trains three times a week.', 'source': f'journal:{first}',
    }])
    _fake_write(monkeypatch)
    job.run_life_wiki_pass()

    second = _journal(db, 'Gym again')
    _fake_extract(monkeypatch, [{
        'slug': 'gym-routine', 'title': 'Health and training',
        'statement': 'Runs on Sundays.', 'source': f'journal:{second}',
    }])
    job.run_life_wiki_pass()

    assert len(wiki.list_articles(kind=wiki.LIFE_KIND)) == 1
