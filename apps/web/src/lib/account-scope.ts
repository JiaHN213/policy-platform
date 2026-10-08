import { api } from "./api";

export function accountPath(path: string, userId?: number) {
  if (!userId) return path;
  const [base, query = ""] = path.split("?");
  const params = new URLSearchParams(query);
  params.set("user_id", String(userId));
  return `${base}?${params}`;
}
export function accountApi(userId?: number) {
  return <T>(path: string, options?: RequestInit) =>
    api<T>(accountPath(path, userId), options);
}
