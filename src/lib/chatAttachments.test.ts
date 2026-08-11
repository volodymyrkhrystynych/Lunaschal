import { describe, it, expect } from 'vitest';
import {
  isAttachablePhoto,
  photoStatusMessage,
  photosFromTransfer,
  rejectedPhotosMessage,
} from './chatAttachments';
import type { ChatAttachment } from '../hooks/api';

const file = (name: string, type = '') =>
  new File(['x'], name, type ? { type } : undefined);

const attachment = (over: Partial<ChatAttachment> = {}): ChatAttachment => ({
  id: 'a1',
  conversationId: 'c1',
  messageId: null,
  mime: 'image/jpeg',
  url: '/api/chat/attachments/a1/file',
  description: 'A plate.',
  descriptionStatus: 'done',
  descriptionError: null,
  latitude: null,
  longitude: null,
  position: 0,
  createdAt: '2026-01-01T08:00:00.000Z',
  ...over,
});

describe('isAttachablePhoto', () => {
  it('accepts anything the browser calls an image', () => {
    expect(isAttachablePhoto(file('meal.jpg', 'image/jpeg'))).toBe(true);
    expect(isAttachablePhoto(file('IMG_1.HEIC', 'image/heic'))).toBe(true);
  });

  it('falls back to the extension when iOS gives no type', () => {
    expect(isAttachablePhoto(file('IMG_0042.HEIC'))).toBe(true);
  });

  it('refuses a video', () => {
    // Not merely unsupported: a video attached here would produce a photo the
    // vision model can never read, which is worse than refusing it.
    expect(isAttachablePhoto(file('clip.mp4', 'video/mp4'))).toBe(false);
  });

  it('refuses a document', () => {
    expect(isAttachablePhoto(file('notes.pdf', 'application/pdf'))).toBe(false);
  });
});

describe('photosFromTransfer', () => {
  it('reads .items when .files is empty', () => {
    const photo = file('meal.jpg', 'image/jpeg');
    const { accepted } = photosFromTransfer({
      files: [] as unknown as FileList,
      items: [
        { kind: 'string', getAsFile: () => null },
        { kind: 'file', getAsFile: () => photo },
      ] as unknown as DataTransferItemList,
    });
    expect(accepted).toEqual([photo]);
  });

  it('separates what it will not attach rather than dropping it', () => {
    const { accepted, rejected } = photosFromTransfer({
      files: [
        file('meal.jpg', 'image/jpeg'),
        file('clip.mp4', 'video/mp4'),
      ] as unknown as FileList,
    });
    expect(accepted).toHaveLength(1);
    expect(rejected).toHaveLength(1);
  });

  it('survives an empty clipboard', () => {
    expect(photosFromTransfer(null)).toEqual({ accepted: [], rejected: [] });
    expect(photosFromTransfer(undefined)).toEqual({
      accepted: [],
      rejected: [],
    });
  });
});

describe('rejectedPhotosMessage', () => {
  it('is null when nothing was refused', () => {
    expect(rejectedPhotosMessage([])).toBeNull();
  });

  it('names what it refused', () => {
    expect(rejectedPhotosMessage([file('clip.mp4', 'video/mp4')])).toContain(
      'clip.mp4'
    );
  });
});

describe('photoStatusMessage', () => {
  it('says nothing when there are no photos', () => {
    expect(photoStatusMessage([], true)).toBeNull();
  });

  it('says nothing when every photo has been read', () => {
    expect(photoStatusMessage([attachment()], true)).toBeNull();
  });

  it('reports reading in progress, counted', () => {
    const running = attachment({ descriptionStatus: 'running' });
    expect(photoStatusMessage([running], true)).toMatch(/Reading the photo/);
    expect(photoStatusMessage([running, running], true)).toMatch(
      /Reading 2 photos/
    );
  });

  it('says a photo could not be read', () => {
    expect(
      photoStatusMessage([attachment({ descriptionStatus: 'error' })], true)
    ).toMatch(/couldn't be read/);
  });

  it('names the missing model rather than spinning forever', () => {
    // With neither reader configured nothing will ever resolve, and a spinner
    // that never finishes is exactly the failure this replaces.
    const message = photoStatusMessage(
      [attachment({ descriptionStatus: 'running' })],
      false
    );
    expect(message).toMatch(/nothing is set up to read them/);
    expect(message).toMatch(/Settings/);
  });

  it('stays quiet when the chat model reads photos itself', () => {
    // There is no pre-read phase on that path — the picture goes into the turn
    // — so there is nothing to report and a status line would be noise.
    expect(
      photoStatusMessage([attachment({ descriptionStatus: null })], false, true)
    ).toBeNull();
    expect(
      photoStatusMessage(
        [attachment({ descriptionStatus: 'running' })],
        true,
        true
      )
    ).toBeNull();
  });
});
