# -*- coding: utf-8 -*-
"""简历文件解析：PDF / Word(docx) / TXT / Markdown -> 纯文本。

智能问答页「上传简历」功能的后端支撑：
- PDF 用 pdfplumber（已安装）；
- docx 用标准库 zipfile + xml 解析（docx 本质是 zip 包，无需 python-docx 依赖）；
- txt / md 直接按 UTF-8 解码。

docx 解析思路：读取 word/document.xml，按 <w:p>（段落）聚合 <w:t>（文本）节点。
"""
import io
import re
import zipfile
from xml.etree import ElementTree as ET

# Word 命名空间
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ResumeParseError(Exception):
    """简历文件解析失败（格式不支持 / 内容为空 / 无法解码等）。"""


def parse_resume_file(filename, data):
    """按扩展名解析简历文件字节流为纯文本。

    参数:
        filename: 原始文件名（用于判断扩展名）
        data: 文件二进制内容

    返回:
        解析出的纯文本（可能为空串）

    抛出:
        ResumeParseError: 格式不支持或解析失败
    """
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext == "pdf":
        return _parse_pdf(data)
    if ext == "docx":
        return _parse_docx(data)
    if ext in ("txt", "md", "markdown"):
        return _parse_text(data)
    raise ResumeParseError(f"不支持的文件格式「{ext or '未知'}」：请上传 PDF、Word(docx)、TXT 或 Markdown 文件")


def _parse_pdf(data):
    try:
        import pdfplumber
    except ImportError as e:
        raise ResumeParseError("PDF 解析依赖 pdfplumber 未安装，请先 pip install pdfplumber") from e
    try:
        pages = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                pages.append(text)
        return "\n\n".join(pages).strip()
    except Exception as e:
        raise ResumeParseError(f"PDF 解析失败：{e}") from e


def _parse_docx(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if "word/document.xml" not in zf.namelist():
                raise ResumeParseError("docx 文件缺少 word/document.xml，可能不是有效的 Word 文档")
            xml_data = zf.read("word/document.xml")
        root = ET.fromstring(xml_data)
        paragraphs = []
        for p in root.iter(f"{_W_NS}p"):
            runs = [t.text or "" for t in p.iter(f"{_W_NS}t")]
            line = "".join(runs).strip()
            if line:
                paragraphs.append(line)
        text = "\n".join(paragraphs).strip()
        if not text:
            raise ResumeParseError("docx 文件未提取到文本，可能为扫描件或空文档")
        return text
    except ResumeParseError:
        raise
    except Exception as e:
        raise ResumeParseError(f"docx 解析失败：{e}") from e


def _parse_text(data):
    for enc in ("utf-8", "gbk", "gb18030", "utf-16"):
        try:
            text = data.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        raise ResumeParseError("文本文件编码无法识别（支持 UTF-8 / GBK / UTF-16）")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ResumeParseError("文本文件内容为空")
    return text
