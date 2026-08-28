// The parts of an event that both the create form (./index.tsx) and the edit
// modal (./EventDetails.tsx) offer.
//
// They used to be two different forms: creating an event let you set a repeat
// rule but not its categories, editing one let you set the categories but not
// the repeat — so a mistyped recurrence could only be fixed by deleting the
// series and typing it again. Both now render these, which is the only way the
// two stay in step as fields are added.

import {
  repeatUnitLabel,
  WEEKDAY_INITIALS,
  WEEKDAY_LABELS,
  type RepeatDraft,
  type RepeatFreq,
} from '@/lib/calendar';
import {
  CATEGORY_COLORS,
  CATEGORY_LABELS,
  EVENT_CATEGORIES,
  type EventCategory,
} from '@/lib/calendarCategories';

const FIELD_LABEL = 'text-xs text-[var(--color-text-muted)]';
const UNDERLINE =
  'bg-transparent text-[var(--color-text)] border-b border-white/10 focus:outline-none';

export function RepeatFields({
  value,
  onChange,
  /** The day the rule is anchored to: seeds a weekly rule's first weekday and
   * floors the "Until" picker, since a series cannot end before it starts. */
  anchorDate,
}: {
  value: RepeatDraft;
  onChange: (next: RepeatDraft) => void;
  anchorDate: string;
}) {
  const toggleWeekday = (day: number) =>
    onChange({
      ...value,
      byweekday: value.byweekday.includes(day)
        ? value.byweekday.filter(d => d !== day)
        : [...value.byweekday, day].sort((a, b) => a - b),
    });

  const selectFreq = (freq: '' | RepeatFreq) =>
    onChange({
      ...value,
      freq,
      // Seed weekly repeats with the anchor's own weekday so a bare "weekly"
      // still means something.
      byweekday:
        freq === 'weekly' && value.byweekday.length === 0
          ? [new Date(anchorDate + 'T00:00:00').getDay()]
          : value.byweekday,
    });

  return (
    <div>
      <label className={`flex items-center gap-2 ${FIELD_LABEL}`}>
        Repeats
        <select
          aria-label="Repeats"
          value={value.freq}
          onChange={e => selectFreq(e.target.value as '' | RepeatFreq)}
          className={`flex-1 pb-1 ${UNDERLINE}`}
        >
          <option value="">Never</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
          <option value="monthly">Monthly</option>
          <option value="yearly">Yearly</option>
        </select>
      </label>

      {value.freq && (
        <div className="mt-2 space-y-2">
          <label className={`flex items-center gap-2 ${FIELD_LABEL}`}>
            Every
            <input
              type="number"
              aria-label="Repeat interval"
              min={1}
              value={value.interval}
              onChange={e =>
                onChange({
                  ...value,
                  interval: Math.max(1, Number(e.target.value) || 1),
                })
              }
              className={`w-14 ${UNDERLINE}`}
            />
            {repeatUnitLabel(value.freq)}
          </label>

          {value.freq === 'weekly' && (
            <div className="flex gap-1">
              {WEEKDAY_INITIALS.map((initial, day) => (
                <button
                  key={day}
                  type="button"
                  aria-label={WEEKDAY_LABELS[day]}
                  aria-pressed={value.byweekday.includes(day)}
                  onClick={() => toggleWeekday(day)}
                  className={`w-7 h-7 rounded text-xs ${
                    value.byweekday.includes(day)
                      ? 'bg-[var(--color-primary)] text-white'
                      : 'bg-white/5 text-[var(--color-text-muted)] hover:bg-white/10'
                  }`}
                >
                  {initial}
                </button>
              ))}
            </div>
          )}

          <label className={`flex items-center gap-2 ${FIELD_LABEL}`}>
            Until
            <input
              type="date"
              aria-label="Repeat until"
              value={value.until}
              min={anchorDate}
              onChange={e => onChange({ ...value, until: e.target.value })}
              className={`flex-1 ${UNDERLINE}`}
            />
          </label>
        </div>
      )}
    </div>
  );
}

export function CategoryTagPicker({
  value,
  onChange,
}: {
  value: EventCategory[];
  onChange: (next: EventCategory[]) => void;
}) {
  return (
    <div>
      <div className={`${FIELD_LABEL} mb-1`}>
        Groups journal entries in the Journal feed
      </div>
      <div className="flex flex-wrap gap-2">
        {EVENT_CATEGORIES.map(category => {
          const checked = value.includes(category);
          return (
            <label
              key={category}
              className="flex items-center gap-1 text-xs px-2 py-1 rounded"
              style={{
                color: checked ? CATEGORY_COLORS[category] : undefined,
                border: `1px solid ${checked ? CATEGORY_COLORS[category] : 'rgba(255,255,255,0.1)'}`,
              }}
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={e =>
                  onChange(
                    e.target.checked
                      ? [...value, category]
                      : value.filter(c => c !== category)
                  )
                }
                className="w-3 h-3"
              />
              {CATEGORY_LABELS[category]}
            </label>
          );
        })}
      </div>
    </div>
  );
}
