"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Card, Checkbox, Collapse, Empty, Form, Input, Modal, Pagination, Popconfirm, Result, Select, Space, Spin, Tabs, Tag } from "antd";
import { accountApi } from "@/lib/account-scope";
import ReadableText from "@/components/policy/ReadableText";
import Link from "next/link";
import { api, safeExternalUrl, type Page } from "@/lib/api";
import { usePolicyWorkspace } from "@/components/workspace/WorkspaceShell";
import EnterpriseIntake, { type IntakeInput } from "./EnterpriseIntake";
import OpportunityPreview from "./OpportunityPreview";
import MatchingResults from "./MatchingResults";
import { useRouter } from "next/navigation";
import MatchConditions, { CheckList, type ConditionCheck, type Conditions } from "./MatchConditions";

type Values = Record<string, string | string[]>;
type Source = { url: string; title: string; quote: string; retrieved_at: string; material: string; source_type: string };
type SubscriptionResult = { status: "created" | "unchanged" | "unavailable"; created_count: number; existing_count: number; message: string };
export type Profile = { matching_task?: { id: string | null; message: string } | null; id: string; name: string; data: Values; evidence: Record<string, { origin: string; sources: Source[] }>; revision: number; confirmed_at: string; can_edit: boolean; refresh_days: number; next_research_at: string | null; research_method: string; subscription_result?: SubscriptionResult | null; follow_subscriptions: boolean; follow_status: string };
type Candidate = { name: string; data: Values; evidence: Record<string, Source[]>; identity_evidence: Source };
export type Project = { follow_subscriptions: boolean; follow_status: string; id: string; profile: string; name: string; description: string; data: Values; revision: number };
export type Options = { workflow_max_policies: number; fields: Record<string, string>; tags: Record<string, { value: string; label: string }[]>; search_ready: boolean; ai_ready: boolean; matching_ai_ready: boolean; levels: Record<string, string> };
type Point = { text: string; quote: string; profile_facts: Values; source?: { source_name: string; start_offset: number } };
type Run = { agent?: { enabled?: boolean; model?: string; usage?: { reads?: number; calls?: number }; max_reads?: number; max_calls?: number; steps?: { sequence: number; label: string; status: string; detail: string }[] }; id: string; kind: string; profile: string | null; inputs: Record<string, unknown>; status: string; stage: string; error: string; result: { candidates?: Candidate[]; warnings?: string[]; sources?: Source[]; notice?: string; name?: string; description?: string; data?: Values; points?: Point[]; conditions?: Conditions; additional_conditions?: ConditionCheck[]; coverage?: { partial: boolean; covered_characters: number; total_characters: number }; policy_id?: string } };
type Match = { region_preference: { matched: boolean; label: string }; policy_id: string; title: string; summary: string; level: string; level_label: string; reasons: string[]; gaps: string[]; requirements: string[]; deadline: string | null; opportunities: { id: string; title: string; support_content: string }[]; recommendation_group: string; recommendation_label: string; evidence_readiness: { status: string; label: string; reason: string }; conditions: { status: string; label: string; notice: string; opportunities: { opportunity_id: string; title: string; label: string; checks: ConditionCheck[]; gaps: string[] }[] } };
type Matches = { items: Match[]; count: number; counts: Record<string, number>; notice: string; conflicting_policies: number; search_backend: string };
const colors: Record<string, string> = { high: "green", medium: "blue", low: "default", insufficient: "orange" };

function Sources({ sources }: { sources?: Source[] }) {
  if (!sources?.length) return null;
  return <Collapse size="small" ghost items={[{ key: "source", label: `查看依据 · ${sources.length}处`, children: sources.map((source, i) => <div key={i} className="enterprise-source">
    {safeExternalUrl(source.url) ? <a href={safeExternalUrl(source.url)} target="_blank" rel="noreferrer">{source.title || "查看来源"}</a> : <strong>{source.title}</strong>}
    <p className="small muted">{source.source_type} · {source.material} · {new Date(source.retrieved_at).toLocaleDateString("zh-CN")}</p>
    <blockquote className="reading-quote"><ReadableText text={source.quote} previewChars={320} expandLabel="展开原文依据" /></blockquote>
  </div>) }]} />;
}

function display(value: string | string[] | undefined, options: Options) {
  const labels = Object.fromEntries(Object.values(options.tags).flat().map(o => [o.value, o.label]));
  return Array.isArray(value) ? value.map(v => labels[v] || v).join("、") || "暂未填写" : value || "暂未填写";
}

