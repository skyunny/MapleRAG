"""공지 본문 HTML → 읽을 수 있는 텍스트.

공식 공지의 contents 는 스타일이 잔뜩 붙은 HTML(표 포함)이다. LLM 추출 비용과 정확도를 위해
- script/style 제거, 블록 태그는 줄바꿈, 표는 한 행을 ' | ' 로 이어 한 줄로 만든다.
- 이미지는 대체 텍스트(alt)가 있으면 남기고 없으면 버린다(이미지 속 정보는 추출 불가: 한계로 기록).
표준 라이브러리만 사용한다.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"}
_SKIP = {"script", "style"}


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def _emit(self, s: str):
        (self._cell if self._cell is not None else self.out).append(s)

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "img":
            alt = (dict(attrs).get("alt") or "").strip()
            if alt:
                self._emit(f"[이미지: {alt}]")
        elif tag in _BLOCK:
            self._emit("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in ("td", "th") and self._cell is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            if self._row is not None and text:
                self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.out.append("\n" + " | ".join(self._row) + "\n")
            self._row = None
        elif tag in _BLOCK:
            self._emit("\n")

    def handle_data(self, data):
        if not self._skip:
            self._emit(data)


def html_to_text(raw: str) -> str:
    if "<" not in raw:
        return raw.strip()
    p = _Extractor()
    p.feed(raw)
    p.close()
    text = html.unescape("".join(p.out)).replace("\xa0", " ").replace("​", "")
    text = "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()
