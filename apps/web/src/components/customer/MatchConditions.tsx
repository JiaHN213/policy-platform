"use client";

import ReadableText from "@/components/policy/ReadableText";
import { Button, Collapse, Space, Tag } from "antd";

export type ConditionCheck = { condition: string; status: string; label: string; reason: string; quote: string; profile_field?: string; source?: { source_name: string; start_offset: number } | null };
export type Conditions = { status: string; label: string; notice: string; opportunities: { opportunity_id: string; title: string; label: string; checks: ConditionCheck[]; gaps: string[] }[] };

export function CheckList({ checks }: { checks: ConditionCheck[] }) {
  return <>{checks.map((check, index) => <div key={index} className="condition-check"><Space wrap><strong>{check.condition}</strong><Tag color={check.status === "conflict" ? "red" : check.status === "consistent" ? "green" : "orange"}>{check.label}</Tag></Space><ReadableText text={check.reason} />{check.quote && <blockquote className="reading-quote"><span className="reading-caption">原文依据</span><ReadableText text={check.quote} previewChars={320} expandLabel="展开原文依据" /></blockquote>}{check.source && <p className="small muted">依据：{check.source.source_name} · 原文字符位置 {check.source.start_offset + 1}</p>}</div>)}</>;
}

export default function MatchConditions({ conditions, supplement, openPolicy }: { conditions: Conditions; supplement?: (fields: string[]) => void; openPolicy: () => void }) {
  const fields = [...new Set(conditions.opportunities.flatMap(o => o.checks.filter(c => c.status === "unknown" && c.profile_field).map(c => c.profile_field!)))].slice(0, 2);
  return <><Collapse ghost items={[{ key: "conditions", label: "查看逐项核对与原文依据", children: <><p className="small muted">{conditions.notice}</p>{conditions.opportunities.map(opportunity => <div key={opportunity.opportunity_id}><h4>{opportunity.title} · {opportunity.label}</h4><CheckList checks={opportunity.checks} />{opportunity.gaps.map(gap => <p className="small muted" key={gap}>{gap}</p>)}</div>)}<Button onClick={openPolicy}>查看政策全文与附件</Button></> }]} />{supplement && fields.length > 0 && <Button className="space-bottom" onClick={() => supplement(fields)}>补充影响判断的信息（最多 {fields.length} 项）</Button>}</>;
}
