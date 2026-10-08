"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, App, Button, Form, Input, Modal, Skeleton, Tabs } from "antd";
import { api } from "@/lib/api";
import type { ManagedUser } from "@/components/management/AccountManagement";
import SavedResults from "@/components/management/SavedResults";
export default function AccountSettings() {
  const { message } = App.useApp();
  const client = useQueryClient();
  const router = useRouter();
  const [closing, setClosing] = useState(false);
  const [passwordForm] = Form.useForm();
  const query = useQuery({
    queryKey: ["private-account"],
    queryFn: () => api<ManagedUser>("account"),
  });
  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api("account", {
        method: "PATCH",
        body: JSON.stringify({
          ...values,
          permission_version: query.data?.permission_version,
        }),
      }),
    onSuccess: () => {
      message.success("账户信息已更新");
      passwordForm.resetFields();
      void query.refetch();
      void client.invalidateQueries({ queryKey: ["me"] });
    },
    onError: (error) => message.error(error.message),
  });
  const remove = useMutation({
    mutationFn: (values: Record<string, string>) =>
      api("account", { method: "DELETE", body: JSON.stringify(values) }),
    onSuccess: () => {
      client.clear();
      router.replace("/login");
    },
    onError: (error) => message.error(error.message),
  });
  if (query.isLoading) return <Skeleton />;
  if (query.error || !query.data)
    return (
      <Alert type="error" title={query.error?.message || "无法读取账户资料"} />
    );
  const user = query.data;
  return (
    <Tabs
      items={[
        {
          key: "profile",
          label: "账户资料",
          children: (
            <div style={{ maxWidth: 580 }}>
              <p>
                当前账号：<strong>{user.username}</strong>
              </p>
              <Form
                key={user.permission_version}
                layout="vertical"
                initialValues={{
                  first_name: user.first_name,
                  email: user.email,
                }}
                onFinish={(values) =>
                  save.mutate({
                    first_name: values.first_name,
                    email: values.email,
                  })
                }
              >
                <Form.Item name="first_name" label="姓名">
                  <Input maxLength={150} />
                </Form.Item>
                <Form.Item
                  name="email"
                  label="邮箱"
                  rules={[{ type: "email" }]}
                >
                  <Input />
                </Form.Item>
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={save.isPending}
                >
                  保存资料
                </Button>
              </Form>
            </div>
          ),
        },
        {
          key: "security",
          label: "账户安全",
          children: (
            <div style={{ maxWidth: 580 }}>
              <Form
                form={passwordForm}
                layout="vertical"
                onFinish={(values) => save.mutate(values)}
              >
                <Form.Item
                  name="current_password"
                  label="当前密码"
                  rules={[{ required: true }]}
                >
                  <Input.Password autoComplete="current-password" />
                </Form.Item>
                <Form.Item
                  name="password"
                  label="新密码"
                  rules={[{ required: true, min: 8, max: 256 }]}
                >
                  <Input.Password autoComplete="new-password" />
                </Form.Item>
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={save.isPending}
                >
                  修改密码
                </Button>
              </Form>
              {user.role === "customer" && (
                <div style={{ marginTop: 40 }}>
                  <h3>注销账户</h3>
                  <p className="muted">
                    将删除私人账号及名下业务资料，操作无法恢复。
                  </p>
                  <Button danger onClick={() => setClosing(true)}>
                    注销我的账户
                  </Button>
                </div>
              )}
              {closing && (
                <Modal
                  open
                  title="注销账户"
                  footer={null}
                  onCancel={() => setClosing(false)}
                >
                  <Form
                    layout="vertical"
                    onFinish={(values) => remove.mutate(values)}
                  >
                    <Form.Item
                      name="confirm_username"
                      label="输入当前用户名确认"
                      rules={[{ required: true }]}
                    >
                      <Input />
                    </Form.Item>
                    <Form.Item
                      name="current_password"
                      label="当前密码"
                      rules={[{ required: true }]}
                    >
                      <Input.Password />
                    </Form.Item>
                    <Button
                      danger
                      type="primary"
                      htmlType="submit"
                      loading={remove.isPending}
                    >
                      确认注销
                    </Button>
                  </Form>
                </Modal>
              )}
            </div>
          ),
        },
        { key: "results", label: "已保存分析", children: <SavedResults /> },
      ]}
    />
  );
}
