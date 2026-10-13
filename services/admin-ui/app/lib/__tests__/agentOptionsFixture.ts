import type { AgentOptions } from '../sessions';

export const options: AgentOptions = {
  models: [{ id: 'm1', name: 'GPT' }, { id: 'm2', name: 'Claude' }], default_model_id: 'm2',
  prompts: [{ id: 'p1', name: 'Careful reviser', content: 'You revise carefully.' }],
  default_prompt: { id: null, name: 'Built-in', content: 'Built-in prompt.' },
  resources: {
    servers: [{ id: 's1', name: 'web-1', linked: true, usable: true, reason: null },
      { id: 's2', name: 'prod-1', linked: true, usable: false, reason: 'SSH is not set up for this server.' }],
    services: [{ id: 'v1', name: 'Payments', linked: true, brings_code: false, brings_servers: true }],
    environments: [], code_projects: [{ id: 'c1', name: 'Shop', linked: false }], collections: [] },
  tools: [{ name: 'ssh_run', label: 'SSH', summary: 'Run a command', group: 'servers', needs: 'ssh', requires: 'at least one server' },
    { name: 'read_file', label: 'Read file', summary: 'Read a file', group: 'code', needs: 'files', requires: 'a code project or a collection' },
    { name: 'get_service_info', label: 'Service info', summary: 'A service', group: 'inventory', needs: null, requires: null }],
  fixed_tools: [{ name: 'get_doc', label: 'Read the document' }, { name: 'save_doc', label: 'Save the document' }, { name: 'edit_doc', label: 'Edit the document' }],
  experts: [{ id: 'e1', name: 'Payments expert', when_to_use: 'Payments questions' }],
};
