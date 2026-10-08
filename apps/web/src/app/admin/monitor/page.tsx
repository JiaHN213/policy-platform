import InternalMonitor from "@/components/InternalMonitor";
import { requireInternalUser } from "@/lib/server-session";
export default async function Page() { await requireInternalUser(true); return <InternalMonitor />; }
