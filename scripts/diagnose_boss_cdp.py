"""
通过 Chrome DevTools Protocol (CDP) 远程执行诊断 JS，
不打开 DevTools 面板，绕过 BOSS 反调试保护。

用法：先用 --remote-debugging-port=9222 启动 Chrome，
打开 BOSS 聊天页面并让简历弹窗处于打开状态，然后运行本脚本。
"""
import json
import sys
import urllib.request
import asyncio
import websockets


DIAGNOSTIC_JS = r"""
(function() {
  const results = {};

  // 1. 高 z-index 浮层
  const overlays = [];
  document.querySelectorAll('*').forEach(el => {
    const style = window.getComputedStyle(el);
    const z = parseInt(style.zIndex);
    if (z > 100 && style.display !== 'none' && style.visibility !== 'hidden') {
      const rect = el.getBoundingClientRect();
      if (rect.width > 300 && rect.height > 300) {
        overlays.push({ el, z, rect });
      }
    }
  });
  overlays.sort((a, b) => b.z - a.z);
  results.overlays = overlays.slice(0, 5).map(item => ({
    z: item.z,
    tag: item.el.tagName,
    class: item.el.getAttribute('class')?.slice(0, 150) || '',
    size: `${Math.round(item.rect.width)}x${Math.round(item.rect.height)}`,
    pos: `(${Math.round(item.rect.left)},${Math.round(item.rect.top)})`
  }));

  const target = overlays[0]?.el || document.body;

  // 2. SVG 元素
  const svgs = target.querySelectorAll('svg');
  results.svgs = [];
  svgs.forEach((svg, i) => {
    const rect = svg.getBoundingClientRect();
    if (rect.width < 5 || rect.height < 5) return;
    const parent = svg.parentElement;
    const paths = svg.querySelectorAll('path');
    results.svgs.push({
      index: i,
      class: svg.getAttribute('class') || '',
      size: `${Math.round(rect.width)}x${Math.round(rect.height)}`,
      pos: `(${Math.round(rect.left)},${Math.round(rect.top)})`,
      parentTag: parent?.tagName || '',
      parentClass: parent?.getAttribute('class')?.slice(0, 100) || '',
      parentClickable: parent ? (parent.tagName === 'A' || parent.tagName === 'BUTTON' || parent.getAttribute('role') === 'button' || window.getComputedStyle(parent).cursor === 'pointer') : false,
      title: svg.querySelector('title')?.textContent || '',
      ariaLabel: svg.getAttribute('aria-label') || parent?.getAttribute('aria-label') || '',
      pathD: paths.length > 0 ? (paths[0].getAttribute('d') || '').slice(0, 120) : '',
      pathCount: paths.length
    });
  });

  // 3. 下载相关元素
  const dlCandidates = target.querySelectorAll(
    'a[download], a[href*="download"], a[href*=".pdf"], [class*="download"], [class*="Download"], [title*="下载"], [aria-label*="下载"]'
  );
  results.downloadCandidates = [];
  dlCandidates.forEach((el, i) => {
    const rect = el.getBoundingClientRect();
    if (rect.width < 5) return;
    results.downloadCandidates.push({
      index: i,
      tag: el.tagName,
      class: el.getAttribute('class')?.slice(0, 120) || '',
      text: (el.textContent || '').trim().slice(0, 50),
      href: (el.getAttribute('href') || '').slice(0, 150),
      title: el.getAttribute('title') || '',
      ariaLabel: el.getAttribute('aria-label') || '',
      size: `${Math.round(rect.width)}x${Math.round(rect.height)}`,
      pos: `(${Math.round(rect.left)},${Math.round(rect.top)})`
    });
  });

  // 4. 弹窗右上角可点击元素
  results.topRightClickables = [];
  if (overlays.length > 0) {
    const topArea = overlays[0].rect;
    target.querySelectorAll('a, button, [role="button"], [onclick], i, span').forEach((el, i) => {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      if (rect.left > topArea.right - 250 && rect.top < topArea.top + 100 && rect.width > 5 && rect.width < 80 && style.cursor === 'pointer') {
        results.topRightClickables.push({
          tag: el.tagName,
          class: el.getAttribute('class')?.slice(0, 120) || '',
          text: (el.textContent || '').trim().slice(0, 30),
          href: el.getAttribute('href')?.slice(0, 100) || '',
          size: `${Math.round(rect.width)}x${Math.round(rect.height)}`,
          pos: `(${Math.round(rect.left)},${Math.round(rect.top)})`,
          innerHTML: el.innerHTML.slice(0, 200)
        });
      }
    });
  }

  // 5. boss-svg / icon 类名
  results.bossIcons = [];
  target.querySelectorAll('[class*="boss-svg"], [class*="icon"], [class*="Icon"]').forEach((el, i) => {
    const rect = el.getBoundingClientRect();
    if (rect.width < 10 || rect.width > 60) return;
    const parent = el.parentElement;
    results.bossIcons.push({
      tag: el.tagName,
      class: el.getAttribute('class')?.slice(0, 120) || '',
      size: `${Math.round(rect.width)}x${Math.round(rect.height)}`,
      pos: `(${Math.round(rect.left)},${Math.round(rect.top)})`,
      parentTag: parent?.tagName || '',
      parentClass: parent?.getAttribute('class')?.slice(0, 80) || '',
      innerHTML: el.innerHTML.slice(0, 150)
    });
  });

  return JSON.stringify(results, null, 2);
})()
"""


async def run_diagnostic():
    # 获取所有可调试页面
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:9222/json")
        pages = json.loads(resp.read())
    except Exception as e:
        print(f"无法连接 CDP (端口 9222): {e}")
        print("请确认 Chrome 已用 --remote-debugging-port=9222 启动")
        sys.exit(1)

    # 找 BOSS 聊天页面
    boss_page = None
    for page in pages:
        url = page.get("url", "")
        if "zhipin.com" in url and ("chat" in url or "geek" in url):
            boss_page = page
            break

    if not boss_page:
        print("未找到 BOSS 直聘聊天页面，当前打开的页面：")
        for p in pages:
            print(f"  - {p.get('title', '?')}: {p.get('url', '?')}")
        sys.exit(1)

    ws_url = boss_page.get("webSocketDebuggerUrl")
    if not ws_url:
        print("该页面没有 webSocketDebuggerUrl，可能已被其他调试器连接")
        sys.exit(1)

    print(f"连接到: {boss_page.get('title', '?')}")
    print(f"URL: {boss_page.get('url', '?')}")
    print(f"WS: {ws_url}")
    print()

    async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
        # 执行诊断 JS
        msg = json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": DIAGNOSTIC_JS,
                "returnByValue": True,
            }
        })
        await ws.send(msg)
        response = await asyncio.wait_for(ws.recv(), timeout=15)
        result = json.loads(response)

        if "error" in result:
            print(f"CDP 错误: {result['error']}")
            sys.exit(1)

        value = result.get("result", {}).get("result", {}).get("value", "")
        if not value:
            print("诊断脚本无返回值")
            print(f"原始响应: {json.dumps(result, ensure_ascii=False, indent=2)}")
            sys.exit(1)

        data = json.loads(value)
        print("=== BOSS 简历弹窗诊断结果 ===\n")
        print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(run_diagnostic())
