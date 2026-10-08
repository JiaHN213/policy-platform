"use client";

import {
api,
type Page,
type Policy
} from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import {
Button,
Empty,
Pagination,
Select,
Skeleton
} from "antd";
import { useState } from "react";


import { ErrorBox,PolicyCards } from "@/components/policy/common";

export default function LatestPolicies({ onSelect }: { onSelect: (id: string) => void }) {
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({
    topic: "",
    document_type: "",
    business_domain: "",
    direction_tag: "",
    geographic_level: "",
    validity_status: "",
    source_grade: "",
  });
  const taxonomy = useQuery({
    queryKey: ["taxonomy"],
    queryFn: () =>
      api<Record<string, { value: string; label: string }[]>>("taxonomies"),
  });
  const queryString = new URLSearchParams({ page: String(page) });
  Object.entries(filters).forEach(([key, value]) => {
    if (value) queryString.set(key, value);
  });
  const query = useQuery({
    queryKey: ["latest-policies", page, filters],
    queryFn: () => api<Page<Policy>>(`policies?${queryString.toString()}`),
  });
  const options = (key: string, allLabel: string) => [
    { value: "", label: allLabel },
    ...(taxonomy.data?.[key] || []),
  ];
  const updateFilter = (key: keyof typeof filters, value: string) => {
    setFilters((current) => ({ ...current, [key]: value || "" }));
    setPage(1);
  };
  return (
    <>
      <details className="search-filter-panel">
        <summary>筛选条件{Object.values(filters).filter(Boolean).length ? `（已选 ${Object.values(filters).filter(Boolean).length} 项）` : ""}</summary>
        <div className="automation-heading">
          <div>
            <h3>筛选条件</h3>
          </div>
          <Button
            onClick={() => {
              setFilters({
                topic: "",
                document_type: "",
                business_domain: "",
                direction_tag: "",
                geographic_level: "",
                validity_status: "",
                source_grade: "",
              });
              setPage(1);
            }}
          >
            清除筛选
          </Button>
        </div>
        <div className="search-filter-grid">
          <Select
            aria-label="政策主题"
            value={filters.topic}
            options={[
              { value: "", label: "全部主题" },
              { value: "水务", label: "水务" },
              { value: "环保", label: "环保" },
              { value: "人工智能＋", label: "人工智能＋" },
            ]}
            onChange={(value) => updateFilter("topic", value)}
          />
          <Select
            aria-label="文件类型"
            value={filters.document_type}
            options={options("document_types", "全部文件类型").filter(
              (item) => item.value !== "unclassified",
            )}
            onChange={(value) => updateFilter("document_type", value)}
          />
          <Select
            aria-label="业务领域"
            value={filters.business_domain}
            options={options("business_domains", "全部业务领域")}
            onChange={(value) => updateFilter("business_domain", value)}
          />
          <Select
            aria-label="技术与政策方向"
            value={filters.direction_tag}
            options={options("direction_tags", "全部技术与政策方向")}
            onChange={(value) => updateFilter("direction_tag", value)}
          />
          <Select
            aria-label="发布层级"
            value={filters.geographic_level}
            options={options("geographic_levels", "全部发布层级").filter(
              (item) => item.value !== "unverified",
            )}
            onChange={(value) => updateFilter("geographic_level", value)}
          />
          <Select
            aria-label="政策效力"
            value={filters.validity_status}
            options={options("validity_statuses", "全部政策效力")}
            onChange={(value) => updateFilter("validity_status", value)}
          />
          <Select
            aria-label="来源等级"
            value={filters.source_grade}
            options={options("source_grades", "全部正式来源").filter((item) =>
              ["", "L1", "L2", "L3"].includes(item.value),
            )}
            onChange={(value) => updateFilter("source_grade", value)}
          />
        </div>
      </details>
      <div className="results-heading">
        <h3>最新发布</h3>
        <span>{query.data?.count || 0} 条可查政策</span>
      </div>
      {query.isLoading ? (
        <Skeleton active />
      ) : query.error ? (
        <ErrorBox error={query.error} retry={() => query.refetch()} />
      ) : !query.data?.items.length ? (
        <Empty description="没有符合当前筛选条件的已发布政策" />
      ) : (
        <>
          <PolicyCards items={query.data.items} onSelect={onSelect} />
          <Pagination
            className="pagination"
            current={page}
            total={query.data.count}
            pageSize={20}
            showSizeChanger={false}
            onChange={setPage}
          />
        </>
      )}
    </>
  );
}

