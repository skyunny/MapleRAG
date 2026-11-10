"""수집 공통 계층: RawDocument, 수집기 인터페이스, 디스크 캐시, 요청 간 지연.

설계 이유
- 모든 소스를 RawDocument 하나로 정규화해야 뒤 단계(추출·적재)가 소스를 몰라도 된다.
- 원본 응답을 data/raw/ 에 캐시해 재실행 시 재요청을 막는다 (호출 제한·예의).
- API 키는 캐시 키·로그·저장 파일 어디에도 들어가지 않는다.
- 실패는 예외로 중단하지 않고 failures 에 모아 마지막에 보고한다 (노트북의 '실패 목록' 습관).
"""
from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator, Literal

SourceTier = Literal["official", "user-curated", "community"]

ROOT = Path(__file__).resolve().parents[3]
RAW_DIR = ROOT / "data" / "raw"
USER_AGENT = "MapleRAG-study/0.1 (educational project; contact: project owner)"


@dataclass
class RawDocument:
    """수집한 원본 1건. 트리플 추출의 입력 단위."""
    source: str          # 수집기 이름 예) nexon_api
    url: str             # URL 또는 API 엔드포인트(키 제외, 파라미터 포함)
    fetched_at: str      # ISO8601 (KST)
    text: str            # 본문 텍스트 (구조화 데이터는 JSON 문자열)
    tier: SourceTier
    title: str = ""
    doc_type: str = ""   # patch_note / notice / event / character_basic ...
    meta: dict = field(default_factory=dict)   # notice_id, date 등

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass
class Failure:
    target: str
    reason: str


class DiskCache:
    """URL+파라미터(키 제외)로 키를 만드는 JSON 파일 캐시."""

    def __init__(self, namespace: str, root: Path = RAW_DIR):
        self.dir = root / namespace
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.dir / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:24] + ".json")

    def get(self, key: str):
        p = self._path(key)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def put(self, key: str, payload: dict) -> None:
        self._path(key).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class Throttle:
    """호출 간 최소 간격 보장 (개발 단계 키 한도: 초당 5건 → 기본 0.5초로 여유)."""

    def __init__(self, min_interval: float):
        self.min_interval, self._last = min_interval, 0.0

    def wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()


class Collector(ABC):
    """소스별 수집기 인터페이스. 비공식 소스(data/raw/unofficial)도 같은 모양으로 붙인다."""

    name: str
    tier: SourceTier

    def __init__(self) -> None:
        self.failures: list[Failure] = []

    @abstractmethod
    def collect(self) -> Iterator[RawDocument]:
        ...

    def report(self, n_docs: int) -> str:
        lines = [f"[{self.name}] 수집 {n_docs}건, 실패 {len(self.failures)}건"]
        lines += [f"  - {f.target}: {f.reason[:100]}" for f in self.failures[:10]]
        return "\n".join(lines)
