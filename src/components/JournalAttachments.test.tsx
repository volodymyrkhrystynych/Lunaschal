// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { JournalAttachments } from './JournalAttachments';
import { api, type JournalAttachment } from '../hooks/api';
import { storePhoto } from '../offline/photoStore';
import { enqueueJournalAttachment } from '../offline/photoQueue';

/** The (file, name) pair one attach produced, whatever route it took. */
const attached = (i = 0) => ({
  file: vi.mocked(storePhoto).mock.calls[i]?.[1],
  entryId: vi.mocked(storePhoto).mock.calls[i]?.[3],
  name: vi.mocked(enqueueJournalAttachment).mock.calls[i]?.[3],
});

vi.mock('../hooks/api', () => ({
  api: {
    journal: {
      attachments: {
        list: vi.fn(),
        upload: vi.fn(),
        rename: vi.fn(),
        delete: vi.fn(),
        rotate: vi.fn(),
        transcribe: vi.fn(),
        describeAudio: vi.fn(),
        link: vi.fn(),
        importStatus: vi.fn().mockResolvedValue({ done: true }),
      },
    },
  },
}));

// An attachment added here goes through the same durable queue the composer's
// staged files do: written to the device first, uploaded (and retried) by the
// offline mutation afterwards. The assertions below are about which files are
// taken, in what order and under what name — all of which the queue preserves.
vi.mock('../offline/photoStore', () => ({
  storePhoto: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../offline/photoQueue', () => ({
  enqueueJournalAttachment: vi.fn().mockResolvedValue(undefined),
}));

function attachment(over: Partial<JournalAttachment> = {}): JournalAttachment {
  const id = over.id ?? 'a1';
  return {
    id,
    entryId: 'e1',
    kind: 'audio',
    name: 'Walk home',
    // Derived, not hardcoded: the server builds this from the id, and a fixture
    // that pinned it to a1 made every row claim the same file.
    url: `/api/journal/attachments/${id}/file`,
    mime: 'audio/mp4',
    size: 2048,
    position: 0,
    transcript: null,
    transcriptStatus: 'idle',
    transcriptError: null,
    description: null,
    descriptionStatus: 'idle',
    descriptionError: null,
    latitude: null,
    longitude: null,
    createdAt: '2026-07-30T12:00:00Z',
    ...over,
  };
}

function renderIt(
  attachments: JournalAttachment[] | undefined,
  editable = true
) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <JournalAttachments
        entryId="e1"
        attachments={attachments}
        editable={editable}
      />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

