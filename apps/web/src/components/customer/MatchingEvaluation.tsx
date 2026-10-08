"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Card, Collapse, Descriptions, Empty, Form, Input, InputNumber, Modal, Pagination, Radio, Select, Space, Tag } from "antd";
import { api, type Page } from "@/lib/api";
import SourceReader from "../quality/SourceReader";

type Values = Record<string, string | string[]>;
type Options = { fields: Record<string, string>; tags: Record<string, { value: string; label: string }[]> };
type Case = { id: string; policy: string | null; snapshot: { title: string; body: string; version: number; source_url: string; attachments: { url: string; parse_status: string }[] }; predicted: boolean; prediction: { reasons: string[]; gaps?: string[] }; verdict: string; notes: string; quote: string; evidence_supported: boolean | null; label_version: number };
type Metrics = { tp: number; fp: number; fn: number; tn: number; total: number; judged: number; pending: number; unsure: number; precision: number | null; recall: number | null; coverage: number | null; evidence_support: number | null; evidence_yes: number; evidence_no: number; notice: string };
type Study = { id: string; created_at: string; view: string; snapshot: { name: string; revision: number; profile: Values; project: { name: string; data: Values; description: string; revision: number } | null }; selection: { population: number; sample_size: number; prediction_mode: string }; metrics: Metrics; cases?: Case[] };
type Label = { verdict: string; evidence: string; notes: string; quote: string };
const verdicts = { pending: "待核验", relevant: "相关", irrelevant: "不相关", unsure: "无法判断" };
const percent = (value: number | null) => value === null ? "暂无可计算样本" : `${(value * 100).toFixed(1)}%`;

function Snapshot({ data, options }: { data: Values; options: Options }) {
  const labels = Object.fromEntries(Object.values(options.tags).flat().map(item => [item.value, item.label]));
  return <Descriptions column={1} size="small" items={Object.entries(data).map(([key, value]) => ({ key, label: options.fields[key] || "补充资料", children: Array.isArray(value) ? value.map(v => labels[v] || v).join("、") : value || "未填写" }))} />;
}

