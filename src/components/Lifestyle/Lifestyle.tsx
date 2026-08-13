import { TasksSection } from '../Tasks';
import { ActivityHeatmap } from './ActivityHeatmap';
import { CaloriesCard } from './CaloriesCard';
import { Progression } from './Progression';
import { SelfieCard } from './SelfieCard';
import { TrendsChart } from './TrendsChart';
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
 */
export function Lifestyle() {
  return (
    <div className="flex-1 overflow-y-auto p-4">
      <div className="flex flex-col gap-4 max-w-6xl">
        <section className={CARD}>
          <ActivityHeatmap />
          <div className={CARD_DIVIDER}>
            <TrendsChart />
          </div>
        </section>

        <TasksSection />
        <WorkoutLog />

        <div className="grid gap-4 lg:grid-cols-2">
          <CaloriesCard />
          <SelfieCard />
        </div>

        <Progression />
      </div>
    </div>
  );
}
