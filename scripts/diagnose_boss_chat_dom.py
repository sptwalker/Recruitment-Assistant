"""
通过 Chrome DevTools Protocol (CDP) 远程探查 BOSS 聊天页候选人列表 DOM 结构。
绕过 BOSS 反调试保护（禁用 F12）。

用法：
  1. 关闭所有 Chrome 窗口
  2. 用以下命令启动 Chrome：
     chrome.exe --remote-debugging-port=9222
  3. 在 Chrome 中打开 BOSS 聊天页面 (www.zhipin.com/web/chat)
  4. 运行本脚本：python scripts/diagnose_boss_chat_dom.py
"""
import json
import sys
import urllib.request
import asyncio

try:
    import websockets
except ImportError:
    print("请安装 websockets: pip install websockets")
    sys.exit(1)


DIAGNOSTIC_JS = r"""
(function() {
  const results = {};

  // ====== 1. 沟通中标签 ======
  // 旧选择器: .chat-label-item[title="沟通中"]
  results.chatting_tab = {};

  const oldTab = document.querySelector('.chat-label-item[title="沟通中"]');
  results.chatting_tab.old_selector_match = oldTab ? {
    tag: oldTab.tagName,
    class: oldTab.className,
    text: (oldTab.textContent || '').trim().slice(0, 50),
    rect: (() => { const r = oldTab.getBoundingClientRect(); return {l: Math.round(r.left), t: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)}; })()
  } : null;

  // 搜索包含"沟通中"文本的所有可点击元素
  const allClickables = document.querySelectorAll('a, button, [role="button"], li, span, div, label');
  const chattingCandidates = [];
  allClickables.forEach(el => {
    const text = (el.textContent || '').trim();
    if (text === '沟通中' || text === '沟通中 ') {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        chattingCandidates.push({
          tag: el.tagName,
          class: (el.className || '').toString().slice(0, 150),
          id: el.id || '',
          title: el.getAttribute('title') || '',
          role: el.getAttribute('role') || '',
          text: text.slice(0, 30),
          rect: {l: Math.round(rect.left), t: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height)},
          parentTag: el.parentElement?.tagName || '',
          parentClass: (el.parentElement?.className || '').toString().slice(0, 100),
          // 向上3层的路径
          path: (() => {
            const parts = [];
            let cur = el;
            for (let i = 0; i < 4 && cur; i++) {
              const cls = (cur.className || '').toString().replace(/\s+/g, '.').slice(0, 60);
              parts.push(cur.tagName + (cls ? '.' + cls : '') + (cur.id ? '#' + cur.id : ''));
              cur = cur.parentElement;
            }
            return parts.join(' < ');
          })()
        });
      }
    }
  });
  results.chatting_tab.text_search = chattingCandidates;

  // ====== 2. 聊天菜单导航 ======
  // 旧选择器: dl.menu-chat a[href*="/web/chat"]
  results.chat_menu = {};

  const oldMenu = document.querySelector('dl.menu-chat a[href*="/web/chat"]');
  results.chat_menu.old_selector_match = oldMenu ? {
    tag: oldMenu.tagName,
    class: oldMenu.className,
    text: (oldMenu.textContent || '').trim().slice(0, 50),
    href: oldMenu.getAttribute('href') || ''
  } : null;

  const chatLinks = document.querySelectorAll('a[href*="/web/chat"], a[href*="chat"]');
  results.chat_menu.chat_links = [];
  chatLinks.forEach(el => {
    const rect = el.getBoundingClientRect();
    if (rect.width > 0) {
      results.chat_menu.chat_links.push({
        tag: el.tagName,
        class: (el.className || '').toString().slice(0, 100),
        href: el.getAttribute('href') || '',
        text: (el.textContent || '').trim().slice(0, 50),
        rect: {l: Math.round(rect.left), t: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height)},
        parentTag: el.parentElement?.tagName || '',
        parentClass: (el.parentElement?.className || '').toString().slice(0, 100)
      });
    }
  });

  // ====== 3. 候选人列表容器 ======
  // 旧选择器: div.user-list.b-scroll-stable / div.user-list
  results.list_container = {};

  const oldContainer = document.querySelector('div.user-list.b-scroll-stable') || document.querySelector('div.user-list');
  results.list_container.old_selector_match = oldContainer ? {
    tag: oldContainer.tagName,
    class: oldContainer.className,
    childCount: oldContainer.children.length,
    scrollHeight: oldContainer.scrollHeight,
    clientHeight: oldContainer.clientHeight,
    rect: (() => { const r = oldContainer.getBoundingClientRect(); return {l: Math.round(r.left), t: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)}; })()
  } : null;

  // 搜索左侧区域中所有可滚动的大容器 (<45%宽度, 高度>200)
  const maxLeft = Math.min(window.innerWidth * 0.45, 520);
  const potentialContainers = [];
  document.querySelectorAll('div, ul, section').forEach(el => {
    const rect = el.getBoundingClientRect();
    if (rect.left < maxLeft && rect.width > 100 && rect.height > 200 &&
        rect.top > 50 && el.children.length > 3) {
      const style = window.getComputedStyle(el);
      const isScrollable = el.scrollHeight > el.clientHeight + 10 ||
                           style.overflow === 'auto' || style.overflow === 'scroll' ||
                           style.overflowY === 'auto' || style.overflowY === 'scroll' ||
                           el.className.toString().includes('scroll');
      potentialContainers.push({
        tag: el.tagName,
        class: (el.className || '').toString().slice(0, 150),
        id: el.id || '',
        childCount: el.children.length,
        scrollable: isScrollable,
        scrollH: el.scrollHeight,
        clientH: el.clientHeight,
        rect: {l: Math.round(rect.left), t: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height)},
        // 前3个子元素的标签和class
        firstChildren: Array.from(el.children).slice(0, 3).map(c => ({
          tag: c.tagName,
          class: (c.className || '').toString().slice(0, 80),
        }))
      });
    }
  });
  // 按 childCount * scrollable 排序
  potentialContainers.sort((a, b) => (b.childCount * (b.scrollable ? 10 : 1)) - (a.childCount * (a.scrollable ? 10 : 1)));
  results.list_container.potential = potentialContainers.slice(0, 10);

  // ====== 4. 候选人条目 ======
  // 旧选择器: .geek-item-wrap > .geek-item
  results.candidate_items = {};

  const oldItems = document.querySelectorAll('.geek-item-wrap > .geek-item');
  results.candidate_items.old_selector_count = oldItems.length;

  const oldWraps = document.querySelectorAll('.geek-item-wrap');
  results.candidate_items.old_wrap_count = oldWraps.length;

  // 搜索包含年龄模式 (NN岁) 或教育关键词的列表项
  const agePattern = /\d{2}\s*岁/;
  const eduPattern = /本科|大专|硕士|博士|研究生|专科|高中|中专/;
  const candidateElements = [];
  document.querySelectorAll('li, [class*="item"], [class*="card"], [class*="geek"], [class*="user"]').forEach(el => {
    const rect = el.getBoundingClientRect();
    if (rect.left > maxLeft || rect.width < 80 || rect.height < 30) return;
    const text = (el.textContent || '').trim();
    if ((agePattern.test(text) || eduPattern.test(text)) && text.length < 200) {
      candidateElements.push({
        tag: el.tagName,
        class: (el.className || '').toString().slice(0, 150),
        text: text.replace(/\s+/g, ' ').slice(0, 120),
        rect: {l: Math.round(rect.left), t: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height)},
        parentTag: el.parentElement?.tagName || '',
        parentClass: (el.parentElement?.className || '').toString().slice(0, 100),
        childCount: el.children.length,
        // 向上3层路径
        path: (() => {
          const parts = [];
          let cur = el;
          for (let i = 0; i < 5 && cur && cur !== document.body; i++) {
            const cls = (cur.className || '').toString().replace(/\s+/g, '.').slice(0, 60);
            parts.push(cur.tagName + (cls ? '.' + cls : ''));
            cur = cur.parentElement;
          }
          return parts.join(' < ');
        })()
      });
    }
  });
  results.candidate_items.text_search = candidateElements.slice(0, 20);

  // ====== 5. 旧选择器全量检测 ======
  const OLD_SELECTORS = {
    'div.user-list.b-scroll-stable': null,
    'div.user-list': null,
    '.geek-item-wrap > .geek-item': null,
    '.geek-item-wrap': null,
    '.geek-item': null,
    '.chat-label-item[title="沟通中"]': null,
    '.chat-label-item': null,
    'dl.menu-chat a[href*="/web/chat"]': null,
    'dl.menu-chat': null,
    '.chat-list': null,
    '.chat-list li': null,
    '.friend-list': null,
    '.friend-list li': null,
    '[class*="chat-list"]': null,
    '[class*="user-list"]': null,
    '[class*="friend-list"]': null,
    '[class*="conversation"]': null,
    '[class*="geek"]': null,
  };
  for (const sel of Object.keys(OLD_SELECTORS)) {
    try {
      OLD_SELECTORS[sel] = document.querySelectorAll(sel).length;
    } catch(e) {
      OLD_SELECTORS[sel] = 'error: ' + e.message;
    }
  }
  results.old_selectors_count = OLD_SELECTORS;

  // ====== 6. 页面基础信息 ======
  results.page_info = {
    url: location.href,
    title: document.title,
    viewport: { w: window.innerWidth, h: window.innerHeight },
    bodyClasses: document.body.className?.slice(0, 200) || '',
    mainDivs: Array.from(document.body.children)
      .filter(el => el.tagName === 'DIV')
      .slice(0, 10)
      .map(el => ({
        class: (el.className || '').toString().slice(0, 100),
        id: el.id || '',
        childCount: el.children.length,
        rect: (() => { const r = el.getBoundingClientRect(); return {w: Math.round(r.width), h: Math.round(r.height)}; })()
      }))
  };

  return JSON.stringify(results, null, 2);
})()
"""


