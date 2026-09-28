import "server-only";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import type { Me } from "./api";

export async function requireInternalUser() {
  const jar = await cookies();
  if (!jar.get("sessionid")) redirect("/login?next=/admin/review");
  const requestHeaders = await headers();
  const backend = process.env.API_INTERNAL_URL || "http://127.0.0.1:8000";
  const response = await fetch(`${backend}/api/v1/me`, {
    headers: { Cookie: jar.toString(), Host: requestHeaders.get("host") || "127.0.0.1" },
    cache: "no-store", signal: AbortSignal.timeout(10000),
  });
  if ([401, 403].includes(response.status)) redirect("/login?next=/admin/review");
  if (!response.ok) throw new Error("暂时无法连接管理服务，请稍后重试。");
  const me = await response.json() as Me;
  if (!me.is_staff) redirect("/search");
  return me;
}