function ReviewCase({ item, study, options, close, onSaved }: { item: Case; study: Study; options: Options; close: () => void; onSaved: (next: boolean) => void }) {
  const { message, modal } = App.useApp();
  const client = useQueryClient();
  const [form] = Form.useForm<Label>();
  const decision = Form.useWatch("verdict", form);
  const [dirty, setDirty] = useState(false);
  const [showSystem, setShowSystem] = useState(false);
  const nextAfterSave = useRef(true);
  function requestClose() {
    if (save.isPending) return;
    if (dirty) modal.confirm({ title: "当前意见还没有保存", content: "关闭将放弃本次填写。", okText: "放弃并关闭", cancelText: "继续填写", onOk: close });
    else close();
  }
  const save = useMutation({ mutationFn: (values: Label) => api(`matching-studies/${study.id}/label/${item.id}`, { method: "POST", body: JSON.stringify({ verdict: values.verdict, notes: values.notes.trim(), quote: values.quote?.trim() || "", label_version: item.label_version, evidence_supported: values.evidence === "yes" ? true : values.evidence === "no" ? false : null }) }),
    onSuccess: () => { setDirty(false); void client.invalidateQueries({ queryKey: ["matching-studies"] }); void client.invalidateQueries({ queryKey: ["matching-study", study.id] }); message.success("核验结果已保存"); onSaved(nextAfterSave.current); }, onError: error => message.error(error.message) });
  return <Modal open title="对照企业资料与政策原文" width={1380} onCancel={requestClose} closable={!save.isPending} keyboard={!save.isPending} mask={{ closable: !save.isPending }} footer={null}>
    <p className="muted">以下是 {new Date(study.created_at).toLocaleString("zh-CN")} 保存的第 {item.snapshot.version} 版原文。请按当时的{study.view === "opportunities" ? "政策机会" : "政策文件"}范围判断是否值得关注，不进行资格认证。</p>
    <div className="quality-review-layout">
      <section className="quality-review-source">
        <SourceReader documents={[{ ...item.snapshot, id: item.policy || item.id }]} onQuote={(_, text) => { if (!save.isPending) { form.setFieldValue("quote", text); setDirty(true); message.success("已填入原文依据"); } }} />
      </section>
      <section className="quality-review-answer">
        <Collapse defaultActiveKey={["profile"]} items={[{ key: "profile", label: `核验对象：${study.snapshot.project?.name || study.snapshot.name}`, children: <><Snapshot data={study.snapshot.profile} options={options} />{study.snapshot.project && <><p>{study.snapshot.project.description}</p><Snapshot data={study.snapshot.project.data} options={options} /></>}</> }]} />
        <Form form={form} layout="vertical" style={{ marginTop: 20 }} disabled={save.isPending} initialValues={{ verdict: item.verdict === "pending" ? undefined : item.verdict, notes: item.notes, quote: item.quote, evidence: item.evidence_supported === null ? "unknown" : item.evidence_supported ? "yes" : "no" }} onValuesChange={() => setDirty(true)} onFinish={values => save.mutate(values)}>
          <Form.Item name="verdict" label="这份政策是否值得该企业关注？" rules={[{ required: true, message: "请选择判断，也可以选择无法判断。" }]}><Radio.Group className="quality-choices" options={[{ value: "relevant", label: "相关，值得关注" }, { value: "irrelevant", label: "不相关，不适用于本次关注范围" }, { value: "unsure", label: "暂时无法判断，需要更多信息" }]} /></Form.Item>
          <Form.Item name="quote" label="原文依据" dependencies={["verdict"]} extra="判为相关时必填。选中左侧原文，再点击引用。" rules={[{ validator: async (_, value: string | undefined) => { const quote = value?.trim() || ""; if (decision === "relevant" && (quote.length < 6 || !item.snapshot.body.includes(quote))) throw new Error("请引用至少 6 个连续的原文字符。"); } }]}><Input.TextArea rows={3} maxLength={5000} /></Form.Item>
          <Form.Item name="notes" label={decision === "unsure" ? "还缺什么信息？" : "用一句话说明原因"} rules={[{ required: true, whitespace: true, min: 5, max: 2000, message: "请写至少 5 个字，说明判断理由或缺少的信息。" }]}><Input.TextArea rows={3} maxLength={2000} placeholder="结合企业业务与原文，说明值得关注、不适用或信息不足的原因。" /></Form.Item>
          {!showSystem ? <Button className="space-bottom" disabled={!decision || save.isPending} onClick={() => setShowSystem(true)}>我已作出判断，查看系统理由</Button> : <div className="quality-system-result"><strong>系统判断：{item.predicted ? "建议关注" : "未推荐"}</strong>{item.prediction.reasons.map((reason, i) => <p key={i}>{reason}</p>)}{item.prediction.gaps?.map((gap, i) => <p key={i} className="muted">{gap}</p>)}<Form.Item name="evidence" label="系统理由是否有材料支持"><Radio.Group className="quality-evidence-options" options={[{ value: "unknown", label: "暂不判断" }, { value: "yes", label: "有支持" }, { value: "no", label: "缺乏支持" }]} /></Form.Item></div>}
          {save.error && <Alert type="error" className="space-bottom" title={save.error.message} />}
          <div className="quality-review-actions"><Space wrap><Button type="primary" loading={save.isPending} onClick={() => { nextAfterSave.current = true; form.submit(); }}>保存并下一份</Button><Button loading={save.isPending} onClick={() => { nextAfterSave.current = false; form.submit(); }}>保存并返回</Button></Space></div>
        </Form>
      </section>
    </div>
  </Modal>;
}

