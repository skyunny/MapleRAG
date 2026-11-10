"""NEXON Open API(메이플스토리) 수집기.

확인한 공식 문서 사실 (https://openapi.nexon.com 문서 페이지, 2026-11-10 열람)
- 서버: https://open.api.nexon.com , 인증 헤더: x-nxopen-api-key
- 개발 단계 키 한도: 초당 5건 / 일 1,000건 (서비스 단계: 500건/초, 2천만/일)
- 공지·업데이트(패치노트)·이벤트·캐시샵: 목록은 '최근 20건'만 조회, 상세는 notice_id 로 본문(contents) 조회
- 캐릭터: /id?character_name= 으로 ocid, 이후 ocid(+date=YYYY-MM-DD) 로 조회. 데이터는 평균 15분 후 반영
- 429(OPENAPI00007) 호출량 초과, 400 OPENAPI00009 데이터 준비 중
- 의무: 앱에 'Data based on NEXON Open API' 표기, API 로 크롤링한 데이터는 30일 이내 갱신
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Iterator

import httpx

from maplerag.config import settings
from maplerag.ingest.base import (USER_AGENT, Collector, DiskCache, Failure,
                                  RawDocument, Throttle)
from maplerag.ingest.html_text import html_to_text

BASE_URL = "https://open.api.nexon.com"
KST = timezone(timedelta(hours=9))

# (목록 경로, 목록 응답 키, 상세 경로, doc_type)
NOTICE_KINDS = {
    "patch_note": ("/maplestory/v1/notice-update", "update_notice", "/maplestory/v1/notice-update/detail"),
    "notice":     ("/maplestory/v1/notice", "notice", "/maplestory/v1/notice/detail"),
    "event":      ("/maplestory/v1/notice-event", "event_notice", "/maplestory/v1/notice-event/detail"),
    "cashshop":   ("/maplestory/v1/notice-cashshop", "cashshop_notice", "/maplestory/v1/notice-cashshop/detail"),
}


class NexonClient:
    """키를 헤더로만 보내고, 캐시 키·URL 기록에는 절대 포함하지 않는 얇은 HTTP 클라이언트."""

    def __init__(self, transport: httpx.BaseTransport | None = None, min_interval: float = 0.5,
                 api_key: str | None = None, cache: DiskCache | None = None):
        key = api_key if api_key is not None else settings.nexon_api_key
        if not key:
            raise RuntimeError("NEXON_API_KEY 가 .env 에 없습니다.")
        self._client = httpx.Client(base_url=BASE_URL, timeout=15, transport=transport,
                                    headers={"x-nxopen-api-key": key, "User-Agent": USER_AGENT})
        self._throttle = Throttle(min_interval)
        self.cache = cache or DiskCache("nexon_api")
        self.live_calls = 0   # 실제 네트워크 호출 수 (일일 한도 관리용)

    def get(self, path: str, **params) -> tuple[dict, str]:
        """(응답 JSON, 기록용 URL) 반환. 캐시에 있으면 네트워크를 쓰지 않는다."""
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        record_url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
        cached = self.cache.get(record_url)
        if cached is not None:
            return cached, record_url
        self._throttle.wait()
        resp = self._client.get(path, params=params)
        self.live_calls += 1
        if resp.status_code != 200:
            err = {}
            try:
                err = resp.json().get("error", {})
            except Exception:
                pass
            raise RuntimeError(f"HTTP {resp.status_code} {err.get('name', '')} {err.get('message', '')}".strip())
        data = resp.json()
        self.cache.put(record_url, data)
        return data, record_url


class NexonNoticeCollector(Collector):
    """공지·패치노트(업데이트)·이벤트·캐시샵 공지를 목록→상세 순으로 수집한다."""

    name, tier = "nexon_api", "official"

    def __init__(self, client: NexonClient, kinds=("patch_note", "event", "notice"), limit_per_kind: int | None = None):
        super().__init__()
        self.client, self.kinds, self.limit = client, kinds, limit_per_kind

    def collect(self) -> Iterator[RawDocument]:
        for kind in self.kinds:
            list_path, list_key, detail_path = NOTICE_KINDS[kind]
            try:
                listing, _ = self.client.get(list_path)
            except Exception as e:
                self.failures.append(Failure(list_path, str(e)))
                continue
            items = listing.get(list_key, [])[: self.limit]
            for it in items:
                nid = it.get("notice_id")
                try:
                    detail, url = self.client.get(detail_path, notice_id=nid)
                except Exception as e:
                    self.failures.append(Failure(f"{detail_path}?notice_id={nid}", str(e)))
                    continue
                raw_html = detail.get("contents") or ""
                text = html_to_text(raw_html)   # 원본 HTML 은 캐시에 보존, 문서에는 정리된 텍스트만
                # 이벤트는 본문이 이미지뿐인 경우가 많다. 기간은 API 필드로 확실히 알 수 있으므로
                # 제목+기간만이라도 문서로 남긴다(보상 등 이미지 속 정보는 추출 불가 → 한계로 기록).
                if kind == "event" and len(text) < 40 and detail.get("date_event_start"):
                    text = (f"{detail.get('title', '')}\n이벤트 기간: {detail['date_event_start']} ~ "
                            f"{detail.get('date_event_end', '')}\n{text}").strip()
                    meta_note = "image_only_body"
                else:
                    meta_note = ""
                if not text:
                    self.failures.append(Failure(f"{detail_path}?notice_id={nid}", "본문 비어 있음"))
                    continue
                meta = {"notice_id": nid, "date": detail.get("date") or it.get("date"),
                        "page_url": detail.get("url") or it.get("url"), "raw_html_chars": len(raw_html)}
                if meta_note:
                    meta["note"] = meta_note
                for k in ("date_event_start", "date_event_end", "date_sale_start", "date_sale_end"):
                    if detail.get(k):
                        meta[k] = detail[k]
                yield RawDocument(source=self.name, url=url, fetched_at=datetime.now(KST).isoformat(timespec="seconds"),
                                  text=text, tier=self.tier, title=detail.get("title", it.get("title", "")),
                                  doc_type=kind, meta=meta)


# 캐릭터 조회에 쓰는 엔드포인트 (문서 확인: ocid + date(YYYY-MM-DD, 선택으로 보임) 파라미터)
CHARACTER_ENDPOINTS = {
    "basic": "/maplestory/v1/character/basic",
    "stat": "/maplestory/v1/character/stat",
    "item_equipment": "/maplestory/v1/character/item-equipment",
    "skill": "/maplestory/v1/character/skill",          # character_skill_grade 필요
    "link_skill": "/maplestory/v1/character/link-skill",
    "set_effect": "/maplestory/v1/character/set-effect",
    "ability": "/maplestory/v1/character/ability",
    "hyper_stat": "/maplestory/v1/character/hyper-stat",
}


class NexonCharacterCollector(Collector):
    """닉네임 → ocid → 각 조회 API. 개인정보라 영구 적재하지 않고 임시 트리플화에만 쓴다."""

    name, tier = "nexon_api_character", "official"

    def __init__(self, client: NexonClient, character_name: str, endpoints=None, date: str | None = None,
                 skill_grade: str = "6"):
        super().__init__()
        self.client, self.nickname, self.date, self.skill_grade = client, character_name, date, skill_grade
        self.endpoints = endpoints or list(CHARACTER_ENDPOINTS)

    def collect(self) -> Iterator[RawDocument]:
        try:
            data, _ = self.client.get("/maplestory/v1/id", character_name=self.nickname)
            ocid = data["ocid"]
        except Exception as e:
            self.failures.append(Failure("/maplestory/v1/id", str(e)))
            return
        for key in self.endpoints:
            params = {"ocid": ocid}
            if self.date:
                params["date"] = self.date
            if key == "skill":
                params["character_skill_grade"] = self.skill_grade
            try:
                body, url = self.client.get(CHARACTER_ENDPOINTS[key], **params)
            except Exception as e:
                self.failures.append(Failure(CHARACTER_ENDPOINTS[key], str(e)))
                continue
            # 기록용 URL 에서 ocid 값은 가린다 (로그·보고서 노출 방지)
            safe_url = url.replace(ocid, "<ocid>")
            yield RawDocument(source=self.name, url=safe_url, fetched_at=datetime.now(KST).isoformat(timespec="seconds"),
                              text=json.dumps(body, ensure_ascii=False), tier=self.tier,
                              title=f"character:{key}", doc_type=f"character_{key}", meta={"ocid": ocid})
