import assert from 'node:assert/strict';
import { test } from 'node:test';
import { validate } from './validate.mjs';

test('real parser accepts the supported diagram types and quoted punctuation', async () => {
  for (const chart of [
    'flowchart LR\n A["API (v1)"] --> B["PostgreSQL"]',
    'sequenceDiagram\n participant U as User\n U->>API: Request\n API-->>U: Response',
    'stateDiagram-v2\n [*] --> Ready\n Ready --> Running',
  ]) assert.deepEqual(await validate(chart), { valid: true });
});

test('syntax errors produce useful diagnostics and do not poison the next parse', async () => {
  const result = await validate('flowchart LR\n A[unclosed --> B');
  assert.equal(result.valid, false);
  assert.match(result.error, /parse|syntax/i);
  assert.deepEqual(await validate('flowchart LR\n A --> B'), { valid: true });
});

test('empty and oversized inputs are rejected', async () => {
  assert.equal((await validate('')).valid, false);
  assert.equal((await validate('x'.repeat(40001))).valid, false);
});
