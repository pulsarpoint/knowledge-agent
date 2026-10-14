export interface AdminSession {
  is_admin: boolean;
}
export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function request<T>(url: string, init?: RequestInit, responseType: "json" | "text" = "json"): Promise<T> {
  const response = init ? await fetch(url, init) : await fetch(url);
  if (!response.ok) {
    let message = `request failed (${response.status})`;
    let code: string | undefined;
    try {
      const body = await response.json();
      if (body?.error?.message) message = body.error.message;
      if (typeof body?.error?.code === "string") code = body.error.code;
    } catch {
      /* non-JSON error body — keep the status message */
    }
    throw new ApiError(message, response.status, code);
  }
  if (response.status === 204) return undefined as T;
  if (responseType === "text") return response.text() as Promise<T>;
  return response.json() as Promise<T>;
}

export const getAdminSession = () => request<AdminSession>("/api/session");
