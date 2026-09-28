import WorkspaceShell from "@/components/workspace/WorkspaceShell";
import { requireInternalUser } from "@/lib/server-session";
export default async function Layout({ children }: { children: React.ReactNode }) {
  await requireInternalUser();
  return <WorkspaceShell area="admin">{children}</WorkspaceShell>;
}
