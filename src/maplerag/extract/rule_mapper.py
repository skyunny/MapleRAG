"""규칙 기반 매퍼: 구조화 데이터 → RDF (LLM 미사용: 비용·정확도 때문).

- 이벤트·패치: 공지 API 의 필드(제목·notice_id·기간·날짜)만 사용. 본문 해석은 4단계 LLM 추출 몫.
- 캐릭터: Open API JSON → 임시 rdflib 그래프. 개인정보라 파일·Neo4j 에 저장하지 않는다.
URI 규칙: 공식 ID(notice_id, ocid) 우선, 없으면 이름 해시 'h-xxxxxxxxxx' (한글 이름은 URI 에 쓰지 않음).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime

from rdflib import RDF, RDFS, XSD, BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import PROV

MAPLE = Namespace("https://example.org/maple#")
MID = Namespace("https://example.org/maple/id/")
TIER = {"official": MAPLE.official, "user-curated": MAPLE.userCurated, "community": MAPLE.community}


def name_hash(name: str) -> str:
    norm = re.sub(r"\s+", "", unicodedata.normalize("NFKC", name)).lower()
    return "h-" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:10]


def new_graph() -> Graph:
    g = Graph()
    g.bind("maple", MAPLE); g.bind("mid", MID); g.bind("prov", PROV)
    return g


def _dt(s: str) -> Literal:
    """'2026-10-22T10:00+09:00' → 초가 있는 xsd:dateTime (원문은 초 생략형이라 그대로는 무효)."""
    return Literal(datetime.fromisoformat(s).isoformat(timespec="seconds"), datatype=XSD.dateTime)


def add_source(g: Graph, doc: dict) -> URIRef:
    """RawDocument(dict) 1건 → SourceDocument 노드. 모든 개체가 여기로 prov:wasDerivedFrom."""
    uri = MID[f"source/{hashlib.sha1(doc['url'].encode()).hexdigest()[:12]}"]
    g.add((uri, RDF.type, MAPLE.SourceDocument))
    g.add((uri, MAPLE.url, Literal(doc["url"], datatype=XSD.anyURI)))
    g.add((uri, MAPLE.sourceTier, TIER[doc["tier"]]))
    g.add((uri, PROV.generatedAtTime, _dt(doc["fetched_at"])))
    if doc.get("title"):
        g.add((uri, MAPLE.docTitle, Literal(doc["title"])))
    return uri


def _entity(g, uri, cls, label, src, tier):
    g.add((uri, RDF.type, cls))
    g.add((uri, RDFS.label, Literal(label, lang="ko")))
    g.add((uri, PROV.wasDerivedFrom, src))
    g.add((uri, MAPLE.sourceTier, TIER[tier]))
    return uri


_VER = re.compile(r"(\d+\.\d+\.\d+(?:\(\d+\))?)")


def map_notices(docs: list[dict]) -> Graph:
    """수집한 공지 RawDocument 목록 → Patch / Event / TimeInterval."""
    g = new_graph()
    for d in docs:
        src = add_source(g, d)
        m = d["meta"]
        if d["doc_type"] == "patch_note":
            uri = MID[f"patch/{m['notice_id']}"]
            _entity(g, uri, MAPLE.Patch, d["title"], src, d["tier"])
            ver = _VER.search(d["title"])
            # 버전 번호가 없으면 날짜+제목을 식별자로 쓴다 (D3 확정)
            g.add((uri, MAPLE.patchVersion, Literal(ver.group(1) if ver else f"{m['date'][:10]} {d['title']}")))
            g.add((uri, MAPLE.patchDate, Literal(m["date"][:10], datatype=XSD.date)))
        elif d["doc_type"] == "event" and m.get("date_event_start"):
            uri, iv = MID[f"event/{m['notice_id']}"], MID[f"interval/event-{m['notice_id']}"]
            _entity(g, uri, MAPLE.Event, d["title"], src, d["tier"])
            g.add((iv, RDF.type, MAPLE.TimeInterval))
            g.add((iv, MAPLE.startDate, _dt(m["date_event_start"])))
            if m.get("date_event_end"):
                g.add((iv, MAPLE.endDate, _dt(m["date_event_end"])))
            g.add((uri, MAPLE.during, iv))
    return g


def _fact(g, subj, pred, obj, attr, value, src, tier, fetched):
    f = BNode()
    g.add((f, RDF.type, MAPLE.Fact))
    g.add((f, RDF.subject, subj)); g.add((f, RDF.predicate, pred))
    if obj is not None:
        g.add((f, RDF.object, obj))
    g.add((f, MAPLE.attribute, Literal(attr)))
    g.add((f, MAPLE.value, Literal(value)))
    g.add((f, PROV.wasDerivedFrom, src)); g.add((f, MAPLE.sourceTier, TIER[tier]))
    g.add((f, PROV.generatedAtTime, _dt(fetched)))
    g.add((f, MAPLE.extractedBy, Literal("rule")))
    g.add((subj, MAPLE.aboutFact, f))


def map_character(docs: dict[str, dict]) -> Graph:
    """docs: doc_type → {'raw': RawDocument dict, 'body': 응답 JSON}. 임시 그래프(비저장)."""
    import json
    g = new_graph()
    basic_raw = docs["character_basic"]; b = json.loads(basic_raw["text"])
    src_b = add_source(g, basic_raw); tier = basic_raw["tier"]
    ocid = basic_raw["meta"]["ocid"]
    ch = MID[f"character/{ocid}"]
    _entity(g, ch, MAPLE.Character, b["character_name"], src_b, tier)
    g.add((ch, MAPLE.ocid, Literal(ocid)))
    g.add((ch, MAPLE.level, Literal(int(b["character_level"]), datatype=XSD.integer)))
    g.add((ch, MAPLE.worldName, Literal(b["world_name"])))
    job = MID[f"job/{name_hash(b['character_class'])}"]
    _entity(g, job, MAPLE.Job, b["character_class"], src_b, tier)
    g.add((ch, MAPLE.hasJob, job))
    if b.get("character_guild_name"):
        guild = MID[f"guild/{name_hash(b['world_name'] + b['character_guild_name'])}"]
        _entity(g, guild, MAPLE.Guild, b["character_guild_name"], src_b, tier)
        g.add((ch, MAPLE.memberOf, guild))

    if "character_item_equipment" in docs:
        raw = docs["character_item_equipment"]; src = add_source(g, raw)
        for it in json.loads(raw["text"])["item_equipment"]:
            item = MID[f"item/{name_hash(it['item_name'])}"]
            _entity(g, item, MAPLE.Equipment, it["item_name"], src, tier)
            g.add((ch, MAPLE.equips, item))
            if it.get("starforce") not in (None, "", "0"):
                _fact(g, ch, MAPLE.equips, item, "starforce", int(it["starforce"]), src, tier, raw["fetched_at"])
            if it.get("potential_option_grade"):
                _fact(g, ch, MAPLE.equips, item, "potential_grade", it["potential_option_grade"], src, tier, raw["fetched_at"])
    return g
