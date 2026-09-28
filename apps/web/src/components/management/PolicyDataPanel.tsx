"use client";

import CatalogAdmin from "@/components/CatalogAdmin";




import SearchIndexPanel from "@/components/management/SearchIndexPanel";
export default function PolicyDataPanel() {
  return (
    <>
      <div className="management-section-intro">
        <div>
          <div className="eyebrow">结构化信息</div>
          <h3>机会、关系与效力</h3>
        </div>
        <p className="muted">
          在同一处补充政策机会、批次、政策关系和效力变化。日常审核结果会自动带入，人工只需修正少量异常。
        </p>
      </div>
      <CatalogAdmin />
      <details className="data-maintenance">
        <summary>
          <span>
            <strong>搜索索引维护</strong>
            <small>低频操作；日常数据会自动同步，无需手动处理</small>
          </span>
          <span className="data-maintenance-action">展开</span>
        </summary>
        <div className="data-maintenance-content">
          <SearchIndexPanel />
        </div>
      </details>
    </>
  );
}

