// @vitest-environment jsdom
/**
 * How a spoken message looks while, and after, the server works out what was
 * said.
 *
 * The clip is the durable half of a dictated message — it exists before the
 * words do and stays after them — so the three states it can be in each have to
 * say something true about where the recording is.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ChatClips, clipAttachments, photoAttachments } from './ChatClips';
import type { ChatAttachment } from '../../hooks/api';

const attachment = (over: Partial<ChatAttachment> = {}): ChatAttachment => ({
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
  transcriptStatus: 'done',
  transcriptError: null,
  latitude: null,
  longitude: null,
  position: 0,
  createdAt: '2026-01-01T08:00:00.000Z',
  ...over,
});

const audioEl = (container: HTMLElement) =>
  container.querySelector('audio') as HTMLAudioElement | null;

describe('ChatClips', () => {
  it('renders nothing when the message was typed', () => {
    const { container } = render(<ChatClips clips={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('plays the recording', () => {
    const { container } = render(<ChatClips clips={[attachment()]} />);
    expect(audioEl(container)?.getAttribute('src')).toBe(
      '/api/chat/attachments/a1/file'
    );
    // `preload="none"` matters on a phone: a day of chat should not fetch every
    // clip in it just to draw the scrollback.
    expect(audioEl(container)?.getAttribute('preload')).toBe('none');
  });

  it('says so while the words are still being worked out', () => {
    render(<ChatClips clips={[attachment({ transcriptStatus: 'running' })]} />);
    expect(screen.getByText(/transcribing/i)).toBeTruthy();
  });

  it('says the recording is safe when transcription failed', () => {
    // The distinction the message has to carry: what failed is the words, not
    // the recording — which is playable on the line directly above.
    const { container } = render(
      <ChatClips
        clips={[
          attachment({
            transcriptStatus: 'error',
            transcriptError: 'No speech found in the recording',
          }),
        ]}
      />
    );
    expect(screen.getByText(/couldn't transcribe this/i)).toBeTruthy();
    expect(screen.getByText(/no speech found/i)).toBeTruthy();
    expect(screen.getByText(/the recording is saved/i)).toBeTruthy();
    expect(audioEl(container)).not.toBeNull();
  });

  it('does not repeat the transcript under the clip', () => {
    // It is the message itself, rendered a line below in the bubble. Printing it
    // here too would be the same words twice in the same box.
    render(
      <ChatClips
        clips={[attachment({ transcript: 'had vareniki at Movati' })]}
      />
    );
    expect(screen.queryByText(/vareniki/i)).toBeNull();
  });

  it('shows every clip on a message', () => {
    const { container } = render(
      <ChatClips
        clips={[attachment(), attachment({ id: 'a2', position: 1 })]}
      />
    );
    expect(container.querySelectorAll('audio')).toHaveLength(2);
  });
});

describe('splitting a message’s attachments', () => {
  const photo = attachment({ id: 'p1', kind: 'image', mime: 'image/jpeg' });
  const clip = attachment({ id: 'c1' });

  it('keeps clips out of the photo strip and photos out of the player', () => {
    expect(photoAttachments([photo, clip]).map(a => a.id)).toEqual(['p1']);
    expect(clipAttachments([photo, clip]).map(a => a.id)).toEqual(['c1']);
  });

  it('treats a message with no attachments as having neither', () => {
    expect(photoAttachments(undefined)).toEqual([]);
    expect(clipAttachments(undefined)).toEqual([]);
  });
});
