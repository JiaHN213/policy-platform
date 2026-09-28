"use client";
import ReviewPanel from "@/components/management/ReviewPanel";
import { usePolicyWorkspace } from "@/components/workspace/WorkspaceShell";
export default function Page() {
  const { openPolicy } = usePolicyWorkspace();
  return <ReviewPanel onSelect={openPolicy} />;
}
