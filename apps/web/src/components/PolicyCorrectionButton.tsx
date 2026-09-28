"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Skeleton,
  Tabs,
  Tag,
} from "antd";
import { api, safeExternalUrl, type PolicyDetail } from "@/lib/api";
import { explainSystemText } from "@/lib/system-messages";

type Option = { value: string; label: string };
type ScopeEvidence = {
  reason?: string;
  evidence?: { term?: string; quote?: string }[];
  ai_review?: {
    decision?: string;
    confidence?: number;
    reason?: string;
    evidence?: { field?: string; quote?: string }[];
  };
};
type EnrichmentResult = {
  status?: string;
  model?: string;
  error_code?: string;
  review?: {
    decision?: string;
    confidence?: number;
    reason?: string;
    facts?: { category?: string; value?: string; quote?: string }[];
  } | null;
};

type FieldProvenance = {
  id: string;
  policy_version: number;
  field_name: string;
  value_snapshot: unknown;
  source_type: string;
  source_label: string;
  evidence_quote: string;
  model: string;
  prompt_version: string;
  config_version: string;
  locked: boolean;
  actor_name: string | null;
  created_at: string;
};

type FieldProvenanceResponse = {
  policy: string;
  version: number;
  current: FieldProvenance[];
  history: FieldProvenance[];
};

const fieldLabels: Record<string, string> = {
  title: "政策名称",
  issuer: "发文机关",
  document_number: "发文字号",
  publication_date: "发布日期",
  body: "政策全文",
  summary: "政策摘要",
  document_type: "文件类型",
  source_grade: "来源等级",
  geographic_level: "地域层级",
  province: "省级行政区",
  city: "地级市",
  validity_status: "政策效力",
  validity_evidence: "效力依据",
  business_domains: "核心业务领域",
  direction_tags: "方向标签",
  document_role: "文档角色",
  opportunity_level: "政策机会级别",
  support_signals: "政策支持信号",
};

function displayValue(value: unknown) {
  if (Array.isArray(value)) return value.join("、") || "空";
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value ?? "空");
}

