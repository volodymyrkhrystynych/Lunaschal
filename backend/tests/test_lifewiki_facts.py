"""The fact store beneath a life-wiki article.

The prose is derived; these rows are the memory. Everything asserted here is
about that inversion holding: facts cite their source, nothing is edited, and a
fact the user has checked cannot be overruled by the pass.
"""
import pytest

from backend.lifewiki import facts as facts_mod
from backend.research import wiki


@pytest.fixture
def article(client):
    return wiki.upsert_article('health-and-training', 'Health and training',
                               '', '', kind=wiki.LIFE_KIND)


def test_a_fact_keeps_the_id_of_the_row_it_came_from(article):
    """The citation is what makes a wrong fact findable rather than merely
    plausible, and it is what a rebuild re-derives from."""
    fact = facts_mod.add_fact(article['id'], 'Trains on Tuesdays and Fridays',
                              source_kind='journal', source_id='01JOURNAL')
    assert fact['sourceKind'] == 'journal'
    assert fact['sourceId'] == '01JOURNAL'


def test_restating_a_fact_moves_last_seen_rather_than_writing_a_second_row(article):
    """More evidence for the same fact is confidence, not a second fact — and it
    is what stops an overlapping nightly window doubling the article each run."""
    first = facts_mod.add_fact(article['id'], 'Trains on Tuesdays',
                               source_kind='journal', source_id='a', now=1000)
    again = facts_mod.add_fact(article['id'], 'trains on TUESDAYS',
                               source_kind='journal', source_id='b', now=2000)

    assert again['id'] == first['id']
    assert len(facts_mod.live_facts(article['id'])) == 1
    assert again['lastSeen'] != again['firstSeen']


def test_an_unknown_source_is_refused_rather_than_stored_uncitable(article):
    with pytest.raises(ValueError):
        facts_mod.add_fact(article['id'], 'Something', source_kind='vibes')


def test_superseding_leaves_both_rows_in_place(article):
    """Nothing is edited and nothing is deleted, so a wrong supersession is
    visible and reversible instead of a silent overwrite."""
    old = facts_mod.add_fact(article['id'], 'Trains at Movati',
                             source_kind='journal', source_id='a')
    new = facts_mod.add_fact(article['id'], 'Trains at GoodLife',
                             source_kind='journal', source_id='b')

    assert facts_mod.supersede(old['id'], new) is True

    live = [f['statement'] for f in facts_mod.live_facts(article['id'])]
    assert live == ['Trains at GoodLife']
    assert len(facts_mod.all_facts(article['id'])) == 2
    assert facts_mod.get_fact(old['id'])['supersededBy'] == new['id']


def test_a_fact_can_retire_with_nothing_replacing_it(article):
    old = facts_mod.add_fact(article['id'], 'Training for a race',
                             source_kind='journal', source_id='a')
    assert facts_mod.supersede(old['id'], None) is True
    assert facts_mod.live_facts(article['id']) == []


def test_a_locked_fact_is_never_superseded(article):
    """The user corrected it by hand. A pass overruling that would make the
    correction pointless — the 'frozen components' mitigation."""
    fact = facts_mod.add_fact(article['id'], 'Their gym is GoodLife',
                              source_kind='journal', source_id='a')
    facts_mod.set_locked(fact['id'], True)
    replacement = facts_mod.add_fact(article['id'], 'Their gym is Movati',
                                     source_kind='journal', source_id='b')

    assert facts_mod.supersede(fact['id'], replacement) is False
    assert len(facts_mod.live_facts(article['id'])) == 2


def test_clearing_derived_facts_keeps_the_locked_ones(article):
    """A rebuild corrects the machine's drift, not the user's corrections to
    it."""
    kept = facts_mod.add_fact(article['id'], 'Their gym is GoodLife',
                              source_kind='journal', source_id='a')
    facts_mod.set_locked(kept['id'], True)
    facts_mod.add_fact(article['id'], 'Runs on Sundays',
                       source_kind='journal', source_id='b')

    assert facts_mod.clear_derived(article['id']) == 1
    assert [f['statement'] for f in facts_mod.live_facts(article['id'])] == [
        'Their gym is GoodLife'
    ]


def test_a_statement_longer_than_a_sentence_is_clipped(article):
    fact = facts_mod.add_fact(article['id'], 'x' * 400,
                              source_kind='journal', source_id='a')
    assert len(fact['statement']) == facts_mod.MAX_STATEMENT_CHARS


def test_formatting_marks_the_locked_ones_for_the_model(article):
    fact = facts_mod.add_fact(article['id'], 'Their gym is GoodLife',
                              source_kind='journal', source_id='a')
    facts_mod.set_locked(fact['id'], True)
    text = facts_mod.format_facts(facts_mod.live_facts(article['id']), with_ids=True)
    assert fact['id'] in text
    assert 'do not contradict' in text
