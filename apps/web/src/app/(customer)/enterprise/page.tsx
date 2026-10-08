import EnterprisePanel from "@/components/customer/EnterprisePanel";
export default async function Page({ searchParams }: { searchParams: Promise<{ profile?: string; result?: string }> }) {
  const params = await searchParams;
  return <EnterprisePanel key={`${params.profile || ""}:${params.result || ""}`} initialProfile={params.profile} initialResult={params.result} />;
}
