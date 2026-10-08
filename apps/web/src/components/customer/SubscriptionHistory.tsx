"use client";

import { accountApi } from "@/lib/account-scope";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Modal, Popconfirm, Skeleton, Space } from "antd";

type Change = { id: string; before: Record<string, unknown>; after: Record<string, unknown>; reason: string; created_at: string };
const labels: Record<string, string> = { name: "订阅名称", business_domain: "业务领域", direction_tag: "技术方向", interest_regions: "关注地区", active: "启用状态", target_view: "政策视角", keywords: "关键词", topic: "主题", document_type: "文件类型", region: "发布地区", province: "发布省份", city: "发布城市", geographic_level: "发布层级", validity_status: "政策效力", opportunity_category: "机会类别", opportunity_status: "机会状态", acquisition_method: "获取方式", eligible_keywords: "对象关键词", authority_keywords: "部门关键词", has_deadline: "截止日期筛选", deadline_within_days: "截止天数" };

export default function SubscriptionHistory({ userId, id, close }: { userId?: number; id: string; close: () => void }) {
  const api = accountApi(userId);
  const client = useQueryClient();
  const { message } = App.useApp();
  const query = useQuery({ queryKey: ["subscription-history", userId, id], queryFn: () => api<{ revision: number; items: Change[] }>(`subscriptions/${id}/history`) });
  const taxonomy = useQuery({ queryKey: ["taxonomy"], queryFn: () => api<Record<string, { value: string; label: string }[]>>("taxonomies") });
  const text = (value: unknown): string => {
    if (value === true) return "开启"; if (value === false) return "关闭"; if (Array.isArray(value)) return value.map(text).join("、") || "不限";
    if (value == null || value === "") return "不限";
    const known = Object.values(taxonomy.data || {}).flat().find(item => typeof item === "object" && item.value === value);
    return known?.label || ({ all: "政策与机会", policy: "政策文件", opportunity: "政策机会" }[String(value)] ?? String(value));
  };
  const restore = useMutation({ mutationFn: (change: Change) => api(`subscriptions/${id}/restore`, { method: "POST", body: JSON.stringify({ change_id: change.id, revision: query.data?.revision }) }), onSuccess: () => { message.success("已恢复，并转为手工管理以防自动覆盖"); void client.invalidateQueries({ queryKey: ["subscriptions"] }); void query.refetch(); }, onError: (error: Error) => message.error(error.message) });
  return <Modal open title="订阅变更记录" footer={null} onCancel={close} width={680}>{query.isLoading ? <Skeleton /> : query.error ? <Alert type="error" title={query.error.message} /> : query.data?.items.length ? query.data.items.map(change => <div key={change.id} className="space-bottom"><Space><strong>{change.reason}</strong><span className="small muted">{new Date(change.created_at).toLocaleString("zh-CN")}</span></Space>{Object.keys(change.after).filter(key => JSON.stringify(change.before[key]) !== JSON.stringify(change.after[key])).map(key => <p key={key}>{labels[key] || "订阅条件"}：{text(change.before[key])} → {text(change.after[key])}</p>)}{!!Object.keys(change.before).length && <Popconfirm title="恢复到这次变更前？" description="恢复后由你手工管理，不会被画像自动覆盖。" onConfirm={() => restore.mutateAsync(change)}><Button size="small" loading={restore.isPending}>恢复变更前设置</Button></Popconfirm>}</div>) : <p>暂无变更记录。</p>}</Modal>;
}
