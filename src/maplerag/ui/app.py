"""MapleRAG 챗 UI (Streamlit).  실행: uv run maplerag-ui   (= streamlit run src/maplerag/ui/app.py)

두 가지 모드
- 게임 정보 질문: LangGraph RAG (질문 1개당 모델 호출 약 3회, ≈ $0.02 추정). 근거 트리플·출처·실행된 Cypher 를 함께 보여 준다.
- 캐릭터 조회: 닉네임 → Open API → 메모리 임시 트리플 → 질의 (LLM 없음, 영구 저장 없음)
"""
import os

import streamlit as st

from maplerag.character import lookup as character
from maplerag.rag import service
from maplerag.ui import view

MAX_QUESTIONS = int(os.getenv("UI_MAX_QUESTIONS", "15"))     # 한 브라우저 세션당 모델 호출 비용 상한(질문 수)

st.set_page_config(page_title="메이플 정보 챗봇", page_icon="🍁", layout="centered")

# ---------------------------------------------------------------- 세션 상태
ss = st.session_state
ss.setdefault("messages", [])          # {"role", "content", "meta"}
ss.setdefault("n_questions", 0)
ss.setdefault("pending", None)         # 예시 버튼으로 들어온 질문
if "session_id" not in ss:
    from maplerag.rag.tracing import new_session_id
    ss.session_id = new_session_id()


# ---------------------------------------------------------------- 렌더링
def render_meta(meta: dict) -> None:
    """답변 아래의 근거·출처·Cypher·트레이스 표시."""
    label, color = view.STATUS_BADGE.get(meta.get("status"), (meta.get("status") or "", "gray"))
    st.badge(label, color=color)
    rows = view.evidence_rows(meta.get("evidence"))
    if rows:
        with st.expander(f"근거 트리플 ({len(rows)})", expanded=False):
            st.dataframe(rows, hide_index=True, width="stretch")
    for u in meta.get("sources") or []:
        st.caption(f"출처: {u}")
    attempts = view.cypher_attempts(meta.get("history"))
    if attempts:
        with st.expander(f"실행된 Cypher ({len(attempts)}회 시도)", expanded=False):
            for i, (cy, err, n) in enumerate(attempts, 1):
                st.markdown(f"**시도 {i}** — " + (f":red[오류] {err}" if err else f"결과 {n}행"))
                st.code(cy, language="cypher")
    if meta.get("trace_url"):
        st.link_button("Langfuse 트레이스 보기", meta["trace_url"])


def run_question(question: str) -> dict:
    """RAG 를 노드 단위로 스트리밍하며 진행 상황을 보여 주고, 최종 상태를 반환한다."""
    final, trace = {}, {}
    with st.status("질문 분석 중…", expanded=True) as status:
        try:
            for node, upd in service.ask_stream(question, session_id=ss.session_id, tags=["ui"]):
                if node == "__trace__":
                    trace = upd
                    continue
                final.update(upd)
                status.update(label=f"{view.NODE_LABELS.get(node, node)} 완료")
                st.write(f"✔ {view.NODE_LABELS.get(node, node)}")
        except Exception as e:                                   # 크레딧 부족·네트워크 등. 키 값이 노출되지 않게 길이를 제한
            status.update(label="실패", state="error")
            return {"status": "query_failed", "answer": f"요청을 처리하지 못했습니다: {type(e).__name__}: {str(e)[:160]}",
                    "evidence": [], "sources": [], "history": final.get("history", [])}
        status.update(label="완료", state="complete", expanded=False)
    meta = {k: final.get(k) for k in ("status", "answer", "evidence", "sources", "history")}
    if trace.get("trace_id"):
        try:
            from langfuse import get_client
            meta["trace_url"] = get_client().get_trace_url(trace_id=trace["trace_id"])
        except Exception:
            pass
    return meta


