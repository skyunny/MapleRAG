"""5단계 정규화: LLM/수동 추출 결과(ChangeRecord·RelationRecord 딕셔너리) → RDF.

핵심 결정
- 개체 병합: 이름을 NFKC·공백·따옴표 제거로 정규화한 키가 같으면 같은 개체. 표기가 다른 변형은 skos:altLabel 로 보존한다.
- 같은 이름의 클래스가 섞이면(Item/Equipment/Consumable, Monster/Boss) 같은 계열 안에서 가장 구체적/빈도 높은 클래스를 택한다.
  계열이 다르면(예: Skill vs Item) 별개 개체로 둔다(충돌 건수는 보고).
- 스킬은 소속 직업이 알려지면 (직업, 이름) 쌍으로 구분한다(직업이 다른 동명 스킬 보호). 직업을 모르면 이름만 쓴다.
- PatchChange / Fact URI 는 내용 해시 → 같은 입력을 다시 넣어도 트리플이 늘지 않는다(멱등).
- 시간: 패치 적용일을 validFrom(xsd:date)에, 어느 패치인지를 inPatch 에 기록한다. validTo 는 후속 변경을 알 수 없어 비워 둔다.
- 출처: 패치노트 문서(SourceDocument)로 prov:wasDerivedFrom, tier 는 문서의 tier 를 따른다.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict

from rdflib import RDF, RDFS, XSD, BNode, Graph, Literal, URIRef
from rdflib.namespace import PROV, SKOS

from maplerag.extract.rule_mapper import MAPLE, MID, TIER, _dt, add_source, name_hash, new_graph

FAMILY = {"Item": "item", "Equipment": "item", "Consumable": "item", "Monster": "monster", "Boss": "monster",
          "Skill": "skill", "Job": "job", "Quest": "quest", "Map": "map", "NPC": "npc", "Event": "event"}
SPECIFIC = {"Equipment", "Consumable", "Boss"}      # 계열 내에서 일반 클래스(Item/Monster)보다 구체적인 것
PREDICATES = {"hasSkill", "dropsItem", "appearsIn", "rewards", "usableBy", "givenBy"}
_QUOTES = re.compile(r"[‘’“”'\"「」『』]")


def name_key(name: str) -> str:
    s = unicodedata.normalize("NFKC", name)
    return re.sub(r"\s+", "", _QUOTES.sub("", s)).lower()


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]


class Normalizer:
    def __init__(self, notices: list[dict], existing: Graph | None = None):
        self.g = new_graph()
        self.stats = Counter()
        self.docs = {str(d["meta"]["notice_id"]): d for d in notices}
        self.src: dict[str, URIRef] = {}
        self.entity_class: dict[tuple, str] = {}          # (family, key) → 확정 클래스
        self.surface: dict[URIRef, Counter] = defaultdict(Counter)
        self.existing_events: dict[str, URIRef] = {}      # 이름키 → API 이벤트 URI
        self.existing_label: dict[URIRef, str] = {}       # 이미 대표 이름이 있는 개체(API 이벤트): 대표 이름을 바꾸지 않는다
        if existing is not None:
            for s_, o_ in existing.subject_objects(RDFS.label):
                self.existing_label[s_] = str(o_)
            for ev in existing.subjects(RDF.type, MAPLE.Event):
                for lab in existing.objects(ev, RDFS.label):
                    self.existing_events[name_key(str(lab))] = ev
                    self.existing_events[name_key(re.sub(r"^\s*\[[^\]]*\]\s*", "", str(lab)))] = ev

    # ---------- 1단계: 클래스 확정 ----------
    def resolve_classes(self, records: list[dict]) -> None:
        seen = defaultdict(Counter)
        for rec in records:
            for c in rec["changes"]:
                seen[(FAMILY[c["target_class"]], name_key(c["target_name"]))][c["target_class"]] += 1
            for r in rec["relations"]:
                seen[(FAMILY[r["subject_class"]], name_key(r["subject_name"]))][r["subject_class"]] += 1
                seen[(FAMILY[r["object_class"]], name_key(r["object_name"]))][r["object_class"]] += 1
        for k, cnt in seen.items():
            spec = Counter({c: n for c, n in cnt.items() if c in SPECIFIC})
            pick = (spec or cnt).most_common(1)[0][0]
            if len(spec) > 1:
                self.stats["클래스 충돌(구체 클래스 2종 이상 → 최빈값)"] += 1
            if len(cnt) > 1:
                self.stats["같은 이름·여러 클래스 표기 → 통일"] += 1
            self.entity_class[k] = pick

    # ---------- 개체 ----------
    def _src(self, notice_id: str) -> URIRef:
        if notice_id not in self.src:
            self.src[notice_id] = add_source(self.g, self.docs[notice_id])
        return self.src[notice_id]

    def entity(self, name: str, cls: str, src: URIRef, tier: str, job: str | None = None) -> URIRef:
        fam, key = FAMILY[cls], name_key(name)
        final = self.entity_class.get((fam, key), cls)
        if fam == "skill" and job:
            uri = MID[f"skill/h-{_hash(name_key(job) + '|' + key)}"]
        elif fam == "event" and key in self.existing_events:
            uri = self.existing_events[key]
            self.stats["API 이벤트와 이름 일치 → 병합"] += 1
        else:
            uri = MID[f"{fam}/h-{_hash(key)}"]
        self.surface[uri][name.strip()] += 1
        if (uri, RDF.type, MAPLE[final]) not in self.g:
            self.g.add((uri, RDF.type, MAPLE[final]))
        self.g.add((uri, PROV.wasDerivedFrom, src))
        self.g.add((uri, MAPLE.sourceTier, TIER[tier]))
        return uri

    def _finish_labels(self) -> None:
        """대표 이름(가장 흔한 표기)을 rdfs:label, 나머지 표기를 skos:altLabel 로."""
        for uri, cnt in self.surface.items():
            fixed = self.existing_label.get(uri)
            # 이미 API 제목이 대표 이름이면 그대로 두고, 추출된 표기는 별칭으로만 남긴다.
            # (rdfs:label 이 둘이면 n10s 가 단일값 속성을 덮어써서 어느 쪽이 남을지 적재 순서에 따라 달라진다)
            main = fixed or cnt.most_common(1)[0][0]
            if not fixed:
                self.g.add((uri, RDFS.label, Literal(main, lang="ko")))
            for alt in cnt:
                if alt != main:
                    self.g.add((uri, SKOS.altLabel, Literal(alt, lang="ko")))
                    self.stats["별칭(altLabel) 추가"] += 1

    # ---------- 변경/관계 ----------
    def _patch_info(self, rec: dict):
        nid = str(rec["notice_id"])
        doc = self.docs[nid]
        return nid, MID[f"patch/{nid}"], self._src(nid), doc["tier"], doc["fetched_at"], doc["meta"]["date"][:10]

    def add_change(self, rec: dict, c: dict, by: str) -> None:
        nid, patch, src, tier, fetched, pdate = self._patch_info(rec)
        job = None
        if c["target_class"] == "Skill" and c.get("related_job"):
            job_uri = self.entity(c["related_job"], "Job", src, tier)
            job = c["related_job"]
        target = self.entity(c["target_name"], c["target_class"], src, tier, job)
        if job:
            if self._add_fact_once(f"hasSkill|{job_uri}|{target}|{nid}", job_uri, MAPLE.hasSkill, target, c["evidence"],
                                   by, patch, src, tier, fetched, pdate, inferred=True):
                self.g.add((job_uri, MAPLE.hasSkill, target))
        cid = MID[f"change/{_hash('|'.join([nid, str(target), c['change_type'], c.get('attribute') or '', c.get('before_value') or '', c.get('after_value') or '', c['evidence']]))}"]
        if (cid, RDF.type, MAPLE.PatchChange) in self.g:
            self.stats["중복 변경 제거"] += 1
            return
        g = self.g
        g.add((cid, RDF.type, MAPLE.PatchChange))
        g.add((cid, RDF.subject, target)); g.add((cid, RDF.predicate, MAPLE.changes))
        g.add((cid, MAPLE.changes, target)); g.add((cid, MAPLE.changeType, MAPLE[c["change_type"]]))
        g.add((cid, MAPLE.inPatch, patch)); g.add((patch, MAPLE.hasChange, cid))
        g.add((cid, MAPLE.validFrom, Literal(pdate, datatype=XSD.date)))
        if c.get("attribute"): g.add((cid, MAPLE.attribute, Literal(c["attribute"])))
        if c.get("before_value"): g.add((cid, MAPLE.beforeValue, Literal(c["before_value"])))
        if c.get("after_value"):
            g.add((cid, MAPLE.afterValue, Literal(c["after_value"])))
            g.add((cid, MAPLE.value, Literal(c["after_value"])))
        else:
            g.add((cid, MAPLE.value, Literal(c["change_type"])))     # Fact 셰이프: object/value 중 하나 필요
        g.add((cid, MAPLE.evidence, Literal(c["evidence"])))
        g.add((cid, MAPLE.extractedBy, Literal(by)))
        g.add((cid, PROV.wasDerivedFrom, src)); g.add((cid, MAPLE.sourceTier, TIER[tier]))
        g.add((cid, PROV.generatedAtTime, _dt(fetched)))
        self.stats["PatchChange"] += 1

    def _add_fact_once(self, key, subj, pred, obj, evidence, by, patch, src, tier, fetched, pdate, inferred=False) -> bool:
        fid = MID[f"fact/{_hash(key)}"]
        if (fid, RDF.type, MAPLE.Fact) in self.g:
            self.stats["중복 관계 제거"] += 1
            return False
        g = self.g
        g.add((fid, RDF.type, MAPLE.Fact))
        g.add((fid, RDF.subject, subj)); g.add((fid, RDF.predicate, pred)); g.add((fid, RDF.object, obj))
        g.add((fid, MAPLE.inPatch, patch)); g.add((fid, MAPLE.validFrom, Literal(pdate, datatype=XSD.date)))
        g.add((fid, MAPLE.evidence, Literal(evidence))); g.add((fid, MAPLE.extractedBy, Literal(by)))
        g.add((fid, PROV.wasDerivedFrom, src)); g.add((fid, MAPLE.sourceTier, TIER[tier]))
        g.add((fid, PROV.generatedAtTime, _dt(fetched)))
        g.add((subj, MAPLE.aboutFact, fid))
        self.stats["hasSkill(소제목 문맥 추론) Fact" if inferred else "관계 Fact"] += 1
        return True

    def add_relation(self, rec: dict, r: dict, by: str) -> None:
        nid, patch, src, tier, fetched, pdate = self._patch_info(rec)
        s = self.entity(r["subject_name"], r["subject_class"], src, tier)
        o = self.entity(r["object_name"], r["object_class"], src, tier)
        pred = MAPLE[r["predicate"]]
        if self._add_fact_once(f"{r['predicate']}|{s}|{o}|{nid}", s, pred, o, r["evidence"], by, patch, src, tier, fetched, pdate):
            self.g.add((s, pred, o))

    def run(self, records: list[dict]) -> Graph:
        """records: {'notice_id','changes','relations','extracted_by'} 목록."""
        self.resolve_classes(records)
        for rec in records:
            by = rec.get("extracted_by", "llm")
            for c in rec["changes"]:
                self.add_change(rec, c, by)
            for r in rec["relations"]:
                self.add_relation(rec, r, by)
        self._finish_labels()
        return self.g
