// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { ReadingTimeline } from './ReadingTimeline';
import type { SleepDay } from '@/lib/sleep';

const date = '2026-07-13';
const at = (clock: string) => new Date(`${date}T${clock}:00`).getTime() / 1000;
const sleep: SleepDay = {
  date,
  wakeAt: at('08:00'),
  sleepAt: at('23:00'),
  wakeSource: 'manual',
  sleepSource: 'manual',
  previousSleepAt: null,
  nextWakeAt: null,
};

it('draws scaled fills and shows titles until the rail is clicked', () => {
  render(
    <ReadingTimeline
      date={date}
      sleep={sleep}
      zoom={2}
      spans={[
        {
          id: 'a',
          title: 'Lighthouse',
          chapterNumber: 3,
          startedAt: at('09:00'),
          endedAt: at('09:30'),
        },
        {
          id: 'b',
          title: 'Storm',
          chapterNumber: 7,
          startedAt: at('11:00'),
          endedAt: at('11:15'),
        },
      ]}
    />
  );
  const rail = screen.getByRole('button', { name: 'Leisure reading timeline' });
  expect(rail.style.top).toBe('480px');
  expect(rail.style.height).toBe('1800px');
  expect(
    screen
      .getAllByTestId('reading-fill')
      .map(el => [el.style.top, el.style.height])
  ).toEqual([
    ['120px', '60px'],
    ['360px', '30px'],
  ]);
  expect(screen.getByText('Lighthouse · Ch. 3')).toBeTruthy();
  expect(screen.getByText('Storm · Ch. 7')).toBeTruthy();
  expect(rail.getAttribute('aria-expanded')).toBe('true');
  fireEvent.click(rail);
  expect(screen.queryByText(/Lighthouse/)).toBeNull();
  expect(rail.getAttribute('aria-expanded')).toBe('false');
  fireEvent.click(rail);
  expect(screen.getByText('Lighthouse · Ch. 3')).toBeTruthy();
  // A title hides them too, since it is what would be covering an event.
  fireEvent.click(screen.getByText('Storm · Ch. 7'));
  expect(screen.queryByText(/Storm/)).toBeNull();
});

it('sits on the right edge, with its titles hanging to its left', () => {
  const { container } = render(
    <ReadingTimeline
      date={date}
      sleep={sleep}
      zoom={1}
      spans={[
        {
          id: 'a',
          title: 'Lighthouse',
          chapterNumber: 3,
          startedAt: at('09:00'),
          endedAt: at('09:30'),
        },
      ]}
    />
  );
  const lane = container.firstElementChild as HTMLElement;
  expect(lane.className).toContain('right-0');
  expect(lane.className).not.toContain('left-');
  const title = screen.getByText('Lighthouse · Ch. 3').closest('button');
  expect(title?.className).toContain('right-6');
});
