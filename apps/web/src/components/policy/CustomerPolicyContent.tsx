"use client";

import PolicyBody from "@/components/policy/PolicyBody";

import Link from "next/link";
import { Alert, Button, Collapse, Space, Tabs, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { api, safeExternalUrl, type PolicyDetail } from "@/lib/api";
import { AttachmentLinks, ProvenanceTags, documentTypeLabel } from "./common";
import ReadableText from "./ReadableText";

export default function CustomerPolicyContent({ policy, compact = false, onSelect, onRead }: { policy: PolicyDetail; compact?: boolean; onSelect: (id: string) => void; onRead?: () => void }) {
  const taxonomy = useQuery({ queryKey: ["taxonomy"], queryFn: () => api<Record<string, { value: string; label: string }[]>>("taxonomies") });
  const label = (group: string, value?: string) => taxonomy.data?.[group]?.find(item => item.value === value)?.label || "待核实";
  const textList = (value: unknown): string[] => Array.isArray(value) ? value.map(String) : [];
  const opportunities = <div className="policy-opportunities">{policy.opportunities.length ? policy.opportunities.map(item => <section className="policy-opportunity" key={item.id}>
    <header className="policy-opportunity-heading"><h4>{item.title}</h4><Tag>{label("opportunity_statuses", item.status)}</Tag></header>
    <div className="policy-reading-field"><h5>支持内容</h5><ReadableText text={item.support_content || "支持内容待核实，请核对官方原文。"} previewChars={compact ? 360 : undefined} expandLabel="展开支持内容" /></div>
    {!!textList(item.eligible_subjects).length && <div className="policy-reading-field"><h5>适用对象</h5><ReadableText text={textList(item.eligible_subjects).join("、")} /></div>}
    {!!textList(item.requirements).length && <div className="policy-reading-field"><h5>申报条件</h5><ul className="reading-list">{textList(item.requirements).map((value, i) => <li key={i}>{value}</li>)}</ul></div>}
    {!!item.batches?.length && <div className="policy-reading-field"><h5>申报批次与时间</h5><div className="policy-batches">{item.batches.map(batch => <div className="policy-batch" key={batch.id}>
      <strong>{batch.name || "申报批次"}</strong><Tag>{label("opportunity_statuses", batch.status)}</Tag>
      <p>{batch.deadline_at ? `截止日期：${new Date(batch.deadline_at).toLocaleDateString("zh-CN")}` : "截止日期未明确，请核对通知"}</p>
    </div>)}</div></div>}
  </section>) : <p className="reading-empty">此文件未提取到明确申报机会，可阅读全文了解政策内容。</p>}</div>;

  return <article className={`customer-policy-content ${compact ? "is-compact" : "is-full"}`}>
    <header className="policy-reading-header">
      <h2 className="detail-title">{policy.title}</h2>
      <dl className="policy-reading-meta">
        <div><dt>发文机关</dt><dd>{policy.issuer || "待核实"}</dd></div>
        <div><dt>发布日期</dt><dd>{policy.publication_date || "待核实"}</dd></div>
        {policy.document_number && <div className="policy-meta-wide"><dt>发文字号</dt><dd>{policy.document_number}</dd></div>}
      </dl>
      <Space wrap className="policy-reading-tags"><Tag>{documentTypeLabel(policy.document_type)}</Tag><Tag>效力：{label("validity_statuses", policy.validity_status)}</Tag><ProvenanceTags policy={policy} /></Space>
      <div className="policy-reading-actions">
        {compact && <Link className="policy-detail-link" href={`/policies/${policy.id}`} onClick={onRead}>阅读全文与附件 <span aria-hidden="true">→</span></Link>}
        <Button href={safeExternalUrl(policy.source_url)} target="_blank" rel="noopener noreferrer">官方原文</Button>
      </div>
    </header>
    {policy.document_type === "draft" && <Alert type="warning" showIcon title="征求意见稿，尚不能作为已生效政策使用" className="space-bottom" />}
    {policy.evidence_readiness?.status === "incomplete" && <Alert type="warning" showIcon title="关键条件待核对" description={String(policy.evidence_readiness.reason)} className="space-bottom" />}
    <section className="policy-summary-panel">
      <h3>政策摘要</h3><ReadableText text={policy.summary || "暂无摘要，请查看正文。"} previewChars={compact ? 420 : undefined} expandLabel="展开完整摘要" collapseLabel="收起摘要" />
    </section>
    {compact ? <section className="policy-reading-section"><h3>支持与条件</h3>{opportunities}</section> : <Tabs defaultActiveKey="body" className="policy-reading-tabs" items={[
      { key: "body", label: "全文与附件", children: <><AttachmentLinks policy={policy} /><PolicyBody text={policy.body || "正文暂不可用，请查看官方原文与附件。"} /></> },
      { key: "opportunities", label: "支持与条件", children: opportunities },
      { key: "relations", label: "关联政策", children: policy.relations.length ? policy.relations.map(relation => <section className="policy-opportunity" key={relation.id}><div className="policy-relation-pair"><p>{relation.from_title}</p><Tag>{label("relation_kinds", relation.kind)} ↓</Tag><p>{relation.to_title}</p></div><Button onClick={() => onSelect(relation.from_policy === policy.id ? relation.to_policy : relation.from_policy)}>查看关联文件</Button><Collapse ghost items={[{ key: "evidence", label: "查看关系依据", children: <blockquote className="reading-quote"><ReadableText text={relation.evidence_quote} /></blockquote> }]} /></section>) : <p className="reading-empty">暂无已核验的关联政策。</p> },
      { key: "sources", label: "来源与摘要依据", children: <>{policy.sources?.map(source => <p className="policy-source-link" key={source.id}><Tag>{source.role_label}</Tag><a href={safeExternalUrl(source.resolved_url || source.url)} target="_blank" rel="noreferrer">{source.publisher || "官方来源"}</a></p>)}{(policy.summary_evidence as { text: string; quote: string }[] || []).map((point, i) => <section className="reading-evidence" key={i}><ReadableText text={point.text} /><blockquote className="reading-quote"><span className="reading-caption">原文依据</span><ReadableText text={point.quote} previewChars={400} expandLabel="展开原文依据" /></blockquote></section>)}</> },
    ]} />}
  </article>;
}
