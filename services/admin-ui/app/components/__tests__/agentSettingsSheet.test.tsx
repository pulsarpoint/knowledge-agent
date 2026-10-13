import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import AgentSettingsSheet from '../AgentSettingsSheet';
import { defaultSettings, type AgentSettings } from '~/lib/agentSettings';
import type { SessionDetail } from '~/lib/sessions';
import { options } from '~/lib/__tests__/agentOptionsFixture';

afterEach(() => vi.unstubAllGlobals());

function Harness({ session = null, onStartNew = () => {}, message = '' }: { session?: SessionDetail | null; onStartNew?: () => void; message?: string }) {
  const [settings, setSettings] = useState<AgentSettings>(defaultSettings(options));
  return <>
    <AgentSettingsSheet open onOpenChange={() => {}} documentId="doc-1" documentTitle="Runbook" options={options} settings={settings}
      onChange={patch => setSettings(current => ({ ...current, ...patch }))} previewMessage={message} session={session} onStartNew={onStartNew} />
    <output aria-label="Settings state">{JSON.stringify(settings)}</output>
  </>;
}

test('editable: linked resources ticked, unusable server disabled, a newly applying tool starts ticked', () => {
  render(<Harness />);
  expect(screen.getByRole('heading', { name: 'Agent settings' })).toBeInTheDocument();
  expect(screen.getByText('What the agent may use in the next conversation on “Runbook”.')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'web-1' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'prod-1' })).toBeDisabled();
  expect(screen.getByText('SSH is not set up for this server.')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'Read the document' })).toBeDisabled();
  expect(screen.queryByRole('checkbox', { name: 'Read file' })).toBeNull();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Shop' }));
  expect(screen.getByRole('checkbox', { name: 'Read file' })).toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: 'SSH' }));
  expect(JSON.parse(screen.getByLabelText('Settings state').textContent!).off).toEqual(['ssh_run']);
});

test('Preview sends the message box text, or a placeholder when it is empty', async () => {
  const bodies: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
    bodies.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify({ config: { model: { name: 'Claude' } }, first_message: 'x' }), { status: 200 });
  }));
  const { rerender } = render(<Harness />);
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  expect((await screen.findByLabelText('Configuration preview')).textContent).toContain('"name": "Claude"');
  expect((bodies[0] as { instruction: string }).instruction).toBe('(your next message)');
  rerender(<Harness message="Add log rotation." />);
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await waitFor(() => expect((bodies[1] as { instruction: string }).instruction).toBe('Add log rotation.'));
});

test('read-only for an open run: its saved configuration, and Start new chat with these settings', () => {
  const onStartNew = vi.fn();
  const session = { id: 'r1', status: 'waiting', config: { model: { name: 'GPT' } } } as unknown as SessionDetail;
  render(<Harness session={session} onStartNew={onStartNew} />);
  expect(screen.getByText("This conversation's settings. They cannot change while it is open.")).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'web-1' })).toBeDisabled();
  expect(screen.getByLabelText('Run configuration').textContent).toContain('"name": "GPT"');
  expect(screen.queryByRole('button', { name: 'Preview' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Start new chat with these settings' }));
  expect(onStartNew).toHaveBeenCalledOnce();
});

test('read-only: Advanced still opens and shows the run\'s system prompt, disabled', () => {
  const session = { id: 'r1', status: 'waiting', config: {} } as unknown as SessionDetail;
  render(<Harness session={session} />);
  const advanced = screen.getByRole('button', { name: 'Advanced' });
  expect(advanced).toBeEnabled();
  fireEvent.click(advanced);
  expect(screen.getByRole('textbox', { name: 'System prompt' })).toBeDisabled();
  expect(screen.getByRole('textbox', { name: 'System prompt' })).toHaveValue('You revise carefully.');
});
