"use client";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
  Empty,
  Input,
  Modal,
  Pagination,
  Popconfirm,
  Space,
  Spin,
  Tag,
} from "antd";
import { accountApi } from "@/lib/account-scope";
import ReadableText from "@/components/policy/ReadableText";
import type { Options } from "@/components/customer/EnterprisePanel";

type Item = {
  id: string;
  kind: string;
  name: string;
  status: string;
  created_at: string;
  quota_category?: string;
};
const kinds: Record<string, string> = {
  company: "企业资料",
  project: "项目资料",
  explanation: "适用分析",
  workflow: "综合匹配",
};
const labels: Record<string, string> = {
  candidates: "企业候选",
  name: "名称",
  data: "资料",
  business_summary: "主营业务",
  province: "省份",
  city: "城市",
  website: "官网",
  credit_code: "信用代码",
  capabilities: "能力与资质线索",
  history_projects: "历史业绩",
  business_domains: "业务领域",
  direction_tags: "技术方向",
  points: "分析说明",
  text: "说明",
  quote: "原文依据",
  notice: "提示",
  warnings: "需要留意",
  description: "项目介绍",
  conditions: "条件核对",
  opportunities: "政策机会",
  title: "名称",
  checks: "逐项核对",
  condition: "条件",
  label: "结论",
  reason: "依据说明",
  gaps: "待补充信息",
  additional_conditions: "其他条件",
  items: "相关结果",
  interest_regions: "关注地区",
  eligible_subjects: "适用对象",
  support_content: "支持内容",
  requirements: "申报要求",
};
function ResultText({
  value,
  names,
  values,
  depth = 0,
}: {
  value: unknown;
  names: Record<string, string>;
  values: Record<string, string>;
  depth?: number;
}) {
  if (value == null || depth > 7) return null;
  if (typeof value === "string" || typeof value === "number")
    return <ReadableText text={values[String(value)] || String(value)} />;
  if (Array.isArray(value))
    return (
      <>
        {value.map((item, i) => (
          <div className="space-bottom" key={i}>
            <ResultText
              value={item}
              names={names}
              values={values}
              depth={depth + 1}
            />
          </div>
        ))}
      </>
    );
  if (typeof value === "object")
    return (
      <>
        {Object.entries(value)
          .filter(([key]) => key in names)
          .map(([key, item]) => (
            <section className="space-bottom" key={key}>
              <strong>{names[key]}</strong>
              <ResultText
                value={item}
                names={names}
                values={values}
                depth={depth + 1}
              />
            </section>
          ))}
      </>
    );
  return null;
}
export default function SavedResults({ userId, profileId }: { userId?: number; profileId?: string }) {
  const [maintenanceRun, setMaintenanceRun] = useState<string>();
  const [maintenanceReason, setMaintenanceReason] = useState("");
  const api = accountApi(userId);
  const { message } = App.useApp();
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string>();
  const options = useQuery({
    queryKey: ["enterprise-options", userId],
    queryFn: () => api<Options>("enterprises/options"),
  });
  const names = {
    ...labels,
    ...options.data?.fields,
    evidence: "字段依据",
    identity_evidence: "企业身份依据",
    sources: "资料来源",
    url: "来源地址",
    retrieved_at: "获取时间",
    material: "资料名称",
    profile_facts: "已确认的企业资料",
    source: "政策依据",
    source_name: "来源名称",
  };
  const values = Object.fromEntries(
    Object.values(options.data?.tags || {})
      .flat()
      .map((option) => [option.value, option.label]),
  );
  const query = useQuery({
    queryKey: ["saved-results", userId, profileId, page],
    queryFn: () =>
      api<{ count: number; items: Item[] }>(
        `enterprise-research/saved-results?page=${page}${profileId ? `&profile_id=${profileId}` : ""}`,
      ),
    refetchInterval: 15000,
  });
  const detail = useQuery({
    queryKey: ["saved-result", userId, profileId, selected],
    queryFn: () =>
      api<{
        kind: string;
        result: {
          items?: { id: string; title: string }[];
          gap_fill?: { can_open: boolean; run_id: string };
        };
        stale_reason: string;
      }>(`enterprise-research/${selected}/saved-result`),
    enabled: !!selected,
  });
  const change = useMutation({
    mutationFn: ({ id, remove, maintenance }: { id: string; remove?: boolean; maintenance?: boolean }) =>
      api(`enterprise-research/${id}${remove ? "" : "/regenerate"}`, {
        method: remove ? "DELETE" : "POST",
        ...(maintenance ? { body: JSON.stringify({ maintenance: true, maintenance_reason: maintenanceReason.trim() }) } : {}),
      }),
    onSuccess: (_, input) => {
      message.success(
        input.remove
          ? "已删除分析结果，已确认的企业资料不受影响。"
          : "已提交，完成后可在这里查看新结果。",
      );
      void query.refetch();
      setMaintenanceRun(undefined);
      setMaintenanceReason("");
    },
    onError: (error) => message.error(error.message),
  });
  return (
    <>
      <Modal title="系统维护重跑" open={!!maintenanceRun} onCancel={() => setMaintenanceRun(undefined)} okText="提交维护重跑" cancelText="取消" confirmLoading={change.isPending} okButtonProps={{ disabled: !maintenanceReason.trim() }} onOk={() => maintenanceRun && change.mutateAsync({ id: maintenanceRun, maintenance: true })}>
        <p>仅用于修复或诊断，保留旧结果与维护记录，不占用客户的正常整理次数。</p>
        <Input.TextArea aria-label="维护原因" placeholder="请填写具体维护原因" maxLength={300} showCount value={maintenanceReason} onChange={event => setMaintenanceReason(event.target.value)} />
      </Modal>
      <p className="muted">
        删除分析结果不会删除已确认的企业画像。重新生成会使用当前资料和平台配置的模型。
        修复或诊断请使用“维护重跑”，填写原因后单独统计，不占用客户次数。
      </p>
      {query.error && <Alert type="error" title={query.error.message} />}
      {query.isLoading && <Spin />}
      {query.data?.items.map((item) => (
        <Card className="space-bottom" key={item.id} title={item.name}>
          <Space wrap>
            <Tag>{kinds[item.kind] || "分析资料"}</Tag>
            {item.quota_category === "maintenance" && <Tag color="blue">维护记录</Tag>}
            <span>{new Date(item.created_at).toLocaleString("zh-CN")}</span>
            <Tag>
              {item.status === "completed"
                ? "已保存"
                : ["queued", "running", "waiting"].includes(item.status)
                  ? "尚未完成"
                  : "需要重新整理"}
            </Tag>
          </Space>
          <Space wrap className="space-bottom">
            <Button onClick={() => setSelected(item.id)}>查看结果</Button>
            <Popconfirm
              title="基于当前资料重新生成？"
              description="会调用平台配置的模型；旧结果保留。"
              onConfirm={() => change.mutateAsync({ id: item.id })}
            >
              <Button
                disabled={["queued", "running", "waiting"].includes(
                  item.status,
                )}
              >
                重新生成
              </Button>
            </Popconfirm>
            <Button type="primary" disabled={["queued", "running", "waiting"].includes(item.status)} onClick={() => { setMaintenanceRun(item.id); setMaintenanceReason(""); }}>维护重跑</Button>
            <Popconfirm
              title="删除这份分析结果？"
              onConfirm={() =>
                change.mutateAsync({ id: item.id, remove: true })
              }
            >
              <Button
                danger
                disabled={["queued", "running", "waiting"].includes(
                  item.status,
                )}
              >
                删除
              </Button>
            </Popconfirm>
          </Space>
        </Card>
      ))}
      {query.data && !query.data.items.length && (
        <Empty description="此范围暂无分析记录；维护重跑需要先有一份资料整理或匹配任务。" />
      )}
      <Pagination
        current={page}
        total={query.data?.count || 0}
        pageSize={20}
        showSizeChanger={false}
        onChange={setPage}
      />
      <Drawer
        title="已保存的分析结果"
        size="large"
        open={!!selected}
        onClose={() => setSelected(undefined)}
      >
        {detail.isLoading ? (
          <Spin />
        ) : detail.error ? (
          <Alert type="error" title={detail.error.message} />
        ) : (
          <>
            {detail.data?.stale_reason && (
              <Alert
                type="warning"
                title="这是一份历史结果"
                description={detail.data.stale_reason}
              />
            )}
            <ResultText
              value={detail.data?.result}
              names={names}
              values={values}
            />
            {detail.data?.kind === "workflow" && (
              <Space orientation="vertical">
                {detail.data.result.items?.map((item) => (
                  <Button key={item.id} onClick={() => setSelected(item.id)}>
                    查看解读：{item.title}
                  </Button>
                ))}
                {detail.data.result.gap_fill?.can_open && (
                  <Button
                    onClick={() =>
                      setSelected(detail.data!.result.gap_fill!.run_id)
                    }
                  >
                    查看补充的企业资料
                  </Button>
                )}
              </Space>
            )}
            {detail.data && !Object.keys(detail.data.result || {}).length && (
              <Empty description="暂未生成可查看的结果" />
            )}
          </>
        )}
      </Drawer>
    </>
  );
}
