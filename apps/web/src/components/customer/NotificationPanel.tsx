"use client";

import { accountApi } from "@/lib/account-scope";

import {

type Notification,
type Page
} from "@/lib/api";
import {
BellOutlined
} from "@ant-design/icons";
import { useMutation,useQuery,useQueryClient } from "@tanstack/react-query";
import {
App,
Badge,
Button,
Empty,
Pagination,
Popconfirm,
Segmented,
Skeleton
} from "antd";
import { useState } from "react";


import { ErrorBox } from "@/components/policy/common";

export default function NotificationPanel({ userId, onSelect }: { userId?: number; onSelect: (id: string) => void }) {
  const api = accountApi(userId);
  const client = useQueryClient();
  const { message } = App.useApp();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("all");
  const query = useQuery({
    queryKey: ["notifications", userId, page, status],
    queryFn: () =>
      api<Page<Notification>>(`notifications?page=${page}&status=${status}`),
    refetchInterval: 30_000,
  });
  const read = useMutation({
    mutationFn: (id: string) =>
      api(`notifications/${id}/read`, { method: "POST" }),
    onSuccess: () => {
      if (status === "unread" && query.data?.items.length === 1 && page > 1)
        setPage(page - 1);
      client.invalidateQueries({ queryKey: ["notifications"] });
      client.invalidateQueries({ queryKey: ["home-notifications"] });
      client.invalidateQueries({ queryKey: ["overview"] });
    },
    onError: (e) => message.error(e.message),
  });
  const remove = useMutation({ mutationFn: (id: string) => api(`notifications/${id}`, { method: "DELETE" }), onSuccess: () => { if (query.data?.items.length === 1 && page > 1) setPage(page - 1); void client.invalidateQueries({ queryKey: ["notifications"] }); void client.invalidateQueries({ queryKey: ["overview"] }); message.success("消息已删除"); }, onError: error => message.error(error.message) });
  const readAll = useMutation({
    mutationFn: () =>
      api<{ updated: number }>("notifications/read-all", { method: "POST" }),
    onSuccess: (result) => {
      setPage(1);
      client.invalidateQueries({ queryKey: ["notifications"] });
      client.invalidateQueries({ queryKey: ["home-notifications"] });
      client.invalidateQueries({ queryKey: ["overview"] });
      message.success(
        result.updated
          ? `已将 ${result.updated} 条消息标为已读。`
          : "没有未读消息。",
      );
    },
    onError: (e) => message.error(e.message),
  });
  if (query.error) return <ErrorBox error={query.error} />;
  if (query.isLoading) return <Skeleton active />;
  return (
    <>
      <div className="panel-heading">
        <Segmented
          value={status}
          options={[
            { label: "全部消息", value: "all" },
            { label: "未读", value: "unread" },
            { label: "已读", value: "read" },
          ]}
          onChange={(value) => {
            setStatus(value);
            setPage(1);
          }}
        />
        <Popconfirm
          title="将所有未读消息标为已读？"
          description="包括其他分页中的消息。"
          okText="确认"
          cancelText="取消"
          onConfirm={() => readAll.mutateAsync()}
        >
          <Button loading={readAll.isPending}>全部标为已读</Button>
        </Popconfirm>
      </div>
      {!query.data?.items.length ? (
        <div className="empty-pad">
          <Empty
            description={
              status === "unread"
                ? "未读消息已处理完"
                : status === "read"
                  ? "还没有已读消息"
                  : "暂无消息，新政策匹配订阅后会出现在这里"
            }
          />
        </div>
      ) : (
        query.data.items.map((n) => (
          <article className="notification-row" key={n.id}>
            <Badge dot={!n.read_at}>
              <BellOutlined className="teal-icon" />
            </Badge>
            <div>
              <button
                className="policy-title"
                onClick={() => {
                  read.mutate(n.id);
                  if (n.policy_id) onSelect(n.policy_id);
                }}
              >
                {n.title}
              </button>
              <p className="muted">
                {(n.reasons as string[]).join("、")}
              </p>
              {n.items?.map((item, index) => <p key={index}><Button type="link" onClick={() => { read.mutate(n.id); onSelect(String(item.policy_id)); }}>{String(item.title)}</Button></p>)}
              <span className="small muted">
                {new Date(n.created_at).toLocaleString("zh-CN")}
              </span>
            </div>
            <Popconfirm title="删除这条消息？" onConfirm={() => remove.mutateAsync(n.id)}><Button danger>删除</Button></Popconfirm>
            {!n.read_at && (
              <Button onClick={() => read.mutate(n.id)}>标为已读</Button>
            )}
          </article>
        ))
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
  );
}

