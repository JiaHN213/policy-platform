"use client";
import PublicationProcessing from "@/components/PublicationProcessing";
import { usePolicyWorkspace } from "@/components/workspace/WorkspaceShell";
export default function Page() {
  const { openPolicy } = usePolicyWorkspace();
  return <PublicationProcessing onSelect={openPolicy} />;
}
