from __future__ import annotations

import re


COMPONENT_VARIABLE_MAP: dict[str, list[tuple[str, str]]] = {
    "primary-btn": [("--color-primary", "主色"), ("--color-primary-hover", "悬停色")],
    "outline-btn": [("--color-primary", "边框/文字"), ("--color-surface", "背景"), ("--color-border", "边框")],
    "platform-card": [
        ("--color-surface", "卡片背景"),
        ("--color-border", "边框"),
        ("--color-bg-soft", "状态格背景"),
        ("--color-primary-soft", "图标背景"),
        ("--color-primary", "图标色"),
    ],
    "status-banner": [
        ("--color-surface", "背景"),
        ("--color-border", "边框"),
        ("--color-text", "数值色"),
        ("--color-text-secondary", "标签色"),
    ],
    "progress-bar": [("--color-primary", "进度主色"), ("--color-accent", "渐变色"), ("--color-primary-soft", "轨道背景")],
    "tags": [("--color-primary-soft", "标签背景"), ("--color-primary", "标签文字")],
    "input": [("--color-surface", "背景"), ("--color-border", "边框"), ("--color-text", "文字")],
    "banner": [("--color-primary", "渐变起始"), ("--color-secondary", "渐变结束")],
}

COMPONENT_LABELS: dict[str, str] = {
    "primary-btn": "主按钮",
    "outline-btn": "描边按钮",
    "platform-card": "平台卡片",
    "status-banner": "状态信息栏",
    "progress-bar": "进度条",
    "tags": "标签",
    "input": "输入框",
    "banner": "Banner 横幅",
}


def parse_theme_variables(css_text: str) -> dict[str, str]:
    variables: dict[str, str] = {}
    for match in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", css_text):
        name = match.group(1)
        value = match.group(2).strip()
        if not value.startswith("var("):
            variables[name] = value
    return variables


