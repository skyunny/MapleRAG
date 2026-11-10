"""RAG 서비스 진입점: 트레이싱 config 를 붙여 실행하고 trace_id 를 돌려준다. 세션 단위 대화·익명 user_id·태그를 지원한다."""
from __future__ import annotations

from maplerag.rag import tracing
from maplerag.rag.pipeline import build_graph


def ask_traced(question: str, session_id: str | None = None, tags: list[str] | None = None, stream: bool = False):
    """(최종 상태, trace_id, session_id). stream=True 면 (노드, 변경분) 제너레이터를 대신 쓰는 ask_stream 을 사용."""
    cfg, handler = tracing.make_config(session_id=session_id, tags=tags)
    state = build_graph().invoke({"question": question}, config=cfg)
    tracing.flush()
    return state, handler.last_trace_id, cfg["metadata"]["langfuse_session_id"]


def ask_stream(question: str, session_id: str | None = None, tags: list[str] | None = None):
    """노드가 끝날 때마다 (노드명, 변경분) 을 내보낸다. 끝나면 flush. 마지막에 ('__trace__', {'trace_id':..., 'session_id':...})."""
    cfg, handler = tracing.make_config(session_id=session_id, tags=tags)
    for chunk in build_graph().stream({"question": question}, config=cfg, stream_mode="updates"):
        for node, upd in chunk.items():
            yield node, upd
    tracing.flush()
    yield "__trace__", {"trace_id": handler.last_trace_id, "session_id": cfg["metadata"]["langfuse_session_id"]}
