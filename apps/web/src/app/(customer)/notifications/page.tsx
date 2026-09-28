"use client";
import NotificationPanel from "@/components/customer/NotificationPanel";
import { usePolicyWorkspace } from "@/components/workspace/WorkspaceShell";
export default function Page() {
  const { openPolicy } = usePolicyWorkspace();
  return <NotificationPanel onSelect={openPolicy} />;
}
