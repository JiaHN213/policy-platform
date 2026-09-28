"use client";

import {
api,
safeExternalUrl,
type DiscoveredItem,
type Page,
type Source,
type SourceRun
} from "@/lib/api";
import { explainSystemMessage,explainSystemText } from "@/lib/system-messages";
import {
GlobalOutlined,
ReloadOutlined
} from "@ant-design/icons";
import { useMutation,useQuery,useQueryClient } from "@tanstack/react-query";
import {
Alert,
App,
Button,
Empty,
Pagination,
Select,
Skeleton,
Switch,
Tag
} from "antd";
import { useState } from "react";


import { ErrorBox } from "@/components/policy/common";

export type IntakeSummary = {
  total: number;
  discovered: number;
  processing: number;
  failed: number;
  imported: number;
  indexed: number;
  excluded: number;
  needs_review: number;
  needs_attention_links: number;
};

export default function SourcePanel() {
  const client = useQueryClient();
  const { message } = App.useApp();
  const [itemPage, setItemPage] = useState(1);
  const [itemStatus, setItemStatus] = useState("");
  const summary = useQuery({
    queryKey: ["discovered-summary"],
    queryFn: () => api<IntakeSummary>("admin/discovered-items/summary"),
    refetchInterval: 5000,
  });
  const items = useQuery({
    queryKey: ["discovered-items", itemPage, itemStatus],
    queryFn: () =>
      api<Page<DiscoveredItem>>(
        `admin/discovered-items?page=${itemPage}&status=${itemStatus}`,
      ),
    refetchInterval: 10_000,
  });
  const retry = useMutation({
    mutationFn: (id: string) =>
      api(`admin/discovered-items/${id}/retry`, { method: "POST" }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["discovered-items"] });
      client.invalidateQueries({ queryKey: ["discovered-summary"] });
      message.success("已重新排队，后台将自动处理。");
    },
    onError: (e) => message.error(e.message),
  });
  const schedule = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api(`admin/sources/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["sources"] }),
    onError: (e) => message.error(e.message),
  });
  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: () => api<Page<Source>>("admin/sources"),
    refetchInterval: 10_000,
  });
  const runs = useQuery({
    queryKey: ["source-runs"],
    queryFn: () => api<Page<SourceRun>>("admin/source-check-runs"),
    refetchInterval: 10_000,
  });
  const check = useMutation({
    mutationFn: (id: string) =>
      api(`admin/sources/${id}/run-now`, { method: "POST" }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["source-runs"] });
      client.invalidateQueries({ queryKey: ["sources"] });
      message.success("已提交断点续采任务，可在下方查看进度。");
    },
    onError: (e) => message.error(e.message),
  });
  return (
    <>
      <div className="automation-heading">
        <div>
          <h3>采集进度</h3>
        </div>
      </div>
      {summary.error ? (
        <ErrorBox error={summary.error} />
      ) : (
        <div className="automation-grid" aria-label="自动化处理进度">
          <div className="automation-metric">
            <span>累计登记链接</span>
            <strong>{summary.data?.total || 0}</strong>
            <small>按链接去重</small>
          </div>
          <div className="automation-metric">
            <span>待解析</span>
            <strong>{summary.data?.discovered || 0}</strong>
            <small>{summary.data?.processing || 0} 份正在处理</small>
          </div>
          <div className="automation-metric">
            <span>已进入政策库</span>
            <strong>{summary.data?.imported || 0}</strong>
            <small>自动进入 AI 审核队列</small>
          </div>
          <div className="automation-metric">
            <span>目录留档</span>
            <strong>{summary.data?.indexed || 0}</strong>
            <small>已保存链接，未进入正文下载</small>
          </div>
          <div className="automation-metric">
            <span>需处理政策链接</span>
            <strong>{summary.data?.needs_attention_links || 0}</strong>
            <small>解析失败或资料不完整</small>
          </div>
        </div>
      )}
      <p className="muted small">暂停自动检查后，已排队的文件仍会继续解析。</p>
      {sources.error ? (
        <ErrorBox error={sources.error} />
      ) : sources.isLoading ? (
        <Skeleton active />
      ) : (
        sources.data?.items.map((source) => {
          const cooldownAt = source.cooldown_until
            ? new Date(source.cooldown_until)
            : null;
          const state = source.crawl_state || "scheduled";
          const cooling = state === "cooldown";
          const stateLabels: Record<string, string> = {
            paused: "定期采集已停止",
            cooldown: "访问保护冷却中",
            running: "正在从断点采集",
            scheduled: "等待下次自动采集",
          };
          const stateColors: Record<string, string> = {
            paused: "default",
            cooldown: "orange",
            running: "processing",
            scheduled: "green",
          };
          return (
            <article className="source-card" key={source.id}>
              <div className="spread">
                <h3>
                  <GlobalOutlined /> {source.name}
                </h3>
                <div className="source-controls">
                  <Tag color={stateColors[state]}>
                    {stateLabels[state] || state}
                  </Tag>
                  <Switch
                    checkedChildren="自动采集开"
                    unCheckedChildren="自动采集关"
                    aria-label={`自动采集${source.name}`}
                    checked={source.enabled}
                    loading={schedule.isPending}
                    onChange={(enabled) =>
                      schedule.mutate({ id: source.id, enabled })
                    }
                  />
                </div>
              </div>
              <a
                href={safeExternalUrl(source.url)}
                target="_blank"
                rel="noopener noreferrer"
              >
                查看来源网站 ↗
              </a>
              <p className="muted">{source.notes}</p>
              {cooling && cooldownAt && (
                <Alert
                  showIcon
                  type="warning"
                  title={`${source.cooldown_reason || "来源保护冷却"}，系统没有继续请求`}
                  description={`预计 ${cooldownAt.toLocaleString("zh-CN")} 后自动从断点恢复。冷却期间无需重复点击。`}
                />
              )}
              <div className="spread">
                <span className="small">
                  每 {Math.round((source.interval_minutes || 0) / 60)}{" "}
                  小时检查一次
                  {source.next_check_at &&
                    ` · 下次计划：${new Date(source.next_check_at).toLocaleString("zh-CN")}`}
                  {" · 最近完整成功："}
                  {source.last_success_at
                    ? new Date(source.last_success_at).toLocaleString("zh-CN")
                    : "尚无"}
                </span>
                <Button
                  icon={<ReloadOutlined />}
                  loading={check.isPending}
                  disabled={!source.enabled || cooling || state === "running"}
                  onClick={() => check.mutate(source.id)}
                >
                  {cooling ? "冷却结束后自动继续" : "立即从断点继续"}
                </Button>
              </div>
            </article>
          );
        })
      )}
      <h3 className="section-subtitle">最近检查记录</h3>
      {runs.error ? (
        <ErrorBox error={runs.error} />
      ) : !runs.data?.items.length ? (
        <Empty description="尚无检查记录" />
      ) : (
        <div className="run-list">
          {runs.data.items.map((run) => {
            const progress = (run.progress || {}) as Record<string, unknown>;
            const queries = Array.isArray(progress.queries)
              ? progress.queries
              : [];
            const queryIndex = Number(progress.query_index || 0);
            const legacyFullCatalog =
              Number(progress.version || 1) < 3 && queries[0] === "";
            return (
              <div className="run-row" key={run.id}>
                <Tag color={run.status === "failed" ? "red" : "gold"}>
                  {(
                    {
                      queued: "排队中",
                      running: "检查中",
                      partial: "部分覆盖",
                      failed: "检查失败",
                      succeeded: "列表覆盖完成",
                    } as Record<string, string>
                  )[run.status || "queued"] || run.status}
                </Tag>
                <div>
                  <span>
                    {explainSystemText(run.error_message) || "正在等待检查结果"}
                  </span>
                  <p className="small muted">
                    {new Date(run.created_at).toLocaleString("zh-CN")} · 新登记{" "}
                    {run.discovered} 条链接
                  </p>
                  {!!run.progress && (
                    <p className="small muted">
                      已检查 {String(progress.pages_scanned || 0)} 页、扫描{" "}
                      {String(progress.rows_scanned || 0)} 条结果
                      {queries.length > 0 &&
                        ` · 检索词 ${Math.min(queryIndex + 1, queries.length)}/${queries.length}`}
                      {legacyFullCatalog
                        ? " · 旧版全站历史扫描已停止，下次续采会自动切换为水务环保定向回填"
                        : " · 列表完成后正文和附件仍会继续串行处理"}
                    </p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
      <h3 className="section-subtitle">发现的政策与解析进度</h3>
      <p className="muted">
        “累计登记链接”是下方全部状态之和；“待解析”不包含正在处理的文件。目录留档包含尚未解析的普通索引，以及已解析但业务适用依据不足的弱关联线索。下载或解析失败最多自动尝试三次。
      </p>
      <Select
        aria-label="解析状态"
        value={itemStatus}
        style={{ width: 180 }}
        onChange={(value) => {
          setItemStatus(value);
          setItemPage(1);
        }}
        options={[
          { value: "", label: `全部来源记录 (${summary.data?.total || 0})` },
          {
            value: "discovered",
            label: `待解析 (${summary.data?.discovered || 0})`,
          },
          {
            value: "processing",
            label: `解析中 (${summary.data?.processing || 0})`,
          },
          { value: "failed", label: `解析失败 (${summary.data?.failed || 0})` },
          {
            value: "imported",
            label: `已进入政策库 (${summary.data?.imported || 0})`,
          },
          {
            value: "indexed",
            label: `目录留档 (${summary.data?.indexed || 0})`,
          },
          {
            value: "excluded",
            label: `业务范围排除 (${summary.data?.excluded || 0})`,
          },
          {
            value: "needs_review",
            label: `来源信息待补全 (${summary.data?.needs_review || 0})`,
          },
        ]}
      />
      {items.error ? (
        <ErrorBox error={items.error} />
      ) : items.isLoading ? (
        <Skeleton active />
      ) : !items.data?.items.length ? (
        <Empty description="暂无对应记录" />
      ) : (
        items.data.items.map((item) => (
          <article className="source-card" key={item.id}>
            <div className="spread">
              <h3>{item.title}</h3>
              <Tag color={item.status === "failed" ? "red" : "blue"}>
                {
                  (
                    {
                      discovered: "待解析",
                      processing: "解析中",
                      failed: "解析失败",
                      imported: "已入库",
                      indexed: "目录留档（未入政策库）",
                      excluded: "业务范围排除",
                      needs_review: "来源信息待补全",
                    } as Record<string, string>
                  )[item.status || "discovered"]
                }
              </Tag>
            </div>
            <a
              href={safeExternalUrl(item.url)}
              target="_blank"
              rel="noopener noreferrer"
            >
              核对官方原文 ↗
            </a>
            {item.status === "imported" && (
              <p className="muted">
                已进入政策库；新入库政策需在“待审核”中核对发布。
              </p>
            )}
            {!!(item.scope_assessment as { reason?: string })?.reason && (
              <p className="muted">
                筛选依据：
                {explainSystemText(
                  (item.scope_assessment as { reason: string }).reason,
                )}
              </p>
            )}
            {!!(item.attachment_issues as { url: string; reason: string }[])
              ?.length && (
              <Alert
                type="warning"
                showIcon
                title="附件资料尚未完整，不能正式发布"
                description={
                  <ul>
                    {(
                      item.attachment_issues as {
                        url: string;
                        reason: string;
                      }[]
                    ).map((issue) => (
                      <li key={issue.url}>
                        <a
                          href={safeExternalUrl(issue.url)}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          查看待处理附件
                        </a>{" "}
                        · {explainSystemMessage(issue.reason)}
                      </li>
                    ))}
                  </ul>
                }
              />
            )}
            {item.status === "needs_review" && !!item.error_code && (
              <p className="muted">
                待核实原因：{explainSystemMessage(item.error_code)}
              </p>
            )}
            {item.status === "failed" && (
              <div className="spread">
                <p className="muted">
                  已尝试 {item.attempts} 次 ·{" "}
                  {explainSystemMessage(item.error_code, item.error_detail)}
                  {item.attempts < 3 && item.retry_at
                    ? ` · ${new Date(item.retry_at).toLocaleString("zh-CN")} 后自动重试`
                    : " · 需人工检查后重试"}
                </p>
                <Button
                  loading={retry.isPending}
                  onClick={() => retry.mutate(item.id)}
                >
                  重新解析
                </Button>
              </div>
            )}
          </article>
        ))
      )}
      <Pagination
        className="pagination"
        current={itemPage}
        total={items.data?.count || 0}
        pageSize={20}
        showSizeChanger={false}
        onChange={setItemPage}
      />
    </>
  );
}

