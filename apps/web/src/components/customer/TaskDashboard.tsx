"use client";

import { Card, Col, Empty, Row, Space, Statistic, Tag, Tooltip } from "antd";

export type DashboardData = {
  generated_at: string; total: number; statuses: Record<string, number>;
  failure_rate: number | null; average_completion_seconds: number | null; duration_samples: number;
  calls: { total: number; failed: number; average_ms: number | null };
  reuse: { count: number; completed_explanations: number; rate: number | null }; retry_waiting: number;
  trend: { day: string; total: number; completed: number; failed: number }[];
  failure_reasons: { reason: string; count: number; suggestion: string }[];
};

export function duration(seconds: number | null) {
  if (seconds === null) return "暂无数据";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)} 分钟`;
  return `${(seconds / 3600).toFixed(1)} 小时`;
}

const statuses = [["", "全部任务"], ["queued", "等待处理"], ["running", "正在处理"], ["waiting", "协作处理中"], ["completed", "已完成"], ["failed", "处理失败"], ["paused", "已停止"]];

export default function TaskDashboard({ data, status, onStatus }: { data: DashboardData; status: string; onStatus: (value: string) => void }) {
  const maximum = Math.max(1, ...data.trend.map(day => day.total));
  return <div className="task-dashboard">
    <div className="task-dashboard-statuses">
      {statuses.map(([key, label]) => <button type="button" key={key} className={`task-dashboard-status${status === key ? " is-selected" : ""}`} aria-pressed={status === key} onClick={() => onStatus(key)}>
        <span>{label}</span><strong>{key ? data.statuses[key] || 0 : data.total}</strong>
      </button>)}
    </div>
    <Row gutter={[12, 12]}>
      <Col xs={24} sm={12} xl={6}><Card size="small"><Statistic title="平均完成耗时" value={duration(data.average_completion_seconds)} /><div className="small muted">{data.duration_samples} 个有效样本，包含排队与重试</div></Card></Col>
      <Col xs={24} sm={12} xl={6}><Card size="small"><Statistic title="任务失败率" value={data.failure_rate ?? "—"} suffix={data.failure_rate === null ? undefined : "%"} /><div className="small muted">失败 ÷（完成＋失败），不含停止和处理中</div></Card></Col>
      <Col xs={24} sm={12} xl={6}><Card size="small"><Statistic title="已记录模型请求" value={data.calls.total} suffix="次" /><div className="small muted">失败／超时 {data.calls.failed} 次 · 均耗时 {duration(data.calls.average_ms === null ? null : data.calls.average_ms / 1000)}</div></Card></Col>
      <Col xs={24} sm={12} xl={6}><Card size="small"><Statistic title="复用已有解读" value={data.reuse.count} suffix="份" /><div className="small muted">占完成解读 {data.reuse.rate === null ? "—" : `${data.reuse.rate}%`} · 等待自动重试 {data.retry_waiting} 项</div></Card></Col>
    </Row>
    <Row gutter={[12, 12]} className="task-dashboard-charts">
      <Col xs={24} lg={14}><Card size="small" title="每日创建任务" extra={<Space size="small"><Tag color="cyan">已完成</Tag><Tag color="red">失败</Tag><Tag>其他状态</Tag></Space>}>
        {data.total === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="此范围内还没有任务" /> : <div className="task-dashboard-trend">
          {data.trend.map(day => <Tooltip key={day.day} title={`${day.day}：共 ${day.total} 项，已完成 ${day.completed}，失败 ${day.failed}，其他 ${day.total - day.completed - day.failed}`}>
            <div className="task-dashboard-day" tabIndex={0} aria-label={`${day.day} 共 ${day.total} 项，完成 ${day.completed}，失败 ${day.failed}`}>
              <div className="task-dashboard-bar"><i style={{ height: `${day.completed / maximum * 100}%`, background: "#168b84" }} /><i style={{ height: `${day.failed / maximum * 100}%`, background: "#df7272" }} /><i style={{ height: `${(day.total - day.completed - day.failed) / maximum * 100}%`, background: "#c8d9d8" }} /></div>
              <small>{day.day.slice(5)}</small>
            </div>
          </Tooltip>)}
        </div>}
        <p className="small muted">按创建日期分组，颜色表示任务当前状态。</p>
      </Card></Col>
      <Col xs={24} lg={10}><Card size="small" title="常见失败原因">
        {!data.failure_reasons.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="此范围内没有失败环节" /> : data.failure_reasons.map(item => <div className="task-dashboard-reason" key={item.reason}>
          <div className="spread"><strong>{item.reason}</strong><Tag>{item.count} 项</Tag></div><div className="small muted">{item.suggestion}</div>
        </div>)}
        <p className="small muted">根据失败提示归类；同一流程的失败子任务分别计数，不重复计算其主任务。点击“处理失败”筛选任务记录；私有过程仅在本人授权任务中查看。</p>
      </Card></Col>
    </Row>
  </div>;
}
