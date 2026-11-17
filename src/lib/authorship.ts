/**
 * Who produced which part of a text field — the split a regeneration needs:
 *
 * - `u`, typed by the user: irreplaceable, never regenerated (blue,
 *   `.text-user`).
 * - `s`, speech-to-text of their audio: a better STT model can redo it, and a
 *   span may name the recording, `['s', 12, '<attachment id>']` (teal,
 *   `.text-speech`).
 * - `a`, AI-written: redone from its source (the normal text colour,
 *   `.text-ai`).
 *
 * The server resolves this (backend/authorship.py) and sends it on each row as
 * `authorship: { field: spans }` with camelCase field names; this only slices
 * text by it, so every client colours the same characters the same way.
 *
 * Span lengths are Unicode **code points**, which is what `Array.from` walks.
 * Not UTF-16 units (`s.length`): an emoji is two of those and one code point,
 * and counting units would shift every colour after it.
 * backend/tests/fixtures/text_authorship.json pins this for all three clients.
 */

export type TextAuthor = 'u' | 's' | 'a';
export type AuthorSpan = [TextAuthor, number] | [TextAuthor, number, string];
export type AuthorSpans = AuthorSpan[];
export type Authorship = Record<string, AuthorSpans>;
export type AuthoredRun = [TextAuthor, string];

const AUTHORS: readonly string[] = ['u', 's', 'a'];

function parse(spans: unknown): AuthorSpans | null {
  if (!Array.isArray(spans)) return null;
  for (const item of spans) {
    if (
      !Array.isArray(item) ||
      (item.length !== 2 && item.length !== 3) ||
      !AUTHORS.includes(item[0]) ||
      !Number.isInteger(item[1]) ||
      item[1] < 0 ||
      (item.length === 3 && typeof item[2] !== 'string')
    ) {
      return null;
    }
  }
  return spans as AuthorSpans;
}

/** One author per code point of `text`, or null when `spans` don't cover it. */
function pointAuthors(points: string[], spans: unknown): TextAuthor[] | null {
  const parsed = parse(spans);
  if (!parsed) return null;
  const out: TextAuthor[] = [];
  for (const [author, len] of parsed) {
    for (let i = 0; i < len; i++) out.push(author);
  }
  return out.length === points.length ? out : null;
}

/**
 * `text` cut into `[author, substring]` runs. Spans that are missing, malformed
 * or don't cover exactly `text` give the whole of it to `fallback`.
 */
export function authoredRuns(
  text: string,
  spans: unknown,
  fallback: TextAuthor
): AuthoredRun[] {
  const points = Array.from(text);
  const authors = pointAuthors(points, spans);
  if (!authors) return text ? [[fallback, text]] : [];
  const out: AuthoredRun[] = [];
  points.forEach((point, i) => {
    const last = out[out.length - 1];
    if (last && last[0] === authors[i]) last[1] += point;
    else out.push([authors[i], point]);
  });
  return out;
}

/**
 * The spans for one field of a row the server attributed. With spans present
 * but no longer fitting, the text was edited locally and not yet saved back,
 * so it is the user's; with none at all (an older server) `fallback` decides.
 */
function fieldSpans(
  text: string,
  authorship: Authorship | undefined,
  field: string,
  fallback: TextAuthor
): { spans: unknown; fallback: TextAuthor } {
  const spans = authorship?.[field];
  return { spans, fallback: spans === undefined ? fallback : 'u' };
}

export function fieldRuns(
  text: string | null | undefined,
  authorship: Authorship | undefined,
  field: string,
  fallback: TextAuthor
): AuthoredRun[] {
  const value = text ?? '';
  const f = fieldSpans(value, authorship, field, fallback);
  return authoredRuns(value, f.spans, f.fallback);
}

/** The field's one author, or null when it is mixed (or empty). */
export function soleAuthor(runs: AuthoredRun[]): TextAuthor | null {
  const authors = new Set(runs.map(([author]) => author));
  return authors.size === 1 ? [...authors][0] : null;
}

/**
 * The one class for something that can't be painted per character (a
 * textarea, an input): the field's author when it has one, the normal text
 * colour when it is mixed.
 */
export function fieldClass(
  text: string | null | undefined,
  authorship: Authorship | undefined,
  field: string,
  fallback: TextAuthor
): string {
  return authorClass(
    soleAuthor(fieldRuns(text, authorship, field, fallback)) ?? 'a'
  );
}

/**
 * Gives each character of rendered text the author of the source character it
 * came from. Rendering Markdown only removes syntax (`**`, `#`, a link's URL)
 * or swaps a character for itself, never reorders, so the displayed text is
 * an in-order subsequence of the source: walking the source forward and taking
 * the next match recovers where each displayed character came from. Pieces
 * must be fed in source order. One with no match ahead keeps the last author.
 * Swift's `AuthorAligner` is the same walk; the shared fixture's `aligned`
 * cases pin the two together.
 */
export class AuthorAligner {
  private readonly source: string[];
  private readonly authors: TextAuthor[];
  private cursor = 0;
  private last: TextAuthor;

  constructor(text: string, spans: unknown, fallback: TextAuthor) {
    this.source = Array.from(text);
    this.authors =
      pointAuthors(this.source, spans) ?? this.source.map(() => fallback);
    this.last = this.authors[0] ?? fallback;
  }

  /** One author per code point of `rendered`, advancing through the source. */
  authorsFor(rendered: string): TextAuthor[] {
    // A piece that is only whitespace is usually the renderer's own: the line
    // break it puts between list items or table rows. Matching it would jump
    // the cursor to the next newline in the source, past a whole item.
    if (!/\S/u.test(rendered)) return Array.from(rendered).map(() => this.last);
    return Array.from(rendered).map(point => {
      let k = this.cursor;
      while (k < this.source.length && this.source[k] !== point) k++;
      if (k < this.source.length) {
        this.cursor = k + 1;
        this.last = this.authors[k];
      }
      return this.last;
    });
  }

  /** `rendered` cut into runs by author. */
  runsFor(rendered: string): AuthoredRun[] {
    const authors = this.authorsFor(rendered);
    const out: AuthoredRun[] = [];
    Array.from(rendered).forEach((point, i) => {
      const last = out[out.length - 1];
      if (last && last[0] === authors[i]) last[1] += point;
      else out.push([authors[i], point]);
    });
    return out;
  }
}

/** The aligner for one field of a row, with `fieldRuns`'s fallback rule. */
export function fieldAligner(
  text: string,
  authorship: Authorship | undefined,
  field: string,
  fallback: TextAuthor
): AuthorAligner {
  const f = fieldSpans(text, authorship, field, fallback);
  return new AuthorAligner(text, f.spans, f.fallback);
}

/** A journal entry's body with no spans: polished when it no longer is its transcript. */
export function journalContentFallback(entry: {
  content: string;
  rawContent: string | null;
}): TextAuthor {
  return entry.rawContent && entry.rawContent !== entry.content ? 'a' : 'u';
}

export const authorClass = (author: TextAuthor) =>
  author === 'u' ? 'text-user' : author === 's' ? 'text-speech' : 'text-ai';

/** `MessageMarkdown`'s `authored` for a learning card's side: generated unless made by hand. */
export function cardAuthored(
  card: { authorship?: Authorship; sourceType?: string | null },
  field: 'question' | 'answer'
) {
  return {
    authorship: card.authorship,
    field,
    fallback: (card.sourceType === 'manual' ? 'u' : 'a') as TextAuthor,
  };
}
