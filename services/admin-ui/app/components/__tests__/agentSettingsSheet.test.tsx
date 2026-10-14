import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import AgentSettingsSheet from '~/features/sessions/AgentSettingsSheet';
import { defaultSettings, type AgentSettings } from '~/features/sessions/agentSettings';
import type { SessionDetail } from '~/features/sessions/api';
import { options } from '~/lib/__tests__/agentOptionsFixture';
import { agentOptions } from '~/lib/__tests__/agentScopeFixture';
import type { AgentOptions, ChatScope } from '~/features/sessions/api';

afterEach(() => vi.unstubAllGlobals());

const docScope: ChatScope = { kind: 'document', id: 'doc-1', title: 'Runbook' };
const agentScope: ChatScope = { kind: 'agent', id: 'ops', name: 'Operations' };

function Harness({ session = null, onStartNew = () => {}, message = '', scope = docScope, opts = options }:
  { session?: SessionDetail | null; onStartNew?: () => void; message?: string; scope?: ChatScope; opts?: AgentOptions }) {
  const [settings, setSettings] = useState<AgentSettings>(defaultSettings(opts));
  return <>
    <AgentSettingsSheet open onOpenChange={() => {}} scope={scope} options={opts} settings={settings}
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
  expect(screen.getByRole('checkbox', { name: 'Save a document' })).toBeChecked();
  expect(screen.queryByRole('checkbox', { name: 'Read file' })).toBeNull();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Shop' }));
  expect(screen.getByRole('checkbox', { name: 'Read file' })).toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: 'SSH' }));
  expect(JSON.parse(screen.getByLabelText('Settings state').textContent!).off).toEqual(['ssh_run']);
});

test('the document is a checked, disabled resource, and the prompt list ends with Own prompt', () => {
  render(<Harness />);
  expect(screen.getByRole('checkbox', { name: 'Runbook' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'Runbook' })).toBeDisabled();
  expect(screen.getByText('The agent may change it (as a draft).')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Advanced' }));
  fireEvent.click(screen.getByRole('combobox', { name: 'Prompt' }));
  expect(screen.getAllByRole('option').map(item => item.textContent)).toEqual(['Careful reviser', 'Revise a document', 'Own prompt']);
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
  expect((bodies[0] as { message: string }).message).toBe('(your next message)');
  rerender(<Harness message="Add log rotation." />);
  fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await waitFor(() => expect((bodies[1] as { message: string }).message).toBe('Add log rotation.'));
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
  expect(screen.getByRole('textbox', { name: 'System prompt' })).toHaveValue('Read with get_document. Change with edit_document or save_document.');
});

test('a saved agent shows its model and prompt read-only and offers only narrowing', () => {
  render(<Harness scope={agentScope} opts={agentOptions} />);
  expect(screen.getByText('What the agent may use in the next conversation.')).toBeInTheDocument();
  expect(screen.getByText('GPT')).toBeInTheDocument();
  expect(screen.queryByRole('combobox', { name: 'Model' })).toBeNull();
  expect(screen.getAllByText('Change them in General.').length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole('button', { name: 'Advanced' }));
  expect(screen.getByText('You operate servers.')).toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: 'System prompt' })).toBeNull();
  expect(screen.queryByRole('combobox', { name: 'Prompt' })).toBeNull();
  fireEvent.click(screen.getByRole('checkbox', { name: 'web-1' }));
  expect(screen.getByRole('checkbox', { name: 'SSH' })).toBeChecked();
  expect(screen.getByRole('switch', { name: 'Allow subagents' })).toBeInTheDocument();
});

test("an open agent run shows the run's model and prompt, not the agent's current ones", () => {
  const session = { id: 'r1', status: 'waiting', model_name: 'GPT then', prompt_snapshot: { system_prompt: 'You operated servers.' },
    config: {} } as unknown as SessionDetail;
  render(<Harness scope={agentScope} opts={agentOptions} session={session} />);
  expect(screen.getByText('GPT then')).toBeInTheDocument();
  expect(screen.queryByText('GPT')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Advanced' }));
  expect(screen.getByLabelText('Agent prompt').textContent).toBe('You operated servers.');
});

test("a fixed agent's resources are not offered", () => {
  const fixed = { ...agentOptions, agent: { ...agentOptions.agent!, resources_fixed: true, subagents_allowed: false } };
  render(<Harness scope={agentScope} opts={fixed} />);
  expect(screen.getByText("This agent's resources are fixed.")).toBeInTheDocument();
  expect(screen.queryByRole('checkbox', { name: 'web-1' })).toBeNull();
  expect(screen.queryByRole('switch', { name: 'Allow subagents' })).toBeNull();
  expect(screen.getByRole('checkbox', { name: 'SSH' })).toBeChecked();
});
