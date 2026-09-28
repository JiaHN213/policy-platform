"use client";

import {
type PolicyDetail
} from "@/lib/api";
import { explainSystemMessage,explainSystemText } from "@/lib/system-messages";
import {
Alert
} from "antd";

export type AIReviewData = {
  id?: string;
  status: string;
  model?: string;
  error_code?: string;
  review?: {
    decision: "include" | "exclude" | "needs_review";
    confidence: number;
    reason: string;
    facts?: { category: string; value: string; quote: string }[];
    warnings?: string[];
    grounding_diagnostics?: { field: string; quote?: string; reason: string }[];
  } | null;
};

export type PolicyReviewSummary = {
  total: number;
  stages: Record<Exclude<PolicyPipelineStage, "all">, number>;
  needs_action: number;
  ai_pending: number;
  ai_failed: number;
  ai_excluded: number;
  published: number;
  withdrawn: number;
};

export type PolicyPipelineStage =
  | "all"
  | "AI_PROCESSING"
  | "WAITING_AI"
  | "AI_REVIEWING"
  | "NEEDS_ACTION"
  | "EXCLUDED"
  | "PUBLISHED"
  | "WITHDRAWN";

export type PolicyPipelineState = {
  stage: Exclude<PolicyPipelineStage, "all">;
  stage_label: string;
  reason_category: string;
  reason_code: string;
  reason: string;
  next_action: string;
  updated_at: string;
};

export function policyWorkflow(policy: PolicyDetail): {
  label: string;
  color: string;
  pipeline?: PolicyPipelineState;
} {
  const pipeline = (
    policy as PolicyDetail & { pipeline?: PolicyPipelineState | null }
  ).pipeline;
  if (pipeline) {
    const colors: Record<PolicyPipelineState["stage"], string> = {
      AI_PROCESSING: "processing",
      WAITING_AI: "gold",
      AI_REVIEWING: "processing",
      NEEDS_ACTION: "blue",
      EXCLUDED: "orange",
      PUBLISHED: "green",
      WITHDRAWN: "default",
    };
    return {
      label: pipeline.stage_label,
      color: colors[pipeline.stage],
      pipeline,
    };
  }
  const enrichment = (
    policy as PolicyDetail & { ai_enrichment?: AIReviewData | null }
  ).ai_enrichment;
  if (policy.status === "published") return { label: "已发布", color: "green" };
  if (policy.status === "withdrawn")
    return { label: "已撤回", color: "default" };
  if (enrichment?.status === "failed")
    return { label: "AI 审核失败", color: "red" };
  if (
    enrichment?.status === "succeeded" &&
    enrichment.review?.decision === "exclude"
  ) {
    return { label: "AI 建议排除", color: "orange" };
  }
  if (enrichment?.status === "succeeded")
    return { label: "待我处理", color: "blue" };
  return { label: "等待 AI 完成", color: "gold" };
}

export function AIReviewSummary({ policy }: { policy: PolicyDetail }) {
  const enrichment = (
    policy as PolicyDetail & { ai_enrichment?: AIReviewData | null }
  ).ai_enrichment;
  if (!enrichment) return null;
  const labels = { include: "纳入", exclude: "排除", needs_review: "待核实" };
  if (!enrichment.review) {
    return (
      <Alert
        showIcon
        type={enrichment.status === "failed" ? "error" : "info"}
        title={
          enrichment.status === "not_started" || enrichment.status === "queued"
            ? "等待 AI 自动审核"
            : enrichment.status === "failed"
              ? `AI 自动审核未完成：${explainSystemMessage(enrichment.error_code)}`
              : "AI 自动审核处理中"
        }
      />
    );
  }
  const review = enrichment.review;
  return (
    <Alert
      showIcon
      type={
        review.decision === "include"
          ? "success"
          : review.decision === "exclude"
            ? "warning"
            : "info"
      }
      title={`AI 最终审核：${labels[review.decision]} · 置信度 ${Math.round(review.confidence * 100)}%`}
      description={
        <div>
          <p>{explainSystemText(review.reason)}</p>
          {!!review.facts?.length && (
            <details>
              <summary>查看结构化事实与原文证据</summary>
              {review.facts.map((fact, index) => (
                <blockquote key={`${fact.category}-${index}`}>
                  {fact.value}
                  <br />
                  依据：{fact.quote}
                </blockquote>
              ))}
            </details>
          )}
          {!!review.warnings?.length && (
            <div>
              <strong>系统校验说明：</strong>
              <ul>
                {review.warnings.map((warning, index) => (
                  <li key={`${warning}-${index}`}>
                    {explainSystemMessage(warning)}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {!!review.grounding_diagnostics?.length && (
            <details>
              <summary>查看未采用内容及具体原因</summary>
              {review.grounding_diagnostics.map((item, index) => (
                <blockquote key={index}>
                  <strong>{({ scope: "适用范围", document_type: "文件类型", validity: "政策效力", target: "适用对象", condition: "条件", measure: "支持措施", deadline: "截止时间", amount: "金额", region: "地域", department: "部门", responsible_department: "负责部门", opportunity: "政策机会" } as Record<string, string>)[item.field] || item.field}</strong>：{explainSystemText(item.reason)}
                  {item.quote && <p>AI 提交的引用：{item.quote}</p>}
                </blockquote>
              ))}
            </details>
          )}
        </div>
      }
    />
  );
}

