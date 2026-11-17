import type { Element, ElementContent, Root } from 'hast';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  authorClass,
  fieldAligner,
  fieldRuns,
  soleAuthor,
  type AuthorAligner,
  type Authorship,
  type TextAuthor,
} from '@/lib/authorship';

interface Props {
  content: string;
  /**
   * Colour the text by who produced it (src/lib/authorship.ts): the row's
   * `authorship`, which of its fields `content` is, and who wrote it when the
   * server sent no spans. Without it the text inherits its colour as before.
   */
  authored?: {
    authorship: Authorship | undefined;
    field: string;
    fallback: TextAuthor;
  };
}

/**
 * Wraps every rendered run of text in a span classed by its author. The tree
 * is walked depth-first, which is document order, so the aligner sees the
 * pieces in the order the source holds them.
 */
function paintAuthors(aligner: AuthorAligner) {
  const walk = (node: Root | Element) => {
    const out: ElementContent[] = [];
    for (const child of node.children as ElementContent[]) {
      if (child.type === 'text') {
        for (const [author, text] of aligner.runsFor(child.value)) {
          out.push({
            type: 'element',
            tagName: 'span',
            properties: { className: [authorClass(author)] },
            children: [{ type: 'text', value: text }],
          });
        }
      } else {
        if (child.type === 'element') walk(child);
        out.push(child);
      }
    }
    node.children = out as typeof node.children;
  };
  return () => (tree: Root) => walk(tree);
}

/** Compact markdown renderer for AI chat bubbles — tight spacing, theme-aware. */
export function MessageMarkdown({ content, authored }: Props) {
  const runs = authored
    ? fieldRuns(content, authored.authorship, authored.field, authored.fallback)
    : [];
  const sole = authored ? soleAuthor(runs) : null;
  const mixed = authored && runs.length > 0 && sole === null;
  return (
    <div className={`break-words ${sole ? authorClass(sole) : ''}`}>
      <ReactMarkdown
        rehypePlugins={
          mixed
            ? [
                paintAuthors(
                  fieldAligner(
                    content,
                    authored.authorship,
                    authored.field,
                    authored.fallback
                  )
                ),
              ]
            : []
        }
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
          ul: ({ children }) => (
            <ul className="list-disc pl-5 mb-2 last:mb-0 space-y-0.5">
              {children}
            </ul>
          ),
          ol: ({ children }) => (
            <ol className="list-decimal pl-5 mb-2 last:mb-0 space-y-0.5">
              {children}
            </ol>
          ),
          strong: ({ children }) => (
            <strong className="font-bold">{children}</strong>
          ),
          h1: ({ children }) => (
            <h1 className="text-base font-bold mt-3 first:mt-0 mb-1">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-base font-bold mt-3 first:mt-0 mb-1">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-sm font-bold mt-3 first:mt-0 mb-1">
              {children}
            </h3>
          ),
          h4: ({ children }) => (
            <h4 className="text-sm font-bold mt-2 first:mt-0 mb-1">
              {children}
            </h4>
          ),
          code: ({ children }) => (
            <code className="rounded text-left bg-white/10 px-1 py-0.5 font-mono text-[0.85em]">
              {children}
            </code>
          ),
          pre: ({ children }) => (
            // em-relative (0.75em == 0.75rem == text-xs at the 16px base) so
            // code blocks scale with an ambient font-size — e.g. the Learning
            // card's font-size shortcut — instead of staying fixed.
            <pre className="mb-2 last:mb-0 p-2 rounded bg-black/30 text-left overflow-x-auto text-[0.75em] [&_code]:bg-transparent [&_code]:p-0">
              {children}
            </pre>
          ),
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-white/20 pl-3 mb-2 last:mb-0 text-[var(--color-text-muted)]">
              {children}
            </blockquote>
          ),
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="text-[var(--color-primary)] underline"
            >
              {children}
            </a>
          ),
          hr: () => <hr className="border-white/10 my-2" />,
          table: ({ children }) => (
            <div className="overflow-x-auto mb-2 last:mb-0">
              <table className="border-collapse text-[0.75em]">
                {children}
              </table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border border-white/10 px-2 py-1 text-left font-bold">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-white/10 px-2 py-1">{children}</td>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
