import type { components } from "./schema";
import { explainSystemText } from "./system-messages";

export type Policy = components["schemas"]["Policy"];
export type PolicyDetail = components["schemas"]["PolicyDetail"];
export type Subscription = components["schemas"]["Subscription"];
export type Notification = components["schemas"]["Notification"];
export type Source = components["schemas"]["Source"];
export type SourceRun = components["schemas"]["SourceRun"];
export type DiscoveredItem = components["schemas"]["DiscoveredItem"];
export type Page<T> = {
  items: T[];
  count: number;
  next: string | null;
  previous: string | null;
};
export type Me = {
  id: number;
  username: string;
  is_staff: boolean;
  capabilities: Record<string, { allowed: boolean }>;
};
export type Overview = {
  policies: number;
  sources: number;
  verified_sources: number;
  subscriptions: number;
  unread: number;
  demo_count: number;
  search_backend: string;
};
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  const method = options.method || "GET";
  if (method !== "GET") {
    const csrf = await fetch("/api/v1/auth/csrf", {
      credentials: "same-origin",
    });
    if (!csrf.ok)
      throw new ApiError(csrf.status, "无法验证会话，请刷新后重试。");
    headers.set("X-CSRFToken", (await csrf.json()).csrf_token);
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`/api/v1/${path}`, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      explainSystemText(body.message) || "服务暂时不可用，请稍后重试。",
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export function safeExternalUrl(url: string) {
  try {
    const parsed = new URL(url);
    return ["https:", "http:"].includes(parsed.protocol) ? url : undefined;
  } catch {
    return undefined;
  }
}
