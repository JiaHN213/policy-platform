"use client";

import {
api
} from "@/lib/api";
import {
ReloadOutlined
} from "@ant-design/icons";
import { useMutation,useQuery,useQueryClient } from "@tanstack/react-query";
import {
Alert,
App,
Button,
Popconfirm,
Skeleton,
Tag
} from "antd";


import { ErrorBox } from "@/components/policy/common";

type SearchIndexStatus = {
  enabled: boolean;
  available: boolean;
  backend: string;
  index: string;
  sync_status: string;
  indexed_count: number;
  last_synced_at: string | null;
  last_attempted_at: string | null;
  last_error: string;
  cluster_status?: string;
  connection_error?: string;
  queued?: boolean;
};

export default function SearchIndexPanel() {
  const client = useQueryClient();
  const { message } = App.useApp();
  const query = useQuery({
    queryKey: ["search-index"],
    queryFn: () => api<SearchIndexStatus>("admin/search-index"),
    refetchInterval: 10_000,
  });
  const run = useMutation({
    mutationFn: (rebuild: boolean) =>
      api<SearchIndexStatus>("admin/search-index", {
        method: "POST",
        body: JSON.stringify({ rebuild }),
      }),
    onSuccess: (result) => {
      void client.invalidateQueries({ queryKey: ["search-index"] });
      message.success(
        result.queued ? "已提交后台同步任务。" : "搜索索引同步完成。",
      );
    },
    onError: (error) => message.error(error.message),
  });
  if (query.error) return <ErrorBox error={query.error} />;
  if (query.isLoading) return <Skeleton active />;
  const data = query.data!;
  return (
    <>
      <div className="automation-heading">
        <div>
          <h3>OpenSearch 全文检索索引</h3>
          <p className="muted">
            用于中文全文召回和相关度排序；每条结果返回前仍由 PostgreSQL
            复核发布状态和访问权限。
          </p>
        </div>
        <Tag color={data.available ? "green" : "orange"}>
          {data.available ? "服务正常" : "数据库降级中"}
        </Tag>
      </div>
      <div className="automation-grid">
        <div className="automation-metric">
          <span>索引文件</span>
          <strong>{data.indexed_count}</strong>
          <small>{data.index}</small>
        </div>
        <div className="automation-metric">
          <span>同步状态</span>
          <strong>
            {(
              {
                idle: "空闲",
                syncing: "同步中",
                failed: "失败",
                not_started: "未开始",
              } as Record<string, string>
            )[data.sync_status] || data.sync_status}
          </strong>
          <small>
            {data.last_synced_at
              ? `最近成功：${new Date(data.last_synced_at).toLocaleString("zh-CN")}`
              : "尚未完成首次索引"}
          </small>
        </div>
      </div>
      {(data.last_error || data.connection_error) && (
        <Alert
          type="warning"
          showIcon
          title="全文索引当前不可用，网站已自动使用 PostgreSQL 检索"
          description={data.last_error || data.connection_error}
        />
      )}
      <div className="spread">
        <p className="muted">
          日常增量同步每分钟自动执行。仅在修改索引结构或数据明显不一致时重建。
        </p>
        <div className="source-controls">
          <Button
            icon={<ReloadOutlined />}
            loading={run.isPending}
            disabled={!data.enabled}
            onClick={() => run.mutate(false)}
          >
            立即同步
          </Button>
          <Popconfirm
            title="重建全部搜索索引？"
            description="重建期间关键词搜索会短暂自动回退到数据库。"
            onConfirm={() => run.mutate(true)}
          >
            <Button loading={run.isPending} disabled={!data.enabled}>
              完整重建
            </Button>
          </Popconfirm>
        </div>
      </div>
    </>
  );
}

