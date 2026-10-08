"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Card, Collapse, Form, InputNumber, Select, Space, Switch, Table, Tag } from "antd";
import { api } from "@/lib/api";

type Settings = { enabled: boolean; all_organizations: boolean; organizations: string[]; organization_options: { value: string; label: string }[]; batch_size: number; daily_batches: number; daily_model_calls: number; model_calls_per_run: number; aggregation_minutes: number; retention_days: number };
type Stats = { enabled_watches: number; today_batches: number; today_model_calls: number; runs: { status: string; count: number }[]; feedback: { feedback: string; count: number }[]; notice: string };
type Observation = { observed_at: string; enabled_watches: number; runs_7d: number; completed_7d: number; p95_run_seconds: number | null; oldest_queued_at: string | null; expired_leases: number; over_24h: number; pending_labels: number; status: string; notice: string };
const labels: Record<string, string> = { queued: "等待处理", running: "处理中", completed: "已完成", failed: "失败暂停", cancelled: "已停止", useful: "有帮助", irrelevant: "不相关", missing_information: "信息不足" };

export default function RecommendationSettings() {
  const { message } = App.useApp();
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ["recommendation-settings"], queryFn: () => api<Settings>("admin/recommendation-settings"), refetchOnWindowFocus: false });
  const stats = useQuery({ queryKey: ["recommendation-statistics"], queryFn: () => api<Stats>("admin/recommendation-statistics"), refetchInterval: 30000 });
  const observations = useQuery({ queryKey: ["matching-observations"], queryFn: () => api<{ current: Observation; history: { hour: string; metrics: Observation }[] }>("admin/matching-observations"), refetchInterval: 60000 });
  const observed = observations.data?.current;
  const save = useMutation({ mutationFn: (values: Settings) => api("admin/recommendation-settings", { method: "PATCH", body: JSON.stringify(values) }), onSuccess: () => { message.success("持续匹配设置已保存"); void client.invalidateQueries({ queryKey: ["recommendation-settings"] }); }, onError: error => message.error(error.message) });
  return <Card title="持续推荐与运行管理">
    <Alert type="info" showIcon className="space-bottom" title="企业策略可以独立设置" description={<span>每轮处理量、解读次数、合并等待与保留时间可在<a href="/admin/enterprises">企业管理 → 企业配置</a>设置。此处保留平台服务开关、总资源保护和默认策略；企业允许使用后仍需客户主动开启关注。</span>} />
    <p className="muted">客户主动开启关注后才会运行。匹配解读复用“AI 模型与审核 → 企业政策匹配解读”的模型；原有订阅和手动匹配独立运行。</p>
    {settings.error && <Alert type="error" title={settings.error.message} />}
    {settings.data && <Form layout="vertical" style={{ maxWidth: 620 }} initialValues={settings.data} onFinish={values => save.mutate(values)}>
      <Form.Item name="enabled" label="启用持续匹配服务" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="all_organizations" label="向所有企业开放" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="organizations" label="指定开放企业" extra="仅在关闭“向所有企业开放”时生效。"><Select mode="multiple" optionFilterProp="label" options={settings.data.organization_options} /></Form.Item>
      <Form.Item name="batch_size" label="每批检查政策数"><InputNumber min={1} max={100} precision={0} /></Form.Item>
      <Form.Item name="daily_batches" label="全系统每天最多处理批数"><InputNumber min={1} max={2000} precision={0} /></Form.Item>
      <Form.Item name="daily_model_calls" label="全系统每天最多自动解读次数"><InputNumber min={0} max={1000} precision={0} /></Form.Item>
      <Form.Item name="model_calls_per_run" label="每轮匹配最多自动解读次数"><InputNumber min={0} max={5} precision={0} /></Form.Item>
      <Form.Item name="aggregation_minutes" label="资料修改后合并等待时间（分钟）"><InputNumber min={1} max={60} precision={0} /></Form.Item>
      <Form.Item name="retention_days" label="运行记录保留天数" extra="到期清理已结束任务和过期结果中的详细资料，保留有效结果、反馈与去重标记；不删除政策和企业画像。"><InputNumber min={7} max={365} precision={0} /></Form.Item>
      <Button type="primary" htmlType="submit" loading={save.isPending}>保存运行设置</Button>
    </Form>}
    {stats.error && <Alert type="error" title={stats.error.message} />}
    {stats.data && <div style={{ marginTop: 20 }}><h3>运行概况</h3><p>开启关注 {stats.data.enabled_watches} 个 · 今日批次 {stats.data.today_batches} · 今日解读预算使用 {stats.data.today_model_calls} 次</p><p className="muted">最近七天任务</p><Space wrap>{stats.data.runs.map(item => <Tag key={item.status}>{labels[item.status] || "其他"} {item.count}</Tag>)}</Space><p>反馈：{stats.data.feedback.map(item => `${labels[item.feedback] || "其他"} ${item.count}`).join(" · ") || "暂无"}</p><p className="small muted">{stats.data.notice}</p></div>}
    {observations.error && <Alert type="error" title={observations.error.message} />}
    {observed && <section style={{ marginTop: 24 }}><h3>持续运行观察</h3>
      <Alert showIcon type={observed.expired_leases || observed.over_24h ? "warning" : "info"} title={observed.status === "no_data" ? "尚未有持续关注与运行记录，暂不能评价运行效果" : observed.expired_leases || observed.over_24h ? "有任务需要关注，请检查处理队列与预算" : "正在记录运行情况"} description={observed.notice} />
      <p>最近七天完成 {observed.completed_7d} 轮 · 95% 轮次完成耗时 {observed.p95_run_seconds === null ? "暂无数据" : `${Math.ceil(observed.p95_run_seconds)} 秒`} · 待核验样本 {observed.pending_labels} 份</p>
      <p>租约已超时 {observed.expired_leases} 轮 · 超过 24 小时未完成 {observed.over_24h} 轮{observed.oldest_queued_at ? ` · 最早排队 ${new Date(observed.oldest_queued_at).toLocaleString("zh-CN")}` : ""}</p>
      <Collapse items={[{ key: "history", label: "每小时观察记录（保留 90 天，展示最近 48 次）", children: <Table size="small" rowKey="hour" dataSource={observations.data?.history || []} pagination={{ pageSize: 8 }} columns={[
        { title: "时间", dataIndex: "hour", render: (value: string) => new Date(value).toLocaleString("zh-CN") },
        { title: "开启关注", render: (_, row) => row.metrics.enabled_watches },
        { title: "七天完成轮数", render: (_, row) => row.metrics.completed_7d },
        { title: "租约超时", render: (_, row) => row.metrics.expired_leases },
        { title: "超过 24 小时", render: (_, row) => row.metrics.over_24h },
      ]} /> }]} />
    </section>}
  </Card>;
}
