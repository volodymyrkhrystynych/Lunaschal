import { describe, expect, it } from 'vitest';
import { previewKind } from './filePreview';

describe('previewKind', () => {
  it.each([
    ['notes.md', 'text'],
    ['script.py', 'text'],
    ['data.json', 'text'],
    ['photo.jpg', 'image'],
    ['photo.JPEG', 'image'],
    ['icon.svg', 'image'],
    ['clip.mp4', 'video'],
    ['clip.mkv', 'video'],
    ['song.mp3', 'audio'],
    ['song.flac', 'audio'],
    ['report.pdf', 'pdf'],
    ['archive.zip', 'other'],
    ['binary.exe', 'other'],
  ] as const)('%s -> %s', (name, kind) => {
    expect(previewKind(name)).toBe(kind);
  });

  it('is case-insensitive', () => {
    expect(previewKind('PHOTO.PNG')).toBe('image');
  });

  it('treats a nested path the same as a bare filename', () => {
    expect(previewKind('a/b/c/photo.png')).toBe('image');
  });

  it('treats a filename with no extension as text, so a fresh "+ New" file stays editable', () => {
    expect(previewKind('README')).toBe('text');
  });

  it('treats a dotfile with no extension as text', () => {
    expect(previewKind('.gitignore')).toBe('text');
  });

  it('treats a trailing dot as no extension', () => {
    expect(previewKind('weird.')).toBe('text');
  });

  it('falls back to other for a real but unrecognized extension', () => {
    expect(previewKind('archive.rar')).toBe('other');
  });
});