export default function PolicyCorrectionButton({
  policyId,
  onSaved,
}: {
  policyId: string;
  onSaved?: () => void;
}) {
  const { message } = App.useApp();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const policy = useQuery({
    queryKey: ["policy-correction", policyId],
    queryFn: () => api<PolicyDetail>(`admin/policies/${policyId}?status=all`),
    enabled: open,
  });
  const taxonomy = useQuery({
    queryKey: ["taxonomy"],
    queryFn: () => api<Record<string, Option[]>>("taxonomies"),
    enabled: open,
  });
  const provenance = useQuery({
    queryKey: ["policy-field-provenance", policyId],
    queryFn: () =>
      api<FieldProvenanceResponse>(
        `admin/policies/${policyId}/field-provenance`,
      ),
    enabled: open,
  });
  const geographicLevel = Form.useWatch("geographic_level", form);

  useEffect(() => {
    if (!policy.data || !open) return;
    form.setFieldsValue({
      version: policy.data.version,
      summary: policy.data.summary,
      document_type: policy.data.document_type,
      source_grade: policy.data.source_grade,
      geographic_level: policy.data.geographic_level,
      province: policy.data.province,
      city: policy.data.city,
      validity_status: policy.data.validity_status,
      validity_evidence: policy.data.validity_evidence,
      business_domains: policy.data.business_domains,
      direction_tags: policy.data.direction_tags,
    });
  }, [form, open, policy.data]);

  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api<PolicyDetail>(`admin/policies/${policyId}/correct`, {
        method: "POST",
        body: JSON.stringify(values),
      }),
    onSuccess: () => {
      setOpen(false);
      void client.invalidateQueries({ queryKey: ["review"] });
      void client.invalidateQueries({ queryKey: ["ai-jobs"] });
      void client.invalidateQueries({ queryKey: ["policies"] });
      void client.invalidateQueries({ queryKey: ["unified-search"] });
      void client.invalidateQueries({ queryKey: ["policy", policyId] });
      message.success("已在 AI 审核结果上保存人工修正，原发布状态保持不变。");
      onSaved?.();
    },
    onError: (error) => message.error(error.message),
  });
  const unlock = useMutation({
    mutationFn: (fieldName: string) =>
      api(`admin/policies/${policyId}/unlock-field`, {
        method: "POST",
        body: JSON.stringify({
          version: policy.data?.version,
          field_name: fieldName,
        }),
      }),
    onSuccess: () => {
      void client.invalidateQueries({
        queryKey: ["policy-field-provenance", policyId],
      });
      message.success("字段已解锁，后续AI重审可以重新计算该字段。");
    },
    onError: (error) => message.error(error.message),
  });

  return (
    <>
      <Button onClick={() => setOpen(true)}>查看并修正</Button>
      <Modal
        title="查看并修正 AI 审核结果"
        open={open}
        width={1120}
        okText="保存修正"
        cancelText="取消"
        confirmLoading={save.isPending}
        onCancel={() => !save.isPending && setOpen(false)}
        onOk={() => form.submit()}
      >
        {policy.error && <Alert type="error" title={policy.error.message} />}
        {policy.isLoading && <Skeleton active />}
        {policy.data && (
          <>
            <p>
              <strong>{policy.data.title}</strong>{" "}
              <Tag
                color={policy.data.status === "published" ? "green" : "orange"}
              >
                {policy.data.status === "published" ? "已发布" : "未发布"}
              </Tag>
            </p>
            <Alert
              type="info"
              showIcon
              title="表单已填入 AI 审核结果，只修改有误的字段即可；保存不会撤下已发布政策。"
              style={{ marginBottom: 16 }}
            />
            <Tabs
              defaultActiveKey="fields"
              items={[
                {
                  key: "fields",
                  label: "AI 结果与字段",
                  children: (
                    <Form
                      form={form}
                      layout="vertical"
                      onFinish={(values) => save.mutate(values)}
                    >
                      <Form.Item name="version" hidden>
                        <Input />
                      </Form.Item>
                      <Form.Item
                        name="summary"
                        label="政策摘要"
                        rules={[{ required: true }]}
                      >
                        <Input.TextArea rows={5} />
                      </Form.Item>
                      <div className="review-actions">
                        <Form.Item
                          name="document_type"
                          label="文件类型"
                          rules={[{ required: true }]}
                          style={{ minWidth: 220, flex: 1 }}
                        >
                          <Select
                            options={taxonomy.data?.document_types?.filter(
                              (item) => item.value !== "unclassified",
                            )}
                          />
                        </Form.Item>
                        <Form.Item
                          name="source_grade"
                          label="来源等级"
                          rules={[{ required: true }]}
                          style={{ minWidth: 180, flex: 1 }}
                        >
                          <Select options={taxonomy.data?.source_grades} />
                        </Form.Item>
                        <Form.Item
                          name="geographic_level"
                          label="地域层级"
                          rules={[{ required: true }]}
                          style={{ minWidth: 180, flex: 1 }}
                        >
                          <Select
                            options={taxonomy.data?.geographic_levels}
                            onChange={(value) => {
                              if (value === "national")
                                form.setFieldsValue({ province: "", city: "" });
                              if (value === "provincial")
                                form.setFieldValue("city", "");
                            }}
                          />
                        </Form.Item>
                      </div>
                      {geographicLevel !== "national" && (
                        <div className="review-actions">
                          <Form.Item
                            name="province"
                            label="所属省级行政区"
                            rules={[{ required: true }]}
                            style={{ minWidth: 220, flex: 1 }}
                          >
                            <Input />
                          </Form.Item>
                          {geographicLevel === "city" && (
                            <Form.Item
                              name="city"
                              label="所属地级市"
                              rules={[{ required: true }]}
                              style={{ minWidth: 220, flex: 1 }}
                            >
                              <Input />
                            </Form.Item>
                          )}
                        </div>
                      )}
                      <Form.Item name="business_domains" label="核心业务领域">
                        <Select
                          mode="multiple"
                          options={taxonomy.data?.business_domains}
                        />
                      </Form.Item>
                      <Form.Item
                        name="direction_tags"
                        label="技术与政策方向标签"
                      >
                        <Select
                          mode="multiple"
                          options={taxonomy.data?.direction_tags}
                        />
                      </Form.Item>
                      <Form.Item name="validity_status" label="政策效力状态">
                        <Select options={taxonomy.data?.validity_statuses} />
                      </Form.Item>
                      <Form.Item
                        name="validity_evidence"
                        label="效力状态原文证据（待核实可留空）"
                      >
                        <Input.TextArea rows={3} />
                      </Form.Item>
                    </Form>
                  ),
                },
                {
                  key: "provenance",
                  label: "字段来源与修改历史",
                  children: provenance.isLoading ? (
                    <Skeleton active />
                  ) : provenance.error ? (
                    <Alert type="error" title={provenance.error.message} />
                  ) : !provenance.data?.current.length ? (
                    <Empty description="尚无字段来源记录" />
                  ) : (
                    <div className="correction-evidence">
                      <Alert
                        showIcon
                        type="info"
                        title="人工修正的字段会自动锁定，日常AI重审不会覆盖；解锁后才允许AI重新计算。"
                      />
                      {provenance.data.current.map((item) => (
                        <div className="source-card" key={item.id}>
                          <div className="spread">
                            <strong>
                              {fieldLabels[item.field_name] || item.field_name}
                            </strong>
                            <div>
                              <Tag color={item.source_type === "human_correction" ? "blue" : undefined}>
                                {item.source_label}
                              </Tag>
                              <Tag color={item.locked ? "orange" : "green"}>
                                {item.locked ? "人工锁定" : "允许AI更新"}
                              </Tag>
                            </div>
                          </div>
                          <p>{displayValue(item.value_snapshot)}</p>
                          {item.evidence_quote && (
                            <blockquote>原文依据：{item.evidence_quote}</blockquote>
                          )}
                          <p className="small muted">
                            政策版本 v{item.policy_version}
                            {item.model && ` · 模型 ${item.model}`}
                            {item.actor_name && ` · 修改人 ${item.actor_name}`}
                            {` · ${new Date(item.created_at).toLocaleString("zh-CN")}`}
                          </p>
                          {item.locked && (
                            <Button
                              loading={unlock.isPending}
                              onClick={() => unlock.mutate(item.field_name)}
                            >
                              解除字段锁定
                            </Button>
                          )}
                          {provenance.data.history.filter(
                            (history) => history.field_name === item.field_name,
                          ).length > 1 && (
                            <details>
                              <summary>查看历史值</summary>
                              {provenance.data.history
                                .filter(
                                  (history) =>
                                    history.field_name === item.field_name,
                                )
                                .map((history) => (
                                  <p className="small" key={history.id}>
                                    v{history.policy_version} · {history.source_label} ·{" "}
                                    {displayValue(history.value_snapshot)} ·{" "}
                                    {new Date(history.created_at).toLocaleString("zh-CN")}
                                  </p>
                                ))}
                            </details>
                          )}
                        </div>
                      ))}
                    </div>
                  ),
                },
                {
                  key: "body",
                  label: "政策全文",
                  children: (
                    <div>
                      <Button
                        href={safeExternalUrl(policy.data.source_url)}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        打开官方原文 ↗
                      </Button>
                      <div className="correction-document">
                        {policy.data.body}
                      </div>
                    </div>
                  ),
                },
                {
                  key: "evidence",
                  label: "分类依据",
                  children: (() => {
                    const scope = (policy.data.scope_evidence ||
                      {}) as ScopeEvidence;
                    const enrichment = (
                      policy.data as PolicyDetail & {
                        ai_enrichment?: EnrichmentResult | null;
                      }
                    ).ai_enrichment;
                    return (
                      <div className="correction-evidence">
                        <h4>规则筛选依据</h4>
                        <p>{scope.reason || "暂无规则筛选说明。"}</p>
                        {scope.evidence?.map((proof, index) => (
                          <blockquote key={`${proof.term}-${index}`}>
                            {proof.term && <strong>{proof.term}：</strong>}
                            {proof.quote || "未保存引文"}
                          </blockquote>
                        ))}
                        <h4>AI 审核依据</h4>
                        <p>
                          状态：
                          {explainSystemText(enrichment?.status || "尚未审核")}
                          {enrichment?.model && ` · 模型：${enrichment.model}`}
                        </p>
                        {enrichment?.review ? (
                          <>
                            <p>
                              结论：
                              {explainSystemText(
                                enrichment.review.decision || "待核实",
                              )}
                              {typeof enrichment.review.confidence ===
                                "number" &&
                                ` · 置信度 ${Math.round(enrichment.review.confidence * 100)}%`}
                            </p>
                            <p>{explainSystemText(enrichment.review.reason)}</p>
                            {enrichment.review.facts?.map((fact, index) => (
                              <blockquote key={`${fact.category}-${index}`}>
                                {fact.value}
                                <br />
                                依据：{fact.quote}
                              </blockquote>
                            ))}
                          </>
                        ) : (
                          <Empty description="暂无 AI 审核依据" />
                        )}
                      </div>
                    );
                  })(),
                },
                {
                  key: "attachments",
                  label: `附件（${policy.data.attachments.length}）`,
                  children: policy.data.attachments.length ? (
                    policy.data.attachments.map((attachment, index) => (
                      <div
                        className="correction-attachment"
                        key={attachment.id}
                      >
                        <div>
                          <strong>附件 {index + 1}</strong>
                          <p className="small muted">
                            {attachment.content_type} ·{" "}
                            {Math.ceil(attachment.size_bytes / 1024)} KB
                          </p>
                        </div>
                        <div>
                          <Tag
                            color={
                              attachment.parse_status === "parsed"
                                ? "green"
                                : "orange"
                            }
                          >
                            {attachment.parse_status === "parsed"
                              ? "已解析"
                              : "待处理"}
                          </Tag>
                          <Button
                            href={safeExternalUrl(attachment.url)}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            查看附件 ↗
                          </Button>
                        </div>
                      </div>
                    ))
                  ) : (
                    <Empty description="该文件没有附件" />
                  ),
                },
              ]}
            />
          </>
        )}
      </Modal>
    </>
  );
}
