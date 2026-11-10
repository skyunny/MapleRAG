"""캐릭터 조회 수집: uv run python -m maplerag.ingest.run_character 닉네임"""
import json, sys
from maplerag.ingest.base import RAW_DIR
from maplerag.ingest.nexon_api import NexonClient, NexonCharacterCollector


def main(nickname: str):
    client = NexonClient()
    col = NexonCharacterCollector(client, nickname)
    docs = list(col.collect())
    print(col.report(len(docs)))
    print("실제 API 호출:", client.live_calls, "회")
    out = RAW_DIR / "docs" / "character_sample.jsonl"   # gitignore 대상, 개인정보
    out.write_text("\n".join(d.to_json() for d in docs), encoding="utf-8")
    for d in docs:
        body = json.loads(d.text)
        print(f"  {d.doc_type:22} {len(d.text):>7}자  최상위 키: {list(body)[:8]}")


if __name__ == "__main__":
    main(sys.argv[1])
