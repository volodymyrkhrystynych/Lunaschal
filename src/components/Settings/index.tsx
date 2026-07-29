import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { CuratedTagsSection } from '../CuratedTagsSection';
import { ShortcutSettings } from '../ShortcutSettings';
import { STTStatusSection } from './STTStatusSection';
import { ShortcutsSection } from './ShortcutsSection';
import { NudgeSection } from './NudgeSection';
import { BriefingSection } from './BriefingSection';
import { VRAMSection } from './VRAMSection';
import { NetworkSection } from './NetworkSection';
import { FanficCookiesSection } from './FanficCookiesSection';
import { DisplaySection } from './DisplaySection';
import { OllamaConfigSection } from './OllamaConfigSection';

export function Settings() {
  const [activeTab, setActiveTab] = useState<'general' | 'tags' | 'shortcuts'>(
    'general'
  );

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-[var(--color-text-muted)]">Loading...</div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-4">
      <div className="flex items-center gap-4 mb-6">
        <h1 className="text-2xl font-semibold text-[var(--color-text)]">
          Settings
        </h1>
        <div className="flex gap-1 ml-2">
          {(['general', 'tags', 'shortcuts'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-4 py-1.5 rounded text-sm transition-colors ${
                activeTab === tab
                  ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
              }`}
            >
              {tab === 'general'
                ? 'General'
                : tab === 'tags'
                  ? 'Tags'
                  : 'Shortcuts'}
            </button>
          ))}
        </div>
      </div>

      {activeTab === 'tags' ? (
        <CuratedTagsSection />
      ) : activeTab === 'shortcuts' ? (
        <ShortcutSettings />
      ) : (
        <>
          <DisplaySection />

          <VRAMSection />

          <OllamaConfigSection />

          <STTStatusSection />

          <ShortcutsSection />

          <NudgeSection />

          <BriefingSection />

          <FanficCookiesSection />

          {settings?.networkMode && <NetworkSection />}

          <section>
            <h2 className="text-lg font-medium text-[var(--color-text)] mb-4">
              About
            </h2>
            <div className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10">
              <p className="text-[var(--color-text)]">Lunaschal v0.1.0</p>
              <p className="text-sm text-[var(--color-text-muted)] mt-1">
                A privacy-first, self-hosted personal AI knowledge assistant.
              </p>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
