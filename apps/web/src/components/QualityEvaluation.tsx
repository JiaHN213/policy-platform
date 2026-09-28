"use client";

import { useState } from "react";
import { Alert, App, Button, Card, Drawer, Form, Input, Modal, Popconfirm, Select, Space, Table, Tabs, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, safeExternalUrl, type Page } from "@/lib/api";

type Kind = "opportunity" | "relation" | "knowledge";
type Document = { id: string; version: number; title: string; body: string; source_url: string; attachments: { url: string; parse_status: string }[] };
type Gold = { opportunity_level?: string; verdict?: boolean; notes?: string; evidence_supported?: boolean; evidence_policy_id?: string; evidence_quote?: string };
type Prediction = { value: string | boolean; model?: string; quotes: number; grounded_quotes: number; citations?: { quote: string; policy_id?: string }[] };
type Sample = { id: string; title: string; kind: Kind; kind_label: string; status: string; status_label: string; label_version: number; gold: Gold; policy: string | null; related_policy: string | null; page: string | null; relation_kind: string; labeled_by_name: string; labeled_at: string | null };
type SampleDetail = Sample & { stale_reason: string; prediction: Prediction | null; prediction_hash: string; snapshot: { documents: Document[]; page_body?: string; citations?: { quote: string; title?: string }[] } };
type Metric = { labeled: number; evaluated: number; stale: number; unavailable: number; tp: number; fp: number; fn: number; tn: number; accuracy: number | null; precision: number | null; recall: number | null; miss_rate: number | null; false_positive_rate: number | null; citation_grounding: number | null; human_evidence_support: number | null };
type Run = { id: string; status: string; created_at: string; config_version: string; metrics: Record<Kind, Metric> };
type Result = { id: string; sample: string; sample_title: string; kind: Kind; correct: boolean | null; status: string; reason: string; gold: Gold; prediction: Prediction };
const kinds = [{ value: "opportunity", label: "政策机会识别" }, { value: "relation", label: "Wiki政策关系" }, { value: "knowledge", label: "Wiki知识页证据" }];
const levels = [{ value: "NONE", label: "无政策机会" }, { value: "SUPPORT_SIGNAL", label: "支持线索" }, { value: "FORMAL_OPPORTUNITY", label: "正式政策机会" }];
const verdictLabel = (kind: Kind, value: string | boolean | undefined) => typeof value === "boolean" ? (kind === "relation" ? (value ? "存在该方向及类型的关系" : "不存在该关系") : (value ? "有原文支持" : "原文支持不足")) : levels.find(item => item.value === value)?.label || "暂无可评测结果";
const percentage = (value: number | null | undefined) => value == null ? "暂无有效样本" : `${(value * 100).toFixed(1)}%`;

function LabelEditor({ sample, onSaved }: { sample: SampleDetail; onSaved: () => void }) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const label = useMutation({ mutationFn: (values: Gold) => api(`admin/quality/samples/${sample.id}/label`, { method: "POST", body: JSON.stringify({ ...values, label_version: sample.label_version, prediction_hash: sample.prediction_hash }) }),
    onSuccess: () => { message.success("人工标注已保存，评测时会作为独立标准答案。"); onSaved(); }, onError: (error: Error) => message.error(error.message) });
  return <Form form={form} layout="vertical" initialValues={sample.gold} onFinish={values => label.mutate(values)} disabled={!!sample.stale_reason || sample.status === "retired"}>
    {sample.kind === "opportunity" ? <Form.Item name="opportunity_level" label="根据原文，正确的机会结论是什么？" rules={[{ required: true }]}><Select options={levels} /></Form.Item> :
      <Form.Item name="verdict" label={sample.kind === "relation" ? "两份文件是否存在标题所示方向和类型的关系？" : "知识页的主要结论是否均有引用原文支持？"} rules={[{ required: true }]}><Select options={[{ value: true, label: sample.kind === "relation" ? "存在该关系" : "有原文支持" }, { value: false, label: sample.kind === "relation" ? "不存在该关系" : "存在无依据或错误推断" }]} /></Form.Item>}
    <Form.Item name="notes" label="判断依据与说明" rules={[{ required: true, min: 5, message: "请说明判断理由，至少5个字符。" }]}><Input.TextArea rows={3} placeholder="例如：仅提出支持方向，没有明确申报对象、利益或参与机制。" /></Form.Item>
    <Form.Item name="evidence_policy_id" label="证据来源文件（正向结论必选）"><Select allowClear options={sample.snapshot.documents.map(doc => ({ value: doc.id, label: doc.title }))} /></Form.Item>
    <Form.Item name="evidence_quote" label="逐字引用原文（正向结论至少5个字符）"><Input.TextArea rows={4} placeholder="从下方原文复制连续的证据文字。" /></Form.Item>
    <details className="space-bottom"><summary>查看当前系统结果及引用，再判断其证据质量</summary><p>{verdictLabel(sample.kind, sample.prediction?.value)}{sample.prediction?.model ? ` · 模型：${sample.prediction.model}` : ""}</p>{sample.prediction?.citations?.map((citation, index) => <blockquote key={index}>{citation.quote}</blockquote>)}<p className="muted">引用可在原文中找到，不代表引用足以支持结论，需要结合上下文判断。</p></details>
    <Form.Item name="evidence_supported" label="当前系统引用的证据是否足以支持其结论？" rules={[{ required: true }]}><Select options={[{ value: true, label: "证据与结论相符且充分" }, { value: false, label: "证据不足、无证据或与结论不符" }]} /></Form.Item>
    <Button type="primary" htmlType="submit" loading={label.isPending}>保存人工标注</Button>
  </Form>;
}

