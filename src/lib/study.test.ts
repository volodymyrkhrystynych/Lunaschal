import { describe, it, expect } from 'vitest';
import {
  formatDuration,
  formatSize,
  isSourceRowFor,
  noteSlugFor,
  sourceSubtitle,
  viewerKindFor,
  progressLabel,
  offlineReason,
  DRIVE_OFFLINE_MESSAGE,
  type StudySource,
} from './study';

function source(overrides: Partial<StudySource> = {}): StudySource {
  return {
    id: '01JB0000000000000000ABCDEF',
    title: 'Attention Is All You Need',
    kind: 'pdf',
    sourceUrl: null,
    contentType: 'application/pdf',
    sizeBytes: 2_400_000,
    durationSeconds: null,
    notePath: null,
    importStatus: 'ready',
    importError: null,
    lastOpenedAt: null,
    position: null,
    paperId: null,
    noteMode: 'note' as const,
    createdAt: '2026-09-04T10:00:00+00:00',
    updatedAt: '2026-09-04T10:00:00+00:00',
    ...overrides,
  };
}

describe('viewerKindFor', () => {
  it('picks a viewer per kind once the import has landed', () => {
    expect(viewerKindFor(source({ kind: 'pdf' }))).toBe('pdf');
    expect(viewerKindFor(source({ kind: 'youtube' }))).toBe('video');
    expect(viewerKindFor(source({ kind: 'web' }))).toBe('article');
  });

  it('reports the import state ahead of the kind', () => {
    // There is no file to render yet, so the kind is not the question.
    expect(viewerKindFor(source({ importStatus: 'importing' }))).toBe(
      'importing'
    );
    expect(viewerKindFor(source({ importStatus: 'error' }))).toBe('error');
  });

  it('reports an unreachable file ahead of the kind too', () => {
    // A video lives on the archive drive alone. Unplugged, there is nothing to
    // render, and a <video> on a 404 is a silent black box.
    expect(
      viewerKindFor(source({ kind: 'youtube', fileAvailable: false }))
    ).toBe('offline');
    // Still after the import states: a failed import never had a file at all.
    expect(
      viewerKindFor(
        source({ kind: 'youtube', fileAvailable: false, importStatus: 'error' })
      )
    ).toBe('error');
  });

  it('treats a source that says nothing about its file as present', () => {
    // The field is optional, and PDFs and articles never carry it.
    expect(viewerKindFor(source({ kind: 'youtube' }))).toBe('video');
    expect(viewerKindFor(source({ fileAvailable: true }))).toBe('pdf');
  });
});

describe('offlineReason', () => {
  it('prefers what the server said over the generic line', () => {
    expect(
      offlineReason(
        source({ fileUnavailableReason: 'The backup drive is not connected.' })
      )
    ).toBe('The backup drive is not connected.');
    expect(offlineReason(source())).toBe(DRIVE_OFFLINE_MESSAGE);
  });
});

describe('progressLabel', () => {
  it('reads the one position column through the row kind', () => {
    expect(progressLabel(source({ kind: 'pdf', position: 214 }))).toBe(
      'page 214'
    );
    expect(progressLabel(source({ kind: 'youtube', position: 2831.5 }))).toBe(
      '47 min in'
    );
  });

  it('says nothing where there is no progress to report', () => {
    // Page 1 and the first minute are where you were anyway.
    expect(progressLabel(source({ kind: 'pdf', position: 1 }))).toBeNull();
    expect(progressLabel(source({ kind: 'youtube', position: 12 }))).toBeNull();
    expect(progressLabel(source({ kind: 'pdf', position: null }))).toBeNull();
    // An archived page never stores one — its iframe is an opaque origin.
    expect(progressLabel(source({ kind: 'web', position: 900 }))).toBeNull();
  });
});

describe('sourceSubtitle', () => {
  it('shows how far in you are, between the length and the size', () => {
    expect(
      sourceSubtitle(
        source({
          kind: 'youtube',
          durationSeconds: 3771,
          position: 2831,
          sizeBytes: 0,
          notePath: null,
        })
      )
    ).toBe('Video · 1:02:51 · 47 min in');
  });
});

