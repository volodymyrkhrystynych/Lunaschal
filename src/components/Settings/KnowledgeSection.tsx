import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { FolderPicker } from './FolderPicker';

export function KnowledgeSection() {
  const [picking, setPicking] = useState(false);
  const client = useQueryClient();
  const config = useQuery({
    queryKey: ['knowledge', 'config'],
    queryFn: api.knowledge.config,
  });
  const save = useMutation({
    mutationFn: api.knowledge.setConfig,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['knowledge'] });
    },
  });
  if (config.isLoading)
    return <p className="text-sm text-[var(--color-text-muted)]">Checking…</p>;
  return (
    <div className="space-y-2">
      <p className="text-xs text-[var(--color-text-muted)]">
        Lunaschal reads .zim files in place and never modifies the ones already
        here. Archives downloaded from the Kiwix catalogue are saved into this
        folder, so it has to be writable.
      </p>
      {config.data?.writeState &&
        config.data.writeState !== 'writable' &&
        config.data.writeState !== 'unset' && (
          <p className="text-xs text-amber-400">{config.data.writeReason}</p>
        )}
      <div className="flex items-start gap-2">
        <code className="text-xs text-[var(--color-text)] break-all">
          {config.data?.path || 'not set'}
        </code>
        <button
          type="button"
          onClick={() => setPicking(true)}
          className="ml-auto shrink-0 px-2 py-0.5 rounded text-[11px] border border-white/10 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          Change…
        </button>
      </div>
      {save.isError && (
        <p className="text-xs text-red-400">{(save.error as Error).message}</p>
      )}
      {picking && (
        <FolderPicker
          title="Choose a ZIM archive folder"
          showWritableWarnings
          initialPath={config.data?.path || '/'}
          onClose={() => setPicking(false)}
          onSelect={path => {
            setPicking(false);
            save.mutate(path);
          }}
        />
      )}
    </div>
  );
}
