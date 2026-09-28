"use client";
import LatestPolicies from "@/components/customer/LatestPolicies";
import { usePolicyWorkspace } from "@/components/workspace/WorkspaceShell";
export default function Page() {
  const { openPolicy } = usePolicyWorkspace();
  return <LatestPolicies onSelect={openPolicy} />;
}
