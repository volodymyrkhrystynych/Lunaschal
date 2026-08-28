import { describe, it, expect } from 'vitest';
import {
  changedFields,
  isDirty,
  mergeServerIdea,
  saveLabel,
  saveState,
  serverDraft,
} from './ideaSave';

const draft = (title: string, body: string) => ({ title, body });

describe('isDirty', () => {
  it('is false when the draft matches what the server has', () => {
    expect(isDirty(draft('a', 'b'), draft('a', 'b'))).toBe(false);
  });

  it('notices either field', () => {
    expect(isDirty(draft('a2', 'b'), draft('a', 'b'))).toBe(true);
    expect(isDirty(draft('a', 'b2'), draft('a', 'b'))).toBe(true);
  });
});

describe('saveState', () => {
  it('is saved when there is nothing to send', () => {
    expect(saveState({ dirty: false, inFlight: false, failed: false })).toBe(
      'saved'
    );
  });

  it('is dirty — local only — while the debounce runs', () => {
    expect(saveState({ dirty: true, inFlight: false, failed: false })).toBe(
      'dirty'
    );
  });

  it('only says "saving" while a request is genuinely in flight', () => {
    // The whole bug: the pane sat on "Saving…" with nothing being sent.
    expect(saveState({ dirty: true, inFlight: true, failed: false })).toBe(
      'saving'
    );
    expect(saveState({ dirty: false, inFlight: false, failed: false })).toBe(
      'saved'
    );
  });

  it('reports a failure only while there is still something unsaved', () => {
    expect(saveState({ dirty: true, inFlight: false, failed: true })).toBe(
      'error'
    );
    // A later save succeeded and cleared the draft; the old failure is history.
    expect(saveState({ dirty: false, inFlight: false, failed: true })).toBe(
      'saved'
    );
  });

  it('labels each state as where the text currently lives', () => {
    expect(saveLabel('saved')).toBe('Saved');
    expect(saveLabel('dirty')).toBe('Unsaved');
    expect(saveLabel('saving')).toBe('Saving…');
    expect(saveLabel('error')).toBe('Save failed');
  });
});

describe('changedFields', () => {
  it('sends only what actually changed', () => {
    expect(changedFields(draft('a', 'b2'), draft('a', 'b'))).toEqual({
      content: 'b2',
    });
    expect(changedFields(draft('a2', 'b'), draft('a', 'b'))).toEqual({
      title: 'a2',
    });
    expect(changedFields(draft('a2', 'b2'), draft('a', 'b'))).toEqual({
      title: 'a2',
      content: 'b2',
    });
  });

  it('sends nothing when nothing changed', () => {
    expect(changedFields(draft('a', 'b'), draft('a', 'b'))).toEqual({});
  });

  it('can clear a field', () => {
    expect(changedFields(draft('', 'b'), draft('a', 'b'))).toEqual({
      title: '',
    });
  });
});

describe('serverDraft', () => {
  it('prefers the polished content', () => {
    expect(
      serverDraft({ title: 't', content: 'polished', rawContent: 'spoken' })
    ).toEqual({ title: 't', body: 'polished' });
  });

  it('falls back to the transcript while content is unset', () => {
    expect(
      serverDraft({ title: 't', content: '', rawContent: 'spoken' })
    ).toEqual({ title: 't', body: 'spoken' });
  });
});

describe('mergeServerIdea', () => {
  const server = (over = {}) => ({
    title: '',
    content: '',
    rawContent: 'a grid of habits',
    ...over,
  });

  it('adopts a title the background pass wrote, without a reload', () => {
    const result = mergeServerIdea(
      draft('', 'a grid of habits'),
      draft('', 'a grid of habits'),
      server({ title: 'Habit grid in the day view' })
    );
    expect(result.draft.title).toBe('Habit grid in the day view');
    expect(isDirty(result.draft, result.baseline)).toBe(false);
  });

  it('never overwrites a field being edited', () => {
    const result = mergeServerIdea(
      draft('My own name', 'a grid of habits'),
      draft('', 'a grid of habits'),
      server({ title: 'Named by the model' })
    );
    expect(result.draft.title).toBe('My own name');
    // Still unsaved, so the debounce will push it and win.
    expect(isDirty(result.draft, result.baseline)).toBe(true);
  });

  it('leaves an edit as the only pending change when the other field moves', () => {
    // The body is being edited while the naming pass writes the title. Only
    // the body should still be waiting to be sent — the adopted title must not
    // come back as a local change the user never made.
    const result = mergeServerIdea(
      draft('', 'my edit'),
      draft('', 'a grid of habits'),
      server({ title: 'Habit grid', content: 'A grid of habits.' })
    );
    expect(result.draft).toEqual({ title: 'Habit grid', body: 'my edit' });
    expect(result.baseline).toEqual({
      title: 'Habit grid',
      body: 'A grid of habits.',
    });
    expect(changedFields(result.draft, result.baseline)).toEqual({
      content: 'my edit',
    });
  });

  it('adopts the polished body once it lands', () => {
    const result = mergeServerIdea(
      draft('t', 'a grid of habits'),
      draft('t', 'a grid of habits'),
      server({ title: 't', content: 'A grid of habits.' })
    );
    expect(result.draft.body).toBe('A grid of habits.');
    expect(isDirty(result.draft, result.baseline)).toBe(false);
  });

  it('is a no-op when the server has not moved', () => {
    const current = draft('t', 'a grid of habits');
    const result = mergeServerIdea(current, current, server({ title: 't' }));
    expect(result.draft).toEqual(current);
    expect(isDirty(result.draft, result.baseline)).toBe(false);
  });
});
