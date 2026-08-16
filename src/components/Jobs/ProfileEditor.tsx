import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/hooks/api';
import type { ProfileSection } from '@/hooks/api';
import { ProfileImport } from './ProfileImport';

function Field({
  label,
  value,
  onCommit,
  multiline = false,
}: {
  label: string;
  value: string;
  onCommit: (next: string) => void;
  multiline?: boolean;
}) {
  const [draft, setDraft] = useState(value);
  const Tag = multiline ? 'textarea' : 'input';

  return (
    <label className="block">
      <span className="block text-xs text-[var(--color-text-muted)] mb-1">
        {label}
      </span>
      <Tag
        value={draft}
        rows={multiline ? 3 : undefined}
        onChange={(
          e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
        ) => setDraft(e.target.value)}
        onBlur={() => draft !== value && onCommit(draft)}
        className="w-full min-h-[44px] p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)]"
      />
    </label>
  );
}

function AddRow({
  placeholder,
  onAdd,
}: {
  placeholder: string;
  onAdd: (value: string) => void;
}) {
  const [value, setValue] = useState('');
  const commit = () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    onAdd(trimmed);
    setValue('');
  };

  return (
    <div className="flex gap-2">
      <input
        value={value}
        onChange={e => setValue(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && commit()}
        placeholder={placeholder}
        className="flex-1 min-h-[44px] p-2 rounded bg-[var(--color-bg)] border border-white/10 text-sm text-[var(--color-text)]"
      />
      <button
        type="button"
        onClick={commit}
        className="min-h-[44px] px-4 rounded text-sm border border-white/20 bg-white/5 hover:bg-white/10"
      >
        Add
      </button>
    </div>
  );
}

/**
 * The master profile. Everything tailoring is allowed to draw on lives here,
 * and nothing it produces can go beyond it — bullets in particular, which the
 * model may reorder and reword but never add to.
 */
