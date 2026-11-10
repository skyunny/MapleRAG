"""SHACL 검증: 위반 건수와 상세를 보고한다 (노트북의 '단계마다 건수 출력' 습관)."""
from collections import Counter
from pathlib import Path

from pyshacl import validate
from rdflib import Graph, Namespace
from rdflib.namespace import SH

ROOT = Path(__file__).resolve().parents[3]
ONTOLOGY = ROOT / "ontology" / "maple.ttl"
SHAPES = ROOT / "ontology" / "shapes.ttl"


def validate_graph(data: Graph) -> dict:
    """data 그래프를 shapes.ttl 로 검증. 하위 클래스 추론을 위해 온톨로지를 함께 사용한다."""
    conforms, report_graph, _ = validate(
        data,
        shacl_graph=str(SHAPES),
        ont_graph=str(ONTOLOGY),
        inference="rdfs",
        abort_on_first=False,
    )
    violations = []
    for r in report_graph.subjects(SH.resultSeverity, None):
        violations.append({
            "focus": str(report_graph.value(r, SH.focusNode)),
            "path": str(report_graph.value(r, SH.resultPath)),
            "message": str(report_graph.value(r, SH.resultMessage)),
            "shape": str(report_graph.value(r, SH.sourceShape)),
        })
    return {
        "conforms": conforms,
        "violation_count": len(violations),
        "by_path": dict(Counter(v["path"] for v in violations)),
        "violations": violations,
    }


def load(path: Path) -> Graph:
    g = Graph()
    g.parse(path, format="turtle")
    return g
