"use client";

import { useRef, useState } from "react";
import { Alert, App, Button, Drawer, Empty, Form, Input, InputNumber, Modal, Progress, Select, Space, Table, Tabs, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Page } from "@/lib/api";
import SampleReview from "./SampleReview";
import QualityReports from "./QualityReports";
import { kinds, statusLabels, type Run, type Sample, type SampleDetail } from "./types";

export default function QualityWorkspace() {
  const { message, modal } = App.useApp();
  const client = useQueryClient();
  const [page, setPage] = useState(1);
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("pending");
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState("samples");
  const [selected, setSelected] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loadingNext, setLoadingNext] = useState(false);
  const skipped = useRef(new Set<string>());
  const [runId, setRunId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [policyQuery, setPolicyQuery] = useState("");
  const [addForm] = Form.useForm();
  const [seedForm] = Form.useForm();
  const addKind = Form.useWatch("kind", addForm);
  const params = new URLSearchParams({ page: String(page), kind, status, q: query });
  const samples = useQuery({ queryKey: ["quality-samples", params.toString()], queryFn: () => api<Page<Sample>>(`admin/quality/samples?${params}`) });
  const summary = useQuery({ queryKey: ["quality-summary"], queryFn: () => api<{ counts: { kind: string; status: string; count: number }[] }>("admin/quality/samples/summary") });
  const detail = useQuery({ queryKey: ["quality-sample", selected], queryFn: () => api<SampleDetail>(`admin/quality/samples/${selected}`), enabled: !!selected, refetchOnWindowFocus: false, refetchOnReconnect: false });
  const runs = useQuery({ queryKey: ["quality-runs"], queryFn: () => api<Page<Run>>("admin/quality/runs") });
  const policies = useQuery({ queryKey: ["quality-policy-options", policyQuery], queryFn: () => api<{ items: { id: string; title: string }[] }>(`admin/quality/samples/policies?q=${encodeURIComponent(policyQuery)}`), enabled: adding });
  const taxonomy = useQuery({ queryKey: ["taxonomy"], queryFn: () => api<Record<string, { value: string; label: string }[]>>("taxonomies") });
  const refresh = () => Promise.all([client.invalidateQueries({ queryKey: ["quality-samples"] }), client.invalidateQueries({ queryKey: ["quality-summary"] }), client.invalidateQueries({ queryKey: ["quality-sample"] })]);
  function guard(action: () => void) {
    if (busy || loadingNext) return;
    if (dirty) modal.confirm({ title: "当前意见还没有保存", content: "继续将放弃本次填写。也可以返回后点击保存。", okText: "放弃并继续", cancelText: "继续填写", onOk: () => { setDirty(false); action(); } });
    else action();
  }
  async function openNext(exclude?: string) {
    if (exclude) skipped.current.add(exclude);
    setLoadingNext(true);
    try {
      let nextPage = 1;
      while (true) {
        const response = await api<Page<Sample>>(`admin/quality/samples?${new URLSearchParams({ status: "pending", kind, page: String(nextPage) })}`);
        const next = response.items.find(item => !skipped.current.has(item.id));
        if (next) { setDirty(false); setSelected(next.id); break; }
        if (!response.next) { setSelected(null); skipped.current.clear(); message.info(response.count ? "本轮其余文件已跳过，可从列表再次打开。" : "当前类型已没有待评测文件，可以查看待协助事项或生成报告。"); break; }
        nextPage++;
      }
    } catch (error) { message.error((error as Error).message); }
    finally { setLoadingNext(false); }
  }
  const seed = useMutation({ mutationFn: (values: { kind: string; limit: number }) => api<{ created: number }>("admin/quality/samples/seed", { method: "POST", body: JSON.stringify({ ...values, kind: values.kind || undefined }) }), onSuccess: async (data, values) => { setPreparing(false); setKind(values.kind); setStatus("pending"); setPage(1); setTab("samples"); skipped.current.clear(); await refresh(); message.info(data.created ? `已准备 ${data.created} 份新材料，可以开始评测。` : "没有新增材料，可继续已有任务或指定文件。相同版本不会重复添加。"); }, onError: (error: Error) => message.error(error.message) });
  const evaluate = useMutation({ mutationFn: () => api<Run>("admin/quality/runs/evaluate", { method: "POST" }), onSuccess: data => { setRunId(data.id); setTab("reports"); void client.invalidateQueries({ queryKey: ["quality-runs"] }); message.info(data.status === "no_labels" ? "没有可比较的有效结果，请查看报告中的说明。" : "报告已生成。"); }, onError: (error: Error) => message.error(error.message) });
  const add = useMutation({ mutationFn: (values: Record<string, unknown>) => api<{ created: boolean; sample: Sample }>("admin/quality/samples/add", { method: "POST", body: JSON.stringify(values) }), onSuccess: data => { setAdding(false); setDirty(false); setSelected(data.sample.id); void refresh(); if (!data.created) message.info("这份材料已在评测库中，已打开已有记录。"); }, onError: (error: Error) => message.error(error.message) });
  const retire = useMutation({ mutationFn: (id: string) => api(`admin/quality/samples/${id}/retire`, { method: "POST" }), onSuccess: () => { void refresh(); setDirty(false); setSelected(null); }, onError: (error: Error) => message.error(error.message) });
  const count = (state: string) => summary.data?.counts.filter(item => item.status === state && (!kind || item.kind === kind)).reduce((sum, item) => sum + item.count, 0) || 0;
  const pending = count("pending"), labeled = count("labeled"), unsure = count("needs_help");
  const allLabeled = summary.data?.counts.filter(item => item.status === "labeled").reduce((sum, item) => sum + item.count, 0) || 0;
  const total = pending + labeled + unsure;
  return <div className="quality-workspace">
    <div className="quality-intro"><div><h2>人工评测工作台</h2><p className="muted">阅读原文，给出你的判断，系统负责统计结果。无需了解模型或技术指标。</p></div><Button onClick={() => setPreparing(true)}>准备评测材料</Button></div>
    <div className="quality-process"><span>① 准备材料</span><span>② 对照原文判断</span><span>③ 查看问题与报告</span></div>
    <div className="quality-summary">
      {[{ key: "pending", value: pending }, { key: "labeled", value: labeled }, { key: "needs_help", value: unsure }].map(item => <button type="button" className={`quality-summary-card ${status === item.key ? "is-active" : ""}`} key={item.key} onClick={() => { setStatus(item.key); setPage(1); setTab("samples"); }}><span>{statusLabels[item.key]}</span><strong>{item.value}</strong></button>)}
      <div className="quality-progress"><span>{kind ? kinds.find(item => item.value === kind)?.label : "全部类型"} · 明确完成 {labeled}/{total}</span><Progress percent={total ? Math.round(labeled / total * 100) : 0} showInfo={false} /><small>待协助判断单独保留，不算错误。</small></div>
    </div>
    <Space wrap className="space-bottom"><Button type="primary" loading={loadingNext} disabled={!pending || summary.isPending || !!summary.error} onClick={() => { skipped.current.clear(); void openNext(); }}>开始 / 继续评测</Button><Button disabled={!allLabeled} loading={evaluate.isPending} onClick={() => evaluate.mutate()}>生成评测报告</Button><Button type="text" onClick={() => { addForm.resetFields(); setPolicyQuery(""); setAdding(true); }}>指定文件评测</Button></Space>
    <details className="inline-help"><summary>第一次使用？查看操作说明</summary><p>先准备一小组材料，再逐份阅读原文。判断后可选核对系统依据，点击“保存并下一份”。资料不足时选“暂时无法判断”；已完成意见可以重新打开修改。</p><p>评测不会修改正式政策或调用模型重跑。报告比较已保存的系统结果，覆盖全部评测类型；列表筛选只影响列表与统计卡片。</p></details>
    {(samples.error || runs.error || summary.error) && <Alert type="error" title={(samples.error || runs.error || summary.error)?.message} action={<Button onClick={() => { void refresh(); void runs.refetch(); }}>重试</Button>} />}
    <Tabs activeKey={tab} onChange={setTab} items={[
      { key: "samples", label: "评测任务", children: <>
        <Space wrap className="space-bottom"><Select aria-label="评测类型" value={kind} style={{ width: 190 }} options={[{ value: "", label: "全部评测类型" }, ...kinds]} onChange={value => { setKind(value); setPage(1); }} /><Select aria-label="评测状态" value={status} style={{ width: 160 }} options={[{ value: "", label: "全部状态" }, ...Object.entries(statusLabels).map(([value, label]) => ({ value, label }))]} onChange={value => { setStatus(value); setPage(1); }} /><Input.Search allowClear placeholder="查找文件名称" aria-label="查找评测文件" onSearch={value => { setQuery(value); setPage(1); }} style={{ width: 240 }} /></Space>
        {status === "needs_help" && <Alert className="space-bottom" type="info" showIcon title="这些材料需要进一步判断" description="查看之前填写的困难原因，补充核对后可以改为明确结论。它们尚未计入评测得分。" />}
        <Table<Sample> rowKey="id" dataSource={samples.data?.items || []} loading={samples.isLoading} scroll={{ x: 780 }} locale={{ emptyText: <Empty description={status === "pending" ? "当前没有待评测材料，可准备新材料或切换状态。" : "没有符合条件的评测记录"} /> }} pagination={{ current: page, total: samples.data?.count, pageSize: 20, showSizeChanger: false, onChange: setPage }} columns={[
          { title: "文件 / 评测内容", dataIndex: "title", render: (title, row) => <><Button type="link" className="quality-title-link" onClick={() => setSelected(row.id)}>{title}</Button>{row.status === "needs_help" && <p className="quality-help-note">待协助：{row.gold.notes}</p>}</> },
          { title: "类型", width: 150, render: (_, row) => kinds.find(item => item.value === row.kind)?.label }, { title: "状态", width: 110, render: (_, row) => <Tag color={row.status === "needs_help" ? "orange" : row.status === "labeled" ? "green" : "default"}>{statusLabels[row.status] || "待评测"}</Tag> },
          { title: "最近填写人", dataIndex: "labeled_by_name", width: 110, render: value => value || "尚未填写" }, { title: "操作", width: 110, render: (_, row) => <Button onClick={() => setSelected(row.id)}>{row.status === "pending" ? "开始判断" : "查看 / 修改"}</Button> },
        ]} /></> },
      { key: "reports", label: "评测报告", children: <QualityReports runs={runs.data?.items || []} selected={runId} onSelect={setRunId} onOpen={setSelected} /> },
    ]} />
    <Drawer open={!!selected} title="对照原文进行评测" size="large" styles={{ wrapper: { width: "min(1440px, 100vw)" } }} onClose={() => guard(() => setSelected(null))} closable={!busy && !loadingNext} mask={{ closable: !busy && !loadingNext }} keyboard={!busy && !loadingNext} destroyOnHidden>
      {detail.error && <Alert type="error" title={detail.error.message} action={<Button onClick={() => void detail.refetch()}>重试</Button>} />}
      {(detail.isLoading || loadingNext) && <p role="status">正在读取评测材料…</p>}
      {detail.data && !loadingNext && <>
        {detail.data.stale_reason && <Alert className="space-bottom" type="warning" title="材料已更新，请使用新版本" description={detail.data.stale_reason} action={<Button loading={add.isPending} onClick={() => guard(() => add.mutate({ kind: detail.data!.kind, ...(detail.data!.policy ? { policy_id: detail.data!.policy } : {}), ...(detail.data!.related_policy ? { related_policy_id: detail.data!.related_policy } : {}), ...(detail.data!.page ? { page_id: detail.data!.page } : {}), ...(detail.data!.relation_kind ? { relation_kind: detail.data!.relation_kind } : {}) }))}>准备新版材料</Button>} />}
        {detail.data.status === "retired" && <Alert type="info" title="这份材料已停用，保留供查阅，不再参与新评测。" />}
        <SampleReview key={`${detail.data.id}:${detail.dataUpdatedAt}`} sample={detail.data} relationLabel={taxonomy.data?.relation_kinds?.find(item => item.value === detail.data?.relation_kind)?.label || "指定关系"} onDirty={setDirty} onBusy={setBusy} onReload={() => guard(() => { void detail.refetch(); })} onSkip={() => guard(() => { void openNext(detail.data!.id); })} onSaved={next => { setDirty(false); void refresh(); if (next) void openNext(detail.data!.id); else setSelected(null); }} />
        <details className="quality-sample-management"><summary>材料管理</summary><p>重复、不适用或已过时的材料可以停用，历史报告会保留。</p><Button danger loading={retire.isPending} disabled={busy || detail.data.status === "retired"} onClick={() => guard(() => modal.confirm({ title: "停用这份评测材料？", content: "不删除政策，只停止这份材料参与后续评测。", okText: "停用", cancelText: "取消", onOk: () => retire.mutateAsync(detail.data!.id) }))}>停用材料</Button></details>
      </>}
    </Drawer>
    <Modal title="准备一组评测材料" open={preparing} onCancel={() => setPreparing(false)} onOk={() => seedForm.submit()} okText="准备材料" cancelText="取消" confirmLoading={seed.isPending}>
      <p>从已有真实资料中抽取，相同版本不会重复添加。建议先做 12 份，熟悉后再增加。</p>
      <Form form={seedForm} layout="vertical" initialValues={{ kind: "opportunity", limit: 12 }} onFinish={values => seed.mutate(values)}>
        <Form.Item name="kind" label="这次想检查什么"><Select options={[...kinds, { value: "", label: "三类都检查" }]} /></Form.Item>
        <Form.Item name="limit" label="最多准备多少份" rules={[{ required: true, message: "请填写数量。" }]} extra="实际数量取决于已有资料；不会生成测试文件。"><InputNumber min={6} max={60} precision={0} /></Form.Item>
      </Form>
    </Modal>
    <Modal title="指定文件评测" open={adding} onCancel={() => setAdding(false)} onOk={() => addForm.submit()} okText="加入并打开" cancelText="取消" confirmLoading={add.isPending}>
      <p>可以加入你认为系统漏掉的文件关系。先选起点，再选终点，方向会保留。</p>
      <Form form={addForm} layout="vertical" initialValues={{ kind: "opportunity" }} onFinish={values => add.mutate(values)}>
        <Form.Item name="kind" label="检查内容" rules={[{ required: true }]}><Select options={kinds.slice(0, 2)} /></Form.Item>
        <Form.Item name="policy_id" label={addKind === "relation" ? "文件 1：关系起点" : "政策文件"} rules={[{ required: true, message: "请选择政策文件。" }]}><Select showSearch filterOption={false} onSearch={setPolicyQuery} placeholder="输入文件名称查找" options={policies.data?.items.map(item => ({ value: item.id, label: item.title }))} /></Form.Item>
        {addKind === "relation" && <><Form.Item name="related_policy_id" label="文件 2：关系终点" preserve={false} rules={[{ required: true, message: "请选择另一份文件。" }]}><Select showSearch filterOption={false} onSearch={setPolicyQuery} options={policies.data?.items.map(item => ({ value: item.id, label: item.title }))} /></Form.Item><Form.Item name="relation_kind" label="文件 1 到文件 2 的关系" preserve={false} rules={[{ required: true, message: "请选择关系类型。" }]}><Select options={taxonomy.data?.relation_kinds || []} /></Form.Item></>}
      </Form>
      {policies.error && <Alert type="error" title={policies.error.message} />}
    </Modal>
  </div>;
}
