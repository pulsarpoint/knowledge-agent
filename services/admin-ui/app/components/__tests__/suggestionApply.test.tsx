import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import SuggestionApply from '../SuggestionApply';
import * as api from '~/features/agents/reviews';
import { ApiError } from '../../lib/api';
vi.mock('~/features/agents/reviews');
vi.mock('../Markdown', () => ({ default: ({ content }: { content: string }) => <div>{content}</div> }));

const review = { id: 'review-1', comment: 'Be clearer', suggestion: 'Be clearer.', created_at: '2026-10-08T10:00:00Z' };
const source: api.ReviewSource = { suggestion: 'Be clearer.', revision: 3, fingerprint: 'f'.repeat(64),
  changes: { system_prompt: null, instructions: 'State uncertainty.', server_instructions: [] },
  system_prompt: 'Use evidence.', instructions: 'Original instructions',
  servers: [{ id: 'server-1', name: 'Test server', instructions: 'Original instructions', inherits: true },
            { id: 'server-2', name: 'Queue host', instructions: 'Inspect only.', inherits: false }] };
const applied = vi.fn();
function page() { return render(<SuggestionApply agentId="custom" review={review} onClose={vi.fn()} onApplied={applied} />); }
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getReviewSource).mockResolvedValue(structuredClone(source));
  vi.mocked(api.applyReview).mockResolvedValue({ agent_id: 'custom' });
});

it('prefills the proposed texts, lets inheriting servers follow and applies to this agent', async () => {
  page();
  expect(await screen.findByLabelText('Additional instructions')).toHaveValue('State uncertainty.');
  expect(api.getReviewSource).toHaveBeenCalledWith('custom', 'review-1');
  expect(screen.getByLabelText('System prompt')).toHaveValue('Use evidence.');
  expect(screen.getByLabelText('Test server instructions')).toHaveValue('State uncertainty.');
  expect(screen.getByLabelText('Queue host instructions')).toHaveValue('Inspect only.');
  fireEvent.change(screen.getByLabelText('Additional instructions'), { target: { value: 'Separate facts from guesses.' } });
  expect(screen.getByLabelText('Test server instructions')).toHaveValue('Separate facts from guesses.');
  fireEvent.change(screen.getByLabelText('Queue host instructions'), { target: { value: 'Check the queue only.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply to this agent' }));
  await waitFor(() => expect(api.applyReview).toHaveBeenCalledWith('custom', 'review-1', source, 'agent', {
    system_prompt: null, instructions: 'Separate facts from guesses.',
    server_instructions: [{ server_id: 'server-2', instructions: 'Check the queue only.' }] }));
  expect(applied).toHaveBeenCalledWith('agent', 'custom');
});

it('applies to a new clone and keeps the edits when applying fails', async () => {
  vi.mocked(api.applyReview)
    .mockRejectedValueOnce(new Error('The agent changed since you opened the suggestion. Review it again.'))
    .mockResolvedValueOnce({ agent_id: 'clone-1' });
  page();
  fireEvent.change(await screen.findByLabelText('System prompt'), { target: { value: 'Answer from evidence.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply to a new clone' }));
  expect(await screen.findByText('The agent changed since you opened the suggestion. Review it again.')).toBeVisible();
  expect(screen.getByLabelText('System prompt')).toHaveValue('Answer from evidence.');
  fireEvent.click(screen.getByRole('button', { name: 'Apply to a new clone' }));
  await waitFor(() => expect(applied).toHaveBeenCalledWith('clone', 'clone-1'));
  const [, , , target, changes] = vi.mocked(api.applyReview).mock.calls[1];
  expect([target, changes.system_prompt]).toEqual(['clone', 'Answer from evidence.']);
});

it('needs a system prompt', async () => {
  page();
  fireEvent.change(await screen.findByLabelText('System prompt'), { target: { value: '  ' } });
  expect(screen.getByRole('button', { name: 'Apply to this agent' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Apply to a new clone' })).toBeDisabled();
});

it('reloads the latest agent after a conflict, keeps the edits and applies with the new revision', async () => {
  const latest: api.ReviewSource = { ...structuredClone(source), revision: 4, fingerprint: 'e'.repeat(64), system_prompt: 'Use sources.',
    servers: [...structuredClone(source.servers), { id: 'server-3', name: 'New host', instructions: 'Read logs.', inherits: false }] };
  vi.mocked(api.getReviewSource).mockResolvedValueOnce(structuredClone(source)).mockResolvedValueOnce(structuredClone(latest));
  vi.mocked(api.applyReview)
    .mockRejectedValueOnce(new ApiError('The agent changed since you opened the suggestion. Review it again.', 409))
    .mockResolvedValueOnce({ agent_id: 'custom' });
  page();
  fireEvent.change(await screen.findByLabelText('System prompt'), { target: { value: 'Answer from evidence.' } });
  fireEvent.change(screen.getByLabelText('Queue host instructions'), { target: { value: 'Check the queue only.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply to this agent' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Reload latest' }));
  expect(await screen.findByLabelText('New host instructions')).toHaveValue('Read logs.');
  expect(api.getReviewSource).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole('button', { name: 'Reload latest' })).not.toBeInTheDocument();
  expect(screen.getByLabelText('System prompt')).toHaveValue('Answer from evidence.');
  expect(screen.getByLabelText('Additional instructions')).toHaveValue('State uncertainty.');
  expect(screen.getByLabelText('Queue host instructions')).toHaveValue('Check the queue only.');
  fireEvent.click(screen.getByRole('button', { name: 'Apply to this agent' }));
  await waitFor(() => expect(applied).toHaveBeenCalledWith('agent', 'custom'));
  const [, , sent, , changes] = vi.mocked(api.applyReview).mock.calls[1];
  expect([sent.revision, sent.fingerprint]).toEqual([4, 'e'.repeat(64)]);
  expect(changes).toEqual({ system_prompt: 'Answer from evidence.', instructions: 'State uncertainty.',
    server_instructions: [{ server_id: 'server-2', instructions: 'Check the queue only.' }] });
});

it('offers Reload latest when the suggestion fails to load', async () => {
  vi.mocked(api.getReviewSource).mockRejectedValueOnce(new ApiError('request failed (500)', 500)).mockResolvedValueOnce(structuredClone(source));
  page();
  expect(await screen.findByText('request failed (500)')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Reload latest' }));
  expect(await screen.findByLabelText('Additional instructions')).toHaveValue('State uncertainty.');
  expect(screen.queryByText('request failed (500)')).not.toBeInTheDocument();
});

it('does not offer Reload latest after other apply failures', async () => {
  vi.mocked(api.applyReview).mockRejectedValueOnce(new ApiError('Instructions refer to a server outside this agent.', 422));
  page();
  fireEvent.click(await screen.findByRole('button', { name: 'Apply to this agent' }));
  expect(await screen.findByText('Instructions refer to a server outside this agent.')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Reload latest' })).not.toBeInTheDocument();
});
