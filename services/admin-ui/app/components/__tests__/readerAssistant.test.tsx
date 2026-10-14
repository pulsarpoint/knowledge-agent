import { render, screen } from '@testing-library/react';
import GroundingSources from '~/features/sessions/GroundingSources';

test('grounding sources list cited documents', () => {
  render(<GroundingSources sources={[{ n: 1, path: 'payments/retries.md', heading_path: 'payments', anchor: null }]} />);
  expect(screen.getByText('[1]')).toBeInTheDocument();
  expect(screen.getByText('payments/retries.md')).toBeInTheDocument();
});
