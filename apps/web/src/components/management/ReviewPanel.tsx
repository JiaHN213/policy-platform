"use client";
import type { IntakeSummary } from "./SourcePanel";

import AIReviewControl from "@/components/AIReviewControl";
import PolicyCorrectionButton from "@/components/PolicyCorrectionButton";
import {
api,
safeExternalUrl,
type Page,
type PolicyDetail
} from "@/lib/api";
import { explainSystemText } from "@/lib/system-messages";
import { useMutation,useQuery,useQueryClient } from "@tanstack/react-query";
import {
Alert,
App,
Button,
Empty,
Input,
Pagination,
Popconfirm,
Select,
Skeleton,
Tag
} from "antd";
import { useState } from "react";


import { AttachmentLinks,documentTypeLabel,ErrorBox,ProvenanceTags } from "@/components/policy/common";

import { AIReviewSummary,policyWorkflow,type AIReviewData,type PolicyPipelineStage,type PolicyReviewSummary } from "@/components/management/ReviewSupport";
export default function ReviewPanel({ onSelect }: { onSelect: (id: string) => void }) {
  const client = useQueryClient();
  const { message, modal } = App.useApp();
  const [page, setPage] = useState(1);
  const [queryText, setQueryText] = useState("");
  const [workflowStatus, setWorkflowStatus] =
    useState<PolicyPipelineStage>("NEEDS_ACTION");
  const reviewSummary = useQuery({
    queryKey: ["review-summary"],
    queryFn: () => api<PolicyReviewSummary>("admin/policies/summary"),
    refetchInterval: 5000,
  });
  const intakeSummary = useQuery({
    queryKey: ["discovered-summary"],
    queryFn: () => api<IntakeSummary>("admin/discovered-items/summary"),
    refetchInterval: 5000,
  });
  const lead = useMutation({
    mutationFn: (policy: PolicyDetail) =>
      api(`admin/policies/${policy.id}/mark-lead`, {
        method: "POST",
        body: JSON.stringify({ version: policy.version }),
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["review"] });
      client.invalidateQueries({ queryKey: ["review-summary"] });
      message.success("已保留为 L4 线索，不会进入正式结果或推送。");
    },
    onError: (e) => message.error(e.message),
  });
  const query = useQuery({
    queryKey: ["review", page, queryText, workflowStatus],
    queryFn: () =>
      api<Page<PolicyDetail>>(
        `admin/policies?page=${page}&stage=${workflowStatus}&q=${encodeURIComponent(queryText)}`,
      ),
    refetchInterval: 5000,
  });
  const retryAI = useMutation({
    mutationFn: (id: string) =>
      api(`admin/policy-enrichments/${id}/retry`, { method: "POST" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["review"] });
      void client.invalidateQueries({ queryKey: ["review-summary"] });
      void client.invalidateQueries({ queryKey: ["ai-configuration"] });
      message.success("已重新加入 AI 审核队列。");
    },
    onError: (error) => message.error(error.message),
  });
  const publish = useMutation({
    mutationFn: (policy: PolicyDetail) =>
      api(`admin/policies/${policy.id}/publish`, {
        method: "POST",
        body: JSON.stringify({
          version: policy.version,
          document_type: policy.document_type,
          source_grade: policy.source_grade,
          geographic_level: policy.geographic_level,
          province: policy.province,
          city: policy.city,
          allow_incomplete_attachments: policy.attachments.some(
            (attachment) => attachment.parse_status !== "parsed",
          ),
        }),
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["review"] });
      client.invalidateQueries({ queryKey: ["review-summary"] });
      client.invalidateQueries({ queryKey: ["policies"] });
      client.invalidateQueries({ queryKey: ["unified-search"] });
      client.invalidateQueries({ queryKey: ["overview"] });
      message.success("已发布，匹配订阅的站内通知将由后台生成。");
    },
    onError: (e) => message.error(e.message),
  });
  const withdraw = useMutation({
    mutationFn: (policy: PolicyDetail) =>
      api(`admin/policies/${policy.id}/withdraw`, {
        method: "POST",
        body: JSON.stringify({ version: policy.version }),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["review"] });
      void client.invalidateQueries({ queryKey: ["review-summary"] });
      void client.invalidateQueries({ queryKey: ["policies"] });
      void client.invalidateQueries({ queryKey: ["unified-search"] });
      void client.invalidateQueries({ queryKey: ["overview"] });
      message.success("政策已撤回，客户搜索与订阅结果将不再显示该政策。");
    },
    onError: (e) => message.error(e.message),
  });
  const workflowCounts: Record<PolicyPipelineStage, number> = {
    all: reviewSummary.data?.total || 0,
    AI_PROCESSING:
      reviewSummary.data?.stages?.AI_PROCESSING ||
      (reviewSummary.data?.stages?.WAITING_AI || 0) +
        (reviewSummary.data?.stages?.AI_REVIEWING || 0),
    WAITING_AI: reviewSummary.data?.stages?.WAITING_AI || 0,
    AI_REVIEWING: reviewSummary.data?.stages?.AI_REVIEWING || 0,
    NEEDS_ACTION: reviewSummary.data?.stages?.NEEDS_ACTION || 0,
    EXCLUDED: reviewSummary.data?.stages?.EXCLUDED || 0,
    PUBLISHED: reviewSummary.data?.stages?.PUBLISHED || 0,
    WITHDRAWN: reviewSummary.data?.stages?.WITHDRAWN || 0,
  };
  if (query.error) return <ErrorBox error={query.error} />;
  if (query.isLoading) return <Skeleton active />;
  return (
    <>
      <section className="policy-process" aria-label="政策自动处理流程">
        <div className="policy-process-heading">
          <div>
            <h3>处理概览</h3>
          </div>
        </div>
        <div className="policy-process-steps">
          <div className="policy-process-step">
            <b>1</b>
            <span>采集与解析</span>
            <strong>
              {(intakeSummary.data?.discovered || 0) +
                (intakeSummary.data?.processing || 0)}
            </strong>
            <small>等待正文处理</small>
          </div>
          <button
            type="button"
            className="policy-process-step"
            onClick={() => {
              setWorkflowStatus("AI_PROCESSING");
              setPage(1);
            }}
          >
            <b>2</b>
            <span>AI 解析与审核</span>
            <strong>
              {workflowCounts.WAITING_AI + workflowCounts.AI_REVIEWING}
            </strong>
            <small>自动提取、分类和判断</small>
          </button>
          <button
            type="button"
            className="policy-process-step policy-process-focus"
            onClick={() => {
              setWorkflowStatus("NEEDS_ACTION");
              setPage(1);
            }}
          >
            <b>3</b>
            <span>待我处理</span>
            <strong>{workflowCounts.NEEDS_ACTION}</strong>
            <small>核对、修改或发布</small>
          </button>
          <button
            type="button"
            className="policy-process-step"
            onClick={() => {
              setWorkflowStatus("PUBLISHED");
              setPage(1);
            }}
          >
            <b>4</b>
            <span>客户可见</span>
            <strong>{workflowCounts.PUBLISHED}</strong>
            <small>进入检索与订阅推送</small>
          </button>
        </div>
      </section>
      <AIReviewControl title="AI 自动审核与发布" />
      <div className="automation-heading review-queue-heading">
        <div>
          <h3>处理队列</h3>
          <p className="muted">点击状态筛选</p>
        </div>
        <Tag>全部政策 {reviewSummary.data?.total || 0}</Tag>
      </div>
      <div className="automation-grid" aria-label="政策处理队列统计">
        {(
          [
            ["NEEDS_ACTION", "需要处理", "待补充或核验"],
            ["AI_PROCESSING", "AI审核处理中", "排队与审核中"],
            ["EXCLUDED", "已排除", "不进入正式政策库"],
            ["PUBLISHED", "已发布", "客户可检索"],
            ["WITHDRAWN", "已撤下", "客户不可见"],
          ] as [PolicyPipelineStage, string, string][]
        ).map(([value, label, description]) => (
          <button
            type="button"
            className={`automation-metric automation-metric-button ${workflowStatus === value ? "active" : ""}`}
            key={value}
            onClick={() => {
              setWorkflowStatus(value);
              setPage(1);
            }}
          >
            <span>{label}</span>
            <strong>{workflowCounts[value]}</strong>
            <small>{description}</small>
          </button>
        ))}
      </div>
      <div className="management-filters review-management-filters">
        <Input.Search
          allowClear
          placeholder="搜索政策标题或文号"
          onSearch={(value) => {
            setQueryText(value.trim());
            setPage(1);
          }}
        />
        <Select
          aria-label="政策处理状态"
          value={workflowStatus}
          options={[
            {
              value: "NEEDS_ACTION",
              label: `需要处理 (${workflowCounts.NEEDS_ACTION})`,
            },
            {
              value: "AI_PROCESSING",
              label: `AI审核处理中 (${workflowCounts.AI_PROCESSING})`,
            },
            {
              value: "EXCLUDED",
              label: `已排除 (${workflowCounts.EXCLUDED})`,
            },
            {
              value: "PUBLISHED",
              label: `已发布 (${workflowCounts.PUBLISHED})`,
            },
            {
              value: "WITHDRAWN",
              label: `已撤下 (${workflowCounts.WITHDRAWN})`,
            },
            {
              value: "all",
              label: `全部政策 (${reviewSummary.data?.total || 0})`,
            },
          ]}
          onChange={(value) => {
            setWorkflowStatus(value as PolicyPipelineStage);
            setPage(1);
          }}
        />
      </div>
      {!query.data?.items.length ? (
        <div className="empty-pad">
          <Empty description="没有符合当前筛选条件的政策" />
        </div>
      ) : (
        query.data.items.map((policy) => {
          const workflow = policyWorkflow(policy);
          const enrichment = (
            policy as PolicyDetail & { ai_enrichment?: AIReviewData | null }
          ).ai_enrichment;
          const nextStep =
            workflow.pipeline?.next_action || "查看政策处理详情。";
          return (
            <article className="review-card" key={policy.id}>
              <div className="review-card-layout">
                <div className="review-card-main">
                  <div className="spread">
                    <Tag color={workflow.color}>
                      {workflow.label} · v{policy.version}
                    </Tag>
                    {policy.is_demo && <Tag>虚构演示数据</Tag>}
                  </div>
                  <h3>{policy.title}</h3>
                  <p className="muted">
                    {policy.issuer} · {policy.publication_date}
                  </p>
                  <ProvenanceTags policy={policy} />
                  <div className="review-summary-copy">
                    <span>政策摘要</span>
                    <p>
                      {policy.summary ||
                        `${policy.body.slice(0, 220)}${policy.body.length > 220 ? "…" : ""}`}
                    </p>
                  </div>
                  <AIReviewSummary policy={policy} />
                  {workflow.pipeline?.stage === "NEEDS_ACTION" && (
                    <Alert
                      showIcon
                      type="warning"
                      title={explainSystemText(workflow.pipeline.reason)}
                      description={workflow.pipeline.next_action}
                    />
                  )}
                  <AttachmentLinks policy={policy} />
                  {policy.source_grade === "L4" && (
                    <Alert
                      type="warning"
                      title="此记录仅作非官方线索，不可正式发布或用作关键事实依据。"
                    />
                  )}
                </div>
                <aside className="review-card-aside">
                  <span className="eyebrow">下一步</span>
                  <p>{nextStep}</p>
                  <Button
                    type="primary"
                    block
                    onClick={() => onSelect(policy.id)}
                  >
                    打开处理工作区
                  </Button>
                  {!!enrichment?.review && (
                    <PolicyCorrectionButton
                      policyId={policy.id}
                      onSaved={() => void query.refetch()}
                    />
                  )}
                  {enrichment?.status === "failed" && !!enrichment.id && (
                    <Button
                      block
                      loading={retryAI.isPending}
                      onClick={() => retryAI.mutate(enrichment.id!)}
                    >
                      重试 AI 审核
                    </Button>
                  )}
                  {policy.status === "candidate" && !!enrichment?.review && (
                    <Button
                      block
                      type="primary"
                      loading={publish.isPending}
                      disabled={
                        policy.source_grade === "L4" ||
                        !policy.document_type ||
                        policy.document_type === "unclassified"
                      }
                      onClick={() =>
                        modal.confirm({
                          title: "确认已核对原文并发布？",
                          content: `${policy.attachments.some((attachment) => attachment.parse_status !== "parsed") ? "部分附件尚未解析；本次人工批准将以已解析正文发布，并在审计记录中保留附件例外。" : "AI 分类和原文已准备完成。"} 将按“${documentTypeLabel(policy.document_type)}”发布，并触发订阅匹配。${policy.is_demo ? "这是虚构演示记录。" : ""}`,
                          okText: "确认发布",
                          cancelText: "继续核对",
                          onOk: () => publish.mutateAsync(policy),
                        })
                      }
                    >
                      审核通过并发布
                    </Button>
                  )}
                  {policy.status === "published" && (
                    <Popconfirm
                      title="确认撤回这份政策？"
                      description="撤回后，客户搜索、订阅匹配和政策详情中将不再显示该政策；原始数据和审核记录仍会保留。"
                      okText="确认撤回"
                      cancelText="取消"
                      okButtonProps={{ danger: true }}
                      onConfirm={() => withdraw.mutateAsync(policy)}
                    >
                      <Button block danger loading={withdraw.isPending}>
                        撤回政策
                      </Button>
                    </Popconfirm>
                  )}
                  {!policy.is_demo && (
                    <Button
                      block
                      href={safeExternalUrl(policy.source_url)}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      查看官方原文 ↗
                    </Button>
                  )}
                  {policy.source_grade !== "L4" && (
                    <Popconfirm
                      title="仅保留为 L4 非官方线索？"
                      description="此记录将禁止正式发布；找到官方原文后须另行收录。"
                      okText="确认"
                      cancelText="取消"
                      onConfirm={() => lead.mutateAsync(policy)}
                    >
                      <Button block loading={lead.isPending}>
                        标为 L4 线索
                      </Button>
                    </Popconfirm>
                  )}
                </aside>
              </div>
            </article>
          );
        })
      )}
      <Pagination
        className="pagination"
        current={page}
        total={query.data?.count || 0}
        pageSize={20}
        showSizeChanger={false}
        onChange={setPage}
      />
    </>
  );
}

