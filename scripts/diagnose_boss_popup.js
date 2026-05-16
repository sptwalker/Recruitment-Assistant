/**
 * BOSS 简历弹窗诊断脚本
 * 使用方法：在 BOSS 聊天页面打开候选人的附件简历弹窗后，
 * 打开 DevTools Console (F12)，粘贴本脚本执行。
 *
 * 输出：弹窗结构、所有 SVG 元素、可能的下载按钮/链接
 */
(function() {
  console.log("=== BOSS 简历弹窗诊断 ===");

  // 1. 查找所有可能的弹窗/浮层
  const modals = document.querySelectorAll(
    '.dialog-wrap, .resume-dialog, .resume-preview, [class*="modal"], [class*="dialog"], [class*="popup"], [class*="preview"], [class*="resume"]'
  );
  console.log(`[1] 匹配弹窗选择器的元素: ${modals.length} 个`);
  modals.forEach((el, i) => {
    const rect = el.getBoundingClientRect();
    if (rect.width > 100 && rect.height > 100 && rect.width < window.innerWidth) {
      console.log(`  弹窗#${i}: tag=${el.tagName} class="${el.className}" size=${Math.round(rect.width)}x${Math.round(rect.height)} visible=${rect.width > 0}`);
    }
  });

  // 2. 查找所有可见的大面积浮层（z-index 高的）
  const allElements = document.querySelectorAll('*');
  const overlays = [];
  allElements.forEach(el => {
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
  console.log(`[2] 高 z-index 浮层: ${overlays.length} 个`);
  overlays.slice(0, 5).forEach((item, i) => {
    const el = item.el;
    console.log(`  浮层#${i}: z=${item.z} tag=${el.tagName} class="${typeof el.className === 'string' ? el.className.slice(0, 120) : el.className?.baseVal?.slice(0, 120) || '(SVG)'}" size=${Math.round(item.rect.width)}x${Math.round(item.rect.height)}`);
  });

  // 3. 在最大浮层内查找所有 SVG 元素
  const targetOverlay = overlays[0]?.el || document.body;
  console.log(`[3] 在最大浮层内扫描 SVG (容器: ${targetOverlay.tagName}.${typeof targetOverlay.className === 'string' ? targetOverlay.className.slice(0, 60) : ''})`);

  const svgs = targetOverlay.querySelectorAll('svg');
  console.log(`  找到 SVG 元素: ${svgs.length} 个`);
  svgs.forEach((svg, i) => {
    const rect = svg.getBoundingClientRect();
    if (rect.width < 5 || rect.height < 5) return; // 跳过不可见的
    const cls = svg.getAttribute('class') || '';
    const parent = svg.parentElement;
    const parentTag = parent ? `${parent.tagName}.${parent.getAttribute('class') || ''}` : '(none)';
    const parentClickable = parent ? (parent.tagName === 'A' || parent.tagName === 'BUTTON' || parent.getAttribute('role') === 'button' || parent.style.cursor === 'pointer') : false;
    const title = svg.querySelector('title')?.textContent || '';
    const ariaLabel = svg.getAttribute('aria-label') || parent?.getAttribute('aria-label') || '';
    const innerHTML = svg.innerHTML.slice(0, 200);

    // 检查是否像下载图标（path 中有向下箭头特征）
    const paths = svg.querySelectorAll('path');
    const pathD = paths.length > 0 ? paths[0].getAttribute('d')?.slice(0, 100) : '';

    console.log(`  SVG#${i}: class="${cls}" size=${Math.round(rect.width)}x${Math.round(rect.height)} pos=(${Math.round(rect.left)},${Math.round(rect.top)})`);
    console.log(`    parent: ${parentTag} clickable=${parentClickable}`);
    if (title) console.log(`    title: "${title}"`);
    if (ariaLabel) console.log(`    aria-label: "${ariaLabel}"`);
    console.log(`    pathD: "${pathD}"`);
    console.log(`    innerHTML(前200): ${innerHTML}`);
  });

  // 4. 查找所有可能的下载相关元素（a[download], button 含"下载"文字, icon-download 类名等）
  console.log(`[4] 下载相关元素扫描`);
  const downloadCandidates = targetOverlay.querySelectorAll(
    'a[download], a[href*="download"], a[href*=".pdf"], button, [class*="download"], [class*="Download"], [title*="下载"], [aria-label*="下载"], [class*="icon-download"]'
  );
  downloadCandidates.forEach((el, i) => {
    const rect = el.getBoundingClientRect();
    if (rect.width < 5) return;
    const text = (el.textContent || '').trim().slice(0, 50);
    const cls = el.getAttribute('class') || '';
    const href = el.getAttribute('href') || '';
    const title = el.getAttribute('title') || '';
    const ariaLabel = el.getAttribute('aria-label') || '';
    console.log(`  候选#${i}: tag=${el.tagName} class="${cls.slice(0, 100)}" text="${text}" href="${href.slice(0, 100)}" title="${title}" aria="${ariaLabel}" pos=(${Math.round(rect.left)},${Math.round(rect.top)}) size=${Math.round(rect.width)}x${Math.round(rect.height)}`);
  });

  // 5. 特别扫描 boss-svg / icon 类名的元素
  console.log(`[5] boss-svg / icon 类名元素`);
  const bossIcons = targetOverlay.querySelectorAll('[class*="boss-svg"], [class*="icon"], [class*="Icon"]');
  bossIcons.forEach((el, i) => {
    const rect = el.getBoundingClientRect();
    if (rect.width < 10 || rect.width > 60) return; // 图标通常 16-48px
    const cls = el.getAttribute('class') || '';
    const parent = el.parentElement;
    const parentCls = parent?.getAttribute('class') || '';
    console.log(`  图标#${i}: tag=${el.tagName} class="${cls.slice(0, 100)}" size=${Math.round(rect.width)}x${Math.round(rect.height)} pos=(${Math.round(rect.left)},${Math.round(rect.top)}) parent="${parent?.tagName}.${parentCls.slice(0, 60)}"`);
  });

  // 6. 弹窗顶部区域（通常下载按钮在右上角）
  if (overlays.length > 0) {
    const topArea = overlays[0].rect;
    console.log(`[6] 弹窗右上角区域 (x>${Math.round(topArea.right - 200)}, y<${Math.round(topArea.top + 80)}) 的可点击元素:`);
    const clickables = targetOverlay.querySelectorAll('a, button, [role="button"], [onclick]');
    clickables.forEach((el, i) => {
      const rect = el.getBoundingClientRect();
      if (rect.left > topArea.right - 200 && rect.top < topArea.top + 80 && rect.width > 5) {
        const cls = el.getAttribute('class') || '';
        const text = (el.textContent || '').trim().slice(0, 30);
        const href = el.getAttribute('href') || '';
        console.log(`  右上角#${i}: tag=${el.tagName} class="${cls.slice(0, 80)}" text="${text}" href="${href.slice(0, 80)}" pos=(${Math.round(rect.left)},${Math.round(rect.top)}) size=${Math.round(rect.width)}x${Math.round(rect.height)}`);
      }
    });
  }

  console.log("=== 诊断完成 ===");
  console.log("请将以上输出全部复制给我分析。");
})();
