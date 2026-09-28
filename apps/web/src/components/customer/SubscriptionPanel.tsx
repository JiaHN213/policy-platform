"use client";

import {
api,
type Page,
type Subscription
} from "@/lib/api";
import {
BookOutlined,
PlusOutlined
} from "@ant-design/icons";
import { useMutation,useQuery,useQueryClient } from "@tanstack/react-query";
import {
Alert,
App,
Button,
Empty,
Form,
Input,
InputNumber,
Modal,
Pagination,
Popconfirm,
Space,
Segmented,
Select,
Skeleton,
Switch,
Tag
} from "antd";
import { useState } from "react";


import { documentTypeLabel,documentTypes,ErrorBox } from "@/components/policy/common";

export default function SubscriptionPanel() {
  const client = useQueryClient();
  const { message } = App.useApp();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Subscription | null>(null);
  const [form] = Form.useForm();
  const [requestKey, setRequestKey] = useState("");
  const [page, setPage] = useState(1);
  const [previewResult, setPreviewResult] = useState<{
    count: number;
    items: Array<{
      id: string;
      title: string;
      publication_date: string;
      reasons: string[];
      opportunities: string[];
    }>;
  } | null>(null);
  const targetView = Form.useWatch("target_view", form);
  const taxonomy = useQuery({
    queryKey: ["taxonomy"],
    queryFn: () =>
      api<Record<string, Array<{ value: string; label: string }>> & { topics: string[] }>(
        "taxonomies",
      ),
  });
  const query = useQuery({
    queryKey: ["subscriptions", page],
    queryFn: () => api<Page<Subscription>>(`subscriptions?page=${page}`),
  });
  const changed = () => {
    client.invalidateQueries({ queryKey: ["subscriptions"] });
    client.invalidateQueries({ queryKey: ["overview"] });
  };
  const create = useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      api(editing ? `subscriptions/${editing.id}` : "subscriptions", {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify(values),
        headers: { "Idempotency-Key": requestKey },
      }),
    onSuccess: () => {
      setOpen(false);
      form.resetFields();
      changed();
      message.success(
        editing ? "订阅已更新。" : "订阅已创建，将接收此后发布的相关政策。",
      );
    },
    onError: (e) => message.error(e.message),
  });
  const preview = useMutation({
    mutationFn: async () => {
      const values = await form.validateFields();
      return api<NonNullable<typeof previewResult>>("subscriptions/preview", {
        method: "POST",
        body: JSON.stringify({
          ...values,
          has_deadline: values.has_deadline === "any" ? null : values.has_deadline,
        }),
      });
    },
    onSuccess: setPreviewResult,
    onError: (e) => message.error(e.message),
  });
  const toggle = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      api(`subscriptions/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ active }),
      }),
    onSuccess: changed,
    onError: (e) => message.error(e.message),
  });
  const remove = useMutation({
    mutationFn: (id: string) =>
      api(`subscriptions/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      if (query.data?.items.length === 1 && page > 1) setPage(page - 1);
      changed();
      message.success("订阅已删除，已有消息仍会保留。");
    },
    onError: (e) => message.error(e.message),
  });
  return (
    <>
      <div className="panel-heading">
        <div>
          <h3>你关注的政策范围</h3>
          <p className="muted">同一政策命中多个订阅时，只生成一条通知。</p>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            setEditing(null);
            form.resetFields();
            setPreviewResult(null);
            setRequestKey(crypto.randomUUID());
            setOpen(true);
          }}
        >
          新建订阅
        </Button>
      </div>
      {query.error ? (
        <ErrorBox error={query.error} />
      ) : query.isLoading ? (
        <Skeleton active />
      ) : (
        <>
          {!query.data?.items.length ? (
            <div className="empty-pad">
              <Empty description="还没有订阅，添加第一个关注条件吧" />
            </div>
          ) : (
            <div className="subscription-grid">
              {query.data.items.map((sub) => (
                <article className="subscription-card" key={sub.id}>
                  <div className="spread">
                    <BookOutlined className="teal-icon" />
                    <Switch
                      aria-label={`启用${sub.name}`}
                      checked={sub.active}
                      loading={toggle.isPending}
                      onChange={(active) =>
                        toggle.mutate({ id: sub.id, active })
                      }
                    />
                  </div>
                  <h3>{sub.name}</h3>
                  <p>{sub.keywords || "不限关键词"}</p>
                  <div>
                    <Tag>
                      {sub.target_view === "opportunity"
                        ? "政策机会"
                        : sub.target_view === "all"
                          ? "政策与机会"
                          : "政策文件"}
                    </Tag>
                    <Tag>{sub.topic || "全部主题"}</Tag>
                    <Tag>
                      {sub.document_type
                        ? documentTypeLabel(sub.document_type)
                        : "全部文件类型"}
                    </Tag>
                    <Tag>{sub.region || "全部地区"}</Tag>
                    {sub.business_domain && (
                      <Tag>
                        {taxonomy.data?.business_domains?.find(
                          (item) => item.value === sub.business_domain,
                        )?.label || sub.business_domain}
                      </Tag>
                    )}
                    {sub.direction_tag && (
                      <Tag>
                        {taxonomy.data?.direction_tags?.find(
                          (item) => item.value === sub.direction_tag,
                        )?.label || sub.direction_tag}
                      </Tag>
                    )}
                  </div>
                  <p className="small muted">
                    {sub.active
                      ? "关注中 · 新政策发布后通知"
                      : "已暂停 · 不接收新通知"}
                  </p>
                  <div className="spread">
                    <Button
                      onClick={() => {
                        setEditing(sub);
                        form.setFieldsValue(sub);
                        setPreviewResult(null);
                        setOpen(true);
                      }}
                    >
                      修改条件
                    </Button>
                    <Popconfirm
                      title={`删除订阅“${sub.name}”？`}
                      description="删除后停止接收匹配通知，已有消息保留。"
                      onConfirm={() => remove.mutateAsync(sub.id)}
                      okText="删除"
                      cancelText="取消"
                      okButtonProps={{
                        danger: true,
                        loading: remove.isPending,
                      }}
                    >
                      <Button danger disabled={remove.isPending}>
                        删除
                      </Button>
                    </Popconfirm>
                  </div>
                </article>
              ))}
            </div>
          )}
          <Pagination
            className="pagination"
            current={page}
            total={query.data?.count || 0}
            pageSize={20}
            showSizeChanger={false}
            onChange={setPage}
          />
        </>
      )}
      <Modal
        title={editing ? "修改政策订阅" : "新建政策订阅"}
        open={open}
        onCancel={() => {
          if (!create.isPending) {
            setOpen(false);
            setPreviewResult(null);
          }
        }}
        onOk={() => form.submit()}
        confirmLoading={create.isPending}
        okText={editing ? "保存修改" : "创建订阅"}
        cancelText="取消"
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) =>
            create.mutate({
              ...v,
              has_deadline: v.has_deadline === "any" ? null : v.has_deadline,
            })
          }
        >
          <Form.Item
            name="name"
            label="订阅名称"
            rules={[{ required: true }, { max: 100 }]}
          >
            <Input placeholder="例如：供水数字化相关政策" />
          </Form.Item>
          <Form.Item name="target_view" label="订阅视角" initialValue="policy">
            <Segmented
              block
              options={[
                { label: "政策文件", value: "policy" },
                { label: "政策机会", value: "opportunity" },
                { label: "政策与机会", value: "all" },
              ]}
            />
          </Form.Item>
          <Form.Item name="topic" label="关注主题" initialValue="">
            <Select
              options={["", "水务", "环保", "人工智能＋"].map((t) => ({
                value: t,
                label: t || "全部主题",
              }))}
            />
          </Form.Item>
          <Form.Item
            name="keywords"
            label="关键词（多个词用空格分隔，须全部匹配）"
            rules={[{ max: 200 }]}
          >
            <Input placeholder="例如：供水 数字化" />
          </Form.Item>
          <Form.Item
            name="document_type"
            label="文件类型"
            initialValue=""
            extra="与主题、关键词、地区同时匹配才会通知；附件随主文件一起查看。"
          >
            <Select
              options={[{ value: "", label: "全部文件类型" }, ...documentTypes]}
            />
          </Form.Item>
          <Form.Item
            name="region"
            label="地区名称（留空表示全部）"
            rules={[{ max: 100 }]}
          >
            <Input placeholder="例如：全国" />
          </Form.Item>
          <div className="form-grid-two">
            <Form.Item name="geographic_level" label="地域层级" initialValue="">
              <Select
                options={[
                  { value: "", label: "全部层级" },
                  ...(taxonomy.data?.geographic_levels || []),
                ]}
              />
            </Form.Item>
            <Form.Item name="validity_status" label="政策效力" initialValue="">
              <Select
                options={[
                  { value: "", label: "全部效力状态" },
                  ...(taxonomy.data?.validity_statuses || []),
                ]}
              />
            </Form.Item>
            <Form.Item name="province" label="省份">
              <Input placeholder="例如：广西壮族自治区" />
            </Form.Item>
            <Form.Item name="city" label="城市">
              <Input placeholder="例如：南宁市" />
            </Form.Item>
            <Form.Item name="business_domain" label="水务业务领域" initialValue="">
              <Select
                showSearch
                options={[
                  { value: "", label: "全部业务领域" },
                  ...(taxonomy.data?.business_domains || []),
                ]}
              />
            </Form.Item>
            <Form.Item name="direction_tag" label="技术与政策方向" initialValue="">
              <Select
                showSearch
                options={[
                  { value: "", label: "全部方向" },
                  ...(taxonomy.data?.direction_tags || []),
                ]}
              />
            </Form.Item>
          </div>
          {(targetView === "opportunity" || targetView === "all") && (
            <>
              <h4>政策机会条件</h4>
              <div className="form-grid-two">
                <Form.Item name="opportunity_category" label="机会分类" initialValue="">
                  <Select
                    options={[
                      { value: "", label: "全部机会分类" },
                      ...(taxonomy.data?.opportunity_categories || []),
                    ]}
                  />
                </Form.Item>
                <Form.Item name="opportunity_status" label="机会状态" initialValue="">
                  <Select
                    options={[
                      { value: "", label: "全部机会状态" },
                      ...(taxonomy.data?.opportunity_statuses || []),
                    ]}
                  />
                </Form.Item>
                <Form.Item name="acquisition_method" label="获取方式" initialValue="">
                  <Select
                    options={[
                      { value: "", label: "全部获取方式" },
                      ...(taxonomy.data?.acquisition_methods || []),
                    ]}
                  />
                </Form.Item>
                <Form.Item name="has_deadline" label="截止时间" initialValue="any">
                  <Select
                    options={[
                      { value: "any", label: "不限" },
                      { value: true, label: "有明确截止时间" },
                      { value: false, label: "长期或未明确截止" },
                    ]}
                  />
                </Form.Item>
              </div>
              <Form.Item
                name="eligible_keywords"
                label="适用对象关键词（多个词用空格分隔）"
              >
                <Input placeholder="例如：水务企业 中小企业" />
              </Form.Item>
              <Form.Item
                name="authority_keywords"
                label="主管或受理部门关键词"
              >
                <Input placeholder="例如：住建局 发改委" />
              </Form.Item>
              <Form.Item
                label="机会进入截止窗口时提醒"
                extra="填写后，系统会在机会进入该天数范围时发送一次截止提醒。"
              >
                <Space.Compact style={{ width: "100%" }}>
                  <Form.Item name="deadline_within_days" noStyle>
                    <InputNumber
                      min={1}
                      max={365}
                      style={{ width: "calc(100% - 96px)" }}
                    />
                  </Form.Item>
                  <Input
                    readOnly
                    value="天内截止"
                    style={{ width: 96, textAlign: "center" }}
                  />
                </Space.Compact>
              </Form.Item>
            </>
          )}
          <Button
            block
            loading={preview.isPending}
            onClick={() => preview.mutate()}
          >
            保存前预览匹配结果
          </Button>
          {previewResult && (
            <Alert
              className="space-top"
              type={previewResult.count ? "success" : "warning"}
              showIcon
              title={`当前条件可匹配 ${previewResult.count} 份已发布政策`}
              description={
                previewResult.items.length ? (
                  <div>
                    {previewResult.items.map((item) => (
                      <p key={item.id}>
                        <strong>{item.title}</strong>
                        <br />
                        <span className="muted">{item.reasons.join("；")}</span>
                      </p>
                    ))}
                    {previewResult.count > 10 && <p>仅展示最新 10 条。</p>}
                  </div>
                ) : (
                  "请放宽一个或多个筛选条件后再试。"
                )
              }
            />
          )}
        </Form>
      </Modal>
    </>
  );
}

