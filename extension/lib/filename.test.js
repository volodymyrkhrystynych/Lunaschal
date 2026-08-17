import { describe, expect, it } from 'vitest';
import { downloadFilename } from './filename.js';

/**
 * These mirror `backend/tests/test_jobs_resume_edit.py`'s filename cases on
 * purpose. The extension names the File it injects into the page and the
 * backend names the download; if the two ever disagree, the same resume
 * reaches one employer under two names.
 */
describe('downloadFilename', () => {
  it('carries the user name', () => {
    expect(downloadFilename('Ada Lovelace', 'pdf')).toBe(
      'Ada Lovelace Resume.pdf'
    );
  });

  it('falls back when there is no name', () => {
    for (const value of ['', '   ', null, undefined]) {
      expect(downloadFilename(value, 'docx')).toBe('Resume.docx');
    }
  });

  it('cannot escape into a path', () => {
    expect(downloadFilename('../../etc/passwd', 'pdf')).toBe(
      'etc passwd Resume.pdf'
    );
  });

  it('strips control characters', () => {
    expect(downloadFilename('Ada\r\nX-Evil: 1', 'pdf')).toBe(
      'Ada X-Evil 1 Resume.pdf'
    );
  });

  it('caps a very long name', () => {
    expect(downloadFilename('A'.repeat(500), 'pdf').length).toBeLessThan(120);
  });

  it('keeps accents', () => {
    expect(downloadFilename('Zoë Müller', 'pdf')).toBe('Zoë Müller Resume.pdf');
  });

  it('collapses runs of whitespace', () => {
    expect(downloadFilename('  Ada   Lovelace  ', 'pdf')).toBe(
      'Ada Lovelace Resume.pdf'
    );
  });
});
