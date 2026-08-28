# -*- coding: utf-8 -*-
"""联网检索服务：对标岗位 JD 检索同类型优质简历范文、行业招聘筛选标准、HR 简历筛选关注点。

复用成熟的全网检索逻辑（Bing 搜索 -> 逐页抓取正文），为简历优化提供高分写作范式与关键词埋点参考。
"""
import base64
import re
import time
from html.parser import HTMLParser

import requests

# 检索结果进程内缓存：key=岗位名，value=(时间戳, items)。TTL 内同一岗位不再打 Bing。
_SEARCH_CACHE = {}
_SEARCH_CACHE_TTL = 600  # 10 分钟

from config import SEARCH_FETCH_LIMIT, SEARCH_PER_LIMIT, SEARCH_TOTAL_LIMIT

_BING_URL = "https://www.bing.com/search"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"}

# 页面正文抓取上限（字符数），避免 LLM 上下文过长
_PAGE_TEXT_LIMIT = 6000
# 页面抓取超时（秒）
_FETCH_TIMEOUT = 15


def _extract_real_url(href):
    """从 Bing 重定向链接中提取真实目标 URL。"""
    if not href:
        return ""
    if "bing.com/ck/a" in href:
        m = re.search(r'[?&]u=([^&]+)', href)
        if m:
            raw = m.group(1)
            padding = 4 - len(raw) % 4
            if padding != 4:
                raw += "=" * padding
            try:
                decoded = base64.urlsafe_b64decode(raw).decode("utf-8")
                if decoded.startswith("http"):
                    return decoded
            except Exception:
                pass
    if href.startswith("http"):
        return href
    return ""


class _BingParser(HTMLParser):
    """从 Bing 搜索结果页提取标题、链接与摘要。"""

    def __init__(self):
        super().__init__()
        self.items = []
        self._in_algo = False
        self._in_title = False
        self._in_snippet = False
        self._cur_title = ""
        self._cur_snippet = ""
        self._cur_url = ""

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        cls = (d.get("class") or "").lower()
        if tag == "li" and "b_algo" in cls.split():
            self._in_algo = True
        if self._in_algo:
            if tag == "h2":
                self._in_title = True
                self._cur_title = ""
            elif tag == "a":
                href = d.get("href", "")
                real = _extract_real_url(d.get("citerewriteurl", "") or href)
                if real and (not self._cur_url or len(real) > len(self._cur_url)):
                    self._cur_url = real
            elif tag == "p" or (tag == "div" and "b_caption" in cls):
                self._in_snippet = True
                self._cur_snippet = ""

    def handle_data(self, data):
        if self._in_title:
            self._cur_title += data
        elif self._in_snippet:
            self._cur_snippet += data

    def handle_endtag(self, tag):
        if self._in_title and tag == "h2":
            self._in_title = False
        if self._in_algo and tag == "li":
            self._in_algo = False
            title = re.sub(r"\s+", " ", self._cur_title).strip()
            snippet = re.sub(r"\s+", " ", self._cur_snippet).strip()
            if title:
                self.items.append({"title": title, "snippet": snippet, "url": self._cur_url})
                self._cur_url = ""


def _extract_text_from_html(html):
    """从 HTML 中提取可见文本，去除 script/style 标签与多余空白。"""
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.S | re.I)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.S | re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = text.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&amp;', '&').replace('&quot;', '"').replace('&#39;', "'")
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n\n', text)
    return text.strip()


def _fetch_page_body(url):
    """抓取页面正文，返回纯文本（最多 _PAGE_TEXT_LIMIT 字符）。"""
    try:
        resp = requests.get(url, headers=_UA, timeout=_FETCH_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        text = _extract_text_from_html(resp.text)
        if len(text) > _PAGE_TEXT_LIMIT:
            text = text[:_PAGE_TEXT_LIMIT] + "..."
        return text
    except Exception:
        return ""


def search_references_stream(job_title, max_results=8):
    """流式检索简历范文与 HR 筛选标准，逐步 yield 进度与最终来源列表。

    检索词围绕岗位名展开：优质简历范文、招聘筛选标准、HR 简历关注点三类意图。

    进程内缓存：同一岗位名的检索结果缓存 10 分钟，避免同一简历反复优化 / 多轮迭代
    时每轮都重复打 Bing（检索是迭代流程里最慢的外部依赖）。命中缓存时直接回放，
    不发起任何网络请求。
    """
    # 岗位名可能为空（_guess_job_title 提取失败）：降级为通用优质简历检索词，
    # 避免拼出「 简历范文…」或把整句 JD 当岗位名搜出垃圾结果。
    title_key = (job_title or "").strip() or "通用简历范文"
    cached = _SEARCH_CACHE.get(title_key)
    if cached and (time.time() - cached[0]) < _SEARCH_CACHE_TTL:
        items = cached[1]
        yield {"type": "progress", "step": "search",
               "message": f"命中检索缓存（{title_key}），跳过联网检索。"}
        yield {"type": "result", "items": items, "from_cache": True}
        return

    if title_key != "通用简历范文":
        queries = [
            f"{title_key} 简历范文 模板 项目经历 量化",
            f"{title_key} 招聘 筛选标准 HR 看重 简历关键词",
        ]
    else:
        queries = [
            "优质简历范文 模板 项目经历 量化 写法 求职",
            "HR 简历筛选标准 看重 关键词 招聘 简历优化",
        ]
    gathered = []
    seen_urls = set()
    for qi, q in enumerate(queries):
        yield {"type": "progress", "step": "search",
               "message": f"正在用 Bing 检索「{q}」…（{qi + 1}/{len(queries)}）"}
        try:
            params = {"q": q, "count": str(max_results + 2), "setlang": "zh-Hans"}
            resp = requests.get(_BING_URL, params=params, headers=_UA, timeout=20)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            html = resp.text
        except requests.RequestException:
            continue
        parser = _BingParser()
        parser.feed(html)
        items = parser.items[:max_results]
        fetch_items = items[:SEARCH_FETCH_LIMIT]
        total = len(fetch_items)
        for i, item in enumerate(fetch_items):
            url = item.get("url", "")
            if url and url not in seen_urls:
                yield {"type": "progress", "step": "fetch",
                       "message": f"正在抓取参考来源：{item.get('title', '')[:24]}"}
                body = _fetch_page_body(url)
                item["body"] = body
                time.sleep(0.6)
                seen_urls.add(url)
                gathered.append(item)
            elif url:
                item["body"] = ""
    _SEARCH_CACHE[title_key] = (time.time(), gathered)
    yield {"type": "result", "items": gathered}


def build_reference_context(items):
    """把检索到的来源整理成喂给模型的参考素材（带总量上限）。"""
    parts = []
    total = 0
    for i, r in enumerate(items):
        body = r.get("body", "")
        snippet = r.get("snippet", "")
        content = body if (body and len(body) > 200) else snippet
        if len(content) > SEARCH_PER_LIMIT:
            content = content[:SEARCH_PER_LIMIT] + "..."
        if total + len(content) > SEARCH_TOTAL_LIMIT:
            remaining = SEARCH_TOTAL_LIMIT - total
            if remaining > 200:
                parts.append(f"【参考来源{i + 1}：{r['title']}】\n{content[:remaining]}")
            break
        parts.append(f"【参考来源{i + 1}：{r['title']}】\n{content}")
        total += len(content)
    return "\n\n".join(parts)
