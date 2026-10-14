import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { expect, test } from 'vitest';
import { kindsOf } from '~/lib/resources';
import ResourcePicker, { deploymentCascade } from '../ResourcePicker';

type Value = { server_ids: string[]; service_ids: string[]; deployment_ids: string[] };
const deployments = [{ id: 'dep-1', name: 'API on db-1', service_id: 'svc-1', server_id: 'srv-2' }];
const options = {
  servers: [{ id: 'srv-1', name: 'web-1' }, { id: 'srv-2', name: 'db-1' }, { id: 'srv-3', name: 'old-1' }],
  services: [{ id: 'svc-1', name: 'API' }], deployments,
};

function Picker({ initial }: { initial: Value }) {
  const [value, setValue] = useState(initial);
  return <ResourcePicker kinds={kindsOf(['servers', 'services', 'deployments'])} options={options} value={value} onChange={setValue}
    idPrefix="test" showUnavailable cascade={deploymentCascade<Value>(deployments)} emptyText={({ plural }) => `No ${plural.toLowerCase()}.`}
    itemState={(kind, item) => kind !== 'servers' ? {} : item.id === 'srv-1' ? { locked: true }
      : item.id === 'srv-3' ? { disabled: true, note: 'No credentials.' } : {}} />;
}

test('locked items are on and fixed; disabled items show their reason', () => {
  render(<Picker initial={{ server_ids: [], service_ids: [], deployment_ids: [] }} />);
  const locked = screen.getByRole('checkbox', { name: 'web-1' });
  expect(locked).toBeChecked();
  expect(locked).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: 'old-1' })).toBeDisabled();
  expect(screen.getByText('No credentials.')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'db-1' })).toBeEnabled();
  expect(screen.getByText('Specific deployments')).toBeInTheDocument();
});

test('chosen ids no longer offered show as unavailable and can be unticked', () => {
  render(<Picker initial={{ server_ids: ['srv-gone'], service_ids: [], deployment_ids: [] }} />);
  const gone = screen.getByRole('checkbox', { name: 'Unavailable resource (srv-gone)' });
  expect(gone).toBeChecked();
  fireEvent.click(gone);
  expect(screen.queryByRole('checkbox', { name: 'Unavailable resource (srv-gone)' })).not.toBeInTheDocument();
});

test('a deployment selects its service and server; unticking the service drops the deployment', () => {
  render(<Picker initial={{ server_ids: [], service_ids: [], deployment_ids: [] }} />);
  fireEvent.click(screen.getByRole('checkbox', { name: 'API on db-1' }));
  expect(screen.getByRole('checkbox', { name: 'API on db-1' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'API' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'db-1' })).toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: 'API' }));
  expect(screen.getByRole('checkbox', { name: 'API on db-1' })).not.toBeChecked();
});
