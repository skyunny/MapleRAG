"""패치노트 본문을 '■' 소제목 단위 섹션으로 나눈다 (추출 단위 = 섹션).

설계 이유
- 본문 전체(평균 3.4만 자)를 한 번에 넣으면 비싸고, 모델이 후반부를 놓치기 쉽다.
- '■ 소제목'은 공식 패치노트의 자연스러운 경계라 근거 문장이 섹션 안에 모인다.
- 너무 긴 섹션은 줄 경계에서 max_chars 이하로 다시 나눈다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Section:
    doc_notice_id: int
    patch_title: str
    patch_date: str
    title: str          # '■' 뒤 소제목 (없으면 '(머리말)')
    text: str           # 섹션 본문 (소제목 줄 포함)
    part: int = 1       # 긴 섹션을 나눴을 때 조각 번호

    @property
    def key(self) -> str:
        return f"{self.doc_notice_id}:{self.title}#{self.part}"


_H = re.compile(r"^■\s*(.+)$")


def split_sections(doc: dict, max_chars: int = 3500, min_chars: int = 60) -> list[Section]:
    meta = doc["meta"]
    base = dict(doc_notice_id=meta["notice_id"], patch_title=doc["title"], patch_date=meta["date"][:10])
    blocks: list[tuple[str, list[str]]] = [("(머리말)", [])]
    for line in doc["text"].splitlines():
        m = _H.match(line.strip())
        if m:
            blocks.append((m.group(1).strip(), [line.strip()]))
        elif line.strip():
            blocks[-1][1].append(line.strip())

    out: list[Section] = []
    for title, lines in blocks:
        body = "\n".join(lines)
        if len(body) < min_chars:
            continue
        # 긴 섹션은 줄 경계에서 분할
        parts, buf, size = [], [], 0
        for ln in lines:
            if buf and size + len(ln) + 1 > max_chars:
                parts.append("\n".join(buf)); buf, size = [], 0
            buf.append(ln); size += len(ln) + 1
        if buf:
            parts.append("\n".join(buf))
        for i, p in enumerate(parts, 1):
            out.append(Section(title=title, text=p, part=i, **base))
    return out


@dataclass
class Chunk:
    """LLM 한 번 호출의 입력 단위: 같은 패치노트의 인접 섹션들을 묶은 것."""
    doc_notice_id: int
    patch_title: str
    patch_date: str
    titles: list[str]
    text: str
    prev_titles: list[str] = field(default_factory=list)   # 직전 청크의 마지막 소제목(직업명 등 문맥)

    @property
    def key(self) -> str:
        return f"{self.doc_notice_id}:{abs(hash(self.text)) % 10**8:08d}"   # 표시용(프로세스마다 달라질 수 있음)

    @property
    def stable_key(self) -> str:
        import hashlib
        return f"{self.doc_notice_id}:{hashlib.sha1(self.text.encode()).hexdigest()[:8]}"


def pack_chunks(sections: list[Section], max_chars: int = 3500) -> list[Chunk]:
    """같은 패치노트의 연속 섹션을 max_chars 이하로 묶는다 (호출 수 절감 + 문맥 유지)."""
    out: list[Chunk] = []
    cur: Chunk | None = None
    for s in sections:
        if cur and cur.doc_notice_id == s.doc_notice_id and len(cur.text) + len(s.text) + 1 <= max_chars:
            cur.text += "\n" + s.text
            if s.title not in cur.titles:
                cur.titles.append(s.title)
        else:
            prev = cur.titles[-3:] if cur and cur.doc_notice_id == s.doc_notice_id else []
            if cur:
                out.append(cur)
            cur = Chunk(s.doc_notice_id, s.patch_title, s.patch_date, [s.title], s.text, prev_titles=prev)
    if cur:
        out.append(cur)
    return out
