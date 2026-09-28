"use client";
import KnowledgeWiki from "@/components/KnowledgeWiki";
import { usePolicyWorkspace } from "@/components/workspace/WorkspaceShell";
export default function Page() {
  const { openPolicy } = usePolicyWorkspace();
  return <KnowledgeWiki isStaff onSelect={openPolicy} />;
}
