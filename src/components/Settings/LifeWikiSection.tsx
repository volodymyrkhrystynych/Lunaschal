import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type LifeFact } from '../../hooks/api';

/**
 * What the assistant has worked out about the user, and the controls that make
 * it correctable.
 *
 * The wiki had no interface at all before this — it only ever held notes about
 * code, written and read by agents. A synthesized wiki *about a person* that
 * they cannot read or correct would be a worse version of the problem the
 * memory editor above exists to solve, so this panel is not decoration on the
 * nightly pass; it is what makes it fair to run one.
 *
 * The facts are shown, not just the prose, because the prose is derived from
 * them and regenerated each time. Correcting the article body would be undone
 * on the next render; correcting a *fact* is what actually sticks — which is
 * why each one carries the kind of row it came from and can be locked or
 * deleted individually.
 */
function FactRow({
  fact,
  onChanged,
}: {
  fact: LifeFact;
  onChanged: () => void;
}) {
  const lock = useMutation({
    mutationFn: () => api.lifeWiki.lockFact(fact.id, !fact.locked),
    onSuccess: onChanged,
  });
  const remove = useMutation({
    mutationFn: () => api.lifeWiki.deleteFact(fact.id),
    onSuccess: onChanged,
  });

  return (
    <li className="flex items-start justify-between gap-2 rounded border border-white/10 p-2 text-xs">
      <div className="min-w-0">
        <p className="text-[var(--color-text)] break-words">{fact.statement}</p>
        <p className="text-[var(--color-text-muted)] mt-0.5">
          from {fact.sourceKind}
          {fact.locked ? ' · kept' : ''}
        </p>
      </div>
      <div className="flex shrink-0 gap-1">
        <button
          type="button"
          aria-label={
            fact.locked
              ? `Unkeep fact: ${fact.statement}`
              : `Keep fact: ${fact.statement}`
          }
          onClick={() => lock.mutate()}
          disabled={lock.isPending}
          className="px-2 py-0.5 rounded bg-white/10 text-[var(--color-text)] hover:bg-white/15 disabled:opacity-50"
        >
          {fact.locked ? 'Unkeep' : 'Keep'}
        </button>
        <button
          type="button"
          aria-label={`Delete fact: ${fact.statement}`}
          onClick={() => remove.mutate()}
          disabled={remove.isPending}
          className="px-2 py-0.5 rounded bg-white/10 text-[var(--color-text)] hover:bg-white/15 disabled:opacity-50"
        >
          Delete
        </button>
      </div>
    </li>
  );
}

function ArticleDetail({ slug }: { slug: string }) {
  const queryClient = useQueryClient();
  const { data: article } = useQuery({
    queryKey: ['lifeWiki', slug],
    queryFn: () => api.lifeWiki.get(slug),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['lifeWiki'] });
  };

  const lock = useMutation({
    mutationFn: () => api.lifeWiki.setLocked(slug, !article?.locked),
    onSuccess: invalidate,
  });
  const rebuild = useMutation({
    mutationFn: () => api.lifeWiki.rebuild(slug),
    onSuccess: invalidate,
  });

  if (!article) return null;

  return (
    <div className="space-y-2 pt-2">
      <pre className="whitespace-pre-wrap text-xs text-[var(--color-text-muted)]">
        {article.content || '(nothing written yet)'}
      </pre>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <button
          type="button"
          onClick={() => lock.mutate()}
          disabled={lock.isPending}
          className="px-2 py-1 rounded bg-white/10 text-[var(--color-text)] hover:bg-white/15 disabled:opacity-50"
        >
          {article.locked ? 'Let it update again' : 'Keep my wording'}
        </button>
        <button
          type="button"
          onClick={() => rebuild.mutate()}
          disabled={rebuild.isPending || article.rebuilding}
          className="px-2 py-1 rounded bg-white/10 text-[var(--color-text)] hover:bg-white/15 disabled:opacity-50"
        >
          {rebuild.isPending || article.rebuilding
            ? 'Rebuilding…'
            : 'Rebuild from source'}
        </button>
        <span className="text-[var(--color-text-muted)]">
          {rebuild.isSuccess
            ? 'Started — reopen in a minute to see it.'
            : 'Re-reads the entries these came from and starts over.'}
        </span>
      </div>

      <div>
        <h5 className="text-xs text-[var(--color-text)] mb-1">
          What this is built from
        </h5>
        {article.facts?.length ? (
          <ul className="space-y-1">
            {article.facts.map(fact => (
              <FactRow key={fact.id} fact={fact} onChanged={invalidate} />
            ))}
          </ul>
        ) : (
          <p className="text-xs text-[var(--color-text-muted)]">
            No facts recorded yet.
          </p>
        )}
      </div>
    </div>
  );
}

export function LifeWikiSection() {
  const [openSlug, setOpenSlug] = useState<string | null>(null);
  const { data: articles } = useQuery({
    queryKey: ['lifeWiki'],
    queryFn: api.lifeWiki.list,
  });

  return (
    <div className="space-y-3">
      <p className="text-xs text-[var(--color-text-muted)]">
        Notes the assistant builds about you overnight, from your own journal,
        calendar, meals, workouts and chats. The wording is regenerated from the
        facts underneath it each time — so to correct something for good, fix
        the fact rather than the paragraph.
      </p>

      {!articles?.length ? (
        <p className="text-xs text-[var(--color-text-muted)]">
          Nothing written yet. The first pass runs overnight, before the morning
          briefing.
        </p>
      ) : (
        <ul className="space-y-2">
          {articles.map(article => (
            <li
              key={article.id}
              className="rounded border border-white/10 p-2 text-xs"
            >
              <button
                type="button"
                onClick={() =>
                  setOpenSlug(openSlug === article.slug ? null : article.slug)
                }
                className="w-full text-left"
              >
                <span className="text-[var(--color-text)]">
                  {article.title}
                </span>
                {article.locked ? (
                  <span className="text-[var(--color-text-muted)]">
                    {' '}
                    · yours
                  </span>
                ) : null}
                {article.summary ? (
                  <p className="text-[var(--color-text-muted)] mt-0.5">
                    {article.summary}
                  </p>
                ) : null}
              </button>
              {openSlug === article.slug && (
                <ArticleDetail slug={article.slug} />
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