export default function MatchingEvaluation({ profile, projects, options }: { profile: string; projects: { id: string; name: string }[]; options: Options }) {
  const [selected, setSelected] = useState<string>();
  const [review, setReview] = useState<Case>();
  const [page, setPage] = useState(1);
  const { message } = App.useApp();
  const client = useQueryClient();
  const studies = useQuery({ queryKey: ["matching-studies", profile, page], queryFn: () => api<Page<Study>>(`matching-studies?profile=${profile}&page=${page}`) });
  const detail = useQuery({ queryKey: ["matching-study", selected], queryFn: () => api<Study>(`matching-studies/${selected}`), enabled: !!selected });
  const sample = useMutation({ mutationFn: (values: { project: string; view: string; limit: number }) => api<Study>("matching-studies/sample", { method: "POST", body: JSON.stringify({ ...values, profile, project: values.project === "company" ? null : values.project }) }),
    onSuccess: result => { setSelected(result.id); setPage(1); void client.invalidateQueries({ queryKey: ["matching-studies"] }); message.success("已抽取推荐和未推荐样本，请逐条核验"); }, onError: error => message.error(error.message) });
  const study = detail.data;
  const m = study?.metrics;
  return <Card title="匹配核验">
    <p className="muted">同时检查误推与漏判。保存抽样时的企业资料和政策原文，由你判断是否相关；核验不会修改画像、发布政策或发送通知。</p>
    <Form layout="inline" initialValues={{ project: "company", view: "opportunities", limit: 20 }} style={{ gap: 12, marginBottom: 20 }} onFinish={values => sample.mutate(values)}>
      <Form.Item name="project" label="核验对象"><Select style={{ minWidth: 180 }} options={[{ value: "company", label: "企业整体" }, ...projects.map(p => ({ value: p.id, label: p.name }))]} /></Form.Item>
      <Form.Item name="view" label="范围"><Select style={{ width: 130 }} options={[{ value: "opportunities", label: "政策机会" }, { value: "policies", label: "政策文件" }]} /></Form.Item>
      <Form.Item name="limit" label="样本数"><InputNumber min={4} max={40} precision={0} /></Form.Item>
      <Button htmlType="submit" type="primary" loading={sample.isPending}>建立核验样本</Button>
    </Form>
    {studies.error && <Alert type="error" title={studies.error.message} />}
    {!studies.isLoading && !studies.error && !studies.data?.count && <Empty description="尚无核验样本" />}
    <Space wrap>{studies.data?.items.map(item => <Button key={item.id} type={selected === item.id ? "primary" : "default"} onClick={() => setSelected(item.id)}>{new Date(item.created_at).toLocaleString("zh-CN")} · {item.snapshot.project?.name || "企业整体"} · 已核验 {item.metrics.judged}/{item.metrics.total}</Button>)}</Space>
    {!!studies.data?.count && <Pagination current={page} pageSize={20} total={studies.data.count} showSizeChanger={false} onChange={setPage} style={{ marginTop: 12 }} />}
    {detail.error && <Alert type="error" title={detail.error.message} />}
    {study && m && <>
      <h3>本组样本结果</h3>
      <p>{study.view === "opportunities" ? "政策机会" : "政策文件"} · 从 {study.selection.population} 份正式政策中抽取 {m.total} 份</p>
      <Button type="primary" className="space-bottom" disabled={!study.cases?.some(item => item.verdict === "pending")} onClick={() => setReview(study.cases?.find(item => item.verdict === "pending"))}>开始 / 继续核验</Button>
      <Alert type="info" showIcon title="指标只代表本组已明确标注的样本" description={m.notice} />
      <Descriptions style={{ marginTop: 16 }} column={{ xs: 1, sm: 2, lg: 3 }} items={[
        { key: "precision", label: "系统推荐中，有多少值得关注", children: `${percent(m.precision)}（${m.tp}/${m.tp + m.fp}）` },
        { key: "recall", label: "值得关注的政策，系统找到了多少", children: `${percent(m.recall)}（${m.tp}/${m.tp + m.fn}）` },
        { key: "coverage", label: "已作出明确判断", children: `${m.judged}/${m.total}` },
        { key: "errors", label: "误推／漏判", children: `${m.fp} / ${m.fn}` },
        { key: "pending", label: "待核验／无法判断", children: `${m.pending} / ${m.unsure}` },
        { key: "evidence", label: "理由支持率", children: `${percent(m.evidence_support)}（${m.evidence_yes}/${m.evidence_yes + m.evidence_no}）` },
      ]} />
      <Collapse size="small" items={[{ key: "profile", label: `核对抽样时的企业／项目资料 · ${study.snapshot.name} · 画像第 ${study.snapshot.revision} 版`, children: <><Snapshot data={study.snapshot.profile} options={options} />{study.snapshot.project && <><h4>{study.snapshot.project.name}</h4><p>{study.snapshot.project.description}</p><Snapshot data={study.snapshot.project.data} options={options} /></>}</> }]} />
      {study.cases?.map(item => <div className="spread" key={item.id} style={{ padding: "16px 0", borderBottom: "1px solid #eee", gap: 16 }}><div><p style={{ margin: "0 0 8px" }}>{item.snapshot.title}</p><Space wrap>{item.verdict !== "pending" && <Tag>{item.predicted ? "系统建议关注" : "系统未推荐"}</Tag>}<Tag>{verdicts[item.verdict as keyof typeof verdicts] || "待核验"}</Tag></Space></div><Button onClick={() => setReview(item)}>查看全文并核验</Button></div>)}
      {review && <ReviewCase key={`${review.id}:${review.label_version}`} item={review} study={study} options={options} close={() => setReview(undefined)} onSaved={next => { const following = next ? study.cases?.find(item => item.id !== review.id && item.verdict === "pending") : undefined; setReview(following); if (next && !following) message.info("本组待核验文件已完成，可查看无法判断的记录和统计结果。"); }} />}
    </>}
  </Card>;
}
