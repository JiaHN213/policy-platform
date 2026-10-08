"use client";

import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Space, Tag } from "antd";
import { api } from "@/lib/api";

type Status = { checked_at: string; notice: string; items: { key: string; label: string; consumer_count: number; shared: boolean; state: string; message: string }[] };

export default function WorkerStatus() {
  const status = useQuery({ queryKey: ["worker-status"], queryFn: () => api<Status>("admin/worker-status"), refetchOnWindowFocus: false, retry: false });
  return <Card title="后台处理服务" extra={<Button loading={status.isFetching} onClick={() => void status.refetch()}>重新检查</Button>}>
    <p className="muted">采集、AI 处理和通知分别安排后台服务，减少慢速采集对其他工作的影响。</p>
    {status.error && <Alert type="error" showIcon title={status.error.message} />}
    {status.isPending && <p>正在检查服务响应…</p>}
    <Space orientation="vertical" style={{ width: "100%" }}>{status.data?.items.map(item => <Card key={item.key} size="small" title={item.label} extra={<Tag color={item.state === "responding" ? "green" : "orange"}>{item.state === "responding" ? "已响应" : "待确认"}</Tag>}>
      <p>{item.message}</p>{item.consumer_count > 0 && <p className="small muted">响应服务 {item.consumer_count} 个 · {item.shared ? "共用执行服务" : "独立执行服务"}</p>}
    </Card>)}</Space>
    {status.data && <><p className="small muted">检查时间：{new Date(status.data.checked_at).toLocaleString("zh-CN")}</p><Alert type="info" showIcon title="服务响应与业务处理结果分别查看" description={status.data.notice} /></>}
  </Card>;
}
