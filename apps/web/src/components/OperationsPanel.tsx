"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Drawer, Select, Space, Table, Tabs, Tag } from "antd";
import ReviewPanel from "@/components/management/ReviewPanel";
import SourcePanel from "@/components/management/SourcePanel";
import PublicationProcessing from "@/components/PublicationProcessing";
import PolicyDrawer from "@/components/policy/PolicyDrawer";
import { api } from "@/lib/api";

type Usage = { items: { label: string; purpose: string; model: string; currency: string; calls: number; average_ms: number; input_tokens: number | null; output_tokens: number | null; input_calls: number; output_calls: number; cost: string | null; priced_calls: number }[]; states: { status: string; count: number }[]; notice: string };
type Row = { id: string; policy_id: string | null; intake_status: string; policy_status: string | null; title: string; source: string; stage: string; overdue: boolean; discovered_at: string; parsed_at: string | null; reviewed_at: string | null; published_at: string | null; notified_at: string | null; elapsed_seconds: number | null };
const date = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "暂无记录";

export default function OperationsPanel({ kind }: { kind: "usage" | "pipeline" }) {
  const [mode, setMode] = useState("pending");
  const [selected, setSelected] = useState<Row | null>(null);
  const [policyDetailId, openPolicy] = useState<string | null>(null);
  const usage = useQuery({ queryKey: ["ai-usage"], queryFn: () => api<Usage>("admin/ai-usage"), enabled: kind === "usage", refetchOnWindowFocus: false });
  const pipeline = useQuery({ queryKey: ["pipeline-status", mode], queryFn: () => api<{ items: Row[]; notice: string }>(`admin/pipeline-status?mode=${mode}`), enabled: kind === "pipeline", refetchOnWindowFocus: false });
  const detail = useQuery({ queryKey: ["pipeline-status", "item", selected?.id], queryFn: () => api<{ items: Row[] }>(`admin/pipeline-status?item_id=${encodeURIComponent(selected!.id)}`), enabled: kind === "pipeline" && !!selected, refetchInterval: selected ? 5000 : false });
  const current = detail.data?.items[0];
  if (kind === "usage") return <Card title="AI 用量与费用估算" extra={<Button loading={usage.isFetching} onClick={() => void usage.refetch()}>刷新</Button>}>
    {usage.error && <Alert type="error" title={usage.error.message} />}
    <p>在“AI 模型与审核”中填写各用途每百万 Token 的输入／输出单价。未填写、供应商未返回用量时，费用显示未估算。</p>
    <Space wrap>{usage.data?.states.map(item => <Tag key={item.status}>{{ received: "已收到响应", failed: "请求失败", timeout: "请求超时" }[item.status] || "其他"} {item.count}</Tag>)}</Space>
    <Table style={{ marginTop: 16 }} loading={usage.isLoading} rowKey={r => `${r.purpose}:${r.model}:${r.currency}`} dataSource={usage.data?.items || []} scroll={{ x: 800 }} columns={[
      { title: "用途", dataIndex: "label" }, { title: "模型", dataIndex: "model" }, { title: "请求次数", dataIndex: "calls" },
      { title: "平均耗时", render: (_, r) => `${(r.average_ms / 1000).toFixed(1)} 秒` },
      { title: "输入／输出 Token", render: (_, r) => `${r.input_tokens ?? "未知"} / ${r.output_tokens ?? "未知"}（分别覆盖 ${r.input_calls}/${r.calls}、${r.output_calls}/${r.calls} 次）` },
      { title: "估算费用", render: (_, r) => r.cost === null ? "未估算" : `${r.cost} ${r.currency}（覆盖 ${r.priced_calls}/${r.calls} 次）` },
    ]} />{usage.data && <Alert type="info" title="统计范围与费用口径" description={usage.data.notice} />}
  </Card>;
  return <><Card title="政策全过程追踪" extra={<Button loading={pipeline.isFetching} onClick={() => void pipeline.refetch()}>刷新</Button>}>
    <Select aria-label="追踪范围" value={mode} onChange={setMode} style={{ width: 220, marginBottom: 16 }} options={[{ value: "pending", label: "最早未发布的链接" }, { value: "recent", label: "最近发现的链接" }]} />
    {pipeline.error && <Alert type="error" title={pipeline.error.message} />}
    <Table loading={pipeline.isLoading} rowKey="id" dataSource={pipeline.data?.items || []} pagination={{ pageSize: 10 }} scroll={{ x: 1400 }} columns={[
      { title: "政策文件", width: 300, render: (_, r) => <Button type="link" style={{ padding: 0, height: "auto", whiteSpace: "normal", textAlign: "left" }} onClick={() => setSelected(r)}>{r.title || "未命名政策链接"}</Button> }, { title: "来源", dataIndex: "source" },
      { title: "当前环节", render: (_, r) => <>{r.stage}{r.overdue && <Tag color="orange">已超过24小时</Tag>}</> },
      { title: "处理入口", fixed: "right", width: 160, render: (_, r) => <Button size="small" onClick={() => setSelected(r)}>查看原因与处理</Button> },
      ...([['discovered_at','发现'],['parsed_at','解析完成'],['reviewed_at','本版审核完成'],['published_at','本版发布'],['notified_at','首次生成通知']] as const).map(([key,title]) => ({ title, dataIndex: key, render: date })),
      { title: "发现至本版通知", render: (_, r) => r.elapsed_seconds === null ? "暂无完整记录" : `${(r.elapsed_seconds/3600).toFixed(1)} 小时` },
    ]} />{pipeline.data && <Alert type="info" title="统计口径" description={pipeline.data.notice} />}
  </Card>
    <Drawer title="当前政策处理" open={!!selected} onClose={() => { setSelected(null); openPolicy(null); void pipeline.refetch(); }} size="min(1180px, 96vw)" destroyOnHidden loading={detail.isLoading}>
      {detail.error && <Alert type="error" title="暂时无法读取处理记录" description={detail.error.message} action={<Button onClick={() => void detail.refetch()}>重试读取</Button>} />}
      {!detail.isLoading && !detail.error && !current && <Alert type="info" title="该链接记录已不存在，请刷新列表。" />}
      {current && <>
        <h3>{current.title || "未命名政策链接"}</h3>
        <Space wrap style={{ marginBottom: 16 }}>
          <Tag>{current.source}</Tag>
          <Tag>{current.stage}</Tag>
          {current.policy_id && <Button onClick={() => openPolicy(current.policy_id!)}>查看政策全文与附件</Button>}
          <Button href="/admin/sources">来源采集设置</Button>
          <Button href="/admin/review">审核队列与开关</Button>
        </Space>
        <p className="muted">下方只处理当前记录。重试会重新排队，不会跳过审核和发布条件；采集冷却、审核开关及任务调度仍然生效。</p>
        <Tabs key={current.id} defaultActiveKey={!current.policy_id || ["failed", "needs_review"].includes(current.intake_status) ? "source" : current.policy_status === "published" ? "publication" : "review"} items={[
          { key: "source", label: "采集与解析", children: <SourcePanel focusedItemId={current.id} /> },
          ...(current.policy_id ? [
            { key: "review", label: "审核与修正", children: <ReviewPanel focusedPolicyId={current.policy_id} onSelect={openPolicy} /> },
            { key: "publication", label: "发布后处理", children: <PublicationProcessing focusedPolicyId={current.policy_id} onSelect={openPolicy} /> },
          ] : []),
        ]} />
      </>}
      <PolicyDrawer id={policyDetailId} review onClose={() => openPolicy(null)} onSelect={openPolicy} />
    </Drawer>
  </>;
}
