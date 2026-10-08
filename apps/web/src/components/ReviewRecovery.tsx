"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Collapse, Form, InputNumber, Pagination, Space, Switch, Tag } from "antd";
import { api, type Page } from "@/lib/api";
import { explainSystemText } from "@/lib/system-messages";

type Recovery = {
  id: string; policy: string; policy_title: string; status: string; stage: string;
  message: string; attempts: number; retry_at: string | null;
  details: { before?: { status: string; error: string; version: number };
    after?: { policy_status: string; decision: string; version: number };
    parsed_attachments?: string[]; model_calls?: number; parse_notes?: string[] };
};
const states: Record<string, string> = { queued: "等待处理", running: "处理中", succeeded: "处理完成", failed: "恢复未完成", blocked: "需要人工处理", cancelled: "已停止" };

export function RecoveryAction({ enrichment }: { enrichment: string }) {
  const client = useQueryClient();
  const { message } = App.useApp();
  const start = useMutation({
    mutationFn: () => api<Recovery>("admin/review-recoveries/enqueue", { method: "POST", body: JSON.stringify({ enrichment }) }),
    onSuccess: data => { message.info(data.message || "已加入异常处理队列"); void client.invalidateQueries({ queryKey: ["review-recovery"] }); },
    onError: error => message.error(error.message),
  });
  return <Button block loading={start.isPending} onClick={() => start.mutate()}>分析原因并尝试恢复</Button>;
}

export function RecoveryHistory({ onSelect, focusedPolicyId }: { onSelect: (id: string) => void; focusedPolicyId?: string }) {
  const [page, setPage] = useState(1);
  const client = useQueryClient();
  const { message } = App.useApp();
  const records = useQuery({ queryKey: ["review-recovery", page, focusedPolicyId], queryFn: () => api<Page<Recovery>>(`admin/review-recoveries?page=${page}${focusedPolicyId ? `&policy_id=${encodeURIComponent(focusedPolicyId)}` : ""}`), refetchInterval: 5000 });
  const action = useMutation({
    mutationFn: ({ id, verb }: { id: string; verb: string }) => api(`admin/review-recoveries/${id}/${verb}`, { method: "POST" }),
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["review-recovery"] }); void client.invalidateQueries({ queryKey: ["review"] }); },
    onError: error => message.error(error.message),
  });
  return <Collapse style={{ marginBottom: 16 }} items={[{
    key: "recovery", label: `审核异常处理记录（${records.data?.count || 0}）`,
    children: <>
      {records.error && <Alert type="error" title={records.error.message} />}
      <p className="muted">按原因恢复，不降低发布标准。单条处理可独立运行；自动处理需同时开启自动审核和异常处理开关。</p>
      {records.data?.items.map(item => <div className="source-card" key={item.id}>
        <strong>{item.policy_title}</strong> <Tag>{states[item.status] || "等待处理"}</Tag>
        <p>{item.stage} · 已尝试 {item.attempts} 次 · 模型请求 {item.details.model_calls || 0} 次</p>
        <p>{item.message}</p>
        {item.details.before?.error && <p className="muted">处理前：{explainSystemText(item.details.before.error)}</p>}
        {!!item.details.parsed_attachments?.length && <p>已补入 {item.details.parsed_attachments.length} 份附件解析内容。</p>}
        {item.details.parse_notes?.map((note, i) => <p key={i}>{explainSystemText(note)}</p>)}
        {item.retry_at && ["queued", "failed"].includes(item.status) && <p className="muted">最早继续时间：{new Date(item.retry_at).toLocaleString("zh-CN")}</p>}
        <Space wrap>
          <Button onClick={() => onSelect(item.policy)}>查看政策与审核依据</Button>
          {["queued", "running"].includes(item.status) && <Button danger loading={action.isPending} onClick={() => action.mutate({ id: item.id, verb: "stop" })}>停止处理</Button>}
          {["failed", "cancelled"].includes(item.status) && <Button loading={action.isPending} onClick={() => action.mutate({ id: item.id, verb: "resume" })}>继续处理</Button>}
        </Space>
      </div>)}
      {!!records.data?.count && <Pagination current={page} total={records.data.count} pageSize={20} showSizeChanger={false} onChange={setPage} />}
    </>,
  }]} />;
}

type Settings = { recovery_enabled: boolean; recovery_daily_limit: number; recovery_attempt_limit: number; recovery_cooldown_minutes: number };
export function RecoverySettings() {
  const { message } = App.useApp();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["recovery-settings"], queryFn: () => api<Settings>("admin/review-recoveries/settings"), refetchOnWindowFocus: false });
  const save = useMutation({ mutationFn: (values: Settings) => api("admin/review-recoveries/settings", { method: "PATCH", body: JSON.stringify(values) }),
    onSuccess: () => { message.success("异常处理设置已保存"); void client.invalidateQueries({ queryKey: ["recovery-settings"] }); }, onError: error => message.error(error.message) });
  return <div style={{ marginTop: 24 }}><h3>审核异常自动处理</h3>
    <p className="muted">重解析已保存附件、恢复分段检查点并重新审核。自动处理默认关闭；开启后仍受自动审核总开关控制。</p>
    {query.error ? <Alert type="error" title={query.error.message} /> : query.data && <Form layout="vertical" initialValues={query.data} onFinish={values => save.mutate(values)}>
      <Form.Item name="recovery_enabled" label="自动处理审核异常" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="recovery_daily_limit" label="每天最多处理次数" rules={[{ required: true }]}><InputNumber min={1} max={200} precision={0} /></Form.Item>
      <Form.Item name="recovery_attempt_limit" label="每份异常最多尝试次数" rules={[{ required: true }]}><InputNumber min={1} max={3} precision={0} /></Form.Item>
      <Form.Item name="recovery_cooldown_minutes" label="失败后等待时间（分钟）" rules={[{ required: true }]}><InputNumber min={5} max={1440} precision={0} /></Form.Item>
      <Button htmlType="submit" type="primary" loading={save.isPending}>保存异常处理设置</Button>
    </Form>}
  </div>;
}
