"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Card, Drawer, Space, Spin, Tag } from "antd";
import { api } from "@/lib/api";
import { explainSystemText } from "@/lib/system-messages";
import { usePolicyWorkspace } from "@/components/workspace/WorkspaceShell";
import MatchConditions, { CheckList, type ConditionCheck, type Conditions } from "./MatchConditions";

type Workflow = { gap_fill: { fields: { field: string; label: string; researchable: boolean }[]; note: string; run_id: string | null; status: string; can_open: boolean }; reused_count: number; concurrency: number; items: { id: string; policy_id: string | null; title: string; status: string; stage: string; can_open: boolean }[]; selected_count: number; completed_count: number; candidate_count: number; retrieved: boolean; calls: number; max_calls: number; notice: string };
type Task = { status: string; stage: string; message: string; can_stop: boolean; can_resume: boolean; workflow: Workflow };
type Analysis = { result: { policy_id: string; notice: string; points: { text: string; quote: string; profile_facts: Record<string, unknown> }[]; conditions?: Conditions; additional_conditions?: ConditionCheck[] } };

export default function MatchingWorkflow({ id, onOpenDraft }: { id: string; onOpenDraft?: (id: string) => void }) {
  const client = useQueryClient();
  const { message } = App.useApp();
  const { openPolicy } = usePolicyWorkspace();
  const [selected, setSelected] = useState<string | null>(null);
  const task = useQuery({ queryKey: ["agent-task", id], queryFn: () => api<Task>(`enterprise-research/${id}/task`), refetchInterval: 3000 });
  const analysis = useQuery({ queryKey: ["workflow-analysis", selected], queryFn: () => api<Analysis>(`enterprise-research/${selected}`), enabled: !!selected, refetchInterval: selected ? 10000 : false });
  const control = useMutation({ mutationFn: (action: "stop" | "resume") => api(`enterprise-research/${id}/${action}`, { method: "POST" }), onSuccess: () => {
    for (const key of ["agent-task", "agent-tasks", "enterprise-run"]) void client.invalidateQueries({ queryKey: [key] });
  }, onError: (error: Error) => message.error(error.message) });
  if (task.isLoading) return <Spin />;
  if (task.error) return <Alert type="error" title={task.error.message} />;
  if (!task.data?.workflow) return null;
  const flow = task.data.workflow;
  const result = analysis.data?.result;
  return <>
    <Space wrap className="space-bottom"><strong>{task.data.stage}</strong>
      {task.data.can_stop && <Button danger loading={control.isPending} onClick={() => control.mutate("stop")}>停止自动匹配</Button>}
      {task.data.can_resume && <Button loading={control.isPending} onClick={() => control.mutate("resume")}>继续未完成的分析</Button>}
    </Space>
    {task.data.message && <Alert type="warning" title={explainSystemText(task.data.message)} />}
    <p className="muted">{flow.notice}</p>
    {!!flow.gap_fill?.fields.length && <Card title="资料缺口与自动补查" size="small" className="space-bottom"><p>{flow.gap_fill.fields.map(field => `${field.label}${field.researchable ? "" : "（需企业补充）"}`).join("；")}</p><p className="muted">{explainSystemText(flow.gap_fill.note)}</p>{flow.gap_fill.can_open && flow.gap_fill.run_id && onOpenDraft ? <Button type="primary" onClick={() => onOpenDraft(flow.gap_fill.run_id!)}>核对补查建议</Button> : <p className="small muted">暂无可确认的建议时，可在企业画像或拟申报项目中补充资料。未确认的信息不会用于匹配。</p>}</Card>}
    {flow.retrieved && <p>检索候选 {flow.candidate_count} 份 · 本次选择 {flow.selected_count} 份 · 当前有效解读 {flow.completed_count} 份 · 模型请求 {flow.calls}/{flow.max_calls} 次 · 复用有效结果 {flow.reused_count} 份 · 最大并行 {flow.concurrency} 份</p>}
    {flow.retrieved && !flow.items.length && <Alert type="info" title="暂无适合自动解读的政策" description="可在政策匹配中查看信息不足的线索，或补充企业／项目业务领域；不会自动放宽你的筛选条件。" />}
    {flow.items.map(item => <Card key={item.id} size="small" className="space-bottom">
      <Space wrap><strong>{item.title}</strong><Tag>{({ pending: "等待前一步", queued: "等待解读", running: "正在解读", completed: "处理完成", paused: "已停止", failed: "未完成" } as Record<string, string>)[item.status] || "待核对"}</Tag></Space>
      <p className="small muted">{explainSystemText(item.stage)}</p>
      <Space wrap>{item.can_open && <Button onClick={() => setSelected(item.id)}>查看分析依据</Button>}{item.policy_id && <Button onClick={() => openPolicy(item.policy_id!)}>查看政策全文</Button>}</Space>
    </Card>)}
    <Drawer title="政策匹配分析依据" open={!!selected} onClose={() => setSelected(null)} size="large" destroyOnHidden loading={analysis.isLoading}>
      {analysis.error && <Alert type="warning" title={analysis.error.message} />}
      {!analysis.error && result && <>
        <p className="muted">{result.notice}</p>
        {result.points?.map((point, index) => <div key={index}><p>{point.text}</p><blockquote>{point.quote}</blockquote><p className="small muted">使用的画像信息：{Object.values(point.profile_facts).map(value => Array.isArray(value) ? value.join("、") : String(value)).join("；")}</p></div>)}
        {result.conditions && <MatchConditions conditions={result.conditions} openPolicy={() => openPolicy(result.policy_id)} />}
        {!!result.additional_conditions?.length && <><h4>补充发现的待核对条款</h4><CheckList checks={result.additional_conditions} /></>}
        <Button onClick={() => openPolicy(result.policy_id)}>查看政策全文与附件</Button>
      </>}
    </Drawer>
  </>;
}
