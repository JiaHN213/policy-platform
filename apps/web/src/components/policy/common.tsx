"use client";

import SearchHighlight,{ type SearchContext } from "@/components/SearchHighlight";
import {
safeExternalUrl,
type Policy,
type PolicyDetail
} from "@/lib/api";
import {
FileSearchOutlined
} from "@ant-design/icons";
import {
Alert,
Button,
Empty,
Tag
} from "antd";

export const documentTypes = [
  { value: "policy", label: "政策与制度文件" },
  { value: "opportunity", label: "政策机会文件" },
  { value: "result", label: "政策执行结果" },
  { value: "interpretation", label: "官方解读" },
  { value: "draft", label: "征求意见稿" },
];
export const documentTypeLabel = (value?: string) =>
  documentTypes.find((t) => t.value === value)?.label || "待分类";
export const geographicLevels = [
  { value: "national", label: "国家级" },
  { value: "provincial", label: "省级" },
  { value: "city", label: "地级市级" },
];
export const sourceGrades = [
  { value: "L1", label: "L1 官方原始" },
  { value: "L2", label: "L2 官方转载" },
  { value: "L3", label: "L3 政府官方业务平台" },
];
export function ProvenanceTags({ policy }: { policy: Policy }) {
  return (
    <>
      <Tag>
        {geographicLevels.find((g) => g.value === policy.geographic_level)
          ?.label || "地域待核验"}
        {policy.province && ` · ${policy.province}`}
        {policy.city && ` · ${policy.city}`}
      </Tag>
      <Tag>
        {sourceGrades.find((g) => g.value === policy.source_grade)?.label ||
          (policy.source_grade === "L4" ? "L4 非官方线索" : "来源待核验")}
      </Tag>
    </>
  );
}

export function AttachmentLinks({ policy }: { policy: PolicyDetail }) {
  if (!policy.attachments.length) return null;
  return (
    <div className="space-bottom">
      <h4>附件 / 附表</h4>
      <p className="muted">随主文件收录，以下链接打开官方附件。</p>
      {policy.attachments.map((file, index) => (
        <div key={file.id}>
          <a
            href={safeExternalUrl(file.url)}
            target="_blank"
            rel="noopener noreferrer"
          >
            附件 {index + 1} ↗
          </a>{" "}
          <span className="muted">{Math.ceil(file.size_bytes / 1024)} KB</span>
        </div>
      ))}
    </div>
  );
}
export function ErrorBox({ error, retry }: { error: Error; retry?: () => void }) {
  return (
    <Alert
      type="error"
      showIcon
      title={error.message}
      action={
        retry && (
          <Button onClick={retry} size="small">
            重试
          </Button>
        )
      }
    />
  );
}

export function PolicyCards({
  items,
  onSelect,
  search,
}: {
  items: Policy[];
  onSelect: (id: string) => void;
  search?: SearchContext;
}) {
  if (!items.length)
    return (
      <div className="empty-pad">
        <Empty description="暂无符合条件的已发布政策" />
      </div>
    );
  return (
    <div className="policy-list">
      {items.map((item) => (
        <article className="policy-card" key={item.id}>
          <div className="policy-icon">
            <FileSearchOutlined />
          </div>
          <div className="policy-content">
            <div className="policy-meta">
              <span>{item.issuer}</span>
              <span>{item.publication_date}</span>
              {item.is_demo && <Tag color="orange">虚构演示数据</Tag>}
            </div>
            <button className="policy-title" onClick={() => onSelect(item.id)}>
              <SearchHighlight text={item.title} terms={search?.keywords || []} />
            </button>
            {search?.previews?.[item.id] ? (
              <p className="search-result-preview">
                <span className="muted">{search.previews[item.id].source}：</span>
                <SearchHighlight text={search.previews[item.id].text} terms={search.keywords} />
                {!!search.previews[item.id].matched_fields.length && (
                  <small className="search-match-fields">匹配位置：{search.previews[item.id].matched_fields.join("、")}</small>
                )}
              </p>
            ) : item.summary && (
              <p className="muted">
                {item.summary_method === "ai"
                  ? "AI 摘要（请核对原文）"
                  : "原文摘录"}
                ：{item.summary.slice(0, 180)}
                {item.summary.length > 180 ? "…" : ""}
              </p>
            )}
            <div className="policy-tags">
              <Tag>{documentTypeLabel(item.document_type)}</Tag>
              <ProvenanceTags policy={item} />
              {(item.topics as string[]).map((topic) => (
                <Tag
                  key={topic}
                  color={
                    topic === "水务"
                      ? "cyan"
                      : topic === "环保"
                        ? "green"
                        : "blue"
                  }
                >
                  {topic}
                </Tag>
              ))}
              <span className="muted">
                {item.region}
                {item.document_number && ` · ${item.document_number}`}
              </span>
            </div>
          </div>
          <Button
            type="text"
            aria-label={`查看${item.title}`}
            onClick={() => onSelect(item.id)}
          >
            查看 →
          </Button>
        </article>
      ))}
    </div>
  );
}