def build_preview_html(theme_css: str, theme_name: str, theme_desc: str, overrides: dict[str, str] | None = None) -> str:
    override_block = ""
    if overrides:
        props = "\n".join(f"  {k}: {v};" for k, v in overrides.items())
        override_block = f"\n:root {{\n{props}\n}}\n"

    return f"""
<style>
{theme_css}
{override_block}
body {{ margin:0; font-family:var(--font-family-base, -apple-system, BlinkMacSystemFont, sans-serif); background:transparent; color:var(--color-text); }}
.tp {{ background:var(--color-bg); border:1px solid var(--color-border); border-radius:var(--radius-xl, 22px); padding:22px; box-shadow:var(--shadow-md); }}
.tp-banner {{ padding:22px; border-radius:var(--radius-lg, 16px); background:linear-gradient(135deg, var(--color-primary), var(--color-secondary)); color:#fff; margin-bottom:16px; }}
.tp-banner h2 {{ margin:0 0 6px; font-size:22px; }}
.tp-banner p {{ margin:0; opacity:.9; font-size:13px; }}
.tp-grid {{ display:grid; grid-template-columns:1.2fr .8fr; gap:14px; }}
.tp-card {{ background:var(--color-surface); border:1px solid var(--color-border); border-radius:var(--radius-lg, 16px); padding:16px; box-shadow:var(--shadow-sm); }}
.tp-title {{ margin:0 0 6px; font-size:18px; color:var(--color-text); font-weight:800; }}
.tp-text {{ margin:0 0 14px; line-height:1.6; color:var(--color-text-secondary); font-size:13px; }}
.tp-actions {{ display:flex; gap:8px; flex-wrap:wrap; margin:10px 0 14px; }}
.tp-btn {{ border:0; border-radius:var(--radius-md, 12px); padding:9px 14px; background:var(--color-primary); color:#fff; font-weight:700; font-size:13px; cursor:pointer; }}
.tp-btn.secondary {{ background:var(--color-primary-soft); color:var(--color-primary); border:1px solid var(--color-border); }}
.tp-btn.outline {{ background:var(--color-surface); color:var(--color-primary); border:1px solid var(--color-primary); }}
.tp-btn.disabled {{ background:var(--color-surface-muted, var(--color-bg-soft)); color:var(--color-text-muted); border:1px solid var(--color-border); opacity:.7; cursor:not-allowed; }}
.tp-input, .tp-select {{ width:100%; box-sizing:border-box; border:1px solid var(--color-border); border-radius:var(--radius-md, 12px); padding:10px 12px; margin-bottom:10px; background:var(--color-surface); color:var(--color-text); outline:none; font-size:13px; }}
.tp-stepper-row {{ display:flex; align-items:center; gap:10px; margin-bottom:10px; }}
.tp-stepper-label {{ color:var(--color-text-secondary); font-size:12px; font-weight:600; min-width:50px; }}
.tp-stepper {{ display:inline-flex; align-items:stretch; border:1px solid var(--color-border); border-radius:var(--radius-md, 12px); overflow:hidden; background:var(--color-surface); }}
.tp-stepper button {{ border:0; background:var(--color-primary-soft); color:var(--color-primary); width:32px; font-size:16px; font-weight:800; cursor:pointer; }}
.tp-stepper input {{ width:52px; text-align:center; border:0; border-left:1px solid var(--color-border); border-right:1px solid var(--color-border); background:var(--color-surface); color:var(--color-text); font-weight:700; font-size:13px; padding:7px 0; }}
.tp-progress {{ height:10px; background:var(--color-primary-soft); border-radius:999px; overflow:hidden; margin-top:8px; }}
.tp-progress span {{ display:block; width:68%; height:100%; background:linear-gradient(90deg, var(--color-primary), var(--color-accent)); }}
.tp-tags {{ display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }}
.tp-tag {{ padding:6px 10px; border-radius:999px; background:var(--color-primary-soft); color:var(--color-primary); font-size:11px; font-weight:700; }}
.tp-section {{ margin-top:16px; padding-top:16px; border-top:1px solid var(--color-border); }}
.tp-section-title {{ font-size:13px; font-weight:700; color:var(--color-text-secondary); margin-bottom:10px; }}
.tp-platform-card {{ background:var(--color-surface); border:1px solid var(--color-border); border-radius:18px; padding:14px; box-shadow:var(--shadow-sm); }}
.tp-platform-head {{ display:flex; justify-content:space-between; align-items:flex-start; padding-bottom:10px; border-bottom:1px solid var(--color-border); }}
.tp-platform-kicker {{ font-size:11px; color:var(--color-text-secondary); font-weight:700; }}
.tp-platform-name {{ margin:3px 0 0; font-size:17px; font-weight:900; color:var(--color-text); }}
.tp-platform-icon {{ width:36px; height:36px; background:var(--color-primary-soft); border-radius:10px; display:grid; place-items:center; color:var(--color-primary); font-weight:900; font-size:14px; }}
.tp-status-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; margin:10px 0; }}
.tp-status-grid div {{ padding:8px; background:var(--color-bg-soft); border:1px solid var(--color-border); border-radius:10px; }}
.tp-status-grid span {{ display:block; font-size:11px; color:var(--color-text-secondary); }}
.tp-status-grid b {{ display:block; margin-top:3px; font-size:13px; color:var(--color-text); }}
.tp-card-actions {{ display:flex; gap:6px; margin-top:10px; }}
.tp-card-actions a {{ flex:1; text-align:center; padding:7px 8px; border-radius:var(--radius-md, 12px); font-size:11px; font-weight:700; text-decoration:none; cursor:pointer; }}
.tp-status-banner {{ min-height:46px; background:var(--color-surface); border:1px solid var(--color-border); border-radius:12px; padding:8px 12px; display:flex; align-items:center; justify-content:space-between; gap:12px; }}
.tp-status-banner .sb-item {{ text-align:center; }}
.tp-status-banner .sb-label {{ display:block; font-size:10px; color:var(--color-text-secondary); }}
.tp-status-banner .sb-value {{ display:block; font-size:14px; font-weight:800; color:var(--color-text); margin-top:2px; }}
.tp-pct-grid {{ display:grid; grid-template-columns:repeat(4, 1fr); gap:12px; }}
.tp-pct-cell {{ background:var(--color-surface); border:1px solid var(--color-border); border-radius:14px; padding:14px 12px 12px; text-align:center; display:flex; flex-direction:column; align-items:center; justify-content:flex-start; box-shadow:var(--shadow-sm); }}
.tp-pct-svg {{ width:100%; max-width:170px; height:96px; }}
.tp-pct-num {{ font-size:20px; font-weight:900; fill:var(--color-text); }}
.tp-pct-num-light {{ font-size:18px; font-weight:900; fill:#fff; paint-order:stroke; stroke:rgba(0,0,0,.28); stroke-width:2px; }}
.tp-pct-label {{ font-size:11px; color:var(--color-text-secondary); margin-top:8px; font-weight:700; line-height:1.4; }}
.tp-gauge-bg {{ fill:none; stroke:var(--color-primary-soft); stroke-width:14; stroke-linecap:round; }}
.tp-gauge-fg {{ fill:none; stroke:var(--color-primary); stroke-width:14; stroke-linecap:round; }}
.tp-ring-bg {{ fill:none; stroke:var(--color-primary-soft); stroke-width:12; }}
.tp-ring-fg {{ fill:none; stroke:var(--color-primary); stroke-width:12; stroke-linecap:round; transform:rotate(-90deg); transform-origin:50% 50%; }}
.tp-pct-cell-bullet {{ justify-content:center; padding:18px 14px 14px; }}
.tp-bullet-num {{ font-size:22px; font-weight:900; color:var(--color-text); margin-bottom:14px; }}
.tp-bullet-track {{ position:relative; height:12px; background:var(--color-primary-soft); border-radius:999px; margin-bottom:8px; width:100%; }}
.tp-bullet-fill {{ height:100%; background:linear-gradient(90deg, var(--color-primary), var(--color-accent)); border-radius:999px; }}
.tp-bullet-target {{ position:absolute; top:-4px; bottom:-4px; width:3px; background:var(--color-text); border-radius:2px; }}
.tp-bullet-scale {{ display:flex; justify-content:space-between; width:100%; font-size:9px; color:var(--color-text-secondary); font-weight:700; }}
.tp-wb-bg {{ fill:var(--color-primary-soft); }}
.tp-wb-wave {{ fill:var(--color-primary); }}
.tp-wb-border {{ fill:none; stroke:var(--color-primary); stroke-width:1.5; opacity:.45; }}
@media (max-width:720px) {{ .tp-grid {{ grid-template-columns:1fr; }} .tp-pct-grid {{ grid-template-columns:repeat(2, 1fr); }} }}
</style>
<div class="tp">
  <div class="tp-banner">
    <h2>{theme_name}</h2>
    <p>{theme_desc}</p>
  </div>
  <div class="tp-grid">
    <div class="tp-card">
      <h3 class="tp-title">招聘数据工作台</h3>
      <p class="tp-text">统一管理候选人采集、简历解析、面试跟进，快速感受主题在真实组件中的效果。</p>
      <div class="tp-actions">
        <button class="tp-btn">主按钮</button>
        <button class="tp-btn secondary">次按钮</button>
        <button class="tp-btn outline">打开页面</button>
        <button class="tp-btn disabled">禁用</button>
      </div>
      <input class="tp-input" value="候选人搜索" />
      <select class="tp-select"><option>下拉选择：全部岗位</option></select>
      <div class="tp-stepper-row">
        <span class="tp-stepper-label">每页</span>
        <div class="tp-stepper"><button type="button">−</button><input value="20" readonly /><button type="button">+</button></div>
      </div>
    </div>
    <div class="tp-card">
      <h3 class="tp-title">任务进度</h3>
      <p class="tp-text">当前采集任务完成度 68%</p>
      <div class="tp-progress"><span></span></div>
      <div style="height:10px"></div>
      <div class="tp-tags"><span class="tp-tag">成功</span><span class="tp-tag">待处理</span><span class="tp-tag">高优先级</span></div>
    </div>
  </div>
  <div class="tp-section">
    <div class="tp-section-title">百分比可视化控件</div>
    <div class="tp-pct-grid">
      <div class="tp-pct-cell">
        <svg class="tp-pct-svg" viewBox="0 0 160 100">
          <path class="tp-gauge-bg" d="M 18 88 A 62 62 0 0 1 142 88" />
          <path class="tp-gauge-fg" d="M 18 88 A 62 62 0 0 1 142 88"
                stroke-dasharray="194.78" stroke-dashoffset="58.43" />
          <text x="80" y="80" text-anchor="middle" class="tp-pct-num">70%</text>
        </svg>
        <div class="tp-pct-label">半圆仪表盘<br/>批次达成率</div>
      </div>
      <div class="tp-pct-cell">
        <svg class="tp-pct-svg" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
          <circle class="tp-ring-bg" cx="50" cy="50" r="38" />
          <circle class="tp-ring-fg" cx="50" cy="50" r="38"
                  stroke-dasharray="238.76" stroke-dashoffset="68.50" />
          <text x="50" y="56" text-anchor="middle" class="tp-pct-num">71%</text>
        </svg>
        <div class="tp-pct-label">环形进度<br/>简历解析进度</div>
      </div>
      <div class="tp-pct-cell tp-pct-cell-bullet">
        <div class="tp-bullet-num">82%</div>
        <div class="tp-bullet-track">
          <div class="tp-bullet-fill" style="width:82%"></div>
          <div class="tp-bullet-target" style="left:90%"></div>
        </div>
        <div class="tp-bullet-scale"><span>0</span><span>50%</span><span>目标 90%</span></div>
        <div class="tp-pct-label" style="margin-top:10px">子弹图<br/>实际 vs 目标</div>
      </div>
      <div class="tp-pct-cell">
        <svg class="tp-pct-svg" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
          <defs>
            <clipPath id="tp-wb-clip"><circle cx="50" cy="50" r="38" /></clipPath>
          </defs>
          <circle class="tp-wb-bg" cx="50" cy="50" r="38" />
          <g clip-path="url(#tp-wb-clip)">
            <path class="tp-wb-wave"
                  d="M -10 55 Q 15 47 40 55 T 90 55 T 140 55 L 140 100 L -10 100 Z" />
            <path class="tp-wb-wave" opacity="0.55"
                  d="M -10 60 Q 15 68 40 60 T 90 60 T 140 60 L 140 100 L -10 100 Z" />
          </g>
          <circle class="tp-wb-border" cx="50" cy="50" r="38" />
          <text x="50" y="56" text-anchor="middle" class="tp-pct-num-light">63%</text>
        </svg>
        <div class="tp-pct-label">水波球<br/>磁盘 / 配额占用</div>
      </div>
    </div>
  </div>
  <div class="tp-section">
    <div class="tp-section-title">首页平台卡片</div>
    <div class="tp-platform-card">
      <div class="tp-platform-head">
        <div><div class="tp-platform-kicker">Chrome 扩展采集</div><div class="tp-platform-name">BOSS直聘</div></div>
        <div class="tp-platform-icon">B</div>
      </div>
      <div class="tp-status-grid">
        <div><span>页面状态</span><b style="color:var(--color-success)">已就绪</b></div>
        <div><span>运行状态</span><b>待启动</b></div>
      </div>
      <div class="tp-card-actions">
        <a class="tp-btn outline" style="font-size:11px;padding:6px 8px;">打开页面</a>
        <a class="tp-btn" style="font-size:11px;padding:6px 8px;">进入采集</a>
        <a class="tp-btn outline" style="font-size:11px;padding:6px 8px;">简历目录</a>
      </div>
    </div>
  </div>
  <div class="tp-section">
    <div class="tp-section-title">采集状态信息栏</div>
    <div class="tp-status-banner">
      <div class="sb-item"><span class="sb-label">WebSocket</span><span class="sb-value" style="color:var(--color-success)">已连接</span></div>
      <div class="sb-item"><span class="sb-label">已获取</span><span class="sb-value">1,284</span></div>
      <div class="sb-item"><span class="sb-label">成功率</span><span class="sb-value">96.2%</span></div>
      <div class="sb-item"><span class="sb-label">耗时</span><span class="sb-value">12:34</span></div>
    </div>
  </div>
</div>
"""
