import { request } from './api';
import type { AgentName } from './prompts';

export type Provider = 'openai' | 'anthropic' | 'bedrock';
export interface ModelInput {
  name: string; provider: Provider; model: string; api_format: 'chat_completions' | 'responses' | 'native' | 'embeddings';
  embedding_dimensions?: number | null; embedding_version?: string | null;
  base_url: string | null; aws_region: string | null; enabled: boolean; api_key?: string | null; revision?: number;
}
export interface ModelProfile extends Omit<ModelInput, 'api_key' | 'revision'> {
  id: string; revision: number; has_api_key: boolean; created_at: string; updated_at: string;
}
export interface Assignments { search_model_id: string | null; code_model_id: string | null; operations_model_id?: string | null; revision: number }
export interface LlmSettings { models: ModelProfile[]; assignments: Assignments }
export const getLlmSettings = () => request<LlmSettings>('/api/llm');
export const saveModel = (value: ModelInput, id?: string) => request<ModelProfile>(`/api/llm/models${id ? `/${encodeURIComponent(id)}` : ''}`, {
  method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
});
export const saveAgentModel = (agent: AgentName, modelId: string | null, revision: number) => request<Assignments>('/api/llm/assignments', {
  method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ [`${agent}_model_id`]: modelId, revision }),
});
export const testModel = (model: ModelProfile) => request<{ ok: boolean; message: string }>(`/api/llm/models/${encodeURIComponent(model.id)}/test`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: model.revision }),
});
export const codeCompatible = (model: ModelProfile) => model.provider === 'openai' ? model.api_format === 'responses'
  : model.provider === 'anthropic' || /anthropic[./]claude/.test(model.model);
export const providerLabels: Record<Provider, string> = { openai: 'OpenAI compatible', anthropic: 'Anthropic', bedrock: 'AWS Bedrock' };
