import type { EvaluationDocument } from "./SourceReader";

export type Kind = "opportunity" | "relation" | "knowledge";
export type Gold = { opportunity_level?: string; verdict?: boolean; unsure?: boolean; notes?: string; evidence_supported?: boolean | null; evidence_policy_id?: string; evidence_quote?: string };
export type Prediction = { value: string | boolean; model?: string; quotes: number; grounded_quotes: number; citations?: { quote: string; policy_id?: string }[] };
export type Sample = { id: string; title: string; kind: Kind; kind_label: string; status: string; status_label: string; label_version: number; gold: Gold; policy: string | null; related_policy: string | null; page: string | null; relation_kind: string; labeled_by_name: string; labeled_at: string | null };
export type SampleDetail = Sample & { stale_reason: string; prediction: Prediction | null; prediction_hash: string; snapshot: { documents: EvaluationDocument[]; page_body?: string; citations?: { quote: string; title?: string }[] } };
export type Metric = { labeled: number; evaluated: number; correct: number; stale: number; unavailable: number; tp: number; fp: number; fn: number; tn: number; accuracy: number | null; precision: number | null; recall: number | null; miss_rate: number | null; false_positive_rate: number | null; citation_grounding: number | null; human_evidence_support: number | null };
export type Run = { id: string; status: string; created_at: string; config_version: string; metrics: Record<Kind, Metric> };
export type Result = { id: string; sample: string; sample_title: string; kind: Kind; correct: boolean | null; status: string; reason: string; gold: Gold; prediction: Prediction };
export const kinds = [{ value: "opportunity", label: "政策机会判断" }, { value: "relation", label: "文件关系判断" }, { value: "knowledge", label: "知识页依据核对" }];
export const levels = [
  { value: "NONE", label: "没有政策机会", description: "没有可获得的支持或参与机会，例如一般工作动态、单纯监管要求。" },
  { value: "SUPPORT_SIGNAL", label: "有支持方向，细则还不够明确", description: "提到鼓励或扶持，但尚不能明确谁能获得什么、如何参与。" },
  { value: "FORMAL_OPPORTUNITY", label: "有明确的政策机会", description: "支持对象、支持内容和参与条件或机制明确；常态化税惠等不一定有申报截止日。" },
];
export const statusLabels: Record<string, string> = { pending: "待评测", labeled: "已完成", needs_help: "待协助判断", retired: "已停用" };
export const percentage = (value: number | null | undefined) => value == null ? "暂无数据" : `${(value * 100).toFixed(1)}%`;
export const verdictLabel = (kind: Kind, value: string | boolean | undefined) => typeof value === "boolean" ? (kind === "relation" ? (value ? "存在该方向及类型的关系" : "不存在该关系") : (value ? "有原文支持" : "原文支持不足")) : levels.find(item => item.value === value)?.label || "暂无可比较的系统结果";
