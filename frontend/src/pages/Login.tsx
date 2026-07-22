import { Button, Card, Spin, Typography } from "antd";
import { Navigate } from "react-router-dom";
import { useMe } from "../api/hooks";

/** 已登录直接进主页；否则一个“飞书登录”按钮，跳后端 OAuth 起点（顶层导航，非 XHR）。 */
export function Login() {
  const { data: me, isLoading } = useMe();
  if (isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", marginTop: 140 }}>
        <Spin size="large" />
      </div>
    );
  }
  if (me) return <Navigate to="/" replace />;

  return (
    <div style={{ display: "flex", justifyContent: "center", marginTop: 140 }}>
      <Card style={{ width: 360, textAlign: "center" }}>
        <Typography.Title level={3}>简历智采助手</Typography.Title>
        <Typography.Paragraph type="secondary">请使用企业飞书账号登录</Typography.Paragraph>
        <Button
          type="primary"
          size="large"
          block
          onClick={() => window.location.assign("/auth/feishu/login")}
        >
          飞书登录
        </Button>
      </Card>
    </div>
  );
}
