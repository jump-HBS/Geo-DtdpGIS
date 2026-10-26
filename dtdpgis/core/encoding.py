"""编码容忍的文本读取器。"""

from __future__ import annotations

from pathlib import Path


def read_text_auto(path: Path) -> str:
    """尝试使用多种编码读取文本文件。"""
    data = path.read_bytes()# 读取文件内容字节流
    for encoding in ("utf-8-sig", "gbk", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)# 尝试解码
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")# 如果所有编码都失败，使用utf-8编码，替换错误字符
