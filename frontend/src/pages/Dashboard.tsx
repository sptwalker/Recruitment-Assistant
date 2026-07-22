import { Card, Col, Row, Statistic } from "antd";
import { useCandidates, useJobs, useMe } from "../api/hooks";

/** 轻量看板：候选人总数（分页 total）+ 职位数 + 当前身份。无独立 stats 端点，复用列表查询。 */
export function Dashboard() {
  const { data: me } = useMe();
  const { data: cand } = useCandidates({ page: 1, page_size: 1 });
  const { data: jobs } = useJobs();

  return (
    <Row gutter={16}>
      <Col span={8}>
        <Card>
          <Statistic title="候选人（可见范围）" value={cand?.total ?? 0} />
        </Card>
      </Col>
      <Col span={8}>
        <Card>
          <Statistic title="在招职位" value={jobs?.length ?? 0} />
        </Card>
      </Col>
      <Col span={8}>
        <Card>
          <Statistic title="当前账号" value={me?.name ?? "-"} />
        </Card>
      </Col>
    </Row>
  );
}
