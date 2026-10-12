import { lineDiff, MAX_DIFF_CELLS } from '../lineDiff';

test('same, removed and added lines in order', () => {
  expect(lineDiff('a\nb\nc', 'a\nx\nc').lines).toEqual([
    { kind: 'same', text: 'a' }, { kind: 'removed', text: 'b' }, { kind: 'added', text: 'x' }, { kind: 'same', text: 'c' }]);
});

test('identical texts have no changes; an insertion keeps the common lines', () => {
  expect(lineDiff('a\nb', 'a\nb').lines.every((line) => line.kind === 'same')).toBe(true);
  expect(lineDiff('a\nc', 'a\nb\nc').lines.map((line) => line.kind)).toEqual(['same', 'added', 'same']);
  expect(lineDiff('', '# New').lines.map((line) => line.kind)).toEqual(['removed', 'added']);
});

test('a change too large to compare is shown as removed then added', () => {
  const size = Math.ceil(Math.sqrt(MAX_DIFF_CELLS)) + 1;
  const before = Array.from({ length: size }, (_, i) => `old ${i}`).join('\n');
  const after = Array.from({ length: size }, (_, i) => `new ${i}`).join('\n');
  const result = lineDiff(`same\n${before}\nend`, `same\n${after}\nend`);
  expect(result.approximate).toBe(true);
  expect(result.lines[0]).toEqual({ kind: 'same', text: 'same' });
  expect(result.lines.at(-1)).toEqual({ kind: 'same', text: 'end' });
  expect(result.lines.filter((line) => line.kind === 'removed')).toHaveLength(size);
  expect(result.lines.filter((line) => line.kind === 'added')).toHaveLength(size);
});

test('a side with no lines is all removed or all added without comparing', () => {
  expect(lineDiff('a\nb', 'a\nb\nc').lines.map((line) => line.kind)).toEqual(['same', 'same', 'added']);
  expect(lineDiff('a\nb\nc', 'a').lines.map((line) => line.kind)).toEqual(['same', 'removed', 'removed']);
  expect(lineDiff('x', 'x').lines).toEqual([{ kind: 'same', text: 'x' }]);
});
