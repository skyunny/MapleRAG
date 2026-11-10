"""추출 검증 로직 테스트 (LLM 호출 없음). 실행: uv run python tests/check_extractor.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from maplerag.extract.llm_extractor import verify  # noqa: E402
from maplerag.extract.schemas import ChangeRecord, ChunkExtraction, RelationRecord  # noqa: E402
from maplerag.extract.sections import Chunk  # noqa: E402

TEXT = ("■ 데모직업\n- 데모베기의 데미지가 100%에서 120%로 증가합니다.\n"
        "- 행동 불가 상태 이상의 저항 시간이 90초에서 81초로 감소됩니다.")
chunk = Chunk(1, "데모 패치", "2026-01-01", ["데모직업"], TEXT)


def ch(**kw):
    base = dict(target_name="데모베기", target_class="Skill", change_type="Buff", attribute="데미지",
                before_value="100%", after_value="120%", evidence="데모베기의 데미지가 100%에서 120%로 증가합니다.")
    base.update(kw)
    return ChangeRecord(**base)


ex = ChunkExtraction(
    changes=[
        ch(),                                                              # 정상
        ch(evidence="데모베기의 데미지가 100%에서 200%로 증가합니다."),            # 환각 문장
        ch(after_value="130%"),                                            # 값이 evidence 에 없음
        ch(target_name="없는스킬"),                                          # 대상 이름이 본문에 없음
        ch(evidence="데모베기의  데미지가   100%에서 120%로 증가합니다."),         # 공백만 다름 → 통과해야 함
    ],
    relations=[
        RelationRecord(subject_name="데모직업", subject_class="Job", predicate="hasSkill",
                       object_name="데모베기", object_class="Skill",
                       evidence="데모베기의 데미지가 100%에서 120%로 증가합니다."),     # 정상
        RelationRecord(subject_name="데모직업", subject_class="Job", predicate="hasSkill",
                       object_name="유령스킬", object_class="Skill", evidence="유령스킬 추가"),  # 환각
    ])
kc, kr, dropped = verify(chunk, ex)
print(f"변경 유지 {len(kc)}/5 (기대 2), 관계 유지 {len(kr)}/2 (기대 1), 폐기 {len(dropped)} (기대 4)")
for d in dropped:
    print("  폐기:", d.kind, "|", d.reason)

# 스키마 밖 값은 Pydantic 단계에서 거부되어야 한다
try:
    ch(change_type="Weird")
    rejected = False
except Exception:
    rejected = True
try:
    RelationRecord(subject_name="a", subject_class="Job", predicate="invented", object_name="b",
                   object_class="Skill", evidence="x")
    rejected2 = False
except Exception:
    rejected2 = True
print("스키마 밖 change_type 거부:", rejected, "| 스키마 밖 관계 거부:", rejected2)
ok = len(kc) == 2 and len(kr) == 1 and len(dropped) == 4 and rejected and rejected2
print("결과:", "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
