export type DiffLine = { kind: "same" | "added" | "removed"; text: string };
/** Above this many line pairs (changed lines before × after) the middle is not compared: it would freeze the page. */
export const MAX_DIFF_CELLS = 4_000_000;

const mark =
  (kind: DiffLine["kind"]) =>
  (text: string): DiffLine => ({ kind, text });

/** A line diff (longest common subsequence) after trimming the common start and end. When the changed middle is too
 * large to compare, it is shown as removed, then added (`approximate`). */
export function lineDiff(before: string, after: string): { lines: DiffLine[]; approximate: boolean } {
  const a = before.split("\n"),
    b = after.split("\n");
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length,
    endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  const head = a.slice(0, start).map(mark("same")),
    tail = a.slice(endA).map(mark("same"));
  const x = a.slice(start, endA),
    y = b.slice(start, endB);
  if (x.length * y.length > MAX_DIFF_CELLS) {
    return { lines: [...head, ...x.map(mark("removed")), ...y.map(mark("added")), ...tail], approximate: true };
  }
  const n = x.length,
    m = y.length;
  if (n === 0 || m === 0)
    return { lines: [...head, ...x.map(mark("removed")), ...y.map(mark("added")), ...tail], approximate: false };
  const table = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i][j] = x[i] === y[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const middle: DiffLine[] = [];
  let i = 0,
    j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) {
      middle.push(mark("same")(x[i]));
      i++;
      j++;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      middle.push(mark("removed")(x[i]));
      i++;
    } else {
      middle.push(mark("added")(y[j]));
      j++;
    }
  }
  while (i < n) middle.push(mark("removed")(x[i++]));
  while (j < m) middle.push(mark("added")(y[j++]));
  return { lines: [...head, ...middle, ...tail], approximate: false };
}
