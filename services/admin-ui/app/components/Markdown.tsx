import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { tomorrow } from 'react-syntax-highlighter/dist/cjs/styles/prism';
import Mermaid from './Mermaid';
import ServiceTalaGraph from './ServiceTalaGraph';

interface MarkdownProps {
  content: string;
  isStreaming?: boolean;
}

// Slugs a heading's plain text into an anchor id using the same rule as the
// former Reader API's server-side `slugify` (since removed): lowercase,
// collapse runs of non-alphanumeric characters to a single hyphen, trim
// leading/trailing hyphens, fall back to "section" if that leaves nothing.
// Headings need stable, matching ids so DocView's table-of-contents `#anchor`
// links land.
function slugifyHeading(text: string): string {
  const slug = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return slug || 'section';
}

// Mirrors indexer.py's `_dedupe_slug`: the first occurrence of a slug stays
// bare, repeats get `-2`, `-3`, ... . `used` is keyed on the *base* slug (not
// on the final, possibly-suffixed id already handed out), exactly like the
// API -- so a literal heading text that happens to collide with a generated
// suffix (e.g. two "Example" headings plus a heading literally titled
// "Example 2") is not specially disambiguated; that's a latent quirk in the
// API's own algorithm and is intentionally reproduced here rather than
// "fixed" locally, so the two stay in lockstep.
function dedupeSlug(used: Record<string, number>, text: string): string {
  const slug = slugifyHeading(text);
  const count = (used[slug] ?? 0) + 1;
  used[slug] = count;
  return count === 1 ? slug : `${slug}-${count}`;
}

function headingPlainText(node: React.ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(headingPlainText).join('');
  if (React.isValidElement(node)) {
    const props = node.props as { children?: React.ReactNode };
    return headingPlainText(props.children);
  }
  return '';
}

