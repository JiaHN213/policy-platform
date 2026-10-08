"use client";
import { RecoverySettings } from "@/components/ReviewRecovery";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  AutoComplete,
  Button,
  Card,
  Collapse,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Tabs,
  Tag,
} from "antd";
import { api, type Page } from "@/lib/api";
import Link from "next/link";
import EnterpriseResearchSettings from "@/components/EnterpriseResearchSettings";
import RecommendationSettings from "@/components/RecommendationSettings";

type Release = {
  id: string;
  version: string;
  status: "draft" | "published" | "archived";
  document_count: number;
  published_at: string | null;
};

type ConfigDocument = {
  id: string;
  release: string;
  key: string;
  content: Record<string, unknown>;
  updated_at: string;
};

type ManagedSource = {
  id: string;
  name: string;
  url: string;
  collection_type: "nanning_v1" | "gov_library_html_v1";
  collection_type_label: string;
  enabled: boolean;
  interval_minutes: number;
  schedule_mode: "interval" | "daily";
  daily_check_time: string | null;
  last_success_at: string | null;
  next_check_at: string | null;
  crawl_state: "paused" | "cooldown" | "running" | "scheduled";
  cooldown_reason: string;
  notes: string;
};

type AIModelProfile = {
  input_price: string | null; output_price: string | null; currency: "CNY" | "USD";
  id: string;
  purpose: "review" | "search" | "search_summary" | "wiki_synthesis" | "wiki_relations" | "enterprise" | "enterprise_match";
  thinking: boolean;
  context_tokens: number | null;
  max_output_tokens: number | null;
  purpose_label: string;
  enabled: boolean;
  base_url: string;
  model: string;
  concurrency: number;
  has_api_key: boolean;
  configured: boolean;
  updated_at: string;
};

type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

const releaseLabels = {
  draft: ["可编辑草稿", "gold"],
  published: ["当前生效", "green"],
  archived: ["历史版本", "default"],
} as const;

const moduleLabels: Record<string, { title: string; description: string }> = {
  business_scope: { title: "行业范围与关键词", description: "决定哪些文件与水务环保相关，以及使用哪些方向标签。" },
  document_classification: { title: "文件类型识别", description: "维护政策、机会、结果、解读和征求意见稿的标题识别词。" },
  opportunity_identification: { title: "政策机会识别", description: "维护资金、申报、资格、材料、时间和结果等识别词。" },
  policy_validity: { title: "政策效力判断", description: "维护规划、计划、方案等时间范围提示词。" },
  wiki_relations: { title: "政策关系识别", description: "维护实施、配套、申报、延期、修订和废止等关系线索。" },
  opportunity_rules: { title: "政策机会判断原则", description: "维护正式机会必须具备的条件和业务判断原则。" },
  system_taxonomies: { title: "分类名称", description: "维护页面中使用的政策类型、机会类型、状态和关系名称。" },
};