describe('JournalAttachments', () => {
  it('summarizes the attachment list', () => {
    renderIt([attachment(), attachment({ id: 'a2', kind: 'image' })]);
    expect(screen.getByText(/1 recording, 1 photo/)).toBeTruthy();
  });

  it('stays out of the way when there is nothing to show and nothing to add', () => {
    const { container } = renderIt([], false);
    expect(container.innerHTML).toBe('');
  });

  it('offers add buttons only while editing', () => {
    const { unmount } = renderIt([attachment()], true);
    expect(screen.getByText(/Photo$/)).toBeTruthy();
    expect(screen.getByText(/File$/)).toBeTruthy();
    expect(screen.getByText('Paste')).toBeTruthy();
    unmount();

    renderIt([attachment()], false);
    expect(screen.queryByText(/Photo$/)).toBeNull();
    expect(screen.queryByText(/File$/)).toBeNull();
    expect(screen.queryByText('Paste')).toBeNull();
  });

  it('rotates an image clockwise and refreshes its displayed file', async () => {
    vi.mocked(api.journal.attachments.rotate).mockResolvedValue(
      attachment({ kind: 'image' })
    );
    renderIt([attachment({ kind: 'image' })], true);

    fireEvent.click(
      screen.getByRole('button', { name: 'Rotate image 90 degrees clockwise' })
    );

    await waitFor(() =>
      expect(api.journal.attachments.rotate).toHaveBeenCalledWith('a1')
    );
    await waitFor(() =>
      expect(screen.getByRole('img').getAttribute('src')).toContain(
        'rotation=1'
      )
    );
  });

  it('only offers image rotation while editing', () => {
    renderIt([attachment({ kind: 'image' })], false);

    expect(
      screen.queryByRole('button', {
        name: 'Rotate image 90 degrees clockwise',
      })
    ).toBeNull();
  });

  it('takes a picked file, naming it after the filename', async () => {
    const { container } = renderIt([]);

    const input = container.querySelector<HTMLInputElement>(
      '[data-testid="journal-file-input"]'
    )!;
    const file = new File(['x'], 'voice-memo-004.m4a', { type: 'audio/mp4' });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(storePhoto).toHaveBeenCalled());
    expect(attached()).toMatchObject({
      file,
      entryId: 'e1',
      name: 'voice-memo-004',
    });
    // Never POSTed straight from here: a file added on a bad connection has to
    // outlive the request, exactly as the composer's staged ones do.
    expect(api.journal.attachments.upload).not.toHaveBeenCalled();
  });

  it('renders a player matched to each kind', () => {
    const { container } = renderIt([
      attachment(),
      attachment({ id: 'a2', kind: 'image', name: 'The sink' }),
      attachment({ id: 'a3', kind: 'video', name: 'Talking it through' }),
    ]);
    expect(container.querySelector('audio')?.getAttribute('src')).toBe(
      '/api/journal/attachments/a1/file'
    );
    expect(container.querySelector('img')?.getAttribute('alt')).toBe(
      'The sink'
    );
    // A video in an <audio> element would silently lose the picture.
    expect(container.querySelector('video')?.getAttribute('src')).toBe(
      '/api/journal/attachments/a3/file'
    );
  });

  it('shows a map link for a located image, and opens it full-screen on click', () => {
    renderIt([
      attachment({
        kind: 'image',
        name: 'The sink',
        latitude: 43.653056,
        longitude: -79.383056,
      }),
    ]);

    const map = screen.getByRole('link', { name: /map/ });
    expect(map.getAttribute('href')).toContain('43.653056');
    expect(map.getAttribute('href')).toContain('-79.383056');

    // The thumbnail is a button, not the full-size image; clicking it opens
    // the full-screen lightbox (its own close button appears only then).
    expect(screen.queryByRole('button', { name: '✕' })).toBeNull();
    fireEvent.click(screen.getByTitle('View'));
    expect(screen.getByRole('button', { name: '✕' })).toBeTruthy();
  });

  it('shows no map link for an image with no GPS EXIF', () => {
    renderIt([attachment({ kind: 'image', name: 'The sink' })]);
    expect(screen.queryByRole('link', { name: /map/ })).toBeNull();
  });

  it('shows a new image description once, then minimizes it on later loads', () => {
    const picture = attachment({
      kind: 'image',
      transcript: 'A long description of the kitchen and everything in it.',
      transcriptStatus: 'done',
    });

    const first = renderIt([picture], false);
    const firstDescription = screen
      .getByText('Image description')
      .closest('details');
    expect(firstDescription?.open).toBe(true);
    expect(
      localStorage.getItem('journal-attachment-description-seen:a1:image')
    ).toBe('true');
    first.unmount();

    renderIt([picture], false);
    const laterDescription = screen
      .getByText('Image description')
      .closest('details');
    expect(laterDescription?.open).toBe(false);

    fireEvent.click(screen.getByText('Image description'));
    expect(laterDescription?.open).toBe(true);
  });

  it('shows a new recording description once, then minimizes it on later loads', () => {
    const recording = attachment({
      description: 'Footsteps, traffic, and a dog barking in the distance.',
      descriptionStatus: 'done',
    });

    const first = renderIt([recording], false);
    const firstDescription = screen
      .getByText('Recording description')
      .closest('details');
    expect(firstDescription?.open).toBe(true);
    expect(
      localStorage.getItem('journal-attachment-description-seen:a1:recording')
    ).toBe('true');
    first.unmount();

    renderIt([recording], false);
    const laterDescription = screen
      .getByText('Recording description')
      .closest('details');
    expect(laterDescription?.open).toBe(false);

    fireEvent.click(screen.getByText('Recording description'));
    expect(laterDescription?.open).toBe(true);
  });

  it('shows a new transcript once, then minimizes it on later loads', () => {
    // The asymmetry this closes: image captions and recording descriptions were
    // already collapsible, while the speech transcript — reliably the longest
    // of the three — rendered fully expanded above the clip forever.
    const recording = attachment({
      transcript: 'Went for a walk this morning and thought about the deploy.',
      transcriptStatus: 'done',
    });

    const first = renderIt([recording], false);
    const firstTranscript = screen.getByText('Transcript').closest('details');
    expect(firstTranscript?.open).toBe(true);
    expect(
      localStorage.getItem('journal-attachment-description-seen:a1:transcript')
    ).toBe('true');
    first.unmount();

    renderIt([recording], false);
    const laterTranscript = screen.getByText('Transcript').closest('details');
    expect(laterTranscript?.open).toBe(false);

    fireEvent.click(screen.getByText('Transcript'));
    expect(laterTranscript?.open).toBe(true);
  });

  it('remembers the transcript and the description separately', () => {
    // One clip carries both — what was said, and what else was audible. A
    // shared key would make opening either close the other on the next load.
    const recording = attachment({
      transcript: 'The words.',
      transcriptStatus: 'done',
      description: 'A dog barking.',
      descriptionStatus: 'done',
    });

    const first = renderIt([recording], false);
    fireEvent.click(screen.getByText('Transcript'));
    first.unmount();

    renderIt([recording], false);
    expect(screen.getByText('Transcript').closest('details')?.open).toBe(false);
    expect(
      screen.getByText('Recording description').closest('details')?.open
    ).toBe(false);
    expect(
      localStorage.getItem('journal-attachment-description-seen:a1:recording')
    ).toBe('true');
  });

  describe('picture sharing', () => {
    afterEach(() => {
      vi.unstubAllGlobals();
      delete (window as Window & { pywebview?: unknown }).pywebview;
    });

    it('shares the picture as a file through the native share sheet', async () => {
      const share = vi.fn().mockResolvedValue(undefined);
      const canShare = vi.fn().mockReturnValue(true);
      vi.stubGlobal('navigator', { ...navigator, share, canShare });
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue({
          ok: true,
          blob: async () => new Blob(['picture'], { type: 'image/jpeg' }),
        })
      );
      renderIt([attachment({ kind: 'image', name: 'The sink' })], false);

      fireEvent.click(screen.getByRole('button', { name: 'Share' }));

      await waitFor(() => expect(share).toHaveBeenCalledTimes(1));
      const shared = share.mock.calls[0][0];
      expect(shared.title).toBe('The sink');
      expect(shared.files[0]).toBeInstanceOf(File);
      expect(shared.files[0].name).toBe('The sink.jpg');
      expect(shared.files[0].type).toBe('image/jpeg');
      expect(canShare).toHaveBeenCalledWith(shared);
    });

    it('copies the picture through the desktop bridge without Web Share', async () => {
      const copyImage = vi.fn().mockResolvedValue({ ok: true });
      vi.stubGlobal('navigator', { ...navigator });
      Object.defineProperty(window, 'pywebview', {
        configurable: true,
        value: { api: { copy_image: copyImage } },
      });
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue({
          ok: true,
          blob: async () => new Blob(['picture'], { type: 'image/png' }),
        })
      );
      renderIt([attachment({ kind: 'image' })], false);

      fireEvent.click(screen.getByRole('button', { name: 'Share' }));

      expect(
        await screen.findByText(/Picture copied to clipboard/)
      ).toBeTruthy();
      expect(copyImage).toHaveBeenCalledWith(
        expect.stringMatching(/^data:image\/png;base64,/)
      );
    });

    it('explains when neither browser nor desktop sharing is available', async () => {
      vi.stubGlobal('navigator', { ...navigator });
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue({
          ok: true,
          blob: async () => new Blob(['picture'], { type: 'image/png' }),
        })
      );
      renderIt([attachment({ kind: 'image' })], false);

      fireEvent.click(screen.getByRole('button', { name: 'Share' }));

      expect(
        await screen.findByText(/Sharing pictures is not supported/)
      ).toBeTruthy();
    });

    it('reports a picture that could not be loaded', async () => {
      vi.stubGlobal('navigator', {
        ...navigator,
        share: vi.fn(),
        canShare: vi.fn().mockReturnValue(true),
      });
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue(new Response(null, { status: 404 }))
      );
      renderIt([attachment({ kind: 'image' })], false);

      fireEvent.click(screen.getByRole('button', { name: 'Share' }));

      expect(
        await screen.findByText(/Could not load the picture/)
      ).toBeTruthy();
    });

    it('does not show an error when the share sheet is cancelled', async () => {
      const cancelled = new Error('cancelled');
      cancelled.name = 'AbortError';
      vi.stubGlobal('navigator', {
        ...navigator,
        share: vi.fn().mockRejectedValue(cancelled),
        canShare: vi.fn().mockReturnValue(true),
      });
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue({
          ok: true,
          blob: async () => new Blob(['picture'], { type: 'image/png' }),
        })
      );
      renderIt([attachment({ kind: 'image' })], false);

      fireEvent.click(screen.getByRole('button', { name: 'Share' }));
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Share' })).toBeTruthy()
      );
      expect(screen.queryByText(/cancelled/i)).toBeNull();
    });
  });

  // The point of the whole paste path: a voice memo copied on a phone should not
  // have to be exported to Files and picked back out.
  describe('paste and drop', () => {
    const pasteEvent = (files: File[]) => ({
      clipboardData: { files: files as unknown as FileList },
    });

    it('takes a pasted media file', async () => {
      const { container } = renderIt([]);
      const zone = container.querySelector(
        '[data-testid="journal-attachment-dropzone"]'
      )!;

      const memo = new File(['x'], 'New Recording 4.m4a', {
        type: 'audio/mp4',
      });
      fireEvent.paste(zone, pasteEvent([memo]));

      await waitFor(() => expect(storePhoto).toHaveBeenCalled());
      expect(attached()).toMatchObject({
        file: memo,
        entryId: 'e1',
        name: 'New Recording 4',
      });
    });

    it('uploads several pasted files in order', async () => {
      vi.mocked(api.journal.attachments.upload).mockResolvedValue(attachment());
      const { container } = renderIt([]);
      const zone = container.querySelector(
        '[data-testid="journal-attachment-dropzone"]'
      )!;

      const first = new File(['x'], 'a.m4a', { type: 'audio/mp4' });
      const second = new File(['x'], 'b.mov', { type: 'video/quicktime' });
      fireEvent.paste(zone, pasteEvent([first, second]));

      // One at a time and in order: the server's `position` counter is what
      // orders an entry's attachments, and it counts arrivals.
      await waitFor(() => expect(storePhoto).toHaveBeenCalledTimes(2));
      expect(vi.mocked(storePhoto).mock.calls.map(c => c[1])).toEqual([
        first,
        second,
      ]);
    });

    it('takes a pasted document now that the backend stores one', async () => {
      // This used to assert a refusal. The attach-a-file button promises any
      // file; a paste of the same file has to mean the same thing.
      const { container } = renderIt([]);
      const zone = container.querySelector(
        '[data-testid="journal-attachment-dropzone"]'
      )!;
      const doc = new File(['x'], 'notes.pdf', { type: 'application/pdf' });

      fireEvent.paste(zone, pasteEvent([doc]));

      await waitFor(() => expect(storePhoto).toHaveBeenCalled());
      expect(attached()).toMatchObject({ file: doc, name: 'notes' });
    });

    it('says why a paste it cannot take was ignored', async () => {
      const { container } = renderIt([]);
      const zone = container.querySelector(
        '[data-testid="journal-attachment-dropzone"]'
      )!;

      fireEvent.paste(zone, pasteEvent([new File([], '', { type: '' })]));

      // Silence here is what made the earlier version feel broken.
      expect(await screen.findByText(/Can't attach/)).toBeTruthy();
      expect(storePhoto).not.toHaveBeenCalled();
    });

    it('uploads a file iOS handed over with no name', async () => {
      const { container } = renderIt([]);
      const zone = container.querySelector(
        '[data-testid="journal-attachment-dropzone"]'
      )!;

      const nameless = new File(['x'], '', { type: 'audio/mp4' });
      fireEvent.drop(zone, {
        dataTransfer: { files: [nameless] as unknown as FileList },
      });

      await waitFor(() => expect(storePhoto).toHaveBeenCalled());
      expect(attached()).toMatchObject({ file: nameless, name: '' });
    });

    it('leaves an ordinary text paste alone', () => {
      const { container } = renderIt([]);
      const zone = container.querySelector(
        '[data-testid="journal-attachment-dropzone"]'
      )!;

      fireEvent.paste(zone, pasteEvent([]));
      expect(storePhoto).not.toHaveBeenCalled();
    });

    it('ignores a paste outside edit mode', () => {
      const { container } = renderIt([attachment()], false);
      const zone = container.querySelector(
        '[data-testid="journal-attachment-dropzone"]'
      )!;

      fireEvent.paste(
        zone,
        pasteEvent([new File(['x'], 'a.m4a', { type: 'audio/mp4' })])
      );
      expect(storePhoto).not.toHaveBeenCalled();
    });

    // Safari offers no paste affordance unless the tap lands in an editable
    // field, so there is an explicit button as well as the event handler.
    describe('the Paste button', () => {
      const stubClipboard = (read: () => Promise<unknown[]>) =>
        vi.stubGlobal('navigator', { ...navigator, clipboard: { read } });

      afterEach(() => vi.unstubAllGlobals());

      it('uploads audio it finds on the clipboard', async () => {
        stubClipboard(async () => [
          {
            types: ['audio/mp4'],
            getType: async () => new Blob(['x'], { type: 'audio/mp4' }),
          },
        ]);
        renderIt([]);

        fireEvent.click(screen.getByText('Paste'));

        await waitFor(() => expect(storePhoto).toHaveBeenCalled());
        expect(vi.mocked(storePhoto).mock.calls[0][1].type).toBe('audio/mp4');
      });

      it('says so when the clipboard holds nothing attachable', async () => {
        stubClipboard(async () => [
          { types: ['text/plain'], getType: async () => new Blob(['hi']) },
        ]);
        renderIt([]);

        fireEvent.click(screen.getByText('Paste'));

        expect(
          await screen.findByText(/clipboard has no audio, video or image/)
        ).toBeTruthy();
        expect(storePhoto).not.toHaveBeenCalled();
      });

      it('reports a clipboard the browser refuses to read', async () => {
        stubClipboard(async () => {
          throw new Error('Read permission denied');
        });
        renderIt([]);

        fireEvent.click(screen.getByText('Paste'));

        expect(await screen.findByText(/Read permission denied/)).toBeTruthy();
      });
    });

    it('takes a dropped file', async () => {
      const { container } = renderIt([]);
      const zone = container.querySelector(
        '[data-testid="journal-attachment-dropzone"]'
      )!;

      const clip = new File(['x'], 'IMG_0043.MOV', { type: 'video/quicktime' });
      fireEvent.drop(zone, {
        dataTransfer: { files: [clip] as unknown as FileList },
      });

      await waitFor(() => expect(storePhoto).toHaveBeenCalled());
      expect(attached()).toMatchObject({ file: clip, name: 'IMG_0043' });
    });
  });

  it('queues a transcription on demand and never on its own', async () => {
    vi.mocked(api.journal.attachments.transcribe).mockResolvedValue(
      attachment({ transcriptStatus: 'running' })
    );
    renderIt([attachment()]);

    // Rendering an attachment must not cost a transcription — it is opt-in.
    expect(api.journal.attachments.transcribe).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText('Transcribe'));
    await waitFor(() =>
      expect(api.journal.attachments.transcribe).toHaveBeenCalledWith('a1')
    );
  });

  it('disables the button while a transcription is running', () => {
    renderIt([attachment({ transcriptStatus: 'running' })]);
    const button = screen.getByText('Transcribing…') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it('shows the transcript once it lands', () => {
    renderIt([
      attachment({
        transcript: 'So today was rough.',
        transcriptStatus: 'done',
      }),
    ]);
    expect(screen.getByText('So today was rough.')).toBeTruthy();
  });

  it('shows why a transcription failed', () => {
    renderIt([
      attachment({
        transcriptStatus: 'error',
        transcriptError: 'No vision model configured',
      }),
    ]);
    expect(screen.getByText('No vision model configured')).toBeTruthy();
  });

  it('queues an audio description on demand and never on its own', async () => {
    vi.mocked(api.journal.attachments.describeAudio).mockResolvedValue(
      attachment({ descriptionStatus: 'running' })
    );
    renderIt([attachment()]);

    expect(api.journal.attachments.describeAudio).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText('Describe audio'));
    await waitFor(() =>
      expect(api.journal.attachments.describeAudio).toHaveBeenCalledWith('a1')
    );
  });

  it('offers no audio-description button for an image', () => {
    renderIt([attachment({ kind: 'image' })]);
    expect(screen.queryByText('Describe audio')).toBeNull();
  });

  it('disables the audio-description button while it is running', () => {
    renderIt([attachment({ descriptionStatus: 'running' })]);
    const button = screen.getByText('Describing audio…') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it('shows the audio description once it lands', () => {
    renderIt([
      attachment({
        description: 'A dog barks twice in the background.',
        descriptionStatus: 'done',
      }),
    ]);
    expect(
      screen.getByText('A dog barks twice in the background.')
    ).toBeTruthy();
  });

  it('shows why an audio description failed', () => {
    renderIt([
      attachment({
        descriptionStatus: 'error',
        descriptionError: 'No audio-description model configured',
      }),
    ]);
    expect(
      screen.getByText('No audio-description model configured')
    ).toBeTruthy();
  });

  it('renames on blur, and only when the name actually changed', async () => {
    vi.mocked(api.journal.attachments.rename).mockResolvedValue(attachment());
    renderIt([attachment()]);

    const field = screen.getByLabelText('What this attachment is about');
    fireEvent.blur(field);
    expect(api.journal.attachments.rename).not.toHaveBeenCalled();

    fireEvent.change(field, {
      target: { value: 'Walk home, parser thoughts' },
    });
    fireEvent.blur(field);
    await waitFor(() =>
      expect(api.journal.attachments.rename).toHaveBeenCalledWith(
        'a1',
        'Walk home, parser thoughts'
      )
    );
  });

  it('reverts an emptied name rather than saving it', () => {
    renderIt([attachment()]);
    const field = screen.getByLabelText(
      'What this attachment is about'
    ) as HTMLInputElement;
    fireEvent.change(field, { target: { value: '   ' } });
    fireEvent.blur(field);

    expect(api.journal.attachments.rename).not.toHaveBeenCalled();
    expect(field.value).toBe('Walk home');
  });

  it('surfaces a file it cannot read instead of failing silently', async () => {
    // A failed *upload* is no longer an error the user sees — it is queued and
    // retried. What is left is a file whose bytes never reached the device: a
    // picker reference that stopped resolving, or an iCloud photo whose
    // full-size original never came down.
    vi.mocked(storePhoto).mockRejectedValue(
      new Error('That photo came back empty')
    );
    const { container } = renderIt([]);

    const input = container.querySelector<HTMLInputElement>(
      '[data-testid="journal-image-input"]'
    )!;
    fireEvent.change(input, {
      target: { files: [new File(['x'], 'big.png', { type: 'image/png' })] },
    });

    expect(await screen.findByText('That photo came back empty')).toBeTruthy();
  });
});

/** A YouTube video attached to an entry, at whatever stage. */
function video(over: Partial<JournalAttachment> = {}): JournalAttachment {
  return attachment({
    id: 'v1',
    kind: 'youtube',
    name: 'But what is a neural network?',
    mime: 'video/mp4',
    url: '/api/journal/attachments/v1/file',
    thumbnailUrl: '/api/journal/attachments/v1/thumbnail',
    sourceUrl: 'https://www.youtube.com/watch?v=aircAruvnKk',
    durationSeconds: 1140,
    importStatus: 'ready',
    ...over,
  });
}

describe('JournalAttachments — a watched YouTube video', () => {
  it('shows the poster and its runtime rather than autoloading the video', () => {
    // An entry can carry several of these; three preloading players is not a
    // page the Pocket 2 can open.
    const { container } = renderIt([video()], false);
    const img = container.querySelector('img');
    expect(img?.getAttribute('src')).toBe(
      '/api/journal/attachments/v1/thumbnail'
    );
    expect(container.querySelector('video')).toBeNull();
    expect(screen.getByText('19:00')).toBeTruthy();
  });

  it('swaps in the local file — not YouTube — when the poster is clicked', () => {
    // Playing the archived copy is the whole point of downloading it.
    const { container } = renderIt([video()], false);
    fireEvent.click(screen.getByTitle('Play the archived copy'));
    const el = container.querySelector('video');
    expect(el?.getAttribute('src')).toBe('/api/journal/attachments/v1/file');
  });

  it('still links out to the original', () => {
    renderIt([video()], false);
    const link = screen.getByText('Watch on YouTube') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe(
      'https://www.youtube.com/watch?v=aircAruvnKk'
    );
  });

  it('says it is downloading, and offers no player yet', () => {
    const { container } = renderIt(
      [
        video({
          importStatus: 'importing',
          url: undefined,
          thumbnailUrl: undefined,
        }),
      ],
      false
    );
    expect(screen.getByText(/Looking the video up|Downloading/)).toBeTruthy();
    expect(container.querySelector('video')).toBeNull();
  });

  it('surfaces a failed download with its reason', () => {
    renderIt(
      [
        video({
          importStatus: 'error',
          importError: 'The backup drive is not connected.',
          url: undefined,
        }),
      ],
      false
    );
    expect(screen.getByText(/The backup drive is not connected/)).toBeTruthy();
  });

  it('shows the summary without making the reader open it', () => {
    // Every other AI block here describes something visible on the page. This
    // one is the only account of twenty minutes nobody has time to rewatch.
    renderIt([video({ description: 'It explains gradient descent.' })], false);
    expect(screen.getByText('It explains gradient descent.')).toBeTruthy();
    expect(screen.getByText('What the video says')).toBeTruthy();
  });

  it('attaches a link typed into the editor', async () => {
    vi.mocked(api.journal.attachments.link).mockResolvedValue(video());
    renderIt([], true);

    fireEvent.click(screen.getByTestId('journal-link-button'));
    fireEvent.change(screen.getByTestId('journal-link-input'), {
      target: { value: 'https://youtu.be/aircAruvnKk' },
    });
    fireEvent.click(screen.getByTestId('journal-link-add'));

    await waitFor(() =>
      expect(api.journal.attachments.link).toHaveBeenCalledWith(
        'e1',
        'https://youtu.be/aircAruvnKk'
      )
    );
  });

  it('counts a watched video apart from a filmed one', () => {
    // Both are "video"; an entry holding one of each must not read
    // "1 video, 1 video".
    renderIt([video(), attachment({ id: 'a9', kind: 'video' })], false);
    expect(screen.getByText(/1 video, 1 YouTube video/)).toBeTruthy();
  });
});
