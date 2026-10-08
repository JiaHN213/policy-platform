"use client";

import { useQuery } from "@tanstack/react-query";
import { Alert, Collapse, Table, Tag } from "antd";
import { api, safeExternalUrl } from "@/lib/api";

type Source = { name: string; url: string; state: string; last_success_at: string | null; next_check_at: string | null; pending_links: number; overdue_24h_links: number; published_samples: number; published_within_24h: number };

export default function SourceCoverage() {
  const query = useQuery({ queryKey: ["source-coverage"], queryFn: () => api<{ items: Source[]; notice: string }>("source-coverage"), staleTime: 60_000 });
  return <Collapse className="space-bottom" items={[{ key: "coverage", label: "来源覆盖与更新情况", children: <>{query.error ? <Alert type="warning" title="暂时无法读取来源状态，请稍后重试" /> : <><p className="small muted">{query.data?.notice}</p><Table size="small" loading={query.isLoading} rowKey="url" pagination={false} scroll={{ x: 800 }} dataSource={query.data?.items} columns={[
    { title: "已接入来源", dataIndex: "name", render: (name, row) => <a href={safeExternalUrl(row.url)} target="_blank" rel="noreferrer">{name}</a> },
    { title: "最近检查", dataIndex: "state", render: value => <Tag>{value}</Tag> },
    { title: "最近成功", dataIndex: "last_success_at", render: value => value ? new Date(value).toLocaleString("zh-CN") : "尚无记录" },
    { title: "待处理链接", dataIndex: "pending_links" },
    { title: "超过24小时", dataIndex: "overdue_24h_links" },
    { title: "已发布样本（24小时内／总数）", render: (_, row) => `${row.published_within_24h} / ${row.published_samples}` },
  ]} /></>}</> }]} />;
}