const fieldLabels: Record<string, string> = {
  automatic_repair: "新关系校验失败后自动补查证据",
  industry: "核心行业", label: "显示名称", enabled: "是否启用", terms: "识别关键词",
  business_domains: "核心业务领域", direction_tags: "技术与政策方向", primary_collection_terms: "优先采集关键词",
  title_signals: "标题识别词", interpretation: "官方解读", draft: "征求意见稿", result: "政策执行结果",
  opportunity: "政策机会文件", policy: "政策与制度文件", opportunity_type_keywords: "机会类型关键词",
  acquisition_action_keywords: "取得方式关键词", benefit_action_keywords: "支持与利益关键词", weak_signal_keywords: "辅助判断词",
  result_keywords: "执行结果关键词", document_role_keywords: "文件角色关键词", status_keywords: "机会状态关键词",
  batch_keywords: "申报批次关键词", time_keywords: "时间关键词", amount_keywords: "金额关键词",
  eligibility_keywords: "适用对象与资格关键词", exclusion_keywords: "排除关键词", authority_keywords: "主管和受理部门关键词",
  material_keywords: "申报材料关键词", attachment_keywords: "附件关键词", context_keywords: "正向语境关键词",
  negative_context_keywords: "负向语境关键词", application_channel_keywords: "申报渠道关键词",
  time_bound_title_terms: "有明确时间范围的标题词", directions: "关系方向说明", relation_cues: "政策关系提示词",
  kind_cues: "各类关系关键词", targeted_revision_cues: "需明确指向旧文件的调整词", core_rule: "核心判断原则", formal_requirements: "正式认定条件",
  industries: "核心行业", document_types: "政策文件类型", source_grades: "来源等级", geographic_levels: "地域层级",
  validity_statuses: "政策效力状态", opportunity_levels: "政策机会级别", opportunity_categories: "政策机会分类",
  opportunity_statuses: "政策机会状态", document_roles: "文件角色", acquisition_methods: "取得方式",
  verification_statuses: "核验状态", relation_kinds: "政策关系类型", name: "名称", purpose: "用途",
  common: "通用", current: "当前有效", historical: "历史内容", news: "新闻动态", excluded_business: "非目标行业",
  government_duty: "政府职责", policy_goal: "政策目标", statistics_and_history: "统计与历史信息", subjects: "适用对象",
  conditions: "申报条件", amount: "支持金额", percentage: "支持比例", upper_bound: "最高额度", lower_bound: "最低额度",
  calculation_basis: "计算依据", units: "金额单位",
};

const valueLabels: Record<string, string> = {
  fiscal: "财政资金支持", tax: "税费优惠", finance: "融资支持", pilot: "项目与试点", honor: "荣誉与认定",
  qualification: "资质与目录", market: "市场与推广支持", other: "其他政策支持", superior: "上位依据",
  implements: "实施", supports: "配套", application: "申报通知", supplements: "补充", extends: "延期", interprets: "解读",
  finalizes: "征求意见转正式", revises: "修订", replaces: "替代", repeals: "废止", publicizes: "公示",
  lists: "正式名单", allocates: "资金下达", approves: "项目批复", accepts: "验收结果",
};

const hiddenBusinessFields = new Set([
  "version", "code", "sort_order", "pattern", "applicability_patterns", "negative_title_patterns",
  "explicit_status_patterns", "target_year_patterns", "newer_source_kinds", "result_roles", "draft_roles",
  "category_codes", "status_codes",
]);

function friendlyLabel(key: string) {
  if (fieldLabels[key]) return fieldLabels[key];
  if (valueLabels[key]) return valueLabels[key];
  if (/^[\u3400-\u9fff]/.test(key)) return key;
  return "相关识别内容";
}

function FriendlyValue({ fieldKey, value, disabled, onChange }: {
  fieldKey: string; value: JsonValue; disabled: boolean; onChange: (value: JsonValue) => void;
}) {
  if (hiddenBusinessFields.has(fieldKey)) return null;
  const label = friendlyLabel(fieldKey);
  if (typeof value === "boolean") {
    return <div className="spread source-card"><span>{label}</span><Switch checked={value} disabled={disabled} checkedChildren="启用" unCheckedChildren="停用" onChange={onChange} /></div>;
  }
  if (typeof value === "number") {
    return <Form.Item label={label}><InputNumber value={value} disabled={disabled} onChange={(next) => onChange(next || 0)} /></Form.Item>;
  }
  if (typeof value === "string") {
    return <Form.Item label={label}>{value.length > 80
      ? <Input.TextArea value={value} disabled={disabled} autoSize={{ minRows: 3, maxRows: 8 }} onChange={(event) => onChange(event.target.value)} />
      : <Input value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)} />}</Form.Item>;
  }
  if (Array.isArray(value)) {
    if (value.every((item) => typeof item === "string")) {
      const labels = (value as string[]).map((item) => valueLabels[item] || item);
      const reverse = Object.fromEntries(Object.entries(valueLabels).map(([internal, visible]) => [visible, internal]));
      return <Form.Item label={label} extra="输入内容后按回车添加，点击标签上的关闭按钮删除。"><Select mode="tags" open={false} value={labels} disabled={disabled} tokenSeparators={["，", ",", "、"]} onChange={(items) => onChange(items.map((item) => reverse[item] || item))} /></Form.Item>;
    }
    return <div className="space-bottom"><h4>{label}</h4><Space orientation="vertical" style={{ width: "100%" }}>
      {value.map((item, index) => <Card size="small" key={index} title={`${label} ${index + 1}`}><FriendlyObject value={(item || {}) as { [key: string]: JsonValue }} disabled={disabled} onChange={(next) => { const copy = [...value]; copy[index] = next; onChange(copy); }} /></Card>)}
    </Space></div>;
  }
  if (value && typeof value === "object") {
    return <Collapse size="small" className="space-bottom" items={[{ key: fieldKey, label, children: <FriendlyObject value={value as { [key: string]: JsonValue }} disabled={disabled} onChange={onChange} /> }]} />;
  }
  return null;
}

