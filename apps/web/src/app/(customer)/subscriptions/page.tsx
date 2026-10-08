import SubscriptionWorkspace from "@/components/customer/SubscriptionWorkspace";
export default async function Page({ searchParams }: { searchParams: Promise<{ tab?: string; profile?: string; project?: string }> }) {
  const { tab, profile, project } = await searchParams;
  return <SubscriptionWorkspace key={`${tab || "subscriptions"}:${profile || ""}:${project || ""}`} messages={tab === "messages"} initialProfile={profile} initialProject={project} />;
}
