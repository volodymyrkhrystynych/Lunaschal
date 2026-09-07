import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, ApiError } from '../../hooks/api';
import {
  issueDateFromFilename,
  validIssueDate,
} from '../../lib/newspaperImport';

type ImportItem = {
  file: File;
  date: string;
  status: 'ready' | 'uploading' | 'imported' | 'exists' | 'failed';
  error?: string;
};

export function NewspapersSection() {
  const queryClient = useQueryClient();
  const subscriber = useQuery({
    queryKey: ['pressreader'],
    queryFn: api.newspapers.pressreader,
  });
  const autoDownload = useMutation({
    mutationFn: api.newspapers.setAutoDownload,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['pressreader'] }),
  });
  const [items, setItems] = useState<ImportItem[]>([]);
  const [busy, setBusy] = useState(false);
  const running = useRef(false);
  const remaining = items.filter(
    item => item.status !== 'imported' && item.status !== 'exists'
  );
  const duplicateDates = new Set(
    remaining
      .filter(
        (item, i) =>
          item.date &&
          remaining.some((other, j) => j !== i && other.date === item.date)
      )
      .map(item => item.date)
  );
  const valid =
    remaining.length > 0 &&
    remaining.every(item => validIssueDate(item.date)) &&
    !duplicateDates.size;

  async function importFiles() {
    if (!valid || running.current) return;
    running.current = true;
    setBusy(true);
    const update = (index: number, patch: Partial<ImportItem>) =>
      setItems(current =>
        current.map((item, i) => (i === index ? { ...item, ...patch } : item))
      );
    try {
      // Each file has its own request/result; a failed upload doesn't stop the batch.
      for (const [index, item] of items.entries()) {
        if (item.status === 'imported' || item.status === 'exists') continue;
        update(index, { status: 'uploading', error: undefined });
        try {
          await api.newspapers.uploadIssue(item.date, item.file);
          update(index, { status: 'imported' });
        } catch (error) {
          update(
            index,
            error instanceof ApiError && error.status === 409
              ? { status: 'exists' }
              : { status: 'failed', error: (error as Error).message }
          );
        }
      }
    } finally {
      running.current = false;
      setBusy(false);
      void queryClient.invalidateQueries({ queryKey: ['newspaper-issues'] });
    }
  }

  return (
    <div className="space-y-4 text-sm">
      <p>
        Opening Newspapers automatically downloads today's Toronto Star once
        your subscription is connected.
      </p>
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={subscriber.data?.autoDownload ?? false}
          disabled={
            autoDownload.isPending ||
            (!subscriber.data?.sessionSaved && !subscriber.data?.autoDownload)
          }
          onChange={event => autoDownload.mutate(event.target.checked)}
        />
        Also download daily after 6 am Toronto time, without opening Newspapers
      </label>
      {(subscriber.error || autoDownload.error) && (
        <p role="alert">{(subscriber.error || autoDownload.error)?.message}</p>
      )}
      <p>
        Select older Toronto Star PDFs, review each issue date, then import.
        Dates are suggested from filenames when possible. Existing issues and
        markup are kept.
      </p>
      <label className="block">
        Select PDFs
        <input
          className="block mt-2"
          type="file"
          accept="application/pdf,.pdf"
          multiple
          disabled={busy}
          onChange={event => {
            setItems(
              Array.from(event.target.files ?? []).map(file => ({
                file,
                date: issueDateFromFilename(file.name),
                status: 'ready',
              }))
            );
            event.target.value = '';
          }}
        />
      </label>
      <div className="max-h-96 overflow-y-auto space-y-2">
        {items.map((item, index) => (
          <div
            key={index}
            className="flex flex-wrap items-center gap-2 border-b border-white/10 pb-2"
          >
            <span className="break-all">{item.file.name}</span>
            <input
              type="date"
              aria-label={`Issue date for ${item.file.name}`}
              value={item.date}
              disabled={
                busy || item.status === 'imported' || item.status === 'exists'
              }
              className="bg-[var(--color-bg)] border border-white/20 rounded p-2"
              onChange={event =>
                setItems(current =>
                  current.map((entry, i) =>
                    i === index
                      ? {
                          ...entry,
                          date: event.target.value,
                          status: 'ready',
                          error: undefined,
                        }
                      : entry
                  )
                )
              }
            />
            <span role="status">
              {item.status === 'imported'
                ? 'Imported'
                : item.status === 'exists'
                  ? 'Already archived'
                  : item.status === 'uploading'
                    ? 'Importing…'
                    : item.status === 'failed'
                      ? item.error
                      : !item.date
                        ? 'Choose issue date'
                        : duplicateDates.has(item.date)
                          ? 'Duplicate date — choose one PDF per issue'
                          : 'Ready'}
            </span>
            <button
              disabled={busy}
              onClick={() =>
                setItems(current => current.filter((_, i) => i !== index))
              }
              aria-label={`Remove ${item.file.name}`}
            >
              Remove
            </button>
          </div>
        ))}
      </div>
      {items.length > 0 && (
        <button
          className="border border-white/20 rounded p-2 disabled:opacity-50"
          disabled={busy || !valid}
          onClick={() => void importFiles()}
        >
          {busy
            ? 'Importing PDFs…'
            : `Import ${remaining.length} PDF${remaining.length === 1 ? '' : 's'}`}
        </button>
      )}
    </div>
  );
}
