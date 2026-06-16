# 测试下载（Test Download）功能研究报告

**日期**：2026-06-12  
**项目**：简历智采助手 (Recruitment-Assistant)  
**查询方式**：全量代码搜索 + 文档分析

---

## 1. 现状总结

### 1.1 菜单项存在，页面不存在

在 `app/components/layout.py` 中定义了"测试下载"菜单项：

```python
MENU_ITEMS = [
    ...
    ("测试下载", "⚡", "/测试下载"),
]

TOPBAR_LINKS = [
    ...
    ("⚡ 测试下载", "/测试下载"),
]
```

**现象**：菜单导航栏中可见"⚡ 测试下载"链接，但**无对应页面实现**。  
**推断**：这是一个**计划中但未实装的功能菜单项**。

### 1.2 页面文件清单

`app/pages/` 目录下现有页面（按编号顺序）：

| 文件名 | 功能 | 备注 |
|---|---|---|
| `05_平台登录.py` | 系统设置（AI模型 + 主题风格） | 最新版本使用 |
| `06_智联采集.py` | 智联招聘采集 | 核心功能 |
| `07_简历管理.py` | 简历库管理与查询 | 核心功能 |
| `08_BOSS采集.py` | BOSS直聘采集 | 核心功能 |
| `09_51前程无忧采集.py` | 51job采集 | 核心功能 |
| `10_面试管理.py` | 面试与评估管理 | 核心功能 |
| `测试下载.py` | **不存在** | ❌ 菜单项孤立 |

---

## 2. 稳定性测试支撑（V2.51计划）

虽然"测试下载"页面本身不存在，但项目中**确实有测试相关的大量改进计划**。

### 2.1 相关规格文档

| 文件 | 描述 | 状态 |
|---|---|---|
| `docs/superpowers/specs/2026-05-22-三平台稳定性测试支撑.md` | **完整的测试框架规格** | ✅ 已写 |
| `docs/superpowers/plans/2026-05-22-三平台稳定性测试支撑.md` | **实现计划（详细的Task清单）** | ✅ 已写 |
| `docs/04_development_log.md` | 历次迭代日志 | ✅ 已维护 |

### 2.2 测试支撑框架（V2.51规格要点）

#### 2.2.1 看门狗机制（Watchdog）

- **候选人级**：120秒无事件自动跳过 + 发送 `watchdog_candidate_timeout` 事件
- **全局级**：300秒无事件全体中止采集 + 发送 `watchdog_global_idle_timeout` 事件

```python
# 来自 recruitment_assistant/services/test_run_watchdog.py
CANDIDATE_TIMEOUT_SECONDS = 120
GLOBAL_TIMEOUT_SECONDS = 300

class WatchdogState:
    """维护候选人超时状态 + 全局空闲检测"""
    def check_candidates(now) -> list[TimedOutCandidate]
    def check_global(now) -> float | None
```

#### 2.2.2 学习模式误进检测（Misroute Detection）

捕获两类异常流程：

- **A2 类**：已落库（`resume_persist_confirmed`）后又弹学习模式 → 重复触发
- **A3 类**：已用"学习下载"（`learned_download_click_used`）后又弹学习 → 学习成果未生效

```python
class MisrouteDetector:
    def on_event(event_type, candidate_id, payload) -> list[misroute_events]
    # 返回需要 emit 的 A2/A3 事件
```

#### 2.2.3 跨轮历史回归检测

维护 `logs/test_runs/_persist_history.json`，记录每个候选人首次成功下载的轮次。

本轮若有 `manual_download_learning_required` 的候选人在历史中存在 → 报警（已下载却又进学习）。

#### 2.2.4 测试摘要自动生成

每轮采集完成时，`scripts/analyze_test_run.py` 自动生成 round 摘要。

摘要包含 7 个 section：
1. **看门狗触发汇总**
2. **按钮命中分布**
3. **误进学习流程（misroute）**
4. **历史回归**
5. **未识别事件**
6. **与上一轮对比**

---

## 3. 实现状态

### 3.1 已实装

根据 `recruitment_assistant/services/test_run_watchdog.py` 的存在，看门狗纯逻辑已完全实现。

### 3.2 待实装（V2.51计划）

根据规格和计划文档：

| Task | 文件 | 状态 | 优先级 |
|---|---|---|---|
| Task 1 | `recruitment_assistant/services/test_run_watchdog.py` | ✅ **已完成** | P0 |
| Task 2.1 | `recruitment_assistant/services/zhilian_ws_bridge.py` | ⏳ **待集成** | P0 |
| Task 2.2 | `recruitment_assistant/services/boss_ws_bridge.py` | ⏳ **待集成** | P0 |
| Task 2.3 | `recruitment_assistant/services/qiancheng_ws_bridge.py` | ⏳ **待集成** | P0 |
| Task 3 | `chrome_extension/content.js` + `background.js` | ⏳ **待集成** | P0 |
| Task 4 | `scripts/analyze_test_run.py` | ⏳ **待创建** | P1 |

