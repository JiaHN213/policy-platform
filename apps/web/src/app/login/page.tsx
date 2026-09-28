"use client";
import Login from "@/components/workspace/Login";
export default function Page() {
  return <Login onSuccess={() => {
    const next = new URLSearchParams(window.location.search).get("next") || "/search";
    const allowed = ["/search", "/policies", "/subscriptions", "/notifications", "/admin/review", "/admin/sources", "/admin/data", "/admin/publication", "/admin/knowledge", "/admin/quality", "/admin/configuration"];
    let destination = "/search";
    try {
      const target = new URL(next, window.location.origin);
      if (target.origin === window.location.origin && allowed.includes(target.pathname)) {
        destination = target.pathname + target.search;
      }
    } catch {
      // Invalid return URLs fall back to the customer search page.
    }
    window.location.assign(destination);
  }} />;
}
