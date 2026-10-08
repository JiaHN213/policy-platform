"use client";
import ReadableText from "@/components/policy/ReadableText";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Drawer, Space } from "antd";
import { api } from "@/lib/api";
import { usePolicyWorkspace } from "@/components/workspace/WorkspaceShell";
import MatchConditions, { CheckList, type Conditions, type ConditionCheck } from "./MatchConditions";
type Results = { items: { id: string; policy_id: string; title: string }[]; gap_fill: { fields: { label: string }[]; can_open: boolean; run_id: string | null }; notice: string };
type Analysis = { result: { policy_id: string; notice: string; points?: { text: string; quote: string }[]; conditions?: Conditions; additional_conditions?: ConditionCheck[] } };
export default function MatchingResults({ id, onOpenDraft }: { id: string; onOpenDraft: (id: string) => void }) {
  const { openPolicy } = usePolicyWorkspace();
  const [selected, setSelected] = useState<string | null>(null);
  const result = useQuery({ queryKey: ["matching-results", id], queryFn: () => api<{ result: Results }>(`enterprise-research/${id}`), refetchInterval: 15000 });
  const detail = useQuery({ queryKey: ["matching-analysis", selected], queryFn: () => api<Analysis>(`enterprise-research/${selected}`), enabled: !!selected });
  if (result.error) return <Alert type="warning" title={result.error.message} />;
  const value = result.data?.result;
  return <>
    {!!value?.gap_fill?.fields.length && <Card size="small" title="可补充的资料" className="space-bottom"><p>{value.gap_fill.fields.map(field => field.label).join("、")}</p>{value.gap_fill.can_open && value.gap_fill.run_id ? <Button onClick={() => onOpenDraft(value.gap_fill.run_id!)}>核对资料建议</Button> : <p>可在企业与项目中补充你了解的信息。</p>}</Card>}
    {value?.items?.map(item => <Card size="small" key={item.id} title={item.title} className="space-bottom"><Space><Button type="primary" onClick={() => setSelected(item.id)}>查看适用分析</Button><Button onClick={() => openPolicy(item.policy_id)}>查看政策</Button></Space></Card>)}
    {!value?.items?.length && <p className="muted">暂无可展示的补充分析，你仍可查看相关政策及原文依据。</p>}
    <p className="small muted">{value?.notice}</p>
    <Drawer title="政策适用分析" open={!!selected} onClose={() => setSelected(null)} size="large" loading={detail.isLoading} destroyOnHidden>
      {detail.error && <Alert type="warning" title={detail.error.message} />}
      {!detail.error && detail.data && <><p>{detail.data.result.notice}</p>{detail.data.result.points?.map((point, i) => <section className="reading-evidence" key={i}><ReadableText text={point.text} /><blockquote className="reading-quote"><span className="reading-caption">原文依据</span><ReadableText text={point.quote} previewChars={320} expandLabel="展开原文依据" /></blockquote></section>)}{detail.data.result.conditions && <MatchConditions conditions={detail.data.result.conditions} openPolicy={() => openPolicy(detail.data!.result.policy_id)} />}{!!detail.data.result.additional_conditions?.length && <CheckList checks={detail.data.result.additional_conditions} />}<Button onClick={() => openPolicy(detail.data!.result.policy_id)}>查看全文与附件</Button></>}
    </Drawer>
  </>;
}
