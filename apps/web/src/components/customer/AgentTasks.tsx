"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Card, Drawer, Select, Space, Table, Tag, Timeline } from "antd";
import { api } from "@/lib/api";
import MatchingWorkflow from "./MatchingWorkflow";
import { explainSystemText } from "@/lib/system-messages";
import TaskDashboard, { type DashboardData, duration } from "./TaskDashboard";

type Step = { id: string; sequence: number; label: string; status: string; detail: string; created_at: string; finished_at: string | null };
type Request = { id: string; step_id: string | null; model: string; status: string; duration_ms: number; input_tokens: number | null; output_tokens: number | null; cost: string | null; currency: string; created_at: string };
type Task = { kind: string; id: string; subject: string; kind_label: string; status: string; status_label: string; stage: string; message: string; created_at: string; finished_at: string | null; elapsed_seconds: number; retry_at: string | null; completed_steps: number; recorded_steps: number; request_count: number; can_stop: boolean; can_resume: boolean; can_open: boolean; resume_label: string; steps?: Step[]; requests?: Request[] };
const date = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—";

export default function AgentTasks({ onOpen }: { onOpen: (id: string) => void }) {
  const [page, setPage] = useState(1);
  const [kind, setKind] = useState("");
  const [days, setDays] = useState(7);
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const client = useQueryClient();
  const { message } = App.useApp();
  const dashboard = useQuery({ queryKey: ["agent-dashboard", days, kind], queryFn: () => api<DashboardData>(`enterprise-research/dashboard?${new URLSearchParams({ days: String(days), kind })}`), refetchInterval: 15000 });
  const tasks = useQuery({ queryKey: ["agent-tasks", page, kind, status, days], queryFn: () => api<{ items: Task[]; count: number }>(`enterprise-research/tasks?${new URLSearchParams({ page: String(page), kind, status, days: String(days) })}`), refetchInterval: 5000 });
  const detail = useQuery({ queryKey: ["agent-task", selected], queryFn: () => api<Task>(`enterprise-research/${selected}/task`), enabled: !!selected, refetchInterval: selected ? 5000 : false });
  const control = useMutation({ mutationFn: ({ id, action }: { id: string; action: "stop" | "resume" }) => api(`enterprise-research/${id}/${action}`, { method: "POST" }),
    onSuccess: () => {
      for (const key of ["agent-dashboard", "agent-tasks", "agent-task", "enterprise-run", "enterprise-recent"]) void client.invalidateQueries({ queryKey: [key] });
      message.success("任务状态已更新");
    }, onError: (error: Error) => message.error(error.message) });
  const actions = (task: Task) => <Space wrap>
    {task.can_stop && <Button loading={control.isPending} onClick={() => control.mutate({ id: task.id, action: "stop" })}>停止任务</Button>}
    {task.can_resume && <Button loading={control.isPending} onClick={() => control.mutate({ id: task.id, action: "resume" })}>{task.resume_label}</Button>}
    {task.can_open && <Button onClick={() => { setSelected(null); onOpen(task.id); window.scrollTo({ top: 0, behavior: "smooth" }); }}>查看结果</Button>}
  </Space>;
  return <Card title="任务运行看板" extra={<Button loading={dashboard.isFetching || tasks.isFetching} onClick={() => { void tasks.refetch(); void dashboard.refetch(); }}>刷新</Button>}>
    <p className="muted">仅统计本人仍可访问的企业 AI 主任务；模型调用与复用包含所属子任务。完成不代表画像已确认或资格已核实。</p>
    <Space wrap className="space-bottom">
      <Select aria-label="统计时间范围" value={days} style={{ width: 150 }} onChange={value => { setDays(value); setPage(1); }} options={[{ value: 1, label: "今天" }, { value: 7, label: "最近 7 天" }, { value: 30, label: "最近 30 天" }, { value: 90, label: "最近 90 天" }]} />
      <Select aria-label="任务类型" value={kind} style={{ width: 180 }} onChange={value => { setKind(value); setPage(1); }} options={[{ value: "", label: "全部任务类型" }, { value: "workflow", label: "自动匹配与解读" }, { value: "company", label: "企业资料整理" }, { value: "project", label: "项目资料整理" }, { value: "explanation", label: "政策匹配解读" }]} />
      <Select aria-label="任务执行状态" value={status} style={{ width: 160 }} onChange={value => { setStatus(value); setPage(1); }} options={[{ value: "", label: "全部执行状态" }, { value: "queued", label: "等待处理" }, { value: "running", label: "正在处理" }, { value: "waiting", label: "正在协作处理" }, { value: "completed", label: "已完成" }, { value: "paused", label: "已停止" }, { value: "failed", label: "处理失败" }]} />
    </Space>
    <p className="small muted">时间与类型筛选作用于整个看板；状态只筛选下方列表。按主任务创建日期统计，调用包含这些任务截至当前的已记录请求。{dashboard.data ? ` 更新于 ${date(dashboard.data.generated_at)}` : ""}</p>
    {dashboard.error && <Alert type="error" title={dashboard.error.message} />}
    {dashboard.isLoading && <Card loading />}
    {dashboard.data && !dashboard.error && <TaskDashboard data={dashboard.data} status={status} onStatus={value => { setStatus(value); setPage(1); }} />}
    <h3>任务明细</h3>
    {tasks.error && <Alert type="error" title={tasks.error.message} />}
    <Table rowKey="id" loading={tasks.isLoading} dataSource={tasks.data?.items || []} scroll={{ x: 900 }} pagination={{ current: page, pageSize: 20, total: tasks.data?.count || 0, showSizeChanger: false, onChange: setPage }} columns={[
      { title: "任务", render: (_, task) => <><strong>{task.kind_label}</strong><div className="muted">{task.subject}</div><div className="small muted">{date(task.created_at)}</div></> },
      { title: "状态与进度", render: (_, task) => <><Tag>{task.status_label}</Tag><p>{task.stage}</p><span className="small muted">已完成 {task.completed_steps} 个步骤 · 累计记录 {task.recorded_steps} 个（含重试）</span></> },
      { title: "耗时与调用", render: (_, task) => <><div>{duration(task.elapsed_seconds)}</div><div className="small muted">{task.request_count} 次请求 · 含排队</div>{task.status === "queued" && task.retry_at && <div className="small muted">下次重试 {date(task.retry_at)}</div>}</> },
      { title: "操作", render: (_, task) => <Space orientation="vertical"><Button onClick={() => setSelected(task.id)}>查看任务过程</Button>{actions(task)}</Space> },
    ]} />
    <Drawer title="AI 任务过程" open={!!selected} onClose={() => setSelected(null)} size="min(980px, 96vw)" loading={detail.isLoading} destroyOnHidden>
      {detail.error && <Alert type="error" title={detail.error.message} />}
      {detail.data && !detail.error && <>
        <h3>{detail.data.kind_label} · {detail.data.subject}</h3>
        <p><Tag>{detail.data.status_label}</Tag>{detail.data.stage}</p>
        <p className="muted">创建于 {date(detail.data.created_at)} · 总耗时 {duration(detail.data.elapsed_seconds)}（含排队与重试）</p>
        {detail.data.message && <Alert type="warning" title={explainSystemText(detail.data.message)} />}
        {actions(detail.data)}
        <p className="muted">停止后正在进行的请求可能仍会结束，但返回结果不会应用。支持检查点的任务从保存进度继续，其余任务重新执行并保留原记录；重试可能产生新的模型费用。</p>
        {detail.data.kind === "workflow" && <MatchingWorkflow id={detail.data.id} onOpenDraft={id => { setSelected(null); onOpen(id); }} />}
        <h4>执行步骤</h4>
        {!detail.data.steps?.length && <p className="muted">暂无可展示的步骤；历史缺失记录不补造，依据已变化的任务不展示旧内容。</p>}
        <Timeline items={detail.data.steps?.map(step => ({ color: step.status === "completed" ? "green" : step.status === "running" ? "blue" : "gray", content: <><strong>{step.sequence}. {step.label}</strong><p>{{ completed: "完成", running: "处理中", failed: "未完成", interrupted: "已中断" }[step.status] || "待核对"} · {date(step.created_at)}{step.finished_at ? ` → ${date(step.finished_at)}` : ""}</p>{step.detail && <p>{explainSystemText(step.detail)}</p>}</> }))} />
        <h4>模型请求记录</h4>
        <p className="muted">仅记录接入后的请求，Token 未返回时显示未知，未设置单价不估算费用。收到响应不等于引用校验通过；统计不是供应商账单。</p>
        <Table rowKey="id" size="small" dataSource={detail.data.requests || []} scroll={{ x: 750 }} columns={[
          { title: "步骤", render: (_, call) => detail.data?.steps?.find(step => step.id === call.step_id)?.label || (detail.data?.kind === "workflow" ? "政策分析子任务" : "任务级调用") },
          { title: "模型", dataIndex: "model" }, { title: "状态", render: (_, call) => ({ received: "收到响应", timeout: "超时", failed: "请求失败" }[call.status] || "待核对") },
          { title: "耗时", render: (_, call) => `${(call.duration_ms / 1000).toFixed(1)} 秒` },
          { title: "输入／输出 Token", render: (_, call) => `${call.input_tokens ?? "未知"} / ${call.output_tokens ?? "未知"}` },
          { title: "估算费用", render: (_, call) => call.cost === null ? "未估算" : `${call.cost} ${call.currency}` },
        ]} />
      </>}
    </Drawer>
  </Card>;
}
