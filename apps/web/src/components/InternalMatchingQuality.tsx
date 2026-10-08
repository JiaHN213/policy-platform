"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Empty, Select } from "antd";
import { api, type Page } from "@/lib/api";
import MatchingEvaluation from "./customer/MatchingEvaluation";
export default function InternalMatchingQuality() {
  const [selected, setSelected] = useState("");
  const profiles = useQuery({ queryKey: ["enterprises"], queryFn: () => api<{ items: { id: string; name: string }[] }>("enterprises") });
  const profile = profiles.data?.items.find(item => item.id === selected) || profiles.data?.items[0];
  const projects = useQuery({ queryKey: ["enterprise-projects", profile?.id], queryFn: () => api<Page<{ id: string; name: string }>>(`enterprise-projects?profile=${profile!.id}&page_size=100`), enabled: !!profile });
  const options = useQuery({ queryKey: ["enterprise-options"], queryFn: () => api<{ fields: Record<string, string>; tags: Record<string, { value: string; label: string }[]> }>("enterprises/options") });
  return <><p className="muted">仅能核验本人具有成员权限的企业，不因内部身份开放客户私有资料。</p>{(profiles.error || options.error || projects.error) && <Alert type="error" title={(profiles.error || options.error || projects.error)?.message} />}{profile ? <><Select className="space-bottom" aria-label="已授权企业" style={{ minWidth: 240 }} value={profile.id} options={profiles.data?.items.map(item => ({ value: item.id, label: item.name }))} onChange={setSelected} />{options.data && <MatchingEvaluation key={profile.id} profile={profile.id} projects={projects.data?.items || []} options={options.data} />}</> : <Empty description="暂无已授权的企业。企业匹配核验需要独立的企业成员授权。" />}</>;
}
