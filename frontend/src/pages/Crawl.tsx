import { useState } from "react";
import { Alert, Badge, Button, Card, Input, InputNumber, Space, Typography, message } from "antd";
import { CopyOutlined } from "@ant-design/icons";
import { useCrawlCommand, useCrawlStatus, useCrawlToken } from "../api/hooks";

const { Text, Paragraph } = Typography;

/** 采集：显示扩展在线状态（轮询）+ 开始/停止命令 + 复制 token 供扩展粘贴。
 * ponytail: 无实时进度条（进度事件未接入库/推送）；只做在线状态 + 触发的最小面。
 * ponytail: start 只传 max_resumes；不注入 boss_pre_dedup_ready 去重基线（那需服务端
 *   拦命令→按租户装载已采 key 再转发，属后续）。缺基线时扩展会本地重复下载旧候选人，
 *   但入库走 upsert_candidate_record 按 candidate_key 去重，数据库不会重复。
 */
export function Crawl() {
  const { data: status, isLoading } = useCrawlStatus();
  const command = useCrawlCommand();
  const tokenMut = useCrawlToken();
  const [token, setToken] = useState("");
  const [maxResumes, setMaxResumes] = useState(20);

  const connected = status?.connected ?? false;

  function send(type: string, config?: Record<string, unknown>) {
    command.mutate(
      config ? { type, config } : { type },
      {
        onSuccess: (r) =>
          r.delivered > 0
            ? message.success(`已下发（${r.delivered} 个扩展）`)
            : message.warning("扩展未在线，命令未送达"),
        onError: () => message.error("下发失败"),
      },
    );
  }

  function fetchToken() {
    tokenMut.mutate(undefined, {
      onSuccess: (r) => setToken(r.token),
      onError: () => message.error("获取 token 失败"),
    });
  }

  async function copyToken() {
    try {
      await navigator.clipboard.writeText(token);
      message.success("已复制");
    } catch {
      message.error("复制失败，请手动选择");
    }
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Card
        title="扩展连接状态"
        loading={isLoading}
        extra={
          <Badge
            status={connected ? "success" : "default"}
            text={connected ? "已连接" : "未连接"}
          />
        }
      >
        {connected ? (
          <Text>在线平台：{status?.platforms.join("、") || "—"}</Text>
        ) : (
          <Alert
            type="info"
            message="扩展未连接。请在扩展 popup 中填入服务端地址与下方 token 后重连。"
          />
        )}
      </Card>

      <Card title="采集控制">
        <Space>
          <InputNumber
            min={1}
            max={9999}
            value={maxResumes}
            onChange={(v) => setMaxResumes(v ?? 20)}
            addonAfter="份"
            style={{ width: 140 }}
          />
          <Button
            type="primary"
            disabled={!connected}
            loading={command.isPending}
            onClick={() => send("start_collect", { max_resumes: maxResumes })}
          >
            开始采集
          </Button>
          <Button
            danger
            disabled={!connected}
            loading={command.isPending}
            onClick={() => send("stop_collect")}
          >
            停止采集
          </Button>
        </Space>
      </Card>

      <Card title="扩展 Token">
        <Paragraph type="secondary">
          扩展跨站无法读取登录 cookie，需手动把 token 粘贴到扩展 popup。点下方按钮生成，复制后填入扩展。
        </Paragraph>
        <Space.Compact style={{ width: "100%" }}>
          <Input.Password
            value={token}
            readOnly
            placeholder="点右侧按钮生成"
            style={{ fontFamily: "monospace" }}
          />
          <Button onClick={fetchToken} loading={tokenMut.isPending}>
            生成
          </Button>
          <Button icon={<CopyOutlined />} disabled={!token} onClick={copyToken}>
            复制
          </Button>
        </Space.Compact>
      </Card>
    </Space>
  );
}
