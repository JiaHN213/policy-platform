"use client";
import UnifiedSearch from "@/components/UnifiedSearch";
import { PolicyCards } from "@/components/policy/common";
import { usePolicyWorkspace } from "@/components/workspace/WorkspaceShell";
export default function Page() {
  const { openPolicy } = usePolicyWorkspace();
  return <UnifiedSearch onSelect={openPolicy} renderPolicies={(items, search) => <PolicyCards items={items} search={search} onSelect={openPolicy} />} />;
}
