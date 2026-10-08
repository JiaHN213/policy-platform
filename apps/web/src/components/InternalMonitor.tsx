"use client";
import { useRouter } from "next/navigation";
import AgentTasks from "./customer/AgentTasks";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Select, Space, Table, Tabs, Tag } from "antd";
import { api } from "@/lib/api";
import TaskDashboard, { type DashboardData } from "./customer/TaskDashboard";
import WorkerStatus from "./WorkerStatus";
import OperationsPanel from "./OperationsPanel";
import SourceCoverage from "./customer/SourceCoverage";

type Row = { id: string; kind_label: string; status_label: string; created_at: string; finished_at: string | null; request_count: number; message: string };
type InternalDashboard = DashboardData & { quota_statistics: { normal: number; failed: number; maintenance: number; maintenance_failed: number } };
function TaskMonitor() {
  const [days, setDays] = useState(7);
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const summary = useQuery({ queryKey: ["internal-task-summary", days, kind], queryFn: () => api<InternalDashboard>(`admin/task-monitor/dashboard?${new URLSearchParams({ days: String(days), kind })}`), refetchInterval: 15000 });
  const tasks = useQuery({ queryKey: ["internal-task-rows", days, kind, status, page], queryFn: () => api<{ items: Row[]; count: number }>(`admin/task-monitor?${new URLSearchParams({ days: String(days), kind, status, page: String(page) })}`), refetchInterval: 15000 });
  return <><Space wrap><Select aria-label="统计时间" value={days} onChange={value => { setDays(value); setPage(1); }} options={[1, 7, 30, 90].map(value => ({ value, label: value === 1 ? "今天" : `最近 ${value} 天` }))} /><Select aria-label="任务类型" style={{ minWidth: 180 }} value={kind} onChange={value => { setKind(value); setPage(1); }} options={[{ value: "", label: "全部任务" }, { value: "company", label: "企业资料" }, { value: "project", label: "项目资料" }, { value: "workflow", label: "自动匹配" }, { value: "explanation", label: "政策分析" }]} /><Button onClick={() => { void summary.refetch(); void tasks.refetch(); }}>刷新</Button></Space>
    <p className="small muted">全平台脱敏运行指标；不展示企业名称、私有材料、模型原始输出或个人身份。统计按主任务创建日期与类型选择，状态只筛选明细。</p>
    {(summary.error || tasks.error) && <Alert type="error" title={(summary.error || tasks.error)?.message} />}
    {summary.data?.quota_statistics && <Space wrap className="space-bottom">
      <Tag>所选范围：正常计次 {summary.data.quota_statistics.normal} 项</Tag>
      <Tag>失败不计次 {summary.data.quota_statistics.failed} 项</Tag>
      <Tag color="blue">维护重跑 {summary.data.quota_statistics.maintenance} 项（含失败 {summary.data.quota_statistics.maintenance_failed} 项），不计次</Tag>
    </Space>}
    {summary.data && <TaskDashboard data={summary.data} status={status} onStatus={value => { setStatus(value); setPage(1); }} />}
    <h3>脱敏任务记录</h3><Table rowKey="id" dataSource={tasks.data?.items} loading={tasks.isLoading} scroll={{ x: 720 }} pagination={{ current: page, pageSize: 20, total: tasks.data?.count || 0, showSizeChanger: false, onChange: setPage }} columns={[
      { title: "任务标识", dataIndex: "id", render: value => <span title={value}>{value.slice(0, 8)}</span> }, { title: "类型", dataIndex: "kind_label" }, { title: "状态", dataIndex: "status_label", render: value => <Tag>{value}</Tag> }, { title: "创建时间", dataIndex: "created_at", render: value => new Date(value).toLocaleString("zh-CN") }, { title: "请求次数", dataIndex: "request_count" }, { title: "说明", dataIndex: "message" },
    ]} />
  </>;
}
export default function InternalMonitor() {
  const router = useRouter();
  return <Tabs items={[
    { key: "tasks", label: "任务运行看板", children: <TaskMonitor /> },
    { key: "authorized", label: "本人任务处理", children: <><p className="muted">仅可操作当前账号发起且仍有资料访问权限的任务，系统管理权限不授予客户私有资料访问权。</p><AgentTasks onOpen={id => router.push(`/enterprise?result=${id}`)} /></> },
    { key: "runtime", label: "后台运行状态", children: <WorkerStatus /> },
    { key: "usage", label: "模型用量", children: <OperationsPanel kind="usage" /> },
    { key: "pipeline", label: "全过程追踪", children: <OperationsPanel kind="pipeline" /> },
    { key: "sources", label: "来源更新情况", children: <SourceCoverage /> },
  ]} />;
}
