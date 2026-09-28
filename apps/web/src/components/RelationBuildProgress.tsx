"use client";

import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Progress, Skeleton } from "antd";
import { api } from "@/lib/api";

export type ReviewScope = "current" | "historical" | "all";
type ReviewCounts = Record<"pending" | "approved" | "rejected" | "superseded", number>;
type RelationProgress = {
  status: string;
  status_label: string;
  policy_count: number;
  total: number;
  completed: number;
  waiting: number;
  failed: number;
  percent: number;
  no_relation_pairs: number;
  verified_relations: number;
  review: Record<ReviewScope, ReviewCounts>;
  last_scan_at: string | null;
  as_of: string;
};

export function useRelationProgress(enabled = true) {
  return useQuery({
    queryKey: ["relation-build-progress"],
    queryFn: () => api<RelationProgress>("admin/knowledge/builds/progress"),
    enabled,
    refetchInterval: enabled ? 10_000 : false,
    staleTime: 5_000,
  });
}

export default function RelationBuildProgress({ onReviewScope }: {
  onReviewScope?: (scope: ReviewScope) => void;
}) {
  const query = useRelationProgress();
  const progress = query.data;
  if (query.isPending) return <Skeleton active paragraph={{ rows: 2 }} />;
  if (!progress) return <Alert type="warning" showIcon title="暂时无法读取关系构建进度" action={<Button onClick={() => void query.refetch()}>重试</Button>} />;
  const statistics = [
    { label: "已分析", value: progress.completed, unit: "对" },
    { label: "待分析", value: progress.waiting, unit: "对" },
    { label: "失败待重试", value: progress.failed, unit: "对" },
    { label: "已确认关系（全库）", value: progress.verified_relations, unit: "条" },
    { label: "当前待复核", value: progress.review.current.pending, unit: "条", scope: "current" as const },
    { label: "历史待复核", value: progress.review.historical.pending, unit: "条", scope: "historical" as const },
  ];
  return <section className="source-card" aria-label="关系构建进度" style={{ marginTop: 16, marginBottom: 16 }}>
    <div className="spread">
      <div><strong>关系构建进度 · {progress.status_label}</strong>
        <p className="small muted">{progress.policy_count} 份正式政策 · {progress.total} 对候选组合 · 每 10 秒刷新</p>
      </div>
      <Button size="small" loading={query.isFetching} onClick={() => void query.refetch()}>刷新进度</Button>
    </div>
    {query.isError && <Alert type="warning" title="刷新失败，以下为上次成功获取的统计" />}
    <Progress percent={progress.percent} status={progress.status === "failed" ? "exception" : progress.completed === progress.total && progress.total > 0 ? "success" : "active"} />
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 16, marginTop: 12 }}>
      {statistics.map((item) => <div key={item.label}>
        <div className="small muted">{item.label}</div>
        {item.scope && onReviewScope ? <Button type="link" style={{ padding: 0, height: "auto", fontSize: 24 }} onClick={() => onReviewScope(item.scope!)}>{item.value} <span className="small">{item.unit}</span></Button>
          : <div style={{ fontSize: 24, fontWeight: 600 }}>{item.value} <span className="small muted">{item.unit}</span></div>}
      </div>)}
    </div>
    <p className="small muted" style={{ marginBottom: 0 }}>
      已分析中，{progress.no_relation_pairs} 对未发现可确认的正式关系。历史候选单独保留，不计入当前待复核。
      {progress.last_scan_at && ` 最近分析：${new Date(progress.last_scan_at).toLocaleString("zh-CN")}。`}
      {` 统计更新：${new Date(progress.as_of).toLocaleTimeString("zh-CN")}。`}
    </p>
  </section>;
}
