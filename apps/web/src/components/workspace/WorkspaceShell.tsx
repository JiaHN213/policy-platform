"use client";

import PolicyDrawer from "@/components/policy/PolicyDrawer";
import { ErrorBox } from "@/components/policy/common";
import { api,ApiError,type Me,type Overview } from "@/lib/api";
import { BellOutlined, BookOutlined, CheckSquareOutlined, DatabaseOutlined, FileSearchOutlined, GlobalOutlined, LogoutOutlined, ProfileOutlined, ReloadOutlined, SafetyCertificateOutlined, SendOutlined, SettingOutlined, TagsOutlined } from "@ant-design/icons";
import { useMutation,useQuery,useQueryClient } from "@tanstack/react-query";
import { App,Button,Skeleton } from "antd";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext,useContext,useState,type ReactNode } from "react";
import Login from "./Login";

const customerLinks = [
  { href: "/search", label: "政策搜索", icon: FileSearchOutlined },
  { href: "/policies", label: "最新政策", icon: ProfileOutlined },
  { href: "/subscriptions", label: "我的订阅", icon: TagsOutlined },
  { href: "/notifications", label: "消息通知", icon: BellOutlined },
];
const adminLinks = [
  { href: "/admin/review", label: "处理工作台", icon: CheckSquareOutlined },
  { href: "/admin/sources", label: "来源采集", icon: GlobalOutlined },
  { href: "/admin/data", label: "政策数据", icon: DatabaseOutlined },
  { href: "/admin/publication", label: "发布后处理", icon: SendOutlined },
  { href: "/admin/knowledge", label: "政策知识库", icon: BookOutlined },
  { href: "/admin/quality", label: "质量评测", icon: SafetyCertificateOutlined },
  { href: "/admin/configuration", label: "系统配置", icon: SettingOutlined },
];
const PolicyContext = createContext<{ openPolicy: (id: string) => void }>({ openPolicy: () => {} });
export const usePolicyWorkspace = () => useContext(PolicyContext);

export default function WorkspaceShell({ area, children }: { area: "customer" | "admin"; children: ReactNode }) {
  const client = useQueryClient();
  const { message } = App.useApp();
  const path = usePathname();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("me"), retry: false });
  const overview = useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("overview"), enabled: !!me.data, refetchInterval: 30000 });
  const [selected, setSelected] = useState<string | null>(null);
  const logout = useMutation({ mutationFn: () => api("auth/logout", { method: "POST" }),
    onSuccess: () => { client.clear(); window.location.assign("/login"); }, onError: (error: Error) => message.error(error.message) });
  if (me.isLoading) return <main className="boot"><h2>正在打开工作台</h2><Skeleton active /></main>;
  if (me.error && (!(me.error instanceof ApiError) || ![401, 403].includes(me.error.status)))
    return <main className="boot"><h2>暂时无法连接工作台</h2><ErrorBox error={me.error} retry={() => me.refetch()} /></main>;
  if (!me.data) return <Login onSuccess={() => client.resetQueries({ queryKey: ["me"] })} />;
  if (area === "admin" && !me.data.is_staff) return <main className="boot"><h2>此页面仅供内部人员使用</h2><Link href="/search">返回政策搜索</Link></main>;
  const links = area === "admin" ? adminLinks : customerLinks;
  const current = links.find(item => path === item.href) || links[0];
  return <PolicyContext.Provider value={{ openPolicy: setSelected }}>
    <div className="workspace">
      <aside className="sidebar">
        <Link href={area === "admin" ? "/admin/review" : "/search"} className="wordmark"><span className="brand-mark"><FileSearchOutlined /></span>政策观察</Link>
        <div className="workspace-label">{area === "admin" ? "内部管理" : "客户工作台"}</div>
        <nav aria-label={area === "admin" ? "管理导航" : "客户导航"}>{links.map(item => <Link key={item.href} href={item.href} className={`nav-item ${path === item.href ? "active" : ""}`} aria-current={path === item.href ? "page" : undefined}><item.icon /><span>{item.label}</span>{item.href === "/notifications" && !!overview.data?.unread && <span className="nav-count">{overview.data.unread}</span>}</Link>)}</nav>
        <div className="profile"><span className="avatar">{me.data.username.slice(0, 1).toUpperCase()}</span><div><strong>{me.data.username}</strong><div className="small muted">{area === "admin" ? "内部运营与研究" : "个人工作台"}</div></div></div>
      </aside>
      <div className="main-shell">
        <header className="topbar"><span>{area === "admin" ? "内部管理" : "客户工作台"} / <strong>{current.label}</strong></span><div className="topbar-right">
          {area === "admin" ? <Link href="/search">查看客户页面</Link> : me.data.is_staff && <Link href="/admin/review">进入管理后台</Link>}
          <Button type="text" icon={<ReloadOutlined />} title="刷新数据" aria-label="刷新数据" onClick={() => client.invalidateQueries()} />
          <Button type="text" icon={<LogoutOutlined />} title="退出登录" aria-label="退出登录" loading={logout.isPending} onClick={() => logout.mutate()} />
        </div></header>
        <main className="main-content"><div className="page-heading"><h1>{current.label}</h1></div>
          <section className="content-panel">{children}</section>
        </main>
      </div>
      <PolicyDrawer id={selected} review={area === "admin"} onClose={() => setSelected(null)} onSelect={setSelected} />
    </div>
  </PolicyContext.Provider>;
}
