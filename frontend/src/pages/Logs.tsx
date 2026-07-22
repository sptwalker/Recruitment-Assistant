import { useState } from "react";
import { Alert, DatePicker, Space, Table, Tabs } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";
import { useAiUsage, useOperationLogs } from "../api/hooks";
import { useMe } from "../api/hooks";
import type { LogRow, LogsResponse } from "../api/types";

/** 从松散中文键行推导列（取首行的键）。 */
function columnsOf(rows: LogRow[]): ColumnsType<LogRow> {
  const keys = rows.length ? Object.keys(rows[0]) : [];
  return keys.map((k) => ({ title: k, dataIndex: k, ellipsis: true }));
}

function LogTable({ data, loading, error }: { data?: LogsResponse; loading: boolean; error: unknown }) {
  if (error) return <Alert type="error" message="加载失败（仅管理员可查看系统日志）" />;
  const rows = data?.items ?? [];
  return (
    <Table
      rowKey={(_, i) => String(i)}
      size="small"
      loading={loading}
      columns={columnsOf(rows)}
      dataSource={rows}
      scroll={{ x: true }}
    />
  );
}

/** 系统日志：仅管理员。操作日志 / AI 用量两个 Tab，列按行的中文键动态生成。 */
export function Logs() {
  const { data: me } = useMe();
  const [day, setDay] = useState<Dayjs>(dayjs());
  const dayStr = day.format("YYYY-MM-DD");
  const isAdmin = me?.role === "admin";
  const ops = useOperationLogs(dayStr, isAdmin);
  const ai = useAiUsage(dayStr, isAdmin);

  if (me && !isAdmin) {
    return <Alert type="warning" message="系统日志仅管理员可访问" />;
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <DatePicker value={day} onChange={(d) => d && setDay(d)} allowClear={false} />
      <Tabs
        items={[
          {
            key: "ops",
            label: "操作日志",
            children: <LogTable data={ops.data} loading={ops.isFetching} error={ops.error} />,
          },
          {
            key: "ai",
            label: "AI 用量",
            children: <LogTable data={ai.data} loading={ai.isFetching} error={ai.error} />,
          },
        ]}
      />
    </Space>
  );
}
