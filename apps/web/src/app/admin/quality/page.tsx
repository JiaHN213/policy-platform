"use client";
import QualityEvaluation from "@/components/QualityEvaluation";
import InternalMatchingQuality from "@/components/InternalMatchingQuality";
import { Tabs } from "antd";
export default function Page() {
  return <Tabs items={[{ key: "policy", label: "政策质量", children: <QualityEvaluation /> }, { key: "matching", label: "企业匹配核验", children: <InternalMatchingQuality /> }]} />;
}
