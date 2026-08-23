import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../hooks/api';
import { FolderPicker } from './FolderPicker';

/**
 * Where the Files tab's cloud-drive root lives on this machine.
 *
 * Mirrors BackupSection's destination row: `files_root` lives in the
 * `settings` table (backend/files_config.py), and the Files tab's blueprint
 * reads it before falling back to the `FILES_ROOT` env var and then the
 * historical `~/notes` default — so the path shown here is always the path
 * the tab is actually serving.
 */
export function FilesSection() {
  const qc = useQueryClient();
  const [picking, setPicking] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['files', 'config'],
    queryFn: api.files.getConfig,
  });

  const saveConfig = useMutation({
    mutationFn: api.files.setConfig,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['files'] }),
  });

  if (isLoading) {
    return <p className="text-sm text-[var(--color-text-muted)]">Checking…</p>;
  }
  if (!data) {
    return <p className="text-sm text-red-400">Could not read Files config.</p>;
  }

  return (
    <div className="space-y-2">
      <div className="flex items-start gap-2">
        <code className="text-xs text-[var(--color-text)] break-all">
          {data.path || 'not set — using ~/notes'}
        </code>
        <button
          type="button"
          onClick={() => setPicking(true)}
          className="ml-auto shrink-0 px-2 py-0.5 rounded text-[11px] text-[var(--color-text-muted)] border border-white/10 hover:text-[var(--color-text)] hover:border-white/25"
        >
          Change…
        </button>
      </div>
      {saveConfig.isError && (
        <p className="text-xs text-red-400">
          {(saveConfig.error as Error)?.message || 'Could not save.'}
        </p>
      )}

      {picking && (
        <FolderPicker
          title="Choose a files folder"
          initialPath={data.path || '/'}
          onClose={() => setPicking(false)}
          onSelect={path => {
            setPicking(false);
            saveConfig.mutate(path);
          }}
        />
      )}
    </div>
  );
}
