import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import SuggestionApply from '../SuggestionApply';
import * as api from '../../lib/reviews';
vi.mock('../../lib/reviews');
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
