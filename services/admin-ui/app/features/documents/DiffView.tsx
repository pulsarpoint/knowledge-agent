import { useMemo } from "react";
import { lineDiff } from "./lineDiff";

/** The draft against the live version, line by line: added lines in <ins>, removed lines in <del>. */
export default function DiffView({ before, after }: { before: string; after: string }) {
  const { lines, approximate } = useMemo(() => lineDiff(before, after), [before, after]);
  if (lines.every((line) => line.kind === "same"))
    return <p className="text-sm text-muted-foreground">No changes against the live version.</p>;
  return (
    <div className="flex flex-col gap-2">
      {approximate && (
        <p className="text-xs text-muted-foreground">
          These texts are too long to compare line by line; the changed part is shown as removed, then added.
        </p>
      )}
      <pre
        aria-label="Changes against the live version"
        className="max-h-[32rem] overflow-auto rounded-lg border p-2 text-xs leading-5"
      >
        {lines.map((line, index) =>
          line.kind === "added" ? (
            <ins key={index} className="block bg-green-500/10 no-underline before:mr-2 before:content-['+']">
              {line.text}
            </ins>
          ) : line.kind === "removed" ? (
            <del key={index} className="block bg-red-500/10 no-underline before:mr-2 before:content-['−']">
              {line.text}
            </del>
          ) : (
            <span key={index} className="block before:mr-2 before:content-['_'] before:invisible">
              {line.text}
            </span>
          ),
        )}
      </pre>
    </div>
  );
}
