"use client";

import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Empty, Skeleton, Space, Tag } from "antd";
import { api } from "@/lib/api";

type OpportunityMatch = { policy_id: string; title: string; level_label: string; reasons: string[]; gaps: string[]; recommendation_label: string; conditions: { label: string; opportunities: { checks: { status: string; profile_field: string; reason: string }[] }[] } };
export default function OpportunityPreview({ profileId, revision, openPolicy, supplement }: { profileId: string; revision: number; openPolicy: (id: string) => void; supplement: (fields: string[]) => void }) {
  const query = useQuery({ queryKey: ["initial-opportunities", profileId, revision], queryFn: () => api<{ items: OpportunityMatch[]; count: number }>(`enterprises/${profileId}/matches?view=opportunities`) });
  const prompts = [...new Set((query.data?.items || []).flatMap(item => item.conditions.opportunities.flatMap(opportunity => opportunity.checks.filter(check => check.status === "unknown").map(check => check.profile_field))).filter(field => field && !field.startsWith("project.")))].slice(0, 2);
  return <section><h3>先看看这些政策机会</h3><p className="muted">先看相关性与待核对条件，资料可以按需补充。</p>{query.isLoading ? <Skeleton active /> : query.error ? <Alert type="warning" title={query.error.message} action={<Button onClick={() => query.refetch()}>重试</Button>} /> : query.data?.items.length ? query.data.items.slice(0, 5).map(item => <Card key={item.policy_id} size="small" className="space-bottom" title={item.title}><Space wrap><Tag>{item.level_label}</Tag><Tag>{item.conditions.label}</Tag><Tag>{item.recommendation_label}</Tag></Space><p>{item.reasons.join("；")}</p>{item.gaps.slice(0, 2).map(gap => <p className="small muted" key={gap}>{gap}</p>)}<Button onClick={() => openPolicy(item.policy_id)}>查看全文与附件</Button></Card>) : <Empty description="当前没有可展示的申报机会，仍可搜索全部正式政策，或补充业务方向。" />}
    {!!prompts.length && <Button onClick={() => supplement(prompts)}>按需补充 {prompts.length} 项关键信息</Button>}
  </section>;
}
