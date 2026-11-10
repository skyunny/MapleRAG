"""질문 1개를 단계별로 실행해 보여 준다. 실행: uv run python -m maplerag.rag.run "질문"  (LLM 호출 발생)"""
import json
import sys

from maplerag.rag.service import ask_stream


def main():
    q = " ".join(sys.argv[1:]) or "에르다 샤워는 어떻게 바뀌었나?"
    print("질문:", q)
    final = {}
    trace = {}
    for node, upd in ask_stream(q, tags=["cli"]):
        if node == "__trace__":
            trace = upd
            continue
        final.update(upd)
        brief = {k: v for k, v in upd.items() if k in ("analysis", "candidates", "cypher", "last_error", "attempts", "status")}
        if "rows" in upd:
            brief["n_rows"] = len(upd["rows"])
        print(f"\n[{node}]", json.dumps(brief, ensure_ascii=False)[:600])
    print("\n=== 답변 ===\n" + final.get("answer", ""))
    print("상태:", final.get("status"))
    for e in final.get("evidence", [])[:5]:
        print(f"  근거[{e['row']}] ({e['subject']}) {e['predicate']} {e['attribute']}: {e['before']} → {e['object']} | {e['patch_date']} | {e['evidence']}")
    print("출처:", final.get("sources"))
    from langfuse import get_client
    print("Langfuse 트레이스:", get_client().get_trace_url(trace_id=trace.get("trace_id")), "| 세션", trace.get("session_id"))


if __name__ == "__main__":
    main()
