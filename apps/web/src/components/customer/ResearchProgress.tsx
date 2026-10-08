"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Collapse, Space, Tag } from "antd";
import { api } from "@/lib/api";

type Agent = { enabled?: boolean; model?: string; usage?: { reads?: number; calls?: number }; max_reads?: number; max_calls?: number; steps?: { sequence: number; label: string; status: string; detail: string }[] };
export default function ResearchProgress({ id, status, agent }: { id: string; status: string; agent?: Agent }) {
  const client = useQueryClient();
  const { message } = App.useApp();
  const control = useMutation({ mutationFn: (action: "stop" | "resume") => api(`enterprise-research/${id}/${action}`, { method: "POST" }), onSuccess: value => { client.setQueryData(["enterprise-run", id], value); void client.invalidateQueries({ queryKey: ["enterprise-recent"] }); }, onError: (e: Error) => message.error(e.message) });
  return <div className="space-bottom"><Space wrap>{["queued", "running"].includes(status) && <Button onClick={() => control.mutate("stop")} loading={control.isPending}>停止整理</Button>}{agent?.enabled && ["paused", "failed"].includes(status) && <Button onClick={() => control.mutate("resume")} loading={control.isPending}>从已保存进度继续</Button>}{agent?.enabled && <Tag>有限补查 · 已读 {agent.usage?.reads || 0}/{agent.max_reads} 次 · 模型 {agent.usage?.calls || 0}/{agent.max_calls} 次</Tag>}</Space>
    {status === "paused" && <Alert type="info" title="已停止整理" description="正在进行的请求可能仍会结束，但停止后的返回结果不会应用；已确认画像不受影响。" />}
    {!!agent?.steps?.length && <Collapse ghost items={[{ key: "steps", label: "查看资料补查过程", children: agent.steps.map(step => <p key={step.sequence}>{step.sequence}. {step.label} · {step.status === "completed" ? "完成" : step.status === "failed" ? "未完成" : status === "running" ? "处理中" : "已中断"}{step.detail ? ` — ${step.detail}` : ""}</p>) }]} />}
  </div>;
}
