"""비정형 공지 본문 → 트리플 후보 (Claude 구조화 출력) + 코드 검증.

핵심 습관(노트북에서 이어받음): 모델이 만든 값을 믿지 않고, 단계마다 건수와 폐기 사유를 센다.
v2 보완: 직전 소제목 문맥, 출력 잘림 시 청크 반분 재시도, 청크 단위 결과 캐시(재실행 시 호출 0).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from langchain_anthropic import ChatAnthropic
from langfuse import Langfuse

from maplerag.config import settings
from maplerag.extract.schemas import ChunkExtraction
from maplerag.extract.sections import Chunk
from maplerag.ingest.base import RAW_DIR

PROMPT_NAME = "maple-extract-changes"
MAX_TOKENS = 12000
MAX_SPLIT_DEPTH = 2

# Langfuse Prompt Management 에 등록되는 원문. {{변수}} 는 Langfuse 문법.
PROMPT_TEMPLATE = """당신은 메이플스토리 공식 공지 본문에서 '게임 정보 변경 사항'을 뽑는 정보 추출기입니다.
아래 [본문]에 **명시된 사실만** 추출하세요. 추측·보충·상식 사용은 금지입니다.

[허용 클래스] Job, Skill, Equipment, Consumable, Item, Monster, Boss, Map, Quest, NPC, Event
[변경 종류 change_type]
- Buff: 유저에게 유리한 수치·효과 변화(데미지 증가, 쿨타임 감소, 보상 증가 등)
- Nerf: 유저에게 불리한 변화
- Added: 새로 추가됨 / Removed: 삭제·종료됨
- Adjusted: 변경됐지만 유리/불리 방향을 본문만으로 판단할 수 없음
[관계 predicate] hasSkill(직업→스킬), dropsItem(몬스터→아이템), appearsIn(몬스터→맵),
  rewards(퀘스트·이벤트→아이템), usableBy(장비→직업), givenBy(퀘스트→NPC)

규칙
1. 오류(버그) 수정, 안내 문구 수정, 점검·접속 안내, 이벤트 참여 방법 설명은 changes 에 넣지 않습니다.
2. 이름은 본문에 나온 표기 그대로 씁니다. 줄이거나 바꾸지 않습니다.
3. before_value/after_value 는 본문에 숫자·값이 있을 때만 채우고, 없으면 null 입니다.
4. evidence 는 본문의 문장을 **한 글자도 바꾸지 않고 그대로 복사**합니다(문장 일부는 가능, 요약은 불가).
5. 위 클래스·관계로 표현할 수 없는 내용은 버립니다. 확신이 없으면 넣지 않습니다.
6. 추출할 것이 없으면 changes 와 relations 를 빈 배열로 반환합니다.
7. 스킬의 related_job 은 [본문]의 소제목 줄(■)이나 [직전 소제목]에 직업명이 있을 때만 그 직업명을 쓰고, 없으면 null 입니다.

