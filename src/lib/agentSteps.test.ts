import { describe, it, expect } from 'vitest';
import { stepLabel, parseAgentMeta, countChatTodoWrites } from './agentSteps';

describe('stepLabel', () => {
  it('describes a successful search with its result count', () => {
    expect(
      stepLabel({ tool: 'web_search', arg: 'fsrs', ok: true, count: 3 })
    ).toBe('Searched the web for "fsrs" — 3 results');
  });

  it('describes a search with zero results', () => {
    expect(
      stepLabel({ tool: 'web_search', arg: 'fsrs', ok: true, count: 0 })
    ).toBe('Searched the web for "fsrs" — 0 results');
  });

  it('describes an unavailable search', () => {
    expect(
      stepLabel({
        tool: 'web_search',
        arg: 'fsrs',
        ok: false,
        error: 'no provider',
      })
    ).toBe('Web search unavailable: no provider');
  });

  it('describes an unavailable search without an error message', () => {
    expect(stepLabel({ tool: 'web_search', arg: 'fsrs', ok: false })).toBe(
      'Web search unavailable'
    );
  });

  it('describes a successful fetch, preferring the page title over the url', () => {
    expect(
      stepLabel({
        tool: 'web_fetch',
        arg: 'https://ex.com',
        ok: true,
        title: 'Example',
      })
    ).toBe('Read Example');
  });

  it('falls back to the url when a fetched page has no title', () => {
    expect(
      stepLabel({ tool: 'web_fetch', arg: 'https://ex.com', ok: true })
    ).toBe('Read https://ex.com');
  });

  it('describes a failed fetch', () => {
    expect(
      stepLabel({ tool: 'web_fetch', arg: 'https://ex.com', ok: false })
    ).toBe('Could not read https://ex.com');
  });

  it('describes the wiki tools the Ideas agent uses', () => {
    // Folded in from IdeaDiscussion's own copy: one labeller, two callers.
    expect(stepLabel({ tool: 'wiki_list', count: 12 })).toBe(
      'Checked the research wiki (12 articles)'
    );
    expect(stepLabel({ tool: 'wiki_search', arg: 'fsrs' })).toBe(
      'Searched the wiki for "fsrs"'
    );
    expect(stepLabel({ tool: 'wiki_read', arg: 'FSRS' })).toBe(
      'Read wiki note: FSRS'
    );
  });

  it('says a proposal was staged, never that it was saved', () => {
    // Nothing is written until the user clicks the confirm card, so a step
    // list claiming otherwise is a lie they only catch by going to look.
    const label = stepLabel({
      tool: 'propose_calendar_event',
      arg: 'event "Dentist"',
      ok: true,
    });
    expect(label).toBe('Staged a calendar event: event "Dentist"');
    expect(label).not.toMatch(/saved/i);
  });

  it('says to-dos were added, never staged, since add_todos writes immediately', () => {
    // add_todos has no `proposal` key on its event — same as
    // create_note_to_self — so it must not read like the propose_ tools it
    // sits alongside.
    const label = stepLabel({
      tool: 'add_todos',
      arg: 'Call the dentist; Buy milk',
      ok: true,
    });
    expect(label).toBe("Added to today's to-dos: Call the dentist; Buy milk");
    expect(label).not.toMatch(/staged/i);
  });

  it('says when add_todos added nothing, and why', () => {
    expect(
      stepLabel({
        tool: 'add_todos',
        ok: false,
        error: 'nothing new to add — already on the list',
      })
    ).toBe("Didn't add that — nothing new to add — already on the list");
  });

  it('names every proposal kind rather than falling through to the raw tool name', () => {
    expect(stepLabel({ tool: 'propose_calendar_event', ok: true })).toBe(
      'Staged a calendar event'
    );
    expect(stepLabel({ tool: 'propose_calorie_log', ok: true })).toBe(
      'Staged a calorie entry'
    );
    expect(stepLabel({ tool: 'draft_flashcard', ok: true })).toBe(
      'Staged a flashcard draft'
    );
    // Plural: the article lives in the label map precisely so this one
    // doesn't read as "a flashcards".
    expect(stepLabel({ tool: 'propose_flashcards', ok: true })).toBe(
      'Staged flashcards'
    );
  });

  it('reports a refused proposal with its reason', () => {
    expect(
      stepLabel({
        tool: 'propose_calorie_log',
        ok: false,
        error: 'calories must be a whole number the user actually gave',
      })
    ).toBe(
      'Could not stage a calorie entry — calories must be a whole number the user actually gave'
    );
  });

  it('reports a clarifying question without implying a card is waiting', () => {
    // ask_user stages nothing — that is the whole point of it — so it must not
    // read like the propose_ tools it sits alongside.
    const label = stepLabel({
      tool: 'ask_user',
      arg: 'the flights to-do',
      ok: true,
    });
    expect(label).toBe('Asked for clarification about the flights to-do');
    expect(label).not.toMatch(/staged/i);
  });

  it('labels a clarifying question with no subject', () => {
    expect(stepLabel({ tool: 'ask_user', ok: true })).toBe(
      'Asked for clarification'
    );
  });

  it('labels a staged food entry', () => {
    expect(
      stepLabel({
        tool: 'propose_food_log',
        arg: 'food log for "Vareniki"',
        ok: true,
      })
    ).toBe('Staged a food entry: food log for "Vareniki"');
  });

  it('says a memory was written, not staged', () => {
    // `remember` writes immediately with no confirm card, so "Staged" would be
    // the same lie in the other direction — the user needs to know something
    // was written in order to go and unwrite it.
    const label = stepLabel({
      tool: 'remember',
      arg: 'Their gym is Movati',
      ok: true,
    });
    expect(label).toBe('Remembered: Their gym is Movati');
    expect(label).not.toMatch(/staged/i);
  });

  it('does not claim a new memory when the fact was already noted', () => {
    // The write was a no-op: the assistant sees only the most recent slice of
    // its own notes, so re-stating one it already has is normal.
    expect(
      stepLabel({
        tool: 'remember',
        arg: 'Their gym is Movati',
        ok: true,
        duplicate: true,
      })
    ).toBe('Already remembered: Their gym is Movati');
  });

  it('labels the recall tools as reading, never as acting', () => {
    // Nothing is written by any of these, so none of them may read like a
    // to-do appearing or a card being staged.
    expect(
      stepLabel({
        tool: 'search_conversations',
        arg: 'the dripping tap',
        ok: true,
        count: 3,
      })
    ).toBe('Searched past chats for "the dripping tap" — 3 found');
    expect(
      stepLabel({
        tool: 'search_journal',
        arg: 'carburettors',
        ok: true,
        count: 0,
      })
    ).toBe('Searched the journal for "carburettors" — nothing found');
    expect(stepLabel({ tool: 'read_day', arg: '2026-03-04', ok: true })).toBe(
      'Looked up 2026-03-04'
    );
  });

  it('says when a recall tool could not run', () => {
    expect(
      stepLabel({ tool: 'read_day', ok: false, error: 'not a date' })
    ).toBe("Couldn't look up that day — not a date");
  });

  it('says when a memory could not be written, and why', () => {
    expect(
      stepLabel({ tool: 'remember', ok: false, error: 'the memory is full' })
    ).toBe("Didn't remember that — the memory is full");
  });

  it('labels a queued memory revision as in progress', () => {
    // The rewrite runs in the background, so this is deliberately not past tense.
    expect(
      stepLabel({ tool: 'revise_memory', arg: 'they switched gyms', ok: true })
    ).toBe("Updating what's remembered: they switched gyms");
  });

  it('says a note to self was written, not staged', () => {
    const label = stepLabel({
      tool: 'create_note_to_self',
      arg: 'buy a birthday card',
      ok: true,
    });
    expect(label).toBe('Noted: buy a birthday card');
    expect(label).not.toMatch(/staged/i);
  });

  it('says when a note to self could not be saved, and why', () => {
    expect(
      stepLabel({
        tool: 'create_note_to_self',
        ok: false,
        error: 'there is nothing to note yet',
      })
    ).toBe("Didn't save that note — there is nothing to note yet");
  });

  it('falls back to a generic label for an unknown tool', () => {
    expect(stepLabel({ tool: 'mystery_tool' })).toBe('Ran mystery_tool');
  });

  it('falls back to "Thinking" when there is no tool at all', () => {
    expect(stepLabel({})).toBe('Thinking');
  });
});

