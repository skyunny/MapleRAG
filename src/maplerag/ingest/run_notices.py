"""공지·패치노트·이벤트 수집 실행: uv run python -m maplerag.ingest.run_notices"""
import json
from collections import Counter
from maplerag.ingest.base import RAW_DIR
from maplerag.ingest.nexon_api import NexonClient, NexonNoticeCollector


def main():
    client = NexonClient()
    col = NexonNoticeCollector(client, kinds=("patch_note", "event", "notice"), limit_per_kind=20)
    docs = list(col.collect())
    out = RAW_DIR / "docs" / "notices.jsonl"
    out.write_text("\n".join(d.to_json() for d in docs), encoding="utf-8")
    print(col.report(len(docs)))
    print("실제 API 호출:", client.live_calls, "회")
    if not docs:
        print("수집된 문서가 없습니다. 위 실패 사유를 확인하세요.")
        return
    print("종류별:", dict(Counter(d.doc_type for d in docs)))
    print("본문 길이(자): 최소", min(len(d.text) for d in docs), "/ 최대", max(len(d.text) for d in docs),
          "/ 평균", sum(len(d.text) for d in docs) // len(docs))
    for kind in ("patch_note", "event", "notice"):
        ds = [d for d in docs if d.doc_type == kind]
        if ds:
            dates = sorted(d.meta.get("date", "")[:10] for d in ds)
            print(f"  {kind}: {len(ds)}건, 기간 {dates[0]} ~ {dates[-1]}")
    print("저장:", out)


if __name__ == "__main__":
    main()
