"use client";
import { Tabs } from "antd";
import UnifiedSearch from "@/components/UnifiedSearch";
import { PolicyCards } from "@/components/policy/common";
import { usePolicyWorkspace } from "@/components/workspace/WorkspaceShell";
import LatestPolicies from "./LatestPolicies";
export default function PolicyLibrary({ latest = false }: { latest?: boolean }) {
  const { openPolicy } = usePolicyWorkspace();
  return <Tabs defaultActiveKey={latest ? "latest" : "search"} items={[
    { key: "search", label: "搜索政策", children: <UnifiedSearch onSelect={openPolicy} renderPolicies={(items, search) => <PolicyCards items={items} search={search} onSelect={openPolicy} />} /> },
    { key: "latest", label: "最新发布", children: <LatestPolicies onSelect={openPolicy} /> },
  ]} />;
}
