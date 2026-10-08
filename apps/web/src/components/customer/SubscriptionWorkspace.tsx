"use client";
import { Collapse, Tabs } from "antd";
import { usePolicyWorkspace } from "@/components/workspace/WorkspaceShell";
import SubscriptionPanel from "./SubscriptionPanel";
import NotificationPanel from "./NotificationPanel";
import ProfileSubscription from "./ProfileSubscription";
import NotificationSettings from "./NotificationSettings";
export default function SubscriptionWorkspace({ messages = false, initialProfile, initialProject }: { messages?: boolean; initialProfile?: string; initialProject?: string }) {
  const { openPolicy } = usePolicyWorkspace();
  return <><Collapse ghost className="space-bottom" items={[{ key: "preferences", label: "提醒时间与通知偏好", children: <NotificationSettings /> }]} /><Tabs defaultActiveKey={messages ? "messages" : "subscriptions"} items={[
    { key: "subscriptions", label: "订阅管理", children: <><ProfileSubscription initialProfile={initialProfile} initialProject={initialProject} /><SubscriptionPanel /></> },
    { key: "messages", label: "收到的提醒", children: <NotificationPanel onSelect={openPolicy} /> },
  ]} /></>;
}
