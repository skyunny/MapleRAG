"""캐릭터 조회 경로: 닉네임 → Open API → 임시 트리플(메모리) → SPARQL 질의. LLM 을 쓰지 않는다(비용·정확도).

개인정보·호출 제한 원칙
- 임시 rdflib 그래프로만 만들고 Neo4j/RDF 파일에 저장하지 않는다. 함수가 끝나면 그래프도 버려진다.
- ocid 는 응답·로그·출처 URL 에 노출하지 않는다(수집기가 URL 의 ocid 를 <ocid> 로 가림).
- 원본 API 응답은 data/raw 디스크 캐시(gitignore 대상)에 남는다 — 같은 조회의 반복 호출을 막기 위한 것이며 커밋 금지.
- 개발용 API 키 한도: 초당 5회·일 1,000회. NexonClient 가 호출 간격을 지키고 실제 호출 횟수를 센다.
- 넥슨 Open API 데이터 사용 표기 의무: 'Data based on NEXON Open API'. 데이터는 약 15분 지연될 수 있다.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from rdflib import Graph

from maplerag.extract.rule_mapper import map_character
from maplerag.ingest.nexon_api import NexonCharacterCollector, NexonClient

ATTRIBUTION = "Data based on NEXON Open API"
ENDPOINTS = ["basic", "item_equipment"]        # 이 경로가 쓰는 엔드포인트만 호출한다(최소 호출)

PREFIX = """PREFIX maple: <https://example.org/maple#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""
Q_SUMMARY = PREFIX + """SELECT ?name ?level ?world ?job ?guild WHERE {
  ?c a maple:Character ; rdfs:label ?name ; maple:level ?level ; maple:worldName ?world ; maple:hasJob/rdfs:label ?job .
  OPTIONAL { ?c maple:memberOf/rdfs:label ?guild } }"""
Q_STARFORCE = PREFIX + """SELECT ?item ?val WHERE {
  ?f a maple:Fact ; rdf:predicate maple:equips ; rdf:object ?i ; maple:attribute "starforce" ; maple:value ?val .
  ?i rdfs:label ?item } ORDER BY DESC(?val) LIMIT %d"""
Q_POTENTIAL = PREFIX + """SELECT ?item ?grade WHERE {
  ?f a maple:Fact ; rdf:predicate maple:equips ; rdf:object ?i ; maple:attribute "potential_grade" ; maple:value ?grade .
  ?i rdfs:label ?item }"""
Q_EQUIP = PREFIX + """SELECT ?item WHERE { ?c a maple:Character ; maple:equips/rdfs:label ?item }"""


@dataclass
class CharacterAnswer:
    answer: str
    rows: list = field(default_factory=list)
    sources: list = field(default_factory=list)       # 사용한 API 엔드포인트(ocid 가림)
    live_calls: int = 0
    attribution: str = ATTRIBUTION
    triples: int = 0


def build_temp_graph(nickname: str, client: NexonClient | None = None, date: str | None = None) -> tuple[Graph, list[str], int, list]:
    """(임시 그래프, 사용한 엔드포인트 URL들, 이번 실제 호출 수, 수집 실패 목록). 캐시가 있으면 live_calls 는 0."""
    client = client or NexonClient()
    col = NexonCharacterCollector(client, nickname, endpoints=ENDPOINTS, date=date)
    docs = {d.doc_type: asdict(d) for d in col.collect()}
    if "character_basic" not in docs:
        return Graph(), [], client.live_calls, col.failures
    return map_character(docs), [d["url"] for d in docs.values()], client.live_calls, col.failures


def _intent(q: str) -> str:
    if re.search(r"(스타포스|별|성)\s*(수|개|높|많|몇)|스타포스", q):
        return "starforce"
    if re.search(r"잠재|레전드리|유니크|에픽|등급", q):
        return "potential"
    if re.search(r"장비|착용|장착|아이템", q):
        return "equip"
    return "summary"


def query_graph(g: Graph, question: str) -> tuple[str, list]:
    intent = _intent(question)
    if intent == "starforce":
        rows = [(str(r.item), int(r.val)) for r in g.query(Q_STARFORCE % 5)]
        return ("스타포스가 높은 장비: " + ", ".join(f"{n} {v}성" for n, v in rows)) if rows else "스타포스가 적용된 장비 정보가 없습니다.", rows
    if intent == "potential":
        rows = [(str(r.item), str(r.grade)) for r in g.query(Q_POTENTIAL)]
        from collections import Counter
        cnt = Counter(gr for _, gr in rows)
        return ("잠재능력 등급 분포: " + ", ".join(f"{k} {v}개" for k, v in cnt.most_common())) if rows else "잠재능력 정보가 없습니다.", rows
    if intent == "equip":
        rows = [str(r.item) for r in g.query(Q_EQUIP)]
        return f"착용 장비 {len(rows)}개: " + ", ".join(rows[:10]) + (" ..." if len(rows) > 10 else ""), rows
    rows = [tuple(str(x) if x is not None else None for x in r) for r in g.query(Q_SUMMARY)]
    if not rows:
        return "기본 정보를 찾지 못했습니다.", rows
    name, level, world, job, guild = rows[0]
    return f"{name}: Lv.{level} {job} ({world}" + (f", 길드 {guild}" if guild else "") + ")", rows


def ask_character(nickname: str, question: str, client: NexonClient | None = None) -> CharacterAnswer:
    g, urls, calls, fails = build_temp_graph(nickname, client)
    if len(g) == 0:
        why = fails[0].reason if fails else "알 수 없는 이유"
        return CharacterAnswer(f"캐릭터 정보를 가져오지 못했습니다: {str(why)[:120]}", live_calls=calls)
    text, rows = query_graph(g, question)
    return CharacterAnswer(text, rows, urls, calls, triples=len(g))
