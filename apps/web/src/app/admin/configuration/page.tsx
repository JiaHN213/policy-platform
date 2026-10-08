import SystemConfigCenter from "@/components/SystemConfigCenter";
import { requireInternalUser } from "@/lib/server-session";
export default async function Page() { await requireInternalUser(true); return <SystemConfigCenter />; }
