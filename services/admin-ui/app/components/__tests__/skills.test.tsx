import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import SkillsView from '../SkillsView';
import type { Skill } from '../../lib/skills';

afterEach(() => vi.unstubAllGlobals());
const initial: Skill = { id: 'skill-1', name: 'inspect-logs', description: 'Investigate service errors', instructions: '# Inspect logs\n\nRead recent errors.\n', enabled: true, revision: 1, created_at: '2026-09-09T10:00:00Z', updated_at: '2026-09-09T10:00:00Z' };

function setup(rows: Skill[] = [], conflict = false) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method) {
      if (conflict) return new Response(JSON.stringify({ error: { message: 'This skill changed. Cancel and reopen it before saving.' } }), { status: 409 });
      const value = JSON.parse(init.body as string);
      if (init.method === 'POST') {
        const saved = { ...initial, ...value }; rows.push(saved);
        return new Response(JSON.stringify(saved), { status: 201 });
      }
      const saved = { ...rows[0], ...value, revision: rows[0].revision + 1 };
      rows = [saved, ...rows.slice(1)];
      return new Response(JSON.stringify(saved));
    }
    if (url === '/api/skills') return new Response(JSON.stringify({ skills: rows.map(({ instructions, ...summary }) => summary) }));
    return new Response(JSON.stringify(rows.find((row) => url.endsWith(row.id))));
  });
  vi.stubGlobal('fetch', fetch);
  render(<SkillsView />);
  return fetch;
}

test('create, preview, edit, disable and re-enable a skill', async () => {
  const fetch = setup();
  await screen.findByText('No skills yet');
  fireEvent.click(screen.getByRole('button', { name: 'Add skill' }));
  fireEvent.change(screen.getByLabelText('Skill name'), { target: { value: initial.name } });
  fireEvent.change(screen.getByLabelText('Description'), { target: { value: initial.description } });
  fireEvent.change(screen.getByLabelText('Instructions (Markdown)'), { target: { value: initial.instructions } });
  fireEvent.click(screen.getByRole('button', { name: 'Preview instructions' }));
  expect(screen.getByRole('heading', { name: 'Inspect logs' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Create skill' }));
  await screen.findByRole('switch', { name: 'inspect-logs enabled' });
  const create = fetch.mock.calls.find(([, init]) => init?.method === 'POST')!;
  expect(create[0]).toBe('/api/skills');
  expect(JSON.parse(create[1]!.body as string)).toEqual({ name: initial.name, description: initial.description, instructions: initial.instructions, enabled: true });
  fireEvent.click(screen.getByRole('switch', { name: 'inspect-logs enabled' }));
  await waitFor(() => expect(screen.getByRole('switch', { name: 'inspect-logs enabled' })).not.toBeChecked());
  fireEvent.click(screen.getByRole('button', { name: 'Edit inspect-logs' }));
  expect(await screen.findByLabelText('Instructions (Markdown)')).toHaveValue(initial.instructions);
  expect(screen.getByRole('switch', { name: 'Enabled' })).not.toBeChecked();
  fireEvent.change(screen.getByLabelText('Instructions (Markdown)'), { target: { value: 'Updated steps\n' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.queryByLabelText('Instructions (Markdown)')).not.toBeInTheDocument());
  const update = fetch.mock.calls.find(([, init]) => init?.method === 'PUT')!;
  expect(JSON.parse(update[1]!.body as string)).toMatchObject({ instructions: 'Updated steps\n', enabled: false, revision: 2 });
  fireEvent.click(screen.getByRole('switch', { name: 'inspect-logs enabled' }));
  await waitFor(() => expect(screen.getByRole('switch', { name: 'inspect-logs enabled' })).toBeChecked());
  const patches = fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH');
  expect(patches.map(([, init]) => JSON.parse(init!.body as string))).toEqual([{ enabled: false, revision: 1 }, { enabled: true, revision: 3 }]);
});

test('filters names and descriptions and keeps disabled skills visible', async () => {
  setup([initial, { ...initial, id: 'skill-2', name: 'check-health', description: 'Review health checks', enabled: false }]);
  const filter = await screen.findByLabelText('Filter skills');
  fireEvent.change(filter, { target: { value: 'health checks' } });
  expect(screen.getByRole('button', { name: 'Edit check-health' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Edit inspect-logs' })).not.toBeInTheDocument();
  expect(screen.getByRole('switch', { name: 'check-health enabled' })).not.toBeChecked();
  fireEvent.change(filter, { target: { value: 'missing' } });
  expect(screen.getByText('No skills match these filters.')).toBeInTheDocument();
});

test('a stale save keeps the edited instructions and cancel sends no additional write', async () => {
  const fetch = setup([initial], true);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit inspect-logs' }));
  fireEvent.change(await screen.findByLabelText('Instructions (Markdown)'), { target: { value: 'Unsaved changes' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
  await screen.findByText('This skill changed. Cancel and reopen it before saving.');
  expect(screen.getByLabelText('Instructions (Markdown)')).toHaveValue('Unsaved changes');
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  await waitFor(() => expect(screen.queryByLabelText('Instructions (Markdown)')).not.toBeInTheDocument());
  expect(fetch.mock.calls.filter(([, init]) => init?.method)).toHaveLength(1);
});

test('failed status changes leave the current status intact', async () => {
  setup([initial], true);
  fireEvent.click(await screen.findByRole('switch', { name: 'inspect-logs enabled' }));
  await screen.findByText('This skill changed. Cancel and reopen it before saving.');
  expect(screen.getByRole('switch', { name: 'inspect-logs enabled' })).toBeChecked();
});

test('unavailable skill storage shows an error and disables creation', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ error: { message: 'Skill storage unavailable' } }), { status: 503 })));
  render(<SkillsView />);
  await screen.findByText('Skill storage unavailable');
  expect(screen.getByRole('button', { name: 'Add skill' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled();
});
