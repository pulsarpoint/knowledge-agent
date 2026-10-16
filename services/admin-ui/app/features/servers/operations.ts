import { request } from "~/lib/api";
export interface SSHServer {
  id: string;
  name: string;
  hostname: string;
  port: number;
  username: string | null;
  revision: number;
  ssh_allowed: boolean;
  enabled: boolean;
  allow_sudo: boolean;
  known_hosts: string;
  has_private_key: boolean;
  last_tested_at: string | null;
  last_test_error: string | null;
}
export interface SSHInput {
  revision: number;
  username: string | null;
  port: number;
  allow_sudo: boolean;
  known_hosts: string;
  private_key?: string | null;
}
export interface SSHTestResult {
  ok: boolean;
  message: string;
  host_key: {
    public_key: string;
    fingerprint: string;
    algorithm: string;
    known_hosts_line: string;
    verified: boolean;
  } | null;
}
const json = (method: string, value?: unknown) => ({
  method,
  headers: { "Content-Type": "application/json" },
  ...(value === undefined ? {} : { body: JSON.stringify(value) }),
});
const sshPath = (id: string) => `/api/servers/${id}/ssh`;
export const getSSH = (id: string) => request<SSHServer>(sshPath(id));
export const saveSSH = (id: string, value: SSHInput) => request<SSHServer>(sshPath(id), json("PUT", value));
export const testSSH = (id: string, value: SSHInput) =>
  request<SSHTestResult>(sshPath(id) + "/test", json("POST", value));
