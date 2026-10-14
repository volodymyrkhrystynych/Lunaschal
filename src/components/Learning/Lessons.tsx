import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type LearningLesson } from '../../hooks/api';
import {
  AGENT_LEARNINGS_SNIPPET,
  describeImport,
} from '../../lib/agentLearnings';
import { ErrorBanner } from '../LoadStates';

const INPUT =
  'w-full bg-transparent text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] border border-white/10 rounded-lg p-2.5 focus:outline-none focus:border-[var(--color-primary)]';
const SECONDARY =
  'px-3 py-1.5 rounded text-sm bg-white/10 hover:bg-white/20 text-[var(--color-text)] transition-colors disabled:opacity-50';

const INBOX_STATE: Record<string, string> = {
  unset: 'No inbox folder set — upload or paste files instead.',
  missing: 'That folder does not exist on the server (is the drive mounted?).',
  ok: 'Scanned whenever the Learning tab opens.',
};

const STATUS_STYLE: Record<LearningLesson['status'], string> = {
  pending: 'bg-white/10 text-[var(--color-text-muted)]',
  carded: 'bg-purple-500/20 text-purple-300',
  covered: 'bg-blue-500/20 text-blue-300',
  error: 'bg-red-500/20 text-red-300',
};

const STATUS_LABEL: Record<LearningLesson['status'], string> = {
  pending: 'Checking…',
  carded: 'In the queue',
  covered: 'Already covered',
  error: 'Failed',
};

