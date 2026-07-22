import { Layout, Menu, Avatar, Dropdown, Typography } from "antd";
import {
  DashboardOutlined,
  TeamOutlined,
  ProfileOutlined,
  FileTextOutlined,
  LogoutOutlined,
} from "@ant-design/icons";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useMe } from "../api/hooks";

const { Header, Sider, Content } = Layout;

const ITEMS = [
  { key: "/", icon: <DashboardOutlined />, label: <Link to="/">看板</Link> },
  { key: "/candidates", icon: <TeamOutlined />, label: <Link to="/candidates">简历管理</Link> },
  { key: "/jobs", icon: <ProfileOutlined />, label: <Link to="/jobs">职位管理</Link> },
  { key: "/logs", icon: <FileTextOutlined />, label: <Link to="/logs">系统日志</Link> },
];

const ROLE_LABEL: Record<string, string> = {
  admin: "管理员",
  manager: "经理",
  recruiter: "招聘专员",
};

export function AppLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: me } = useMe();

  async function logout() {
    await api.post("/auth/logout");
    qc.clear(); // 清掉 me/列表缓存，否则 Login/守卫读到旧 me 会闪回主页
    navigate("/login", { replace: true });
  }

  // 顶层 key：/candidates/xx 也高亮 /candidates
  const selected =
    ITEMS.map((i) => i.key)
      .filter((k) => k !== "/" && location.pathname.startsWith(k))
      .sort((a, b) => b.length - a.length)[0] ?? "/";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider theme="dark" breakpoint="lg" collapsedWidth="0">
        <div style={{ color: "#fff", padding: 16, fontWeight: 600, fontSize: 16 }}>
          简历智采助手
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={[selected]} items={ITEMS} />
      </Sider>
      <Layout>
        <Header
          style={{
            background: "#fff",
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
            paddingRight: 24,
          }}
        >
          <Dropdown
            menu={{
              items: [
                { key: "logout", icon: <LogoutOutlined />, label: "退出登录", onClick: logout },
              ],
            }}
          >
            <span style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 8 }}>
              <Avatar src={me?.avatar_url ?? undefined}>{me?.name?.[0] ?? "用"}</Avatar>
              <Typography.Text>
                {me?.name ?? "用户"}
                {me?.role ? `（${ROLE_LABEL[me.role] ?? me.role}）` : ""}
              </Typography.Text>
            </span>
          </Dropdown>
        </Header>
        <Content style={{ margin: 16 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
