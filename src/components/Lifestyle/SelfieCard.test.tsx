// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { api, type Selfie } from '@/hooks/api';
import { addDays, todayISO } from '@/lib/lifestyle';
import { SelfieCard } from './SelfieCard';

vi.mock('@/hooks/api', () => ({
  api: {
    lifestyle: {
      selfies: {
        list: vi.fn().mockResolvedValue([]),
        upload: vi.fn().mockResolvedValue({}),
        delete: vi.fn(),
      },
    },
  },
}));

const list = vi.mocked(api.lifestyle.selfies.list);
const upload = vi.mocked(api.lifestyle.selfies.upload);
const TODAY = todayISO();
const YESTERDAY = addDays(TODAY, -1);

const selfie = (date: string): Selfie => ({
  id: `sel-${date}`,
  date,
  mime: 'image/jpeg',
  url: `/api/lifestyle/selfies/sel-${date}/image`,
  createdAt: '',
});

beforeEach(() => {
  vi.clearAllMocks();
  list.mockResolvedValue([]);
  upload.mockResolvedValue({} as Selfie);
});

function renderCard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SelfieCard />
    </QueryClientProvider>
  );
}

const cameraInput = () =>
  screen.getByTestId('selfie-camera-input') as HTMLInputElement;

describe('capture', () => {
  it('opens the device camera instead of an in-page preview', async () => {
    // The old button called getUserMedia and rendered a <video>; on an iPad the
    // OS camera app is the right thing. If anything still reaches for
    // getUserMedia this test fails loudly rather than silently regressing.
    const getUserMedia = vi.fn();
    Object.defineProperty(navigator, 'mediaDevices', {
      value: { getUserMedia },
      configurable: true,
    });
    const click = vi.spyOn(HTMLInputElement.prototype, 'click');
    renderCard();

    fireEvent.click(screen.getByRole('button', { name: 'Take selfie' }));

    expect(click).toHaveBeenCalled();
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(document.querySelector('video')).toBeNull();
    click.mockRestore();
  });

  it('asks the OS for the front camera, and the library for a chosen photo', () => {
    renderCard();
    expect(cameraInput().getAttribute('capture')).toBe('user');
    expect(cameraInput().getAttribute('accept')).toBe('image/*');
    const file = screen.getByTestId('selfie-file-input');
    expect(file.getAttribute('capture')).toBeNull();
  });

  it('uploads what the camera returns', async () => {
    renderCard();
    const image = new File(['x'], 'selfie.jpg', { type: 'image/jpeg' });
    fireEvent.change(cameraInput(), { target: { files: [image] } });

    await waitFor(() => expect(upload).toHaveBeenCalledWith(image));
  });

  it('says Retake once today already has a selfie', async () => {
    list.mockResolvedValue([selfie(TODAY)]);
    renderCard();
    expect(await screen.findByRole('button', { name: 'Retake' })).toBeTruthy();
  });
});

describe('recent-days strip', () => {
  it('never deletes — a thumbnail tap only shows a larger preview', async () => {
    list.mockResolvedValue([selfie(YESTERDAY)]);
    renderCard();

    const thumb = await screen.findByTitle(`${YESTERDAY} — show larger`);
    fireEvent.click(thumb);

    // Deleting a selfie is a deliberate database operation, never a stray tap.
    expect(api.lifestyle.selfies.delete).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getAllByAltText(`Selfie from ${YESTERDAY}`).length).toBe(2)
    );
    expect(screen.getByRole('button', { name: 'Close' })).toBeTruthy();
  });

  it('closes the preview on a second tap of the same day', async () => {
    list.mockResolvedValue([selfie(YESTERDAY)]);
    renderCard();

    const thumb = await screen.findByTitle(`${YESTERDAY} — show larger`);
    fireEvent.click(thumb);
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Close' })).toBeTruthy()
    );
    fireEvent.click(thumb);
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Close' })).toBeNull()
    );
  });

  it('leaves a day with no selfie as an empty, non-interactive slot', async () => {
    list.mockResolvedValue([]);
    renderCard();
    const slot = await screen.findByTitle(`${YESTERDAY} — no selfie`);
    expect(slot.tagName).toBe('DIV');
  });
});
