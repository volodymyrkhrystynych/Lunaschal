import { describe, it, expect } from 'vitest';
import { shouldPollConversation } from './chatPolling';
import type { ChatAttachment, Message } from '../hooks/api';

const clip = (over: Partial<ChatAttachment> = {}): ChatAttachment => ({
  id: 'a1',
  conversationId: 'c1',
  messageId: 'm1',
  mime: 'audio/webm',
  url: '/api/chat/attachments/a1/file',
  kind: 'audio',
  description: null,
  descriptionStatus: null,
  descriptionError: null,
  transcript: null,
  transcriptStatus: 'running',
  transcriptError: null,
  latitude: null,
  longitude: null,
  position: 0,
  createdAt: '2026-01-01T08:00:00.000Z',
  ...over,
});

const message = (over: Partial<Message> = {}): Message => ({
  id: 'm1',
  conversationId: 'c1',
  role: 'user',
  content: 'hello',
  metadata: null,
  status: 'done',
  createdAt: '2026-01-01T08:00:00.000Z',
  ...over,
});

describe('shouldPollConversation', () => {
  it('does not poll a settled conversation', () => {
    expect(shouldPollConversation([message()])).toBe(false);
  });

  it('does not poll when there is nothing yet', () => {
    expect(shouldPollConversation(undefined)).toBe(false);
    expect(shouldPollConversation([])).toBe(false);
  });

  it('polls while a reply is being generated', () => {
    expect(
      shouldPollConversation([
        message(),
        message({ id: 'm2', role: 'assistant', status: 'streaming' }),
      ])
    ).toBe(true);
  });

  it('polls while a voice message is still being transcribed', () => {
    // This is the window the old rule missed entirely: the clip has landed, no
    // reply exists yet, and the transcript is what the user is waiting on. A
    // poll keyed only on 'streaming' stopped exactly here.
    expect(
      shouldPollConversation([message({ content: '', attachments: [clip()] })])
    ).toBe(true);
  });

  it('stops once the transcript has landed', () => {
    expect(
      shouldPollConversation([
        message({
          content: 'what I said',
          attachments: [clip({ transcriptStatus: 'done', transcript: 'x' })],
        }),
      ])
    ).toBe(false);
  });

  it('stops when a transcription failed rather than polling forever', () => {
    expect(
      shouldPollConversation([
        message({ attachments: [clip({ transcriptStatus: 'error' })] }),
      ])
    ).toBe(false);
  });

  it('notices a clip on an older message, not only the newest', () => {
    // Several clips can be in flight, and the newest message is not necessarily
    // the one still working.
    expect(
      shouldPollConversation([
        message({ attachments: [clip()] }),
        message({ id: 'm2', content: 'typed after' }),
      ])
    ).toBe(true);
  });

  it('ignores an old reply stuck mid-stream', () => {
    // A 'streaming' row that is not the newest message is a dead run from a
    // killed process. Polling on its behalf would never end.
    expect(
      shouldPollConversation([
        message({ role: 'assistant', status: 'streaming' }),
        message({ id: 'm2', content: 'asked again' }),
      ])
    ).toBe(false);
  });

  it('ignores a photo that is still being read', () => {
    // Photo descriptions have their own polling in the composer, and a picture
    // being read does not change anything in the transcript above.
    expect(
      shouldPollConversation([
        message({
          attachments: [
            clip({
              kind: 'image',
              transcriptStatus: null,
              descriptionStatus: 'running',
            }),
          ],
        }),
      ])
    ).toBe(false);
  });
});
