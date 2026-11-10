"""챗 UI 의 표시 로직(Streamlit 비의존 → 단위 테스트 가능)."""
from __future__ import annotations

import re

ATTRIBUTION = "Data based on NEXON Open API"
COST_PER_QUESTION = 0.02          # 8단계 실측(질문 1개 ≈ 모델 호출 3회)을 반올림한 추정치(USD, 단가표 기반)

NODE_LABELS = {
    "analyze_question": "질문 분석",
    "refuse": "범위 확인",
    "link_entities": "개체 연결",
    "generate_cypher": "Cypher 생성",
    "execute_query": "그래프 조회",
    "generate_answer": "답변 작성",
}
STATUS_BADGE = {
    "answered": ("답변 완료", "green"),
    "no_evidence": ("근거 없음", "orange"),
    "refused": ("범위 밖 질문", "gray"),
    "query_failed": ("조회 실패", "red"),
}
# 정답이 그래프에 있음을 확인한 질문(eval/questions.jsonl) 중에서 고른 예시
EXAMPLES = [
    "소울 어썰트 데미지 변화는?",
    "가장 최근 패치는 몇 버전이고 언제 적용됐어?",
    "유피테르는 어느 패치에서 추가된 보스야?",
    "몬스터파크 핸즈 이벤트 보상에는 뭐가 있어?",
]
CHARACTER_QUESTIONS = {"기본 정보": "기본 정보 알려줘", "스타포스 높은 장비": "스타포스 높은 장비는?",
                       "잠재능력 등급": "잠재능력 등급 분포는?", "착용 장비 목록": "착용 장비 알려줘"}
_NICK = re.compile(r"^[0-9A-Za-z가-힣]{1,12}$")


def is_valid_nickname(s: str) -> bool:
    """넥슨 닉네임 규칙(최대 12자, 한글·영문·숫자)에 맞는지만 확인한다. 조회 전에 이상한 입력을 API 에 보내지 않기 위함."""
    return bool(_NICK.match((s or "").strip()))


def evidence_rows(evidence: list[dict]) -> list[dict]:
    """근거 트리플 → 표 행. 인용 번호([1] 등)와 맞춰 보도록 '번호' 컬럼을 둔다."""
    rows = []
    for e in evidence or []:
        change = f"{e['before']} → {e['object']}" if e.get("before") and e.get("object") else (e.get("object") or "")
        rows.append({"번호": e.get("row"), "대상": e.get("subject"), "종류": e.get("predicate"), "속성": e.get("attribute"),
                     "변경": change, "패치일": e.get("patch_date"), "원문 근거": e.get("evidence")})
    return rows


def cypher_attempts(history: list[dict]) -> list[tuple[str, str, int]]:
    """시도별 (Cypher, 오류 메시지, 결과 행 수)."""
    return [(h.get("cypher", ""), h.get("error", ""), h.get("n_rows", 0)) for h in history or []]


def estimated_cost(n_questions: int) -> float:
    return round(n_questions * COST_PER_QUESTION, 2)


def character_table(rows: list) -> list[dict]:
    """캐릭터 조회 결과 행 → 표. 행 모양: 기본 정보(5개 값) / (장비, 값) 쌍 / 장비 이름 문자열."""
    out = []
    for r in rows or []:
        if isinstance(r, (tuple, list)) and len(r) == 5:
            out.append(dict(zip(["닉네임", "레벨", "월드", "직업", "길드"], [x if x is not None else "-" for x in r])))
        elif isinstance(r, (tuple, list)) and len(r) == 2:
            out.append({"장비": r[0], "값": r[1]})
        else:
            out.append({"장비": str(r)})
    return out
