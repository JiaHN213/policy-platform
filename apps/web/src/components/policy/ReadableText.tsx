"use client";

import { useId, useState } from "react";
import { Button } from "antd";

type Props = { text: string; previewChars?: number; expandLabel?: string; collapseLabel?: string };

/** Preserve source wording and paragraph breaks without interpreting supplied HTML. */
export default function ReadableText(props: Props) {
  return <TextContent key={props.text} {...props} />;
}

function TextContent({ text, previewChars, expandLabel = "展开全部", collapseLabel = "收起" }: Props) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const boundary = previewChars ? text.slice(previewChars, previewChars + 80).search(/[。！？\n]/) : -1;
  const end = previewChars ? previewChars + (boundary >= 0 ? boundary + 1 : 0) : text.length;
  const long = text.length > end;
  const visible = long && !expanded ? `${text.slice(0, end)}…` : text;
  return <div className="reading-text">
    <div id={id} className="reading-prose">{visible.split(/\r?\n/).filter(line => line.trim()).map((line, index) => <p key={index}>{line}</p>)}</div>
    {long && <Button type="link" size="small" className="reading-expand" aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded(!expanded)}>{expanded ? collapseLabel : expandLabel}</Button>}
  </div>;
}
