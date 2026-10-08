"use client";

import PolicyDrawer from "@/components/policy/PolicyDrawer";
import { ErrorBox } from "@/components/policy/common";
import { api,ApiError,type Me,type Overview } from "@/lib/api";
import { BellOutlined, BookOutlined, CheckSquareOutlined, DatabaseOutlined, FileSearchOutlined, GlobalOutlined, HomeOutlined, LogoutOutlined, ReloadOutlined, SafetyCertificateOutlined, SendOutlined, SettingOutlined, DashboardOutlined } from "@ant-design/icons";
import { useMutation,useQuery,useQueryClient } from "@tanstack/react-query";
import { App,Button,Skeleton } from "antd";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext,useContext,useState,type ReactNode } from "react";
import Login from "./Login";

const customerLinks = [
  { href: "/home", label: "首页", icon: HomeOutlined },
  { href: "/search", label: "政策库", icon: FileSearchOutlined },
  { href: "/enterprise", label: "企业与项目", icon: DatabaseOutlined },
  { href: "/subscriptions", label: "订阅与消息", icon: BellOutlined },
];
const adminLinks = [
  { href: "/admin/users", label: "用户管理", icon: HomeOutlined },
  { href: "/admin/enterprises", label: "企业管理", icon: DatabaseOutlined },
  { href: "/admin/review", label: "处理工作台", icon: CheckSquareOutlined },
  { href: "/admin/sources", label: "来源采集", icon: GlobalOutlined },
  { href: "/admin/data", label: "政策数据", icon: DatabaseOutlined },
  { href: "/admin/publication", label: "发布后处理", icon: SendOutlined },
  { href: "/admin/knowledge", label: "政策知识库", icon: BookOutlined },
  { href: "/admin/quality", label: "质量评测", icon: SafetyCertificateOutlined },
  { href: "/admin/configuration", label: "系统配置", icon: SettingOutlined },
  { href: "/admin/monitor", label: "运行监控", icon: DashboardOutlined },
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
  const systemRoutes = ["/admin/users", "/admin/enterprises", "/admin/monitor", "/admin/configuration", "/admin/publication"];
  if (area === "admin" && systemRoutes.includes(path) && !me.data.can_manage_system) return <main className="boot"><h2>此页面仅供系统管理员使用</h2><Link href="/admin/review">返回日常处理</Link></main>;
  const links = area === "admin" ? adminLinks.filter(item => me.data.can_manage_system || !systemRoutes.includes(item.href)) : customerLinks;
  const activePath = path.startsWith("/policies") ? "/search" : path === "/notifications" ? "/subscriptions" : path.startsWith("/home") ? "/home" : path;
  const current = path === "/account" ? { label: "账户设置" } : path === "/welcome" ? { label: "完善企业信息（可选）" } : path.startsWith("/policies/") ? { label: "政策详情" } : path === "/home/matches" ? { label: "相关政策" } : links.find(item => activePath === item.href) || links[0];
  const groups = area === "admin" ? [
    { label: "日常处理", routes: ["/admin/review", "/admin/sources", "/admin/data"] },
    { label: "知识与质量", routes: ["/admin/knowledge", "/admin/quality"] },
    { label: "账号与企业", routes: ["/admin/users", "/admin/enterprises"] },
    { label: "系统维护", routes: ["/admin/monitor", "/admin/publication", "/admin/configuration"] },
  ] : [{ label: "", routes: customerLinks.map(item => item.href) }];
  return <PolicyContext.Provider value={{ openPolicy: setSelected }}>
    <div className="workspace">
      <aside className="sidebar">
        <Link href={area === "admin" ? "/admin/review" : "/home"} className="wordmark"><span className="brand-mark"><FileSearchOutlined /></span>政策观察</Link>
        <div className="workspace-label">{area === "admin" ? "内部管理" : "客户工作台"}</div>
        <nav aria-label={area === "admin" ? "管理导航" : "客户导航"}>{groups.map(group => <div key={group.label} className="nav-group">{!!group.label && links.some(item => group.routes.includes(item.href)) && <div className="nav-group-title">{group.label}</div>}{links.filter(item => group.routes.includes(item.href)).map(item => <Link key={item.href} href={item.href} className={`nav-item ${activePath === item.href ? "active" : ""}`} aria-current={activePath === item.href ? "page" : undefined}><item.icon /><span>{item.label}</span>{item.href === "/subscriptions" && !!overview.data?.unread && <span className="nav-count">{overview.data.unread}</span>}</Link>)}</div>)}</nav>
        <div className="profile"><span className="avatar">{me.data.username.slice(0, 1).toUpperCase()}</span><div><strong>{me.data.username}</strong><div className="small muted">{area === "admin" ? "内部运营与研究" : "个人工作台"}</div></div></div>
      </aside>
      <div className="main-shell">
        <header className="topbar"><span>{area === "admin" ? "内部管理" : "客户工作台"} / <strong>{current.label}</strong></span><div className="topbar-right">
          {area === "admin" ? <Link href="/home">查看客户页面</Link> : me.data.is_staff && <Link href="/admin/review">进入管理后台</Link>}
          <Link href="/account">账户设置</Link>
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
