"""Langfuse Prompt Management 연동: 프롬프트 3종을 등록(멱등)하고 label=production 으로 조회한다.

- 코드의 prompts.py 가 '기본값(fallback)' 이자 등록 원본이다. Langfuse 에 접속하지 못해도 파이프라인은 기본 프롬프트로 동작한다.
- 내용이 바뀌면 새 버전이 만들어지고, 트레이스의 generation 은 사용한 프롬프트 버전에 연결된다(metadata['langfuse_prompt']).
"""
from __future__ import annotations

from dataclasses import dataclass

from langfuse import Langfuse

import maplerag.config  # noqa: F401  (.env 를 먼저 읽어 LANGFUSE_* 환경변수를 준비)
from maplerag.rag import prompts as P

PROMPTS = {
    "maple-analyze-question": (P.ANALYZE_TEMPLATE, "질문 분류: 범위 판단 + 개체 이름 추출"),
    "maple-text2cypher": (P.CYPHER_TEMPLATE, "Text2Cypher: 스키마·후보·재시도 사유 → 읽기 전용 Cypher"),
    "maple-answer": (P.ANSWER_TEMPLATE, "답변 생성: 조회 행만 근거로 인용 번호와 함께 답변"),
}
_client: Langfuse | None = None


def client() -> Langfuse:
    from maplerag.rag.tracing import get_langfuse      # 공용 클라이언트(중복 span 필터 적용)
    return get_langfuse()


def register_all(commit_message: str = "") -> dict:
    """없으면 만들고, 내용이 달라졌으면 새 버전을 만든다. {이름: (버전, 신규 여부)} 반환."""
    out = {}
    for name, (template, desc) in PROMPTS.items():
        try:
            cur = client().get_prompt(name, label="production", type="text", cache_ttl_seconds=0, max_retries=1)
            if cur.prompt == template:
                out[name] = (cur.version, False)
                continue
        except Exception:
            pass
        p = client().create_prompt(name=name, prompt=template, labels=["production"], type="text",
                                   tags=["rag", "maple"], commit_message=commit_message or desc)
        out[name] = (p.version, True)
    return out


@dataclass
class Resolved:
    """컴파일된 프롬프트 + (있으면) Langfuse 프롬프트 객체. 객체가 있어야 generation 에 버전이 연결된다."""
    text: str
    lf_prompt: object | None
    version: int | None


def resolve(name: str, **variables) -> Resolved:
    template = PROMPTS[name][0]
    try:
        p = client().get_prompt(name, label="production", type="text", fallback=template, max_retries=1)
        return Resolved(p.compile(**variables), None if p.is_fallback else p, None if p.is_fallback else p.version)
    except Exception:
        text = template
        for k, v in variables.items():
            text = text.replace("{{" + k + "}}", str(v))
        return Resolved(text, None, None)