describe('stepLabel for deep research', () => {
  it('names the query and its source count on a completed pass', () => {
    expect(
      stepLabel({ tool: 'deep_research', arg: 'fsrs', ok: true, count: 6 })
    ).toBe('Deep-researched "fsrs" — 6 sources');
  });

  it('singularizes a lone source', () => {
    expect(
      stepLabel({ tool: 'deep_research', arg: 'fsrs', ok: true, count: 1 })
    ).toBe('Deep-researched "fsrs" — 1 source');
  });

  it('says a timed-out pass still answered, because it did', () => {
    // `ok` stays true on the salvage path — the pass stopped searching and
    // wrote up what it had, so this must not read as "no answer came back".
    expect(
      stepLabel({
        tool: 'deep_research',
        arg: 'fsrs',
        ok: true,
        count: 4,
        timedOut: true,
      })
    ).toBe('Deep research timed out — answered from 4 sources so far');
  });

  it('still reports a genuine failure as a failure', () => {
    expect(
      stepLabel({
        tool: 'deep_research',
        arg: 'fsrs',
        ok: false,
        error: 'no answer was produced',
      })
    ).toBe('Deep research failed: no answer was produced');
  });
});

describe('parseAgentMeta', () => {
  const empty = {
    steps: [],
    sources: [],
    thinking: '',
    truncated: false,
    timedOut: false,
  };

  it('returns empty arrays for null/undefined metadata', () => {
    expect(parseAgentMeta(null)).toEqual(empty);
    expect(parseAgentMeta(undefined)).toEqual(empty);
    expect(parseAgentMeta('')).toEqual(empty);
  });

  it('returns empty arrays for malformed JSON rather than throwing', () => {
    expect(parseAgentMeta('not json')).toEqual(empty);
  });

  it('returns empty arrays for metadata with no steps/sources (e.g. a break marker)', () => {
    expect(parseAgentMeta(JSON.stringify({ break: true }))).toEqual(empty);
  });

  it('parses steps and sources from a websearch assistant message', () => {
    const meta = JSON.stringify({
      steps: [{ tool: 'web_search', ok: true, count: 1 }],
      sources: [{ url: 'https://ex.com', title: 'Example' }],
    });
    expect(parseAgentMeta(meta)).toEqual({
      steps: [{ tool: 'web_search', ok: true, count: 1 }],
      sources: [{ url: 'https://ex.com', title: 'Example' }],
      thinking: '',
      truncated: false,
      timedOut: false,
    });
  });

  it('parses the persisted reasoning of a delegate reply', () => {
    const meta = JSON.stringify({
      agent: 'delegate',
      steps: [],
      sources: [],
      thinking: 'weighing it up',
    });
    expect(parseAgentMeta(meta).thinking).toBe('weighing it up');
  });

  // Every message written before reasoning was persisted has no `thinking` at
  // all, and a non-string there must not reach the renderer as one.
  it('treats a missing or non-string thinking field as no reasoning', () => {
    expect(parseAgentMeta(JSON.stringify({ steps: [] })).thinking).toBe('');
    expect(parseAgentMeta(JSON.stringify({ thinking: 42 })).thinking).toBe('');
  });

  it('parses the flag that says the reply was cut off at the ceiling', () => {
    expect(parseAgentMeta(JSON.stringify({ truncated: true })).truncated).toBe(
      true
    );
  });
});

describe('countChatTodoWrites', () => {
  it('counts the add_todos calls that actually wrote rows', () => {
    expect(
      countChatTodoWrites([
        { tool: 'web_search', ok: true },
        { tool: 'add_todos', ok: true, arg: 'Water the plants' },
        { tool: 'add_todos', ok: true, arg: 'Call the dentist' },
      ])
    ).toBe(2);
  });

  // A refused call wrote nothing — counting it would refetch the bar for a
  // to-do that does not exist, and worse, make a later real write look like
  // no change at all.
  it('ignores a refused add_todos and every other tool', () => {
    expect(
      countChatTodoWrites([
        { tool: 'add_todos', ok: false, error: 'nothing new to add' },
        { tool: 'propose_calendar_event', ok: true },
        {},
      ])
    ).toBe(0);
  });
});
