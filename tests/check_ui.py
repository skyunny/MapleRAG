"""챗 UI 테스트 (Streamlit AppTest, 모델·Open API 호출 없음: 서비스 함수를 가짜로 대체).
실행: uv run python tests/check_ui.py"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
APP = str(ROOT / "src" / "maplerag" / "ui" / "app.py")

from streamlit.testing.v1 import AppTest  # noqa: E402

from maplerag.character import lookup as character  # noqa: E402
from maplerag.rag import service  # noqa: E402
from maplerag.ui import view  # noqa: E402

ok = True
def check(n, c):
    global ok; ok &= bool(c); print("OK  " if c else "FAIL", n)

CALLS = []
EVID = [{"row": 1, "subject": "소울 어썰트", "predicate": "Nerf", "attribute": "데미지", "before": "145%", "object": "65%",
         "patch_date": "2026-09-17", "evidence": "소울 어썰트의 데미지가 145%에서 65%로 감소됩니다.", "source_url": "https://open.api.nexon.com/x"}]
HIST = [{"cypher": "MATCH (n) RETURN n LIMIT 50", "error": "", "n_rows": 1}]


def fake_stream(question, session_id=None, tags=None):
    CALLS.append((question, session_id, tags))
    if "날씨" in question:
        yield "analyze_question", {"analysis": {"in_scope": False}}
        yield "refuse", {"status": "refused", "answer": "게임 정보 질문에만 답할 수 있어요.", "evidence": [], "sources": [], "rows": []}
    elif "오류" in question:
        raise RuntimeError("Your credit balance is too low")
    else:
        yield "analyze_question", {"analysis": {"in_scope": True}}
        yield "generate_cypher", {"cypher": HIST[0]["cypher"]}
        yield "execute_query", {"history": HIST, "rows": [{}]}
        yield "generate_answer", {"status": "answered", "answer": "소울 어썰트는 145%에서 65%로 너프됐습니다 [1].",
                                  "evidence": EVID, "sources": ["https://open.api.nexon.com/x"]}
    yield "__trace__", {"trace_id": None, "session_id": session_id}


class FakeAns:
    def __init__(self, answer, live_calls=0, rows=(), sources=(), triples=581):
        self.answer, self.live_calls, self.rows, self.sources, self.triples = answer, live_calls, list(rows), list(sources), triples
        self.attribution = view.ATTRIBUTION


CHAR_CALLS = []
def fake_char(nick, q):
    CHAR_CALLS.append((nick, q)); return FakeAns(f"{nick}: Lv.295 비숍", sources=["https://open.api.nexon.com/maplestory/v1/character/basic?ocid=<ocid>"])


service.ask_stream = fake_stream
character.ask_character = fake_char


def texts(at):
    return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption])


# --- 1. 첫 화면
os.environ["UI_MAX_QUESTIONS"] = "2"
at = AppTest.from_file(APP, default_timeout=30).run()
check("첫 화면: 예외 없음", not at.exception)
labels = [b.label for b in at.button]
check("예시 질문 버튼 4개 + 대화 지우기", all(e in labels for e in view.EXAMPLES) and "대화 지우기" in labels)
check("첨부 문구(Data based on NEXON Open API) 표시", view.ATTRIBUTION in texts(at))

# --- 2. 예시 버튼 → 답변·근거·Cypher
at.button[[b.label for b in at.button].index(view.EXAMPLES[0])].click().run()
check("예시 클릭: 예외 없음, 서비스 1회 호출", not at.exception and len(CALLS) == 1)
check("세션 ID·태그 전달", CALLS[0][1] and CALLS[0][2] == ["ui"])
check("답변 본문 표시", "145%에서 65%로 너프" in texts(at))
exp = [e.label for e in at.expander]
check(f"근거 트리플·Cypher 펼침 영역 {exp}", any("근거 트리플 (1)" in e for e in exp) and any("실행된 Cypher" in e for e in exp))
check("상태 배지(답변 완료)", "답변 완료" in texts(at))
check("남은 질문 수 갱신(1/2 사용)", at.session_state["n_questions"] == 1)

# --- 3. 직접 입력 + 범위 밖
at.chat_input[0].set_value("오늘 날씨 어때?").run()
check("범위 밖 질문: 거절 배지", "범위 밖 질문" in texts(at) and not at.exception)

# --- 4. 한도 도달
check("질문 한도(2) 도달 시 경고", any("한도" in w.value for w in at.warning))
check("한도 후에는 입력창이 비활성화(서비스 호출 불가)", at.chat_input[0].disabled)
at.sidebar.button[0].click().run()
check("대화 지우기: 초기화", at.session_state["n_questions"] == 0 and not at.session_state["messages"])

# --- 5. 서비스 예외 → 화면에 오류 표시(앱은 죽지 않음)
at.chat_input[0].set_value("오류 내줘").run()
check("서비스 예외를 사용자 메시지로 변환", not at.exception and "처리하지 못했습니다" in texts(at))
check("오류 문구에 키·URL 이 노출되지 않음", "sk-" not in texts(at))

# --- 6. 캐릭터 조회 모드
at.sidebar.radio[0].set_value("캐릭터 조회").run()
check("캐릭터 모드 전환", not at.exception and any(t.label.startswith("캐릭터 닉네임") for t in at.text_input))
at.text_input[0].set_value("잘못된 닉네임!!"); at.button[0].click().run()
check("잘못된 닉네임은 API 호출 없이 거절", any("1~12자" in e.value for e in at.error) and not CHAR_CALLS)
at.text_input[0].set_value("꽃게쥬"); at.button[0].click().run()   # 폼 안의 입력은 제출과 같은 run 에서 반영된다
check("정상 닉네임 → 조회 결과 표시", CHAR_CALLS == [("꽃게쥬", "기본 정보 알려줘")] and any("Lv.295" in s.value for s in at.success))

# --- 7. view 헬퍼
check("닉네임 검증", view.is_valid_nickname("꽃게쥬") and view.is_valid_nickname("abc12") and not view.is_valid_nickname("a b") and not view.is_valid_nickname("가" * 13) and not view.is_valid_nickname(""))
check("근거 표 변환(전→후)", view.evidence_rows(EVID)[0]["변경"] == "145% → 65%")
check("캐릭터 표 변환(기본·쌍·문자열)", view.character_table([("꽃게쥬", "295", "크로아", "비숍", None)])[0] == {"닉네임": "꽃게쥬", "레벨": "295", "월드": "크로아", "직업": "비숍", "길드": "-"}
      and view.character_table([("모자", 22)])[0] == {"장비": "모자", "값": 22} and view.character_table(["모자"])[0] == {"장비": "모자"})
check("비용 추정", view.estimated_cost(15) == 0.3)
print("결과:", "OK" if ok else "FAIL"); sys.exit(0 if ok else 1)
