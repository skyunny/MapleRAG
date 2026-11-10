"""수집기 검증 (실제 API 호출 없음 — httpx MockTransport 사용).

확인 항목: 정규화 건수, 캐시 재사용(재실행 시 호출 0), 키 비노출, 실패 목록 수집
실행: uv run python tests/check_ingest.py
"""
import sys
import tempfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from maplerag.ingest.base import DiskCache  # noqa: E402
from maplerag.ingest.nexon_api import NexonClient, NexonNoticeCollector  # noqa: E402

FAKE_KEY = "TEST-KEY-should-never-be-stored"
seen_headers = []


def handler(req: httpx.Request) -> httpx.Response:
    seen_headers.append(req.headers.get("x-nxopen-api-key"))
    p, q = req.url.path, dict(req.url.params)
    if p == "/maplestory/v1/notice-update":
        return httpx.Response(200, json={"update_notice": [
            {"title": "데모 업데이트 A", "url": "u", "notice_id": 1, "date": "2026-01-01T00:00+09:00"},
            {"title": "데모 업데이트 B(본문 없음)", "url": "u", "notice_id": 2, "date": "2026-01-02T00:00+09:00"},
            {"title": "데모 업데이트 C(오류)", "url": "u", "notice_id": 3, "date": "2026-01-03T00:00+09:00"}]})
    if p == "/maplestory/v1/notice-update/detail":
        nid = q["notice_id"]
        if nid == "1":
            return httpx.Response(200, json={"title": "데모 업데이트 A", "url": "u", "contents": "스킬 X 데미지 100%→120%", "date": "2026-01-01T00:00+09:00"})
        if nid == "2":
            return httpx.Response(200, json={"title": "B", "url": "u", "contents": "  ", "date": "2026-01-02T00:00+09:00"})
        return httpx.Response(429, json={"error": {"name": "OPENAPI00007", "message": "API 호출량 초과"}})
    return httpx.Response(400, json={"error": {"name": "OPENAPI00006", "message": "invalid path"}})


with tempfile.TemporaryDirectory() as tmp:
    cache = DiskCache("t", root=Path(tmp))
    client = NexonClient(transport=httpx.MockTransport(handler), min_interval=0, api_key=FAKE_KEY, cache=cache)
    col = NexonNoticeCollector(client, kinds=("patch_note",))
    docs = list(col.collect())
    print(col.report(len(docs)))
    print(f"1회차 실제 호출 {client.live_calls}회 (기대: 목록1 + 상세3 = 4)")
    r1 = (len(docs) == 1 and len(col.failures) == 2 and client.live_calls == 4)

    # 2회차: 성공한 응답은 캐시 → 실패(429)만 재시도되므로 호출 1회
    client.live_calls = 0
    col2 = NexonNoticeCollector(client, kinds=("patch_note",))
    docs2 = list(col2.collect())
    print(f"2회차 실제 호출 {client.live_calls}회 (기대: 재시도 대상 429 상세 1회만)")
    r2 = client.live_calls == 1 and len(docs2) == 1

    # 키 비노출: 캐시 파일과 RawDocument 어디에도 키가 없어야 한다
    blob = "".join(p.read_text(encoding="utf-8") for p in Path(tmp).rglob("*.json")) + "".join(d.to_json() for d in docs)
    r3 = FAKE_KEY not in blob and all(h == FAKE_KEY for h in seen_headers)
    print(f"키가 캐시·문서에 노출되지 않음: {FAKE_KEY not in blob} / 헤더로는 전송됨: {all(h == FAKE_KEY for h in seen_headers)}")

    d = docs[0]
    print("샘플:", d.doc_type, d.tier, d.title, "|", d.text, "|", d.url)
    r4 = "key" not in d.url.lower()

ok = r1 and r2 and r3 and r4
print("결과:", "OK" if ok else f"FAIL {r1, r2, r3, r4}")
sys.exit(0 if ok else 1)
