// The instructions a coding agent needs to keep a daily learnings file that
// the Learning tab can import. Pasted into a CLAUDE.md / AGENTS.md by hand;
// docs/agent-learnings.md carries the same text, and a test keeps them equal.
//
// The file format is the contract with backend/learning/lessons.py's
// parse_lessons: one `## ` section per lesson, a `# ` date heading ignored.
export const AGENT_LEARNINGS_SNIPPET = `## Learnings log

When we resolve something worth remembering — a bug whose cause wasn't
obvious, a design mistake caught late, a principle that would have prevented
the problem — append a lesson to today's learnings file,
\`~/learnings/YYYY-MM-DD.md\`. Create the folder and the file if needed, and
start a new file with a \`# YYYY-MM-DD\` heading. Don't ask first; tell me in
one line that you logged it.

Each lesson is one section in exactly this shape:

\`\`\`markdown
## <the principle, as one imperative sentence>

- **What happened:** the symptom, in a sentence or two.
- **Failure it prevents:** what goes wrong without it.
- **When it doesn't apply:** the honest exception, if there is one.
\`\`\`

Rules:

- Only lessons that transfer to other projects: engineering principles,
  language or library behaviour, debugging heuristics, architecture
  trade-offs. Facts about this codebase belong in its own docs, not here.
- Keep it generic. No company, product or internal service names, no file
  paths, no code copied from this project, no credentials or customer data.
  Describe the situation in ordinary terms.
- A few per day at most. Skip routine fixes (typos, missing imports) and
  anything the first paragraph of the relevant docs would have told me.
- Only append. Never edit or remove an earlier lesson.
`;

/** One line summarising an import or an inbox scan. */
export function describeImport(r: {
  added: number;
  alreadyImported: number;
  files?: number;
}): string {
  const parts: string[] = [];
  if (r.files !== undefined)
    parts.push(`${r.files} file${r.files === 1 ? '' : 's'} read`);
  parts.push(`${r.added} new lesson${r.added === 1 ? '' : 's'}`);
  if (r.alreadyImported > 0)
    parts.push(`${r.alreadyImported} already imported`);
  return parts.join(' · ');
}
