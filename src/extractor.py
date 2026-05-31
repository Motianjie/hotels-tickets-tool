"""
秀米（xiumius.cn）H5 页面内容提取器。

提取流程：
1. 请求页面 HTML
2. 解析 injectedData.showInfo.show_data_url 获取 JSON 地址
3. 请求 JSON，从 cubes[].pages[].layers[]._comp._$raHTML 提取文本
4. 按分类标签组织输出
"""

import json
import re
import requests
from typing import Optional

# 默认请求头
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class XiumiusExtractor:
    """秀米 H5 页面内容提取器。"""

    def __init__(self, timeout: int = 30):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.timeout = timeout

    def extract(self, url: str) -> str:
        """
        从秀米 H5 页面提取完整文本内容。

        Args:
            url: 秀米 H5 页面链接（a.xiumius.cn / v.xiumius.cn）

        Returns:
            提取到的纯文本内容。

        Raises:
            ValueError: 无法提取内容时抛出。
            requests.RequestException: 网络请求失败时抛出。
        """
        # 步骤 1: 获取页面 HTML
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        html = resp.text

        # 步骤 2: 查找 show_data_url
        data_url = self._find_data_url(html)
        if not data_url:
            # 尝试直接提取页面中的文本内容（非秀米页面）
            text = self._extract_direct_text(html)
            if text:
                return text
            raise ValueError("无法在页面中找到 show_data_url，且无法直接提取文本内容")

        # 补全 URL
        if data_url.startswith("//"):
            data_url = "https:" + data_url

        # 步骤 3: 请求 JSON 数据
        json_resp = self.session.get(data_url, timeout=self.timeout)
        json_resp.raise_for_status()
        data = json_resp.json()

        # 步骤 4: 从 JSON 中提取文本
        text = self._extract_from_json(data)
        if not text:
            raise ValueError("JSON 数据中未找到有效文本内容")

        return text

    def _find_data_url(self, html: str) -> Optional[str]:
        """从 HTML 中查找 show_data_url。"""
        import urllib.parse

        # 先尝试查找 decodeURIComponent 包裹的 showInfo（c.xiumius.cn 格式）
        # 格式: injectedData.showInfo = JSON.parse(decodeURIComponent("%7B%22...show_data_url%22%3A%22%2F%2Fsd...%22...%7D"));
        show_info_match = re.search(
            r"injectedData\.showInfo\s*=\s*JSON\.parse\(decodeURIComponent\([\"']([^\"']+)[\"']\)\)",
            html,
        )
        if show_info_match:
            try:
                decoded = urllib.parse.unquote(show_info_match.group(1))
                info = json.loads(decoded)
                data_url = info.get("show_data_url")
                if data_url:
                    return data_url
            except (json.JSONDecodeError, KeyError):
                pass

        # 匹配直接出现的 show_data_url（a.xiumius.cn / v.xiumius.cn 格式）
        patterns = [
            r'"show_data_url"\s*:\s*"([^"]+)"',
            r"show_data_url\s*:\s*'([^']+)'",
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                return match.group(1)
        return None

    def _extract_from_json(self, data: dict) -> str:
        """从 JSON 数据中提取 cubes 中的文本内容。"""
        texts = []
        cubes = data.get("cubes") or data.get("data", {}).get("cubes") or []
        for cube in cubes:
            pages = cube.get("pages") or []
            for page in pages:
                layers = page.get("layers") or []
                for layer in layers:
                    comp = layer.get("_comp") or {}
                    ra_html = comp.get("_$raHTML") or ""
                    if ra_html:
                        # 提取 HTML 中的纯文本
                        text = self._html_to_text(ra_html)
                        if text.strip():
                            texts.append(text.strip())
        return "\n\n".join(texts)

    def _extract_direct_text(self, html: str) -> Optional[str]:
        """直接从 HTML 页面提取可见文本（非秀米页面兜底）。"""
        # 尝试提取 body 中的文本
        body_match = re.search(
            r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE
        )
        if body_match:
            text = self._html_to_text(body_match.group(1))
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if text:
                return text
        return None

    @staticmethod
    def _html_to_text(html: str) -> str:
        """将 HTML 转换为纯文本。"""
        # 移除 script 和 style 标签
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        # 替换块级标签为换行
        text = re.sub(r"</?(?:div|p|br|li|h[1-6]|tr|blockquote)[^>]*>", "\n", text)
        # 移除其他 HTML 标签
        text = re.sub(r"<[^>]+>", "", text)
        # 解码 HTML 实体
        text = text.replace("&nbsp;", " ").replace("&amp;", "&")
        text = text.replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&quot;", '"').replace("&#39;", "'")
        # 清理多余空白
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n", "\n\n", text)
        return text.strip()
