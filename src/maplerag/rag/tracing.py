"""Langfuse 트레이싱 설정: LangGraph 호출 config 를 만든다. 노드(분석/연결/생성/실행/답변)마다 span, LLM 호출은 generation 으로 남는다.

- session_id : 같은 대화(세션)의 질문들을 묶는다.   - user_id : 익명(무작위) — 닉네임·계정 정보를 넣지 않는다.
- tags       : 환경·모델·질문 유형 검색용.            - 메타데이터 키는 langfuse_* (CallbackHandler 소스에서 확인).
"""
from __future__ import annotations

import uuid

from langfuse import Langfuse
from langfuse._client.span_filter import is_langfuse_span
from langfuse.langchain import CallbackHandler

import maplerag.config  # noqa: F401
from maplerag.config import settings


_lf: Langfuse | None = None


def get_langfuse() -> Langfuse:
    """프로세스 공용 Langfuse 클라이언트. Anthropic SDK 가 자체 OTel span(anthropic.messages.create)을 내보내면
    CallbackHandler 의 generation 과 같은 호출이 두 번 기록돼 비용·토큰이 2배로 보이므로, Langfuse SDK 의 span 만 내보낸다."""
    global _lf
    if _lf is None:
        _lf = Langfuse(should_export_span=is_langfuse_span)
    return _lf


def new_session_id() -> str:
    return "sess-" + uuid.uuid4().hex[:12]


def make_config(session_id: str | None = None, user_id: str | None = None, tags: list[str] | None = None,
                trace_name: str = "maple-rag") -> tuple[dict, CallbackHandler]:
    get_langfuse()
    handler = CallbackHandler()
    cfg = {
        "callbacks": [handler],
        "run_name": trace_name,
        "metadata": {
            "langfuse_session_id": session_id or new_session_id(),
            "langfuse_user_id": user_id or "anon-" + uuid.uuid4().hex[:8],
            "langfuse_tags": ["maplerag", f"model:{settings.llm_model}", *(tags or [])],
            "langfuse_trace_name": trace_name,
        },
    }
    return cfg, handler


def flush() -> None:
    from langfuse import get_client
    get_client().flush()
