"""평가 지표 (순수 함수: 모델·DB 없이 테스트 가능).

지표 정의
- status_match      : 실제 상태가 기대 상태 목록에 있는가 (answered / no_evidence / refused / query_failed)
- cypher_success    : (범위 안 질문) 마지막 실행된 Cypher 가 오류 없이 실행됐는가.   cypher_first_try : 첫 시도에 성공했는가
- answer_contains   : (answered 기대) answer_contains 의 모든 그룹에서 표기 하나 이상이 답변에 있는가 (공백 무시)
- evidence_match    : (answered 기대) evidence_contains 의 각 묶음을 모두 포함하는 근거 항목이 하나 이상 있는가
- out_of_scope_refusal : (범위 밖 질문, refused 기대) 실제로 거절했는가
- no_data_abstain   : (자료 없음 질문) 지어내지 않고 '근거 없음'으로 끝났는가
- latency_s / input_tokens / output_tokens / cost_usd : 비용·속도 (cost 는 단가표 기반 추정)
"""
from __future__ import annotations

import json

PRICE_IN, PRICE_OUT = 2.0, 10.0      # USD / 1M 토큰 (스킬 문서의 단가표 — 실제 청구액과 다를 수 있음)


def _n(s) -> str:
    return "".join(str(s).split())


def estimate_cost(in_tok: int, out_tok: int) -> float:
    return in_tok / 1e6 * PRICE_IN + out_tok / 1e6 * PRICE_OUT


def score(q: dict, out: dict) -> list[tuple[str, float, str]]:
    exp, cat = q["expected"], q["category"]
    res: list[tuple[str, float, str]] = []
    status = out.get("status")
    res.append(("status_match", float(status in exp["status"]), f"기대 {exp['status']} / 실제 {status}"))

    hist = out.get("history") or []
    if cat != "out_of_scope":
        ok = bool(hist) and not hist[-1].get("error")
        res.append(("cypher_success", float(ok), f"시도 {out.get('attempts')}회, 마지막 오류: {(hist[-1].get('error') if hist else 'Cypher 없음')[:80]}"))
        res.append(("cypher_first_try", float(ok and out.get("attempts") == 1), ""))

    if exp["status"] == ["answered"]:
        ans = _n(out.get("answer", ""))
        miss = [g for g in exp["answer_contains"] if not any(_n(a) in ans for a in g)]
        res.append(("answer_contains", float(not miss), f"빠진 그룹: {miss}" if miss else "모두 포함"))
        if exp.get("evidence_contains"):
            dumps = [_n(json.dumps(e, ensure_ascii=False)) for e in out.get("evidence") or []]
            miss = [g for g in exp["evidence_contains"] if not any(all(_n(s) in d for s in g) for d in dumps)]
            res.append(("evidence_match", float(not miss), f"근거 {len(dumps)}건, 못 찾은 묶음: {miss}" if miss else f"근거 {len(dumps)}건 중 일치"))
    if exp["status"] == ["refused"]:
        res.append(("out_of_scope_refusal", float(status == "refused"), ""))
    if cat == "no_data":
        res.append(("no_data_abstain", float(status == "no_evidence"), f"실제 {status}"))

    for k in ("latency_s", "input_tokens", "output_tokens", "cost_usd"):
        res.append((k, float(out.get(k, 0) or 0), ""))
    return res


def aggregate(per_item: list[list[tuple[str, float, str]]]) -> dict[str, float]:
    """항목별 점수 → 실행 전체 요약. 비율 지표는 '해당되는 항목'만 분모로 쓴다."""
    by: dict[str, list[float]] = {}
    for item in per_item:
        for name, val, _ in item:
            by.setdefault(name, []).append(val)
    agg = {}
    for name, vals in by.items():
        if name in ("input_tokens", "output_tokens", "cost_usd"):
            agg[f"total_{name}"] = sum(vals)
        elif name == "latency_s":
            s = sorted(vals)
            agg["latency_mean_s"] = sum(s) / len(s)
            agg["latency_p95_s"] = s[min(len(s) - 1, int(0.95 * len(s)))]
        else:
            agg[f"{name}_rate"] = sum(vals) / len(vals)
            agg[f"{name}_n"] = float(len(vals))
    return agg
