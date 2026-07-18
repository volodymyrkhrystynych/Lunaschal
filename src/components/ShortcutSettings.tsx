import { useEffect, useRef, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../hooks/api';
import {
  ACTION_LABELS, DEFAULT_BINDINGS, comboFromEvent, displayCombo, isModifierCode, keyCapture,
} from '../shortcuts/keymap';
import type { ActionId } from '../shortcuts/keymap';

const GROUPS: { title: string; actions: ActionId[] }[] = [
  { title: 'Navigation', actions: ['nav.up', 'nav.down', 'nav.in', 'nav.out', 'global.toggleSidebar'] },
  { title: 'Actions', actions: ['action.new', 'action.newAlt', 'action.search', 'global.newJournalEntry'] },
  { title: 'Reader / Writing', actions: ['action.annotate', 'reader.fontUp', 'reader.fontDown', 'reader.toggleList'] },
  {
    title: 'Tabs',
    actions: ['tab.chat', 'tab.tasks', 'tab.journal', 'tab.writing', 'tab.calendar', 'tab.learning', 'tab.cookbook', 'tab.fanfic', 'tab.files', 'tab.settings'],
  },
];

function BrowserKeyRecorder({ value, onChange }: { value: string; onChange: (combo: string) => void }) {
  const [listening, setListening] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!listening) return;
    keyCapture.active = true;
    const handleDown = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (isModifierCode(e.code)) return; // wait for the actual key
      if (e.code === 'Escape') {
        setListening(false);
        return;
      }
      onChange(comboFromEvent(e));
      setListening(false);
    };
    window.addEventListener('keydown', handleDown, true);
    return () => {
      window.removeEventListener('keydown', handleDown, true);
      keyCapture.active = false;
    };
  }, [listening, onChange]);

  useEffect(() => {
    if (listening) ref.current?.focus();
  }, [listening]);

  return (
    <button
      ref={ref}
      onClick={() => setListening(true)}
      onBlur={() => setListening(false)}
      className={`px-3 py-1.5 rounded text-sm border transition-colors min-w-28 ${
        listening
          ? 'border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-primary)] animate-pulse'
          : 'border-white/20 bg-white/5 hover:bg-white/10 text-[var(--color-text)]'
      }`}
    >
      {listening ? 'Press a key…' : displayCombo(value)}
    </button>
  );
}

export function ShortcutSettings() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Record<ActionId, string>>({ ...DEFAULT_BINDINGS });
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data } = useQuery({ queryKey: ['shortcuts'], queryFn: api.shortcuts.get });

  useEffect(() => {
    if (!data) return;
    const merged = { ...DEFAULT_BINDINGS };
    for (const k of Object.keys(merged) as ActionId[]) {
      const v = data.bindings[k];
      if (typeof v === 'string' && v) merged[k] = v;
    }
    setDraft(merged);
  }, [data]);

  const save = useMutation({
    mutationFn: (bindings: Record<string, string>) => api.shortcuts.put(bindings),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['shortcuts'] });
      setMessage({ type: 'success', text: 'Shortcuts saved' });
      setTimeout(() => setMessage(null), 3000);
    },
    onError: (error: Error) => setMessage({ type: 'error', text: error.message }),
  });

  // Actions whose combo is shared with another action
  const comboCounts = new Map<string, number>();
  for (const combo of Object.values(draft)) comboCounts.set(combo, (comboCounts.get(combo) ?? 0) + 1);
  const hasConflicts = [...comboCounts.values()].some((n) => n > 1);

  const handleExport = () => {
    const blob = new Blob([JSON.stringify({ version: 1, bindings: draft }, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'shortcuts.json';
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleImport = (file: File) => {
    file.text().then((text) => {
      try {
        const parsed = JSON.parse(text);
        const bindings = parsed?.bindings;
        if (typeof bindings !== 'object' || bindings === null) throw new Error('missing "bindings" object');
        const merged = { ...DEFAULT_BINDINGS };
        for (const k of Object.keys(merged) as ActionId[]) {
          const v = bindings[k];
          if (typeof v === 'string' && v) merged[k] = v;
        }
        setDraft(merged);
        setMessage({ type: 'success', text: 'Imported — review and Save to apply' });
      } catch (err) {
        setMessage({ type: 'error', text: `Invalid shortcuts file: ${err instanceof Error ? err.message : err}` });
      }
    });
  };

  return (
    <div className="max-w-2xl">
      {message && (
        <div className={`mb-4 p-3 rounded-lg ${message.type === 'success' ? 'bg-green-900/30 border border-green-600/50 text-green-200' : 'bg-red-900/30 border border-red-600/50 text-red-200'}`}>
          {message.text}
        </div>
      )}

      <p className="text-sm text-[var(--color-text-muted)] mb-6">
        Navigate the app with the keyboard: move between tabs, drill into a tab, and step through its items.
        Shortcuts are disabled while you're typing in a text field — press <span className="text-[var(--color-text)]">Escape</span> to
        leave the field and get them back. Stored at <code className="text-[var(--color-text)]">./data/shortcuts.json</code> —
        copy that file to another machine to transfer your setup.
      </p>

      {GROUPS.map((group) => (
        <section key={group.title} className="mb-6">
          <h2 className="text-lg font-medium text-[var(--color-text)] mb-3">{group.title}</h2>
          <div className="space-y-2">
            {group.actions.map((action) => {
              const conflict = (comboCounts.get(draft[action]) ?? 0) > 1;
              return (
                <div key={action}
                  className={`flex items-center justify-between gap-3 px-3 py-2 rounded-lg border ${
                    conflict ? 'border-red-500/60 bg-red-900/20' : 'border-white/10 bg-[var(--color-surface)]'
                  }`}>
                  <div className="min-w-0">
                    <div className="text-sm text-[var(--color-text)]">{ACTION_LABELS[action]}</div>
                    {conflict && <div className="text-xs text-red-400">Conflicts with another shortcut</div>}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <BrowserKeyRecorder
                      value={draft[action]}
                      onChange={(combo) => setDraft((d) => ({ ...d, [action]: combo }))}
                    />
                    {draft[action] !== DEFAULT_BINDINGS[action] && (
                      <button
                        onClick={() => setDraft((d) => ({ ...d, [action]: DEFAULT_BINDINGS[action] }))}
                        className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                        title={`Reset to ${displayCombo(DEFAULT_BINDINGS[action])}`}
                      >↺</button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      ))}

      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={() => save.mutate(draft)}
          disabled={hasConflicts || save.isPending}
          className="px-4 py-2 bg-[var(--color-primary)] text-white rounded-lg hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
        >
          {save.isPending ? 'Saving…' : 'Save Shortcuts'}
        </button>
        <button
          onClick={() => setDraft({ ...DEFAULT_BINDINGS })}
          className="px-4 py-2 rounded-lg border border-white/20 text-[var(--color-text)] hover:bg-white/10"
        >
          Reset All to Defaults
        </button>
        <button
          onClick={handleExport}
          className="px-4 py-2 rounded-lg border border-white/20 text-[var(--color-text)] hover:bg-white/10"
        >
          Export
        </button>
        <button
          onClick={() => fileInputRef.current?.click()}
          className="px-4 py-2 rounded-lg border border-white/20 text-[var(--color-text)] hover:bg-white/10"
        >
          Import
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".json,application/json"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleImport(file);
            e.target.value = '';
          }}
        />
      </div>
    </div>
  );
}
