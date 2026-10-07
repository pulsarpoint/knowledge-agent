import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { expect, test } from 'vitest';
import SessionsRedirect from '~/routes/documentationSessions';
import SessionRedirect from '~/routes/documentationSession';

function open(path: string) {
  render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/documentation/sessions" element={<SessionsRedirect />} />
    <Route path="/documentation/sessions/:sessionId" element={<SessionRedirect />} />
    <Route path="/documentation/generations" element={<p>Generate documents list</p>} />
    <Route path="/documentation/generations/:sessionId" element={<p>Generation page</p>} />
  </Routes></MemoryRouter>);
}

test('old session links open the generation', async () => {
  open('/documentation/sessions/s1');
  expect(await screen.findByText('Generation page')).toBeVisible();
});

test('the old Sessions list opens Generate documents', async () => {
  open('/documentation/sessions');
  expect(await screen.findByText('Generate documents list')).toBeVisible();
});