export function ProfileEditor({ userId, profile, candidate, name, run, options, close, saved, compact = false, focusFields }: { userId?: number; profile?: Profile; candidate?: Candidate; name: string; run?: Run; options: Options; close: () => void; saved: (profile: Profile) => void; compact?: boolean; focusFields?: string[] }) {
  const api = accountApi(userId);
  const [form] = Form.useForm();
  const { message } = App.useApp();
  const initial = profile?.data || candidate?.data || {};
  const method = run ? String(run.inputs.source_mode || "search") : profile?.research_method || "materials";
  const canRefresh = method === "website" || method === "search";
  const save = useMutation({
    mutationFn: (values: { name: string; data: Values }) => api<Profile>(profile ? `enterprises/${profile.id}` : "enterprises", { method: profile ? "PATCH" : "POST", body: JSON.stringify({ ...values, data: { ...initial, ...values.data }, revision: profile?.revision, ...(run && candidate ? { run_id: run.id, candidate_index: run.result.candidates?.indexOf(candidate) } : {}) }) }),
    onSuccess: (value) => { message.success("画像已确认，匹配将使用这些信息。"); saved(value); }, onError: (e: Error) => message.error(e.message),
  });
  const fields = (keys: string[]) => <div className="enterprise-field-grid">{Object.entries(options.fields).filter(([key]) => keys.includes(key) && !["project_stage", "investment_wan"].includes(key)).map(([key, label]) => <div key={key} className={["business_summary", "history_projects", "capabilities"].includes(key) ? "enterprise-field-wide" : ""}>
    <Form.Item name={["data", key]} label={label}>
      {options.tags[key] ? <Select mode="multiple" allowClear options={options.tags[key]} placeholder="选择已知方向" /> : ["capabilities", "history_projects", "interest_regions"].includes(key) ? <Select mode="tags" tokenSeparators={["；", "、"]} placeholder="可留空，输入后按回车添加" /> : key === "business_summary" ? <Input.TextArea rows={3} maxLength={2000} /> : <Input maxLength={key === "website" ? 500 : 200} />}
    </Form.Item>
    {profile && candidate?.data[key] !== undefined && JSON.stringify(candidate.data[key]) !== JSON.stringify(profile.data[key]) && <div className="enterprise-suggestion"><span>新资料：{display(candidate.data[key], options)}</span><Button size="small" onClick={() => form.setFieldValue(["data", key], candidate.data[key])}>采用此项</Button></div>}
    <Sources sources={candidate?.evidence[key] || profile?.evidence[key]?.sources} />
  </div>)}</div>;
  const essential = ["business_summary", "business_domains", "direction_tags"];
  return <Modal title={profile ? "核对与修改企业画像" : "确认企业画像"} open width={800} onCancel={save.isPending ? undefined : close} closable={!save.isPending} footer={null}>
    <p className="muted">只需确认你了解的信息，其他内容可以留空。能力与资质线索不代表认证结果。</p>
    {profile && candidate && <Alert type="info" showIcon title="已保留当前画像。请逐项选择需要采用的新资料，再保存。" />}
    <Form form={form} layout="vertical" initialValues={{ name: profile?.name || candidate?.name || name, data: initial, refresh_days: profile?.refresh_days || 0, auto_subscribe: false, start_matching: !userId && options.matching_ai_ready && !focusFields, follow_subscriptions: profile?.follow_subscriptions || false }} onFinish={values => save.mutate(values)}>
      <Form.Item name="name" label="企业名称" rules={[{ required: true, whitespace: true, max: 200 }]}><Input disabled={!!candidate} maxLength={200} /></Form.Item>
      {candidate && <Sources sources={[candidate.identity_evidence]} />}
      {fields(focusFields || (compact ? essential : Object.keys(options.fields)))}
      {compact && !focusFields && <Collapse ghost items={[{ key: "more", label: "更多企业信息与依据（可选修改）", forceRender: true, children: fields(Object.keys(options.fields).filter(key => !essential.includes(key))) }]} />}
      {canRefresh ? <Form.Item name="refresh_days" label={method === "website" ? "自动检查官网" : "自动搜索公开资料"} extra="沿用本次资料获取方式，仅生成待确认草稿，不会覆盖现有画像。"><Select options={[{ value: 0, label: "仅手动检查" }, { value: 7, label: "每周检查" }, { value: 30, label: "每月检查" }, { value: 90, label: "每季度检查" }]} /></Form.Item> : <p className="small muted">上传或粘贴的资料由你按需补充，不会自动改用联网搜索。</p>}
      <Form.Item name="start_matching" valuePropName="checked" extra={`保存后自动查找相关政策，最多解读${options.workflow_max_policies}份。会使用平台配置的模型分析已确认资料，结果可在首页查看；不会自动订阅。`}><Checkbox disabled={!options.matching_ai_ready}>确认后自动查找并解读相关政策</Checkbox></Form.Item>
      <Form.Item name="auto_subscribe" valuePropName="checked" extra="按已确认的业务领域订阅政策与机会，不限定发布地区，避免漏掉国家和省级政策；后续通过站内通知提醒，可在“订阅与消息”修改或关闭。已有订阅不会被覆盖。"><Checkbox>帮我订阅与企业业务相关的政策</Checkbox></Form.Item>
      <Form.Item name="follow_subscriptions" valuePropName="checked" extra="开启后生成企业专属规则，并随确认后的画像更新；你修改或关闭的规则会保留。取消跟随不会关闭已有订阅。"><Checkbox>以后自动跟随画像调整订阅</Checkbox></Form.Item>
      <Space><Button type="primary" htmlType="submit" loading={save.isPending}>{compact ? "确认并开始使用" : "确认并保存"}</Button><Button disabled={save.isPending} onClick={close}>取消</Button></Space>
    </Form>
  </Modal>;
}