describe('noteSlugFor', () => {
  it('names the note after the title, under the study folder', () => {
    expect(noteSlugFor('Attention Is All You Need', 'abcdefGHIJKL')).toBe(
      'study/attention-is-all-you-need-ghijkl.md'
    );
  });

  it('keeps two same-named sources apart', () => {
    const a = noteSlugFor('Lecture 1', '000000000000AAAAAA');
    const b = noteSlugFor('Lecture 1', '000000000000BBBBBB');
    expect(a).not.toBe(b);
  });

  // The row reaching this can come from the persisted query cache, which is
  // only as well-formed as whatever was written into it -- and a poisoned entry
  // is what turned a missing title into a blank app.
  it('falls back to the id when there is no title at all', () => {
    expect(noteSlugFor(null, 'abcdefGHIJKL')).toBe('study/ghijkl.md');
    expect(noteSlugFor(undefined, 'abcdefGHIJKL')).toBe('study/ghijkl.md');
    expect(noteSlugFor('', 'abcdefGHIJKL')).toBe('study/ghijkl.md');
  });

  it('still produces a usable path for a title that slugs to nothing', () => {
    // Non-ASCII is dropped rather than transliterated, so the id carries it.
    expect(noteSlugFor('注意力就是一切', 'abcdefGHIJKL')).toBe(
      'study/ghijkl.md'
    );
    expect(noteSlugFor('   ', 'abcdefGHIJKL')).toBe('study/ghijkl.md');
  });

  it('does not leave a trailing dash where the title was truncated', () => {
    const path = noteSlugFor(`${'word '.repeat(30)}`, 'abcdefGHIJKL');
    expect(path).not.toContain('--');
    expect(path.endsWith('-ghijkl.md')).toBe(true);
  });
});

describe('formatDuration', () => {
  it('drops the hour unless there is one', () => {
    expect(formatDuration(247)).toBe('4:07');
    expect(formatDuration(3771)).toBe('1:02:51');
  });

  it('has nothing to say about a missing or zero duration', () => {
    expect(formatDuration(null)).toBeNull();
    expect(formatDuration(0)).toBeNull();
  });
});

describe('formatSize', () => {
  it('scales to the unit and keeps a decimal only where it reads', () => {
    expect(formatSize(512)).toBe('512 B');
    expect(formatSize(2_400_000)).toBe('2.3 MB');
    expect(formatSize(120_000_000)).toBe('114 MB');
    expect(formatSize(0)).toBeNull();
  });
});

describe('sourceSubtitle', () => {
  it('leads with the kind and adds only what the source knows', () => {
    expect(sourceSubtitle(source())).toBe('PDF · 2.3 MB');
    expect(
      sourceSubtitle(
        source({
          kind: 'youtube',
          durationSeconds: 3771,
          sizeBytes: 0,
          notePath: 'study/lecture.md',
        })
      )
    ).toBe('Video · 1:02:51 · study/lecture.md');
  });
});

describe('isSourceRowFor', () => {
  // The guard on every cache write the desk and its panes make: a response
  // that is not the row we asked about must not replace the row we have.
  it('accepts the row it asked about', () => {
    expect(isSourceRowFor(source({ id: 's1' }), 's1')).toBe(true);
  });

  it('rejects a row of nulls, which is what poisoned the cache', () => {
    const nulls = Object.fromEntries(Object.keys(source()).map(k => [k, null]));
    expect(isSourceRowFor(nulls, 's1')).toBe(false);
  });

  it('rejects a different source, an error body, and nothing at all', () => {
    expect(isSourceRowFor(source({ id: 's2' }), 's1')).toBe(false);
    expect(isSourceRowFor({ error: 'Not found' }, 's1')).toBe(false);
    expect(isSourceRowFor(null, 's1')).toBe(false);
    expect(isSourceRowFor(undefined, 's1')).toBe(false);
  });
});
