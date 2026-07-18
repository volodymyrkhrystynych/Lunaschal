import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface Props {
  content: string;
}

/** Compact markdown renderer for AI chat bubbles — tight spacing, theme-aware. */
export function MessageMarkdown({ content }: Props) {
  return (
    <div className="break-words">
      <ReactMarkdown
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
            <pre className="mb-2 last:mb-0 p-2 rounded bg-black/30 text-left overflow-x-auto text-xs [&_code]:bg-transparent [&_code]:p-0">
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
              <table className="border-collapse text-xs">{children}</table>
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
