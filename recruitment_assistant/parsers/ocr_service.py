"""PaddleOCR 集成 — 图像格式 PDF 简历的 OCR 回退。

pymupdf / pypdf 抽不出文本（< 200 字符）时，把 PDF 每页用 pymupdf 渲染成图，
喂给 PaddleOCR。首次调用懒加载 PaddleOCR 实例并复用。

OCR 结果写入 PDF 同目录 `<文件名>.ocr.txt`，下次同 PDF 直接读缓存。
PaddleOCR 未安装时 is_paddleocr_available() 返回 False，调用方应优雅降级。
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Callable, Optional

_ocr_instance = None
_availability_checked = False
_availability_result = False


def is_paddleocr_available() -> bool:
    """检查 paddleocr 是否可导入；结果缓存，避免每次都触发 import 开销。"""
    global _availability_checked, _availability_result
    if _availability_checked:
        return _availability_result
    _availability_checked = True
    try:
        import paddleocr  # noqa: F401
        _availability_result = True
    except Exception:
        _availability_result = False
    return _availability_result


def _get_ocr():
    """懒加载 PaddleOCR 实例（中英文 + 方向检测）。首次约 3-5 秒，之后复用。

    PaddleOCR 2.x 与 3.x 构造参数不兼容：2.x 用 use_angle_cls/show_log，3.x 用
    use_textline_orientation 且不接受 show_log。逐级尝试以同时兼容两个大版本。
    """
    global _ocr_instance
    if _ocr_instance is not None:
        return _ocr_instance
    from paddleocr import PaddleOCR
    for kwargs in (
        # 3.x：text_det_limit_side_len 默认 4000，长图简历会被强缩放导致识别率掉；放宽到 8000
        {"lang": "ch", "use_textline_orientation": True, "text_det_limit_side_len": 8000},
        {"lang": "ch", "use_textline_orientation": True},
        {"lang": "ch", "use_angle_cls": True, "show_log": False},  # 2.x
        {"lang": "ch"},
    ):
        try:
            _ocr_instance = PaddleOCR(**kwargs)
            return _ocr_instance
        except (TypeError, ValueError):
            continue
    raise RuntimeError("PaddleOCR 初始化失败：无法匹配 2.x / 3.x 构造签名")


def _cache_path(pdf_path: Path) -> Path:
    return pdf_path.with_suffix(pdf_path.suffix + ".ocr.txt")


def _extract_text_from_paddle_result(result) -> list[str]:
    """兼容 PaddleOCR 2.x list 与 3.x OCRResult/dict 两套返回结构。"""
    lines: list[str] = []
    if not result:
        return lines
    try:
        items = list(result)
    except TypeError:
        items = [result]
    for page in items:
        # 3.x: OCRResult 既支持 [] 也支持 .get；优先取 rec_texts
        texts = None
        try:
            texts = page["rec_texts"]
        except Exception:
            texts = getattr(page, "rec_texts", None)
        if isinstance(texts, list):
            lines.extend(str(t).strip() for t in texts if str(t).strip())
            continue
        # 2.x: page 本身是 [ [box, (text, conf)], ... ]
        if isinstance(page, list):
            for item in page:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    text_part = item[1]
                    if isinstance(text_part, (list, tuple)) and text_part:
                        lines.append(str(text_part[0]).strip())
                    elif isinstance(text_part, str):
                        lines.append(text_part.strip())
    return [line for line in lines if line]


def ocr_pdf_to_text(
    pdf_path: str | Path,
    log: Optional[Callable[[str], None]] = None,
    use_cache: bool = True,
    dpi: int = 200,
) -> str:
    """对 PDF 跑 OCR，返回拼接的纯文本。命中 `.ocr.txt` 缓存则直接返回。"""
    pdf_path = Path(pdf_path)
    cache_file = _cache_path(pdf_path)

    if use_cache and cache_file.exists():
        cached = cache_file.read_text(encoding="utf-8")
        if cached.strip():
            if log:
                log(f"           📦 命中 OCR 缓存：{cache_file.name}（{len(cached)} 字符）")
            return cached

    if not is_paddleocr_available():
        raise RuntimeError("PaddleOCR 未安装；请 pip install paddlepaddle paddleocr")

    import pymupdf
    import numpy as np
    from PIL import Image

    ocr = _get_ocr()

    # 屏幕滚动型长图简历单页物理尺寸 > 10000 pt（约 3.5 米+），渲染会得到数万像素高的图，
    # 既触发 OpenCV warpPerspective 的 SHRT_MAX 限制，OCR 内部也会强缩放导致行高失真识别率骤降。
    # 这类文件 OCR 价值很低，直接跳过该页。标准 A4 仅 595x842 pt，阈值 8000 pt 已经很宽松。
    SKIP_LONG_PAGE_PT = 8000
    all_lines: list[str] = []
    skipped = 0
    with pymupdf.open(str(pdf_path)) as doc:
        page_count = len(doc)
        for idx, page in enumerate(doc, 1):
            max_pt = max(page.rect.width, page.rect.height)
            if max_pt > SKIP_LONG_PAGE_PT:
                skipped += 1
                if log:
                    log(f"           ⏭️ 第 {idx}/{page_count} 页物理尺寸过大（最长边 {max_pt:.0f}pt > {SKIP_LONG_PAGE_PT}pt），跳过 OCR")
                continue
            if log:
                log(f"           🔍 OCR 渲染并识别第 {idx}/{page_count} 页…")
            pix = page.get_pixmap(dpi=dpi)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            arr = np.array(img)
            try:
                if hasattr(ocr, "predict"):
                    result = ocr.predict(arr)
                else:
                    result = ocr.ocr(arr, cls=True)
            except TypeError:
                result = ocr.ocr(arr)
            page_lines = _extract_text_from_paddle_result(result)
            all_lines.extend(page_lines)
    if skipped == page_count and page_count > 0 and log:
        log(f"           ⚠️ {page_count} 页全部因尺寸过大被跳过，OCR 未产出文本")

    text = "\n".join(all_lines)
    if use_cache and text.strip():
        try:
            cache_file.write_text(text, encoding="utf-8")
        except Exception:
            pass
    return text
