import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import SearchAgentView from "../SearchAgentView";
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });
const contextOptions = { folders: [{ path: 'payments', description: 'Payment operations', indexed_files: 2 }], repositories: [], warnings: [] };

test("search TODO preserves context and input without creating or loading a session", async () => {
  const payloads: Record<string, unknown>[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.split('?')[0].endsWith('/context')) return Response.json(contextOptions);
    if (url.split('?')[0].endsWith('/status')) return Response.json({ name: 'Search agent', ask_enabled: true, ask_reason: null, model: null, sources: { docs: true, codex: false } });
    if (url.split('?')[0].endsWith('/chat')) { payloads.push(JSON.parse(init!.body as string)); return new Response('TODO'); }
    throw new Error(`Unexpected URL ${url}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<MemoryRouter><SearchAgentView /></MemoryRouter>);
  const input = await screen.findByRole('textbox');
  fireEvent.click(screen.getByRole('button', { name: 'Advanced' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'All document folders' }));
  expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled();
  fireEvent.click(await screen.findByRole('checkbox', { name: 'payments' }));
  fireEvent.change(input, { target: { value: 'Explain retries' } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  expect(await screen.findByText('TODO')).toBeVisible();
  expect(input).toHaveValue('Explain retries');
  expect(input).toBeEnabled();
  expect(screen.getByRole('checkbox', { name: 'payments' })).toBeEnabled();
  expect(payloads[0]).toMatchObject({ question: 'Explain retries', context: { folders: ['payments'] } });
  expect(payloads[0]).not.toHaveProperty('session');
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  await waitFor(() => expect(payloads).toHaveLength(2));
  expect(screen.getAllByText('TODO')).toHaveLength(1);
  expect(fetchMock.mock.calls.filter(([url]) => url.includes('/chats/'))).toEqual([]);
});

test("an unavailable agent shows the API reason and cannot submit", async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => Response.json(url.split('?')[0].endsWith('/context') ? contextOptions : { name: 'Search agent', ask_enabled: false, ask_reason: 'This agent is disabled.', model: null, sources: { docs: true, codex: false } })));
  render(<MemoryRouter><SearchAgentView /></MemoryRouter>);
  expect(await screen.findByText('This agent is disabled.')).toBeVisible();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
});
