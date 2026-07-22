import { useState } from "react";
import {
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Table,
  Tag,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  useCreateJob,
  useDeleteJob,
  useJobMatches,
  useJobs,
  type Job,
  type JobMatch,
  type JobPositionCreateBody,
} from "../api/hooks";

/** 职位管理：列表 + 新建（Modal 表单）+ 删除 + 匹配结果抽屉（useJobMatches）。 */
export function Jobs() {
  const { data: jobs, isFetching } = useJobs();
  const create = useCreateJob();
  const del = useDeleteJob();
  const [form] = Form.useForm<JobPositionCreateBody>();
  const [creating, setCreating] = useState(false);
  const [matchId, setMatchId] = useState<number | null>(null);
  const matches = useJobMatches(matchId);

  const columns: ColumnsType<Job> = [
    { title: "职位", dataIndex: "title" },
    { title: "部门", dataIndex: "department" },
    { title: "城市", dataIndex: "work_city" },
    { title: "薪资", dataIndex: "salary_range" },
    { title: "状态", dataIndex: "status", width: 90, render: (s) => <Tag>{s}</Tag> },
    {
      title: "操作",
      width: 160,
      render: (_, r) => (
        <Space>
          <Button size="small" type="link" onClick={() => setMatchId(r.id)}>匹配</Button>
          <Popconfirm
            title="确认删除该职位？"
            onConfirm={() =>
              del.mutate(r.id, {
                onSuccess: () => message.success("已删除"),
                onError: () => message.error("删除失败"),
              })
            }
          >
            <Button danger size="small" type="link">删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const matchColumns: ColumnsType<JobMatch> = [
    { title: "候选人", dataIndex: "name" },
    { title: "得分", dataIndex: "score", width: 80, sorter: (a, b) => a.score - b.score, defaultSortOrder: "descend" },
    { title: "理由", dataIndex: "reason" },
  ];

  async function submit() {
    const body = await form.validateFields();
    create.mutate(body, {
      onSuccess: () => { message.success("已创建"); setCreating(false); form.resetFields(); },
      onError: () => message.error("创建失败"),
    });
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Button type="primary" onClick={() => setCreating(true)}>新建职位</Button>
      <Table rowKey="id" loading={isFetching} columns={columns} dataSource={jobs ?? []} />

      <Modal title="新建职位" open={creating} onOk={submit} confirmLoading={create.isPending} onCancel={() => setCreating(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="title" label="职位名称" rules={[{ required: true, message: "请填写职位名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="department" label="部门"><Input /></Form.Item>
          <Form.Item name="work_city" label="城市"><Input /></Form.Item>
          <Form.Item name="salary_range" label="薪资范围"><Input /></Form.Item>
          <Form.Item name="job_requirements" label="职位要求"><Input.TextArea rows={3} /></Form.Item>
        </Form>
      </Modal>

      <Drawer title="匹配结果" width={640} open={matchId != null} onClose={() => setMatchId(null)}>
        <Table
          rowKey="candidate_id"
          loading={matches.isFetching}
          columns={matchColumns}
          dataSource={matches.data ?? []}
          pagination={false}
        />
      </Drawer>
    </Space>
  );
}
