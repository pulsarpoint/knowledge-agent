import { useState } from "react";
import type { ServiceAsset } from "./services";

export interface RelatedService {
  service_id: string;
  description: string;
}
import { Checkbox } from "~/components/ui/checkbox";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Textarea } from "~/components/ui/textarea";

/** One kind of service relation in one direction: the related services, each with an optional description. */
export default function ServiceDependencyFields({
  name,
  title,
  help,
  relationship,
  services,
  value,
  onChange,
  disabled,
}: {
  name: string;
  title: string;
  help: string;
  /** How the relation reads with the other service, e.g. "Payments depends on PostgreSQL". */
  relationship: (other: string) => string;
  services: ServiceAsset[];
  value: RelatedService[];
  onChange: (value: RelatedService[]) => void;
  disabled: boolean;
}) {
  const [query, setQuery] = useState("");
  const matching = services.filter((service) =>
    `${service.name} ${service.description}`.toLowerCase().includes(query.trim().toLowerCase()),
  );
  return (
    <FieldSet disabled={disabled}>
      <FieldLegend>{title}</FieldLegend>
      <FieldDescription>
        {help} {value.length} selected.
      </FieldDescription>
      {services.length > 0 ? (
        <>
          <Input
            aria-label={`Filter ${title.toLowerCase()} services`}
            placeholder="Find a service…"
            value={query}
            disabled={disabled}
            onChange={(event) => setQuery(event.target.value)}
          />
          <FieldGroup className="max-h-80 gap-4 overflow-y-auto p-1">
            {matching.map((service) => {
              const selected = value.find((link) => link.service_id === service.id);
              const id = `${name}-${service.id}`;
              return (
                <FieldGroup key={service.id} className="gap-2">
                  <Field orientation="horizontal" data-disabled={disabled}>
                    <Checkbox
                      id={id}
                      checked={selected !== undefined}
                      disabled={disabled}
                      onCheckedChange={(checked) =>
                        onChange(
                          checked === true
                            ? [...value, { service_id: service.id, description: "" }]
                            : value.filter((link) => link.service_id !== service.id),
                        )
                      }
                    />
                    <FieldContent>
                      <FieldLabel htmlFor={id}>{service.name}</FieldLabel>
                      {service.description && <FieldDescription>{service.description}</FieldDescription>}
                    </FieldContent>
                  </Field>
                  {selected && (
                    <Field data-disabled={disabled}>
                      <FieldLabel htmlFor={`${id}-description`}>Why {relationship(service.name)} (optional)</FieldLabel>
                      <Textarea
                        id={`${id}-description`}
                        rows={2}
                        maxLength={4000}
                        disabled={disabled}
                        value={selected.description}
                        placeholder="For example: stores orders, handles login, or publishes events."
                        onChange={(event) =>
                          onChange(
                            value.map((link) =>
                              link.service_id === service.id ? { ...link, description: event.target.value } : link,
                            ),
                          )
                        }
                      />
                    </Field>
                  )}
                </FieldGroup>
              );
            })}
            {matching.length === 0 && (
              <FieldDescription>No services match your search. Existing selections are kept.</FieldDescription>
            )}
          </FieldGroup>
        </>
      ) : (
        <FieldDescription>Add another service to select a relationship.</FieldDescription>
      )}
    </FieldSet>
  );
}