export function ProjectEditor({ userId, project, draft, profile, options, close, saved, focusFields }: { userId?: number; project?: Project; draft?: Run["result"]; profile: Profile; options: Options; close: () => void; saved: () => void; focusFields?: string[] }) {
  const api = accountApi(userId);
  const { message } = App.useApp();
  const save = useMutation({ mutationFn: (values: Record<string, unknown>) => api(project ? `enterprise-projects/${project.id}` : "enterprise-projects", { method: project ? "PATCH" : "POST", body: JSON.stringify({ name: project?.name, description: project?.description, ...values, data: { ...project?.data, ...(values.data as Values) }, profile: profile.id, revision: project?.revision }) }), onSuccess: () => { message.success("项目已保存。"); saved(); }, onError: (e: Error) => message.error(e.message) });
  return <Modal title={project ? "修改项目" : "确认拟申报项目"} open onCancel={close} footer={null}>
    <Form layout="vertical" initialValues={project || draft || { data: {} }} onFinish={values => save.mutate(values)}>
      {!focusFields && <><Form.Item name="name" label="项目名称" rules={[{ required: true, max: 200 }]}><Input maxLength={200} /></Form.Item><Form.Item name="description" label="项目计划"><Input.TextArea maxLength={4000} rows={3} /></Form.Item></>}
      {["province", "city", "interest_regions", "business_domains", "direction_tags", "project_stage", "investment_wan"].filter(key => !focusFields || focusFields.includes(key)).map(key => <Form.Item key={key} name={["data", key]} label={options.fields[key]}>{options.tags[key] ? <Select mode="multiple" options={options.tags[key]} /> : key === "interest_regions" ? <Select mode="tags" /> : <Input maxLength={100} placeholder={key === "investment_wan" ? "填写万元数，不确定可留空" : "不确定可留空"} />}</Form.Item>)}
      <Button htmlType="submit" type="primary" loading={save.isPending}>确认并保存项目</Button>
    </Form>
  </Modal>;
}

