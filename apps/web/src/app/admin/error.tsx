"use client";
export default function ErrorPage({ reset }: { reset: () => void }) {
  return <main className="boot"><h2>暂时无法打开管理页面</h2><p>请检查服务连接后重试。</p><button onClick={reset}>重新连接</button><a href="/search">返回政策搜索</a></main>;
}
