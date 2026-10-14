export type GroundingSource = { n: number; path: string; heading_path: string; anchor: string | null };

export default function GroundingSources({ sources }: { sources: GroundingSource[] }) {
  if (!sources.length) return null;
  return (
    <ol aria-label="Cited sources" className="mt-2 flex flex-col gap-1 text-sm">
      {sources.map((source) => (
        <li key={source.n}>
          <span>[{source.n}]</span> <span className="font-mono">{source.path}</span>{" "}
          <span className="text-muted-foreground">{source.heading_path}</span>
        </li>
      ))}
    </ol>
  );
}
