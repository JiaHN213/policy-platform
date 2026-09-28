"use client";
import { useState } from "react";
import { App, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: false, staleTime: 15_000 } },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          token: {
            colorPrimary: "#14746c",
            borderRadius: 8,
            colorText: "#22343b",
            fontFamily:
              'system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif',
          },
        }}
      >
        <App>{children}</App>
      </ConfigProvider>
    </QueryClientProvider>
  );
}
