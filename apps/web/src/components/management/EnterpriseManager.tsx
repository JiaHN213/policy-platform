"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Empty,
  Pagination,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tabs,
} from "antd";
import ContinuousMatching from "@/components/customer/ContinuousMatching";
import { usePolicyWorkspace } from "@/components/workspace/WorkspaceShell";
import { accountApi } from "@/lib/account-scope";
import type { Page } from "@/lib/api";
import ScopedSettingsPanel from "./ScopedSettingsPanel";
import SavedResults from "./SavedResults";
import {
  ProfileEditor,
  ProjectEditor,
  type Profile,
  type Project,
  type Options,
} from "@/components/customer/EnterprisePanel";

type Values = Record<string, string | string[]>;
export default function EnterpriseManager({ userId }: { userId: number }) {
  const api = accountApi(userId);
  const { openPolicy } = usePolicyWorkspace();
  const client = useQueryClient();
  const { message } = App.useApp();
  const [selected, setSelected] = useState<string>();
  const [tab, setTab] = useState("profile");
  const [editor, setEditor] = useState<Profile | "new">();
  const [projectEditor, setProjectEditor] = useState<Project | "new">();
  const [projectPage, setProjectPage] = useState(1);
  const profiles = useQuery({
    queryKey: ["managed-enterprises", userId],
    queryFn: () => api<{ items: Profile[] }>("enterprises"),
  });
  const options = useQuery({
    queryKey: ["enterprise-options"],
    queryFn: () => api<Options>("enterprises/options"),
  });
  const profile =
    profiles.data?.items.find((item) => item.id === selected) ||
    profiles.data?.items[0];
  const projects = useQuery({
    queryKey: ["managed-projects", userId, profile?.id],
    queryFn: async () => {
      const first = await api<Page<Project>>(
        `enterprise-projects?profile=${profile!.id}`,
      );
      const items = [...first.items];
      for (let page = 2; items.length < first.count && page <= 100; page++) {
        const next = await api<Page<Project>>(
          `enterprise-projects?profile=${profile!.id}&page=${page}`,
        );
        if (!next.items.length) break;
        items.push(...next.items);
      }
      return { ...first, items };
    },
    enabled: !!profile,
  });
  const refresh = () => {
    void client.invalidateQueries({
      queryKey: ["managed-enterprises", userId],
    });
    void client.invalidateQueries({ queryKey: ["managed-projects", userId] });
    void client.invalidateQueries({ queryKey: ["managed-users"] });
    void client.invalidateQueries({ queryKey: ["subscriptions"] });
  };
  const remove = useMutation({
    mutationFn: ({ id, company }: { id: string; company: boolean }) =>
      api(company ? `enterprises/${id}` : `enterprise-projects/${id}`, {
        method: "DELETE",
        ...(company
          ? { body: JSON.stringify({ confirm_name: profile!.name }) }
          : {}),
      }),
    onSuccess: () => {
      setProjectPage(1);
      refresh();
      message.success("已删除");
    },
    onError: (error) => message.error(error.message),
  });
  if (profiles.isLoading || options.isLoading) return <Spin />;
  if (profiles.error || options.error || !options.data)
    return (
      <Alert
        type="error"
        title={
          profiles.error?.message || options.error?.message || "无法读取配置"
        }
      />
    );
  const config = options.data;
  const fields = (data: Values) =>
    Object.entries(data).map(([key, value]) => ({
      key,
      label: config.fields[key] || "补充资料",
      children: Array.isArray(value)
        ? value
            .map(
              (v) => config.tags[key]?.find((t) => t.value === v)?.label || v,
            )
            .join("、")
        : value || "未填写",
    }));
  return (
    <>
      <Space wrap className="space-bottom">
        <Select
          aria-label="选择企业"
          style={{ minWidth: 260, maxWidth: "100%" }}
          value={profile?.id}
          options={profiles.data?.items.map((item) => ({
            value: item.id,
            label: item.name,
          }))}
          onChange={(value) => {
            setSelected(value);
            setProjectPage(1);
            setTab("profile");
          }}
          placeholder="该账号暂无企业"
        />
        <Button type="primary" onClick={() => setEditor("new")}>
          新增企业
        </Button>
        {profile && <><Button onClick={() => setTab("settings")}>企业配置</Button><Button onClick={() => setTab("analysis")}>分析与维护</Button></>}
      </Space>
      {!profile ? (
        <Empty description="该账号尚未建立企业资料" />
      ) : (
        <Tabs
          key={profile.id}
          activeKey={tab}
          onChange={setTab}
          items={[
            { key: "settings", label: "企业配置", children: <ScopedSettingsPanel profileId={profile.id} /> },
            { key: "analysis", label: "分析与维护", children: <SavedResults userId={userId} profileId={profile.id} /> },
            {
              key: "profile",
              label: "企业画像",
              children: (
                <Card
                  title={profile.name}
                  extra={
                    <Space>
                      <Button onClick={() => setEditor(profile)}>修改</Button>
                      <Popconfirm
                        title="删除企业及关联资料？"
                        description="企业画像、项目、专属订阅及分析结果会一并删除，无法恢复。"
                        onConfirm={() =>
                          remove.mutateAsync({ id: profile.id, company: true })
                        }
                      >
                        <Button danger loading={remove.isPending}>
                          删除企业
                        </Button>
                      </Popconfirm>
                    </Space>
                  }
                >
                  <Descriptions column={1} items={fields(profile.data)} />
                </Card>
              ),
            },
            {
              key: "projects",
              label: `项目（${projects.data?.count ?? 0}）`,
              children: (
                <>
                  <Button
                    type="primary"
                    className="space-bottom"
                    onClick={() => setProjectEditor("new")}
                  >
                    新增项目
                  </Button>
                  {projects.error && (
                    <Alert type="error" title={projects.error.message} />
                  )}
                  {projects.data?.items
                    .slice((projectPage - 1) * 10, projectPage * 10)
                    .map((project) => (
                      <Card
                        className="space-bottom"
                        key={project.id}
                        title={project.name}
                      >
                        <p>{project.description}</p>
                        <Descriptions column={1} items={fields(project.data)} />
                        <Space>
                          <Button onClick={() => setProjectEditor(project)}>
                            修改项目
                          </Button>
                          <Popconfirm
                            title="删除项目及其专属订阅？"
                            onConfirm={() =>
                              remove.mutateAsync({
                                id: project.id,
                                company: false,
                              })
                            }
                          >
                            <Button danger>删除</Button>
                          </Popconfirm>
                        </Space>
                      </Card>
                    ))}
                  {!projects.data?.items.length && (
                    <Empty description="暂无项目" />
                  )}
                  <Pagination
                    current={projectPage}
                    pageSize={10}
                    total={projects.data?.count || 0}
                    onChange={setProjectPage}
                    showSizeChanger={false}
                    hideOnSinglePage
                  />
                </>
              ),
            },
            {
              key: "follow",
              label: "持续关注",
              children: (
                <ContinuousMatching
                  key={profile.id}
                  userId={userId}
                  profile={profile.id}
                  projects={projects.data?.items || []}
                  openPolicy={openPolicy}
                />
              ),
            },
          ]}
        />
      )}
      {editor && (
        <ProfileEditor
          userId={userId}
          profile={editor === "new" ? undefined : editor}
          name={editor === "new" ? "" : editor.name}
          options={config}
          close={() => setEditor(undefined)}
          saved={(value) => {
            setSelected(value.id);
            setEditor(undefined);
            refresh();
          }}
        />
      )}
      {projectEditor && profile && (
        <ProjectEditor
          userId={userId}
          project={projectEditor === "new" ? undefined : projectEditor}
          profile={profile}
          options={config}
          close={() => setProjectEditor(undefined)}
          saved={() => {
            setProjectEditor(undefined);
            refresh();
          }}
        />
      )}
    </>
  );
}
