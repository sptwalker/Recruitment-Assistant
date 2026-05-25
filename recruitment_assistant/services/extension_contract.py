"""Chrome 扩展契约版本号 — 三 bridge 共享真相源。

`chrome_extension/content.js` 和 `chrome_extension/manifest.json` 是单一物理文件，给 BOSS / 51 / 智联
三平台共用。历史上每个 bridge 各声明一份 EXPECTED_EXTENSION_VERSION + EXPECTED_CONTENT_SCRIPT_VERSION，
content.js 每动一行就要同步改三处，漏改任何一处都会让对应平台报 "版本不匹配" warning（参考
2026-05-23 第一轮 BOSS 测试日志：扩展 v2.14.0 已发布、boss bridge 期望 1.95.0 → 误报）。

本模块把"期望的扩展契约版本"抽成单一常量；三 bridge 都 import 自这里。
任何对 content.js / manifest.json 的改动只需要：

  1. bump chrome_extension/content.js 的 CONTENT_SCRIPT_VERSION
  2. bump chrome_extension/manifest.json 的 version
  3. bump 本文件的 EXPECTED_EXTENSION_VERSION (= CONTENT_SCRIPT_VERSION)

bridge 各自的 BRIDGE_VERSION 仍由各 bridge 模块独立维护，表达 bridge 自身的代码版本。
"""

EXPECTED_EXTENSION_VERSION = "2.38.0"
EXPECTED_CONTENT_SCRIPT_VERSION = "2.38.0"
