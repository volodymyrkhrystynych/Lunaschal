import { describe, it, expect } from 'vitest';
import {
  formatDuration,
  formatSize,
  noteSlugFor,
  sourceSubtitle,
  viewerKindFor,
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
