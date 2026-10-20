import { Field, FieldDescription, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import type { JiraPullParams } from "./schedules";

export interface JiraPull {
  jql: string;
  limit: string;
}

export const jiraPull = (params: Record<string, unknown>): JiraPull => ({
  jql: typeof params.jql === "string" ? params.jql : "",
  limit: typeof params.limit === "number" ? String(params.limit) : "",
});

/** Empty fields are left out: the pull then uses the last manual fetch's query and number (then 25 tickets). */
export function jiraPullParams(value: JiraPull): JiraPullParams {
  const jql = value.jql.trim();
  return { ...(jql ? { jql } : {}), ...(value.limit ? { limit: Number(value.limit) } : {}) };
}

/** What a Jira tickets schedule pulls: its own query and number of tickets. */
export default function JiraPullFields({
  id,
  value,
  onChange,
}: {
  id: string;
  value: JiraPull;
  onChange: (value: JiraPull) => void;
}) {
  return (
    <>
      <Field>
        <FieldLabel htmlFor={`${id}-jql`}>Jira query (JQL)</FieldLabel>
        <Input
          id={`${id}-jql`}
          maxLength={4000}
          placeholder="project = DEMO ORDER BY updated DESC"
          value={value.jql}
          onChange={(event) => onChange({ ...value, jql: event.target.value })}
        />
        <FieldDescription>
          Separate from the query on Jira → Tickets. Leave empty to use the query of the last fetch on Jira → Tickets
          (number of tickets: the last fetch's, or 25).
        </FieldDescription>
      </Field>
      <Field className="w-40">
        <FieldLabel htmlFor={`${id}-limit`}>Number of tickets</FieldLabel>
        <Input
          id={`${id}-limit`}
          type="number"
          min={1}
          max={200}
          step={1}
          placeholder="25"
          value={value.limit}
          onChange={(event) => onChange({ ...value, limit: event.target.value })}
        />
      </Field>
    </>
  );
}
