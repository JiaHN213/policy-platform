"use client";

import { useRef, useState } from "react";
import { Alert, Button, Empty, Input, Space, Tabs } from "antd";
import { safeExternalUrl } from "@/lib/api";

export type EvaluationDocument = {
  id: string; title: string; body: string; version: number; source_url: string;
  attachments: { url: string; parse_status: string }[];
};

/** Quotes come from frozen source text, never from the AI answer. */
function DocumentReader({ document, onQuote }: { document: EvaluationDocument; onQuote: (id: string, text: string) => void }) {
  const root = useRef<HTMLPreElement>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState("");
  const [match, setMatch] = useState(0);
  const pieces = query ? document.body.split(query) : [document.body];
  const count = pieces.length - 1;
  function selectText() {
    const selection = window.getSelection();
    const text = selection?.toString().trim() || "";
    setSelected(selection && root.current?.contains(selection.anchorNode) && root.current?.contains(selection.focusNode) && document.body.includes(text) ? text : "");
  }
  function locate() {
    if (!count) return;
    root.current?.querySelectorAll("mark")[match % count]?.scrollIntoView({ block: "center", behavior: "smooth" });
    setMatch((match + 1) % count);
  }
  return <>
    <h3 className="quality-document-title">{document.title}</h3>
    <Space wrap className="space-bottom">
      <span className="muted">抽样时保存的第 {document.version} 版</span>
      {safeExternalUrl(document.source_url) && <a href={safeExternalUrl(document.source_url)} target="_blank" rel="noopener noreferrer">官方原文 ↗</a>}
      {document.attachments.map((item, i) => safeExternalUrl(item.url) && <a key={i} href={safeExternalUrl(item.url)} target="_blank" rel="noopener noreferrer">原件 / 附件 {i + 1} ↗</a>)}
    </Space>
    {!!document.attachments.length && <p className="muted">附件链接指向来源网站，内容可能已更新；本次引用请以保存的正文为准。</p>}
    <div className="quality-reader-tools">
      <Input aria-label="在原文中查找" placeholder="在原文中查找关键词" value={query} onChange={event => { setQuery(event.target.value); setMatch(0); }} allowClear onPressEnter={locate} />
      <Button disabled={!count} onClick={locate}>定位{query ? `（${count}处）` : ""}</Button>
    </div>
    <div className="quality-quote-tools">
      <span className="muted">选中原文中的一段文字，再点击引用。</span>
      <Button disabled={selected.length < 5} onClick={() => onQuote(document.id, selected)}>引用所选文字</Button>
    </div>
    {document.body ? <pre ref={root} tabIndex={0} aria-label={`${document.title}全文`} className="quality-source-text" onMouseUp={selectText} onKeyUp={selectText}>
      {pieces.map((piece, i) => <span key={i}>{i > 0 && <mark>{query}</mark>}{piece}</span>)}
    </pre> : <Alert type="warning" showIcon title="没有可供核对的正文" description="可以选择“暂时无法判断”，留待补充资料后再评测。" />}
  </>;
}

export default function SourceReader({ documents, onQuote, pageBody }: { documents: EvaluationDocument[]; onQuote: (id: string, text: string) => void; pageBody?: string }) {
  if (!documents.length && !pageBody) return <Empty description="没有可阅读的材料，请选择暂时无法判断" />;
  return <Tabs className="quality-source-tabs" items={[
    ...(pageBody ? [{ key: "knowledge-page", label: "待核对的知识页", children: <><p className="muted">核对其中的结论，再切换到引用文件阅读依据。</p><pre className="quality-source-text">{pageBody}</pre></> }] : []),
    ...documents.map((document, i) => ({ key: document.id, label: documents.length === 1 ? "政策全文" : `文件 ${i + 1}`, children: <DocumentReader document={document} onQuote={onQuote} /> })),
  ]} />;
}
