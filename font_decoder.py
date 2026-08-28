from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from fontTools.ttLib import TTFont


FetchBytes = Callable[[str], Awaitable[bytes]]


class DianpingFontDecoder:
    """Decode Dianping's private-use glyphs using dianping_spider's template map.

    The original project serialised WOFF files to XML first.  Here the same glyph
    order mapping is read directly through fontTools, which avoids temporary files.
    """

    FONT_CLASSES = (
        "address",
        "shopNum",
        "tagName",
        "reviewTag",
        "dishname",
        "shopdesc",
        "review",
        "hours",
        "num",
    )
    _CSS_LINK_RE = re.compile(
        r"(?:href|src)=[\"'](?P<url>(?://|https?://)[^\"']*s3plus\.meituan\.net/v1/[^\"']+)[\"']",
        re.I,
    )
    _FONT_FACE_RE = re.compile(r"@font-face\s*\{.*?\}", re.I | re.S)
    _WOFF_RE = re.compile(r"url\([\"']?(?P<url>[^)\"']+\.woff(?:2)?)[\"']?\)", re.I)

    def __init__(self) -> None:
        template_path = Path(__file__).with_name("assets") / "template_map.json"
        self._template: dict[str, str] = json.loads(template_path.read_text(encoding="utf-8"))
        self._font_cache: dict[str, dict[int, str]] = {}
        self._latest_maps: dict[str, dict[int, str]] = {}

    @staticmethod
    def _absolute_url(url: str) -> str:
        if url.startswith("//"):
            return "https:" + url
        return urljoin("https://www.dianping.com/", url)

    async def decode_html(self, html: str, fetch_bytes: FetchBytes) -> str:
        if "svgmtsi" not in html and "s3plus.meituan.net/v1/" not in html:
            return html
        mappings = await self._load_page_mappings(html, fetch_bytes)
        if mappings:
            self._latest_maps = mappings
        return self._replace_tags(html, mappings or self._latest_maps)

    def decode_fragment(self, fragment: str) -> str:
        if not fragment:
            return ""
        return BeautifulSoup(self._replace_tags(fragment, self._latest_maps), "lxml").get_text(" ", strip=True)

    async def _load_page_mappings(
        self, html: str, fetch_bytes: FetchBytes
    ) -> dict[str, dict[int, str]]:
        match = self._CSS_LINK_RE.search(html)
        if not match:
            return {}
        css_url = self._absolute_url(match.group("url"))
        css = (await fetch_bytes(css_url)).decode("utf-8", errors="ignore")
        mappings: dict[str, dict[int, str]] = {}
        for block in self._FONT_FACE_RE.findall(css):
            woff_match = self._WOFF_RE.search(block)
            if not woff_match:
                continue
            font_class = next((item for item in self.FONT_CLASSES if item.lower() in block.lower()), "")
            if not font_class:
                continue
            woff_url = self._absolute_url(woff_match.group("url"))
            mapping = self._font_cache.get(woff_url)
            if mapping is None:
                mapping = self._parse_woff(await fetch_bytes(woff_url))
                self._font_cache[woff_url] = mapping
            mappings[font_class] = mapping
        return mappings

    def _parse_woff(self, data: bytes) -> dict[int, str]:
        font = TTFont(BytesIO(data), lazy=True)
        try:
            glyph_order = font.getGlyphOrder()
            glyph_indexes = {name: index for index, name in enumerate(glyph_order)}
            result: dict[int, str] = {}
            for codepoint, glyph_name in (font.getBestCmap() or {}).items():
                index = glyph_indexes.get(glyph_name)
                replacement = self._template.get(f"glyph{index}") if index is not None else None
                if replacement:
                    result[codepoint] = replacement
            return result
        finally:
            font.close()

    @staticmethod
    def _replace_tags(html: str, mappings: dict[str, dict[int, str]]) -> str:
        if not mappings or "svgmtsi" not in html:
            return html
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all("svgmtsi"):
            classes = tag.get("class") or []
            font_class = next((name for name in classes if name in mappings), "")
            if not font_class:
                continue
            mapping = mappings[font_class]
            decoded = "".join(mapping.get(ord(char), char) for char in tag.get_text())
            tag.replace_with(decoded)
        return str(soup)

