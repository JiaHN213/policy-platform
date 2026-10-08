"use client";

import {
api
} from "@/lib/api";
import {
FileSearchOutlined
} from "@ant-design/icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import {
Button,
Form,
Input
} from "antd";
import { useState } from "react";


import { ErrorBox } from "@/components/policy/common";

export default function Login({ onSuccess }: { onSuccess: () => void }) {
  const router = useRouter();
  const client = useQueryClient();
  const [register, setRegister] = useState(false);
  const mutation = useMutation({
    mutationFn: (values: {
      username: string;
      password: string;
      password_confirm?: string;
    }) =>
      api(register ? "auth/register" : "auth/login", {
        method: "POST",
        body: JSON.stringify(values),
      }),
    onSuccess: () => {
      if (register) { client.clear(); router.replace("/welcome"); }
      else onSuccess();
    },
  });
  return (
    <main className="login-screen">
      <section className="login-intro">
        <div className="wordmark">
          <span className="brand-mark">
            <FileSearchOutlined />
          </span>
          政策观察
        </div>
        <div className="eyebrow">POLICY OBSERVER / 工作台</div>
        <h1>
          政策在更新，
          <br />
          关注不必重复。
        </h1>
        <p>
          聚焦水务环保，关注智慧水务与设备更新，
          <br />
          在原始资料中找到依据，持续跟踪相关变化。
        </p>
        <div className="login-topics">
          <span>水务</span>
          <span>环保</span>
          <span>人工智能＋</span>
        </div>
        <div className="login-foot">官方来源 · 原文可溯 · 按需订阅</div>
      </section>
      <section className="login-panel">
        <div className="login-form">
          <div className="eyebrow">{register ? "欢迎加入" : "欢迎回来"}</div>
          <h2>{register ? "创建政策订阅账号" : "登录你的工作台"}</h2>
          <p className="muted">
            {register
              ? "注册后即可查看最新政策，订阅你关注的方向。"
              : "登录后查看新政策和订阅消息。"}
          </p>
          <Form
            key={register ? "register" : "login"}
            layout="vertical"
            onFinish={(v) => mutation.mutate(v)}
            requiredMark={false}
          >
            <Form.Item
              name="username"
              label="用户名"
              rules={[
                { required: true, message: "请输入用户名" },
                { max: 150, message: "用户名不能超过150个字符" },
              ]}
              extra={
                register ? "可使用文字、数字及 @ . + - _ 符号。" : undefined
              }
            >
              <Input
                autoComplete="username"
                size="large"
                placeholder="你的用户名"
              />
            </Form.Item>
            <Form.Item
              name="password"
              label="密码"
              rules={[
                { required: true, message: "请输入密码" },
                ...(register
                  ? [
                      { min: 8, message: "密码至少8位" },
                      { max: 256, message: "密码不能超过256位" },
                    ]
                  : []),
              ]}
              extra={
                register
                  ? "至少8位，不能是纯数字、常见密码或与用户名过于相似。"
                  : undefined
              }
            >
              <Input.Password
                autoComplete={register ? "new-password" : "current-password"}
                size="large"
                placeholder="输入密码"
              />
            </Form.Item>
            {register && (
              <Form.Item
                name="password_confirm"
                label="确认密码"
                dependencies={["password"]}
                rules={[
                  { required: true, message: "请再次输入密码" },
                  ({ getFieldValue }) => ({
                    validator(_, value) {
                      return !value || getFieldValue("password") === value
                        ? Promise.resolve()
                        : Promise.reject(new Error("两次输入的密码不一致"));
                    },
                  }),
                ]}
              >
                <Input.Password
                  autoComplete="new-password"
                  size="large"
                  placeholder="再次输入密码"
                />
              </Form.Item>
            )}
            {mutation.error && (
              <div className="space-bottom">
                <ErrorBox error={mutation.error} />
              </div>
            )}
            <Button
              type="primary"
              htmlType="submit"
              block
              size="large"
              loading={mutation.isPending}
            >
              {register ? "注册并进入" : "登录工作台"}
            </Button>
          </Form>
          <Button
            type="link"
            block
            disabled={mutation.isPending}
            onClick={() => {
              mutation.reset();
              setRegister(!register);
            }}
          >
            {register ? "已有账号？返回登录" : "还没有账号？自主注册"}
          </Button>
          <p className="login-note">自主注册账号可查看政策和管理个人订阅。</p>
        </div>
      </section>
    </main>
  );
}

