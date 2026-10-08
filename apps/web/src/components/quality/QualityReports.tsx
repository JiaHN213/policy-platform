"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Select, Space, Table, Tag } from "antd";
import { api, type Page } from "@/lib/api";
import { kinds, percentage, verdictLabel, type Kind, type Result, type Run } from "./types";

export default function QualityReports({ runs, selected, onSelect, onOpen }: { runs: Run[]; selected: string | null; onSelect: (id: string) => void; onOpen: (id: string) => void }) {
  const [scope, setScope] = useState("errors");
  const [page, setPage] = useState(1);
  const run = runs.find(item => item.id === selected) || runs[0];
  const results = useQuery({ queryKey: ["quality-results", run?.id, scope, page], queryFn: () => api<Page<Result>>(`admin/quality/runs/${run!.id}/results?errors_only=${scope === "errors"}&page=${page}`), enabled: !!run });
  return <>
    <Select className="space-bottom" aria-label="历史评测报告" placeholder="选择历史报告" style={{ width: "min(100%, 360px)" }} value={run?.id} options={runs.map(item => ({ value: item.id, label: new Date(item.created_at).toLocaleString("zh-CN") }))} onChange={id => { onSelect(id); setPage(1); }} />
    {(!run || run.status === "no_labels") && <Alert className="space-bottom" type="info" title="暂无有效比较结果" description="请先完成判断。有意见但没有得分时，查看是否缺少系统结果或材料已经更新。" />}
    <div className="quality-report-grid">{kinds.map(item => {
      const m = run?.metrics[item.value as Kind];
      return <Card key={item.value} title={item.label} size="small">
        <p className="muted">有效比较 {m?.evaluated || 0} 份</p><strong className="quality-report-score">{percentage(m?.accuracy)}</strong><p>系统与人工判断一致</p>
        <Space wrap><Tag color="green">一致 {m?.correct || 0}</Tag><Tag color="orange">不一致 {(m?.evaluated || 0) - (m?.correct || 0)}</Tag></Space>
        <p>漏判 {m?.fn || 0} · 误判 {m?.fp || 0}</p>
        <details><summary>这些数字是什么意思？</summary>
          <p>漏判：人工认为有机会、关系或依据，但系统没识别。误判：系统认为有，人工认为没有。机会的漏判与误判针对“明确的政策机会”；支持线索另计入整体一致性。</p>
          <p>系统判为有的结果中，有多少正确：{percentage(m?.precision)}（精确率）</p>
          <p>人工确认有的内容，系统找到了多少：{percentage(m?.recall)}（召回率）</p>
          <p>引用能在原文找到：{percentage(m?.citation_grounding)}</p>
          <p>人工核对后，系统依据足以支持结论：{percentage(m?.human_evidence_support)}</p>
          <p>材料已更新 {m?.stale || 0} · 无可比较的系统结果 {m?.unavailable || 0}</p>
        </details>
      </Card>;
    })}</div>
    <p className="muted">报告只代表本次有效样本，不代表全库准确率；无法判断和材料已变化的样本不计分。修改意见后需重新生成报告，历史报告保留原结果。</p>
    <Select className="space-bottom" aria-label="报告结果范围" value={scope} options={[{ value: "errors", label: "只看不一致与无法比较的记录" }, { value: "all", label: "全部记录" }]} onChange={value => { setScope(value); setPage(1); }} style={{ width: 290 }} />
    {results.error && <Alert type="error" title={results.error.message} action={<Button onClick={() => void results.refetch()}>重试</Button>} />}
    <Table<Result> rowKey="id" loading={results.isLoading && !!run} dataSource={results.data?.items || []} scroll={{ x: 850 }} pagination={{ current: page, pageSize: 20, total: results.data?.count, showSizeChanger: false, onChange: setPage }} columns={[
      { title: "文件", dataIndex: "sample_title", render: (value, row) => <Button type="link" className="quality-title-link" onClick={() => onOpen(row.sample)}>{value}</Button> },
      { title: "人工判断", render: (_, row) => verdictLabel(row.kind, row.kind === "opportunity" ? row.gold.opportunity_level : row.gold.verdict) },
      { title: "系统判断", render: (_, row) => verdictLabel(row.kind, row.prediction.value) },
      { title: "比较结果", render: (_, row) => row.reason || (row.correct === true ? "判断一致" : row.correct === false ? "判断不一致" : "暂时无法比较") },
    ]} />
  </>;
}
