// @vitest-environment jsdom
import { expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { FoodDescriptions } from './FoodDescriptions';
import { api, type FoodMedia } from '../../hooks/api';
import { hasRunningMealTranscript } from '../../lib/food';

vi.mock('../../hooks/api', () => ({
  api: {
    food: { describeMedia: vi.fn().mockResolvedValue({ success: true }) },
  },
}));

const photo: FoodMedia = {
  id: 'photo',
  kind: 'image',
  position: 0,
  url: '/photo',
};

function show(media: FoodMedia[]) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  render(
    <QueryClientProvider client={client}>
      <FoodDescriptions media={media} />
    </QueryClientProvider>
  );
  return invalidate;
}

it('lets an existing photo be described and refreshes the food list', async () => {
  const invalidate = show([photo]);
  fireEvent.click(screen.getByRole('button', { name: 'Describe photo 1' }));
  await waitFor(() =>
    expect(api.food.describeMedia).toHaveBeenCalledWith(
      'photo',
      expect.anything()
    )
  );
  await waitFor(() =>
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['food'] })
  );
});

it('shows a collapsed description and surfaces a failed retry', () => {
  show([
    {
      ...photo,
      description: 'The menu says Pad Thai.',
      descriptionStatus: 'error',
      descriptionError: 'Model offline',
    },
  ]);
  expect(screen.getByText('Photo 1 description').closest('details')?.open).toBe(
    false
  );
  expect(screen.getByRole('alert').textContent).toBe('Model offline');
  expect(screen.getByRole('button', { name: 'Describe again' })).toBeTruthy();
});

it('shows progress without offering a duplicate description request', () => {
  show([{ ...photo, descriptionStatus: 'running' }]);
  expect(screen.getByRole('status').textContent).toBe('Describing photo 1…');
  expect(screen.queryByRole('button')).toBeNull();
});

it('polls through description and polishing, then stops on completion or failure', () => {
  expect(
    hasRunningMealTranscript([{ media: [{ descriptionStatus: 'running' }] }])
  ).toBe(true);
  expect(hasRunningMealTranscript([{ polishing: true, media: [] }])).toBe(true);
  expect(
    hasRunningMealTranscript([
      {
        media: [{ descriptionStatus: 'done' }, { descriptionStatus: 'error' }],
      },
    ])
  ).toBe(false);
});
