"use client";

import { useState } from "react";
import { Alert, App, Button, Collapse, Form, Input, Radio, Space } from "antd";

export type IntakeInput = Record<string, unknown> | FormData;
type Identity = { id: string; name: string; data: Record<string, string | string[]>; research_method: string };

export default function EnterpriseIntake({ profile, searchReady, aiReady, busy, onSubmit, onManual, onboarding = false }: {
  profile?: Identity;
  searchReady: boolean;
  aiReady: boolean;
  busy: boolean;
  onSubmit: (input: IntakeInput) => void;
  onManual: (name: string) => void;
  onboarding?: boolean;
}) {
  const [form] = Form.useForm();
  const { message } = App.useApp();
  const [mode, setMode] = useState(profile?.research_method === "search" || (!profile && searchReady) ? "search" : profile?.research_method === "materials" ? "text" : "website");
  const [file, setFile] = useState<File>();
  const submit = (values: Record<string, string>) => {
    const input: Record<string, unknown> = { kind: "company", source_mode: mode, name: values.name, ...(profile ? { profile_id: profile.id } : {}) };
    if (mode === "website") input.website = values.website;
    if (mode === "text") input.introduction = values.introduction;
    if (mode === "search") {
      input.city = values.city || "";
      input.credit_code = values.credit_code || "";
    }
    if (mode === "file") {
      if (!file) { message.warning("请先选择企业介绍文件。"); return; }
      if (file.size > 5 * 1024 * 1024) { message.warning("文件不能超过5MB。"); return; }
      const body = new FormData();
      Object.entries(input).forEach(([key, value]) => body.append(key, String(value)));
      body.append("file", file);
      onSubmit(body);
    } else onSubmit(input);
  };
  return <Form form={form} layout="vertical" onFinish={submit} initialValues={{ name: profile?.name, website: profile?.data.website || "", city: profile?.data.city || "", credit_code: profile?.data.credit_code || "" }}>
    <Form.Item label="选择方便你的方式">
      <Radio.Group value={mode} onChange={event => { setMode(event.target.value); setFile(undefined); }} optionType="button" buttonStyle="solid" options={[{ value: "search", label: "只填企业名称" }, { value: "website", label: "填写官网" }, { value: "text", label: "粘贴简介" }, { value: "file", label: "上传介绍" }]} />
    </Form.Item>
    {!aiReady && <Alert type="info" title="企业画像模型尚未配置，可先直接填写画像。" />}
    {mode === "search" && !searchReady && <Alert type="info" title="联网搜索尚未配置，请选择官网、上传或粘贴方式，无需搜索密钥。" />}
    <Form.Item name="name" label="企业全称" rules={[{ required: true, whitespace: true, max: 200 }]}><Input disabled={!!profile} maxLength={200} placeholder="填写企业全称，避免与其他企业混淆" /></Form.Item>
    {mode === "website" && <Form.Item name="website" label="企业官网或企业介绍页面" extra="读取首页及少量同站介绍、业务和案例页面；不需要搜索服务密钥。" rules={[{ required: true, whitespace: true, message: "请填写企业官网" }]}><Input maxLength={500} placeholder="例如：www.example.com" /></Form.Item>}
    {mode === "text" && <Form.Item name="introduction" label="企业简介" extra="粘贴主营业务、技术能力或代表项目，AI 会整理成画像草稿。" rules={[{ required: true, min: 20, max: 40000, message: "请粘贴20至40000字的企业简介" }]}><Input.TextArea rows={7} maxLength={40000} showCount placeholder="从已有企业介绍中复制粘贴即可，不需要按表格填写。" /></Form.Item>}
    {mode === "file" && <Form.Item label="企业介绍文件" required extra="支持 PDF、DOCX、PPTX、TXT、Markdown，最大5MB。扫描件或纯图片请改用文字版。"><input aria-label="上传企业介绍文件" type="file" accept=".pdf,.docx,.pptx,.txt,.md" onChange={event => setFile(event.target.files?.[0])} /></Form.Item>}
    {mode === "search" && <Collapse ghost items={[{ key: "hints", label: "补充城市或信用代码（可选）", children: <><Form.Item name="city" label="所在城市"><Input maxLength={100} /></Form.Item><Form.Item name="credit_code" label="统一社会信用代码"><Input maxLength={18} /></Form.Item></> }]} />}
    <p className="small muted">{mode === "file" || mode === "text" ? "介绍材料只提交给平台配置的画像模型，不发送给网页搜索服务。" : "系统只根据实际读取的资料生成草稿。"}核对并确认后才会更新画像。</p>
    <Space wrap><Button htmlType="submit" type="primary" loading={busy} disabled={!aiReady || (mode === "search" && !searchReady)}>{onboarding ? "自动整理企业信息" : mode === "website" ? "读取官网并生成草稿" : mode === "search" ? "搜索并生成草稿" : "整理介绍并生成草稿"}</Button><Button onClick={async () => { try { const values = await form.validateFields(["name"]); onManual(values.name); } catch { /* Field-level validation is shown by the form. */ } }}>直接填写画像</Button></Space>
  </Form>;
}
