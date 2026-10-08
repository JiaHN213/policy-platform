"use client";
import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Card, Collapse, Empty, Select, Space, Tag } from "antd";
import { api, type Page } from "@/lib/api";
import { usePolicyWorkspace } from "@/components/workspace/WorkspaceShell";
import ContinuousMatching from "./ContinuousMatching";
type Rule = { name: string; business_domain: string; label: string; target_view: string; interest_regions: string[] };
export default function ProfileSubscription({ initialProfile, initialProject }: { initialProfile?: string; initialProject?: string }) {
  const [selected, setSelected] = useState(initialProfile || "");
  const [project, setProject] = useState(initialProject || "");
  const [preview, setPreview] = useState<{ scope: string; label: string; count: number; items: { id: string; title: string }[] }>();
  const client = useQueryClient();
  const { message } = App.useApp();
  const { openPolicy } = usePolicyWorkspace();
  const profiles = useQuery({ queryKey: ["enterprises"], queryFn: () => api<{ items: { id: string; name: string }[] }>("enterprises") });
  const profile = profiles.data?.items.find(item => item.id === selected) || profiles.data?.items[0];
  const projects = useQuery({ queryKey: ["enterprise-projects", profile?.id], queryFn: () => api<Page<{ id: string; name: string }>>(`enterprise-projects?profile=${profile!.id}&page_size=100`), enabled: !!profile });
  const scope = `${profile?.id}:${project}`;
  const plan = useQuery({ queryKey: ["subscription-plan", profile?.id, project], queryFn: () => api<{ revision: number; enabled: boolean; rules: Rule[]; notice: string }>(`enterprises/${profile!.id}/subscription-plan${project ? `?project_id=${project}` : ""}`), enabled: !!profile });
  const save = useMutation({ mutationFn: (enabled: boolean) => api<{ message: string; status: string }>(`enterprises/${profile!.id}/subscription-plan`, { method: "POST", body: JSON.stringify({ enabled, revision: plan.data!.revision, ...(project ? { project_id: project } : {}) }) }), onSuccess: result => { if (result.status === "unavailable") message.warning(result.message); else message.success(result.message); for (const key of ["subscription-plan", "subscriptions", "enterprises", "enterprise-projects"]) void client.invalidateQueries({ queryKey: [key] }); }, onError: error => message.error(error.message) });
  const check = useMutation({ mutationFn: async (rule: Rule) => ({ ...await api<{ count: number; items: { id: string; title: string }[] }>("subscriptions/preview", { method: "POST", body: JSON.stringify({ name: rule.name, business_domain: rule.business_domain, target_view: rule.target_view, interest_regions: rule.interest_regions }) }), scope, label: rule.label }), onSuccess: setPreview, onError: error => message.error(error.message) });
  return <Card title="按企业／项目订阅" className="space-bottom" loading={profiles.isLoading}>
    {(profiles.error || projects.error || plan.error) && <Alert type="error" title={(profiles.error || projects.error || plan.error)?.message} />}
    {!profile ? <Empty description={<span>暂无企业资料，<Link href="/enterprise">先添加企业</Link>，也可使用下方自定义订阅。</span>} /> : <>
      <Space wrap className="space-bottom"><Select aria-label="订阅企业" style={{ minWidth: 200 }} disabled={save.isPending} value={profile.id} onChange={value => { setSelected(value); setProject(""); }} options={profiles.data?.items.map(item => ({ value: item.id, label: item.name }))} /><Select aria-label="订阅对象" style={{ minWidth: 160 }} disabled={save.isPending} value={project} onChange={setProject} options={[{ value: "", label: "企业整体" }, ...(projects.data?.items.map(item => ({ value: item.id, label: item.name })) || [])]} /></Space>
      {plan.data && <><p>{plan.data.notice}</p>{plan.data.rules.map(rule => <div className="spread space-bottom" key={rule.business_domain}><span><Tag>{rule.label}</Tag>政策与机会 · {rule.interest_regions.join("、") || "不限关注地区"}</span><Button loading={check.isPending} onClick={() => check.mutate(rule)}>预览此领域</Button></div>)}
        {!plan.data.rules.length && <p>尚未确定业务领域，请先在企业与项目中补充。</p>}
        <Button type="primary" disabled={!plan.data.rules.length || plan.data.enabled} loading={save.isPending} onClick={() => save.mutate(true)}>{plan.data.enabled ? "已开启资料跟随" : "确认开启订阅并跟随资料更新"}</Button>
        {plan.data.enabled && <Button className="space-left" loading={save.isPending} onClick={() => save.mutate(false)}>停止资料跟随</Button>}
        <p className="small muted">停止跟随只停止自动调整规则，不会关闭已有提醒；要停止提醒，请在下方已有订阅中关闭对应规则。</p>
      </>}
      {preview?.scope === scope && <Alert type="info" title={`${preview.label} · 当前匹配 ${preview.count} 份政策`} description={<>{preview.items.slice(0, 5).map(item => <div key={item.id}><Button type="link" onClick={() => openPolicy(item.id)}>{item.title}</Button></div>)}<span>仅预览，不会自动创建订阅。</span></>} />}
      <Collapse ghost items={[{ key: "continuous", label: "持续关注政策条件变化（可选）", children: <ContinuousMatching profile={profile.id} projects={projects.data?.items || []} openPolicy={openPolicy} /> }]} />
    </>}
  </Card>;
}
