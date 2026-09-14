import type { DocumentInput, DocumentResources } from '~/lib/documents';
import { Checkbox } from '~/components/ui/checkbox';
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from '~/components/ui/field';

export default function DocumentResourceSelection({ resources, value, onChange }: {
  resources: DocumentResources; value: DocumentInput; onChange: (value: DocumentInput) => void;
}) {
  const groups = [
    { kind: 'environments', field: 'environment_ids', label: 'Environments' },
    { kind: 'code_projects', field: 'code_project_ids', label: 'Code projects' },
    { kind: 'services', field: 'service_ids', label: 'Services' },
    { kind: 'servers', field: 'server_ids', label: 'Servers' },
    { kind: 'deployments', field: 'deployment_ids', label: 'Specific deployments' },
  ] as const;
  return <div className="grid gap-6 md:grid-cols-2">{groups.map(({ kind, field, label }) => {
    const options = [...(resources[kind] ?? []), ...(value[field] ?? []).filter((id) => !(resources[kind] ?? []).some((r) => r.id === id)).map((id) => ({ id, name: `Unavailable resource (${id})` }))];
    return <FieldSet key={kind} className="rounded-lg border p-4"><FieldLegend>{label}</FieldLegend>
      <FieldDescription>{kind === 'deployments' ? 'Select a deployment to distinguish environments. Its service and server are selected too.' : `Link the ${label.toLowerCase()} this document describes.`}</FieldDescription>
      <FieldGroup className="max-h-64 overflow-y-auto">{options.length === 0 && <p className="text-sm text-muted-foreground">No {label.toLowerCase()} available.</p>}
        {options.map((item) => <Field key={item.id} orientation="horizontal"><Checkbox id={`doc-${kind}-${item.id}`} checked={(value[field] ?? []).includes(item.id)} onCheckedChange={(checked) => {
          const next = { ...value, [field]: checked ? [...(value[field] ?? []), item.id] : (value[field] ?? []).filter((id) => id !== item.id) };
          if (kind === 'deployments' && checked) {
            const deployment = resources.deployments.find((d) => d.id === item.id);
            if (deployment?.service_id) next.service_ids = [...new Set([...next.service_ids, deployment.service_id])];
            if (deployment?.server_id) next.server_ids = [...new Set([...next.server_ids, deployment.server_id])];
          }
          if ((kind === 'services' || kind === 'servers') && !checked) next.deployment_ids = next.deployment_ids.filter((id) => {
            const deployment = resources.deployments.find((d) => d.id === id);
            return kind === 'services' ? deployment?.service_id !== item.id : deployment?.server_id !== item.id;
          });
          onChange(next);
        }} /><FieldLabel htmlFor={`doc-${kind}-${item.id}`} className="break-words">{item.name}</FieldLabel></Field>)}
      </FieldGroup>
    </FieldSet>;
  })}</div>;
}
