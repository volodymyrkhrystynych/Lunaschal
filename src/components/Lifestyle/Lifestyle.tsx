import { ActivityHeatmap } from './ActivityHeatmap';
import { CaloriesCard } from './CaloriesCard';
import { ChoresCard } from './ChoresCard';
import { Progression } from './Progression';
import { SelfieCard } from './SelfieCard';
import { WorkoutLog } from './WorkoutLog';

/**
 * The Lifestyle tab: workouts, activity heatmap, progression, chores, daily
 * selfie and calories in one scrollable column (docs/lifestyle-tab.md).
 *
 * Paired cards sit side by side on desktop and stack on the Pocket 2's narrow
 * screen; the heatmap leads because it's the at-a-glance summary.
 */
export function Lifestyle() {
  return (
    <div className="flex-1 overflow-y-auto p-4">
      <h1 className="text-2xl font-semibold text-[var(--color-text)] mb-4">
        Lifestyle
      </h1>
      <div className="flex flex-col gap-4 max-w-6xl">
        <ActivityHeatmap />
        <div className="grid gap-4 lg:grid-cols-2">
          <WorkoutLog />
          <ChoresCard />
        </div>
        <Progression />
        <div className="grid gap-4 lg:grid-cols-2">
          <SelfieCard />
          <CaloriesCard />
        </div>
      </div>
    </div>
  );
}
