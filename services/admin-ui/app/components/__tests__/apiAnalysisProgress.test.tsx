import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import ApiAnalysisProgress from '../ApiAnalysisProgress';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test('failed runs keep route coverage and completed section previews available', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({
    inventory: {
      coverage: [{ id: 'repo', name: 'API code', route_count: 2, limitations: 'Dynamic routes may be absent.' }],
      batches: [{ key: 'first', project_name: 'API code', gaps: ['The storage definition was not resolved.'],
        routes: [{ id: 'get', name: 'GET /items', file_path: 'app/router.py', start_line: 12 }] },
        { key: 'second', project_name: 'API code', gaps: [],
          routes: [{ id: 'post', name: 'POST /items', file_path: 'app/router.py', start_line: 14 }] }] },
    sections: [{ fingerprint: 'saved', batch_key: 'first', position: 1, title: 'Items', markdown: '# Saved endpoint evidence', reused_from: 'old-job' }],
  }) })));
  render(<ApiAnalysisProgress jobId="job" status="failed" />);
  expect(await screen.findByText('2 route registrations · 1/2 sections saved')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Inspect API routes and saved sections' }));
  expect(screen.getByText('GET /items')).toBeInTheDocument();
  expect(screen.getByText('POST /items')).toBeInTheDocument();
  expect(screen.getByText('Section 1 · Saved · reused')).toBeInTheDocument();
  expect(screen.getByText('Section 2 · Not completed')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Preview section 1' }));
  expect(screen.getByRole('heading', { name: 'Saved endpoint evidence' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Evidence gaps (1)' }));
  expect(screen.getByText('The storage definition was not resolved.')).toBeInTheDocument();
});

test('old runs without route inventory remain usable', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ inventory: null, sections: [] }) })));
  const view = render(<ApiAnalysisProgress jobId="old" status="failed" />);
  expect(view.container).toBeEmptyDOMElement();
});