export default function QualityEvaluation() {
  const { message } = App.useApp();
  const client = useQueryClient();
  const [page, setPage] = useState(1);
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [errorsOnly, setErrorsOnly] = useState(true);
  const [resultPage, setResultPage] = useState(1);
  const [adding, setAdding] = useState(false);
  const [policyQuery, setPolicyQuery] = useState("");
  const [addForm] = Form.useForm();
  const addKind = Form.useWatch("kind", addForm);
  const params = new URLSearchParams({ page: String(page), kind, status });
  const samples = useQuery({ queryKey: ["quality-samples", params.toString()], queryFn: () => api<Page<Sample>>(`admin/quality/samples?${params}`) });
  const summary = useQuery({ queryKey: ["quality-summary"], queryFn: () => api<{ counts: { status: string; count: number }[] }>("admin/quality/samples/summary") });
  const detail = useQuery({ queryKey: ["quality-sample", selected], queryFn: () => api<SampleDetail>(`admin/quality/samples/${selected}`), enabled: !!selected });
  const runs = useQuery({ queryKey: ["quality-runs"], queryFn: () => api<Page<Run>>("admin/quality/runs") });
  const activeRun = runs.data?.items.find(item => item.id === runId) || runs.data?.items[0];
  const results = useQuery({ queryKey: ["quality-results", activeRun?.id, errorsOnly, resultPage], queryFn: () => api<Page<Result>>(`admin/quality/runs/${activeRun?.id}/results?errors_only=${errorsOnly}&page=${resultPage}`), enabled: !!activeRun });
  const policies = useQuery({ queryKey: ["quality-policy-options", policyQuery], queryFn: () => api<{ items: { id: string; title: string }[] }>(`admin/quality/samples/policies?q=${encodeURIComponent(policyQuery)}`), enabled: adding });
  const taxonomy = useQuery({ queryKey: ["taxonomy"], queryFn: () => api<Record<string, { value: string; label: string }[]>>("taxonomies") });
  const refresh = () => { client.invalidateQueries({ queryKey: ["quality-samples"] }); client.invalidateQueries({ queryKey: ["quality-summary"] }); client.invalidateQueries({ queryKey: ["quality-sample"] }); };
  const seed = useMutation({ mutationFn: () => api<{ created: number }>("admin/quality/samples/seed", { method: "POST", body: JSON.stringify({ limit: 30 }) }), onSuccess: data => { message.success(`新增 ${data.created} 份待人工标注样本。`); refresh(); }, onError: (error: Error) => message.error(error.message) });
  const evaluate = useMutation({ mutationFn: () => api<Run>("admin/quality/runs/evaluate", { method: "POST" }), onSuccess: data => { setRunId(data.id); setResultPage(1); client.invalidateQueries({ queryKey: ["quality-runs"] }); message.info(data.status === "no_labels" ? "尚无可评测的已标注样本，请先完成标注。" : "评测完成，结果已保存。"); }, onError: (error: Error) => message.error(error.message) });
  const add = useMutation({ mutationFn: (values: Record<string, unknown>) => api<{ sample: Sample }>("admin/quality/samples/add", { method: "POST", body: JSON.stringify(values) }), onSuccess: data => { setAdding(false); setSelected(data.sample.id); refresh(); }, onError: (error: Error) => message.error(error.message) });
  const retire = useMutation({ mutationFn: (id: string) => api(`admin/quality/samples/${id}/retire`, { method: "POST" }), onSuccess: () => { refresh(); setSelected(null); }, onError: (error: Error) => message.error(error.message) });
  const labeled = summary.data?.counts.filter(item => item.status === "labeled").reduce((sum, item) => sum + item.count, 0) || 0;
  const pending = summary.data?.counts.filter(item => item.status === "pending").reduce((sum, item) => sum + item.count, 0) || 0;
  return <Space orientation="vertical" size="large" style={{ width: "100%" }}>
    <details className="inline-help"><summary>评测说明</summary><p>先人工标注，再评测，不改写正式政策。AI结果不作为标准答案，版本变化的样本不计分。已标注样本每天自动复评，也可手动评测。抽样指标不能直接代表全库质量。</p></details>
    <Space wrap><Tag>待标注 {pending}</Tag><Tag color="green">已标注 {labeled}</Tag><Button loading={seed.isPending} onClick={() => seed.mutate()}>从真实库补充样本</Button><Button onClick={() => { addForm.resetFields(); setAdding(true); }}>新增指定文件 / 漏判关系样本</Button><Button type="primary" loading={evaluate.isPending} onClick={() => evaluate.mutate()}>开始评测</Button></Space>
    {(samples.error || runs.error || summary.error) && <Alert type="error" title={(samples.error || runs.error || summary.error)?.message} />}
    <Tabs items={[
      { key: "samples", label: "样本与人工标注", children: <>
        <Space wrap className="space-bottom"><Select value={kind} style={{ width: 180 }} options={[{ value: "", label: "全部评测类型" }, ...kinds]} onChange={value => { setKind(value); setPage(1); }} /><Select value={status} style={{ width: 160 }} options={[{ value: "", label: "全部状态" }, { value: "pending", label: "待人工标注" }, { value: "labeled", label: "已人工标注" }, { value: "retired", label: "已停用" }]} onChange={value => { setStatus(value); setPage(1); }} /></Space>
        <Table<Sample> rowKey="id" dataSource={samples.data?.items || []} loading={samples.isLoading} pagination={{ current: page, total: samples.data?.count, pageSize: 20, showSizeChanger: false, onChange: setPage }} columns={[
          { title: "样本", dataIndex: "title", render: (title, row) => <Button type="link" style={{ whiteSpace: "normal", height: "auto", textAlign: "left" }} onClick={() => setSelected(row.id)}>{title}</Button> },
          { title: "类型", dataIndex: "kind_label", width: 150 }, { title: "状态", dataIndex: "status_label", width: 130 },
          { title: "标注人", dataIndex: "labeled_by_name", width: 100, render: value => value || "尚未标注" },
        ]} /></> },
      { key: "reports", label: "评测报告与错误样本", children: <>
        <Select placeholder="选择历史评测报告" value={activeRun?.id} style={{ minWidth: 300 }} options={runs.data?.items.map(item => ({ value: item.id, label: new Date(item.created_at).toLocaleString("zh-CN") }))} onChange={value => { setRunId(value); setResultPage(1); }} />
        {!activeRun || activeRun.status === "no_labels" ? <Alert className="space-bottom" type="warning" title="暂无有效评测结果" description="请完成真实样本的人工标注；没有样本时不会显示虚假的准确率。" /> : null}
        <div className="stats-grid">{kinds.map(item => { const metrics = activeRun?.metrics[item.value as Kind]; return <Card key={item.value} title={item.label} size="small"><p>有效样本 {metrics?.evaluated || 0} · 过期 {metrics?.stale || 0} · 无独立输出 {metrics?.unavailable || 0}</p><p>准确率：{percentage(metrics?.accuracy)}</p><p>精确率：{percentage(metrics?.precision)} · 召回率：{percentage(metrics?.recall)}</p><p>漏判率：{percentage(metrics?.miss_rate)} · 误报率：{percentage(metrics?.false_positive_rate)}</p><p>引用原文匹配：{percentage(metrics?.citation_grounding)}</p><p>人工证据支持度：{percentage(metrics?.human_evidence_support)}</p></Card>; })}</div>
        <p className="muted">机会准确率按三级分类计算，精确率和召回率针对“正式政策机会”。关系按指定的文件对、方向和类型判断。知识页区分原文引用匹配与人工证据判断；原文匹配不代表语义正确。历史报告保留当时样本、标注及模型版本，样本集变化时不宜直接比较百分比。</p>
        <Select value={errorsOnly} options={[{ value: true, label: "仅看错误与不可评测样本" }, { value: false, label: "全部评测样本" }]} onChange={value => { setErrorsOnly(value); setResultPage(1); }} style={{ width: 250 }} />
        {results.error && <Alert type="error" title={results.error.message} />}
        <Table<Result> rowKey="id" dataSource={results.data?.items || []} pagination={{ current: resultPage, pageSize: 20, total: results.data?.count, showSizeChanger: false, onChange: setResultPage }} columns={[
          { title: "样本", dataIndex: "sample_title", render: (value, row) => <Button type="link" style={{ whiteSpace: "normal", height: "auto", textAlign: "left" }} onClick={() => setSelected(row.sample)}>{value}</Button> },
          { title: "人工标准", render: (_, row) => verdictLabel(row.kind, row.kind === "opportunity" ? row.gold.opportunity_level : row.gold.verdict) },
          { title: "系统结果", render: (_, row) => verdictLabel(row.kind, row.prediction.value) },
          { title: "评测结论", render: (_, row) => row.reason || (row.correct ? "判断一致" : "判断不一致，需分析漏判或误判原因") },
        ]} /></> },
    ]} />
    <Drawer open={!!selected} title="阅读原文并人工标注" size="large" onClose={() => setSelected(null)} destroyOnHidden>
      {detail.error && <Alert type="error" title={detail.error.message} />}
      {detail.isLoading && <p>正在读取样本全文…</p>}
      {detail.data && <>
        <h2>{detail.data.title}</h2>
        {detail.data.stale_reason && <Alert type="warning" title={detail.data.stale_reason} action={<Button onClick={() => add.mutate({ kind: detail.data!.kind, ...(detail.data!.policy ? { policy_id: detail.data!.policy } : {}), ...(detail.data!.related_policy ? { related_policy_id: detail.data!.related_policy } : {}), ...(detail.data!.page ? { page_id: detail.data!.page } : {}), ...(detail.data!.relation_kind ? { relation_kind: detail.data!.relation_kind } : {}) })}>创建当前版本样本</Button>} />}
        <LabelEditor key={`${detail.data.id}:${detail.data.label_version}`} sample={detail.data} onSaved={refresh} />
        <Popconfirm title="停用后，该样本不再参与新评测，历史报告仍保留。" onConfirm={() => retire.mutate(detail.data!.id)}><Button className="space-bottom" danger disabled={detail.data.status === "retired"}>停用此样本</Button></Popconfirm>
        {detail.data.snapshot.page_body && <details open><summary>待评测的知识页全文</summary><div className="policy-body">{detail.data.snapshot.page_body}</div>{detail.data.snapshot.citations?.map((citation, index) => <blockquote key={index}>{citation.title}<p>{citation.quote}</p></blockquote>)}</details>}
        {detail.data.snapshot.documents.map(doc => <details key={doc.id} open><summary>{doc.title} · 第 {doc.version} 版原文</summary><a href={safeExternalUrl(doc.source_url)} target="_blank" rel="noopener noreferrer">打开官方来源</a><Space wrap>{doc.attachments.map((attachment, index) => <a key={index} href={safeExternalUrl(attachment.url)} target="_blank" rel="noopener noreferrer">原件 / 附件 {index + 1}</a>)}</Space><div className="policy-body">{doc.body}</div></details>)}
      </>}
    </Drawer>
    <Modal title="新增指定样本" open={adding} onCancel={() => setAdding(false)} onOk={() => addForm.submit()} confirmLoading={add.isPending} destroyOnHidden>
      <p>发现漏判关系时，主动选取两份文件并指定方向、类型，避免只评估系统已经发现的关系。</p>
      <Form form={addForm} layout="vertical" initialValues={{ kind: "opportunity" }} onFinish={values => add.mutate(values)}>
        <Form.Item name="kind" label="样本类型" rules={[{ required: true }]}><Select options={kinds.slice(0, 2)} /></Form.Item>
        <Form.Item name="policy_id" label={addKind === "relation" ? "关系起点文件" : "政策文件"} rules={[{ required: true }]}><Select showSearch filterOption={false} onSearch={setPolicyQuery} options={policies.data?.items.map(item => ({ value: item.id, label: item.title }))} /></Form.Item>
        {addKind === "relation" && <><Form.Item name="related_policy_id" label="关系终点文件" preserve={false} rules={[{ required: true }]}><Select showSearch filterOption={false} onSearch={setPolicyQuery} options={policies.data?.items.map(item => ({ value: item.id, label: item.title }))} /></Form.Item><Form.Item name="relation_kind" label="从起点到终点的关系类型" preserve={false} rules={[{ required: true }]}><Select options={taxonomy.data?.relation_kinds || []} /></Form.Item></>}
      </Form>
    </Modal>
  </Space>;
}
