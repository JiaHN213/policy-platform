import EnterprisePanel from "@/components/customer/EnterprisePanel";
export default async function Page({ searchParams }: { searchParams: Promise<{ profile?: string; project?: string }> }) {
  const params = await searchParams;
  return <EnterprisePanel key={`${params.profile || ""}:${params.project || ""}`} matchesOnly initialProfile={params.profile} initialProject={params.project} />;
}
