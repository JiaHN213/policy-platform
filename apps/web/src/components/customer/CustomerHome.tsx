"use client";
import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Col, Empty, Row, Select, Skeleton, Space, Tag } from "antd";
import { api, type Page, type Notification } from "@/lib/api";
import { usePolicyWorkspace } from "@/components/workspace/WorkspaceShell";
import ReadableText from "@/components/policy/ReadableText";
import LatestPolicies from "./LatestPolicies";

type Match = { policy_id: string; title: string; summary: string; reasons: string[]; gaps: string[]; deadline: string | null; opportunities: { support_content: string }[]; conditions: { label: string } };
type Home = { recommended: Match[]; uncertain: Match[]; deadlines: Match[]; counts: { recommended: number; uncertain: number }; notice: string };
export default function CustomerHome() {
  const { openPolicy } = usePolicyWorkspace();
  const [selected, setSelected] = useState("");
  const [project, setProject] = useState("");
  const profiles = useQuery({ queryKey: ["enterprises"], queryFn: () => api<{ items: { id: string; name: string }[] }>("enterprises") });
  const profile = profiles.data?.items.find(item => item.id === selected) || profiles.data?.items[0];
  const projects = useQuery({ queryKey: ["enterprise-projects", profile?.id], queryFn: () => api<Page<{ id: string; name: string }>>(`enterprise-projects?profile=${profile!.id}&page_size=100`), enabled: !!profile });
  const home = useQuery({ queryKey: ["customer-home", profile?.id, project], queryFn: () => api<Home>(`enterprises/${profile!.id}/home?${new URLSearchParams({ project_id: project })}`), enabled: !!profile, refetchInterval: 60000 });
  const results = useQuery({ queryKey: ["enterprise-recent"], queryFn: () => api<{ items: { id: string; kind: string; inputs: { name?: string }; created_at: string }[] }>("enterprise-research"), refetchInterval: 15000 });
  const notifications = useQuery({ queryKey: ["home-notifications"], queryFn: () => api<Page<Notification>>("notifications?status=unread"), refetchInterval: 30000 });
  const cards = (items: Match[], uncertain = false) => items.length ? <div className="enterprise-card-grid">{items.map(item => <Card key={item.policy_id} size="small" title={<button className="policy-title" onClick={() => openPolicy(item.policy_id)}>{item.title}</button>}>
    <Tag color={uncertain ? "orange" : "cyan"}>{uncertain ? "待补充核对" : "推荐关注"}</Tag><ReadableText text={item.opportunities.map(opportunity => opportunity.support_content).filter(Boolean).join("；") || item.summary} previewChars={180} expandLabel="展开支持内容" />
    <p>{item.reasons.slice(0, 2).join("；")}</p><p className="small muted">{item.conditions.label}</p>
    {item.deadline && <p>最近截止：{new Date(item.deadline).toLocaleDateString("zh-CN")}</p>}
    {uncertain && <><p className="small muted">{item.gaps.slice(0, 2).join("；")}</p><Link href={`/enterprise?profile=${profile?.id || ""}`}>补充企业／项目资料</Link></>}
    <div><Button onClick={() => openPolicy(item.policy_id)}>查看政策</Button></div>
  </Card>)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={uncertain ? "暂无需要补充判断的线索" : "暂未找到条件较明确的推荐机会，可查看下方线索或搜索政策库"} />;
  if (profiles.isLoading) return <Skeleton active />;
  if (profiles.error) return <Alert type="error" title={profiles.error.message} action={<Button onClick={() => void profiles.refetch()}>重试</Button>} />;
  return <>
    <div className="spread space-bottom"><div><h2>与你相关的政策</h2><p className="muted">先看值得关注的机会，再按需要完善资料。</p></div><Link href="/search"><Button>搜索全部政策</Button></Link></div>
    {profile ? <Space wrap className="space-bottom"><Select aria-label="当前企业" value={profile.id} style={{ minWidth: 220 }} options={profiles.data?.items.map(item => ({ value: item.id, label: item.name }))} onChange={value => { setSelected(value); setProject(""); }} /><Select aria-label="关注对象" value={project} style={{ minWidth: 180 }} options={[{ value: "", label: "企业整体" }, ...(projects.data?.items.map(item => ({ value: item.id, label: item.name })) || [])]} onChange={setProject} /><Link href={`/home/matches?profile=${profile.id}&project=${project}`}>查看全部相关政策</Link></Space> : <Alert type="info" showIcon title="完善企业信息后，可查看与你相关的政策" description="可提供官网、上传介绍或粘贴简介，也可以稍后填写。" action={<Link href="/welcome"><Button>完善企业信息</Button></Link>} className="space-bottom" />}
    <Row gutter={[16, 16]} className="space-bottom">
      <Col xs={24} lg={12}><Card size="small" title="待确认资料与可查看结果">
        {results.error ? <Alert type="warning" title="暂时无法读取结果" /> : !results.data?.items.length ? <p className="muted">暂无待查看结果。</p> : results.data.items.slice(0, 5).map(item => <p key={item.id}><Link href={`/enterprise?result=${item.id}`}>{item.kind === "company" ? "核对企业资料" : item.kind === "project" ? "核对项目资料" : "查看政策适用分析"}{item.inputs.name ? ` · ${item.inputs.name}` : ""}</Link></p>)}
      </Card></Col>
      <Col xs={24} lg={12}><Card size="small" title="政策更新与截止提醒" extra={<Link href="/subscriptions?tab=messages">全部消息</Link>}>
        {notifications.error && <Alert type="warning" title="暂时无法读取提醒" />}
        {notifications.data?.items.slice(0, 3).map(item => <p key={item.id}><Link href={item.policy_id ? `/policies/${item.policy_id}` : "/subscriptions?tab=messages"}>{item.title}</Link></p>)}
        {home.data?.deadlines.map(item => <p key={item.policy_id}><Tag color="orange">{new Date(item.deadline!).toLocaleDateString("zh-CN")} 截止</Tag><button className="policy-title" onClick={() => openPolicy(item.policy_id)}>{item.title}</button></p>)}
        {!notifications.data?.items.length && !home.data?.deadlines.length && <p className="muted">暂无新提醒。<Link href="/subscriptions">设置关注内容</Link></p>}
      </Card></Col>
    </Row>
    {!profile ? <LatestPolicies onSelect={openPolicy} /> : home.isLoading ? <Skeleton active /> : home.error ? <Alert type="error" title={home.error.message} action={<Button onClick={() => void home.refetch()}>重试</Button>} /> : home.data && <>
      <h3>推荐关注 <span className="small muted">{home.data.counts.recommended} 份</span></h3>{cards(home.data.recommended)}
      <h3>补充信息后可判断 <span className="small muted">{home.data.counts.uncertain} 份</span></h3>{cards(home.data.uncertain, true)}<p className="small muted">{home.data.notice}</p>
    </>}
  </>;
}