[패치노트] {{patch_title}} ({{patch_date}})
[직전 소제목(문맥용, 추출 대상 아님)] {{prev_titles}}
[소제목] {{section_titles}}
[본문]
{{text}}
"""


def register_prompt(client: Langfuse | None = None) -> dict:
    """프롬프트를 Langfuse 에 등록한다. 내용이 같으면 새 버전을 만들지 않는다(멱등)."""
    lf = client or Langfuse()
    try:
        cur = lf.get_prompt(PROMPT_NAME, label="production", type="text", cache_ttl_seconds=0, max_retries=1)
        if cur.prompt == PROMPT_TEMPLATE:
            return {"name": PROMPT_NAME, "version": cur.version, "created": False}
    except Exception:
        pass   # 아직 없음
    p = lf.create_prompt(name=PROMPT_NAME, prompt=PROMPT_TEMPLATE, labels=["production"], type="text",
                         tags=["extract", "patch-note"],
                         commit_message="v2: 직전 소제목 문맥 + related_job 규칙 추가")
    return {"name": PROMPT_NAME, "version": p.version, "created": True}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


@dataclass
class Dropped:
    kind: str
    reason: str
    item: dict


@dataclass
class ChunkResult:
    chunk: Chunk
    kept_changes: list = field(default_factory=list)
    kept_relations: list = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    error: str = ""
    calls: int = 0
    cached: bool = False
    splits: int = 0


def verify(chunk: Chunk, ex: ChunkExtraction) -> tuple[list, list, list[Dropped]]:
    """모델 출력 검증: evidence 가 본문에 실제로 있는가, 값이 evidence 에 있는가. 실패는 버린다."""
    body, dropped, kc, kr = _norm(chunk.text), [], [], []
    for c in ex.changes:
        d = c.model_dump()
        if _norm(c.evidence) not in body:
            dropped.append(Dropped("change", "evidence 가 본문에 없음(환각 의심)", d)); continue
        ev = _norm(c.evidence)
        miss = [v for v in (c.before_value, c.after_value) if v and _norm(v) not in ev]
        if miss:
            dropped.append(Dropped("change", f"값 {miss} 이(가) evidence 에 없음", d)); continue
        if _norm(c.target_name) not in body:
            dropped.append(Dropped("change", "target_name 이 본문에 없음", d)); continue
        kc.append(c)
    for r in ex.relations:
        d = r.model_dump()
        if _norm(r.evidence) not in body:
            dropped.append(Dropped("relation", "evidence 가 본문에 없음(환각 의심)", d)); continue
        if _norm(r.subject_name) not in body or _norm(r.object_name) not in body:
            dropped.append(Dropped("relation", "주어/목적어 이름이 본문에 없음", d)); continue
        kr.append(r)
    return kc, kr, dropped


def make_llm() -> ChatAnthropic:
    # 샘플링 파라미터(temperature 등)는 지정하지 않는다. max_tokens 는 출력 상한일 뿐이다.
    return ChatAnthropic(model=settings.llm_model, max_tokens=MAX_TOKENS)


def _build_prompt(template: str, chunk: Chunk, text: str) -> str:
    return (template.replace("{{patch_title}}", chunk.patch_title).replace("{{patch_date}}", chunk.patch_date)
            .replace("{{prev_titles}}", ", ".join(chunk.prev_titles) or "(없음)")
            .replace("{{section_titles}}", ", ".join(chunk.titles[:12])).replace("{{text}}", text))


def _call(prompt: str, llm: ChatAnthropic):
    """(parsed|None, in_tok, out_tok, stop_reason, error)"""
    # 강제 tool_choice 는 이 모델에서 400 이라 function_calling 대신 json_schema(구조화 출력)를 쓴다.
    chain = llm.with_structured_output(ChunkExtraction, method="json_schema", include_raw=True)
    try:
        out = chain.invoke(prompt)
    except Exception as e:
        return None, 0, 0, None, f"{type(e).__name__}: {e}"[:300]
    raw = out["raw"]
    um = getattr(raw, "usage_metadata", None) or {}
    stop = (getattr(raw, "response_metadata", {}) or {}).get("stop_reason")
    if stop == "refusal":
        return None, um.get("input_tokens", 0), um.get("output_tokens", 0), stop, "refusal (안전 분류기 거절)"
    if out.get("parsing_error") or out.get("parsed") is None:
        return None, um.get("input_tokens", 0), um.get("output_tokens", 0), stop, f"파싱 실패 (stop_reason={stop})"
    return out["parsed"], um.get("input_tokens", 0), um.get("output_tokens", 0), stop, ""


def _split_text(text: str) -> tuple[str, str]:
    """줄 경계에서 대략 반으로 나눈다."""
    lines = text.split("\n")
    acc, mid = 0, len(text) // 2
    for i, ln in enumerate(lines):
        acc += len(ln) + 1
        if acc >= mid:
            return "\n".join(lines[: i + 1]), "\n".join(lines[i + 1:])
    return text, ""


def _extract_text(chunk: Chunk, text: str, template: str, llm, res: ChunkResult, depth: int) -> ChunkExtraction | None:
    parsed, tin, tout, stop, err = _call(_build_prompt(template, chunk, text), llm)
    res.calls += 1; res.input_tokens += tin; res.output_tokens += tout
    if parsed is not None:
        return parsed
    if stop == "max_tokens" and depth < MAX_SPLIT_DEPTH:      # 출력이 잘림 → 반으로 나눠 재시도
        a, b = _split_text(text)
        if a and b:
            res.splits += 1
            pa = _extract_text(chunk, a, template, llm, res, depth + 1)
            pb = _extract_text(chunk, b, template, llm, res, depth + 1)
            if pa is None or pb is None:
                return None
            return ChunkExtraction(changes=pa.changes + pb.changes, relations=pa.relations + pb.relations)
    res.error = err
    return None


def extract_chunk(chunk: Chunk, template: str, llm: ChatAnthropic, cache_dir: Path | None = None) -> ChunkResult:
    """청크 1개 추출. cache_dir 이 있으면 모델 원출력(검증 전)을 저장해 재실행 시 호출하지 않는다."""
    res = ChunkResult(chunk=chunk)
    cf = cache_dir / (chunk.stable_key.replace(":", "_") + ".json") if cache_dir else None
    parsed = None
    t0 = time.time()
    if cf and cf.exists():
        c = json.loads(cf.read_text(encoding="utf-8"))
        parsed, res.cached = ChunkExtraction.model_validate(c["parsed"]), True
        res.input_tokens, res.output_tokens, res.splits = c["in"], c["out"], c.get("splits", 0)
    else:
        parsed = _extract_text(chunk, chunk.text, template, llm, res, 0)
        if parsed is not None and cf:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cf.write_text(json.dumps({"parsed": parsed.model_dump(), "in": res.input_tokens, "out": res.output_tokens,
                                      "splits": res.splits}, ensure_ascii=False), encoding="utf-8")
    res.seconds = time.time() - t0
    if parsed is not None:
        res.kept_changes, res.kept_relations, res.dropped = verify(chunk, parsed)
    return res