---

## 4. 既有测试/调试机制

### 4.1 API 连通性测试（已实装）

在 `app/pages/05_平台登录.py` 中的"API Key测试"对话框可以验证 AI 接口的连通性。

### 4.2 日志系统

所有桥接（bridge）类都维护事件日志：

- `logs/zhilian_extension/YYYYMMDD/run_<runid>.jsonl`
- `logs/boss_extension/YYYYMMDD/run_<runid>.jsonl`
- `logs/qiancheng_extension/YYYYMMDD/run_<runid>.jsonl`

---

## 5. 代码搜索结果汇总

### 5.1 "测试下载" 出现的位置

```
./app/components/layout.py:22:    ("测试下载", "⚡", "/测试下载"),
./app/components/layout.py:32:    ("⚡ 测试下载", "/测试下载"),
```

仅在菜单定义中出现，**无实现代码**。

### 5.2 相关的"下载"功能

| 所在文件 | 功能描述 |
|---|---|
| `recruitment_assistant/services/boss_ws_bridge.py` | BOSS 作品集下载处理（PDF提取） |
| `recruitment_assistant/services/zhilian_ws_bridge.py` | 智联简历/附件下载 + 文件归档 |
| `recruitment_assistant/services/qiancheng_ws_bridge.py` | 51job 简历/附件下载 |
| `recruitment_assistant/parsers/pdf_resume_parser.py` | PDF 解析与文字提取 |
| `chrome_extension/content.js` | 浏览器端下载触发与识别 |
| `chrome_extension/background.js` | 下载管理与重命名 |

---

## 6. "测试下载"的推断用途

基于现有代码结构和规格文档，"测试下载"菜单项**很可能是以下场景之一**：

### 假设 A：测试摘要查看 + 历史对比工具
- 展示 `logs/test_runs/` 下的历轮摘要
- 提供图表化的对比界面
- **支持依据**：规格中详细设计了 round 摘要结构、历史回归对比

### 假设 B：单条候选人的模拟下载测试
- 用户输入候选人 ID，触发该候选人的完整下载流程
- 观察流程各环节的日志输出

### 假设 C：稳定性测试的自动化驱动
- 集成了看门狗 + misroute 检测
- 循环运行多轮，自动生成摘要
- **支持依据**：计划中的 `scripts/analyze_test_run.py` 自动调用

---

## 7. 推荐行动

### 7.1 立即可做

✅ **查看 V2.51 规格文档**  
- 位置：`docs/superpowers/specs/2026-05-22-三平台稳定性测试支撑.md`
- 内容：完整的测试框架设计

✅ **检查 Task 清单**  
- 位置：`docs/superpowers/plans/2026-05-22-三平台稳定性测试支撑.md`
- 内容：5 大 Task + 40+ 个小步骤（包括代码位置和行号）

### 7.2 需要产品确认

❓ **"测试下载"菜单项的真实意图**
- 是否要实装一个新页面？
- 还是仅作为占位符预留？

### 7.3 如果要实装测试页面

建议的优先级顺序：

1. **第一阶段**：实装 V2.51 看门狗 + misroute + analyze
2. **第二阶段**：创建 `app/pages/11_测试下载.py`
3. **第三阶段**：（可选）集成自动化测试驱动

---

## 8. 特别发现

### 8.1 Watchdog 模块已存在但未集成

关键发现：`recruitment_assistant/services/test_run_watchdog.py` 文件**已完整创建**，包含所有必要的类和逻辑，但**三个桥接（boss/zhilian/qiancheng）尚未导入和调用它**。

### 8.2 V2.51 版本规划已明确

从规格文档来看，**V2.51 是明确的版本目标**，包括：
- 所有版本号和 EXPECTED 版本已设定
- Task 顺序和行号位置已标注
- 测试用例和验收标准已写好

---

## 9. 总结

| 问题 | 答案 |
|---|---|
| **测试下载功能是什么？** | 菜单项存在但页面未实装。likely 是 V2.51 稳定性测试框架的配套展示工具。 |
| **有现成的测试机制吗？** | ✅ 有。看门狗纯逻辑（`test_run_watchdog.py`）已实装；摘要脚本规格已定；三桥接集成待启动。 |
| **规格文档完整吗？** | ✅ 完整。规格（257行） + 计划（1360+行，含 40+ 微任务）。 |
| **可以立即启动开发吗？** | ⚠️ 需确认产品意图。建议先复审 V2.51 规格。 |

---

**报告完成于**：2026-06-12  
**来源**：全量代码检索 + 文档分析
