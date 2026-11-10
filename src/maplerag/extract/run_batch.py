"""4단계 일괄 추출: 패치노트 전체 청크를 병렬 4개로 추출한다. 재실행하면 캐시된 청크는 건너뛴다.
실행: uv run python -m maplerag.extract.run_batch
"""
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from langfuse import Langfuse

from maplerag.extract.llm_extractor import PROMPT_NAME, extract_chunk, make_llm, register_prompt
from maplerag.extract.sections import pack_chunks, split_sections
from maplerag.ingest.base import RAW_DIR

WORKERS = 4
PRICE_IN, PRICE_OUT = 2.0, 10.0           # USD / 1M 토큰 (스킬 문서의 Sonnet 5.5 가격표, 청구액은 별도 확인 필요)
TOKEN_CEILING = (2_500_000, 1_200_000)    # 입력/출력 합계가 추정의 2배를 넘으면 중단하는 안전장치


def main():
    docs = [json.loads(l) for l in (RAW_DIR / "docs" / "notices.jsonl").read_text(encoding="utf-8").splitlines()]
    chunks = [c for d in docs if d["doc_type"] == "patch_note" for c in pack_chunks(split_sections(d))]
    info = register_prompt()
    lf = Langfuse()
    prompt = lf.get_prompt(PROMPT_NAME, label="production", type="text", cache_ttl_seconds=0)
    print(f"청크 {len(chunks)}개 / 프롬프트 v{prompt.version} ({'신규' if info['created'] else '기존'}) / 병렬 {WORKERS}")

    cache_dir = RAW_DIR / "extraction" / f"v{prompt.version}"
    llm, results, tin, tout, t0 = make_llm(), [], 0, 0, time.time()
    stopped = False
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {}
        it = iter(chunks)
        pending = set()
        def submit_next():
            c = next(it, None)
            if c is None:
                return False
            f = ex.submit(extract_chunk, c, prompt.prompt, llm, cache_dir); pending.add(f); return True
        for _ in range(WORKERS):
            submit_next()
        done_n = 0
        while pending:
            for f in as_completed(list(pending)):
                pending.discard(f)
                r = f.result(); results.append(r); done_n += 1
                tin += r.input_tokens if not r.cached else 0
                tout += r.output_tokens if not r.cached else 0
                if done_n % 20 == 0 or r.error:
                    cost = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT
                    print(f"  진행 {done_n}/{len(chunks)} | 신규 토큰 입력 {tin:,} 출력 {tout:,} | 약 ${cost:.2f} | {time.time()-t0:.0f}s"
                          + (f" | 오류: {r.chunk.stable_key} {r.error[:60]}" if r.error else ""))
                if tin > TOKEN_CEILING[0] or tout > TOKEN_CEILING[1]:
                    stopped = True
                    print("!! 토큰 안전장치 초과: 새 청크 제출 중단");
                elif not stopped:
                    submit_next()
                break

    # 검증된 결과 저장 (청크 메타 포함)
    out = RAW_DIR / "docs" / "extraction_all.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps({
                "notice_id": r.chunk.doc_notice_id, "patch": r.chunk.patch_title, "date": r.chunk.patch_date,
                "titles": r.chunk.titles[:8], "prompt_version": prompt.version, "error": r.error,
                "changes": [c.model_dump() for c in r.kept_changes], "relations": [x.model_dump() for x in r.kept_relations],
                "dropped": [{"kind": d.kind, "reason": d.reason, "item": d.item} for d in r.dropped]}, ensure_ascii=False) + "\n")

    ok = [r for r in results if not r.error]
    fails = [r for r in results if r.error]
    nch, nrel = sum(len(r.kept_changes) for r in ok), sum(len(r.kept_relations) for r in ok)
    ndrop = sum(len(r.dropped) for r in ok)
    new_in, new_out = sum(r.input_tokens for r in results if not r.cached), sum(r.output_tokens for r in results if not r.cached)
    print(f"\n=== 결과 ===\n청크 {len(results)}개: 성공 {len(ok)} / 실패 {len(fails)} (캐시 사용 {sum(r.cached for r in results)}, 반분 재시도 {sum(r.splits for r in results)}회)")
    print(f"유지: 변경 {nch}건, 관계 {nrel}건 / 코드 검증 폐기 {ndrop}건 ({ndrop/max(1,ndrop+nch+nrel):.1%})")
    print("폐기 사유:", dict(Counter(d.reason[:18] for r in ok for d in r.dropped)))
    print("변경 종류:", dict(Counter(c.change_type for r in ok for c in r.kept_changes)))
    print("클래스:", dict(Counter(c.target_class for r in ok for c in r.kept_changes)))
    sk = [c for r in ok for c in r.kept_changes if c.target_class == "Skill"]
    print(f"스킬 {len(sk)}건 중 related_job 채움 {sum(bool(c.related_job) for c in sk)}건")
    print(f"이번 실행 신규 토큰 입력 {new_in:,} / 출력 {new_out:,} → 약 ${new_in/1e6*PRICE_IN + new_out/1e6*PRICE_OUT:.2f}, {time.time()-t0:.0f}초")
    for r in fails[:8]:
        print("  실패:", r.chunk.patch_title[:24], r.chunk.titles[:2], r.error[:80])
    print("저장:", out)
    lf.flush()


if __name__ == "__main__":
    main()
