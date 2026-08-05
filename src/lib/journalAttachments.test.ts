import { describe, it, expect } from 'vitest';
import type { JournalAttachment } from '../hooks/api';
import {
  canDescribeAudio,
  canTranscribe,
  defaultNameFor,
  describeAudioLabel,
  filesFromTransfer,
  formatBytes,
  hasRunningTranscription,
  isAttachableFile,
  rejectedFilesMessage,
  summarizeAttachments,
  transcribeLabel,
  uploadFilenameFor,
} from './journalAttachments';

function attachment(over: Partial<JournalAttachment> = {}): JournalAttachment {
  return {
    id: 'a1',
    entryId: 'e1',
    kind: 'audio',
    name: 'memo',
    url: '/api/journal/attachments/a1/file',
    mime: 'audio/mp4',
    size: 1024,
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

describe('formatBytes', () => {
  it('keeps small sizes in bytes', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(999)).toBe('999 B');
  });

  it('shows one decimal below ten and none above', () => {
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(12 * 1024 * 1024)).toBe('12 MB');
  });

  it('renders nothing for a missing size', () => {
    expect(formatBytes(null)).toBe('');
    expect(formatBytes(undefined)).toBe('');
  });
});

describe('isAttachableFile', () => {
  const file = (name: string, type: string) => new File(['x'], name, { type });

  it('accepts audio, video and images', () => {
    expect(isAttachableFile(file('memo.m4a', 'audio/mp4'))).toBe(true);
    expect(isAttachableFile(file('clip.mov', 'video/quicktime'))).toBe(true);
    expect(isAttachableFile(file('sink.jpg', 'image/jpeg'))).toBe(true);
  });

  it('accepts a media file whose type iOS left blank', () => {
    expect(isAttachableFile(file('New Recording 4.m4a', ''))).toBe(true);
    expect(isAttachableFile(file('IMG_0043.MOV', ''))).toBe(true);
  });

  it('rejects everything else', () => {
    expect(isAttachableFile(file('notes.pdf', 'application/pdf'))).toBe(false);
    expect(isAttachableFile(file('pasted.txt', 'text/plain'))).toBe(false);
    expect(isAttachableFile(file('index.html', 'text/html'))).toBe(false);
  });
});

describe('filesFromTransfer', () => {
  const transfer = (files: File[]) =>
    ({ files: files as unknown as FileList }) as DataTransfer;

  it('is empty for a paste with no files', () => {
    expect(filesFromTransfer(null)).toEqual({ accepted: [], rejected: [] });
    expect(filesFromTransfer(transfer([]))).toEqual({
      accepted: [],
      rejected: [],
    });
  });

  it('keeps media and reports the rest, preserving order', () => {
    const memo = new File(['x'], 'memo.m4a', { type: 'audio/mp4' });
    const junk = new File(['x'], 'clip.html', { type: 'text/html' });
    const clip = new File(['x'], 'clip.mov', { type: 'video/quicktime' });

    expect(filesFromTransfer(transfer([memo, junk, clip]))).toEqual({
      accepted: [memo, clip],
      rejected: [junk],
    });
  });

  it('falls back to .items when .files is empty', () => {
    // Some clipboard payloads only expose the file through .items.
    const memo = new File(['x'], 'memo.m4a', { type: 'audio/mp4' });
    const items = [
      { kind: 'string', getAsFile: () => null },
      { kind: 'file', getAsFile: () => memo },
    ] as unknown as DataTransferItemList;

    expect(
      filesFromTransfer({ files: [] as unknown as FileList, items })
    ).toEqual({ accepted: [memo], rejected: [] });
  });

  it('prefers .files when both are populated', () => {
    const fromFiles = new File(['x'], 'a.m4a', { type: 'audio/mp4' });
    const fromItems = new File(['x'], 'b.m4a', { type: 'audio/mp4' });
    const items = [
      { kind: 'file', getAsFile: () => fromItems },
    ] as unknown as DataTransferItemList;

    expect(
      filesFromTransfer({ files: [fromFiles] as unknown as FileList, items })
    ).toEqual({ accepted: [fromFiles], rejected: [] });
  });
});

describe('uploadFilenameFor', () => {
  it('passes a real filename through', () => {
    expect(
      uploadFilenameFor(new File(['x'], 'memo.m4a', { type: 'audio/mp4' }))
    ).toBe('memo.m4a');
  });

  it('synthesizes one from the mime type when iOS gives no name', () => {
    // The bug this exists for: FormData would otherwise send filename="" and
    // the backend rejected it as "file is required".
    expect(uploadFilenameFor(new File(['x'], '', { type: 'audio/mp4' }))).toBe(
      'attachment.mp4'
    );
    expect(
      uploadFilenameFor(new File(['x'], '', { type: 'audio/x-m4a' }))
    ).toBe('attachment.m4a');
    expect(
      uploadFilenameFor(new File(['x'], '', { type: 'video/quicktime' }))
    ).toBe('attachment.mov');
    expect(uploadFilenameFor(new File(['x'], '', { type: 'audio/mpeg' }))).toBe(
      'attachment.mp3'
    );
    expect(uploadFilenameFor(new File(['x'], '', { type: 'image/jpeg' }))).toBe(
      'attachment.jpg'
    );
  });

  it('still produces something for a file with neither name nor type', () => {
    expect(uploadFilenameFor(new File(['x'], '', { type: '' }))).toBe(
      'attachment.bin'
    );
  });
});

