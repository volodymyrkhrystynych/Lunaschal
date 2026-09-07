// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, expect, it, vi } from 'vitest';
import { api, ApiError } from '../../hooks/api';
import { NewspapersSection } from './NewspapersSection';

vi.mock('../../hooks/api', async importOriginal => {
  const original = await importOriginal<typeof import('../../hooks/api')>();
  return {
    ...original,
    api: {
      newspapers: {
        pressreader: vi.fn(),
        setAutoDownload: vi.fn(),
        uploadIssue: vi.fn(),
      },
    },
  };
});
function show() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <NewspapersSection />
    </QueryClientProvider>
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.newspapers.pressreader).mockResolvedValue({
    sessionSaved: true,
    autoDownload: false,
    jobs: [],
  });
  vi.mocked(api.newspapers.uploadIssue).mockResolvedValue({
    date: '2020-09-01',
    byteSize: 100,
    pageCount: 1,
    pdfUrl: '/pdf',
  });
});
it('imports multiple selected PDFs with separate dates and continues after a failure', async () => {
  show();
  const files = ['star-20200901.pdf', 'star-2020-09-02.pdf', 'older.pdf'].map(
    name => new File(['pdf'], name, { type: 'application/pdf' })
  );
  const input = screen.getByLabelText('Select PDFs') as HTMLInputElement;
  expect(input.multiple).toBe(true);
  fireEvent.change(input, { target: { files } });
  expect(
    (screen.getByText('Import 3 PDFs') as HTMLButtonElement).disabled
  ).toBe(true);
  fireEvent.change(screen.getByLabelText('Issue date for older.pdf'), {
    target: { value: '2020-09-03' },
  });
  vi.mocked(api.newspapers.uploadIssue).mockRejectedValueOnce(
    new Error('Upload failed')
  );
  fireEvent.click(screen.getByText('Import 3 PDFs'));
  await waitFor(() =>
    expect(api.newspapers.uploadIssue).toHaveBeenCalledTimes(3)
  );
  expect(api.newspapers.uploadIssue).toHaveBeenNthCalledWith(
    1,
    '2020-09-01',
    files[0]
  );
  expect(api.newspapers.uploadIssue).toHaveBeenNthCalledWith(
    3,
    '2020-09-03',
    files[2]
  );
  await screen.findByText('Upload failed');
  expect(screen.getAllByText('Imported')).toHaveLength(2);
  fireEvent.click(screen.getByText('Import 1 PDF'));
  await waitFor(() =>
    expect(api.newspapers.uploadIssue).toHaveBeenCalledTimes(4)
  );
  expect(screen.getAllByText('Imported')).toHaveLength(3);
});
it('requires unique dates and keeps an already archived issue', async () => {
  show();
  fireEvent.change(screen.getByLabelText('Select PDFs'), {
    target: {
      files: [
        new File([''], 'a-20200901.pdf'),
        new File([''], 'b-20200901.pdf'),
      ],
    },
  });
  expect(
    (screen.getByText('Import 2 PDFs') as HTMLButtonElement).disabled
  ).toBe(true);
  fireEvent.click(screen.getByLabelText('Remove b-20200901.pdf'));
  vi.mocked(api.newspapers.uploadIssue).mockRejectedValueOnce(
    new ApiError('Already archived', 409)
  );
  fireEvent.click(screen.getByText('Import 1 PDF'));
  await screen.findByText('Already archived');
  expect(
    (screen.getByText('Import 0 PDFs') as HTMLButtonElement).disabled
  ).toBe(true);
});
