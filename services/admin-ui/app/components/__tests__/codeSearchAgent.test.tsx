import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import CodeSearchAgentView from '../CodeSearchAgentView';
import * as api from '../../lib/codeAgent';
vi.mock('../../lib/codeAgent');
vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));
const ready = { model: 'Code model', ask_enabled: true, ask_reason: null, repositories: [{ name: 'repo-1', label: 'Payments', path_exists: true }] };
beforeEach(() => { vi.resetAllMocks(); vi.mocked(api.getCodeStatus).mockResolvedValue(ready); });

test('tests the selected code repository and shows its answer and citations', async () => {
  vi.mocked(api.askCode).mockImplementation(async function* () { yield { type: 'done', answer: 'Retries happen here.', citations: [{ file: 'app.py', lines: '2-3', quote: 'retry()' }] }; });
  const user = userEvent.setup();
  const view = render(<MemoryRouter><CodeSearchAgentView /></MemoryRouter>);
  await screen.findByText('Code model');
  await user.click(screen.getByRole('combobox', { name: 'Repository' }));
  await user.click(screen.getByRole('option', { name: 'Payments' }));
  await user.type(screen.getByLabelText('Question'), 'Where are retries?');
  await user.type(screen.getByLabelText('Additional context (optional)'), 'Payment failures');
  await user.click(screen.getByRole('button', { name: 'Ask code agent' }));
  expect(await screen.findByText('Retries happen here.')).toBeVisible();
  expect(screen.getByText('app.py:2-3')).toBeVisible();
  expect(api.askCode).toHaveBeenCalledWith('repo-1', 'Where are retries?', 'Payment failures', expect.any(AbortSignal), "code");
  vi.mocked(api.getCodeStatus).mockResolvedValue({ ...ready, model: 'Updated code model' });
  view.rerender(<MemoryRouter><CodeSearchAgentView modelRevision={1} /></MemoryRouter>);
  expect(await screen.findByText('Updated code model')).toBeVisible();
  expect(screen.getByText('Retries happen here.')).toBeVisible();
  expect(screen.getByLabelText('Question')).toHaveValue('Where are retries?');
});

test('requires a code model and does not substitute the search agent', async () => {
  vi.mocked(api.getCodeStatus).mockResolvedValue({ ...ready, ask_enabled: false, ask_reason: 'Choose a code model.' });
  render(<MemoryRouter><CodeSearchAgentView /></MemoryRouter>);
  expect(await screen.findByText('Choose a code model.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Ask code agent' })).toBeDisabled();
  expect(api.askCode).not.toHaveBeenCalled();
});

test('TODO keeps the selected repository and question ready for another submission', async () => {
  vi.mocked(api.getCodeStatus).mockResolvedValue({ ...ready, model: null });
  vi.mocked(api.askCode).mockImplementation(async function* () { yield { type: 'todo', text: 'TODO' }; });
  const user = userEvent.setup();
  render(<MemoryRouter><CodeSearchAgentView /></MemoryRouter>);
  await user.click(await screen.findByRole('combobox', { name: 'Repository' }));
  await user.click(screen.getByRole('option', { name: 'Payments' }));
  await user.type(screen.getByLabelText('Question'), 'Where are retries?');
  await user.click(screen.getByRole('button', { name: 'Ask code agent' }));
  expect(await screen.findByText('TODO')).toBeVisible();
  expect(screen.getByLabelText('Question')).toHaveValue('Where are retries?');
  expect(screen.getByRole('button', { name: 'Ask code agent' })).toBeEnabled();
  expect(screen.queryByText('Code search result')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Ask code agent' }));
  expect(api.askCode).toHaveBeenCalledTimes(2);
});