export default function EnterprisePanel({ onboarding = false, matchesOnly = false, initialProfile, initialProject, initialResult }: { onboarding?: boolean; matchesOnly?: boolean; initialProfile?: string; initialProject?: string; initialResult?: string }) {
  const router = useRouter();
  const client = useQueryClient();
  const { message } = App.useApp();
  const { openPolicy } = usePolicyWorkspace();
  const [selected, setSelected] = useState<string | undefined>(initialProfile);
  const [runId, setRunId] = useState<string | null | undefined>(initialResult);
  const [editor, setEditor] = useState<{ profile?: Profile; candidate?: Candidate; name: string; run?: Run; focusFields?: string[] }>();
  const [projectEditor, setProjectEditor] = useState<{ profile: Profile; project?: Project; draft?: Run["result"]; focusFields?: string[] }>();
  const [projectId, setProjectId] = useState(initialProject || "");
  const [view, setView] = useState("opportunities");
  const [level, setLevel] = useState("");
  const [page, setPage] = useState(1);
  const [matchFilters, setMatchFilters] = useState({ q: "", category: "", city: "", sort: "comprehensive", include_conflicts: "false" });
  const [activeTab, setActiveTab] = useState(matchesOnly ? "matches" : "profile");
  const [intakeProfile, setIntakeProfile] = useState<Profile>();
  const [subscriptionResult, setSubscriptionResult] = useState<SubscriptionResult | null>();
  const [dismissedDraft, setDismissedDraft] = useState<string>();
  const [projectForm] = Form.useForm();
  const profiles = useQuery({ queryKey: ["enterprises"], queryFn: () => api<{ items: Profile[] }>("enterprises") });
  const taxonomy = useQuery({ queryKey: ["taxonomy"], queryFn: () => api<Record<string, { value: string; label: string }[]>>("taxonomies") });
  const options = useQuery({ queryKey: ["enterprise-options"], queryFn: () => api<Options>("enterprises/options") });
  const profile = profiles.data?.items.find(p => p.id === selected) || profiles.data?.items[0];
  const projects = useQuery({ queryKey: ["enterprise-projects", profile?.id], queryFn: () => api<Page<Project>>(`enterprise-projects?profile=${profile!.id}&page_size=100`), enabled: !!profile });
  const recent = useQuery({ queryKey: ["enterprise-recent"], queryFn: () => api<{ items: Run[] }>("enterprise-research"), refetchInterval: 15000 });
  const activeRunId = runId === undefined && onboarding && !profile ? recent.data?.items.find(item => item.kind === "company" && !item.profile)?.id : runId;
  const run = useQuery({ queryKey: ["enterprise-run", activeRunId], queryFn: () => api<Run>(`enterprise-research/${activeRunId}`), enabled: !!activeRunId, refetchInterval: query => ["pending"].includes(query.state.data?.status || "") ? 2000 : false });
  const singleCandidate = onboarding && !profile && run.data?.status === "completed" && run.data.kind === "company" && run.data.result.candidates?.length === 1 && dismissedDraft !== run.data.id ? run.data.result.candidates[0] : undefined;
  const activeEditor = editor || (singleCandidate ? { candidate: singleCandidate, name: singleCandidate.name, run: run.data } : undefined);
  const closeEditor = () => { setEditor(undefined); setDismissedDraft(run.data?.id); };
  const running = ["pending"].includes(run.data?.status || "");
  const research = useMutation({ mutationFn: (input: IntakeInput) => api<Run>("enterprise-research", { method: "POST", body: input instanceof FormData ? input : JSON.stringify(input) }), onSuccess: result => { setIntakeProfile(undefined); setRunId(result.id); client.setQueryData(["enterprise-run", result.id], result); void client.invalidateQueries({ queryKey: ["enterprise-recent"] }); }, onError: (e: Error) => message.error(e.message) });
  const startWorkflow = useMutation({ mutationFn: () => api<{ id: string }>(`enterprises/${profile!.id}/matching-workflow`, { method: "POST", body: JSON.stringify({ ...(projectId ? { project_id: projectId } : {}), view, level, filters: matchFilters }) }), onSuccess: result => { setRunId(result.id); void client.invalidateQueries({ queryKey: ["agent-tasks"] }); message.success("已提交，结果可在首页查看。"); }, onError: (error: Error) => message.error(error.message) });
  const matches = useQuery({ queryKey: ["enterprise-matches", profile?.id, profile?.revision, projectId, view, level, page, matchFilters], queryFn: () => api<Matches>(`enterprises/${profile!.id}/matches?${new URLSearchParams({ ...matchFilters, project_id: projectId, view, level, page: String(page) })}`), enabled: !!profile && activeTab === "matches" });
  const removeProfile = useMutation({ mutationFn: () => api(`enterprises/${profile!.id}`, { method: "DELETE", body: JSON.stringify({ confirm_name: profile!.name }) }), onSuccess: () => { setSelected(undefined); setProjectId(""); setRunId(null); void client.invalidateQueries({ queryKey: ["enterprises"] }); void client.invalidateQueries({ queryKey: ["subscriptions"] }); message.success("企业及关联项目已删除。"); }, onError: (error: Error) => message.error(error.message) });
  const removeProject = useMutation({ mutationFn: (id: string) => api(`enterprise-projects/${id}`, { method: "DELETE" }), onSuccess: () => { setProjectId(""); void client.invalidateQueries({ queryKey: ["enterprise-projects"] }); void client.invalidateQueries({ queryKey: ["enterprise-matches"] }); }, onError: (e: Error) => message.error(e.message) });
  const refresh = () => { void client.invalidateQueries({ queryKey: ["enterprises"] }); void client.invalidateQueries({ queryKey: ["enterprise-matches"] }); };
  const saveProfile = (value: Profile) => { setSelected(value.id); setSubscriptionResult(value.subscription_result); closeEditor(); setRunId(value.matching_task?.id || null); if (value.matching_task?.message) message.info(value.matching_task.message); setActiveTab(matchesOnly ? "matches" : "profile"); setView("opportunities"); setPage(1); client.setQueryData<{ items: Profile[] }>(["enterprises"], previous => ({ items: [...(previous?.items.filter(item => item.id !== value.id) || []), value] })); void client.invalidateQueries({ queryKey: ["subscriptions"] }); refresh(); };
  const supplement = (fields: string[]) => {
    if (!profile?.can_edit) return;
    const projectFields = fields.filter(field => field.startsWith("project.")).map(field => field.slice(8)).slice(0, 2);
    if (projectFields.length) {
      const project = projects.data?.items.find(item => item.id === projectId);
      setProjectEditor({ profile, project, ...(project ? { focusFields: projectFields } : { draft: { data: {} } }) });
    } else setEditor({ profile, name: profile.name, focusFields: fields.slice(0, 2) });
  };
  if (profiles.isLoading || options.isLoading) return <Spin />;
  if (profiles.error || options.error || !options.data) return <Alert type="error" title={profiles.error?.message || options.error?.message || "暂时无法读取企业设置"} />;
  const config = options.data;
  if (onboarding && profile && run.data?.kind !== "company") return <><Result status={subscriptionResult?.status === "unavailable" ? "warning" : "success"} title="企业画像已保存" subTitle={subscriptionResult?.message || "先看相关机会，需要时再补充资料。"} extra={<Space wrap><Link href="/enterprise"><Button>企业与项目</Button></Link><Link href="/subscriptions"><Button>我的订阅</Button></Link><Link href="/search"><Button>搜索全部政策</Button></Link></Space>} />{runId && <Card title="政策适用分析" className="space-bottom"><MatchingResults id={runId} onOpenDraft={setRunId} /></Card>}<OpportunityPreview profileId={profile.id} revision={profile.revision} openPolicy={openPolicy} supplement={fields => setEditor({ profile, name: profile.name, focusFields: fields })} />{editor && <ProfileEditor {...editor} options={config} close={closeEditor} saved={saveProfile} />}</>;
  const newCompany = <Card title="添加企业" size="small">
    <EnterpriseIntake onboarding={onboarding} searchReady={config.search_ready} aiReady={config.ai_ready} busy={research.isPending || running} onSubmit={input => research.mutate(input)} onManual={name => setEditor({ name })} />
  </Card>;
  return <div className="enterprise-panel">
    {onboarding && <div className="space-bottom"><div className="spread"><div><h2>让政策更贴近你的企业</h2><p className="muted">提供企业名称或介绍，AI 自动整理；确认后可一并订阅相关政策。</p></div><Link href="/search"><Button>暂时跳过，先看政策</Button></Link></div><p className="small muted">填写企业信息 → 自动整理 → 确认画像与可选订阅。跳过不影响搜索，稍后可在“企业与项目”补充。</p></div>}
    {!onboarding && subscriptionResult && <Alert className="space-bottom" showIcon closable type={subscriptionResult.status === "unavailable" ? "warning" : "success"} title={subscriptionResult.message} action={<Link href="/subscriptions">管理订阅</Link>} onClose={() => setSubscriptionResult(null)} />}
    {!onboarding && <div className="spread space-bottom"><p className="muted">确认企业画像，查找相关政策；有项目计划时再添加项目。</p>{profile && <Select aria-label="当前企业" value={profile.id} style={{ minWidth: 240 }} options={profiles.data?.items.map(p => ({ value: p.id, label: p.name }))} onChange={value => { setSelected(value); setProjectId(""); setPage(1); }} />}</div>}
    {run.error && <Alert type="error" title={run.error.message} closable onClose={() => setRunId(null)} />}
    {run.data && <Card size="small" className="space-bottom" title={run.data.kind === "workflow" ? "政策适用分析" : run.data.kind === "company" ? "企业资料整理" : run.data.kind === "project" ? "项目草稿" : "政策匹配解读"} extra={<Button type="text" onClick={() => setRunId(null)}>收起</Button>}>
      {run.data.kind === "workflow" ? <MatchingResults id={run.data.id} onOpenDraft={setRunId} /> : null}
      {running && run.data.kind !== "workflow" && <p className="muted">资料已收到。你可以继续浏览政策，整理结果可在首页查看。</p>}
      {run.data.status === "needs_information" && <Alert type="warning" showIcon title="暂未获得可用结果" description={run.data.error || "请核对资料是否完整，可重新提供介绍，或直接填写企业与项目信息。"} />}
      {run.data.status === "completed" && run.data.kind === "company" && <>
        <p className="muted">{run.data.result.notice}</p>
        {!run.data.result.candidates?.length && <Empty description="当前资料不足以生成完整草稿，请换用官网、介绍文件或粘贴简介，也可以直接填写画像。" />}
        <div className="enterprise-card-grid">{run.data.result.candidates?.map((candidate, i) => <Card key={i} size="small" title={candidate.name}>
          <p>{[candidate.data.province, candidate.data.city, candidate.data.credit_code].filter(Boolean).join(" · ") || "请核对企业身份"}</p><p>{candidate.data.business_summary}</p><Sources sources={[candidate.identity_evidence]} />
          <Button type="primary" onClick={() => { const current = profiles.data?.items.find(p => p.id === run.data!.profile); setEditor({ profile: current, candidate, name: candidate.name, run: run.data }); }}>这是我的企业，核对画像</Button>
        </Card>)}</div>
        {!!run.data.result.warnings?.length && <p className="muted small">{run.data.result.warnings.join(" ")}</p>}
      </>}
      {run.data.status === "completed" && run.data.kind === "project" && <><p>{run.data.result.name}</p><p>{run.data.result.description}</p><Button type="primary" onClick={() => { const target = profiles.data?.items.find(p => p.id === run.data!.profile); if (target) setProjectEditor({ profile: target, draft: run.data!.result }); }}>核对并保存项目</Button></>}
      {run.data.status === "completed" && run.data.kind === "explanation" && <>
        <p className="small muted">{run.data.result.notice || "依据已确认画像与政策原文生成，相关性不代表申报资格。"}</p>
        {run.data.result.coverage?.partial && <Alert type="info" title="本次读取了与条件相关的正文和已解析附件片段，并非全文逐项核验。" />}
        {run.data.result.points?.map((point, i) => <div className="reading-evidence" key={i}><ReadableText text={point.text} /><blockquote className="reading-quote"><span className="reading-caption">原文依据</span><ReadableText text={point.quote} previewChars={320} expandLabel="展开原文依据" /></blockquote>{point.source && <p className="small muted">{point.source.source_name} · 字符位置 {point.source.start_offset + 1}</p>}<p className="small muted">使用的画像信息：{Object.values(point.profile_facts).map(v => display(v, config)).join("；")}</p></div>)}
        {run.data.result.conditions && <MatchConditions conditions={run.data.result.conditions} openPolicy={() => openPolicy(run.data!.result.policy_id!)} />}
        {!!run.data.result.additional_conditions?.length && <Collapse ghost items={[{ key: "additional", label: "待核对的补充条款", children: <CheckList checks={run.data.result.additional_conditions} /> }]} />}
        <Button onClick={() => openPolicy(run.data!.result.policy_id!)}>查看政策全文与附件</Button>
      </>}

    </Card>}
    <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
      { key: "profile", label: "企业画像", children: <>
        {profile && <Card title={profile.name} extra={profile.can_edit && <Space wrap><Button onClick={() => setEditor({ profile, name: profile.name })}>修改画像</Button><Popconfirm title="删除这家企业？" description="将删除企业画像、项目、专属订阅和关联分析结果，无法恢复。" onConfirm={() => removeProfile.mutateAsync()}><Button danger loading={removeProfile.isPending}>删除企业</Button></Popconfirm><Button loading={research.isPending || running} onClick={() => setIntakeProfile(profile)}>补充或更新资料</Button></Space>}>
          <div className="enterprise-field-grid">{Object.entries(config.fields).filter(([key]) => !["project_stage", "investment_wan"].includes(key)).map(([key, label]) => <div key={key}><span className="small muted">{label}</span><p>{display(profile.data[key], config)}</p><Sources sources={profile.evidence[key]?.sources} /></div>)}</div>
          <p className="small muted">最近确认：{new Date(profile.confirmed_at).toLocaleString("zh-CN")} · {profile.refresh_days ? `每${profile.refresh_days}天检查公开资料` : "仅手动检查公开资料"} · 新资料不会自动覆盖已确认内容</p>
          <Button type="primary" onClick={() => router.push(`/home/matches?profile=${profile!.id}`)}>查看匹配政策</Button>
        </Card>}
        {(!onboarding || !run.data || run.data.status === "needs_information" || (run.data.status === "completed" && !run.data.result.candidates?.length)) && <div style={{ marginTop: 16 }}>{newCompany}</div>}
      </> },
      { key: "projects", label: "拟申报项目", disabled: !profile, children: profile && <>
        <p className="muted">只记录企业准备实施或申报的项目，公开历史业绩保留在企业画像中。</p>
        {profile.can_edit && <Form form={projectForm} onFinish={values => research.mutate({ kind: "project", profile_id: profile.id, ...values })} layout="vertical"><Form.Item name="description" label="用一句话描述项目计划" rules={[{ required: true, max: 4000 }]}><Input.TextArea maxLength={4000} placeholder="例如：计划在南宁对污水处理厂进行智能运维改造" rows={3} /></Form.Item><p className="small muted">AI 整理会将这段描述发送至平台配置的模型服务；可直接手动添加。</p><Space><Button type="primary" htmlType="submit" disabled={!config.ai_ready} loading={research.isPending || running}>整理项目资料</Button><Button onClick={() => setProjectEditor({ profile, draft: { description: projectForm.getFieldValue("description") || "", data: {} } })}>手动添加</Button></Space></Form>}
        {projects.error && <Alert type="error" title={projects.error.message} />}
        <div className="enterprise-card-grid" style={{ marginTop: 20 }}>{projects.data?.items.map(project => <Card key={project.id} title={project.name} size="small"><p>{project.description}</p><p>{display(project.data.business_domains, config)}</p><p className="small muted">项目订阅只使用此项目的业务领域和关注地区；更改项目后自动更新，手工修改的订阅保留。停止跟随会保留现有订阅，可到“订阅与消息”关闭。</p>{project.follow_status && <Alert type="warning" title={project.follow_status} />}<Space wrap><Button onClick={() => router.push(`/subscriptions?profile=${profile.id}&project=${project.id}`)}>订阅设置</Button><Button onClick={() => router.push(`/home/matches?profile=${profile!.id}&project=${project.id}`)}>匹配政策</Button>{profile.can_edit && <><Button onClick={() => setProjectEditor({ profile, project })}>修改</Button><Popconfirm title="删除项目及其专属订阅和关注记录？" onConfirm={() => removeProject.mutateAsync(project.id)}><Button danger>删除</Button></Popconfirm></>}</Space></Card>)}</div>
      </> },
      { key: "matches", label: "政策匹配", disabled: !profile, children: profile && <>
        <Space wrap className="space-bottom"><Select aria-label="匹配对象" value={projectId} style={{ minWidth: 200 }} options={[{ value: "", label: "按企业画像匹配" }, ...(projects.data?.items.map(p => ({ value: p.id, label: p.name })) || [])]} onChange={value => { setProjectId(value); setPage(1); }} /><Select aria-label="匹配视角" value={view} options={[{ value: "policies", label: "政策文件" }, { value: "opportunities", label: "政策机会" }]} onChange={value => { setView(value); setMatchFilters(previous => ({ ...previous, category: "", sort: "comprehensive" })); setPage(1); }} /><Select aria-label="相关度" value={level} style={{ minWidth: 140 }} options={[{ value: "", label: "全部相关度" }, ...Object.entries(config.levels).map(([value, label]) => ({ value, label: `${label} ${matches.data?.counts[value] ?? ""}` }))]} onChange={value => { setLevel(value); setPage(1); }} /></Space>
        <details className="space-bottom"><summary>筛选相关政策</summary><Space wrap className="space-bottom">
          <Input.Search aria-label="匹配结果关键词" placeholder="在候选政策中搜索" allowClear style={{ width: 240 }} onSearch={q => { setMatchFilters(previous => ({ ...previous, q })); setPage(1); }} />
          <Input.Search aria-label="发布城市筛选" placeholder="发布城市（精确名称）" allowClear style={{ width: 200 }} onSearch={city => { setMatchFilters(previous => ({ ...previous, city })); setPage(1); }} />
          {view === "opportunities" && <Select aria-label="机会类别" value={matchFilters.category} style={{ minWidth: 180 }} options={[{ value: "", label: "全部机会类别" }, ...(taxonomy.data?.opportunity_categories || [])]} onChange={category => { setMatchFilters(previous => ({ ...previous, category })); setPage(1); }} />}
          <Select aria-label="匹配排序" value={matchFilters.sort} options={[{ value: "comprehensive", label: "综合排序" }, { value: "latest", label: "最新发布" }, { value: "relevance", label: "相关度" }, ...(view === "opportunities" ? [{ value: "deadline", label: "截止时间" }] : [])]} onChange={sort => { setMatchFilters(previous => ({ ...previous, sort })); setPage(1); }} />
          <Checkbox checked={matchFilters.include_conflicts === "true"} onChange={event => { setMatchFilters(previous => ({ ...previous, include_conflicts: String(event.target.checked) })); setPage(1); }}>显示明确条件冲突（{matches.data?.conflicting_policies ?? 0}）</Checkbox>
        </Space></details>
        <Space wrap className="space-bottom"><Button type="primary" disabled={!config.matching_ai_ready} loading={startWorkflow.isPending} onClick={() => startWorkflow.mutate()}>获取相关政策分析</Button><span className="small muted">结合当前企业或项目资料，说明相关政策的适用条件与待补充信息。</span></Space>
        <p className="small muted">{matches.data?.notice}</p>
        {matches.isLoading && <Spin />}{matches.error && <Alert type="error" title={matches.error.message} />}
        {matches.data && !matches.data.items.length && <Empty description="暂无符合当前条件的政策，可切换视角或完善画像。" />}
        {matches.data?.items.map(item => <Card size="small" key={item.policy_id} className="space-bottom" title={<span style={{ whiteSpace: "normal" }}>{item.title}</span>} extra={<Tag color={colors[item.level]}>{item.level_label}</Tag>}>
          <Space wrap><Tag color={item.recommendation_group === "priority" ? "blue" : "default"}>{item.recommendation_label}</Tag><Tag color={item.conditions.status === "conflict" ? "red" : item.conditions.status === "consistent" ? "green" : "orange"}>{item.conditions.label}</Tag></Space>
          {item.evidence_readiness.status === "incomplete" && <Alert className="space-bottom" type="warning" showIcon title={item.evidence_readiness.label} description={item.evidence_readiness.reason} />}
          <ReadableText text={item.summary} previewChars={220} expandLabel="展开摘要" collapseLabel="收起摘要" /><p className="small muted">{item.region_preference.label}</p>{item.reasons.map(reason => <p key={reason}>{reason}</p>)}
          {!!item.gaps.length && <p className="small muted">待补充核对：{item.gaps.join(" ")}</p>}
          {item.deadline && <p>最近批次截止：{new Date(item.deadline).toLocaleDateString("zh-CN")}</p>}
          {!!item.requirements.length && <Collapse ghost items={[{ key: "requirements", label: "查看申报条件", children: item.requirements.map((requirement, i) => <p key={i}>{requirement}</p>) }]} />}
          {!!item.conditions.opportunities.length && <MatchConditions conditions={item.conditions} supplement={profile.can_edit ? supplement : undefined} openPolicy={() => openPolicy(item.policy_id)} />}
          <Space><Button onClick={() => openPolicy(item.policy_id)}>查看政策全文</Button><Button disabled={!config.matching_ai_ready} loading={research.isPending || running} onClick={() => research.mutate({ kind: "explanation", matching_view: view, profile_id: profile.id, policy_id: item.policy_id, ...(projectId ? { project_id: projectId } : {}) })}>查看适用分析</Button></Space>
        </Card>)}
        {!!matches.data?.count && <Pagination current={page} total={matches.data.count} pageSize={20} showSizeChanger={false} onChange={setPage} />}
      </> },
    ].filter(item => onboarding ? item.key === "profile" : matchesOnly ? item.key === "matches" : item.key !== "matches")} />
    {!!recent.data?.items.length && <Collapse ghost items={[{ key: "recent", label: "可查看的资料与分析结果", children: <Space wrap>{recent.data.items.map(item => <Button key={item.id} onClick={() => setRunId(item.id)}>{String(item.inputs.name || (item.kind === "company" ? "企业资料" : item.kind === "project" ? "项目资料" : "政策适用分析"))}</Button>)}</Space> }]} />}
    {activeEditor && <ProfileEditor {...activeEditor} compact={onboarding} options={config} close={closeEditor} saved={saveProfile} />}
    {profile?.follow_status && <Alert type="warning" title={profile.follow_status} />}

    {intakeProfile && <Modal title="补充或更新企业资料" open footer={null} width={720} onCancel={() => setIntakeProfile(undefined)}><EnterpriseIntake profile={intakeProfile} searchReady={config.search_ready} aiReady={config.ai_ready} busy={research.isPending || running} onSubmit={input => research.mutate(input)} onManual={name => { setEditor({ profile: intakeProfile, name }); setIntakeProfile(undefined); }} /></Modal>}
    {projectEditor && <ProjectEditor {...projectEditor} options={config} close={() => setProjectEditor(undefined)} saved={() => { setProjectEditor(undefined); setRunId(undefined); void client.invalidateQueries({ queryKey: ["enterprise-projects"] }); void client.invalidateQueries({ queryKey: ["enterprise-matches"] }); }} />}
  </div>;
}
