"use client";

import PolicyBody from "@/components/policy/PolicyBody";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Collapse, Modal, Space, Tag } from "antd";
import { api, safeExternalUrl, type PolicyDetail } from "@/lib/api";

export type Repair = {
  id: string; status: string; stage: string; outcome: string; attempts: number;
  message: string; can_retry: boolean;
  result: {
    actions?: string[]; assessment?: string; page_refresh?: string;
    coverage?: { policy_id: string; title: string; read_chars: number; total_chars: number;
      full: boolean; readiness: { status: string; reason: string } }[];
    evidence?: { policy_id: string; quote: string }[];
  };
};
const labels: Record<string, string> = {
  valid_relation: "找到有效关系", no_relation: "本轮未发现直接关系", needs_review: "仍需人工复审",
  failed: "处理失败", cancelled: "已停止", queued: "等待补查", running: "正在补查",
};

export function RepairSummary({ scope }: { scope: string }) {
  const client = useQueryClient();
  const { message } = App.useApp();
  const stats = useQuery({
    queryKey: ["relation-repair-stats", scope],
    queryFn: () => api<Record<string, number>>(`admin/relation-review-candidates/repair-stats?status=all&scope=${scope}`),
    refetchInterval: 10_000,
  });
  const batch = useMutation({
    mutationFn: () => api<{ queued: number; skipped: string[] }>(`admin/relation-review-candidates/repair-batch?scope=${scope}`, { method: "POST" }),
    onSuccess: (data) => { message.info(data.queued ? `已安排 ${data.queued} 组补查` : data.skipped[0] || "当前范围没有可新增的补查任务"); void client.invalidateQueries(); },
    onError: (error) => message.error(error.message),
  });
  return <div className="source-card">
    <div className="spread"><strong>关系证据补查</strong><Button loading={batch.isPending} onClick={() => batch.mutate()}>补查本范围待复审关系（最多20组）</Button></div>
    <p className="muted">按当前候选范围统计。未发现直接关系仅指已读取资料中的结论，仍可人工核验。</p>
    {stats.error ? <Alert type="error" title={stats.error.message} /> : <Space wrap>
      <Tag>新增有效关系 {stats.data?.new_relations || 0}</Tag>
      {["queued", "running", "valid_relation", "no_relation", "needs_review", "failed", "cancelled"].map(key => <Tag key={key}>{labels[key]} {stats.data?.[key] || 0}</Tag>)}
    </Space>}
  </div>;
}

export function PolicyPair({ from, to }: { from: string; to: string }) {
  const left = useQuery({ queryKey: ["repair-policy", from], queryFn: () => api<PolicyDetail>(`admin/policies/${from}`) });
  const right = useQuery({ queryKey: ["repair-policy", to], queryFn: () => api<PolicyDetail>(`admin/policies/${to}`) });
  return <Collapse items={[left, right].map((query, index) => ({
    key: String(index), label: query.data?.title || (index ? "关系终点全文" : "关系起点全文"),
    children: query.error ? <Alert type="error" title={query.error.message} /> : query.data ? <>
      <p>{query.data.document_number} · {query.data.publication_date}</p>
      <a href={safeExternalUrl(query.data.source_url)} target="_blank" rel="noopener noreferrer">查看官方原文 ↗</a>
      {query.data.attachments?.map((attachment, i) => {
        const item = attachment as Record<string, unknown>;
        return <p key={i}><a href={safeExternalUrl(String(item.url || ""))} target="_blank" rel="noopener noreferrer">{String(item.name || item.title || "附件")}</a></p>;
      })}
      <PolicyBody text={query.data.body || "暂无正文"} />
    </> : "正在读取全文及已解析附件……",
  }))} />;
}

export function RepairCard({ id, repair, pending, from, to }: {
  id: string; repair?: Repair | null; pending: boolean; from: string; to: string;
}) {
  const [open, setOpen] = useState(false);
  const client = useQueryClient();
  const { message } = App.useApp();
  const action = useMutation({
    mutationFn: (stop: boolean) => api(`admin/relation-review-candidates/${id}/${stop ? "stop-repair" : "repair"}?status=all`, { method: "POST" }),
    onSuccess: () => { void client.invalidateQueries(); message.success("补查任务已更新"); },
    onError: (error) => message.error(error.message),
  });
  const active = repair && ["queued", "running"].includes(repair.status);
  return <div style={{ margin: "12px 0" }}>
    <Space wrap>
      {pending && (!repair || (["failed", "cancelled"].includes(repair.status) && repair.can_retry)) && <Button loading={action.isPending} onClick={() => action.mutate(false)}>补查证据并重新判断</Button>}
      {pending && repair?.status === "succeeded" && <Button loading={action.isPending} onClick={() => action.mutate(false)}>检查资料更新后补查</Button>}
      {active && <Button loading={action.isPending} onClick={() => action.mutate(true)}>停止补查</Button>}
      {repair && <Tag>{active ? repair.stage : labels[repair.outcome || repair.status] || repair.stage}</Tag>}
      <Button type="link" onClick={() => setOpen(true)}>查看全文与补查详情</Button>
    </Space>
    {repair?.message && <p style={{ whiteSpace: "pre-line" }}>{repair.message}</p>}
    {["pending", "running"].includes(repair?.result.page_refresh || "") && <p className="muted">关系已保存，相关知识页正在排队更新。</p>}
    {repair?.result.page_refresh === "failed" && <p className="muted">关系已保存，知识页更新多次未完成，可在知识管理中重新构建。</p>}
    <Modal title="关系依据与政策全文" width={1050} open={open} onCancel={() => setOpen(false)} footer={null} destroyOnHidden>
      {open && <>
        {repair?.result.actions?.map((item, index) => <p key={index}>{index + 1}. {item}</p>)}
        {repair?.result.assessment && <Alert type="info" title="AI 补查说明（需结合下方校验结果）" description={repair.result.assessment} />}
        {repair?.message && <p style={{ whiteSpace: "pre-line" }}><strong>程序校验：</strong>{repair.message}</p>}
        {repair?.result.coverage?.map(item => <p key={item.policy_id}>{item.title}：读取 {item.read_chars} / {item.total_chars} 字，{item.full ? "已读完已保存内容" : "仅阅读相关片段"}。{item.readiness.reason}</p>)}
        {repair?.result.evidence?.map((item, index) => <blockquote key={index}>{item.quote}</blockquote>)}
        <PolicyPair from={from} to={to} />
      </>}
    </Modal>
  </div>;
}
