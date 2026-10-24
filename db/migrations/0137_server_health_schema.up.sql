BEGIN;
SET search_path = ka, public;

-- 0137: a built-in output schema, "Server health check", in every workspace that has none of that name: the result of
-- an agent that checks a server, with one finding per problem (severity, category, service, a stable title, details,
-- evidence, recommendation) so a handler can turn each issue into a ticket. A workspace that already has a schema of
-- this name keeps its own.
WITH seeded AS (
    INSERT INTO output_schemas (project_id, name, description)
    SELECT p.id, 'Server health check',
        'A server check: the overall status, a summary, and one finding per problem for tickets (empty when normal).'
    FROM projects p
    WHERE NOT EXISTS (SELECT 1 FROM output_schemas s WHERE s.project_id = p.id AND s.name = 'Server health check')
    RETURNING id
)
INSERT INTO output_schema_versions (schema_id, version, schema)
SELECT id, 1, $h${
  "type": "object",
  "properties": {
    "server": {
      "type": "string",
      "description": "Name of the server that was checked, as recorded in the inventory."
    },
    "overall_status": {
      "type": "string",
      "enum": [
        "normal",
        "worth_checking",
        "issue"
      ],
      "description": "normal: nothing found. worth_checking: something may become a problem. issue: something is broken now."
    },
    "summary": {
      "type": "string",
      "description": "One or two sentences on the state of the server."
    },
    "findings": {
      "type": "array",
      "description": "One entry per problem found; empty when overall_status is normal.",
      "items": {
        "type": "object",
        "properties": {
          "severity": {
            "type": "string",
            "enum": [
              "issue",
              "worth_checking"
            ],
            "description": "issue: broken now. worth_checking: likely to become a problem."
          },
          "category": {
            "type": "string",
            "enum": [
              "disk",
              "memory",
              "cpu",
              "service_down",
              "service_restarting",
              "errors_in_logs",
              "certificate",
              "backup",
              "security",
              "other"
            ],
            "description": "What kind of problem it is."
          },
          "service": {
            "type": [
              "string",
              "null"
            ],
            "description": "The affected service as recorded in the inventory, or null when it concerns the server itself."
          },
          "title": {
            "type": "string",
            "description": "Short ticket title, e.g. 'Disk /var at 92% on app-1'. Keep it stable between checks for the same problem."
          },
          "details": {
            "type": "string",
            "description": "What was found and why it matters; this becomes the ticket description."
          },
          "evidence": {
            "type": "string",
            "description": "The commands run and the relevant output lines that show the problem."
          },
          "recommendation": {
            "type": "string",
            "description": "What should be done to fix or prevent it."
          }
        },
        "required": [
          "severity",
          "category",
          "service",
          "title",
          "details",
          "evidence",
          "recommendation"
        ],
        "additionalProperties": false
      }
    }
  },
  "required": [
    "server",
    "overall_status",
    "summary",
    "findings"
  ],
  "additionalProperties": false
}$h$::jsonb FROM seeded;

COMMIT;
