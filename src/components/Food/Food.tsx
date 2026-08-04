import { useState } from 'react';
import { FoodLog } from './FoodLog';
import { RecipeList } from './RecipeList';

type Section = 'log' | 'recipes';

/**
 * The Food tab: a food **Log** (what you ate, where, whether it was good, with
 * photos/videos) and a **Recipes** collection. Only one section mounts at a
 * time, so each owns shortcut scope 1 while active.
 */
export function Food() {
  const [section, setSection] = useState<Section>('log');

  const tab = (id: Section, label: string) => (
    <button
      onClick={() => setSection(id)}
      className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
        section === id
          ? 'bg-[var(--color-primary)] text-white'
          : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="flex-1 flex flex-col p-4 overflow-hidden">
      <div className="flex items-center justify-end mb-4">
        <div className="flex gap-1 p-1 bg-[var(--color-surface)] rounded-lg border border-white/10">
          {tab('log', 'Log')}
          {tab('recipes', 'Recipes')}
        </div>
      </div>

      {section === 'log' ? <FoodLog /> : <RecipeList />}
    </div>
  );
}
