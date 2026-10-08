"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Card, Form, Input, InputNumber, Select, Space, Switch } from "antd";
import { api } from "@/lib/api";

type Settings = { workflow_gap_fill: boolean; workflow_concurrency: number; workflow_cache_hours: number; workflow_retry_limit: number; workflow_daily_calls: number; workflow_max_policies: number; workflow_max_calls: number; workflow_max_seconds: number; organization_options: { value: string; label: string }[]; agent_enabled: boolean; agent_all_organizations: boolean; agent_organizations: string[]; agent_max_reads: number; agent_max_calls: number; agent_max_seconds: number; enabled: boolean; provider: string; searxng_url: string; has_api_key: boolean; max_sources: number; daily_limit: number };
type TestResult = { connected: boolean; message: string; result_count?: number; unavailable_engines?: number; items?: { title: string; url: string }[] };

export default function EnterpriseResearchSettings() {
  const [form] = Form.useForm();
  const client = useQueryClient();
  const { message } = App.useApp();
  const provider = Form.useWatch("provider", form);
  const allOrganizations = Form.useWatch("agent_all_organizations", form);
  const [dirty, setDirty] = useState(false);
  const [query, setQuery] = useState("南宁 水务 企业");
  const [testResult, setTestResult] = useState<TestResult>();
  const settings = useQuery({ queryKey: ["enterprise-search-settings"], queryFn: () => api<Settings>("admin/enterprise-research-settings") });
  const save = useMutation({ mutationFn: (values: Record<string, unknown>) => api<Settings>("admin/enterprise-research-settings", { method: "PATCH", body: JSON.stringify(values) }), onSuccess: value => { client.setQueryData(["enterprise-search-settings"], value); form.setFieldValue("api_key", ""); setDirty(false); setTestResult(undefined); void client.invalidateQueries({ queryKey: ["enterprise-options"] }); message.success("企业资料搜索设置已保存。"); }, onError: (error: Error) => message.error(error.message) });
  const test = useMutation({ mutationFn: (mode: "connection" | "search") => api<TestResult>("admin/enterprise-research-settings", { method: "POST", body: JSON.stringify({ mode, query: query.trim() || "南宁 水务 企业" }) }), onSuccess: setTestResult, onError: (error: Error) => { setTestResult(undefined); message.error(error.message); } });
  useEffect(() => { if (settings.data) form.setFieldsValue({ ...settings.data, api_key: "" }); }, [settings.data, form]);
  return <Card title="企业资料服务与平台默认值" loading={settings.isLoading}>
    <Alert type="info" showIcon className="space-bottom" title="个别账号或企业的设置请在管理页面修改" description={<span>用户额度在<a href="/admin/users">用户管理 → 使用配置</a>设置；企业整理与匹配参数在<a href="/admin/enterprises">企业管理 → 企业配置</a>设置。下面的业务参数作为没有独立配置时的默认值。</span>} />
    <p className="muted">供客户输入企业名称后自动生成画像草稿。模型请在“AI 模型与审核”中设置“企业与项目资料提取”。</p>
    {settings.error && <Alert type="error" title={settings.error.message} />}
    <Alert type="info" showIcon title="本地 SearXNG 无需搜索密钥。企业也可选择官网、上传介绍或粘贴简介。" description="此处开关只控制按企业名称联网搜索。私有介绍和项目描述不会发送给搜索服务；生成资料仍需企业确认。" />
    <Form form={form} layout="vertical" onValuesChange={() => { setDirty(true); setTestResult(undefined); }} onFinish={values => save.mutate(values)} style={{ marginTop: 20, maxWidth: 620 }}>
      <Form.Item name="enabled" label="开启联网查资料" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="provider" label="搜索服务" rules={[{ required: true }]}><Select options={[{ value: "searxng", label: "SearXNG（本地部署，无需搜索密钥）" }, { value: "tavily", label: "Tavily（返回网页正文或摘要）" }, { value: "brave", label: "Brave Search（返回搜索摘要）" }]} /></Form.Item>
      {provider === "searxng" ? <Form.Item name="searxng_url" label="SearXNG 服务地址" rules={[{ required: true }]} extra="Docker 内使用 http://searxng:8080。其他地址需先由运维加入部署配置。"><Input placeholder="http://searxng:8080" /></Form.Item>
        : <Form.Item name="api_key" label="搜索服务 API 密钥" extra={settings.data?.has_api_key ? "密钥已保存，留空继续使用；切换到其他 API 服务时需填写新密钥。" : "从搜索服务商获取密钥后填写，保存后不会回显。"}><Input.Password autoComplete="new-password" /></Form.Item>}
      <Form.Item name="max_sources" label="每次最多参考网页数" rules={[{ required: true }]}><InputNumber min={2} max={8} precision={0} /></Form.Item>
      <Form.Item name="daily_limit" label="每位用户24小时内最多使用 AI 整理次数" extra="企业资料、项目整理、单份解读及自动匹配主任务共用额度。处理中先预占，失败后释放；维护重跑单独统计，子任务不重复计次。成功与主动停止的正常任务计次；按最近24小时滚动计算，复用已有结果不新增次数。" rules={[{ required: true }]}><InputNumber min={1} max={100} precision={0} /></Form.Item>
      <h3>AI 资料补查</h3>
      <p className="muted">按缺口选择已发现网页或材料片段，生成草稿后仍由企业确认。</p>
      <Form.Item name="agent_enabled" label="开启有限补查" valuePropName="checked"><Switch /></Form.Item>
      <Form.Item name="agent_all_organizations" label="向所有企业开放" valuePropName="checked" extra="包含现有企业及新用户首次整理企业资料，无需逐个添加。"><Switch /></Form.Item>
      {!allOrganizations && <Form.Item name="agent_organizations" label="指定企业"><Select mode="multiple" allowClear optionFilterProp="label" options={settings.data?.organization_options || []} placeholder="选择可以使用补查的企业" /></Form.Item>}
      <Form.Item name="agent_max_reads" label="每次最多读取资料次数" extra="含初次读取；网页内部仍会按规则检查访问许可及跳转。"><InputNumber min={1} max={5} precision={0} /></Form.Item>
      <Form.Item name="agent_max_calls" label="每次最多调用模型次数"><InputNumber min={2} max={10} precision={0} /></Form.Item>
      <Form.Item name="agent_max_seconds" label="补查时间预算（秒）" extra="达到预算后不再发起新步骤；正在进行的请求会先结束，最多480秒预算。"><InputNumber min={60} max={480} precision={0} /></Form.Item>
      <h3>自动匹配与解读</h3>
      <p className="muted">确认画像后或手动启动，逐份解读相关政策。以下预算由整个流程共享，恢复不会重置。</p>
      <Form.Item name="workflow_gap_fill" label="按资料缺口自动补查" valuePropName="checked" extra="使用企业原先选择的资料方式，仅生成缺失字段的有据建议，确认前不应用。"><Switch /></Form.Item>
      <Form.Item name="workflow_concurrency" label="每个流程最多并行解读份数" extra="还受后台任务进程及模型共享并发限制；本地小模型建议从1开始。"><InputNumber min={1} max={3} precision={0} /></Form.Item>
      <Form.Item name="workflow_cache_hours" label="有效解读复用时长（小时）" extra="仅复用本人相同画像、项目、政策与配置版本的结果；0表示关闭。"><InputNumber min={0} max={72} precision={0} /></Form.Item>
      <Form.Item name="workflow_retry_limit" label="临时故障自动重试次数" extra="仅重试超时、暂时断连和服务繁忙；证据或资料问题不反复请求。"><InputNumber min={0} max={3} precision={0} /></Form.Item>
      <Form.Item name="workflow_daily_calls" label="每位用户每天自动匹配调用上限" extra="不同自动匹配主任务共用，包含资料补查和失败请求；次日恢复额度。"><InputNumber min={1} max={500} precision={0} /></Form.Item>
      <Form.Item name="workflow_max_policies" label="每次最多自动解读政策数"><InputNumber min={1} max={10} precision={0} /></Form.Item>
      <Form.Item name="workflow_max_calls" label="整个流程最多模型请求次数" extra="失败请求和重试也计入；建议为所选政策数预留少量重试额度。"><InputNumber min={1} max={20} precision={0} /></Form.Item>
      <Form.Item name="workflow_max_seconds" label="累计分析时间预算（秒）" extra="不计排队时间；达到预算后不再启动新分析，已发出的请求可能继续结束。"><InputNumber min={60} max={900} precision={0} /></Form.Item>
      <Space wrap><Button type="primary" htmlType="submit" loading={save.isPending}>保存设置</Button>{settings.data?.has_api_key && <Button danger loading={save.isPending} onClick={() => save.mutate({ clear_api_key: true })}>{settings.data.provider === "searxng" ? "清除备用密钥" : "清除密钥并停用搜索"}</Button>}</Space>
    </Form>
    {provider === "searxng" && <div style={{ marginTop: 24, maxWidth: 620 }}>
      <p className="muted">测试已保存的配置。连接成功只代表服务可达，测试搜索可以检查上游引擎是否返回结果。</p>
      <Space.Compact style={{ width: "100%", marginBottom: 12 }}>
        <Input aria-label="搜索测试关键词" value={query} maxLength={200} onChange={event => setQuery(event.target.value)} placeholder="输入企业名称或公开关键词" />
        <Button disabled={dirty || save.isPending || settings.data?.provider !== "searxng"} loading={test.isPending} onClick={() => test.mutate("search")}>测试搜索</Button>
      </Space.Compact>
      <Button disabled={dirty || save.isPending || settings.data?.provider !== "searxng"} loading={test.isPending} onClick={() => test.mutate("connection")}>测试连接</Button>
      {dirty && <p className="muted">请先保存修改，再进行测试。</p>}
      {testResult && <Alert style={{ marginTop: 12 }} showIcon type={testResult.result_count === 0 ? "warning" : "success"} title={testResult.message} description={<div>
        {!!testResult.unavailable_engines && <p>有 {testResult.unavailable_engines} 个引擎暂时不可用。</p>}
        {testResult.items?.map((item, index) => <p key={`${item.url}-${index}`} style={{ marginBottom: 6 }}>{/^https?:\/\//i.test(item.url) ? <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title || item.url}</a> : item.title}</p>)}
      </div>} />}
    </div>}
  </Card>;
}
