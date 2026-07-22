import { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { Spin } from "antd";
import { useMe } from "../api/hooks";

/** 未登录（/auth/me 401）→ 跳 /login；加载中转圈；否则渲染子节点。 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { isLoading, isError } = useMe();
  if (isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", marginTop: 120 }}>
        <Spin size="large" />
      </div>
    );
  }
  if (isError) return <Navigate to="/login" replace />;
  return <>{children}</>;
}