# ---------------------------------------------------------------- 사이드바
with st.sidebar:
    st.title("🍁 메이플 정보 챗봇")
    mode = st.radio("모드", ["게임 정보 질문", "캐릭터 조회"], label_visibility="collapsed")
    st.divider()
    if mode == "게임 정보 질문":
        st.caption("공식 Open API 의 **최신 패치노트 20건**(2026-01~10)과 이벤트 공지에서 만든 지식그래프로 답합니다.")
        st.caption("확률표·도감·드롭 테이블은 거의 없어 이런 질문은 '근거 없음'으로 답합니다.")
        left = MAX_QUESTIONS - ss.n_questions
        st.metric("남은 질문", f"{max(left, 0)} / {MAX_QUESTIONS}")
        st.caption(f"질문당 모델 호출 약 3회 (≈ ${view.COST_PER_QUESTION}, 추정). 이번 세션 누적 ≈ ${view.estimated_cost(ss.n_questions)}")
        if st.button("대화 지우기"):
            ss.messages, ss.n_questions = [], 0
            st.rerun()
    else:
        st.caption("닉네임 → Open API → **메모리 임시 트리플** → 질의. LLM 을 쓰지 않고 **저장하지 않습니다**.")
        st.caption("개발용 키 한도: 초당 5회·일 1,000회. 데이터는 약 15분 지연될 수 있습니다.")
    st.divider()
    st.caption(view.ATTRIBUTION)

# ---------------------------------------------------------------- 게임 정보 질문 모드
if mode == "게임 정보 질문":
    st.subheader("게임 정보 질문")
    for m in ss.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            if m.get("meta"):
                render_meta(m["meta"])
    if not ss.messages:
        st.caption("예시를 눌러 보거나 아래에 직접 질문해 보세요.")
        cols = st.columns(2)
        for i, ex in enumerate(view.EXAMPLES):
            if cols[i % 2].button(ex, key=f"ex{i}", width="stretch"):
                ss.pending = ex
                st.rerun()

    question = st.chat_input("패치·스킬·이벤트에 대해 물어보세요", disabled=ss.n_questions >= MAX_QUESTIONS) or ss.pending
    ss.pending = None
    if ss.n_questions >= MAX_QUESTIONS:
        st.warning(f"이 세션의 질문 한도({MAX_QUESTIONS}개)에 도달했습니다. 사이드바의 '대화 지우기'로 초기화할 수 있습니다.")
    elif question:
        question = question.strip()[:300]                         # 과도하게 긴 입력은 잘라서 비용·프롬프트 오염을 줄인다
        ss.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        ss.n_questions += 1
        with st.chat_message("assistant"):
            meta = run_question(question)
            st.markdown(meta["answer"])
            render_meta(meta)
        ss.messages.append({"role": "assistant", "content": meta["answer"], "meta": meta})
        st.rerun()                                                # 사이드바의 남은 질문 수 등을 갱신

# ---------------------------------------------------------------- 캐릭터 조회 모드
else:
    st.subheader("캐릭터 조회")
    with st.form("char"):
        nick = st.text_input("캐릭터 닉네임", max_chars=12, placeholder="예: 꽃게쥬")
        kind = st.selectbox("무엇을 볼까요?", list(view.CHARACTER_QUESTIONS))
        go = st.form_submit_button("조회")
    if go:
        if not view.is_valid_nickname(nick):
            st.error("닉네임은 한글·영문·숫자 1~12자여야 합니다.")
        else:
            with st.spinner("조회 중…"):
                r = character.ask_character(nick.strip(), view.CHARACTER_QUESTIONS[kind])
            st.success(r.answer)
            st.caption(f"임시 트리플 {r.triples}개 · 이번 실제 API 호출 {r.live_calls}회 · {r.attribution}")
            if r.rows:
                st.dataframe(view.character_table(r.rows[:30]), hide_index=True, width="stretch")
            with st.expander("사용한 출처"):
                for u in r.sources:
                    st.caption(u)
            st.caption("조회 결과는 저장하지 않습니다. (단, Open API 원본 응답은 서버의 디스크 캐시에 남을 수 있습니다.)")
