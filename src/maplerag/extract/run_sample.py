"""4단계 샘플 추출: 대표 청크 10개만 LLM 으로 추출해 결과를 확인한다.
실행: uv run python -m maplerag.extract.run_sample
"""
import json
import random
import re

from langfuse import Langfuse

from maplerag.extract.llm_extractor import (PROMPT_NAME, extract_chunk, make_llm,
                                            register_prompt)
from maplerag.extract.sections import pack_chunks, split_sections
from maplerag.ingest.base import RAW_DIR

# 유형별로 골고루 뽑기 위한 버킷 (제목+본문 키워드). 순서대로 2개씩, 서로 다른 패치 우선.
BUCKETS = [
    ("스킬 조정", r"스킬.{0,30}(증가|감소|변경)"),
    ("아이템/장비/확률", r"(스타포스|큐브|잠재|아이템|장비|확률)"),
    ("보스/몬스터", r"(보스|몬스터|드롭)"),
    ("이벤트/보상", r"(이벤트).{0,40}(보상|지급)"),
    ("오류 수정(변경 아님: 음성 대조)", r"(오류|현상이 수정)"),
]


def select_samples(chunks, n=10, seed=7):
    rng = random.Random(seed)
    picked, used_patch = [], set()
    for name, pat in BUCKETS:
        pool = [c for c in chunks if re.search(pat, c.text) and c not in [p[1] for p in picked]]
        rng.shuffle(pool)
        pool.sort(key=lambda c: c.doc_notice_id in used_patch)   # 안 쓴 패치 우선
        for c in pool[:2]:
            picked.append((name, c)); used_patch.add(c.doc_notice_id)
    return picked[:n]


def main():
    docs = [json.loads(l) for l in (RAW_DIR / "docs" / "notices.jsonl").read_text(encoding="utf-8").splitlines()]
    chunks = [c for d in docs if d["doc_type"] == "patch_note" for c in pack_chunks(split_sections(d))]
    samples = select_samples(chunks)
    print(f"전체 청크 {len(chunks)}개 중 샘플 {len(samples)}개 선택")

    info = register_prompt()
    lf = Langfuse()
    prompt = lf.get_prompt(PROMPT_NAME, label="production", type="text", cache_ttl_seconds=0)
    print(f"Langfuse 프롬프트 '{info['name']}' v{prompt.version} ({'신규 등록' if info['created'] else '기존 버전 재사용'})")

    llm, results = make_llm(), []
    for i, (bucket, c) in enumerate(samples, 1):
        r = extract_chunk(c, prompt.prompt, llm)
        results.append((bucket, r))
        print(f"[{i:02d}] {bucket:22} {c.patch_title[:26]:26} 입력 {r.input_tokens:>5}tok 출력 {r.output_tokens:>5}tok "
              f"{r.seconds:>5.1f}s | 변경 {len(r.kept_changes)} 관계 {len(r.kept_relations)} 폐기 {len(r.dropped)}"
              + (f" | 오류: {r.error}" if r.error else ""))

    out = RAW_DIR / "docs" / "extraction_sample.json"
    out.write_text(json.dumps([{
        "bucket": b, "patch": r.chunk.patch_title, "date": r.chunk.patch_date, "titles": r.chunk.titles[:8],
        "chars": len(r.chunk.text), "prompt_version": prompt.version,
        "changes": [c.model_dump() for c in r.kept_changes], "relations": [x.model_dump() for x in r.kept_relations],
        "dropped": [{"kind": d.kind, "reason": d.reason, "item": d.item} for d in r.dropped],
        "tokens": [r.input_tokens, r.output_tokens], "error": r.error} for b, r in results],
        ensure_ascii=False, indent=1), encoding="utf-8")
    tin, tout = sum(r.input_tokens for _, r in results), sum(r.output_tokens for _, r in results)
    print(f"\n합계: 입력 {tin:,}tok / 출력 {tout:,}tok / 호출 {len(results)}회 / 저장 {out}")
    lf.flush()


if __name__ == "__main__":
    main()
