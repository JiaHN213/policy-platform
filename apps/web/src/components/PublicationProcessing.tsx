"use client";

import { useState } from "react";
import { Alert, App, Button, Card, Input, Select, Space, Table, Tag } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Page } from "@/lib/api";

type Consumption = {
  id: string; policy_id: string; policy_title: string; policy_version: number;
  event_label: string; consumer: string; consumer_label: string; status: string;
  status_label: string; attempts: number; retry_at: string | null;
  last_error: string; last_success_version: number | null; succeeded_at: string | null;
  result: { message?: string }; can_retry: boolean;
};
type Summary = { events: number; counts: Record<string, number>; consumers: Record<string, {
  label: string; total: number; pending: number; running: number; waiting: number;
  retry: number; failed: number; succeeded: number;
}> };
const statuses = [
  { value: "", label: "全部状态" }, { value: "pending", label: "等待处理" },
  { value: "running", label: "正在处理" }, { value: "waiting", label: "等待知识构建完成" },
  { value: "retry", label: "等待自动重试" }, { value: "failed", label: "需要处理" },
  { value: "succeeded", label: "已完成" },
];
const timeText = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—";

export default function PublicationProcessing({ onSelect, focusedPolicyId }: { onSelect: (id: string) => void; focusedPolicyId?: string }) {
  const { message } = App.useApp();
  const client = useQueryClient();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [consumer, setConsumer] = useState("");
  const [keyword, setKeyword] = useState("");
  const summary = useQuery({ queryKey: ["publication-consumers-summary"],
    queryFn: () => api<Summary>("admin/publication-consumers/summary"), refetchInterval: 15000 });
  const params = new URLSearchParams({ page: String(page), status, consumer, q: keyword });
  if (focusedPolicyId) params.set("policy_id", focusedPolicyId);
  const jobs = useQuery({ queryKey: ["publication-consumers", params.toString()],
    queryFn: () => api<Page<Consumption>>(`admin/publication-consumers?${params}`), refetchInterval: 15000 });
  const retry = useMutation({
    mutationFn: (id: string) => api(`admin/publication-consumers/${id}/retry`, { method: "POST" }),
    onSuccess: () => {
      message.success("已重新排队，只重试这一环节，已有通知不会重复发送。");
      client.invalidateQueries({ queryKey: ["publication-consumers"] });
      client.invalidateQueries({ queryKey: ["publication-consumers-summary"] });
      void client.invalidateQueries({ queryKey: ["pipeline-status"] });
    }, onError: (error: Error) => message.error(error.message),
  });
  return <Space orientation="vertical" style={{ width: "100%" }} size="large">
    <Alert type="info" showIcon title="政策发布后，各环节独立处理" description="搜索、站内通知、Wiki 和统计分别记录结果，失败自动重试，连续失败后可在这里手动重试。Wiki 按设定频率合并构建，完成后才显示成功。订阅仅影响通知，不限制客户搜索政策。" />
    {!focusedPolicyId && <>
    <div className="stats-grid">
      {Object.entries(summary.data?.consumers || {}).map(([key, value]) => <Card key={key} size="small">
        <strong>{value.label}</strong>
        <p>已完成 {value.succeeded} / {value.total}</p>
        <p className="muted">等待或处理中 {value.pending + value.running + value.waiting} · 自动重试 {value.retry} · 需要处理 {value.failed}</p>
        <Button size="small" onClick={() => { setConsumer(key); setStatus(""); setPage(1); }}>查看记录</Button>
      </Card>)}
    </div>
    <p className="muted">按处理环节计数，同一事件通常包含四个环节；共 {summary.data?.events ?? "—"} 个事件。截止提醒只处理通知和统计。</p>
    </>}
    <Space wrap>
      {!focusedPolicyId && <Input.Search placeholder="按政策名称查询" allowClear onSearch={(value) => { setKeyword(value); setPage(1); }} style={{ width: 260 }} />}
      <Select aria-label="处理环节" style={{ width: 170 }} value={consumer} onChange={(value) => { setConsumer(value); setPage(1); }} options={[{ value: "", label: "全部处理环节" }, ...Object.entries(summary.data?.consumers || {}).map(([value, item]) => ({ value, label: item.label }))]} />
      <Select aria-label="处理状态" style={{ width: 190 }} value={status} onChange={(value) => { setStatus(value); setPage(1); }} options={statuses} />
      <Button onClick={() => { jobs.refetch(); summary.refetch(); }}>刷新</Button>
    </Space>
    {(jobs.error || summary.error) && <Alert type="error" showIcon title={(jobs.error || summary.error)?.message} />}
    <Table<Consumption> rowKey="id" dataSource={jobs.data?.items || []} loading={jobs.isLoading} scroll={{ x: 1000 }}
      pagination={{ current: page, total: jobs.data?.count || 0, pageSize: 20, showSizeChanger: false, onChange: setPage }}
      columns={[
        { title: "政策与事件", key: "policy", width: 300, render: (_, row) => <><Button type="link" style={{ padding: 0, whiteSpace: "normal", height: "auto", textAlign: "left" }} onClick={() => onSelect(row.policy_id)}>{row.policy_title}</Button><div className="small muted">{row.event_label} · 第 {row.policy_version} 版</div></> },
        { title: "处理环节", dataIndex: "consumer_label", width: 140 },
        { title: "当前状态", key: "status", width: 170, render: (_, row) => <Tag color={row.status === "succeeded" ? "green" : row.status === "failed" ? "red" : "gold"}>{row.status_label}</Tag> },
        { title: "进度与处理建议", key: "progress", render: (_, row) => <><div>{row.last_error || row.result.message || "已登记，后台会自动处理。"}</div><div className="small muted">已尝试 {row.attempts} 次{row.status === "succeeded" ? ` · 完成于 ${timeText(row.succeeded_at)} · 已处理第 ${row.last_success_version ?? row.policy_version} 版` : row.retry_at ? ` · 下次处理 ${timeText(row.retry_at)}` : ""}</div></> },
        { title: "操作", key: "action", width: 110, render: (_, row) => row.can_retry ? <Button size="small" loading={retry.isPending && retry.variables === row.id} onClick={() => retry.mutate(row.id)}>重试此环节</Button> : "—" },
      ]} />
  </Space>;
}
