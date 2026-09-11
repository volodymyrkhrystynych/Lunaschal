import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import { todayISO } from '@/lib/lifestyle';
import { TasksSection } from '../Tasks';
import { ActivityHeatmap } from './ActivityHeatmap';
import { CaloriesCard } from './CaloriesCard';
import { BodyWeightCard, Progression } from './Progression';
import { SelfieCard } from './SelfieCard';
import { TrendsChart } from './TrendsChart';
import { WeatherCard } from './WeatherCard';
import { WorkoutLog } from './WorkoutLog';
import { CARD, CARD_DIVIDER } from './card';

/**
 * The Lifestyle tab: activity, tasks, workouts, calories, selfie and weight,
 * one scrollable column (docs/lifestyle-tab.md).
 *
 * **DOM order is the phone's stacking order** — paired cards sit side by side on
 * the desktop and stack on the Pocket 2's narrow screen — so the order is by how
 * often a thing is touched, not by topic: activity and tasks are checked several
 * times a day, weight progression is looked at once in a while.
 *
 * Two pairs share a card rather than sitting in their own: activity + momentum
 * (what happened, and which way it's going) and daily tasks + to-dos (both are
 * "what am I doing today"). Four small cards had four headers and four borders
 * asking to be read as four separate things.
 *
 * Below activity, tasks/to-dos get their own column rather than sharing a row
 * with anything — that list is open-ended and routinely runs long, so pairing
 * it with a fixed-height card would either crop the card or leave it stranded
 * next to empty space. Everything else stacks vertically in the other column.
 */
export function Lifestyle() {
  const [priorities, setPriorities] = useState<{
    needsSelfie: boolean;
    needsWeight: boolean;
    needsCalories: boolean;
  } | null>(null);
  const today = todayISO();
  const { data: selfies } = useQuery({
    queryKey: ['lifestyle', 'selfies'],
    queryFn: () => api.lifestyle.selfies.list(120),
  });
  const { data: weights } = useQuery({
    queryKey: ['lifestyle', 'weight'],
    queryFn: () => api.lifestyle.weight.list(),
  });
  const { data: calories } = useQuery({
    queryKey: ['lifestyle', 'calories'],
    queryFn: () => api.lifestyle.calories.day(),
  });

  // Choose priorities once per visit. Moving a card to another parent unmounts
  // its input, losing focus and pending UI state even if its draft is stored.
  // If someone starts interacting before the queries resolve, keep the layout
  // they are already using. Otherwise wait for all three before arranging it.
  const resolvedPriorities = {
    needsSelfie:
      selfies !== undefined && !selfies.some(selfie => selfie.date === today),
    needsWeight:
      weights !== undefined && !weights.some(log => log.date === today),
    needsCalories: calories !== undefined && calories.total < 2000,
  };
  const ready =
    selfies !== undefined && weights !== undefined && calories !== undefined;
  if (priorities === null && ready) setPriorities(resolvedPriorities);
  const layout =
    priorities ??
    (ready
      ? resolvedPriorities
      : {
          needsSelfie: false,
          needsWeight: false,
          needsCalories: false,
        });
  const { needsSelfie, needsWeight, needsCalories } = layout;
  const hasDailyPriorities = needsSelfie || needsWeight || needsCalories;

  return (
    <div
      className="flex-1 overflow-y-auto p-4"
      onFocusCapture={() => {
        if (priorities === null) setPriorities(layout);
      }}
      onPointerDownCapture={() => {
        if (priorities === null) setPriorities(layout);
      }}
    >
      <div className="flex flex-col gap-4 max-w-6xl">
        {hasDailyPriorities && (
          <div
            aria-label="Today's priorities"
            className="grid gap-4 lg:grid-cols-2 items-start"
          >
            {needsSelfie && <SelfieCard />}
            {needsWeight && <BodyWeightCard />}
            {needsCalories && <CaloriesCard />}
          </div>
        )}

        <section className={CARD}>
          <ActivityHeatmap />
          <div className={CARD_DIVIDER}>
            <TrendsChart />
          </div>
        </section>

        <div className="grid gap-4 lg:grid-cols-2 items-start">
          <TasksSection />
          <div className="flex flex-col gap-4 min-w-0">
            <WorkoutLog />
            {!needsCalories && <CaloriesCard />}
            <WeatherCard />
            {!needsSelfie && <SelfieCard />}
            <Progression hideBodyWeight={needsWeight} />
          </div>
        </div>
      </div>
    </div>
  );
}
