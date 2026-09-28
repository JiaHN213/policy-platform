"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Empty,
  Form,
  Input,
  Pagination,
  Segmented,
  Select,
  Skeleton,
  Tag,
} from "antd";
import { api, type Policy } from "@/lib/api";
import type { components } from "@/lib/schema";
import SearchHighlight, { type SearchContext } from "./SearchHighlight";

type Opportunity = components["schemas"]["Opportunity"];
type Option = { value: string; label: string };
type Taxonomy = Record<string, Option[]> & { topics: string[] };
type Request = Record<string, string | number>;
type Result = {
  items: (Policy | Opportunity)[];
  count: number;
  page: number;
  view: string;
  answer?: string;
  claims?: { text: string; policy_id: string }[];
  keywords: string[];
  citations: { policy_id: string; title: string }[];
  applied_filters: Request;
  search_backend: string;
  previews?: SearchContext["previews"];
  elapsed_ms?: number;
};

const filterFields: Record<string, [string, string]> = {
  industry: ["核心行业", "industries"], business_domain: ["业务领域", "business_domains"],
  direction_tag: ["技术与政策方向", "direction_tags"], topic: ["主题", ""],
  document_type: ["文件类型", "document_types"], geographic_level: ["发布层级", "geographic_levels"],
  source_grade: ["来源等级", "source_grades"], province: ["省份", ""], city: ["城市", ""],
  region: ["地域", ""], validity_status: ["政策效力", "validity_statuses"],
  category: ["支持类别", "opportunity_categories"], opportunity_status: ["机会状态", "opportunity_statuses"],
  published_from: ["发布起始", ""], published_to: ["发布截至", ""],
};

function readSearchRequest(): Request {
  const params = new URLSearchParams(typeof window === "undefined" ? "" : window.location.search);
  const choice = (key: string, allowed: string[], fallback: string) =>
    allowed.includes(params.get(key) || "") ? params.get(key)! : fallback;
  const view = choice("view", ["policy", "opportunity"], "policy");
  return {
    ...Object.fromEntries(["q", ...Object.keys(filterFields)].map((key) => [key, params.get(key) || ""])),
    view, mode: choice("mode", ["keyword", "natural"], "keyword"),
    sort: choice("sort", view === "opportunity" ? ["comprehensive", "latest", "relevance", "deadline"] : ["comprehensive", "latest", "relevance"], "comprehensive"),
    scope: choice("scope", ["all", "title", "document_number", "issuer"], "all"),
    page: Math.min(10000, Math.max(1, Number.parseInt(params.get("page") || "1", 10) || 1)),
    ...(view === "policy" ? { category: "", opportunity_status: "" } : {}),
  };
}

