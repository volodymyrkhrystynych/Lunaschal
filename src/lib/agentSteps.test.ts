import { describe, it, expect } from 'vitest';
import { stepLabel, parseAgentMeta } from './agentSteps';

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
      tool: 'propose_task',
      arg: 'to-do "Call the dentist"',
      ok: true,
    });
    expect(label).toBe('Staged a to-do: to-do "Call the dentist"');
    expect(label).not.toMatch(/saved/i);
  });

  it('names every proposal kind rather than falling through to the raw tool name', () => {
    expect(stepLabel({ tool: 'propose_calendar_event', ok: true })).toBe(
      'Staged a calendar event'
    );
    expect(stepLabel({ tool: 'propose_calorie_log', ok: true })).toBe(
      'Staged a calorie entry'
    );
    expect(stepLabel({ tool: 'propose_note_to_self', ok: true })).toBe(
      'Staged a note to self'
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

  it('falls back to a generic label for an unknown tool', () => {
    expect(stepLabel({ tool: 'mystery_tool' })).toBe('Ran mystery_tool');
  });

  it('falls back to "Thinking" when there is no tool at all', () => {
    expect(stepLabel({})).toBe('Thinking');
  });
});

describe('parseAgentMeta', () => {
  it('returns empty arrays for null/undefined metadata', () => {
    expect(parseAgentMeta(null)).toEqual({ steps: [], sources: [] });
    expect(parseAgentMeta(undefined)).toEqual({ steps: [], sources: [] });
    expect(parseAgentMeta('')).toEqual({ steps: [], sources: [] });
  });

  it('returns empty arrays for malformed JSON rather than throwing', () => {
    expect(parseAgentMeta('not json')).toEqual({ steps: [], sources: [] });
  });

  it('returns empty arrays for metadata with no steps/sources (e.g. a break marker)', () => {
    expect(parseAgentMeta(JSON.stringify({ break: true }))).toEqual({
      steps: [],
      sources: [],
    });
  });

  it('parses steps and sources from a websearch assistant message', () => {
    const meta = JSON.stringify({
      steps: [{ tool: 'web_search', ok: true, count: 1 }],
      sources: [{ url: 'https://ex.com', title: 'Example' }],
    });
    expect(parseAgentMeta(meta)).toEqual({
      steps: [{ tool: 'web_search', ok: true, count: 1 }],
      sources: [{ url: 'https://ex.com', title: 'Example' }],
    });
  });
});
