"use client";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Skeleton } from "antd";
import { api, type PolicyDetail } from "@/lib/api";
import { ErrorBox } from "@/components/policy/common";
import CustomerPolicyContent from "@/components/policy/CustomerPolicyContent";
import { usePolicyWorkspace } from "@/components/workspace/WorkspaceShell";
export default function PolicyReading({ id }: { id: string }) {
  const { openPolicy } = usePolicyWorkspace();
  const query = useQuery({ queryKey: ["policy", id, false], queryFn: () => api<PolicyDetail>(`policies/${id}`) });
  return <div className="policy-reading-page"><Link className="policy-reading-back" href="/search">← 返回政策库</Link>{query.isLoading ? <Skeleton active /> : query.error ? <ErrorBox error={query.error} /> : query.data && <CustomerPolicyContent key={id} policy={query.data} onSelect={openPolicy} />}</div>;
}
