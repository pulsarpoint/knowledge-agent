import { request } from "~/lib/api";

export interface SkillSummary {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  revision: number;
  created_at: string;
  updated_at: string;
}
export interface Skill extends SkillSummary {
  instructions: string;
}
export interface SkillInput {
  name: string;
  description: string;
  instructions: string;
  enabled: boolean;
  revision?: number;
}
export const getSkills = (signal?: AbortSignal) => request<{ skills: SkillSummary[] }>("/api/skills", { signal });
export const getSkill = (id: string) => request<Skill>(`/api/skills/${encodeURIComponent(id)}`);
export const saveSkill = (value: SkillInput, id?: string) =>
  request<Skill>(`/api/skills${id ? `/${encodeURIComponent(id)}` : ""}`, {
    method: id ? "PUT" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
export const setSkillEnabled = (skill: SkillSummary, enabled: boolean) =>
  request<SkillSummary>(`/api/skills/${encodeURIComponent(skill.id)}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled, revision: skill.revision }),
  });