const Markdown: React.FC<MarkdownProps> = ({ content, isStreaming = false }) => {
  // Fresh per render (component body re-runs on every render of this
  // element): a new document must not inherit slug counts from whatever was
  // rendered before it. Keyed on the base slug, mirroring indexer.py's
  // per-document `used` dict in `render_html`.
  const usedSlugs: Record<string, number> = {};
  // Define markdown components
  const MarkdownComponents: React.ComponentProps<typeof ReactMarkdown>['components'] = {
    pre({ children }) {
      const diagram = React.Children.toArray(children).some(child =>
        React.isValidElement<{ className?: string }>(child)
        && /language-(mermaid|d2)\b/i.test(child.props.className ?? ''));
      // Diagrams must not inherit code-block fonts, whitespace, or typography.
      return diagram ? <div className="not-prose">{children}</div> : <pre>{children}</pre>;
    },
    p({ children, ...props }: { children?: React.ReactNode }) {
      return <p className="mb-3 text-sm leading-relaxed dark:text-white" {...props}>{children}</p>;
    },
    h1({ children, ...props }: { children?: React.ReactNode }) {
      const id = dedupeSlug(usedSlugs, headingPlainText(children));
      return <h1 id={id} className="text-xl font-bold mt-6 mb-3 dark:text-white" {...props}>{children}</h1>;
    },
    h2({ children, ...props }: { children?: React.ReactNode }) {
      const id = dedupeSlug(usedSlugs, headingPlainText(children));
      // Special styling for ReAct headings
      if (children && typeof children === 'string') {
        const text = children.toString();
        if (text.includes('Thought') || text.includes('Action') || text.includes('Observation') || text.includes('Answer')) {
          return (
            <h2
              id={id}
              className={`text-base font-bold mt-5 mb-3 p-2 rounded ${
                text.includes('Thought') ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-800 dark:text-blue-300' :
                text.includes('Action') ? 'bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-300' :
                text.includes('Observation') ? 'bg-amber-100 dark:bg-amber-900/30 text-amber-800 dark:text-amber-300' :
                text.includes('Answer') ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-800 dark:text-purple-300' :
                'dark:text-white'
              }`}
              {...props}
            >
              {children}
            </h2>
          );
        }
      }
      return <h2 id={id} className="text-lg font-bold mt-5 mb-3 dark:text-white" {...props}>{children}</h2>;
    },
    h3({ children, ...props }: { children?: React.ReactNode }) {
      const id = dedupeSlug(usedSlugs, headingPlainText(children));
      return <h3 id={id} className="text-base font-semibold mt-4 mb-2 dark:text-white" {...props}>{children}</h3>;
    },
    h4({ children, ...props }: { children?: React.ReactNode }) {
      const id = dedupeSlug(usedSlugs, headingPlainText(children));
      return <h4 id={id} className="text-sm font-semibold mt-3 mb-2 dark:text-white" {...props}>{children}</h4>;
    },
    ul({ children, ...props }: { children?: React.ReactNode }) {
      return <ul className="list-disc pl-6 mb-4 text-sm dark:text-white space-y-2" {...props}>{children}</ul>;
    },
    ol({ children, ...props }: { children?: React.ReactNode }) {
      return <ol className="list-decimal pl-6 mb-4 text-sm dark:text-white space-y-2" {...props}>{children}</ol>;
    },
    li({ children, ...props }: { children?: React.ReactNode }) {
      return <li className="mb-2 text-sm leading-relaxed dark:text-white" {...props}>{children}</li>;
    },
    a({ children, href, ...props }: { children?: React.ReactNode; href?: string }) {
      return (
        <a
          href={href}
          className="text-purple-600 dark:text-purple-400 hover:underline font-medium"
          target="_blank"
          rel="noopener noreferrer"
          {...props}
        >
          {children}
        </a>
      );
    },
    blockquote({ children, ...props }: { children?: React.ReactNode }) {
      return (
        <blockquote
          className="border-l-4 border-gray-300 dark:border-gray-700 pl-4 py-1 text-gray-700 dark:text-gray-300 italic my-4 text-sm"
          {...props}
        >
          {children}
        </blockquote>
      );
    },
    table({ children, ...props }: { children?: React.ReactNode }) {
      return (
        <div className="overflow-x-auto my-6 rounded-md">
          <table className="min-w-full text-sm border-collapse" {...props}>
            {children}
          </table>
        </div>
      );
    },
    thead({ children, ...props }: { children?: React.ReactNode }) {
      return <thead className="bg-gray-100 dark:bg-gray-800" {...props}>{children}</thead>;
    },
    tbody({ children, ...props }: { children?: React.ReactNode }) {
      return <tbody className="divide-y divide-gray-200 dark:divide-gray-700" {...props}>{children}</tbody>;
    },
    tr({ children, ...props }: { children?: React.ReactNode }) {
      return <tr className="hover:bg-gray-50 dark:hover:bg-gray-900" {...props}>{children}</tr>;
    },
    th({ children, ...props }: { children?: React.ReactNode }) {
      return (
        <th
          className="px-4 py-3 text-left font-medium text-gray-700 dark:text-gray-300"
          {...props}
        >
          {children}
        </th>
      );
    },
    td({ children, ...props }: { children?: React.ReactNode }) {
      return <td className="px-4 py-3 border-t border-gray-200 dark:border-gray-700" {...props}>{children}</td>;
    },
    code(props: {
      inline?: boolean;
      className?: string;
      children?: React.ReactNode;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      [key: string]: any; // Using any here as it's required for ReactMarkdown components
    }) {
      const { inline, className, children, ...otherProps } = props;
      const match = /language-(\w+)/.exec(className || '');
      const codeContent = children ? String(children).replace(/\n$/, '') : '';

      if (!inline && match?.[1].toLowerCase() === 'd2') {
        if (isStreaming) return <p role="status">Diagram will appear when the answer is complete.</p>;
        return <div className="not-prose my-6"><ServiceTalaGraph source={codeContent} label="Component architecture rendered with D2 and TALA" /></div>;
      }

      // Handle Mermaid diagrams
      if (!inline && match && match[1].toLowerCase() === 'mermaid') {
        if (isStreaming) return <p role="status" className="text-sm text-muted-foreground">Diagram will appear when the answer is complete.</p>;
        return (
          <div className="my-8 bg-gray-50 dark:bg-gray-800 rounded-md overflow-hidden shadow-sm">
            <Mermaid
              chart={codeContent}
              className="w-full max-w-full"
              zoomingEnabled={true}
            />
          </div>
        );
      }

      // Handle code blocks
      if (!inline && match) {
        return (
          <div className="my-6 rounded-md overflow-x-auto text-sm shadow-sm">
            <div className="bg-gray-800 text-gray-200 px-5 py-2 text-sm flex justify-between items-center">
              <span>{match[1]}</span>
              <button
                onClick={() => {
                  navigator.clipboard.writeText(codeContent);
                }}
                className="text-gray-400 hover:text-white"
                title="Copy code"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="h-5 w-5"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                  />
                </svg>
              </button>
            </div>
            <SyntaxHighlighter
              language={match[1]}
              style={tomorrow}
              className="!text-sm"
              customStyle={{ margin: 0, borderRadius: '0 0 0.375rem 0.375rem', padding: '1rem' }}
              showLineNumbers={true}
              wrapLines={true}
              wrapLongLines={true}
              {...otherProps}
            >
              {codeContent}
            </SyntaxHighlighter>
          </div>
        );
      }

      // Handle inline code. break-words: an inline code span has no scroll
      // container of its own (unlike the table/code-block wrappers above),
      // so an unbroken token (hash, token, long identifier) would otherwise
      // overflow straight through the surrounding prose and force horizontal
      // scroll on the whole page at narrow widths — confirmed live at 375px.
      return (
        <code
          className={`${className} font-mono bg-gray-100 dark:bg-gray-800 px-2 py-0.5 rounded text-pink-500 dark:text-pink-400 text-sm break-words`}
          {...otherProps}
        >
          {children}
        </code>
      );
    },
  };

  return (
    <div className="prose prose-base dark:prose-invert max-w-none px-2 py-4">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={MarkdownComponents}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
};

export default Markdown;
