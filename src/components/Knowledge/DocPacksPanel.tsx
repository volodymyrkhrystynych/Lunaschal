import { FormEvent, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type KnowledgeDocPack } from '../../hooks/api';
import { ErrorBanner } from '../LoadStates';
import { sizeLabel } from '../../lib/knowledge';

/**
 * The package managers the community registry publishes under. The registry
 * matches names exactly and has no browse endpoint, so the user names the
 * package; this list only saves them guessing the registry's spelling.
 */
export const DOC_REGISTRIES = [
  'npm',
  'pip',
  'go',
  'maven',
  'hex',
  'python',
  'java',
  'docker',
  'kubernetes',
  'podman',
  'systemd',
  'godot',
  'gdscript',
];

function DocPackRow({ pack }: { pack: KnowledgeDocPack }) {
  const client = useQueryClient();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['knowledge'] });
  };
  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      api.knowledge.updateDocPack(pack.id, { enabled }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api.knowledge.deleteDocPack(pack.id),
    onSuccess: refresh,
  });
  const label = `${pack.name} ${pack.version}`;

  return (
    <div
      className={`rounded border border-white/10 p-3 bg-[var(--color-surface)] ${pack.enabled && pack.available ? '' : 'opacity-60'}`}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0">
          <div className="font-medium text-sm truncate">{label}</div>
          <div className="text-xs text-[var(--color-text-muted)] mt-1">
            {[
              pack.registry,
              `${pack.chunkCount} sections`,
              sizeLabel(pack.size),
            ].join(' · ')}
          </div>
        </div>
        <label className="ml-auto shrink-0 flex items-center gap-1 text-[11px] text-[var(--color-text-muted)]">
          <input
            type="checkbox"
            checked={pack.enabled}
            disabled={toggle.isPending}
            onChange={event => toggle.mutate(event.target.checked)}
            aria-label={`Search ${label}`}
          />
          search
        </label>
      </div>
      {!pack.available && (
        <div className="text-xs text-red-400 mt-1">
          The package file is missing — reinstall it.
        </div>
      )}
      <button
        type="button"
        onClick={() => remove.mutate()}
        disabled={remove.isPending}
        className="mt-1 text-[11px] text-[var(--color-text-muted)] hover:text-red-400"
      >
        {remove.isPending ? 'Removing…' : 'Remove'}
      </button>
    </div>
  );
}

function AddDocPack({ onClose }: { onClose: () => void }) {
  const client = useQueryClient();
  const [registry, setRegistry] = useState('npm');
  const [name, setName] = useState('');
  const [lookup, setLookup] = useState<{
    registry: string;
    name: string;
  } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const versions = useQuery({
    queryKey: ['knowledge', 'docpack-versions', lookup?.registry, lookup?.name],
    queryFn: () =>
      api.knowledge.docPackVersions(lookup!.registry, lookup!.name),
    enabled: lookup !== null,
  });
  const install = useMutation({
    mutationFn: (version: string) =>
      api.knowledge.installDocPack(lookup!.registry, lookup!.name, version),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['knowledge'] });
    },
  });
  const uploadFile = useMutation({
    mutationFn: (file: File) => api.knowledge.uploadDocPack(file),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['knowledge'] });
      if (fileInput.current) fileInput.current.value = '';
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const next = name.trim();
    if (next) setLookup({ registry, name: next });
  };

  return (
    <div className="rounded border border-white/10 p-3 space-y-2">
      <div className="flex items-center">
        <h3 className="text-xs font-semibold">Add documentation</h3>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto text-[11px] text-[var(--color-text-muted)]"
        >
          Done
        </button>
      </div>
      <form onSubmit={submit} className="flex gap-1">
        <select
          value={registry}
          onChange={event => setRegistry(event.target.value)}
          aria-label="Package registry"
          className="bg-[var(--color-surface)] border border-white/10 rounded px-1 text-xs"
        >
          {DOC_REGISTRIES.map(r => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <input
          value={name}
          onChange={event => setName(event.target.value)}
          placeholder="exact package name"
          aria-label="Package name"
          className="flex-1 min-w-0 bg-[var(--color-surface)] border border-white/10 rounded px-2 py-1 text-xs outline-none focus:border-[var(--color-primary)]"
        />
        <button className="px-2 py-1 rounded text-xs bg-[var(--color-primary)]/20 text-[var(--color-primary)]">
          Find
        </button>
      </form>
      {versions.isLoading && (
        <p className="text-xs text-[var(--color-text-muted)]">Looking up…</p>
      )}
      {versions.isError && <ErrorBanner error={versions.error} />}
      {versions.data?.length === 0 && (
        <p className="text-xs text-[var(--color-text-muted)]">
          The registry has no docs for {lookup?.registry}/{lookup?.name}.
        </p>
      )}
      {install.isError && <ErrorBanner error={install.error} />}
      <div className="space-y-1">
        {versions.data?.map(item => (
          <div key={item.version} className="flex items-center gap-2 text-xs">
            <span className="font-medium">{item.version}</span>
            {item.size !== null && (
              <span className="text-[var(--color-text-muted)]">
                {sizeLabel(item.size)}
              </span>
            )}
            <button
              type="button"
              disabled={item.installed || install.isPending}
              onClick={() => install.mutate(item.version)}
              className="ml-auto px-2 py-0.5 rounded border border-white/10 text-[var(--color-primary)] disabled:text-[var(--color-text-muted)]"
            >
              {item.installed
                ? 'Installed'
                : install.isPending && install.variables === item.version
                  ? 'Installing…'
                  : 'Install'}
            </button>
          </div>
        ))}
      </div>
      <label className="block text-[11px] text-[var(--color-text-muted)]">
        Or upload a package built with <code>npx @neuledge/context add</code>:
        <input
          ref={fileInput}
          type="file"
          accept=".db"
          aria-label="Upload docs package"
          disabled={uploadFile.isPending}
          onChange={event => {
            const file = event.target.files?.[0];
            if (file) uploadFile.mutate(file);
          }}
          className="block mt-1 text-xs"
        />
      </label>
      {uploadFile.isError && <ErrorBanner error={uploadFile.error} />}
    </div>
  );
}

/**
 * Library documentation packages — a local Context7. Each is one library
 * version, searched by the same `local_knowledge_search` the chat uses.
 */
export function DocPacksPanel() {
  const [adding, setAdding] = useState(false);
  const packs = useQuery({
    queryKey: ['knowledge', 'docpacks'],
    queryFn: api.knowledge.docPacks,
  });

  return (
    <section className="mt-4">
      <div className="flex items-center gap-1 mb-2">
        <h2 className="text-sm font-semibold">Library docs</h2>
        {!adding && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="ml-auto px-2 py-0.5 rounded text-[11px] border border-white/10 text-[var(--color-primary)]"
          >
            Add docs
          </button>
        )}
      </div>
      {adding && <AddDocPack onClose={() => setAdding(false)} />}
      {packs.isError && <ErrorBanner error={packs.error} />}
      {packs.data?.length === 0 && !adding && (
        <p className="text-xs text-[var(--color-text-muted)]">
          No documentation packages installed.
        </p>
      )}
      <div className="space-y-2 mt-2">
        {packs.data?.map(pack => (
          <DocPackRow key={pack.id} pack={pack} />
        ))}
      </div>
    </section>
  );
}