function FriendlyObject({ value, disabled, onChange }: {
  value: { [key: string]: JsonValue }; disabled: boolean; onChange: (value: JsonValue) => void;
}) {
  return <>{Object.entries(value).map(([key, child]) => <FriendlyValue key={key} fieldKey={key} value={child} disabled={disabled} onChange={(next) => onChange({ ...value, [key]: next })} />)}</>;
}

function SourceLibrarySettings() {
  const { message } = App.useApp();
  const client = useQueryClient();
  const [form] = Form.useForm();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ManagedSource>();
  const scheduleMode = Form.useWatch("schedule_mode", form);
  const sources = useQuery({ queryKey: ["managed-policy-sources"], queryFn: () => api<Page<ManagedSource>>("admin/sources") });
  const refresh = () => client.invalidateQueries({ queryKey: ["managed-policy-sources"] });
  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => api<ManagedSource>(editing ? `admin/sources/${editing.id}` : "admin/sources", { method: editing ? "PATCH" : "POST", body: JSON.stringify(values) }),
    onSuccess: async () => { setOpen(false); await refresh(); message.success(editing ? "政策文件库设置已更新。" : "政策文件库已添加。" ); },
    onError: (error) => message.error(error.message),
  });
  const toggle = useMutation({
    mutationFn: (source: ManagedSource) => api(`admin/sources/${source.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !source.enabled }) }),
    onSuccess: async () => { await refresh(); message.success("自动检查状态已更新。" ); }, onError: (error) => message.error(error.message),
  });
  const run = useMutation({
    mutationFn: (source: ManagedSource) => api(`admin/sources/${source.id}/run-now`, { method: "POST" }),
    onSuccess: async () => { await refresh(); message.success("已安排立即检查。" ); }, onError: (error) => message.error(error.message),
  });
  const remove = useMutation({
    mutationFn: (source: ManagedSource) => api(`admin/sources/${source.id}`, { method: "DELETE" }),
    onSuccess: async () => { await refresh(); message.success("政策文件库已删除。" ); }, onError: (error) => message.error(error.message),
  });
  const showForm = (source?: ManagedSource) => {
    setEditing(source);
    form.setFieldsValue(source || { collection_type: "nanning_v1", enabled: true, schedule_mode: "interval", daily_check_time: null, interval_minutes: 1440 });
    setOpen(true);
  };
  const stateLabel = { paused: ["已暂停", "default"], cooldown: ["访问保护中", "orange"], running: ["正在检查", "processing"], scheduled: ["等待定时检查", "green"] } as const;
  return <div>
    <div className="spread space-bottom"><h3>政策文件库</h3><Button type="primary" onClick={() => showForm()}>新增政策文件库</Button></div>
    {!sources.data?.items.length ? <Empty description="尚未添加政策文件库" /> : <Space orientation="vertical" style={{ width: "100%" }}>
      {sources.data.items.map((source) => <Card key={source.id}><div className="spread"><div>
        <Space wrap><h3 style={{ margin: 0 }}>{source.name}</h3><Tag color={stateLabel[source.crawl_state][1]}>{stateLabel[source.crawl_state][0]}</Tag><Tag>{source.collection_type_label}</Tag></Space>
        <p>{source.url}</p><p className="muted">{source.schedule_mode === "daily" ? `每天 ${String(source.daily_check_time || "02:00").slice(0, 5)} 自动检查` : `每 ${Math.round(source.interval_minutes / 60)} 小时自动检查`}{source.next_check_at ? ` · 下次 ${new Date(source.next_check_at).toLocaleString("zh-CN")}` : " · 当前未安排检查"}{source.last_success_at ? ` · 最近成功 ${new Date(source.last_success_at).toLocaleString("zh-CN")}` : ""}</p>
        {source.cooldown_reason && <p className="muted">{source.cooldown_reason}</p>}
      </div><Space wrap><Button onClick={() => showForm(source)}>修改设置</Button><Button loading={run.isPending} onClick={() => run.mutate(source)}>立即检查</Button><Button loading={toggle.isPending} onClick={() => toggle.mutate(source)}>{source.enabled ? "暂停自动检查" : "启用自动检查"}</Button><Popconfirm title="删除这个政策文件库？" description="已有采集记录的政策库不能删除，可以改为暂停自动检查。" onConfirm={() => remove.mutateAsync(source)}><Button danger>删除</Button></Popconfirm></Space></div></Card>)}
    </Space>}
    <Modal title={editing ? "修改政策文件库" : "新增政策文件库"} open={open} okText="保存设置" cancelText="取消" confirmLoading={save.isPending} onCancel={() => setOpen(false)} onOk={() => form.validateFields().then((values) => save.mutate(values))}>
      <p className="muted small">网址需匹配已支持的采集类型；其他网站需先适配采集规则。</p>
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="政策文件库名称" rules={[{ required: true, message: "请输入名称" }]}><Input placeholder="例如：南宁市政策文件库" /></Form.Item>
        <Form.Item name="collection_type" label="采集类型" rules={[{ required: true }]}><Select disabled={!!editing} options={[{ value: "nanning_v1", label: "南宁市政策文件库" }, { value: "gov_library_html_v1", label: "中国政府网政策文件库" }]} /></Form.Item>
        <Form.Item name="url" label="政策文件库网址" rules={[{ required: true, type: "url", message: "请输入完整网址" }]}><Input placeholder="https://..." /></Form.Item>
        <Form.Item name="enabled" label="自动检查" valuePropName="checked"><Switch checkedChildren="启用" unCheckedChildren="暂停" /></Form.Item>
        <Form.Item name="schedule_mode" label="检查方式" rules={[{ required: true }]}><Select options={[{ value: "daily", label: "每天固定时间检查" }, { value: "interval", label: "按固定间隔检查" }]} /></Form.Item>
        {scheduleMode === "daily" ? <Form.Item name="daily_check_time" label="每天检查时间" rules={[{ required: true, message: "请选择检查时间" }]}><Input type="time" /></Form.Item> : <Form.Item name="interval_minutes" label="检查频率" rules={[{ required: true }]}><Select options={[{ value: 60, label: "每1小时" }, { value: 240, label: "每4小时" }, { value: 720, label: "每12小时" }, { value: 1440, label: "每24小时" }]} /></Form.Item>}
        <Form.Item name="notes" label="备注"><Input.TextArea placeholder="记录该来源的用途或注意事项" /></Form.Item>
      </Form>
    </Modal>
  </div>;
}

function BusinessSettings() {
  const { message } = App.useApp();
  const client = useQueryClient();
  const [chosenRelease, setSelectedRelease] = useState<string>();
  const [chosenDocument, setSelectedDocument] = useState<string>();
  const [drafts, setDrafts] = useState<Record<string, Record<string, JsonValue>>>({});
  const [newVersion, setNewVersion] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const releases = useQuery({ queryKey: ["config-releases"], queryFn: () => api<Page<Release>>("admin/config/releases") });
  const selectedRelease = chosenRelease || releases.data?.items.find((item) => item.status === "draft")?.id || releases.data?.items[0]?.id;
  const documents = useQuery({ queryKey: ["config-documents", selectedRelease], queryFn: () => api<Page<ConfigDocument>>(`admin/config/documents?release=${selectedRelease}`), enabled: !!selectedRelease });
  const release = releases.data?.items.find((item) => item.id === selectedRelease);
  const document = documents.data?.items.find((item) => item.id === chosenDocument) || documents.data?.items[0];
  const selectedDocument = document?.id;
  const savedContent = (selectedDocument && drafts[selectedDocument]) || document?.content as Record<string, JsonValue> || {};
  const content = document?.key === "wiki_relations" ? { automatic_repair: false, ...savedContent } : savedContent;
  const hasUnsaved = documents.data?.items.some((item) => !!drafts[item.id]) || false;
  const refresh = async () => { await client.invalidateQueries({ queryKey: ["config-releases"] }); await client.invalidateQueries({ queryKey: ["config-documents"] }); };
  const createDraft = useMutation({
    mutationFn: () => selectedRelease ? api<Release>(`admin/config/releases/${selectedRelease}/clone`, { method: "POST", body: JSON.stringify({ version: newVersion.trim() }) }) : api<Release>("admin/config/releases", { method: "POST", body: JSON.stringify({ version: newVersion.trim(), schema_version: "1.0" }) }),
    onSuccess: async (created) => { setCreateOpen(false); setNewVersion(""); setSelectedRelease(created.id); setSelectedDocument(undefined); await refresh(); message.success("已创建可编辑的配置草稿。" ); }, onError: (error) => message.error(error.message),
  });
  const beginEditing = useMutation({
    mutationFn: () => api<Release>(`admin/config/releases/${selectedRelease}/clone`, {
      method: "POST",
      body: JSON.stringify({ version: `业务规则草稿-${new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14)}` }),
    }),
    onSuccess: async (created) => {
      setSelectedRelease(created.id);
      setSelectedDocument(undefined);
      await refresh();
      message.success("已进入编辑状态，可以直接修改分类和关键词。");
    },
    onError: (error) => message.error(error.message),
  });
  const save = useMutation({
    mutationFn: (change: { id: string; content: Record<string, JsonValue> }) => api(`admin/config/documents/${change.id}`, { method: "PATCH", body: JSON.stringify({ content: change.content }) }),
    onSuccess: async (_, change) => {
      await refresh();
      setDrafts((previous) => {
        if (previous[change.id] !== change.content) return previous;
        const next = { ...previous }; delete next[change.id]; return next;
      });
      message.success("当前设置已保存到草稿。");
    }, onError: (error) => message.error(error.message),
  });
  const validate = useMutation({ mutationFn: () => api<{ valid: boolean; errors: string[] }>(`admin/config/releases/${selectedRelease}/validate`, { method: "POST" }), onSuccess: (result) => result.valid ? message.success("全部设置检查通过，可以发布。") : Modal.error({ title: "部分设置需要调整", content: result.errors.join("；") }), onError: (error) => message.error(error.message) });
  const publish = useMutation({ mutationFn: () => api(`admin/config/releases/${selectedRelease}/publish`, { method: "POST" }), onSuccess: async () => { await refresh(); message.success("新设置已经生效。" ); }, onError: (error) => message.error(error.message) });
  const rollback = useMutation({ mutationFn: () => api(`admin/config/releases/${selectedRelease}/rollback`, { method: "POST" }), onSuccess: async () => { await refresh(); message.success("已经恢复到所选历史设置。" ); }, onError: (error) => message.error(error.message) });
  return <div>
    {(releases.error || documents.error) && <Alert type="error" showIcon title={(releases.error || documents.error)?.message} />}
    {hasUnsaved && <Alert type="warning" showIcon title="有尚未保存的修改" description="切换分类或后台刷新会保留本页修改；请先分别保存修改过的模块，再发布。离开此页面前请保存。" />}
    <Alert showIcon type="info" title={release?.status === "draft" ? "当前是可编辑草稿" : "当前显示的是已生效设置"} description={release?.status === "draft" ? "可以直接修改下方分类、关键词和判断规则；保存后点击“发布并生效”。" : "点击“开始修改”会自动复制当前设置并进入编辑状态，不会影响正在运行的任务。"} />
    <div className="config-toolbar"><Select aria-label="设置版本" value={selectedRelease} style={{ minWidth: 260 }} options={releases.data?.items.map((item, index, items) => ({ value: item.id, label: `设置版本 ${items.length - index} · ${releaseLabels[item.status][0]}` }))} onChange={(value) => { setSelectedRelease(value); setSelectedDocument(undefined); }} />
      {release && <Tag color={releaseLabels[release.status][1]}>{releaseLabels[release.status][0]}</Tag>}{release?.status === "draft" ? <Button onClick={() => setCreateOpen(true)}>另建草稿</Button> : <Button type="primary" loading={beginEditing.isPending} onClick={() => beginEditing.mutate()}>开始修改</Button>}<Button disabled={!selectedRelease} loading={validate.isPending} onClick={() => validate.mutate()}>检查全部设置</Button>
      <Button type="primary" disabled={release?.status !== "draft" || hasUnsaved} loading={publish.isPending} onClick={() => Modal.confirm({ title: "让这套设置正式生效？", content: "系统会先检查分类和关键词是否完整，检查通过后新任务将使用这套设置。", okText: "确认发布", onOk: () => publish.mutateAsync() })}>发布并生效</Button>
      <Button disabled={release?.status !== "archived"} loading={rollback.isPending} onClick={() => rollback.mutate()}>恢复此历史版本</Button>
    </div>
    {!documents.data?.items.length ? <Empty description="暂无业务设置" /> : <div className="config-editor-grid"><div>{documents.data.items.map((item) => <Button key={item.id} type={item.id === selectedDocument ? "primary" : "text"} block style={{ height: "auto", padding: 12, textAlign: "left", marginBottom: 6 }} onClick={() => setSelectedDocument(item.id)}><div><strong>{moduleLabels[item.key]?.title || "业务设置"}{drafts[item.id] ? " · 未保存" : ""}</strong><div className="small" style={{ whiteSpace: "normal" }}>{moduleLabels[item.key]?.description || "维护业务识别内容"}</div></div></Button>)}</div>
      <Card title={moduleLabels[document?.key || ""]?.title || "业务设置"} extra={<Button type="primary" disabled={!document || release?.status !== "draft"} loading={save.isPending} onClick={() => selectedDocument && save.mutate({ id: selectedDocument, content })}>保存当前设置</Button>}><p className="muted">{moduleLabels[document?.key || ""]?.description}</p><FriendlyObject value={content} disabled={release?.status !== "draft"} onChange={(next) => { if (selectedDocument) setDrafts((previous) => ({ ...previous, [selectedDocument]: next as Record<string, JsonValue> })); }} /></Card>
    </div>}
    <Modal title="创建可编辑副本" open={createOpen} okText="创建草稿" cancelText="取消" confirmLoading={createDraft.isPending} okButtonProps={{ disabled: !newVersion.trim() }} onOk={() => createDraft.mutate()} onCancel={() => setCreateOpen(false)}><p className="muted">系统会复制当前所选设置，修改草稿不会立即影响正在运行的业务。</p><Input value={newVersion} placeholder="例如：2026年9月业务规则调整" onChange={(event) => setNewVersion(event.target.value)} /></Modal>
  </div>;
}

const aiPurposeDescriptions: Record<AIModelProfile["purpose"], string> = {
  review: "提取摘要、关键词、分类、效力、业务标签和政策机会，并决定是否自动发布。",
  search: "将问题转换为地区、关键词和筛选条件。优先选择响应快、结构化输出稳定的模型。",
  search_summary: "政策列表先显示，再根据相关原文片段整理要点。优先考虑引用准确和响应速度。",
  wiki_synthesis: "把已经发布的政策整理为内部 Wiki 知识页。",
  wiki_relations: "跨政策查找实施、配套、修订、替代和废止等关系。",
  enterprise: "从官网、介绍材料等提取企业资料与项目标签，轻量模型可先承担此任务。",
  enterprise_match: "对照企业、项目和政策原文解释适用条件。优先考虑条件理解和证据准确性。",
};

function AIModelCard({ profile, onSaved }: { profile: AIModelProfile; onSaved: () => void }) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => api<AIModelProfile>(`admin/ai-models/${profile.id}`, { method: "PATCH", body: JSON.stringify(values) }),
    onSuccess: () => { form.setFieldValue("api_key", ""); onSaved(); message.success(`${profile.purpose_label}设置已保存。`); },
    onError: (error) => message.error(error.message),
  });
  useEffect(() => { form.setFieldsValue({ enabled: profile.enabled, base_url: profile.base_url, model: profile.model, concurrency: profile.concurrency, thinking: profile.thinking, context_tokens: profile.context_tokens, max_output_tokens: profile.max_output_tokens, input_price: profile.input_price, output_price: profile.output_price, currency: profile.currency, api_key: "" }); }, [form, profile]);
  const applyPreset = (preset: string) => {
    if (preset === "ollama") form.setFieldValue("base_url", "http://host.docker.internal:11434/v1");
    if (preset === "deepseek") form.setFieldValue("base_url", "https://api.deepseek.com");
    if (preset === "openai") form.setFieldValue("base_url", "https://api.openai.com/v1");
  };
  return <Card title={<Space wrap><span>{profile.purpose_label}</span><Tag color={profile.configured ? "blue" : "orange"}>{!profile.enabled ? "已停用" : profile.configured ? "已配置" : "待完善"}</Tag></Space>}>
    <p className="muted">{aiPurposeDescriptions[profile.purpose]}</p>
    <Form form={form} layout="vertical" onFinish={(values) => save.mutate(values)}>
      <Form.Item name="enabled" label="是否使用此 AI 功能" valuePropName="checked"><Switch checkedChildren="启用" unCheckedChildren="停用" /></Form.Item>
      <Form.Item label="常用服务"><Select placeholder="选择后自动填写服务地址" allowClear onChange={applyPreset} options={[{ value: "ollama", label: "本机 Ollama" }, { value: "deepseek", label: "DeepSeek API" }, { value: "openai", label: "OpenAI API" }, { value: "custom", label: "其他兼容服务" }]} /></Form.Item>
      <Form.Item name="base_url" label="模型服务地址" extra="Docker 访问电脑上的 Ollama 使用 host.docker.internal；已配置表示信息齐全，不代表已验证连接。" rules={[{ required: true, type: "url", message: "请输入完整的模型服务地址" }]}><Input placeholder="例如：http://host.docker.internal:11434/v1" /></Form.Item>
      <Form.Item name="model" label="使用的模型" rules={[{ required: true, message: "请选择或输入模型名称" }]}><AutoComplete placeholder="选择或输入模型名称" options={["qwen3.5:4b", "qwen3.5:9b", "deepseek-chat", "deepseek-reasoner", "gpt-5.1"].map((value) => ({ value }))} /></Form.Item>
      <Form.Item name="concurrency" label="此任务同时调用数" extra="通常从 1 开始。同一服务还受平台共享并发上限约束，多模型共用显卡时不宜盲目增加。" rules={[{ required: true }]}><InputNumber min={1} max={4} precision={0} /></Form.Item>
      <Collapse className="space-bottom" items={[{ key: "generation", label: "处理参数（可选）", children: <>
        <Form.Item name="thinking" label="深度思考（本地 Ollama）" valuePropName="checked" extra="简单提取和搜索建议关闭；开启后通常需要更长时间，且模型本身须支持。远程 API 使用供应商自身设置。"><Switch /></Form.Item>
        <Form.Item name="context_tokens" label="上下文容量（本地 Ollama）" extra="留空沿用模型服务设置。包含原文、指令与输出，容量过小可能无法容纳资料；不是越大越快。"><InputNumber min={2048} max={131072} step={1024} precision={0} placeholder="沿用服务设置" style={{ width: 240 }} /></Form.Item>
        <Form.Item name="max_output_tokens" label="输出长度上限" extra="留空沿用任务默认值；填写后作为额外上限。过低可能导致结构化结果不完整。"><InputNumber min={128} max={16384} step={128} precision={0} placeholder="沿用任务默认值" style={{ width: 240 }} /></Form.Item>
      </> }]} />
      <Space wrap><Form.Item name="input_price" label="输入单价／百万 Token"><InputNumber min={0} precision={6} placeholder="可留空" /></Form.Item><Form.Item name="output_price" label="输出单价／百万 Token"><InputNumber min={0} precision={6} placeholder="可留空" /></Form.Item><Form.Item name="currency" label="计价币种"><Select style={{ width: 110 }} options={[{ value: "CNY", label: "人民币" }, { value: "USD", label: "美元" }]} /></Form.Item></Space>
      <p className="small muted">单价仅用于新调用费用估算，不是供应商账单。切换模型时请同步核对价格；留空表示不估算，本地模型可填零。</p>
      <Form.Item name="api_key" label="API 密钥" extra={profile.has_api_key ? "已保存密钥；留空表示继续使用原密钥。" : "本机 Ollama 可以留空；远程模型服务通常需要填写。"}><Input.Password autoComplete="new-password" placeholder={profile.has_api_key ? "已保存，输入新值可替换" : "请输入 API 密钥"} /></Form.Item>
      <Button type="primary" htmlType="submit" loading={save.isPending}>保存此用途设置</Button>
    </Form>
  </Card>;
}

function AISettings() {
  const client = useQueryClient();
  const profiles = useQuery({ queryKey: ["ai-model-profiles"], queryFn: () => api<Page<AIModelProfile>>("admin/ai-models") });
  const refresh = () => { void client.invalidateQueries({ queryKey: ["ai-model-profiles"] }); void client.invalidateQueries({ queryKey: ["ai-configuration"] }); };
  return <div>
    <p className="muted small">各任务可独立选择模型。保存后对新任务生效，密钥不回显。</p>
    {profiles.isLoading ? <p>正在读取 AI 设置…</p> : profiles.error ? <Alert type="error" title={profiles.error.message} /> : <Space orientation="vertical" style={{ width: "100%" }}>{profiles.data?.items.map((profile) => <AIModelCard key={profile.id} profile={profile} onSaved={refresh} />)}</Space>}
    <p><Link href="/admin/review">前往处理工作台管理审核开关与待办</Link></p>
    <Card style={{ marginTop: 16 }}><RecoverySettings /></Card>
  </div>;
}

export default function SystemConfigCenter() {
  return <div className="config-center">
    <Tabs defaultActiveKey="sources" items={[
      { key: "sources", label: "文件库与检查计划", children: <SourceLibrarySettings /> },
      { key: "business", label: "分类与识别规则", children: <BusinessSettings /> },
      { key: "automation", label: "AI 模型与审核", children: <AISettings /> },
      { key: "enterprise", label: "企业资料搜索", children: <EnterpriseResearchSettings /> },
      { key: "recommendations", label: "持续推荐", children: <RecommendationSettings /> },
    ]} />
  </div>;
}
