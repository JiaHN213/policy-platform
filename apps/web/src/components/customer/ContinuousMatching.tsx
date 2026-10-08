"use client";

import ReadableText from "@/components/policy/ReadableText";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Card, Collapse, Empty, Form, Pagination, Select, Space, Switch, Tag } from "antd";
import { type Page } from "@/lib/api";
import { accountApi } from "@/lib/account-scope";

type Watch = { id: string; profile: string; project: string | null; enabled: boolean; view: string; interval_hours: number; ai_explanations: boolean; consent_version: number; message: string; next_run_at: string | null;
  latest_run: { status: string; scanned: number; matched: number; model_calls: number; message: string } | null };
type Recommendation = { id: string; policy: string; title: string; feedback: string; result: { level_label: string; recommendation_label: string; reasons: string[]; gaps: string[]; analysis_message: string; analysis?: { points: { text: string; quote: string }[]; notice: string } | null } };

function WatchForm({ userId, watch, profile, project }: { userId?: number; watch?: Watch; profile: string; project: string | null }) {
  const api = accountApi(userId);
  const { message } = App.useApp();
  const client = useQueryClient();
  const save = useMutation({ mutationFn: (values: Record<string, unknown>) => api("policy-watches/configure", { method: "POST", body: JSON.stringify({ ...values, profile, project }) }),
    onSuccess: () => { message.success("持续匹配设置已保存"); void client.invalidateQueries({ queryKey: ["policy-watches", userId] }); void client.invalidateQueries({ queryKey: ["watch-results"] }); }, onError: error => message.error(error.message) });
  return <Form layout="inline" initialValues={watch || { enabled: false, view: "opportunities", interval_hours: 24, ai_explanations: false }} onFinish={values => save.mutate(values)} style={{ gap: 16 }}>
    <Form.Item name="enabled" label="持续关注" valuePropName="checked"><Switch /></Form.Item>
    <Form.Item name="view" label="范围"><Select style={{ width: 130 }} options={[{ value: "opportunities", label: "政策机会" }, { value: "policies", label: "政策文件" }]} /></Form.Item>
    <Form.Item name="interval_hours" label="更新频率"><Select style={{ width: 130 }} options={[{ value: 1, label: "每小时" }, { value: 6, label: "每 6 小时" }, { value: 24, label: "每天" }]} /></Form.Item>
    <Form.Item name="ai_explanations" label="附带适用分析" valuePropName="checked"><Switch /></Form.Item>
    <Button type="primary" htmlType="submit" loading={save.isPending}>保存关注设置</Button>
  </Form>;
}

export default function ContinuousMatching({ userId, profile, projects, openPolicy }: { userId?: number; profile: string; projects: { id: string; name: string }[]; openPolicy: (id: string) => void }) {
  const [project, setProject] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const api = accountApi(userId);
  const { message } = App.useApp();
  const client = useQueryClient();
  const watches = useQuery({ queryKey: ["policy-watches", userId], queryFn: () => api<Page<Watch>>("policy-watches"), refetchInterval: 15000 });
  const watch = watches.data?.items.find(w => w.profile === profile && w.project === project);
  const results = useQuery({ queryKey: ["watch-results", userId, watch?.id, page], queryFn: () => api<Page<Recommendation>>(`policy-watches/results?watch=${watch!.id}&page=${page}`), enabled: !!watch?.enabled, refetchInterval: 60000 });
  const feedback = useMutation({ mutationFn: ({ id, value }: { id: string; value: string }) => api(`policy-watches/feedback/${id}`, { method: "POST", body: JSON.stringify({ feedback: value }) }),
    onSuccess: () => { message.success("感谢反馈，已记录"); void client.invalidateQueries({ queryKey: ["watch-results"] }); }, onError: error => message.error(error.message) });
  return <Card title="按企业／项目持续关注" loading={watches.isLoading}>
    <p className="muted">首次建立当前匹配结果，后续有实质变化时通过站内通知提醒。通知频率沿用“消息通知”的设置；关闭关注后停止处理与后续提醒。</p>
    <Select aria-label="持续匹配对象" value={project || "company"} style={{ minWidth: 220, marginBottom: 20 }} options={[{ value: "company", label: "企业整体" }, ...projects.map(p => ({ value: p.id, label: p.name }))]} onChange={value => { setProject(value === "company" ? null : value); setPage(1); }} />
    {watches.error ? <Alert type="error" title={watches.error.message} /> : <WatchForm userId={userId} key={`${profile}:${project}:${watch?.consent_version || 0}`} watch={watch} profile={profile} project={project} />}
    <p className="small muted">开启 AI 解读即同意将已确认画像、所选项目和政策片段发送至平台配置的匹配模型；不进行额外网页搜索。受额度限制的结果仍可查看规则依据，不作申报资格认证。</p>
    {results.error && <Alert type="error" title={results.error.message} />}
    {watch?.enabled && !results.data?.items.length && <Empty description="暂无新的推荐结果，可到首页查看相关政策" />}
    {watch?.enabled && results.data?.items.map(item => <Card key={item.id} size="small" style={{ marginTop: 16 }} title={<Button type="link" onClick={() => openPolicy(item.policy)} style={{ whiteSpace: "normal", textAlign: "left", height: "auto" }}>{item.title}</Button>}>
      <Space wrap><Tag>{item.result.level_label}</Tag><Tag>{item.result.recommendation_label}</Tag></Space>
      {item.result.reasons.map((reason, i) => <p key={i}>{reason}</p>)}
      <Collapse ghost size="small" items={[{ key: "details", label: "查看依据与待核对事项", children: <>
        {item.result.gaps.map((gap, i) => <p key={i}>{gap}</p>)}
        {item.result.analysis?.points.map((point, i) => <div className="reading-evidence" key={i}><ReadableText text={point.text} /><blockquote className="reading-quote"><span className="reading-caption">原文依据</span><ReadableText text={point.quote} previewChars={320} expandLabel="展开原文依据" /></blockquote></div>)}
        <p className="muted">{item.result.analysis?.notice}</p><Button onClick={() => openPolicy(item.policy)}>核对政策全文与附件</Button>
      </> }]} />
      <Space wrap>{[{ value: "useful", label: "有帮助" }, { value: "irrelevant", label: "不相关" }, { value: "missing_information", label: "信息不足" }].map(option => <Button key={option.value} size="small" type={item.feedback === option.value ? "primary" : "default"} loading={feedback.isPending} onClick={() => feedback.mutate({ id: item.id, value: option.value })}>{option.label}</Button>)}</Space>
    </Card>)}
    {watch?.enabled && !!results.data?.count && <Pagination style={{ marginTop: 16 }} current={page} pageSize={20} total={results.data.count} showSizeChanger={false} onChange={setPage} />}
  </Card>;
}
