# 简历智采助手（Recruitment Assistant）

一个面向单个招聘专员的**桌面级招聘提效工具**：在主流招聘网站自动采集附件简历 → AI 结构化解析入库 → AI 岗位匹配 → 面试邀约与评价 → 生成结构化面试大纲。

> 定位：单人本地使用的「采集 + 初筛 + 面试跟进」加速器。多人协作/ATS 化的规划见 [`docs/架构升级规划.md`](docs/架构升级规划.md)。

---

## 核心功能

| 模块 | 能力 |
|---|---|
| **简历采集** | 通过 Chrome 扩展在页面内自动采集附件简历，支持 **BOSS直聘 / 51前程无忧 / 智联招聘**；采集期自动去重 |
| **AI 解析入库** | 调 LLM 把简历结构化为 9 张表的完整字段（教育/工作/项目/技能/意向/荣誉等）；解析前按文件/姓名去重省流量 |
| **岗位匹配** | 岗位录入（支持 Excel 批量导入）+ AI 四维评分（技能/经验/学历/地域）+ 综合分 + 一句话点评 |
| **面试管理** | 邀约 → 多轮面试 → 星级评价与结论；AI 生成 STAR 结构化面试大纲（可打印） |
| **数据备份** | 系统设置内一键备份简历库、导出候选人 Excel |

### AI 接口（多接口 + 自动降级）
- 「系统设置 → AI模型」里可配置**多个 OpenAI 兼容接口**（DeepSeek / 通义千问 / OpenAI / MiniMax / GLM 等），卡片式增删。
- 单选一个**主接口**用于所有 AI 调用；其余启用的接口作为**备用**，主接口失败时**自动降级**并提示。
- 可为**简历解析**单独指定一个更快的模型（解析是简单结构化任务，不必用推理大模型）。
- 端点若不支持 `response_format=json_object` 会自动去参重试，兼容更多代理。

---

## 技术架构

- **界面**：Streamlit 多页应用（`app/`）
- **采集**：Chrome MV3 扩展（`chrome_extension/`）↔ 应用内 WebSocket 桥（BOSS 8765 / 51job 8766 / 智联 8767）
- **数据**：**单一 SQLite 库** `data/resume_archive.db`（候选人 PII + 岗位 + 采集/去重记录 + 面试/匹配，全部一库）
  > 注：V3.24 起统一到 SQLite，**不再捆绑 PostgreSQL**。老版本升级时会一次性把旧 PG 数据迁入 SQLite（迁移前自动备份、幂等、可重试）。
- **AI**：`recruitment_assistant/services/resume_ai_service.py`（OpenAI 兼容，多端点降级）
- **打包**：Inno Setup 6（`build/installer.iss`）+ 内嵌 Python，产出 Windows 独立安装包

---

## 安装使用（终端用户）

1. 运行 `dist/简历智采助手_V3.xx_Setup.exe` 安装。
2. 从桌面/开始菜单启动；首次启动会自动建库并打开浏览器界面（`http://127.0.0.1:8501`）。
3. **首次必做：配置 AI Key** —— 进「系统设置 → AI模型」，添加接口填入 **名称 / 地址 / API Key / 模型**，设为主接口。
   - ⚠️ 请在界面里填写 Key，**切勿把真实 Key 提交进代码仓库**。
4. 安装浏览器扩展（`chrome_extension/`，开发者模式加载），登录招聘平台即可采集。

---

## 本地开发

```bash
# Python >= 3.10
pip install -e ".[dev]"          # 运行 + 测试依赖
# 可选：图像简历 OCR 回退
pip install -e ".[ocr]"

# 首次运行前复制 AI 配置模板并填写 Key（此文件已 gitignore，不会入库）
cp data/ai_models.json.example data/ai_models.json

# 启动（纯 SQLite，无需 PostgreSQL）
streamlit run app/main.py

# 测试
pytest -q
```

- CI：`.github/workflows/ci.yml`（push/PR 自动跑 `pytest`）。
- 迁移框架：alembic（`recruitment_assistant/storage/migrations/`，目标为统一 SQLite metadata）。

---

## 目录速览

```
app/                     Streamlit 页面（首页 + 平台采集 + 简历管理 + 面试管理 + 系统设置）
recruitment_assistant/
  services/              业务逻辑（AI 服务 / 归档 / 岗位 / 采集任务 / 备份 …）
  storage/               ORM 模型 + SQLite 引擎 + 迁移
  config/                设置 + 多接口 AI 配置管理
chrome_extension/        采集扩展（content.js + WS）
build/                   launcher.pyw + installer.iss
tests/                   去重 / 匹配复用 / 降级 / 备份 / 迁移 等核心链路测试
docs/                    架构升级规划等文档
```

---

## 说明与限制

- 单用户桌面工具：无登录/多租户/权限；PII 明文存于本机 SQLite，请注意本机数据安全并定期备份。
- 采集依赖第三方招聘平台页面结构，平台改版可能需要更新扩展选择器。
- 自动化采集第三方平台数据涉及对方 ToS 与个人信息合规，请在合法授权范围内使用。
