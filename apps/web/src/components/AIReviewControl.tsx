"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Tag } from "antd";
import { api } from "@/lib/api";

export type AIConfiguration = {
  configured: boolean;
  model: string;
  automation_enabled: boolean;
  eligible_count: number;
  queued_count: number;
  running_count: number;
  succeeded_count: number;
  failed_count: number;
};

export function useAIReviewConfiguration() {
  return useQuery({
    queryKey: ["ai-configuration"],
    queryFn: () =>
      api<AIConfiguration>("admin/policy-enrichments/configuration"),
    refetchInterval: 5000,
  });
}

export default function AIReviewControl({ title }: { title?: string }) {
  const client = useQueryClient();
  const configuration = useAIReviewConfiguration();
  const automationEnabled = configuration.data?.automation_enabled ?? false;
  const toggleAutomation = useMutation({
    mutationFn: () =>
      api<AIConfiguration>(
        `admin/policy-enrichments/${automationEnabled ? "stop-automation" : "start-automation"}`,
        { method: "POST" },
      ),
    onSuccess: (data) => {
      client.setQueryData(["ai-configuration"], data);
      void client.invalidateQueries({ queryKey: ["ai-jobs"] });
      void client.invalidateQueries({ queryKey: ["review"] });
    },
  });
  const queueStatus = automationEnabled
    ? configuration.data?.running_count
      ? `正在审核 ${configuration.data.running_count} 份，另有 ${configuration.data.queued_count} 份等待`
      : `已开始，${configuration.data?.queued_count || 0} 份等待审核`
    : configuration.data?.running_count
      ? "已停止领取新任务，当前文件完成后暂停"
      : `已暂停，${configuration.data?.queued_count || 0} 份等待审核`;

  return (
    <div style={{ marginBottom: 20 }}>
      {title && <h3>{title}</h3>}
      <Alert
        showIcon
        type={
          !configuration.data?.configured
            ? "warning"
            : automationEnabled
              ? "success"
              : "info"
        }
        title={
          configuration.data?.configured
            ? `审核模型：${configuration.data.model}`
            : "请先配置 AI 模型服务，完成后重启服务"
        }
        description={<details className="inline-help"><summary>审核说明</summary><p>按入队顺序提取摘要、分类、标签、效力与机会，符合条件后自动发布。政策关系由 Wiki LLM 独立构建。停止后不再领取新任务，正在审核的文件会继续完成。</p></details>}
      />
      <div
        style={{ display: "flex", gap: 12, marginTop: 12, flexWrap: "wrap" }}
      >
        <Button
          type={automationEnabled ? "default" : "primary"}
          danger={automationEnabled}
          disabled={!configuration.data?.configured}
          loading={configuration.isLoading || toggleAutomation.isPending}
          onClick={() => toggleAutomation.mutate()}
        >
          {automationEnabled ? "停止自动审核" : "开始自动审核"}
        </Button>
        <Tag color={automationEnabled ? "green" : "default"}>{queueStatus}</Tag>
      </div>
      {configuration.error && (
        <Alert
          style={{ marginTop: 12 }}
          type="error"
          title={configuration.error.message}
        />
      )}
      {toggleAutomation.error && (
        <Alert
          style={{ marginTop: 12 }}
          type="error"
          title={toggleAutomation.error.message}
        />
      )}
    </div>
  );
}
