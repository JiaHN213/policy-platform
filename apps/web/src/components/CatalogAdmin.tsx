"use client";

import { useState } from "react";
import RelationBuildProgress, { useRelationProgress, type ReviewScope } from "@/components/RelationBuildProgress";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Divider,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Pagination,
  Select,
  Spin,
  Tag,
} from "antd";
import {
  api,
  safeExternalUrl,
  type Page,
  type Policy,
  type PolicyDetail,
} from "@/lib/api";

type Option = { value: string; label: string };
type CatalogItem = {
  id: string;
  title?: string;
  name?: string;
  kind?: string;
  status?: string;
  verification_status?: string;
  [key: string]: unknown;
};
const paths: Record<string, string> = {
  opportunity: "admin/opportunities",
  relation: "admin/policy-relations",
  relation_review: "admin/relation-review-candidates",
};
function localInput(value: unknown) {
  if (!value) return "";
  const date = new Date(String(value));
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

export default function CatalogAdmin() {
  const { message } = App.useApp();
  const client = useQueryClient();
  const [kind, setKind] = useState("opportunity");
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<CatalogItem | null>(null);
  const [open, setOpen] = useState(false);
  const [policyQuery, setPolicyQuery] = useState("");
  const [reviewStatus, setReviewStatus] = useState("pending");
  const [reviewScope, setReviewScope] = useState<ReviewScope>("current");
  const relationProgress = useRelationProgress(kind !== "opportunity");
  const reviewCounts = relationProgress.data?.review[reviewScope];
  const [form] = Form.useForm();
  const selectedPolicyId = Form.useWatch("policy", form) as string | undefined;
  const taxonomy = useQuery({
    queryKey: ["taxonomy"],
    queryFn: () => api<Record<string, Option[]>>("taxonomies"),
  });
  const policies = useQuery({
    queryKey: ["catalog-policies", policyQuery],
    queryFn: () =>
      api<Page<Policy>>(
        `admin/policies?status=all&q=${encodeURIComponent(policyQuery)}`,
      ),
  });
  const records = useQuery({
    queryKey: ["catalog", kind, page, reviewStatus, reviewScope],
    queryFn: () =>
      api<Page<CatalogItem>>(
        `${paths[kind]}?page=${page}${kind === "relation_review" ? `&status=${reviewStatus}&scope=${reviewScope}` : ""}`,
      ),
    refetchInterval: kind === "relation_review" && !open ? 10_000 : false,
  });
  const policyDetail = useQuery({
    queryKey: ["catalog-policy-detail", selectedPolicyId],
    queryFn: () => api<PolicyDetail>(`admin/policies/${selectedPolicyId}`),
    enabled: open && kind === "opportunity" && !!selectedPolicyId,
  });
  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => {
      if (kind === "relation_review" && editing) {
        return api(`${paths[kind]}/${editing.id}/approve`, {
          method: "POST",
          body: JSON.stringify(values),
        });
      }
      const payload = { ...values };
      return api(`${paths[kind]}${editing ? `/${editing.id}` : ""}`, {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
    },
    onSuccess: () => {
      setOpen(false);
      client.invalidateQueries();
      message.success("已保存。只有核验通过且证据版本有效的记录会展示给客户。");
    },
    onError: (error) => message.error(error.message),
  });
  const rejectCandidate = useMutation({
    mutationFn: (id: string) =>
      api(`${paths.relation_review}/${id}/reject`, { method: "POST" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["catalog"] });
      void client.invalidateQueries({ queryKey: ["relation-build-progress"] });
      message.success("已确认两份政策没有可成立的正式关系。");
    },
    onError: (error) => message.error(error.message),
  });
  const basePolicyOptions =
    policies.data?.items.map((policy) => ({
      value: policy.id,
      label: `${policy.title} · v${policy.version}`,
    })) || [];
  const policyOptions =
    editing?.policy &&
    !basePolicyOptions.some((option) => option.value === editing.policy)
      ? [
          {
            value: String(editing.policy),
            label: `${String(editing.policy_title || "所属政策文件")} · v${String(editing.policy_version || "?")}`,
          },
          ...basePolicyOptions,
        ]
      : basePolicyOptions;
  const proofChange = (id: string) => {
    const policy = policies.data?.items.find((item) => item.id === id);
    form.setFieldValue("evidence_version", policy?.version);
  };
  const policySelect = (name: string, label: string) => (
    <Form.Item name={name} label={label} rules={[{ required: true }]}>
      <Select
        showSearch
        filterOption={false}
        onSearch={setPolicyQuery}
        options={policyOptions}
        onChange={(id) => {
          if (name === "evidence_policy") proofChange(id);
          if (name === "policy" && kind === "opportunity") {
            form.setFieldValue("evidence_policy", id);
            form.setFieldValue("evidence_quote", "");
            proofChange(id);
          }
        }}
        placeholder="按标题搜索并选择文件"
      />
    </Form.Item>
  );
  return (
    <>
      <Alert
        type="info"
        showIcon
        title="维护政策机会、政策关系与关系复审候选"
        description="申报批次作为政策机会的子项展示；政策效力在政策审核与修正中维护。核验政策机会时可同时查看政策全文、摘要、来源、附件与证据。"
      />
      <div className="panel-heading" style={{ marginTop: 20 }}>
        <Select
          value={kind}
          style={{ width: 220 }}
          options={[
            { value: "opportunity", label: "政策机会" },
            { value: "relation", label: "政策关系" },
            { value: "relation_review", label: "关系人工复审" },
          ]}
          onChange={(value) => {
            setKind(value);
            setPage(1);
          }}
        />
        {kind === "relation_review" && <Select
          aria-label="关系复审范围"
          value={reviewScope}
          style={{ width: 180 }}
          options={[
            { value: "current", label: "当前构建候选" },
            { value: "historical", label: "历史候选" },
            { value: "all", label: "全部候选" },
          ]}
          onChange={(value) => { setReviewScope(value); setPage(1); }}
        />}
        {kind === "relation_review" && (
          <Select
            value={reviewStatus}
            style={{ width: 180 }}
            options={[
              { value: "pending", label: `待人工复审${reviewCounts ? `（${reviewCounts.pending}）` : ""}` },
              { value: "approved", label: `已确认有关系${reviewCounts ? `（${reviewCounts.approved}）` : ""}` },
              { value: "rejected", label: `已确认无关系${reviewCounts ? `（${reviewCounts.rejected}）` : ""}` },
              { value: "superseded", label: `已由后续结果替代${reviewCounts ? `（${reviewCounts.superseded}）` : ""}` },
              { value: "all", label: `全部复审记录${reviewCounts ? `（${Object.values(reviewCounts).reduce((a, b) => a + b, 0)}）` : ""}` },
            ]}
            onChange={(value) => {
              setReviewStatus(value);
              setPage(1);
            }}
          />
        )}
        {kind !== "relation_review" && (
          <Button
            type="primary"
            onClick={() => {
              setEditing(null);
              form.resetFields();
              setOpen(true);
            }}
          >
            新增记录
          </Button>
        )}
      </div>
      {kind !== "opportunity" && <RelationBuildProgress onReviewScope={(scope) => {
        setKind("relation_review"); setReviewScope(scope); setReviewStatus("pending"); setPage(1);
      }} />}
      {records.error ? (
        <Alert type="error" title={records.error.message} />
      ) : !records.data?.items.length ? (
        <Empty description={kind === "relation_review" ? "当前筛选下暂无复审候选" : "暂无记录，请先新增并核实依据"} />
      ) : (
        records.data.items.map((record) =>
          kind === "relation_review" ? (
            <article className="source-card" key={record.id}>
              <div className="spread">
                <strong>
                  {String(record.from_title)} →{" "}
                  {taxonomy.data?.relation_kinds?.find(
                    (option) => option.value === record.proposed_kind,
                  )?.label || String(record.proposed_kind)}{" "}
                  → {String(record.to_title)}
                </strong>
                <Tag color={record.status === "pending" ? "orange" : "default"}>
                  {record.status === "pending"
                    ? "待人工复审"
                    : record.status === "approved"
                      ? "已确认有关系"
                      : record.status === "rejected"
                        ? "已确认无关系"
                        : "已由后续结果替代"}
                </Tag>
              </div>
              <Alert
                type="warning"
                showIcon
                title="待核验原因"
                description={<div style={{ whiteSpace: "pre-line" }}>{String(record.rejection_reason)}</div>}
              />
              <p style={{ marginTop: 12 }}>
                <strong>模型判断：</strong>
                {String(record.model_reason || "模型认为两份文件存在关系。")}
              </p>
              <blockquote>
                {String(record.evidence_quote || "模型未提供有效证据")}
              </blockquote>
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                <Button
                  href={safeExternalUrl(String(record.from_source_url))}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  查看关系起点原文 ↗
                </Button>
                <Button
                  href={safeExternalUrl(String(record.to_source_url))}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  查看关系终点原文 ↗
                </Button>
                {record.status === "pending" && (
                  <>
                    <Button
                      type="primary"
                      onClick={() => {
                        setEditing(record);
                        form.resetFields();
                        form.setFieldsValue({
                          from_policy: record.from_policy,
                          to_policy: record.to_policy,
                          kind: record.proposed_kind,
                          evidence_policy:
                            record.evidence_policy || record.from_policy,
                          evidence_quote: record.evidence_quote,
                        });
                        setOpen(true);
                      }}
                    >
                      确认或修正关系
                    </Button>
                    <Button
                      danger
                      loading={rejectCandidate.isPending}
                      onClick={() => rejectCandidate.mutate(record.id)}
                    >
                      确认无关系
                    </Button>
                  </>
                )}
              </div>
            </article>
          ) : (
            <article className="source-card" key={record.id}>
              <div className="spread">
                <strong>
                  {record.title ||
                    record.name ||
                    taxonomy.data?.relation_kinds?.find(
                      (option) => option.value === record.kind,
                    )?.label ||
                    "文件关系"}
                </strong>
                <Tag>
                  {
                    taxonomy.data?.verification_statuses?.find(
                      (option) => option.value === record.verification_status,
                    )?.label
                  }
                </Tag>
              </div>
              {!!record.from_policy && (
                <p className="small muted">
                  {String(record.from_title || record.from_policy)} →{" "}
                  {String(record.to_title || record.to_policy)}
                </p>
              )}
              <p>{String(record.evidence_quote || "")}</p>
              {!!record.discovery &&
                (record.discovery as { method?: string }).method ===
                  "wiki_llm" && (
                  <p className="small muted">
                    Wiki LLM 自动关系：
                    {String(
                      (record.discovery as { reason?: string }).reason ||
                        "已通过来源、版本、方向和逐字证据校验，可人工修正",
                    )}
                  </p>
                )}
              <Button
                onClick={() => {
                  setEditing(record);
                  form.resetFields();
                  form.setFieldsValue({
                    ...record,
                    starts_at: localInput(record.starts_at),
                    deadline_at: localInput(record.deadline_at),
                  });
                  setOpen(true);
                }}
              >
                修改 / 核验
              </Button>
            </article>
          ),
        )
      )}
      <Pagination
        className="pagination"
        current={page}
        total={records.data?.count || 0}
        pageSize={20}
        showSizeChanger={false}
        onChange={setPage}
      />
      <Modal
        title={
          kind === "relation_review"
            ? "确认或修正政策关系"
            : editing
              ? "修改并核验"
              : "新增结构化记录"
        }
        open={open}
        width={kind === "opportunity" ? 1280 : 760}
        onCancel={() => {
          if (!save.isPending) setOpen(false);
        }}
        onOk={() => form.submit()}
        confirmLoading={save.isPending}
        okText={kind === "relation_review" ? "确认关系成立" : "保存"}
        cancelText="取消"
      >
        <Form
          key={kind}
          form={form}
          layout="vertical"
          onFinish={(values) => save.mutate(values)}
          initialValues={{
            verification_status: "pending",
            status: "unverified",
          }}
        >
          {kind === "opportunity" && (
            <div
              style={{
                display: "grid",
                gridTemplateColumns:
                  "minmax(360px, 0.9fr) minmax(460px, 1.1fr)",
                gap: 24,
                alignItems: "start",
              }}
            >
              <div>
                {policySelect("policy", "所属政策文件")}
                <Form.Item
                  name="title"
                  label="机会名称"
                  rules={[{ required: true }, { max: 500 }]}
                >
                  <Input />
                </Form.Item>
                <Form.Item
                  name="category"
                  label="支持类别"
                  rules={[{ required: true }]}
                >
                  <Select options={taxonomy.data?.opportunity_categories} />
                </Form.Item>
                <Form.Item name="acquisition_method" label="获取方式">
                  <Input placeholder="例如：申报、认定、直接享受" />
                </Form.Item>
                <Form.Item name="eligible_subjects" label="适用主体">
                  <Select mode="tags" tokenSeparators={[",", "，", "、"]} />
                </Form.Item>
                <Form.Item name="eligible_projects" label="适用项目">
                  <Select mode="tags" tokenSeparators={[",", "，", "、"]} />
                </Form.Item>
                <Form.Item name="eligible_products" label="适用产品">
                  <Select mode="tags" tokenSeparators={[",", "，", "、"]} />
                </Form.Item>
                <Form.Item name="support_content" label="支持内容">
                  <Input.TextArea rows={3} />
                </Form.Item>
                <Form.Item name="support_method" label="支持方式">
                  <Input.TextArea rows={2} />
                </Form.Item>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: 12,
                  }}
                >
                  <Form.Item name="amount" label="明确金额">
                    <InputNumber style={{ width: "100%" }} min={0} />
                  </Form.Item>
                  <Form.Item name="amount_unit" label="金额单位">
                    <Input placeholder="万元、亿元等" />
                  </Form.Item>
                  <Form.Item name="percentage" label="支持比例">
                    <InputNumber style={{ width: "100%" }} min={0} />
                  </Form.Item>
                  <Form.Item name="max_amount" label="最高金额">
                    <InputNumber style={{ width: "100%" }} min={0} />
                  </Form.Item>
                </div>
                <Form.Item name="calculation_basis" label="计算依据">
                  <Input.TextArea rows={2} />
                </Form.Item>
                <Form.Item name="requirements" label="申报条件">
                  <Select mode="tags" tokenSeparators={[",", "，"]} />
                </Form.Item>
                <Form.Item name="exclusion_conditions" label="排除条件">
                  <Select mode="tags" tokenSeparators={[",", "，"]} />
                </Form.Item>
                <Form.Item name="prerequisites" label="前置条件">
                  <Select mode="tags" tokenSeparators={[",", "，"]} />
                </Form.Item>
                <Form.Item name="regions" label="适用地区">
                  <Select mode="tags" tokenSeparators={[",", "，", "、"]} />
                </Form.Item>
                <Form.Item name="competent_authorities" label="主管部门">
                  <Select mode="tags" tokenSeparators={[",", "，", "、"]} />
                </Form.Item>
                <Form.Item name="acceptance_authorities" label="受理部门">
                  <Select mode="tags" tokenSeparators={[",", "，", "、"]} />
                </Form.Item>
                <Form.Item name="application_channels" label="申报渠道">
                  <Select mode="tags" tokenSeparators={[",", "，", "、"]} />
                </Form.Item>
                <Form.Item name="missing_information" label="原文未明确的信息">
                  <Select mode="tags" tokenSeparators={[",", "，"]} />
                </Form.Item>
              </div>
              <div
                style={{
                  position: "sticky",
                  top: 0,
                  maxHeight: "70vh",
                  overflow: "auto",
                  padding: 16,
                  border: "1px solid var(--line)",
                  borderRadius: 12,
                  background: "var(--surface)",
                }}
              >
                <strong>核验依据：所属政策文件</strong>
                {policyDetail.isLoading ? (
                  <div style={{ padding: 36, textAlign: "center" }}>
                    <Spin />
                  </div>
                ) : policyDetail.error ? (
                  <Alert type="error" title={policyDetail.error.message} />
                ) : policyDetail.data ? (
                  <>
                    <h3>{policyDetail.data.title}</h3>
                    <p className="small muted">
                      {policyDetail.data.issuer || "发文机关待核实"} ·{" "}
                      {policyDetail.data.document_number || "无文号"} ·{" "}
                      {policyDetail.data.publication_date || "发布日期待核实"}
                    </p>
                    <p>
                      <strong>政策摘要：</strong>
                      {policyDetail.data.summary || "暂无摘要"}
                    </p>
                    {!!policyDetail.data.source_url && (
                      <Button
                        href={safeExternalUrl(policyDetail.data.source_url)}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        查看官方原文 ↗
                      </Button>
                    )}
                    {!!policyDetail.data.attachments?.length && (
                      <div style={{ marginTop: 12 }}>
                        <strong>附件：</strong>
                        {policyDetail.data.attachments.map(
                          (attachment, index) => (
                            <Button
                              key={attachment.id}
                              type="link"
                              href={safeExternalUrl(attachment.url)}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              附件 {index + 1}
                            </Button>
                          ),
                        )}
                      </div>
                    )}
                    {!!editing && Array.isArray(editing.batches) && (
                      <div style={{ marginTop: 12 }}>
                        <strong>申报批次：</strong>
                        {editing.batches.length ? (
                          editing.batches.map((batch, index) => {
                            const item = batch as Record<string, unknown>;
                            return (
                              <p
                                className="small"
                                key={String(item.id || index)}
                              >
                                {String(item.name || "未命名批次")} ·{" "}
                                {String(
                                  item.current_status ||
                                    item.status ||
                                    "待核实",
                                )}
                              </p>
                            );
                          })
                        ) : (
                          <p className="small muted">
                            原文未明确独立申报批次，不生成空批次。
                          </p>
                        )}
                      </div>
                    )}
                    <Divider />
                    <strong>政策全文</strong>
                    <div className="policy-body" style={{ marginTop: 12 }}>
                      {policyDetail.data.body || "暂无可核验正文"}
                    </div>
                  </>
                ) : (
                  <Empty description="选择政策文件后显示全文与附件" />
                )}
              </div>
            </div>
          )}
          {kind === "opportunity" && (
            <Form.Item
              name="status"
              label="机会状态"
              rules={[{ required: true }]}
            >
              <Select options={taxonomy.data?.opportunity_statuses} />
            </Form.Item>
          )}
          {kind === "relation" && (
            <>
              {policySelect("from_policy", "关系起点 A")}
              {policySelect("to_policy", "关系终点 B")}
              <Form.Item
                name="kind"
                label="关系类型（A 对 B；征求→正式为草案 A 指向正式 B）"
                rules={[{ required: true }]}
              >
                <Select options={taxonomy.data?.relation_kinds} />
              </Form.Item>
            </>
          )}
          {kind === "relation_review" && editing && (
            <>
              <Alert
                type="warning"
                showIcon
                title="待核验原因"
                description={<><div style={{ whiteSpace: "pre-line" }}>{String(editing.rejection_reason)}</div><p>可交换方向、修正类型，并从当前全文重新选取证据。</p></>}
              />
              <Form.Item
                name="from_policy"
                label="关系起点"
                rules={[{ required: true }]}
              >
                <Select
                  options={[
                    { value: editing.from_policy, label: editing.from_title },
                    { value: editing.to_policy, label: editing.to_title },
                  ]}
                />
              </Form.Item>
              <Form.Item
                name="to_policy"
                label="关系终点"
                rules={[{ required: true }]}
              >
                <Select
                  options={[
                    { value: editing.from_policy, label: editing.from_title },
                    { value: editing.to_policy, label: editing.to_title },
                  ]}
                />
              </Form.Item>
              <Form.Item
                name="kind"
                label="关系类型"
                rules={[{ required: true }]}
              >
                <Select options={taxonomy.data?.relation_kinds} />
              </Form.Item>
              <Form.Item
                name="evidence_policy"
                label="证据所在文件"
                rules={[{ required: true }]}
              >
                <Select
                  options={[
                    { value: editing.from_policy, label: editing.from_title },
                    { value: editing.to_policy, label: editing.to_title },
                  ]}
                />
              </Form.Item>
              <Form.Item
                name="evidence_quote"
                label="能够直接证明关系的逐字原文"
                rules={[{ required: true }, { min: 5 }]}
              >
                <Input.TextArea rows={6} />
              </Form.Item>
            </>
          )}
          {kind !== "relation_review" ? (
            <>
              {policySelect("evidence_policy", "证据所在文件")}
              <Form.Item
                name="evidence_version"
                label="证据版本"
                rules={[{ required: true }]}
              >
                <InputNumber min={1} />
              </Form.Item>
              <Form.Item
                name="evidence_quote"
                label="原文证据（至少5字；须能证明所填字段）"
                rules={[{ required: true }, { min: 5 }]}
              >
                <Input.TextArea rows={5} />
              </Form.Item>
              <Form.Item
                name="verification_status"
                label="核验状态"
                rules={[{ required: true }]}
              >
                <Select options={taxonomy.data?.verification_statuses} />
              </Form.Item>
            </>
          ) : null}
        </Form>
      </Modal>
    </>
  );
}
