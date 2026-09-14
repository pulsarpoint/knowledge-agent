export const serviceTypes = {
  unclassified: "Unclassified",
  custom: "Custom application",
  builtin: "Built-in software",
  external: "External service",
} as const;

// One semantic palette shared by TALA, the interactive graph, and their legend.
export const serviceRoles = {
  unclassified: { label: "Unclassified", fill: "#f1f5f9", stroke: "#64748b", text: "#334155" },
  application: { label: "Application", fill: "#dbeafe", stroke: "#2563eb", text: "#1e3a8a" },
  proxy: { label: "Proxy", fill: "#ffedd5", stroke: "#c2410c", text: "#7c2d12" },
  web_server: { label: "Web server", fill: "#cffafe", stroke: "#0e7490", text: "#164e63" },
  database: { label: "Database", fill: "#ede9fe", stroke: "#7c3aed", text: "#4c1d95" },
  cache: { label: "Cache", fill: "#dcfce7", stroke: "#15803d", text: "#14532d" },
  message_broker: { label: "Message broker", fill: "#fef3c7", stroke: "#b45309", text: "#78350f" },
  worker: { label: "Worker", fill: "#fce7f3", stroke: "#be185d", text: "#831843" },
  other: { label: "Other", fill: "#e2e8f0", stroke: "#475569", text: "#1e293b" },
} as const;

export type ServiceType = keyof typeof serviceTypes;
export type ServiceRole = keyof typeof serviceRoles;