async def run_diagnostic():
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:9222/json")
        pages = json.loads(resp.read())
    except Exception as e:
        print(f"无法连接 CDP (端口 9222): {e}")
        print("请确认 Chrome 已用 --remote-debugging-port=9222 启动")
        print()
        print("启动命令示例：")
        print('  chrome.exe --remote-debugging-port=9222')
        print("  或指定完整路径：")
        print('  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222')
        sys.exit(1)

    boss_page = None
    for page in pages:
        url = page.get("url", "")
        if "zhipin.com" in url:
            boss_page = page
            break

    if not boss_page:
        print("未找到 BOSS 直聘页面，当前打开的页面：")
        for p in pages:
            print(f"  - {p.get('title', '?')}: {p.get('url', '?')}")
        print()
        print("请在 Chrome 中打开 www.zhipin.com/web/chat")
        sys.exit(1)

    ws_url = boss_page.get("webSocketDebuggerUrl")
    if not ws_url:
        print("该页面没有 webSocketDebuggerUrl，可能已被其他调试器连接")
        sys.exit(1)

    print(f"连接到: {boss_page.get('title', '?')}")
    print(f"URL: {boss_page.get('url', '?')}")
    print()

    async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
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

        output_file = "boss_chat_dom_diagnostic.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"诊断结果已保存到 {output_file}")
        print()

        # 打印摘要
        print("=" * 60)
        print("BOSS 聊天页 DOM 结构诊断摘要")
        print("=" * 60)

        print()
        print("--- 旧选择器匹配情况 ---")
        for sel, count in data.get("old_selectors_count", {}).items():
            status = "OK" if isinstance(count, int) and count > 0 else "MISS"
            print(f"  [{status}] {sel}: {count}")

        print()
        print("--- 沟通中标签 ---")
        tab = data.get("chatting_tab", {})
        if tab.get("old_selector_match"):
            print(f"  旧选择器仍有效: {tab['old_selector_match']}")
        else:
            print("  旧选择器失效!")
        for item in tab.get("text_search", []):
            print(f"  找到文本匹配: <{item['tag']}> class=\"{item['class']}\" path={item.get('path', '')}")

        print()
        print("--- 候选人列表容器 ---")
        lc = data.get("list_container", {})
        if lc.get("old_selector_match"):
            print(f"  旧选择器仍有效: children={lc['old_selector_match']['childCount']}")
        else:
            print("  旧选择器失效!")
        for item in lc.get("potential", [])[:5]:
            print(f"  潜在容器: <{item['tag']}> class=\"{item['class'][:80]}\" children={item['childCount']} scrollable={item['scrollable']}")

        print()
        print("--- 候选人条目 ---")
        ci = data.get("candidate_items", {})
        print(f"  旧 .geek-item-wrap 数量: {ci.get('old_wrap_count', 0)}")
        print(f"  旧 .geek-item 数量: {ci.get('old_selector_count', 0)}")
        for item in ci.get("text_search", [])[:5]:
            print(f"  文本匹配: <{item['tag']}> class=\"{item['class'][:60]}\" text=\"{item['text'][:60]}\"")
            print(f"    path: {item.get('path', '')}")

        print()
        print(f"完整诊断数据: {output_file}")


if __name__ == "__main__":
    asyncio.run(run_diagnostic())
