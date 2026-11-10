"""Cypher 안전장치: LLM 이 만든 쿼리를 실행하기 전에 읽기 전용·단일문·LIMIT 을 코드로 강제한다.
프롬프트로 '쓰기 금지'를 부탁하는 것만으로는 부족하므로(프롬프트 인젝션·실수), 실행 전 차단 + 읽기 전용 세션을 이중으로 건다.
"""
from __future__ import annotations

import re

DEFAULT_LIMIT = 50
MAX_LIMIT = 100
TIMEOUT_SEC = 10

# CALL 은 전부 금지(apoc/n10s/db.* 우회 차단). SET/REMOVE 등 쓰기 키워드도 금지.
FORBIDDEN = ["CREATE", "MERGE", "DELETE", "DETACH", "SET", "REMOVE", "DROP", "LOAD", "FOREACH", "CALL",
             "APOC", "N10S", "DBMS", "GRANT", "REVOKE", "DENY", "ALTER", "START", "STOP", "TERMINATE", "USE"]
_STR = re.compile(r"""'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*\"""")
_BACKTICK = re.compile(r"`[^`]*`")
# 문자열 | 백틱 | 주석 을 한 번에 훑어, 주석만 지운다(문자열 안의 '//' 는 보존)
_TOKEN = re.compile(_STR.pattern + "|" + _BACKTICK.pattern + r"|(?P<c>//[^\n]*|/\*.*?\*/)", re.S)
_LIMIT_END = re.compile(r"\bLIMIT\s+(\d+)\s*$", re.I)


class CypherRejected(ValueError):
    pass


def _strip_comments(q: str) -> str:
    """주석만 제거. 문자열 안의 '//'(예: URL)는 건드리지 않는다."""
    return _TOKEN.sub(lambda m: " " if m.group("c") else m.group(0), q)


def _skeleton(q: str) -> str:
    """문자열·주석을 비워 키워드 검사에서 값 안의 단어(예: '세트 효과 SET')가 오탐되지 않게 한다."""
    return _STR.sub("''", _BACKTICK.sub("``", _strip_comments(q)))


def guard(cypher: str) -> str:
    """검사를 통과한 쿼리를 반환(LIMIT 보정 포함). 위반 시 CypherRejected."""
    q = _strip_comments(cypher or "").strip().rstrip(";").strip()
    if not q:
        raise CypherRejected("빈 쿼리")
    sk = _skeleton(q)
    if ";" in sk:
        raise CypherRejected("여러 문장은 허용되지 않습니다")
    if not re.match(r"(?is)^\s*(MATCH|OPTIONAL\s+MATCH|WITH|UNWIND|RETURN)\b", sk):
        raise CypherRejected("MATCH/WITH/UNWIND/RETURN 으로 시작하는 조회만 허용됩니다")
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sk.upper()))
    bad = sorted(w for w in FORBIDDEN if w in words)
    if bad:
        raise CypherRejected(f"허용되지 않는 키워드: {', '.join(bad)}")
    if re.search(r"\bOPTIONAL\s+MATCH\b", sk, re.I):
        raise CypherRejected("OPTIONAL MATCH 대신 필수 MATCH 를 쓰세요(뒤의 WHERE 가 선택 매치에만 적용되어 필터가 무시됩니다)")
    if not re.search(r"\bRETURN\b", sk, re.I):
        raise CypherRejected("RETURN 이 없습니다")
    m = _LIMIT_END.search(q)
    if not m:
        return f"{q} LIMIT {DEFAULT_LIMIT}"
    if int(m.group(1)) > MAX_LIMIT:
        return _LIMIT_END.sub(f"LIMIT {MAX_LIMIT}", q)
    return q
