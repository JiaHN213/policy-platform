"use client";

import { useState } from "react";
import { Segmented } from "antd";

const chapter = /^第[一二三四五六七八九十百千万零〇0-9]+[章节编]/;
const section = /^[一二三四五六七八九十百]+[、．]/;
const subheading = /^[（(][一二三四五六七八九十百0-9]+[）)]/;
const article = /^(第[一二三四五六七八九十百千万零〇0-9]+条)(\s*.*)$/;
const shortHeading = (line: string) => line.length <= 70 && !/[。！？；;]/.test(line);
const tableCells = (line: string) => /(?:\s\|\s|^\|\s|\s\|$)/.test(line) ? line.split("|").map(cell => cell.trim()) : [];

/** Presentation only: stored source, evidence offsets and copyable raw text stay intact. */
export default function PolicyBody({ text }: { text: string }) {
  const [mode, setMode] = useState("reading");
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    // Tables require consecutive rows with the same number of explicit cells.
    // Never infer a table from prose, spaces, monetary values or dates.
    const cells = tableCells(lines[i]);
    if (cells.length > 1 && tableCells(lines[i + 1] || "").length === cells.length) {
      const rows = [cells];
      while (tableCells(lines[i + 1] || "").length === cells.length) rows.push(tableCells(lines[++i]));
      blocks.push(<div className="policy-text-table" key={i} tabIndex={0} role="region" aria-label="原文表格，可横向滚动"><table><tbody>{rows.map((row, index) => <tr key={index}>{row.map((cell, column) => <td key={column}>{cell}</td>)}</tr>)}</tbody></table></div>);
      continue;
    }
    if (chapter.test(line) && shortHeading(line)) {
      blocks.push(<h3 key={i}>{line}</h3>);
    } else if (section.test(line) && shortHeading(line)) {
      blocks.push(<h4 key={i}>{line}</h4>);
    } else if (subheading.test(line) && shortHeading(line)) {
      blocks.push(<h5 key={i}>{line}</h5>);
    } else {
      const clause = line.match(article);
      // Put a detached article label beside its following paragraph, but never
      // consume another heading or a table. The raw view retains every newline.
      if (clause && !clause[2].trim()) {
        let next = i + 1;
        while (next < lines.length && !lines[next].trim()) next++;
        const continuation = lines[next]?.trim();
        if (continuation && !chapter.test(continuation) && !section.test(continuation) && !subheading.test(continuation) && !article.test(continuation) && !continuation.includes("|")) {
          blocks.push(<p className="policy-text-article" key={i}><strong>{clause[1]}</strong>{"　"}{continuation}</p>);
          i = next;
          continue;
        }
      }
      blocks.push(<p key={i} className={clause ? "policy-text-article" : undefined}>{clause ? <><strong>{clause[1]}</strong>{"　"}{clause[2].trim()}</> : line}</p>);
    }
  }
  return <section className="policy-text-reader">
    <div className="policy-text-toolbar"><Segmented aria-label="正文显示方式" value={mode} onChange={setMode} options={[{ value: "reading", label: "阅读排版" }, { value: "raw", label: "原始文本" }]} /></div>
    {mode === "raw" ? <pre className="policy-text-raw">{text}</pre> : <div className="policy-text-content">{blocks}</div>}
  </section>;
}
