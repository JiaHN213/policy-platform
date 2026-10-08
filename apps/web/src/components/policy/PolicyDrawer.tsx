"use client";

import PolicyBody from "@/components/policy/PolicyBody";

import {
api,
safeExternalUrl,
type PolicyDetail
} from "@/lib/api";
import { explainSystemText } from "@/lib/system-messages";
import {
CheckCircleOutlined,
GlobalOutlined
} from "@ant-design/icons";
import { useMutation,useQuery,useQueryClient } from "@tanstack/react-query";
import {
Alert,
App,
Button,
Drawer,
Popconfirm,
Skeleton,
Tag,
Timeline
} from "antd";


import { AttachmentLinks,documentTypeLabel,ErrorBox,ProvenanceTags } from "@/components/policy/common";

import { AIReviewSummary,type PolicyPipelineState } from "@/components/management/ReviewSupport";
import ReadableText from "./ReadableText";
import CustomerPolicyContent from "./CustomerPolicyContent";
export default function PolicyDrawer({
  id,
  onClose,
  onSelect,
  review = false,
}: {
  id: string | null;
  onClose: () => void;
  onSelect: (id: string) => void;
  review?: boolean;
}) {
  const client = useQueryClient();
  const { message } = App.useApp();
  const taxonomy = useQuery({
    queryKey: ["taxonomy"],
    queryFn: () =>
      api<Record<string, { value: string; label: string }[]>>("taxonomies"),
    enabled: !!id,
  });
  const query = useQuery({
    queryKey: ["policy", id, review],
    queryFn: () =>
      api<PolicyDetail>(
        review ? `admin/policies/${id}?status=all` : `policies/${id}`,
      ),
    enabled: !!id,
  });
  const timeline = useQuery({
    queryKey: ["policy-pipeline-timeline", id],
    queryFn: () =>
      api<{
        current: PolicyPipelineState;
        events: {
          key: string;
          label: string;
          status: "completed" | "active" | "pending" | "warning" | "failed";
          at: string | null;
          detail: string;
        }[];
      }>(`admin/policies/${id}/pipeline-timeline`),
    enabled: !!id && review,
  });
  const withdraw = useMutation({
    mutationFn: (policy: PolicyDetail) =>
      api(`admin/policies/${policy.id}/withdraw`, {
        method: "POST",
        body: JSON.stringify({ version: policy.version }),
      }),
    onSuccess: () => {
      void query.refetch();
      void timeline.refetch();
      void client.invalidateQueries({ queryKey: ["review"] });
      void client.invalidateQueries({ queryKey: ["review-summary"] });
      void client.invalidateQueries({ queryKey: ["policies"] });
      void client.invalidateQueries({ queryKey: ["unified-search"] });
      void client.invalidateQueries({ queryKey: ["overview"] });
      message.success("政策已撤回，不再向客户展示。");
    },
    onError: (error) => message.error(error.message),
  });
  if (!review) return <Drawer open={!!id} onClose={onClose} title="政策速览" rootClassName="policy-reading-drawer" size="min(760px, 96vw)">
    {query.isLoading ? <Skeleton active /> : query.error ? <ErrorBox error={query.error} /> : query.data && <CustomerPolicyContent key={id} policy={query.data} compact onSelect={onSelect} onRead={onClose} />}
  </Drawer>;
  return (
    <Drawer open={!!id} onClose={onClose} title="政策详情" size={720}>
      {query.isLoading ? (
        <Skeleton active />
      ) : query.error ? (
        <ErrorBox error={query.error} />
      ) : (
        query.data && (
          <>
            {query.data.is_demo && (
              <Alert
                type="warning"
                title="此记录为虚构演示数据，不是真实政府政策。"
                showIcon
              />
            )}
            <h2 className="detail-title">{query.data.title}</h2>
            {query.data.evidence_readiness?.status === "incomplete" && <Alert type="warning" showIcon title="关键条件待核对" description={String(query.data.evidence_readiness.reason)} className="space-bottom" />}
            <Tag>
              政策效力：
              {taxonomy.data?.validity_statuses?.find(
                (s) => s.value === query.data.validity_status,
              )?.label || "待核实"}
            </Tag>
            <div className="detail-meta">
              {query.data.issuer} · {query.data.publication_date} ·{" "}
              {query.data.region}
            </div>
            <div className="policy-tags">
              <Tag>{documentTypeLabel(query.data.document_type)}</Tag>
              <ProvenanceTags policy={query.data} />
              {(query.data.topics as string[]).map((t) => (
                <Tag key={t}>{t}</Tag>
              ))}
            </div>
            {review && <AIReviewSummary policy={query.data} />}
            {review && timeline.data && (
              <section className="space-bottom">
                <h3>政策处理时间线</h3>
                <Alert
                  showIcon
                  type={
                    timeline.data.current.stage === "NEEDS_ACTION"
                      ? "warning"
                      : timeline.data.current.stage === "PUBLISHED"
                        ? "success"
                        : "info"
                  }
                  title={timeline.data.current.stage_label}
                  description={explainSystemText(
                    `${timeline.data.current.reason} ${timeline.data.current.next_action}`,
                  )}
                />
                <Timeline
                  items={timeline.data.events.map((event) => ({
                    color:
                      event.status === "completed"
                        ? "green"
                        : event.status === "failed"
                          ? "red"
                          : event.status === "warning"
                            ? "orange"
                            : event.status === "active"
                              ? "blue"
                              : "gray",
                    content: (
                      <div>
                        <strong>{event.label}</strong>
                        <p className="muted">{event.detail}</p>
                        {event.at && (
                          <small className="muted">
                            {new Date(event.at).toLocaleString("zh-CN")}
                          </small>
                        )}
                      </div>
                    ),
                  }))}
                />
              </section>
            )}
            {!query.data.is_demo && (
              <Button
                href={safeExternalUrl(query.data.source_url)}
                target="_blank"
                rel="noopener noreferrer"
                icon={<GlobalOutlined />}
              >
                查看官方原文
              </Button>
            )}
            {!!query.data.sources?.length && (
              <div className="space-bottom">
                <h3>政策发布来源</h3>
                <p className="muted">
                  同一政策只保留一条政策记录，原始发布页与官方转载页在此集中展示。
                </p>
                {query.data.sources.map((source) => (
                  <div className="source-card" key={source.id}>
                    <p>
                      <Tag color={source.is_primary ? "blue" : undefined}>
                        {source.role_label}
                      </Tag>
                      <Tag>{source.source_grade_label}</Tag>
                      {source.publisher || "发布机构待识别"}
                      {source.publication_date &&
                        ` · ${source.publication_date}`}
                    </p>
                    <a
                      href={safeExternalUrl(source.resolved_url || source.url)}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      查看该来源页面 ↗
                    </a>
                  </div>
                ))}
              </div>
            )}
            {query.data.document_type === "draft" && (
              <Alert
                type="warning"
                showIcon
                title="此文件为征求意见稿，不代表已经生效的正式政策。"
              />
            )}
            <h3>
              {query.data.summary_method === "ai"
                ? "AI 摘要与结构化关键词"
                : "原文摘录与结构化关键词"}
            </h3>
            <ReadableText text={query.data.summary || "尚未提取"} />
            {query.data.summary_method === "ai" && (
              <details>
                <summary>查看 AI 摘要的原文依据（摘要仍需核对）</summary>
                {(
                  query.data.summary_evidence as {
                    text: string;
                    quote: string;
                  }[]
                ).map((point, index) => (
                  <blockquote key={index}>
                    <p>{point.text}</p>
                    <p>依据：{point.quote}</p>
                  </blockquote>
                ))}
              </details>
            )}
            <p>
              {(
                query.data.structured_keywords as {
                  term: string;
                  category: string;
                }[]
              ).map((keyword, index) => (
                <Tag key={index}>{keyword.term}</Tag>
              ))}
            </p>
            {!!query.data.industry && (
              <div>
                <h3>业务领域与方向标签</h3>
                <p>
                  {(query.data.business_domains as string[]).map((key) => (
                    <Tag key={key}>
                      {taxonomy.data?.business_domains?.find(
                        (option) => option.value === key,
                      )?.label || key}
                    </Tag>
                  ))}
                </p>
                <p>
                  {(query.data.direction_tags as string[]).map((key) => (
                    <Tag color="blue" key={key}>
                      {taxonomy.data?.direction_tags?.find(
                        (option) => option.value === key,
                      )?.label || key}
                    </Tag>
                  ))}
                </p>
                <details>
                  <summary>查看水务业务收录依据</summary>
                  {(
                    (
                      query.data.scope_evidence as {
                        evidence?: { quote: string }[];
                      }
                    ).evidence || []
                  ).map((item, index) => (
                    <blockquote key={index}>{item.quote}</blockquote>
                  ))}
                </details>
              </div>
            )}
            <h3>已核验的政策关系</h3>
            {!query.data.relations.length ? (
              <p className="muted">
                暂无已核验关系，不按关键词相似度直接认定。
              </p>
            ) : (
              query.data.relations.map((relation) => (
                <div className="source-card" key={relation.id}>
                  <p>
                    {relation.from_title} →{" "}
                    {
                      taxonomy.data?.relation_kinds?.find(
                        (kind) => kind.value === relation.kind,
                      )?.label
                    }{" "}
                    → {relation.to_title}
                  </p>
                  <blockquote>{relation.evidence_quote}</blockquote>
                  <Button
                    onClick={() =>
                      onSelect(
                        relation.from_policy === id
                          ? relation.to_policy
                          : relation.from_policy,
                      )
                    }
                  >
                    查看关联文件
                  </Button>
                </div>
              ))
            )}
            <h3>政策正文与附件</h3>
            <AttachmentLinks policy={query.data} />
            <PolicyBody text={query.data.body} />
            <div className="detail-footer">
              <CheckCircleOutlined />{" "}
              {query.data.status === "published"
                ? "已发布"
                : query.data.status === "withdrawn"
                  ? "已撤下"
                  : "待审核，尚未发布"}{" "}
              · 版本 {query.data.version}
            </div>
            {review && query.data.status === "published" && (
              <Popconfirm
                title="确认撤回这份政策？"
                description="撤回后客户将无法搜索或订阅到该政策，原始数据和审核记录仍会保留。"
                okText="确认撤回"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={() => withdraw.mutateAsync(query.data)}
              >
                <Button danger block loading={withdraw.isPending}>
                  撤回政策
                </Button>
              </Popconfirm>
            )}
          </>
        )
      )}
    </Drawer>
  );
}

