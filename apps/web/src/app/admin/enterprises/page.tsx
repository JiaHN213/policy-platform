import AccountManagement from "@/components/management/AccountManagement";
import { requireInternalUser } from "@/lib/server-session";
export default async function Page() { await requireInternalUser(true); return <AccountManagement enterprisesOnly />; }
