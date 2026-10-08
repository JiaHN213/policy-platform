import PolicyReading from "@/components/customer/PolicyReading";
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PolicyReading id={id} />;
}
