import { TasksSection } from '../Tasks';
import { ActivityHeatmap } from './ActivityHeatmap';
import { CaloriesCard } from './CaloriesCard';
import { Progression } from './Progression';
import { SelfieCard } from './SelfieCard';
import { TrendsChart } from './TrendsChart';
import { WorkoutLog } from './WorkoutLog';

/**
 * The Lifestyle tab: activity, tasks, workouts, momentum, progression, daily
 * selfie and calories in one scrollable column (docs/lifestyle-tab.md).
 *
 * It absorbed the Tasks tab, which is why the order is what it is. **DOM order
 * is the phone's stacking order** — paired cards sit side by side on the desktop
 * and stack on the Pocket 2's narrow screen — so the two things checked several
 * times a day come first: the heatmap (did I do anything?) and then daily tasks
 * and to-dos. Everything below is reviewed, not tapped.
 */
export function Lifestyle() {
  return (
    <div className="flex-1 overflow-y-auto p-4">
      <div className="flex flex-col gap-4 max-w-6xl">
        <ActivityHeatmap />
        <TasksSection />
        <div className="grid gap-4 lg:grid-cols-2">
          <WorkoutLog />
          <TrendsChart />
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
