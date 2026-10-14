import type { ReactNode } from 'react';
import { Checkbox } from '~/components/ui/checkbox';
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from '~/components/ui/field';

/** A group of checkboxes: a resource kind from RESOURCE_KINDS, or another list shown alike (the settings sheet's documents). */
export interface PickerKind { key: string; field: string; plural: string }
export interface PickerItem { id: string; name: string }
/** The chosen ids per id-list field (e.g. `server_ids`); the value passed in may hold other fields too. */
export type PickerValue = Partial<Record<string, string[]>>;
/** How one item shows: locked on (always ticked, cannot change), disabled (cannot be ticked), a note under it. */
export interface PickerItemState { locked?: boolean; disabled?: boolean; note?: string | null }
/** A change the person made, for a cascade to extend (e.g. a deployment also selects its service and server). */
export interface PickerChange { kind: string; id: string; checked: boolean }

/** Checkbox groups per resource kind, in the order of `kinds`. `list`: compact groups inside a surrounding FieldSet (agent
 * settings; an empty kind is hidden unless `emptyText` gives a text). `cards`: a bordered card per kind in a two-column grid
 * (a document's Linked resources). Checkbox ids are `${idPrefix}-${kind}-${item id}`. */
export default function ResourcePicker<V extends object>({ kinds, options, value, onChange, idPrefix, layout = 'list', disabled = false,
  itemState, description, emptyText, showUnavailable = false, cascade }: {
  kinds: readonly PickerKind[]; options: Partial<Record<string, readonly PickerItem[]>>; value: V; onChange: (next: V) => void;
  idPrefix: string; layout?: 'list' | 'cards'; disabled?: boolean;
  itemState?: (kind: string, item: PickerItem) => PickerItemState;
  /** The text under a kind's legend. */
  description?: (kind: PickerKind) => ReactNode;
  /** What an empty kind says; without it an empty kind is not shown. */
  emptyText?: (kind: PickerKind) => string;
  /** Chosen ids no longer offered show as "Unavailable resource (id)" so they can be unticked. */
  showUnavailable?: boolean;
  cascade?: (next: V, change: PickerChange) => V;
}) {
  const ids = (kind: PickerKind) => (value as PickerValue)[kind.field] ?? [];
  const toggle = (kind: PickerKind, id: string, checked: boolean) => {
    const next = { ...value, [kind.field]: checked ? [...ids(kind), id] : ids(kind).filter(item => item !== id) } as V;
    onChange(cascade ? cascade(next, { kind: kind.key, id, checked }) : next);
  };
  const groups = kinds.map(kind => {
    const offered = options[kind.key] ?? [];
    const missing = showUnavailable ? ids(kind).filter(id => !offered.some(item => item.id === id))
      .map(id => ({ id, name: `Unavailable resource (${id})` })) : [];
    return { kind, items: [...offered, ...missing] };
  }).filter(({ kind, items }) => items.length > 0 || emptyText);
  const cards = layout === 'cards';

  const item = (kind: PickerKind, entry: PickerItem) => {
    const state = itemState?.(kind.key, entry) ?? {};
    const id = `${idPrefix}-${kind.key}-${entry.id}`;
    return <Field key={entry.id} orientation="horizontal">
      <Checkbox id={id} aria-label={entry.name} disabled={disabled || state.disabled || state.locked}
        checked={state.locked || ids(kind).includes(entry.id)} onCheckedChange={on => toggle(kind, entry.id, on === true)} />
      <FieldLabel htmlFor={id} className={cards ? 'break-words' : undefined}>{entry.name}</FieldLabel>
      {state.note && <FieldDescription>{state.note}</FieldDescription>}
    </Field>;
  };

  if (!cards) return <>{groups.map(({ kind, items }) => <FieldSet key={kind.key}><FieldLegend variant="label">{kind.plural}</FieldLegend>
    {description && <FieldDescription>{description(kind)}</FieldDescription>}
    {items.length === 0 && emptyText && <p className="text-sm text-muted-foreground">{emptyText(kind)}</p>}
    {items.map(entry => item(kind, entry))}
  </FieldSet>)}</>;
  return <div className="grid gap-6 md:grid-cols-2">{groups.map(({ kind, items }) => <FieldSet key={kind.key} className="rounded-lg border p-4">
    <FieldLegend>{kind.plural}</FieldLegend>
    {description && <FieldDescription>{description(kind)}</FieldDescription>}
    <FieldGroup className="max-h-64 overflow-y-auto">{items.length === 0 && emptyText && <p className="text-sm text-muted-foreground">{emptyText(kind)}</p>}
      {items.map(entry => item(kind, entry))}
    </FieldGroup>
  </FieldSet>)}</div>;
}

/** Selecting a deployment also selects its service and server; unselecting a service or server unselects its deployments. */
export function deploymentCascade<V extends { service_ids: string[]; server_ids: string[]; deployment_ids: string[] }>(
  deployments: readonly { id: string; service_id?: string; server_id?: string }[]) {
  return (next: V, { kind, id, checked }: PickerChange): V => {
    const result = { ...next };
    if (kind === 'deployments' && checked) {
      const deployment = deployments.find(item => item.id === id);
      if (deployment?.service_id) result.service_ids = [...new Set([...result.service_ids, deployment.service_id])];
      if (deployment?.server_id) result.server_ids = [...new Set([...result.server_ids, deployment.server_id])];
    }
    if ((kind === 'services' || kind === 'servers') && !checked) result.deployment_ids = result.deployment_ids.filter(item => {
      const deployment = deployments.find(entry => entry.id === item);
      return kind === 'services' ? deployment?.service_id !== id : deployment?.server_id !== id;
    });
    return result;
  };
}
