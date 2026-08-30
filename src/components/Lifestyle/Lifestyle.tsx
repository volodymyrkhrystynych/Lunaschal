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

  // Do not rearrange the page around loading placeholders. Once today's data
  // is known, unfinished daily inputs move above everything else. The cards'
  // own mutations update/invalidate these same query keys, so each one returns
  // to its usual position as soon as it is completed.
  const needsSelfie =
    selfies !== undefined && !selfies.some(selfie => selfie.date === today);
  const needsWeight =
    weights !== undefined && !weights.some(log => log.date === today);
  const needsCalories = calories !== undefined && calories.total < 2000;
  const hasDailyPriorities = needsSelfie || needsWeight || needsCalories;

  return (
    <div className="flex-1 overflow-y-auto p-4">
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