// A coding agent's daily learnings files → lessons → cards. Upload or paste
// here, or point the server at an inbox folder. Each lesson is checked
// against the deck first, and only one no card covers becomes cards.
export function Lessons() {
  const queryClient = useQueryClient();
  const [text, setText] = useState('');
  const [sourceName, setSourceName] = useState<string | undefined>();
  const [message, setMessage] = useState<string | null>(null);
  const [inboxPath, setInboxPath] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const { data } = useQuery({
    queryKey: ['learning', 'lessons'],
    queryFn: api.learning.listLessons,
    refetchInterval: q =>
      q.state.data?.lessons.some(l => l.status === 'pending') ? 2000 : false,
  });
  const { data: folders } = useQuery({
    queryKey: ['learning', 'folders'],
    queryFn: api.learning.listFolders,
  });
  const config = data?.config;

  // The text field mirrors the saved path until it is edited.
  useEffect(() => {
    if (config && inboxPath === null) setInboxPath(config.inboxPath);
  }, [config, inboxPath]);

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: ['learning'] });

  const importText = useMutation({
    mutationFn: () =>
      api.learning.importLessons({ text, sourceName: sourceName }),
    onSuccess: r => {
      setMessage(describeImport(r));
      setText('');
      setSourceName(undefined);
      refresh();
    },
  });

  const scan = useMutation({
    mutationFn: api.learning.scanLessons,
    onSuccess: r => {
      setMessage(r.state === 'ok' ? describeImport(r) : INBOX_STATE[r.state]);
      refresh();
    },
  });

  const saveConfig = useMutation({
    mutationFn: api.learning.updateLessonsConfig,
    onSuccess: refresh,
  });

  const force = useMutation({
    mutationFn: api.learning.forceLesson,
    onSuccess: refresh,
  });

  const readFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    // Several days at once arrive as one text; lessons are deduplicated on
    // their own content, so the file boundary doesn't matter.
    const texts = await Promise.all([...files].map(f => f.text()));
    setText(texts.join('\n\n'));
    setSourceName(files.length === 1 ? files[0].name : `${files.length} files`);
  };

  const copySnippet = async () => {
    try {
      await navigator.clipboard.writeText(AGENT_LEARNINGS_SNIPPET);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard blocked (plain http on the LAN) — the text is selectable.
    }
  };

  const pathDirty =
    inboxPath !== null && inboxPath !== (config?.inboxPath ?? '');

  return (
    <div className="max-w-2xl mx-auto space-y-4">
      <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-6 space-y-3">
        <div className="text-sm text-[var(--color-text-muted)]">
          Import a coding agent's learnings files. Each lesson is checked
          against your cards first; only new ones become scenario cards in the
          approval queue.
        </div>
        <textarea
          value={text}
          onChange={e => {
            setText(e.target.value);
            setSourceName(undefined);
          }}
          placeholder={
            'Paste a learnings file, or one lesson:\n\n## Recursive functions need a depth limit\n- **Failure it prevents:** …'
          }
          rows={6}
          className={`${INPUT} p-3 resize-y`}
        />
        <div className="flex flex-wrap items-center gap-2">
          <label className={`${SECONDARY} cursor-pointer`}>
            Choose .md files…
            <input
              type="file"
              accept=".md,.markdown,text/markdown,text/plain"
              multiple
              className="hidden"
              onChange={e => {
                void readFiles(e.target.files);
                e.target.value = '';
              }}
            />
          </label>
          {sourceName && (
            <span className="text-xs text-[var(--color-text-muted)]">
              {sourceName}
            </span>
          )}
          <button
            onClick={() => importText.mutate()}
            disabled={!text.trim() || importText.isPending}
            className="ml-auto px-4 py-1.5 rounded text-sm bg-[var(--color-primary)] text-white hover:bg-[var(--color-primary)]/80 transition-colors disabled:opacity-50"
          >
            {importText.isPending ? 'Importing…' : 'Import'}
          </button>
        </div>
        {importText.isError && <ErrorBanner error={importText.error} />}
        {message && (
          <div role="status" className="text-sm text-[var(--color-text)]">
            {message}
          </div>
        )}
      </div>

      <div className="bg-[var(--color-surface)] rounded-lg border border-white/10 p-6 space-y-3">
        <div className="text-sm font-medium text-[var(--color-text)]">
          Inbox and destination
        </div>
        <div className="flex gap-2">
          <input
            value={inboxPath ?? ''}
            onChange={e => setInboxPath(e.target.value)}
            placeholder="Inbox folder on the server, e.g. ~/learnings"
            aria-label="Inbox folder"
            className={INPUT}
          />
          <button
            onClick={() => saveConfig.mutate({ inboxPath: inboxPath ?? '' })}
            disabled={!pathDirty || saveConfig.isPending}
            className={SECONDARY}
          >
            Save
          </button>
          <button
            onClick={() => scan.mutate()}
            disabled={config?.inboxState !== 'ok' || scan.isPending}
            className={SECONDARY}
          >
            {scan.isPending ? 'Scanning…' : 'Scan now'}
          </button>
        </div>
        {config && (
          <div
            className={`text-xs ${config.inboxState === 'missing' ? 'text-orange-400' : 'text-[var(--color-text-muted)]'}`}
          >
            {INBOX_STATE[config.inboxState]}
          </div>
        )}
        <label className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
          Cards go to
          <select
            value={config?.folderId ?? ''}
            onChange={e =>
              saveConfig.mutate({ folderId: e.target.value || null })
            }
            aria-label="Destination folder"
            className="bg-[var(--color-surface)] text-[var(--color-text)] border border-white/10 rounded p-1.5"
          >
            <option value="">No folder</option>
            {folders
              ?.filter(f => !f.system)
              .map(f => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
          </select>
        </label>
        {saveConfig.isError && <ErrorBanner error={saveConfig.error} />}
      </div>

      <details className="bg-[var(--color-surface)] rounded-lg border border-white/10">
        <summary className="px-6 py-3 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] cursor-pointer">
          Instructions for your coding agent (paste into CLAUDE.md / AGENTS.md)
        </summary>
        <div className="px-6 pb-6 space-y-2">
          <button onClick={() => void copySnippet()} className={SECONDARY}>
            {copied ? 'Copied' : 'Copy'}
          </button>
          <pre className="text-xs whitespace-pre-wrap text-[var(--color-text)] bg-white/5 rounded p-3 select-all">
            {AGENT_LEARNINGS_SNIPPET}
          </pre>
        </div>
      </details>

      <div className="space-y-2">
        {data?.lessons.map(lesson => (
          <div
            key={lesson.id}
            className="p-4 bg-[var(--color-surface)] rounded-lg border border-white/10"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="text-[var(--color-text)] font-medium">
                {lesson.title}
              </div>
              <span
                className={`shrink-0 px-2 py-0.5 text-xs rounded ${STATUS_STYLE[lesson.status]}`}
              >
                {STATUS_LABEL[lesson.status]}
                {lesson.status === 'carded' && ` (${lesson.cardCount})`}
              </span>
            </div>
            {lesson.sourceName && (
              <div className="text-xs text-[var(--color-text-muted)] mt-0.5">
                {lesson.sourceName}
              </div>
            )}
            {lesson.status === 'covered' && (
              <div className="mt-2 text-sm text-[var(--color-text-muted)]">
                {lesson.coveredQuestion && (
                  <div>
                    Covered by: <em>{lesson.coveredQuestion}</em>
                  </div>
                )}
                {lesson.reason && <div>{lesson.reason}</div>}
              </div>
            )}
            {lesson.status === 'error' && lesson.error && (
              <div className="mt-2 text-sm text-red-400">{lesson.error}</div>
            )}
            {(lesson.status === 'covered' || lesson.status === 'error') && (
              <button
                onClick={() => force.mutate(lesson.id)}
                disabled={force.isPending}
                className="mt-2 text-xs underline text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                {lesson.status === 'covered' ? 'Make cards anyway' : 'Retry'}
              </button>
            )}
          </div>
        ))}
        {data && data.lessons.length === 0 && (
          <div className="text-center text-sm text-[var(--color-text-muted)] py-8">
            No lessons imported yet.
          </div>
        )}
      </div>
    </div>
  );
}
