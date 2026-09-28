"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Empty,
  Input,
  Modal,
  Pagination,
  Select,
  Skeleton,
  Tag,
} from "antd";
import { api, safeExternalUrl, type Page } from "@/lib/api";
import { explainSystemText } from "@/lib/system-messages";

type KnowledgePage = {
  id: string;
  slug: string;
  page_type: "policy" | "chain" | "topic" | "region";
  title: string;
  abstract: string;
  status: string;
  source_count: number;
  revision: number;
  built_at: string;
};
type Citation = {
  policy_id: string;
  version: number;
  title: string;
  document_number: string;
  quote: string;
  source_url: string;
  purpose: string;
};
type KnowledgeDetail = KnowledgePage & {
  body: string;
  citations: Citation[];
  source_versions: Record<string, number>;
  model: string;
  prompt_version: string;
  sources: {
    id: string;
    title: string;
    version: number;
    source_url: string;
  }[];
};
type KnowledgeBuild = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  result: {
    pages?: number;
    revisions_created?: number;
    lint?: { issues?: number };
  };
  error_code: string;
  updated_at: string;
};

const pageTypes = [
  { value: "policy", label: "政策知识页" },
  { value: "chain", label: "政策链" },
  { value: "topic", label: "业务与方向专题" },
  { value: "region", label: "地区专题" },
];

function MarkdownBody({ value }: { value: string }) {
  return (
    <div className="wiki-body">
      {value.split("\n").map((line, index) => {
        if (line.startsWith("### ")) return <h4 key={index}>{line.slice(4)}</h4>;
        if (line.startsWith("## ")) return <h3 key={index}>{line.slice(3)}</h3>;
        if (line.startsWith("# ")) return <h2 key={index}>{line.slice(2)}</h2>;
        if (line.startsWith("- ")) return <p key={index}>• {line.slice(2)}</p>;
        if (!line.trim()) return <div className="wiki-gap" key={index} />;
        return <p key={index}>{line}</p>;
      })}
    </div>
  );
}

