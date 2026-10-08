"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
} from "antd";
import { api, type Me, type Page } from "@/lib/api";
import SubscriptionPanel from "@/components/customer/SubscriptionPanel";
import NotificationSettings from "@/components/customer/NotificationSettings";
import NotificationPanel from "@/components/customer/NotificationPanel";
import { usePolicyWorkspace } from "@/components/workspace/WorkspaceShell";
import EnterpriseManager from "./EnterpriseManager";
import SavedResults from "./SavedResults";
import ScopedSettingsPanel from "./ScopedSettingsPanel";
export type ManagedUser = {
  id: number;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  role: string;
  is_active: boolean;
  is_superuser: boolean;
  permission_version: number;
  enterprise_count: number;
  date_joined: string;
};
const roles: Record<string, string> = {
  customer: "普通用户",
  staff: "内部运营",
  admin: "系统管理员",
};
function UserData({ user, tab, onTab }: { user: ManagedUser; tab: string; onTab: (value: string) => void }) {
  const { openPolicy } = usePolicyWorkspace();
  return (
    <>
      <Alert
        className="space-bottom"
        type="info"
        title={`正在管理 ${user.username} 的资料`}
        description="操作记录保留管理员身份，资料仍属于该用户。"
      />
      <Tabs
        destroyOnHidden
        activeKey={tab}
        onChange={onTab}
        items={[
          { key: "settings", label: "使用配置", children: <ScopedSettingsPanel userId={user.id} /> },
          {
            key: "enterprise",
            label: "企业与项目",
            children: <EnterpriseManager userId={user.id} />,
          },
          {
            key: "subscriptions",
            label: "订阅规则",
            children: <SubscriptionPanel userId={user.id} />,
          },
          {
            key: "messages",
            label: "消息",
            children: (
              <>
                <NotificationSettings userId={user.id} />
                <NotificationPanel userId={user.id} onSelect={openPolicy} />
              </>
            ),
          },
          {
            key: "results",
            label: "分析与维护",
            children: <SavedResults userId={user.id} />,
          },
        ]}
      />
    </>
  );
}
export default function AccountManagement({
  enterprisesOnly = false,
}: {
  enterprisesOnly?: boolean;
}) {
  const { message } = App.useApp();
  const client = useQueryClient();
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<ManagedUser | "new">();
  const [deleting, setDeleting] = useState<ManagedUser>();
  const [selected, setSelected] = useState<ManagedUser>();
  const [selectedTab, setSelectedTab] = useState("enterprise");
  const query = useQuery({
    queryKey: ["managed-users", q, page],
    queryFn: () =>
      api<Page<ManagedUser>>(
        `admin/users?${new URLSearchParams({ q, page: String(page) })}`,
      ),
  });
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("me") });
  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api(
        editing && editing !== "new"
          ? `admin/users/${editing.id}`
          : "admin/users",
        {
          method: editing === "new" ? "POST" : "PATCH",
          body: JSON.stringify({
            ...values,
            ...(!values.password ? { password: undefined } : {}),
            ...(editing && editing !== "new"
              ? { permission_version: editing.permission_version }
              : {}),
          }),
        },
      ),
    onSuccess: () => {
      setEditing(undefined);
      void client.invalidateQueries({ queryKey: ["managed-users"] });
      void client.invalidateQueries({ queryKey: ["me"] });
      message.success("账号已保存");
    },
    onError: (error) => message.error(error.message),
  });
  const remove = useMutation({
    mutationFn: (values: { confirm_username: string }) =>
      api(`admin/users/${deleting!.id}`, {
        method: "DELETE",
        body: JSON.stringify(values),
      }),
    onSuccess: () => {
      setDeleting(undefined);
      if (query.data?.items.length === 1 && page > 1) setPage(page - 1);
      void client.invalidateQueries({ queryKey: ["managed-users"] });
      message.success("账号及其私人业务资料已删除");
    },
    onError: (error) => message.error(error.message),
  });
  return (
    <>
      <div className="spread space-bottom">
        <Input.Search
          style={{ maxWidth: 360 }}
          allowClear
          placeholder="搜索用户名、姓名或邮箱"
          onSearch={(value) => {
            setQ(value);
            setPage(1);
          }}
        />
        {!enterprisesOnly && (
          <Button type="primary" onClick={() => setEditing("new")}>
            新增用户
          </Button>
        )}
      </div>
      {query.error && <Alert type="error" title={query.error.message} />}
      <Table
        rowKey="id"
        loading={query.isLoading}
        dataSource={query.data?.items}
        scroll={{ x: 780 }}
        pagination={{
          current: page,
          total: query.data?.count || 0,
          pageSize: 20,
          showSizeChanger: false,
          onChange: setPage,
        }}
        columns={[
          {
            title: "账号",
            dataIndex: "username",
            render: (value, user) => (
              <div>
                <strong>{value}</strong>
                <div className="small muted">
                  {user.first_name} {user.last_name}
                </div>
              </div>
            ),
          },
          {
            title: "邮箱",
            dataIndex: "email",
            render: (value) => value || "未填写",
          },
          {
            title: "角色",
            dataIndex: "role",
            render: (value) => roles[value] || "普通用户",
          },
          {
            title: "状态",
            dataIndex: "is_active",
            render: (value) => (
              <Tag color={value ? "green" : "default"}>
                {value ? "正常" : "已停用"}
              </Tag>
            ),
          },
          { title: "企业数", dataIndex: "enterprise_count" },
          {
            title: "操作",
            key: "actions",
            render: (_, user) => (
              <Space wrap>
                <Button onClick={() => { setSelected(user); setSelectedTab("enterprise"); }}>
                  {enterprisesOnly ? "管理企业与项目" : "管理名下资料"}
                </Button>
                {!enterprisesOnly && (
                  <>
                    <Button onClick={() => { setSelected(user); setSelectedTab("settings"); }}>使用配置</Button>
                    <Button onClick={() => { setSelected(user); setSelectedTab("results"); }}>分析与维护</Button>
                    <Button onClick={() => setEditing(user)}>编辑账号</Button>
                    <Button
                      danger
                      disabled={user.id === me.data?.id || user.is_superuser}
                      onClick={() => setDeleting(user)}
                    >
                      删除
                    </Button>
                  </>
                )}
              </Space>
            ),
          },
        ]}
      />
      <Drawer
        title={
          selected
            ? `${selected.username} · ${enterprisesOnly ? "企业管理" : "用户资料"}`
            : "用户资料"
        }
        size="min(1120px, 96vw)"
        open={!!selected}
        onClose={() => setSelected(undefined)}
        destroyOnHidden
      >
        {selected &&
          (enterprisesOnly ? (
            <EnterpriseManager key={selected.id} userId={selected.id} />
          ) : (
            <UserData key={selected.id} user={selected} tab={selectedTab} onTab={setSelectedTab} />
          ))}
      </Drawer>
      {editing && (
        <Modal
          title={editing === "new" ? "新增用户" : "编辑账号"}
          open
          footer={null}
          onCancel={() => setEditing(undefined)}
        >
          <Form
            layout="vertical"
            initialValues={
              editing === "new"
                ? { role: "customer", is_active: true }
                : editing
            }
            onFinish={(values) => save.mutate(values)}
          >
            <Form.Item
              name="username"
              label="用户名"
              rules={[{ required: true, max: 150 }]}
            >
              <Input />
            </Form.Item>
            <Form.Item name="first_name" label="姓名">
              <Input maxLength={150} />
            </Form.Item>
            <Form.Item name="email" label="邮箱" rules={[{ type: "email" }]}>
              <Input maxLength={254} />
            </Form.Item>
            <Form.Item name="role" label="角色">
              <Select
                disabled={
                  editing !== "new" &&
                  (editing.is_superuser || editing.id === me.data?.id)
                }
                options={Object.entries(roles).map(([value, label]) => ({
                  value,
                  label,
                }))}
              />
            </Form.Item>
            <Form.Item
              name="is_active"
              label="允许登录"
              valuePropName="checked"
            >
              <Switch
                disabled={editing !== "new" && editing.id === me.data?.id}
              />
            </Form.Item>
            <Form.Item
              name="password"
              label={
                editing === "new" ? "初始密码" : "重设密码（留空则不修改）"
              }
              rules={[{ required: editing === "new", min: 8, max: 256 }]}
            >
              <Input.Password autoComplete="new-password" />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={save.isPending}>
              保存
            </Button>
          </Form>
        </Modal>
      )}
      {deleting && (
        <Modal
          title={`删除 ${deleting.username}`}
          open
          footer={null}
          onCancel={() => setDeleting(undefined)}
        >
          <Alert
            className="space-bottom"
            type="warning"
            title="此操作无法恢复"
            description="账号、私人企业与项目、订阅、消息及分析结果将被删除；其他成员共用的企业保留，操作审计保留。"
          />
          <Form layout="vertical" onFinish={(values) => remove.mutate(values)}>
            <Form.Item
              name="confirm_username"
              label="输入用户名确认"
              rules={[{ required: true }]}
            >
              <Input />
            </Form.Item>
            <Button
              danger
              type="primary"
              htmlType="submit"
              loading={remove.isPending}
            >
              确认删除
            </Button>
          </Form>
        </Modal>
      )}
    </>
  );
}