export function ProfileEditor() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['jobs', 'profile'],
    queryFn: api.jobs.profile.get,
  });

  // Every profile mutation funnels through here, so this is also where the
  // feed gets re-ranked: the match score is computed against the profile, and
  // a score from last week's skills list is worse than no score. Fire and
  // forget — it is pure string work server-side, and a failure costs a stale
  // ordering rather than the edit the user just made.
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['jobs', 'profile'] });
    api.jobs
      .rescore()
      .then(() => queryClient.invalidateQueries({ queryKey: ['jobs', 'feed'] }))
      .catch(() => {});
  };

  const patchContact = useMutation({
    mutationFn: (patch: Record<string, unknown>) =>
      api.jobs.profile.update(patch),
    onSuccess: invalidate,
  });

  const createChild = useMutation({
    mutationFn: ({
      kind,
      body,
    }: {
      kind: ProfileSection;
      body: Record<string, unknown>;
    }) => api.jobs.profile.create(kind, body),
    onSuccess: invalidate,
  });

  const updateChild = useMutation({
    mutationFn: ({
      kind,
      id,
      body,
    }: {
      kind: ProfileSection;
      id: string;
      body: Record<string, unknown>;
    }) => api.jobs.profile.update_(kind, id, body),
    onSuccess: invalidate,
  });

  const removeChild = useMutation({
    mutationFn: ({ kind, id }: { kind: ProfileSection; id: string }) =>
      api.jobs.profile.remove(kind, id),
    onSuccess: invalidate,
  });

  if (isLoading || !data) {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--color-text-muted)]">
        Loading…
      </div>
    );
  }

  const { profile, roles, skills, education, answers } = data;

  return (
    <div className="flex-1 overflow-y-auto min-w-0 space-y-6 pb-8">
      {/* First, because on an empty profile it is the only thing worth doing:
          nothing else in the tab works until there are bullets to tailor from
          and skills to score postings against. */}
      <ProfileImport onImported={invalidate} />

      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">
          Contact
        </h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            label="Full name"
            value={profile.fullName}
            onCommit={v => patchContact.mutate({ fullName: v })}
          />
          <Field
            label="Email"
            value={profile.email}
            onCommit={v => patchContact.mutate({ email: v })}
          />
          <Field
            label="Phone"
            value={profile.phone}
            onCommit={v => patchContact.mutate({ phone: v })}
          />
          <Field
            label="Location"
            value={profile.location}
            onCommit={v => patchContact.mutate({ location: v })}
          />
        </div>
        <Field
          label="Headline"
          value={profile.headline}
          onCommit={v => patchContact.mutate({ headline: v })}
        />
        <Field
          label="Default summary"
          value={profile.summary}
          multiline
          onCommit={v => patchContact.mutate({ summary: v })}
        />
        <div>
          <p className="text-xs text-[var(--color-text-muted)] mb-1">Links</p>
          <div className="space-y-1 mb-2">
            {profile.links.map((link, i) => (
              <div key={`${link.url}-${i}`} className="flex items-center gap-2">
                <span className="text-sm text-[var(--color-text)] truncate">
                  {link.label}: {link.url}
                </span>
                <button
                  type="button"
                  onClick={() =>
                    patchContact.mutate({
                      links: profile.links.filter((_, j) => j !== i),
                    })
                  }
                  className="text-xs text-[var(--color-text-muted)] hover:text-red-400"
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
          <AddRow
            placeholder="GitHub https://github.com/you"
            onAdd={value => {
              const [label, ...rest] = value.split(' ');
              patchContact.mutate({
                links: [
                  ...profile.links,
                  { label, url: rest.join(' ') || label },
                ],
              });
            }}
          />
        </div>
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">
          Experience
        </h3>
        <p className="text-xs text-[var(--color-text-muted)]">
          Each bullet is one accomplishment. Tailoring picks from these and may
          reword them for a posting, but can never add one that is not here.
        </p>
        {roles.map(role => (
          <div
            key={role.id}
            className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-3 space-y-2"
          >
            <div className="grid gap-2 sm:grid-cols-2">
              <Field
                label="Title"
                value={role.title}
                onCommit={v =>
                  updateChild.mutate({
                    kind: 'roles',
                    id: role.id,
                    body: { title: v },
                  })
                }
              />
              <Field
                label="Company"
                value={role.company}
                onCommit={v =>
                  updateChild.mutate({
                    kind: 'roles',
                    id: role.id,
                    body: { company: v },
                  })
                }
              />
              <Field
                label="From"
                value={role.startLabel}
                onCommit={v =>
                  updateChild.mutate({
                    kind: 'roles',
                    id: role.id,
                    body: { startLabel: v },
                  })
                }
              />
              <Field
                label="To"
                value={role.endLabel}
                onCommit={v =>
                  updateChild.mutate({
                    kind: 'roles',
                    id: role.id,
                    body: { endLabel: v },
                  })
                }
              />
            </div>

            <ul className="space-y-1">
              {role.bullets.map(bullet => (
                <li key={bullet.id} className="flex items-start gap-2">
                  <span className="text-[var(--color-text-muted)]">•</span>
                  <span className="flex-1 text-sm text-[var(--color-text)]">
                    {bullet.text}
                  </span>
                  <button
                    type="button"
                    onClick={() =>
                      removeChild.mutate({ kind: 'bullets', id: bullet.id })
                    }
                    className="text-xs text-[var(--color-text-muted)] hover:text-red-400"
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>

            <AddRow
              placeholder="Add an accomplishment…"
              onAdd={text =>
                createChild.mutate({
                  kind: 'bullets',
                  body: { roleId: role.id, text, ord: role.bullets.length },
                })
              }
            />

            <button
              type="button"
              onClick={() => removeChild.mutate({ kind: 'roles', id: role.id })}
              className="text-xs text-[var(--color-text-muted)] hover:text-red-400"
            >
              Delete this role
            </button>
          </div>
        ))}
        <AddRow
          placeholder="Add a role (job title)…"
          onAdd={title =>
            createChild.mutate({
              kind: 'roles',
              body: { title, ord: roles.length },
            })
          }
        />
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">
          Skills
        </h3>
        <p className="text-xs text-[var(--color-text-muted)]">
          These also widen the keyword scanner: list a technology once and every
          future posting is checked for it.
        </p>
        <div className="flex flex-wrap gap-1">
          {skills.map(skill => (
            <span
              key={skill.id}
              className="text-xs px-2 py-1 rounded border border-white/20 text-[var(--color-text)]"
            >
              {skill.name}
              <button
                type="button"
                onClick={() =>
                  removeChild.mutate({ kind: 'skills', id: skill.id })
                }
                className="ml-1 text-[var(--color-text-muted)] hover:text-red-400"
              >
                ×
              </button>
            </span>
          ))}
        </div>
        <AddRow
          placeholder="Add a skill…"
          onAdd={name =>
            createChild.mutate({
              kind: 'skills',
              body: { name, ord: skills.length },
            })
          }
        />
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">
          Education
        </h3>
        <div className="space-y-1">
          {education.map(entry => (
            <div key={entry.id} className="flex items-center gap-2">
              <span className="flex-1 text-sm text-[var(--color-text)]">
                {entry.institution} — {entry.credential}
              </span>
              <button
                type="button"
                onClick={() =>
                  removeChild.mutate({ kind: 'education', id: entry.id })
                }
                className="text-xs text-[var(--color-text-muted)] hover:text-red-400"
              >
                ×
              </button>
            </div>
          ))}
        </div>
        <AddRow
          placeholder="Institution — credential"
          onAdd={value => {
            const [institution, ...rest] = value.split('—');
            createChild.mutate({
              kind: 'education',
              body: {
                institution: institution.trim(),
                credential: rest.join('—').trim(),
                ord: education.length,
              },
            });
          }}
        />
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">
          Saved answers
        </h3>
        <p className="text-xs text-[var(--color-text-muted)]">
          The standard questions every portal asks. Answered from here with no
          model call at all — this is what makes the second tap instant.
        </p>
        <div className="space-y-2">
          {answers.map(entry => (
            <div
              key={entry.id}
              className="rounded-lg border border-white/10 bg-[var(--color-surface)] p-2"
            >
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-medium text-[var(--color-text)]">
                  {entry.question}
                </p>
                <button
                  type="button"
                  onClick={() =>
                    removeChild.mutate({ kind: 'answers', id: entry.id })
                  }
                  className="text-xs text-[var(--color-text-muted)] hover:text-red-400"
                >
                  ×
                </button>
              </div>
              <Field
                label="Answer"
                value={entry.answer}
                multiline
                onCommit={v =>
                  updateChild.mutate({
                    kind: 'answers',
                    id: entry.id,
                    body: { answer: v },
                  })
                }
              />
            </div>
          ))}
        </div>
        <AddRow
          placeholder="Add a question you keep being asked…"
          onAdd={question =>
            createChild.mutate({
              kind: 'answers',
              body: { question, answer: '', ord: answers.length },
            })
          }
        />
      </section>
    </div>
  );
}
