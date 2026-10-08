"use client";

import { useRef, useState } from "react";
import { Alert, App, Button, Form, Input, Radio, Select, Space } from "antd";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import SourceReader from "./SourceReader";
import { levels, verdictLabel, type SampleDetail } from "./types";

type Answer = { decision: string; notes: string; evidence: string; evidence_policy_id?: string; evidence_quote?: string };

export default function SampleReview({ sample, relationLabel, onSaved, onDirty, onBusy, onReload, onSkip }: {
  sample: SampleDetail; relationLabel: string; onSaved: (next: boolean) => void;
  onDirty: (dirty: boolean) => void; onBusy: (busy: boolean) => void; onReload: () => void; onSkip: () => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm<Answer>();
  const decision = Form.useWatch("decision", form);
  const [showSystem, setShowSystem] = useState(false);
  const nextAfterSave = useRef(true);
  const blocked = !!sample.stale_reason || sample.status === "retired";
  const positive = decision && decision !== "unsure" && decision !== "NONE" && decision !== "no";
  const initialDecision = sample.gold.unsure ? "unsure" : sample.kind === "opportunity" ? sample.gold.opportunity_level : sample.gold.verdict === undefined ? undefined : sample.gold.verdict ? "yes" : "no";
  const save = useMutation({
    mutationFn: (values: Answer) => api<SampleDetail>(`admin/quality/samples/${sample.id}/label`, { method: "POST", body: JSON.stringify({
      notes: values.notes.trim(), unsure: values.decision === "unsure",
      ...(sample.kind === "opportunity" ? { opportunity_level: values.decision === "unsure" ? undefined : values.decision } : { verdict: values.decision === "unsure" ? undefined : values.decision === "yes" }),
      evidence_supported: values.evidence === "yes" ? true : values.evidence === "no" ? false : null,
      evidence_policy_id: values.evidence_policy_id || null, evidence_quote: values.evidence_quote?.trim() || "",
      label_version: sample.label_version, prediction_hash: sample.prediction_hash,
    }) }),
    onMutate: () => onBusy(true),
    onSuccess: result => { onDirty(false); message.success(result.status === "needs_help" ? "已放入待协助判断，不计入正确或错误结果" : "判断已保存"); onSaved(nextAfterSave.current); },
    onError: (error: Error) => message.error(error.message),
    onSettled: () => onBusy(false),
  });
  const choices = sample.kind === "opportunity" ? levels : sample.kind === "relation" ? [
    { value: "yes", label: "存在这项关系", description: "文件之间确有上述联系，关系类型和方向都正确。" },
    { value: "no", label: "不存在这项关系", description: "只是主题接近，或所写的关系类型、方向不正确。" },
  ] : [
    { value: "yes", label: "主要结论都有原文依据", description: "引用的文件能支持结论，且没有扩大适用范围或改变原意。" },
    { value: "no", label: "存在无依据或错误的结论", description: "引用不支持结论、信息拼接错误，或结论超出了原文。" },
  ];
  return <div className="quality-review-layout">
    <section className="quality-review-source" aria-label="阅读材料">
      {sample.kind === "relation" && <div className="quality-relation-context"><strong>本次核对的关系方向</strong><p>文件 1：{sample.snapshot.documents[0]?.title}</p><p>↓ {relationLabel}</p><p>文件 2：{sample.snapshot.documents[1]?.title}</p></div>}
      <SourceReader documents={sample.snapshot.documents} pageBody={sample.snapshot.page_body} onQuote={(id, text) => {
        if (blocked || save.isPending) return;
        form.setFieldsValue({ evidence_policy_id: id, evidence_quote: text }); onDirty(true); message.success("已填入原文依据");
      }} />
    </section>
    <section className="quality-review-answer" aria-label="填写人工判断">
      <Form form={form} layout="vertical" initialValues={{ decision: initialDecision, notes: sample.gold.notes, evidence: sample.gold.evidence_supported == null ? "unknown" : sample.gold.evidence_supported ? "yes" : "no", evidence_policy_id: sample.gold.evidence_policy_id || (sample.snapshot.documents.length === 1 ? sample.snapshot.documents[0].id : undefined), evidence_quote: sample.gold.evidence_quote }} onValuesChange={() => onDirty(true)} onFinish={values => save.mutate(values)} disabled={blocked || save.isPending}>
        <h3>1．先根据原文作出判断</h3>
        <Form.Item name="decision" rules={[{ required: true, message: "请选择你的判断，也可以选择暂时无法判断。" }]}>
          <Radio.Group className="quality-choices" aria-label="人工判断">
            {[...choices, { value: "unsure", label: "暂时无法判断", description: "资料不完整或需要同事协助，暂不计入评测得分。" }].map(item => <Radio key={item.value} value={item.value}><strong>{item.label}</strong><span>{item.description}</span></Radio>)}
          </Radio.Group>
        </Form.Item>
        <Form.Item name="notes" label={decision === "unsure" ? "还缺什么信息，或需要谁协助？" : "用一句话说明原因"} rules={[{ required: true, whitespace: true, min: 5, max: 3000, message: "请写至少 5 个字，说明判断理由或缺少的信息。" }]}>
          <Input.TextArea rows={3} maxLength={3000} placeholder={decision === "unsure" ? "例如：正文只引用附件，但附件暂时无法打开。" : "例如：原文明确了支持对象和补助方式，企业可按条件参与。"} />
        </Form.Item>
        {decision !== "unsure" && <>
          <Form.Item name="evidence_policy_id" label="依据来自哪份文件" rules={[{ required: !!positive, message: "请选择引用的原文文件。" }]}>
            <Select allowClear placeholder="选择文件，或在左侧选中文字引用" options={sample.snapshot.documents.map(doc => ({ value: doc.id, label: doc.title }))} />
          </Form.Item>
          <Form.Item name="evidence_quote" label={positive ? "引用原文依据（必填）" : "引用原文依据（选填）"} dependencies={["evidence_policy_id", "decision"]} rules={[{ validator: async (_, value: string | undefined) => {
            const text = value?.trim() || "";
            if (!positive && !text) return;
            const doc = sample.snapshot.documents.find(item => item.id === form.getFieldValue("evidence_policy_id"));
            if (!doc || text.length < 5 || !doc.body.includes(text)) throw new Error("请引用所选文件中至少 5 个连续字符，可直接使用左侧的引用按钮。");
          } }]}><Input.TextArea rows={3} maxLength={10000} placeholder="选中左侧原文后点击“引用所选文字”，也可复制粘贴。" /></Form.Item>
          <h3>2．可选：核对系统的依据</h3>
          {!showSystem ? <Button className="space-bottom" disabled={!decision || blocked || save.isPending} onClick={() => setShowSystem(true)}>我已作出判断，查看系统结果</Button> : <div className="quality-system-result">
            <strong>{verdictLabel(sample.kind, sample.prediction?.value)}</strong>
            {sample.prediction?.citations?.map((citation, i) => <blockquote key={i}>{citation.quote}</blockquote>)}
            {!sample.prediction?.citations?.length && <p className="muted">当前系统结果没有提供可核对的引用。</p>}
            <Form.Item name="evidence" label="这些依据是否足以支持系统结论？"><Radio.Group className="quality-evidence-options" options={[{ value: "yes", label: "足以支持" }, { value: "no", label: "不足或不相符" }, { value: "unknown", label: "暂不判断" }]} /></Form.Item>
          </div>}
        </>}
        {save.error && <Alert className="space-bottom" type="error" title={save.error.message} action={<Button onClick={onReload}>重新读取</Button>} />}
        <div className="quality-review-actions"><Space wrap>
          <Button type="primary" loading={save.isPending} onClick={() => { nextAfterSave.current = true; form.submit(); }}>保存并下一份</Button>
          <Button loading={save.isPending} onClick={() => { nextAfterSave.current = false; form.submit(); }}>保存并返回</Button>
          <Button onClick={onSkip}>暂时跳过</Button>
        </Space><p className="muted">仅保存评测意见，不会自动改动政策或发布结果。</p></div>
      </Form>
    </section>
  </div>;
}
