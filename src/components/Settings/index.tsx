import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { CuratedTagsSection } from '../CuratedTagsSection';
import { ShortcutSettings } from '../ShortcutSettings';
import { STTStatusSection } from './STTStatusSection';
import { ShortcutsSection } from './ShortcutsSection';
import { NudgeSection } from './NudgeSection';
import { WeatherSection } from './WeatherSection';
import { BriefingSection } from './BriefingSection';
import { ResearchSection } from './ResearchSection';
import { VRAMSection } from './VRAMSection';
import { NetworkSection } from './NetworkSection';
import { FanficCookiesSection } from './FanficCookiesSection';
import { EmailSection } from './EmailSection';
import { JobsSection } from './JobsSection';
import { DisplaySection } from './DisplaySection';
import { LlamaConfigSection } from './LlamaConfigSection';
import { MemorySection } from './MemorySection';
import { BackupSection } from './BackupSection';
import { FilesSection } from './FilesSection';
import { CollapsibleSection } from './CollapsibleSection';
import { shouldAutoExpand } from '../../lib/backup';

export function Settings() {
  const [activeTab, setActiveTab] = useState<'general' | 'tags' | 'shortcuts'>(
    'general'
  );

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: api.settings.get,
  });

  // Same queryKey as BackupSection's own query, so react-query serves both
  // from one request. Only a *broken* backup pops its section open — the whole
  // point of collapsing by default is undone if every group finds a reason.
  const { data: backup } = useQuery({
    queryKey: ['backup', 'status'],
    queryFn: api.backup.status,
  });
  const backupBroken = shouldAutoExpand(backup);

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
        <div className="flex gap-1">
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
          <CollapsibleSection title="Display">
            <DisplaySection />
          </CollapsibleSection>

          <CollapsibleSection title="Backup" autoExpand={backupBroken}>
            <BackupSection />
          </CollapsibleSection>

          <CollapsibleSection title="Files">
            <FilesSection />
          </CollapsibleSection>

          <CollapsibleSection title="Model & VRAM">
            <VRAMSection />
          </CollapsibleSection>

          <CollapsibleSection title="llama.cpp Configuration">
            <LlamaConfigSection />
          </CollapsibleSection>

          <CollapsibleSection title="Memory">
            <MemorySection />
          </CollapsibleSection>

          <CollapsibleSection title="Voice Status">
            <STTStatusSection />
          </CollapsibleSection>

          <CollapsibleSection title="Voice Shortcuts">
            <ShortcutsSection />
          </CollapsibleSection>

          <CollapsibleSection title="Task Nudges">
            <NudgeSection />
          </CollapsibleSection>

          <CollapsibleSection title="Weather">
            <WeatherSection />
          </CollapsibleSection>

          <CollapsibleSection title="Overnight Briefing">
            <BriefingSection />
          </CollapsibleSection>

          <CollapsibleSection title="Research Agent">
            <ResearchSection />
          </CollapsibleSection>

          <CollapsibleSection title="Fanfic Site Cookies">
            <FanficCookiesSection />
          </CollapsibleSection>

          <CollapsibleSection title="Email (Gmail)">
            <EmailSection />
          </CollapsibleSection>

          <CollapsibleSection title="Jobs">
            <JobsSection />
          </CollapsibleSection>

          {settings?.networkMode && (
            <CollapsibleSection title="Network Access">
              <NetworkSection />
            </CollapsibleSection>
          )}

          <CollapsibleSection title="About">
            <p className="text-[var(--color-text)]">Lunaschal v0.1.0</p>
            <p className="text-sm text-[var(--color-text-muted)] mt-1">
              A privacy-first, self-hosted personal AI knowledge assistant.
            </p>
          </CollapsibleSection>
        </>
      )}
    </div>
  );
}
