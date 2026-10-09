import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import OperationsView from '../OperationsView';
import ServerSSHForm from '../ServerSSHForm';
import * as api from '../../lib/operations';
import { getAgentConfig, getRunResourceOptions, type AgentProfile } from '../../lib/profiles';
import { createTestSession } from '../../lib/testSessions';
import type { SessionDetail } from '../../lib/generations';
vi.mock('../../lib/operations');
vi.mock('../../lib/profiles');
vi.mock('../../lib/testSessions');
vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));
const server: api.SSHServer = { id: 'server-1', name: 'Staging', hostname: 'staging.internal', port: 22, username: 'operator', revision: 2, ssh_allowed: true, enabled: true, allow_sudo: false, known_hosts: 'staging.internal ssh-ed25519 test', has_private_key: true, last_tested_at: null, last_test_error: null };
const settings: api.OperationsSettings = { revision: 1, instructions: '', server_ids: [], mcp_server_ids: [], skill_ids: [] };
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getOperationsSettings).mockResolvedValue({ settings, configs: [], mcp_servers: [], servers: [server, { ...server, id: 'server-2', name: 'Disabled', ssh_allowed: false }], skills: [{ id: 'skill-1', name: 'service-checks', description: 'Verify services', enabled: true }] });
  vi.mocked(getRunResourceOptions).mockResolvedValue({ resources: 'fixed' });
  vi.mocked(api.getSSH).mockResolvedValue(server);
  vi.mocked(getAgentConfig).mockResolvedValue({ id: 'operations', system_prompt: { content: 'Use the saved system prompt.', version: 2 }, ssh: { enabled: true, servers: [] } });
});
describe('Operations configuration', () => {
  it('previews saved config and the current prompt without running a task', async () => {
    render(<MemoryRouter><OperationsView view="test" agentId="custom-agent" /></MemoryRouter>);
    await waitFor(() => expect(screen.queryByText('Loading agent configuration…')).not.toBeInTheDocument());
    expect(getAgentConfig).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Inspect service status.\nDo not restart it.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Show config' }));
    const preview = await screen.findByLabelText('Agent configuration');
    expect(getAgentConfig).toHaveBeenCalledWith('custom-agent');
    expect(JSON.parse(preview.textContent!)).toMatchObject({
      prompt: 'Inspect service status.\nDo not restart it.',
      config: { system_prompt: { content: 'Use the saved system prompt.', version: 2 } },
    });
    expect(createTestSession).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Updated task' } });
    expect(getAgentConfig).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText('Agent configuration')).not.toBeInTheDocument();
    vi.mocked(getAgentConfig).mockResolvedValue({ system_prompt: { content: 'New active prompt', version: 3 } });
    fireEvent.click(screen.getByRole('button', { name: 'Show config' }));
    expect(await screen.findByLabelText('Agent configuration')).toHaveTextContent('New active prompt');
  });

  it('allows preview for disabled agents and empty tasks, and handles loading failures', async () => {
    vi.mocked(getAgentConfig).mockRejectedValueOnce(new Error('Administration database unavailable'));
    render(<MemoryRouter><OperationsView view="test" available={false} /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: 'Show config' }));
    expect(await screen.findByText('Administration database unavailable')).toBeVisible();
    expect(screen.queryByLabelText('Agent configuration')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    fireEvent.click(screen.getByRole('button', { name: 'Show config' }));
    expect(JSON.parse((await screen.findByLabelText('Agent configuration')).textContent!).prompt).toBe('');
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.getByRole('button', { name: 'Run task' })).toBeDisabled();
  });

  it('does not preview unsaved configuration', async () => {
    render(<MemoryRouter><OperationsView view="test" unsavedProfile /></MemoryRouter>);
    await screen.findByText('Save or discard changes in the configuration tabs before starting a task.');
    expect(screen.getByRole('button', { name: 'Show config' })).toBeDisabled();
    expect(getAgentConfig).not.toHaveBeenCalled();
  });

  it('allows a task on a configured server without an additional enable switch', async () => {
    const data = await api.getOperationsSettings();
    data.settings = { ...settings, server_ids: [server.id] };
    vi.mocked(api.getOperationsSettings).mockResolvedValue(data);
    render(<MemoryRouter><OperationsView view="test" /></MemoryRouter>);
    await waitFor(() => expect(screen.queryByText('Loading agent configuration…')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Run task' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Inspect service status.' } });
    expect(screen.getByRole('button', { name: 'Run task' })).toBeEnabled();
    expect(screen.queryByText(/Enable the agent/)).toBeNull();
    expect(createTestSession).not.toHaveBeenCalled();
  });

  it.each(['no selection', 'SSH disabled', 'no credentials'])('starts the selected agent without imposing SSH prerequisites: %s', async state => {
    const data = await api.getOperationsSettings();
    data.settings = { ...settings, server_ids: state === 'no selection' ? [] : [server.id] };
    data.servers = [{ ...server, ssh_allowed: state !== 'SSH disabled', enabled: state !== 'no credentials' }];
    vi.mocked(api.getOperationsSettings).mockResolvedValue(data);
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ error: { message: 'Session not found.' } }, { status: 404 })));
    vi.mocked(createTestSession).mockResolvedValue({ id: 'new-session' } as SessionDetail);
    render(<MemoryRouter><OperationsView view="test" agentId="specific-agent" profile={{ model_id: null } as AgentProfile} /></MemoryRouter>);
    await waitFor(() => expect(screen.queryByText('Loading agent configuration…')).not.toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Inspect service status.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Run task' }));
    await waitFor(() => expect(createTestSession).toHaveBeenCalledWith('specific-agent', 'Inspect service status.', undefined));
    expect(screen.getByLabelText('Message')).toHaveValue('');
    vi.unstubAllGlobals();
  });
  it('lists only SSH-enabled servers and creates optional overrides with field inheritance', async () => {
    const user = userEvent.setup();
    const data = await api.getOperationsSettings();
    data.settings = { ...settings, instructions: 'Global procedure.', skill_ids: ['skill-1'] };
    data.servers.push({ ...server, id: 'unconfigured', name: 'No credentials', enabled: false, has_private_key: false });
    vi.mocked(api.getOperationsSettings).mockImplementation(async () => structuredClone(data));
    vi.mocked(api.saveOperationsServerConfig).mockImplementation(async (serverId, config) => {
      const saved = { id: 'config-1', server_id: serverId, revision: 1, instructions: config.instructions, mcp_server_ids: config.mcp_server_ids, skill_ids: config.skill_ids };
      data.configs = [saved]; data.settings.revision += 1; return saved;
    });
    render(<MemoryRouter><OperationsView view="settings" /></MemoryRouter>);
    await user.click(await screen.findByRole('combobox', { name: 'SSH configuration' }));
    expect(screen.getByRole('option', { name: 'Allowed servers' })).toBeVisible();
    expect(screen.queryByRole('option', { name: 'Disabled' })).toBeNull();
    expect(screen.getByRole('option', { name: 'No credentials' })).toBeVisible();
    await user.click(screen.getByRole('option', { name: 'Staging' }));
    expect(screen.getByLabelText('Server instructions')).toHaveValue('Global procedure.');
    expect(screen.getByLabelText('Server instructions')).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'Use service-checks' })).toBeChecked();
    expect(api.saveOperationsServerConfig).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Create server configuration' }));
    await user.click(screen.getByRole('checkbox', { name: 'Use agent instructions' }));
    await user.clear(screen.getByLabelText('Server instructions'));
    await user.type(screen.getByLabelText('Server instructions'), 'Inspect Staging logs.');
    expect(screen.getByRole('combobox', { name: 'SSH configuration' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Save server configuration' }));
    await screen.findByText('Server configuration saved. New runs use its resolved instructions and skills.');
    expect(api.saveOperationsServerConfig).toHaveBeenCalledWith(server.id, { settings_revision: 1, revision: null, instructions: 'Inspect Staging logs.', mcp_server_ids: null, skill_ids: null }, "operations");
    expect(screen.getByRole('combobox', { name: 'SSH configuration' })).toHaveTextContent('Staging');
    expect(screen.getByLabelText('Server instructions')).toHaveValue('Inspect Staging logs.');
    await user.click(screen.getByRole('checkbox', { name: 'Use agent skills' }));
    await user.click(screen.getByRole('checkbox', { name: 'Use service-checks' }));
    await user.click(screen.getByRole('button', { name: 'Save server configuration' }));
    await waitFor(() => expect(api.saveOperationsServerConfig).toHaveBeenLastCalledWith(server.id, { settings_revision: 2, revision: 1, instructions: 'Inspect Staging logs.', mcp_server_ids: null, skill_ids: [] }, 'operations'));
  });

  it('confirms deletion of server overrides and returns to global defaults', async () => {
    const user = userEvent.setup();
    const data = await api.getOperationsSettings();
    data.settings = { ...settings, instructions: 'Global procedure.' };
    data.configs = [{ id: 'config-1', server_id: server.id, revision: 3, instructions: 'Custom procedure.', mcp_server_ids: [], skill_ids: [] }];
    vi.mocked(api.getOperationsSettings).mockImplementation(async () => structuredClone(data));
    vi.mocked(api.deleteOperationsServerConfig).mockImplementation(async () => { data.configs = []; data.settings.revision += 1; });
    render(<MemoryRouter><OperationsView view="settings" /></MemoryRouter>);
    await screen.findByRole('combobox', { name: 'SSH configuration' });
    expect(screen.queryByRole('button', { name: 'Remove server configuration' })).toBeNull();
    await user.click(screen.getByRole('combobox', { name: 'SSH configuration' }));
    await user.click(screen.getByRole('option', { name: 'Staging' }));
    await user.click(screen.getByRole('button', { name: 'Remove server configuration' }));
    expect(screen.getByRole('alertdialog')).toHaveTextContent('This agent will use its own instructions, skills, and MCP selections on this server.');
    expect(api.deleteOperationsServerConfig).not.toHaveBeenCalled();
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Cancel' }));
    expect(api.deleteOperationsServerConfig).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Remove server configuration' }));
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Remove configuration' }));
    await screen.findByText('Server configuration removed. The agent’s configuration now applies.');
    expect(api.deleteOperationsServerConfig).toHaveBeenCalledWith(server.id, 3, 1, "operations");
    expect(screen.getByLabelText('Server instructions')).toHaveValue('Global procedure.');
    expect(screen.getByLabelText('Server instructions')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Create server configuration' })).toBeVisible();
    expect(api.saveSSH).not.toHaveBeenCalled();
  });

  it('keeps unsaved server instructions after a failed save and supports discard', async () => {
    const user = userEvent.setup();
    vi.mocked(api.saveOperationsServerConfig).mockRejectedValue(new Error('Unavailable'));
    render(<MemoryRouter><OperationsView view="settings" /></MemoryRouter>);
    await user.click(await screen.findByRole('combobox', { name: 'SSH configuration' }));
    await user.click(screen.getByRole('option', { name: 'Staging' }));
    await user.click(screen.getByRole('button', { name: 'Create server configuration' }));
    await user.click(screen.getByRole('checkbox', { name: 'Use agent instructions' }));
    await user.type(screen.getByLabelText('Server instructions'), 'Keep my changes.');
    await user.click(screen.getByRole('button', { name: 'Save server configuration' }));
    await screen.findByText('The operation failed. Try refreshing.');
    expect(screen.getByLabelText('Server instructions')).toHaveValue('Keep my changes.');
    await user.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(screen.getByRole('combobox', { name: 'SSH configuration' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Create server configuration' })).toBeVisible();
  });
  it('keeps unsaved configuration across Settings and Test and prevents running with a draft', async () => {
    const view = render(<MemoryRouter><OperationsView view="settings" /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText('Instructions'), { target: { value: 'Preserve this runbook.' } });
    view.rerender(<MemoryRouter><OperationsView view="test" /></MemoryRouter>);
    expect(screen.queryByRole('textbox', { name: 'Instructions' })).not.toBeInTheDocument();
    expect(screen.getByText('Save or discard changes in the configuration tabs before starting a task.')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'Inspect disk usage.' } });
    expect(screen.getByRole('button', { name: 'Run task' })).toBeDisabled();
    view.rerender(<MemoryRouter><OperationsView view="settings" /></MemoryRouter>);
    expect(screen.getByRole('textbox', { name: 'Instructions' })).toHaveValue('Preserve this runbook.');
    expect(api.saveOperationsSettings).not.toHaveBeenCalled();
  });
  it('saves explicit servers, skills and global instructions without a master switch', async () => {
    vi.mocked(api.saveOperationsSettings).mockResolvedValue({ ...settings, revision: 2, server_ids: ['server-1'], skill_ids: ['skill-1'], instructions: 'Verify before restarting.' });
    render(<MemoryRouter><OperationsView view="settings" /></MemoryRouter>);
    await screen.findByLabelText('Instructions');
    expect(screen.queryByRole('switch', { name: 'Enable server operations agent' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Run task' })).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Allow Disabled' })).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Allow Staging' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Use service-checks' }));
    fireEvent.change(screen.getByLabelText('Instructions'), { target: { value: 'Verify before restarting.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save agent configuration' }));
    await waitFor(() => expect(api.saveOperationsSettings).toHaveBeenCalledWith({ ...settings, server_ids: ['server-1'], skill_ids: ['skill-1'], instructions: 'Verify before restarting.' }, "operations"));
    expect(screen.getByRole('link', { name: 'Server SSH settings' })).toHaveAttribute('href', '/servers');
  });
});
describe('SSH settings', () => {
  it.each(['closed', 'empty', 'whitespace', 'cancelled'])('keeps the saved key with replacement %s and allows testing with unsaved permissions', async (mode) => {
    vi.mocked(api.saveSSH).mockResolvedValue({ ...server, revision: 3, allow_sudo: true });
    render(<ServerSSHForm id={server.id} onClose={vi.fn()} />);
    expect(await screen.findByText('Private key saved')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'SSH private key' })).not.toBeInTheDocument();
    if (mode !== 'closed') {
      fireEvent.click(screen.getByRole('button', { name: 'Replace key' }));
      const input = screen.getByLabelText('SSH private key');
      expect(input).toHaveValue('');
      if (mode === 'whitespace') fireEvent.change(input, { target: { value: '  \n  ' } });
      if (mode === 'cancelled') {
        fireEvent.change(input, { target: { value: 'discarded-replacement' } });
        fireEvent.click(screen.getByRole('button', { name: 'Cancel replacement' }));
        expect(screen.queryByText(/You have unsaved changes/)).not.toBeInTheDocument();
      }
    }
    fireEvent.click(screen.getByRole('switch', { name: 'Allow sudo to root' }));
    expect(screen.getByRole('button', { name: 'Test connection' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(api.saveSSH).toHaveBeenCalledWith(server.id, { revision: 2, username: 'operator', port: 22, allow_sudo: true, known_hosts: server.known_hosts }));
    expect(await screen.findByRole('status')).toHaveTextContent('saved');
  });
  it('submits a replacement key only when entered and clears the input after saving', async () => {
    vi.mocked(api.saveSSH).mockResolvedValue({ ...server, revision: 3 });
    render(<ServerSSHForm id={server.id} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Replace key' }));
    fireEvent.change(screen.getByLabelText('SSH private key'), { target: { value: 'replacement-test-key' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(api.saveSSH).toHaveBeenCalledWith(server.id, expect.objectContaining({ private_key: 'replacement-test-key' })));
    await waitFor(() => expect(screen.queryByRole('textbox', { name: 'SSH private key' })).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Replace key' })).toBeInTheDocument();
  });
  it('tests an unsaved key without trusted hosts, then lets the user add and save the discovered key', async () => {
    vi.mocked(api.getSSH).mockResolvedValue({ ...server, username: null, has_private_key: false, known_hosts: '', enabled: false });
    const hostKey = { public_key: 'ssh-ed25519 public-test-key', known_hosts_line: 'staging.internal ssh-ed25519 public-test-key', fingerprint: 'SHA256:test-fingerprint', algorithm: 'ssh-ed25519', verified: false };
    vi.mocked(api.testSSH).mockResolvedValue({ ok: true, message: 'SSH connected. Host verification is off.', host_key: hostKey });
    vi.mocked(api.saveSSH).mockResolvedValue({ ...server, revision: 3, known_hosts: hostKey.known_hosts_line });
    render(<ServerSSHForm id={server.id} onClose={vi.fn()} />);
    const privateKey = await screen.findByLabelText('SSH private key');
    expect(screen.getByRole('button', { name: 'Test connection' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Default SSH user'), { target: { value: 'operator' } });
    fireEvent.change(privateKey, { target: { value: 'draft-private-key' } });
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect(await screen.findByText('SHA256:test-fingerprint')).toBeInTheDocument();
    expect(api.testSSH).toHaveBeenCalledWith(server.id, expect.objectContaining({ username: 'operator', private_key: 'draft-private-key', known_hosts: '' }));
    expect(api.saveSSH).not.toHaveBeenCalled();
    expect(privateKey).toHaveValue('draft-private-key');
    fireEvent.click(screen.getByRole('button', { name: 'Add to trusted hosts' }));
    expect(screen.getByLabelText('Trusted host keys (optional)')).toHaveValue(hostKey.known_hosts_line);
    expect(screen.queryByText('SHA256:test-fingerprint')).not.toBeInTheDocument();
    expect(api.saveSSH).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(api.saveSSH).toHaveBeenCalledWith(server.id, expect.objectContaining({ private_key: 'draft-private-key', known_hosts: hostKey.known_hosts_line })));
  });
  it('clears stale discovery after connection changes and does not offer trust after failure', async () => {
    vi.mocked(api.testSSH).mockResolvedValueOnce({ ok: true, message: 'Connected', host_key: { public_key: 'public', fingerprint: 'SHA256:old', algorithm: 'ssh-ed25519', known_hosts_line: 'staging.internal ssh-ed25519 observed', verified: true } })
      .mockResolvedValueOnce({ ok: false, message: 'Host key verification failed.', host_key: null });
    render(<ServerSSHForm id={server.id} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Test connection' }));
    expect(await screen.findByText('SHA256:old')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('SSH port'), { target: { value: '2222' } });
    expect(screen.queryByRole('button', { name: 'Add to trusted hosts' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    expect(await screen.findByText('Host key verification failed.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add to trusted hosts' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('switch', { name: 'Remove saved key' }));
    expect(screen.getByRole('button', { name: 'Test connection' })).toBeDisabled();
  });

});

it('keeps server drafts in SSH and prevents saving them as agent instructions', async () => {
  const user = userEvent.setup();
  const view = render(<MemoryRouter><OperationsView view="settings" section="ssh" /></MemoryRouter>);
  await user.click(await screen.findByRole('combobox', { name: 'SSH configuration' }));
  await user.click(screen.getByRole('option', { name: 'Staging' }));
  await user.click(screen.getByRole('button', { name: 'Create server configuration' }));
  await user.click(screen.getByRole('checkbox', { name: 'Use agent instructions' }));
  await user.type(screen.getByLabelText('Server instructions'), 'Keep server-specific instructions.');
  view.rerender(<MemoryRouter><OperationsView view="settings" section="general" /></MemoryRouter>);
  expect(screen.getByText(/There are unsaved changes/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Save instructions' })).toBeDisabled();
  expect(screen.getByRole('textbox', { name: 'Instructions' })).toBeDisabled();
  view.rerender(<MemoryRouter><OperationsView view="settings" section="ssh" /></MemoryRouter>);
  expect(screen.getByLabelText('Server instructions')).toHaveValue('Keep server-specific instructions.');
  expect(screen.getByRole('button', { name: 'Save server configuration' })).toBeEnabled();
  expect(api.saveOperationsSettings).not.toHaveBeenCalled();
});


it('General shows agent instructions without skills or SSH controls', async () => {
  render(<MemoryRouter><OperationsView view="settings" section="general" /></MemoryRouter>);
  expect(await screen.findByRole('textbox', { name: 'Instructions' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Save instructions' })).toBeDisabled();
  expect(screen.queryByRole('checkbox', { name: 'Use service-checks' })).toBeNull();
  expect(screen.queryByRole('combobox', { name: 'SSH configuration' })).toBeNull();
  expect(screen.queryByRole('link', { name: 'Server SSH settings' })).toBeNull();
});

it('lets an agent select multiple servers and open each server configuration', async () => {
  const user = userEvent.setup();
  const data = await api.getOperationsSettings();
  data.servers = [server, { ...server, id: 'second', name: 'Second', enabled: false, username: null, has_private_key: false }];
  vi.mocked(api.getOperationsSettings).mockImplementation(async () => structuredClone(data));
  vi.mocked(api.saveOperationsSettings).mockImplementation(async value => {
    data.settings = { ...value, revision: value.revision + 1 }; return data.settings;
  });
  render(<MemoryRouter><OperationsView view="settings" section="ssh" /></MemoryRouter>);
  await user.click(await screen.findByRole('checkbox', { name: 'Allow Staging' }));
  await user.click(screen.getByRole('checkbox', { name: 'Allow Second' }));
  expect(screen.getByRole('button', { name: 'Configure Staging' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Save allowed servers' }));
  await screen.findByText('Agent configuration saved.');
  expect(api.saveOperationsSettings).toHaveBeenCalledWith(expect.objectContaining({ server_ids: [server.id, 'second'] }), 'operations');
  await user.click(screen.getByRole('button', { name: 'Configure Staging' }));
  expect(screen.getByRole('button', { name: 'Configure SSH connection' })).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Create server configuration' }));
  await user.click(screen.getByRole('checkbox', { name: 'Use agent instructions' }));
  await user.type(screen.getByRole('textbox', { name: 'Server instructions' }), 'Inspect the service logs first.');
  expect(screen.getByRole('button', { name: 'Save server configuration' })).toBeEnabled();
});

it('shows inherited SSH settings and saves an agent override without resending the private key', async () => {
  const user = userEvent.setup();
  const inherited = { ...server, revision: 0, server_revision: 2, connection_source: 'server' as const };
  vi.mocked(api.getSSH).mockResolvedValue(inherited);
  vi.mocked(api.saveSSH).mockImplementation(async (_id, value) => ({ ...inherited, ...value, revision: 1, connection_source: 'agent' }));
  vi.mocked(api.deleteAgentSSH).mockResolvedValue(inherited);
  render(<MemoryRouter><ServerSSHForm id={server.id} agentId="code" agentName="Code investigator" onClose={() => {}} /></MemoryRouter>);
  const toggle = await screen.findByRole('switch', { name: 'Override server SSH configuration' });
  expect(toggle).not.toBeChecked();
  expect(screen.getByRole('textbox', { name: 'SSH user' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled();
  expect(api.getSSH).toHaveBeenCalledWith(server.id, 'code');
  await user.click(toggle);
  await user.clear(screen.getByRole('textbox', { name: 'SSH user' }));
  await user.type(screen.getByRole('textbox', { name: 'SSH user' }), 'code-reader');
  await user.click(screen.getByRole('button', { name: 'Save settings' }));
  await screen.findByText('SSH settings saved.');
  expect(api.saveSSH).toHaveBeenCalledWith(server.id, expect.objectContaining({ revision: 0, server_revision: 2, username: 'code-reader' }), 'code');
  expect(vi.mocked(api.saveSSH).mock.calls[0][1]).not.toHaveProperty('private_key');
  await user.click(toggle);
  const dialog = screen.getByRole('alertdialog');
  expect(api.deleteAgentSSH).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
  expect(toggle).toBeChecked();
  await user.click(toggle);
  await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Use server configuration' }));
  await screen.findByText('Using the server SSH configuration.');
  expect(api.deleteAgentSSH).toHaveBeenCalledWith(server.id, 'code', 1, 2);
  expect(toggle).not.toBeChecked();
  expect(screen.getByRole('textbox', { name: 'SSH user' })).toHaveValue('operator');
});


it('allows configuring a server before enabling the agent SSH capability', async () => {
  const user = userEvent.setup();
  const profile: AgentProfile = { documents_grounded: false,
    id: 'code', name: 'Code agent', description: '', when_to_use: '', enabled: true, revision: 1,
    model_id: 'model', documents_enabled: false, repositories_enabled: true, ssh_enabled: false,
    skills_enabled: true, mcp_enabled: true, services_enabled: false, all_collections: false,
    all_repositories: true, collection_ids: [], repository_ids: [], service_ids: [], skill_ids: [], system_prompt: '',
  };
  const data = await api.getOperationsSettings();
  vi.mocked(api.getOperationsSettings).mockImplementation(async () => structuredClone(data));
  vi.mocked(api.saveOperationsServerConfig).mockImplementation(async (serverId, value) => {
    const saved = { ...value, id: 'config', server_id: serverId, revision: 1 };
    data.configs = [saved]; data.settings.revision += 1; return saved;
  });
  render(<MemoryRouter><OperationsView view="settings" section="ssh" agentId="code" profile={profile} /></MemoryRouter>);
  expect(await screen.findByRole('checkbox', { name: 'Allow Staging' })).toBeEnabled();
  expect(screen.getByRole('checkbox', { name: 'Allow Disabled' })).toBeDisabled();
  expect(screen.getByRole('combobox', { name: 'SSH configuration' })).toBeVisible();
  expect(screen.getByText(/SSH access is off for this agent/)).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Configure Staging' }));
  await user.click(screen.getByRole('button', { name: 'Configure SSH connection' }));
  expect(await screen.findByRole('dialog')).toBeVisible();
  expect(api.getSSH).toHaveBeenCalledWith(server.id, 'code');
  await user.click(screen.getAllByRole('button', { name: 'Close' })[0]);
  await user.click(await screen.findByRole('button', { name: 'Create server configuration' }));
  await user.click(screen.getByRole('checkbox', { name: 'Use agent instructions' }));
  await user.type(screen.getByRole('textbox', { name: 'Server instructions' }), 'Check the deployment logs.');
  await user.click(screen.getByRole('button', { name: 'Save server configuration' }));
  await screen.findByText('Server configuration saved. New runs use its resolved instructions and skills.');
  expect(api.saveOperationsServerConfig).toHaveBeenCalledWith(server.id, expect.objectContaining({ instructions: 'Check the deployment logs.' }), 'code');
  expect(screen.getByText(/SSH access is off for this agent/)).toBeVisible();
  expect(createTestSession).not.toHaveBeenCalled();
});
