import { redirect } from "next/navigation";
export default async function Home({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const params = await searchParams;
  const routes: Record<string, string> = { search: "/search", policies: "/policies", subscriptions: "/subscriptions", notifications: "/notifications", management: "/admin/review", knowledge: "/admin/knowledge" };
  const section = typeof params.section === "string" ? params.section : "search";
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (key !== "section" && typeof value === "string") query.set(key, value);
  redirect((routes[section] || "/search") + (query.size ? `?${query}` : ""));
}
