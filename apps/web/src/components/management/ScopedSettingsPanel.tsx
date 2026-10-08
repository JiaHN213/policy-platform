"use client";

import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Card, Form, InputNumber, Select, Space, Spin, Tag } from "antd";
import { api } from "@/lib/api";

type Value = number | boolean | null;
type Field = { key: string; label: string; group: string; kind: "number" | "boolean"; min?: number; max?: number };
type Settings = { revision: number; fields: Field[]; defaults: Record<string, Value>; overrides: Record<string, Value>; effective: Record<string, Value>; usage?: { normal: number; failed: number; maintenance: number } };
const label = (value: Value) => typeof value === "boolean" ? value ? "允许" : "不允许" : String(value);

export default function ScopedSettingsPanel({ userId, profileId }: { userId?: number; profileId?: string }) {
  const [form] = Form.useForm();
  const { message } = App.useApp();
  const client = useQueryClient();
  const endpoint = profileId ? `admin/enterprise-settings/${profileId}` : `admin/user-settings/${userId}`;
  const queryKey = ["scoped-settings", endpoint];
  const query = useQuery({ queryKey, queryFn: () => api<Settings>(endpoint), refetchOnWindowFocus: false });
  useEffect(() => {
    if (!query.data) return;
    form.resetFields();
    form.setFieldsValue(Object.fromEntries(query.data.fields.map(field => {
      const value = query.data!.overrides[field.key];
      return [field.key, field.kind === "boolean" ? typeof value === "boolean" ? value ? "yes" : "no" : "" : value ?? undefined];
    })));
  }, [query.data, form]);
  const save = useMutation({
    mutationFn: (overrides: Record<string, Value>) => api<Settings>(endpoint, { method: "PATCH", body: JSON.stringify({ revision: query.data!.revision, overrides }) }),
    onSuccess: data => { client.setQueryData(queryKey, data); message.success("独立配置已保存"); },
    onError: error => message.error(error.message),
  });
  if (query.isLoading) return <Spin />;
  if (query.error) return <Alert type="error" title={query.error.message} action={<Button onClick={() => void query.refetch()}>刷新</Button>} />;
  if (!query.data) return null;
  const data = query.data;
  const groups = [...new Set(data.fields.map(field => field.group))];
  return <Card title={profileId ? "企业独立配置" : "账号使用配置"}>
    <p className="muted">只对当前{profileId ? "企业（含其成员与项目）" : "用户"}生效。数字留空或选择“使用默认”可继承平台默认值；保存不会启动任务或覆盖已确认资料。</p>
    {profileId && <Alert type="info" className="space-bottom" title="平台提供服务，企业单独设置策略" description="模型、搜索连接及平台资源保护由系统配置管理。允许持续推荐后，客户仍需主动开启关注；平台关闭服务或总资源达到上限时不会继续处理。" />}
    {data.usage && <Space wrap className="space-bottom"><Tag>最近24小时正常计次 {data.usage.normal}</Tag><Tag>失败不计次 {data.usage.failed}</Tag><Tag>维护不计次 {data.usage.maintenance}</Tag><Tag color="cyan">剩余正常次数 {Math.max(0, Number(data.effective.daily_limit) - data.usage.normal)}</Tag></Space>}
    <Form form={form} layout="vertical" style={{ maxWidth: 680 }} onFinish={values => save.mutate(Object.fromEntries(data.fields.map(field => [field.key, field.kind === "boolean" ? values[field.key] === "yes" ? true : values[field.key] === "no" ? false : null : values[field.key] ?? null])))}>
      {groups.map(group => <section key={group}><h3>{group}</h3>{data.fields.filter(field => field.group === group).map(field => <Form.Item key={field.key} name={field.key} label={field.label} extra={`平台默认：${label(data.defaults[field.key])}；当前生效：${label(data.effective[field.key])}`}>
        {field.kind === "boolean" ? <Select options={[{ value: "", label: "使用默认" }, { value: "yes", label: "允许" }, { value: "no", label: "不允许" }]} /> : <InputNumber min={field.min} max={field.max} precision={0} placeholder="使用默认" style={{ width: 220 }} />}
      </Form.Item>)}</section>)}
      <Space wrap><Button type="primary" htmlType="submit" loading={save.isPending}>保存当前{profileId ? "企业" : "用户"}配置</Button><Button loading={save.isPending} onClick={() => save.mutate({})}>恢复平台默认</Button><Button onClick={() => void query.refetch()}>刷新</Button></Space>
    </Form>
  </Card>;
}