export default function KnowledgeWiki({
  isStaff,
  onSelect,
}: {
  isStaff: boolean;
  onSelect: (id: string) => void;
}) {
  const { message } = App.useApp();
  const client = useQueryClient();
  const [page, setPage] = useState(1);
  const [pageType, setPageType] = useState<string>();
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string>();
  const list = useQuery({
    queryKey: ["knowledge-pages", page, pageType, q],
    queryFn: () =>
      api<Page<KnowledgePage>>(
        `knowledge/pages?${new URLSearchParams({ page: String(page), ...(pageType ? { page_type: pageType } : {}), ...(q ? { q } : {}) })}`,
      ),
  });
  const detail = useQuery({
    queryKey: ["knowledge-page", selected],
    queryFn: () => api<KnowledgeDetail>(`knowledge/pages/${selected}`),
    enabled: !!selected,
  });
  const builds = useQuery({
    queryKey: ["knowledge-builds"],
    queryFn: () => api<Page<KnowledgeBuild>>("admin/knowledge/builds"),
    enabled: isStaff,
    refetchInterval: isStaff ? 10_000 : false,
  });
  const sync = useMutation({
    mutationFn: () => api<KnowledgeBuild>("admin/knowledge/builds/sync", { method: "POST" }),
    onSuccess: (build) => {
      void client.invalidateQueries({ queryKey: ["knowledge-builds"] });
      message.success(
        build.status === "succeeded" ? "知识库已经是最新版本。" : "知识库同步任务已提交。",
      );
    },
    onError: (error) => message.error(error.message),
  });
  const lint = useMutation({
    mutationFn: () => api<{ pages: number; issues: number }>("admin/knowledge/pages/lint", { method: "POST" }),
    onSuccess: (result) => {
      void client.invalidateQueries({ queryKey: ["knowledge-pages"] });
      message.success(
        result.issues
          ? `检查完成：发现 ${result.issues} 个需要更新的引用。`
          : `检查完成：${result.pages} 个页面引用全部有效。`,
      );
    },
    onError: (error) => message.error(error.message),
  });
  const exportObsidian = useMutation({
    mutationFn: () =>
      api<{ pages: number; vault: string }>("admin/knowledge/pages/export-obsidian", {
        method: "POST",
      }),
    onSuccess: (result) =>
      message.success(`已导出 ${result.pages} 个页面到内部 Obsidian Vault“${result.vault}”。`),
    onError: (error) => message.error(error.message),
  });
  const importObsidian = useMutation({
    mutationFn: () =>
      api<{ imported: number; unchanged: number; conflicts: { path: string; reason: string }[] }>(
        "admin/knowledge/pages/import-obsidian",
        { method: "POST" },
      ),
    onSuccess: (result) => {
      void client.invalidateQueries({ queryKey: ["knowledge-builds"] });
      if (result.conflicts.length) {
        Modal.warning({
          title: `已导入 ${result.imported} 份，${result.conflicts.length} 份需要处理`,
          content: result.conflicts.slice(0, 8).map((item) => (
            <p key={item.path}>
              <strong>{item.path}</strong>：{explainSystemText(item.reason)}
            </p>
          )),
        });
      } else {
        message.success(`已从 Obsidian 导入 ${result.imported} 份政策修改。`);
      }
    },
    onError: (error) => message.error(error.message),
  });
  const latestBuild = builds.data?.items[0];
  return (
    <>
      <Alert
        showIcon
        type="info"
        title="政策知识库由正式政策、AI 摘要和有证据的政策关系自动维护"
        description="每个结论都保留来源政策、版本和逐字引用。政策版本发生变化后，旧引用会被标记并由后台增量重建；知识页用于理解和导航，具体政策事实仍以原文为准。"
      />
      {isStaff && (
        <div className="wiki-admin-bar">
          <div>
            <strong>知识库自动维护</strong>
            <div className="small muted">
              {latestBuild
                ? `最近任务：${({ queued: "等待构建", running: "正在构建", succeeded: "本批完成", failed: "本批失败" })[latestBuild.status]} · ${new Date(latestBuild.updated_at).toLocaleString()}`
                : "尚无构建任务记录"}
            </div>
          </div>
          <Button loading={lint.isPending} onClick={() => lint.mutate()}>
            检查引用
          </Button>
          <Button
            loading={exportObsidian.isPending}
            onClick={() => exportObsidian.mutate()}
          >
            导出 Obsidian
          </Button>
          <Button
            loading={importObsidian.isPending}
            onClick={() => importObsidian.mutate()}
          >
            导入 Obsidian 修改
          </Button>
          <Button type="primary" loading={sync.isPending} onClick={() => sync.mutate()}>
            立即同步知识库
          </Button>
        </div>
      )}
      <div className="wiki-filters">
        <Input.Search
          aria-label="搜索政策知识库"
          allowClear
          placeholder="搜索政策、专题或地区"
          onSearch={(value) => {
            setQ(value.trim());
            setPage(1);
          }}
        />
        <Select
          aria-label="知识页类型"
          allowClear
          placeholder="全部知识页"
          value={pageType}
          options={pageTypes}
          onChange={(value) => {
            setPageType(value);
            setPage(1);
          }}
        />
      </div>
      {list.error ? (
        <Alert type="error" title={list.error.message} />
      ) : list.isLoading ? (
        <Skeleton active />
      ) : !list.data?.items.length ? (
        <Empty description="暂无匹配知识页，管理员可先执行知识库同步。" />
      ) : (
        <div className="wiki-grid">
          {list.data.items.map((item) => (
            <article className="wiki-card" key={item.id}>
              <div className="spread">
                <Tag>{pageTypes.find((type) => type.value === item.page_type)?.label}</Tag>
                <span className="small muted">修订 {item.revision}</span>
              </div>
              <h3>{item.title}</h3>
              <p>{item.abstract}</p>
              <div className="spread small muted">
                <span>{item.source_count} 份来源政策</span>
                <Button type="link" onClick={() => setSelected(item.slug)}>
                  阅读知识页 →
                </Button>
              </div>
            </article>
          ))}
        </div>
      )}
      <Pagination
        className="pagination"
        current={page}
        total={list.data?.count || 0}
        pageSize={20}
        showSizeChanger={false}
        onChange={setPage}
      />
      <Modal
        title={detail.data?.title || "政策知识页"}
        open={!!selected}
        width={1040}
        footer={null}
        onCancel={() => setSelected(undefined)}
      >
        {detail.isLoading ? (
          <Skeleton active />
        ) : detail.error ? (
          <Alert type="error" title={detail.error.message} />
        ) : detail.data ? (
          <div className="wiki-detail">
            <div className="wiki-detail-meta">
              <Tag>{pageTypes.find((type) => type.value === detail.data.page_type)?.label}</Tag>
              <Tag>修订 {detail.data.revision}</Tag>
              <Tag>{detail.data.source_count} 份来源</Tag>
              <span className="small muted">
                构建规则：{detail.data.prompt_version}
                {detail.data.model ? ` · 模型 ${detail.data.model}` : ""}
              </span>
            </div>
            <MarkdownBody value={detail.data.body} />
            <h3>引用与原文依据</h3>
            <div className="wiki-citations">
              {detail.data.citations.map((citation, index) => (
                <article key={`${citation.policy_id}-${index}`}>
                  <strong>[{index + 1}] {citation.title}</strong>
                  <p>{citation.quote}</p>
                  <div className="spread">
                    <span className="small muted">
                      版本 {citation.version} · {citation.purpose}
                    </span>
                    <span>
                      <Button type="link" onClick={() => onSelect(citation.policy_id)}>
                        查看库内政策
                      </Button>
                      <a
                        href={safeExternalUrl(citation.source_url)}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        官方原文 ↗
                      </a>
                    </span>
                  </div>
                </article>
              ))}
            </div>
          </div>
        ) : null}
      </Modal>
    </>
  );
}
