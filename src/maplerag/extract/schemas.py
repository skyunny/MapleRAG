"""LLM 추출 출력 스키마 (Pydantic). 온톨로지(maple.ttl)에 정의된 클래스·관계만 허용한다.

설계 이유
- Literal 로 클래스/관계/변경종류를 닫아 두면 구조화 출력(json_schema)이 스키마 밖 값을 생성하지 못한다.
- evidence(원문 근거 문장)를 필수로 받아, 코드에서 '본문에 실제로 있는 문장인지' 검증한다.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

EntityClass = Literal["Job", "Skill", "Equipment", "Consumable", "Item", "Monster", "Boss",
                      "Map", "Quest", "NPC", "Event"]
ChangeKind = Literal["Buff", "Nerf", "Added", "Removed", "Adjusted"]
Predicate = Literal["hasSkill", "dropsItem", "appearsIn", "rewards", "usableBy", "givenBy"]


class ChangeRecord(BaseModel):
    """패치가 어떤 개체에 가한 변경 1건 → maple:PatchChange."""
    target_name: str = Field(description="변경 대상 이름. 본문에 나온 표기 그대로")
    target_class: EntityClass
    related_job: Optional[str] = Field(default=None, description="스킬이면 소속 직업명(본문에서 확인될 때만)")
    change_type: ChangeKind
    attribute: Optional[str] = Field(default=None, description="변경된 속성 예: 데미지, 재사용 대기시간, 확률")
    before_value: Optional[str] = Field(default=None, description="변경 전 값. 본문에 있는 경우만")
    after_value: Optional[str] = Field(default=None, description="변경 후 값. 본문에 있는 경우만")
    evidence: str = Field(description="근거가 되는 본문 문장을 한 글자도 바꾸지 말고 그대로 복사")


class RelationRecord(BaseModel):
    """개체 사이의 관계 1건 → 직접 엣지 + Fact."""
    subject_name: str
    subject_class: EntityClass
    predicate: Predicate
    object_name: str
    object_class: EntityClass
    evidence: str = Field(description="근거가 되는 본문 문장을 그대로 복사")


class ChunkExtraction(BaseModel):
    changes: list[ChangeRecord] = Field(default_factory=list)
    relations: list[RelationRecord] = Field(default_factory=list)
