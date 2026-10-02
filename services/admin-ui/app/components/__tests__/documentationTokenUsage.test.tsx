import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import DocumentationTokenUsage from '../DocumentationTokenUsage';
import type { DocumentationGeneration } from '~/lib/documents';

afterEach(cleanup);
const run = { status: 'succeeded', prompt_snapshot: [{kind: 'overview'}], token_usage: [
  {phase:'overview',step:'inspection',requests:2,reported_calls:2,input_tokens:1800,output_tokens:500,total_tokens:2300,
    input_tokens_details:{cached_tokens:800,cache_write_tokens:100},output_tokens_details:{reasoning_tokens:120}},
  {phase:'overview',step:'writing',requests:1,reported_calls:1,input_tokens:2000,output_tokens:400,total_tokens:2400},
] } as DocumentationGeneration;

test('shows inspection, writing and totals without double-counting cache or reasoning', () => {
  render(<DocumentationTokenUsage run={run} />);
  const stats = screen.getByRole('region',{name:'Phase token usage'});
  expect(within(stats).getByRole('row',{name:/Source inspection/})).toHaveTextContent('2,300');
  expect(within(stats).getByRole('row',{name:/Writing/})).toHaveTextContent('2,400');
  expect(within(stats).getByRole('row',{name:/^Total/})).toHaveTextContent('3,8009004,7003');
  expect(stats).toHaveTextContent('800 cached input tokens');
  expect(stats).toHaveTextContent('120 reasoning tokens');
});

test('shows missing historical usage as not recorded, not zero', () => {
  render(<DocumentationTokenUsage run={{...run,token_usage:[]}} />);
  expect(screen.getByRole('region',{name:'Phase token usage'})).toHaveTextContent('Not recorded for this run');
  expect(screen.queryByText('0')).not.toBeInTheDocument();
});

test('preserves reported usage in failed runs and identifies missing provider reports', () => {
  render(<DocumentationTokenUsage run={{...run,status:'failed',token_usage:[{...run.token_usage![0],requests:3}]}} />);
  const stats = screen.getByRole('region',{name:'Phase token usage'});
  expect(stats).toHaveTextContent('Recorded total · partial');
  expect(stats).toHaveTextContent('Some model responses did not report token usage');
  expect(stats).toHaveTextContent('2,300');
});

test('shows current usage while a phase is running', () => {
  render(<DocumentationTokenUsage run={{...run,status:'running'}} />);
  expect(screen.getByText('Token usage · 4,700 tokens so far')).toBeInTheDocument();
});
