import { useState } from "react";
import { Button, Input, Popconfirm, Space, Table, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCandidates, useDeleteCandidate, type Candidate } from "../api/hooks";

/** 简历管理：服务端分页 + 姓名/城市过滤 + 删除确认。可见范围由后端租户/归属过滤保证。 */
export function Candidates() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [name, setName] = useState("");
  const [city, setCity] = useState("");
  const { data, isFetching } = useCandidates({ page, page_size: pageSize, name: name || undefined, city: city || undefined });
  const del = useDeleteCandidate();

  const columns: ColumnsType<Candidate> = [
    { title: "姓名", dataIndex: "name" },
    { title: "性别", dataIndex: "gender", width: 70 },
    { title: "年龄", dataIndex: "age", width: 70 },
    { title: "城市", dataIndex: "current_city" },
    { title: "学历", dataIndex: "education_level" },
    { title: "电话", dataIndex: "phone" },
    { title: "邮箱", dataIndex: "email" },
    {
      title: "操作",
      width: 90,
      render: (_, r) => (
        <Popconfirm
          title="确认删除该候选人？"
          onConfirm={() =>
            del.mutate(r.candidate_id, {
              onSuccess: () => message.success("已删除"),
              onError: () => message.error("删除失败"),
            })
          }
        >
          <Button danger size="small" type="link">删除</Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Space>
        <Input.Search placeholder="姓名" allowClear onSearch={(v) => { setName(v); setPage(1); }} style={{ width: 180 }} />
        <Input.Search placeholder="城市" allowClear onSearch={(v) => { setCity(v); setPage(1); }} style={{ width: 180 }} />
      </Space>
      <Table
        rowKey="candidate_id"
        loading={isFetching}
        columns={columns}
        dataSource={data?.items ?? []}
        pagination={{
          current: page,
          pageSize,
          total: data?.total ?? 0,
          showSizeChanger: true,
          onChange: (p, ps) => { setPage(p); setPageSize(ps); },
        }}
      />
    </Space>
  );
}