describe('rejectedFilesMessage', () => {
  it('is null when nothing was rejected', () => {
    expect(rejectedFilesMessage([])).toBeNull();
  });

  it('names what it refused so a paste never just does nothing', () => {
    const msg = rejectedFilesMessage([
      new File(['x'], 'notes.pdf', { type: 'application/pdf' }),
    ]);
    expect(msg).toContain('notes.pdf');
  });
});

describe('transcribeLabel', () => {
  it('names the action after the media type', () => {
    expect(transcribeLabel(attachment())).toBe('Transcribe');
    expect(transcribeLabel(attachment({ kind: 'image' }))).toBe('Describe');
  });

  it('transcribes video too — speech is what a clip is kept for', () => {
    expect(transcribeLabel(attachment({ kind: 'video' }))).toBe('Transcribe');
    expect(
      transcribeLabel(
        attachment({ kind: 'video', transcriptStatus: 'running' })
      )
    ).toBe('Transcribing…');
  });

  it('offers a retry once text exists', () => {
    expect(transcribeLabel(attachment({ transcript: 'hello' }))).toBe(
      'Re-transcribe'
    );
    expect(
      transcribeLabel(attachment({ kind: 'image', transcript: 'a pipe' }))
    ).toBe('Re-describe');
  });

  it('reports work in flight', () => {
    expect(transcribeLabel(attachment({ transcriptStatus: 'running' }))).toBe(
      'Transcribing…'
    );
    expect(
      transcribeLabel(
        attachment({ kind: 'image', transcriptStatus: 'running' })
      )
    ).toBe('Describing…');
  });
});

describe('canTranscribe', () => {
  it('blocks only while a job is queued', () => {
    expect(canTranscribe(attachment())).toBe(true);
    expect(canTranscribe(attachment({ transcriptStatus: 'error' }))).toBe(true);
    expect(canTranscribe(attachment({ transcriptStatus: 'done' }))).toBe(true);
    expect(canTranscribe(attachment({ transcriptStatus: 'running' }))).toBe(
      false
    );
  });
});

describe('hasRunningTranscription', () => {
  it('is false for an empty or idle list', () => {
    expect(hasRunningTranscription(undefined)).toBe(false);
    expect(hasRunningTranscription([])).toBe(false);
    expect(hasRunningTranscription([attachment()])).toBe(false);
  });

  it('is true when any attachment is working', () => {
    expect(
      hasRunningTranscription([
        attachment(),
        attachment({ id: 'a2', transcriptStatus: 'running' }),
      ])
    ).toBe(true);
  });

  it('is also true while an audio description is in flight', () => {
    expect(
      hasRunningTranscription([
        attachment({ id: 'a2', descriptionStatus: 'running' }),
      ])
    ).toBe(true);
  });
});

describe('describeAudioLabel', () => {
  it('reports work in flight', () => {
    expect(
      describeAudioLabel(attachment({ descriptionStatus: 'running' }))
    ).toBe('Describing audio…');
  });

  it('offers a retry once a description exists', () => {
    expect(describeAudioLabel(attachment({ description: 'a dog barks' }))).toBe(
      'Re-describe audio'
    );
  });

  it('otherwise invites a first run', () => {
    expect(describeAudioLabel(attachment())).toBe('Describe audio');
  });
});

describe('canDescribeAudio', () => {
  it('allows audio and video but not images', () => {
    expect(canDescribeAudio(attachment({ kind: 'audio' }))).toBe(true);
    expect(canDescribeAudio(attachment({ kind: 'video' }))).toBe(true);
    expect(canDescribeAudio(attachment({ kind: 'image' }))).toBe(false);
  });

  it('blocks only while a job is queued', () => {
    expect(canDescribeAudio(attachment({ descriptionStatus: 'running' }))).toBe(
      false
    );
    expect(canDescribeAudio(attachment({ descriptionStatus: 'error' }))).toBe(
      true
    );
  });
});

describe('summarizeAttachments', () => {
  it('describes an empty section', () => {
    expect(summarizeAttachments([])).toBe('No attachments');
    expect(summarizeAttachments(undefined)).toBe('No attachments');
  });

  it('counts each kind and pluralizes', () => {
    expect(summarizeAttachments([attachment()])).toBe('1 recording');
    expect(
      summarizeAttachments([
        attachment(),
        attachment({ id: 'a2' }),
        attachment({ id: 'a3', kind: 'image' }),
      ])
    ).toBe('2 recordings, 1 photo');
  });

  it('orders the kinds regardless of the list order', () => {
    expect(
      summarizeAttachments([
        attachment({ id: 'a1', kind: 'image' }),
        attachment({ id: 'a2', kind: 'audio' }),
      ])
    ).toBe('1 recording, 1 photo');
  });

  it('counts videos as their own kind', () => {
    expect(
      summarizeAttachments([
        attachment({ id: 'a1', kind: 'video' }),
        attachment({ id: 'a2', kind: 'video' }),
        attachment({ id: 'a3', kind: 'audio' }),
      ])
    ).toBe('1 recording, 2 videos');
  });
});

describe('defaultNameFor', () => {
  it('strips the extension', () => {
    expect(defaultNameFor('voice-memo-004.m4a')).toBe('voice-memo-004');
  });

  it('strips a path', () => {
    expect(defaultNameFor('/home/volodya/Music/walk.mp3')).toBe('walk');
  });

  it('keeps a dotfile-style name rather than emptying it', () => {
    expect(defaultNameFor('.m4a')).toBe('.m4a');
  });

  it('handles a name with no extension', () => {
    expect(defaultNameFor('IMG_0042')).toBe('IMG_0042');
  });
});
