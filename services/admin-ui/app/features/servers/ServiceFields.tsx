import type { ServiceAsset } from "./services";
import { serviceRoles, serviceTypes, type ServiceRole, type ServiceType } from "./serviceClassification";
import { Field, FieldDescription, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "~/components/ui/select";
import { Textarea } from "~/components/ui/textarea";

/** A service's own fields, as a form holds them. */
export interface ServiceFieldValues {
  name: string;
  service_type: ServiceType;
  service_role: ServiceRole;
  owner: string;
  description: string;
  address: string;
}

/** The fields of a saved service, or a new one's defaults. */
export const serviceFields = (service?: ServiceAsset | null): ServiceFieldValues => ({
  name: service?.name ?? "",
  service_type: service?.service_type ?? "custom",
  service_role: service?.service_role ?? "application",
  owner: service?.owner ?? "",
  description: service?.description ?? "",
  address: service?.address ?? "",
});

/** What a save sends: the address only for an external service (cleared for another type). */
export const serviceValue = (value: ServiceFieldValues) => ({
  ...value,
  address: value.service_type === "external" ? value.address.trim() : "",
});

/** The service's own fields: name, type, the address of an external service, role, owner and description. */
export default function ServiceFields({
  idPrefix,
  value,
  onChange,
}: {
  idPrefix: string;
  value: ServiceFieldValues;
  onChange: (value: ServiceFieldValues) => void;
}) {
  const set = (changes: Partial<ServiceFieldValues>) => onChange({ ...value, ...changes });
  const id = (field: string) => `${idPrefix}-${field}`;
  return (
    <>
      <Field>
        <FieldLabel htmlFor={id("name")}>Name</FieldLabel>
        <Input
          id={id("name")}
          maxLength={200}
          value={value.name}
          onChange={(event) => set({ name: event.target.value })}
        />
      </Field>
      <Field>
        <FieldLabel htmlFor={id("type")}>Type</FieldLabel>
        <Select value={value.service_type} onValueChange={(type) => set({ service_type: type as ServiceType })}>
          <SelectTrigger id={id("type")} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {(Object.keys(serviceTypes) as ServiceType[]).map((type) => (
                <SelectItem key={type} value={type}>
                  {serviceTypes[type]}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
        <FieldDescription>
          Custom: software we build. Built-in: software such as Nginx or PostgreSQL. External: a service operated
          outside this system.
        </FieldDescription>
      </Field>
      {value.service_type === "external" && (
        <Field>
          <FieldLabel htmlFor={id("address")}>Address</FieldLabel>
          <Input
            id={id("address")}
            maxLength={253}
            value={value.address}
            placeholder="api.stripe.com"
            onChange={(event) => set({ address: event.target.value })}
          />
          <FieldDescription>Where the external service is reached: a hostname or IP address.</FieldDescription>
        </Field>
      )}
      <Field>
        <FieldLabel htmlFor={id("role")}>Role</FieldLabel>
        <Select value={value.service_role} onValueChange={(role) => set({ service_role: role as ServiceRole })}>
          <SelectTrigger id={id("role")} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {(Object.keys(serviceRoles) as ServiceRole[]).map((role) => (
                <SelectItem key={role} value={role}>
                  {serviceRoles[role].label}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
      </Field>
      <Field>
        <FieldLabel htmlFor={id("owner")}>Owner</FieldLabel>
        <Input
          id={id("owner")}
          maxLength={200}
          value={value.owner}
          onChange={(event) => set({ owner: event.target.value })}
        />
      </Field>
      <Field>
        <FieldLabel htmlFor={id("description")}>Description</FieldLabel>
        <Textarea
          id={id("description")}
          maxLength={4000}
          rows={3}
          value={value.description}
          onChange={(event) => set({ description: event.target.value })}
        />
      </Field>
    </>
  );
}
