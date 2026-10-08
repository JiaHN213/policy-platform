"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Card, Form, Select, Skeleton, Switch } from "antd";
import { accountApi } from "@/lib/account-scope";

type Preferences = { update_mode: string; digest_hour: number; deadline_enabled: boolean; deadline_days: number[] };

export default function NotificationSettings({ userId }: { userId?: number } = {}) {
  const api = accountApi(userId);
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const query = useQuery({ queryKey: ["notification-preferences", userId], queryFn: () => api<Preferences>("notifications/preferences") });
  const save = useMutation({ mutationFn: (values: Preferences) => api<Preferences>("notifications/preferences", { method: "PATCH", body: JSON.stringify(values) }), onSuccess: data => { queryClient.setQueryData(["notification-preferences", userId], data); message.success("提醒设置已保存"); }, onError: (error: Error) => message.error(error.message) });
  return <Card size="small" title="站内提醒设置" className="space-bottom">{query.isLoading ? <Skeleton active /> : query.error ? <Alert type="warning" title={query.error.message} /> : <Form key={JSON.stringify(query.data)} layout="inline" initialValues={query.data} onFinish={values => save.mutate(values)}>
    <Form.Item name="update_mode" label="普通更新"><Select style={{ width: 130 }} options={[{ value: "daily", label: "每日汇总" }, { value: "instant", label: "即时通知" }]} /></Form.Item>
    <Form.Item name="digest_hour" label="汇总时间"><Select style={{ width: 110 }} options={Array.from({ length: 24 }, (_, value) => ({ value, label: `${String(value).padStart(2, "0")}:00` }))} /></Form.Item>
    <Form.Item name="deadline_enabled" label="截止提醒" valuePropName="checked"><Switch /></Form.Item>
    <Form.Item name="deadline_days" label="提前天数" rules={[{ required: true }]}><Select mode="multiple" style={{ minWidth: 160 }} options={[1, 3, 7, 14, 30, ...(query.data?.deadline_days || [])].filter((v, i, a) => a.indexOf(v) === i).sort((a, b) => a - b).map(value => ({ value, label: `${value}天` }))} /></Form.Item>
    <Button type="primary" htmlType="submit" loading={save.isPending}>保存</Button>
  </Form>}<p className="small muted">按北京时间汇总。延期、暂停、效力变化单独提醒；同一机会批次、截止日期和提醒节点不会因多条订阅重复通知。</p></Card>;
}