export default function UnifiedSearch({
  onSelect,
  renderPolicies,
}: {
  onSelect: (id: string) => void;
  renderPolicies: (items: Policy[], search: SearchContext) => ReactNode;
}) {
  const [form] = Form.useForm();
  const [request, setRequest] = useState<Request>(readSearchRequest);
  useEffect(() => {
    const restore = () => { const next = readSearchRequest(); setRequest(next); form.setFieldsValue(next); };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, [form]);
  const [run, setRun] = useState(0);
  const view = Form.useWatch("view", form) || request.view;
  const mode = Form.useWatch("mode", form) || request.mode;
  const taxonomy = useQuery({
    queryKey: ["taxonomy"],
    queryFn: () => api<Taxonomy>("taxonomies"),
  });
  const query = useQuery({
    queryKey: ["unified-search", request, run],
    queryFn: ({ signal }) =>
      api<Result>("search", { method: "POST", body: JSON.stringify(request), signal }),
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: 30_000,
    enabled: request.mode !== "natural" || !!String(request.q || "").trim(),
  });
  const label = (kind: string, value?: string) =>
    taxonomy.data?.[kind]?.find((option) => option.value === value)?.label ||
    value;
  const options = (key: string, title: string) => [
    { value: "", label: title },
    ...(taxonomy.data?.[key] || []),
  ];
  const submit = (values: Request) => {
    const next: Request = { ...request, ...values, q: String(values.q ?? request.q ?? "").trim(), page: 1 };
    if (next.view === "policy") {
      next.category = "";
      next.opportunity_status = "";
      if (next.sort === "deadline") next.sort = "latest";
    }
    form.setFieldsValue(next);
    setRequest(next);
    setRun((n) => n + 1);
    const params = new URLSearchParams();
    Object.entries(next).forEach(([key, value]) => {
      if (value) params.set(key, String(value));
    });
    window.history.pushState(null, "", `/search?${params}`);
  };
  const changePage = (page: number) => {
    const next = { ...request, page };
    setRequest(next);
    const params = new URLSearchParams();
    Object.entries(next).forEach(([key, value]) => {
      if (value) params.set(key, String(value));
    });
    window.history.pushState(null, "", `/search?${params}`);
  };
  const activeFilters = Object.keys(filterFields).filter((key) => request[key]);
  const clearFilters = () => submit({ ...request, ...Object.fromEntries(Object.keys(filterFields).map((key) => [key, ""])) });
  return (
    <div className="search-workspace">
      <Form
        form={form}
        layout="vertical"
        initialValues={request}
        onFinish={submit}
        onValuesChange={(changed, values) => {
          // Typing a query does not spend AI calls. Selects and dates apply immediately.
          if (!("q" in changed) && !("province" in changed) && !("city" in changed)) submit(values);
        }}
      >
        <div className="search-mode-row">
          <Form.Item name="view" noStyle>
            <Segmented
              options={[
                { value: "policy", label: "政策文件" },
                { value: "opportunity", label: "政策机会" },
              ]}
            />
          </Form.Item>
          <Form.Item name="mode" noStyle>
            <Segmented
              options={[
                { value: "keyword", label: "关键词搜索" },
                { value: "natural", label: "自然语言提问" },
              ]}
            />
          </Form.Item>
        </div>
        <Form.Item
          name="q"
          rules={[{ max: 500 }]}
          className="search-main-input"
        >
          <Input.Search
            size="large"
            placeholder={
              mode === "natural"
                ? "例如：南宁目前有哪些污水处理项目申报机会？"
                : "输入政策名称、文号、发文机关或正文关键词"
            }
            aria-label="搜索政策名称、文号、机关或需求"
            allowClear
            enterButton="搜索"
            loading={query.isFetching}
            onSearch={(_, event) => { event?.preventDefault(); form.submit(); }}
          />
        </Form.Item>
        <div className="search-quick-row">
          <Form.Item name="scope" label="搜索范围">
            <Select style={{ width: "100%" }} options={[
              { value: "all", label: "全部内容" }, { value: "title", label: "仅标题" },
              { value: "document_number", label: "仅文号" }, { value: "issuer", label: "仅发文机关" },
            ]} />
          </Form.Item>
          <Form.Item name="sort" label="排序方式">
            <Select
              style={{ width: "100%" }}
              options={[
                { value: "comprehensive", label: "综合排序" },
                { value: "latest", label: "最新发布" },
                { value: "relevance", label: "相关度" },
                {
                  value: "deadline",
                  label: "截止时间",
                  disabled: view !== "opportunity",
                },
              ]}
            />
          </Form.Item>
          <span className="muted small">
            {mode === "natural"
              ? "根据已发布政策回答，附原文依据"
              : "多词用空格分隔，精确词组加英文双引号"}
          </span>
        </div>
        <details className="search-filter-panel">
          <summary>筛选条件{activeFilters.length ? ` · 已选 ${activeFilters.length} 项` : ""}</summary>
          <div className="automation-heading">
            <div>
              <p className="muted">选项自动生效；填写省市后点击“应用筛选”。</p>
            </div>
            <Button
              onClick={clearFilters}
            >
              清除筛选
            </Button>
          </div>
          <div className="search-filter-grid">
            <Form.Item name="industry" label="核心行业">
              <Select options={options("industries", "全部已收录行业")} />
            </Form.Item>
            <Form.Item name="business_domain" label="业务领域">
              <Select
                options={options("business_domains", "全部水务业务领域")}
              />
            </Form.Item>
            <Form.Item name="direction_tag" label="技术与政策方向">
              <Select options={options("direction_tags", "全部方向标签")} />
            </Form.Item>
            <Form.Item name="document_type" label="文件类型">
              <Select options={options("document_types", "全部类型")} />
            </Form.Item>
            <Form.Item name="geographic_level" label="发布层级">
              <Select options={options("geographic_levels", "全部层级")} />
            </Form.Item>
            <Form.Item name="source_grade" label="来源等级">
              <Select
                options={options("source_grades", "全部正式来源").filter((o) =>
                  ["", "L1", "L2", "L3"].includes(o.value),
                )}
              />
            </Form.Item>
            <Form.Item name="province" label="所属省份">
              <Input placeholder="省级行政区全称" />
            </Form.Item>
            <Form.Item name="city" label="所属地级市">
              <Input placeholder="城市全称" />
            </Form.Item>
            <Form.Item name="validity_status" label="政策效力">
              <Select options={options("validity_statuses", "全部效力状态")} />
            </Form.Item>
            <Form.Item name="published_from" label="发布日期起始"><Input type="date" /></Form.Item>
            <Form.Item name="published_to" label="发布日期截至"><Input type="date" /></Form.Item>
            {view === "opportunity" && (
              <>
                <Form.Item name="category" label="机会一级分类">
                  <Select
                    options={options("opportunity_categories", "全部支持类别")}
                  />
                </Form.Item>
                <Form.Item name="opportunity_status" label="机会状态">
                  <Select
                    options={options("opportunity_statuses", "全部机会状态")}
                  />
                </Form.Item>
              </>
            )}
          </div>
          <div className="search-filter-actions"><Button type="primary" onClick={() => form.submit()}>应用筛选</Button></div>
        </details>
      </Form>
      {!!activeFilters.length && <div className="search-active-filters" aria-label="已应用筛选">
        <span className="muted">已选条件</span>
        {activeFilters.map((key) => <Tag key={key} closable onClose={(event) => {
          event.preventDefault(); submit({ ...request, [key]: "" });
        }}>{filterFields[key][0]}：{label(filterFields[key][1], String(request[key]))}</Tag>)}
        <Button type="link" size="small" onClick={clearFilters}>清除全部筛选</Button>
      </div>}
      {query.error ? (
        <Alert type="error" showIcon title={query.error.message} description={<div>
          <Button onClick={() => void query.refetch()}>重新搜索</Button>{" "}
          {request.mode === "natural" && <Button onClick={() => submit({ ...request, mode: "keyword" })}>改用关键词搜索</Button>}
        </div>} />
      ) : query.isFetching ? (
        <div role="status" aria-live="polite"><p className="muted">{request.mode === "natural" ? "正在理解需求、检索政策并核对原文依据，请稍候…" : "正在查找匹配的政策…"}</p><Skeleton active /></div>
      ) : (
        query.data && (
          <>
            {query.data.answer && (
              <Alert
                type="info"
                title={query.data.answer}
                description={
                  <>
                    {query.data.keywords.length > 0 && (
                      <p>解析关键词：{query.data.keywords.join("、")}</p>
                    )}
                    <div className="search-inferred-filters">{Object.keys(filterFields).filter((key) => query.data?.applied_filters[key] && !request[key]).map((key) =>
                      <Tag key={key}>AI识别 · {filterFields[key][0]}：{label(filterFields[key][1], String(query.data!.applied_filters[key]))}</Tag>
                    )}</div>
                    {query.data.claims?.map((claim, index) => (
                      <p key={index}>
                        {claim.text}{" "}
                        <Button
                          size="small"
                          type="link"
                          onClick={() => onSelect(claim.policy_id)}
                        >
                          核对引用
                        </Button>
                      </p>
                    ))}
                  </>
                }
              />
            )}
            <div className="results-heading">
              <h3>
                {query.data.view === "opportunity" ? "政策机会" : "政策文件"}
              </h3>
              <span role="status">共 {query.data.count} 条结果{query.data.elapsed_ms !== undefined ? ` · ${(query.data.elapsed_ms / 1000).toFixed(2)} 秒` : ""}</span>
            </div>
            {!query.data.items.length ? (
              <Empty description={<><p>没有找到匹配的正式记录</p><p className="muted">可缩短关键词、改用政策名称或文号，或减少筛选条件。</p></>}>
                {!!activeFilters.length && <Button onClick={clearFilters}>保留关键词，清除筛选</Button>}
                {request.scope !== "all" && <Button onClick={() => submit({ ...request, scope: "all" })}>改为搜索全部内容</Button>}
              </Empty>
            ) : query.data.view === "policy" ? (
              renderPolicies(query.data.items as Policy[], { keywords: query.data.keywords, previews: query.data.previews })
            ) : (
              (query.data.items as Opportunity[]).map((opportunity) => (
                <article className="policy-card" key={opportunity.id}>
                  <div className="policy-content">
                    <button
                      className="policy-title"
                      onClick={() => onSelect(opportunity.policy)}
                    >
                      <SearchHighlight text={opportunity.title} terms={query.data!.keywords} />
                    </button>
                    {query.data!.previews?.[opportunity.policy] && <p className="search-result-preview">
                      <span className="muted">{query.data!.previews[opportunity.policy].source}：</span>
                      <SearchHighlight text={query.data!.previews[opportunity.policy].text} terms={query.data!.keywords} />
                    </p>}
                    <p>
                      <Tag>
                        {label("opportunity_categories", opportunity.category)}
                      </Tag>
                      <Tag>
                        机会：
                        {label("opportunity_statuses", opportunity.status)}
                      </Tag>
                    </p>
                    {!opportunity.batches.length && (
                      <p className="muted">尚无已核验批次，申报时间待核实。</p>
                    )}
                    {opportunity.batches.map((batch) => (
                      <div key={batch.id}>
                        <strong>{batch.name}</strong>{" "}
                        <Tag>
                          {label("opportunity_statuses", batch.current_status)}
                        </Tag>
                        {batch.closing_soon && (
                          <Tag color="orange">即将截止</Tag>
                        )}
                        <p className="muted">
                          开始：
                          {batch.starts_at
                            ? new Date(batch.starts_at).toLocaleString("zh-CN")
                            : "未明确"}{" "}
                          · 截止：
                          {batch.deadline_at
                            ? new Date(batch.deadline_at).toLocaleString(
                                "zh-CN",
                              )
                            : "未明确 / 无固定截止"}
                        </p>
                        <Button
                          size="small"
                          type="link"
                          onClick={() => onSelect(batch.evidence_policy)}
                        >
                          核对批次依据
                        </Button>
                      </div>
                    ))}
                  </div>
                </article>
              ))
            )}
            <Pagination
              className="pagination"
              current={query.data.page}
              pageSize={20}
              total={query.data.count}
              showSizeChanger={false}
              onChange={changePage}
            />
          </>
        )
      )}
    </div>
  );
}
