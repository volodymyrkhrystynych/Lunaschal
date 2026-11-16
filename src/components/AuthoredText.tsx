import {
  authorClass,
  fieldRuns,
  type Authorship,
  type TextAuthor,
} from '@/lib/authorship';

interface Props {
  text: string | null | undefined;
  authorship: Authorship | undefined;
  field: string;
  /** Who wrote it when the server sent no spans for this field. */
  fallback: TextAuthor;
}

/**
 * A text field coloured by who wrote each part: the user's own words blue,
 * a model's the normal text colour. Renders inline spans only, so it drops into
 * whatever block (and `whitespace-pre-wrap`) the caller already had.
 */
export function AuthoredText({ text, authorship, field, fallback }: Props) {
  return (
    <>
      {fieldRuns(text, authorship, field, fallback).map(([author, part], i) => (
        <span key={i} className={authorClass(author)} data-author={author}>
          {part}
        </span>
      ))}
    </>
  );
}
