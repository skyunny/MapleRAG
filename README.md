# MapleRAG — 메이플스토리 게임 정보 질의응답 (RDF 지식그래프 + Neo4j/n10s + LangGraph + Langfuse)

수업 노트북(`D04_2_Regulations_RAG_Pipeline[Lab].ipynb`)의 **벡터DB(Milvus) RAG** 를
**RDF 트리플 온톨로지 지식그래프(Neo4j + neosemantics)** 기반 RAG 로 바꾸고, 전 과정을 **Langfuse** 로 관찰하는 학습용 프로젝트입니다.
임베딩·벡터DB 는 쓰지 않습니다. 질문 → Text2Cypher → 그래프 조회 → **근거 트리플·출처가 붙은 답변** 구조입니다.

> 데이터 출처: **Data based on NEXON Open API** (넥슨 Open API 데이터 사용 표기 의무)

---

## 1. 한눈에 보기

```
공식 Open API ──▶ RawDocument ──▶ 규칙 매퍼(API JSON, LLM 없음)  ─┐
 (공지·이벤트)        (출처·tier)    LLM 추출(비정형 본문, 구조화 출력) ─┼▶ 정규화·중복제거 ─▶ SHACL 검증 ─▶ RDF(.ttl)
                                     + 코드 검증(근거 문장 실재 확인)   ─┘                                  │
                                                                                            n10s 멱등 적재 ▼
질문 ─▶ analyze_question ─▶ link_entities ─▶ generate_cypher ─▶ execute_query(가드·읽기전용·재시도≤2) ─▶ generate_answer
          (범위 밖이면 거절)   (라벨·별칭 매칭)   (Text2Cypher)       Neo4j                              (근거 트리플·출처 URL)
                                          └──────────────── 모든 노드·LLM 호출이 Langfuse 트레이스로 기록 ────────────────┘
```

| 수업 노트북 (벡터 RAG) | 이 프로젝트 (그래프 RAG) |
|---|---|
| 문서 로드·청킹 | `ingest/` (Open API 수집) + `extract/sections.py` (■ 소제목 단위 청크) |
| 임베딩 + Milvus 적재 | `extract/normalizer.py` (트리플화) + `graph/load_n10s.py` (n10s 적재) |
| 유사도 검색 | `rag/pipeline.py` 의 `link_entities` + Text2Cypher |
| 답변 생성 | `generate_answer` (근거 행 번호 인용 → 코드가 근거 트리플 생성) |
| 데이터 품질 확인 | `graph/validate.py` (SHACL 위반 건수) |

## 2. 빠른 시작

사전 준비: Docker, [uv](https://docs.astral.sh/uv/), NEXON Open API 키, Anthropic API 키, Langfuse 프로젝트(클라우드 무료 플랜 가능).

```bash
cp .env.example .env          # 값을 채운다 (키는 이 파일에만. .env 는 커밋 금지)
docker compose up -d          # Neo4j 5.26 (+ n10s 플러그인 jar 는 data/neo4j/plugins 에 직접 넣어야 함, 아래 참고)
uv sync
uv run maplerag               # 키 설정 여부(값은 출력 안 함) + Neo4j 연결·n10s 확인
```

- Neo4j Browser: <http://localhost:7475> (Connect URL `bolt://localhost:7688`, 다른 프로젝트와 포트가 겹쳐 7475/7688 로 바꿨습니다)
- **n10s**: `NEO4J_PLUGINS=["n10s"]` 자동 설치는 지원되지 않아, `neosemantics-5.26.0.jar` 를 `data/neo4j/plugins/` 에 직접 내려받아 마운트합니다.
- Windows 에서 한글 출력이 깨지면 `PYTHONIOENCODING=utf-8` 를 앞에 붙이세요.

### 챗 UI 실행

```bash
uv run maplerag-ui                       # http://localhost:8501 (다른 포트: uv run maplerag-ui --server.port 8601)
```

- **게임 정보 질문**: 채팅 형태로 질문하면 노드별 진행 상황이 보이고, 답변 아래에 **근거 트리플 표 · 출처 URL · 실행된 Cypher(시도별 오류/행 수) · Langfuse 트레이스 링크**가 붙습니다. 상태 배지: 답변 완료 / 근거 없음 / 범위 밖 질문 / 조회 실패.
- **캐릭터 조회**: 닉네임(한글·영문·숫자 1~12자 검증) → 기본 정보·스타포스·잠재능력·장비. LLM 을 쓰지 않고 저장하지 않습니다.
- **비용 가드**: 질문 1개당 모델 호출 약 3회(≈ $0.02, 추정)라 브라우저 세션당 질문 수를 제한합니다(기본 15개, 환경변수 `UI_MAX_QUESTIONS`). 한도에 도달하면 입력창이 비활성화되고, 사이드바에 남은 질문 수와 누적 추정 비용이 표시됩니다. 입력은 300자로 자릅니다.
- 오류(크레딧 부족·네트워크 등)는 화면에 짧은 메시지로 바꿔 보여 주며 앱이 죽지 않습니다.
- 인증이 없는 단일 사용자용 데모입니다. 외부에 공개하면 누구나 본인 크레딧으로 질문하게 되고 세션 한도는 새로고침으로 초기화되므로, 공개 배포 전에 인증과 서버측 호출 제한이 필요합니다.

### 파이프라인 실행 순서

```bash
uv run python -m maplerag.ingest.run_notices          # 1. 수집 (공식 API, 일 1,000회 한도)
uv run python -m maplerag.extract.run_batch           # 2. LLM 추출 (유료, 캐시 재사용)
uv run python -m maplerag.extract.manual_supplement   #    (선택) 수동 보충 청크
uv run python -m maplerag.extract.run_normalize       # 3. 정규화 → RDF → SHACL 검증
uv run python -m maplerag.graph.load_n10s             # 4. Neo4j 적재 (멱등. 전체 재적재는 --drop-old, 확인 프롬프트)
uv run python -m maplerag.rag.run "소울 어썰트 데미지 변화는?"   # 5. 질문 1개 (모델 호출 약 3회)
uv run python -m maplerag.character.run 닉네임 "스타포스 높은 장비는?"   # 캐릭터 조회 (LLM 없음)
uv run python -m maplerag.evaluation.run_eval --upload --dry-run --all   # 평가 데이터셋 등록·비용 추정(호출 없음)
uv run python -m maplerag.evaluation.run_eval --run --ids P03,E06,O01     # 지정 문항만 실행
```

노트북: `notebooks/01_build_kg.ipynb`, `notebooks/02_rag_demo.ipynb` (얇은 실행·점검용, `RUN_LLM=False` 가 기본이라 유료 호출이 없습니다. 생성 스크립트 `notebooks/make_notebooks.py`).

## 3. 현재 결과 (실측)

| 항목 | 값 |
|---|---|
| 수집 | 공식 Open API 공지 53건 (패치노트 20건, 2026-01-20 ~ 2026-10-22 / 이벤트·공지 등) |
| LLM 추출 | 217청크 중 **195 성공 / 22 실패(크레딧 부족)**. 변경 3,129건, 관계 231건, 코드 검증 폐기 33건(1.0%) |
| 수동 보충 | 실패 청크 1개(유피테르 구간)에서 변경 16·관계 2건만 직접 기입 (`extractedBy="manual"`) |
| RDF | 공지+추출 **81,093 트리플**, PatchChange 3,145건 (전부 출처·패치·근거 문장 보유) |
| SHACL | **conforms=True, 위반 0건** (완화한 규칙은 아래 한계 참고) |
| Neo4j | 노드 7,169 / 관계 43,138 — 재적재해도 불변(멱등) |
| 평가 질문 | 36개 (패치 변경 13, 개체 5, 패치 정보 2, 이벤트 4, 자료 없음 5, 범위 밖 7). 정답이 그래프에 있음을 모델 없이 **24/24 확인** |
| 평가 실행 | **3문항 표본만 실행**(전부 통과, 약 $0.066). **전체 36문항은 미실행** → 성능 수치로 쓸 수 없음 |
| 테스트 | `uv run python tests/run_all.py` → 11개 스크립트 전체 통과 (모델 호출 없음) |

## 4. 데이터 정책과 의무

- **공식 출처만** 수집합니다: NEXON Open API. `maplestory.nexon.com` 은 robots.txt 상 `/Guide/N23GameInformation`, `/Guide/N23Job` 만 허용인데 **홈페이지는 한 페이지도 수집하지 않았고 이용약관도 읽지 않았습니다.**
- 비공식 출처(위키·커뮤니티)는 **수집하지 않습니다.** 사용자가 직접 저장한 파일을 넣는 수집기 인터페이스만 있고, 모든 트리플에 `sourceTier`(official 3 / userCurated 2 / community 1)가 붙어 충돌 시 공식이 우선합니다.
- **표기 의무**: 서비스 화면·문서에 `Data based on NEXON Open API` 를 표시해야 합니다. 응답 객체(`CharacterAnswer.attribution`)에 포함돼 있습니다.
- **갱신 의무**: 넥슨 약관상 수집한 데이터는 **30일 이내에 갱신**해야 합니다. 이 프로젝트는 자동 갱신 스케줄이 없으므로 `run_notices` → 추출 → 적재를 사람이 주기적으로 실행해야 합니다. 방치하면 약관 위반 상태가 됩니다.
- 개발용 API 키 한도: 초당 5회 · 일 1,000회. 데이터는 약 15분 지연될 수 있습니다.
- 이 정책 내용은 공식 문서에서 확인한 것이지만, 법률 검토를 거친 것은 아닙니다. 공개 서비스 전에 약관 전문을 직접 확인하세요.

## 5. 온톨로지·그래프 설계 요약

- 네임스페이스 `https://example.org/maple#`, 개체 URI `https://example.org/maple/id/{종류}/{공식 ID | h-해시}` (한글은 URI 에 쓰지 않고 `rdfs:label`, 별칭은 `skos:altLabel`).
- 클래스: Job, Skill, Item(Equipment·Consumable), Monster(Boss), Map, Quest, NPC, Event, Patch, SourceDocument, TimeInterval, Probability 계열 등.
- **시간·출처**: 직접 엣지 + `maple:Fact`(rdf:Statement 하위) / `maple:PatchChange` 노드에 `validFrom`(패치 적용일), `inPatch`, `prov:wasDerivedFrom`, `sourceTier`, `evidence`(원문 근거 문장), `extractedBy`(rule/llm/manual) 를 기록합니다.
- 캐릭터·길드는 **영구 저장하지 않고** 메모리 임시 그래프로만 다룹니다.
- n10s 설정: `handleVocabUris=SHORTEN`, `handleRDFTypes=LABELS`, `handleMultival=ARRAY`(altLabel 만), `keepLangTag=false`. 속성·라벨 이름은 `maple__PatchChange`, `rdfs__label` 형태입니다. **Graph Config 는 적재 후 변경할 수 없어** 바꾸려면 전체 삭제 후 재적재해야 합니다.
- 주의: n10s 는 **단일값 속성에 값이 둘 이상이면 하나로 덮어씁니다.** 적재 순서에 따라 결과가 달라질 수 있어, 정규화 단계에서 같은 (노드, 속성)에 값이 둘 이상 생기지 않게 하고(`tests/check_normalizer.py`) 적재 시 트리플을 정렬합니다.

## 6. 안전장치

- **Cypher 가드** (`rag/guard.py`): 읽기 전용(`MATCH/WITH/UNWIND/RETURN` 시작), 단일 문장, `CREATE/MERGE/SET/DELETE/CALL/LOAD/…` 및 `OPTIONAL MATCH` 차단, `LIMIT` 자동 추가(최대 100). 실행은 읽기 전용 세션 + 10초 서버 타임아웃. 가드·쿼리 오류는 사유를 모델에 돌려주고 **최대 2회 재시도**합니다.
- **환각 방어**: 추출 단계에서 (근거 문장이 본문에 실제로 있는지, 값이 근거에 있는지) 코드로 검사해 버립니다. 답변의 근거 트리플은 LLM 이 쓰지 않고 **인용된 조회 행에서 코드가 만듭니다.**
- **비밀값**: `.env` 에만 두고 출력·커밋하지 않습니다(`.gitignore`: `.env`, `data/raw/`, `data/neo4j/`). `uv run maplerag` 는 키의 설정 여부만 보여 줍니다.
- **개인정보**: 캐릭터 조회는 ocid 를 응답·로그·URL 기록에 노출하지 않습니다. 단 **원본 API 응답은 `data/raw/` 디스크 캐시에 남으므로**(재호출 방지용, gitignore 대상) 커밋하면 안 됩니다.
- **파괴적 작업**: 그래프 전체 삭제(`--drop-old`)는 `yes` 입력을 요구합니다.

## 7. Langfuse 관찰

- 질문 1개가 **노드별 span**(`analyze_question → link_entities → generate_cypher → execute_query → generate_answer`)과 LLM **generation**(모델·토큰·비용)으로 한 트레이스에 남습니다. `session_id`, 익명 `user_id`(무작위), 태그(`maplerag`, `model:…`)가 붙습니다.
- 프롬프트 4종을 Prompt Management 에 등록(`maple-extract-changes`, `maple-analyze-question`, `maple-text2cypher`, `maple-answer`)하고, generation 이 사용한 **프롬프트 버전에 연결**됩니다. Langfuse 에 접속하지 못하면 코드의 기본 프롬프트로 동작합니다.
- 평가: Langfuse 데이터셋 `maple-rag-eval-v1` + 실험 run. run 이름 규칙 `{날짜-시각}_{모델}_p{분석.Cypher.답변 프롬프트 버전}_n{문항수}_{라벨}`. 지표: Cypher 성공률(첫 시도 포함), 답변 포함률, 근거 트리플 일치율, 범위 밖 거절률, 자료 없음 기권률, 지연(평균·p95), 토큰, 비용(추정).
- 중복 기록 주의: Anthropic SDK 의 자체 OTel span 이 같은 호출을 한 번 더 기록해 비용이 2배로 보이는 문제가 있어, Langfuse SDK span 만 내보내도록 필터를 달았습니다(`rag/tracing.py`).

## 8. 한계 (정직한 기록)

**데이터 범위**
- 최신 패치노트 **20건**(약 9개월)뿐입니다. API 가 목록 엔드포인트에서 최근 20건만 돌려줍니다. 그 이전 이력은 없습니다.
- **도감(직업·몬스터 전체), 확률표(스타포스·큐브), 드롭 테이블은 사실상 없습니다.** 드롭·출현·퀘스트 관계는 합쳐서 수 건입니다. 이런 질문은 `no_evidence` 로 끝나는 것이 정상 동작입니다.
- 22청크(1.2.412~1.2.413대 이벤트 안내)는 크레딧 부족으로 추출하지 못했고, 그중 유피테르 구간만 수동 보충했습니다. 나머지는 이벤트 보상표·상점 목록 등으로 가치가 낮다고 판단했습니다(전부 읽어 본 것은 아님).
- 이벤트 공지 일부는 이미지 전용이라 보상 정보가 비어 있습니다. 패치노트 본문에서 추출한 이벤트 56개 중 **기간 정보가 있는 것은 API 이벤트 13개뿐**이라, SHACL 의 `Event.during` 필수 조건을 `maxCount 1` 로 **완화**했습니다.
- 확률표는 현재 추출 스키마(`ChangeRecord`/`RelationRecord`)로 표현할 수 없어 포함하지 못했습니다(예: '숲의 근원' 등급 확률).

**그래프 품질**
- 같은 이름의 스킬이 **직업별로 수십 개 노드**입니다(에르다 샤워 47개, 태초의 결정 34개). 직업을 알 수 없는 변경은 이름만으로 합쳐져 서로 다른 직업의 변경이 한 노드에 섞일 수 있습니다. 아이템도 이름이 같으면 한 개체가 됩니다.
- `hasSkill`(직업→스킬) **1,344개는 패치노트 소제목 문맥으로 추정한 값**이고 정확도를 따로 검증하지 않았습니다. 근거 문장은 스킬 변경 문장입니다.
- `Adjusted`(방향 불명) 판정과 표(table)를 평문으로 펼친 입력의 정보 손실이 있습니다. 한 패치(1.2.419)가 변경의 54%를 차지해 패치 간 균형이 맞지 않습니다.
- `validTo`(유효 종료일)는 비어 있습니다. 후속 패치가 같은 값을 바꿨는지 알 수 없어 "그 시점에 유효했던 값" 질문은 `validFrom` 기준으로만 답합니다.
- `Item/Equipment/Consumable`, `Monster/Boss` 는 하위 클래스 추론이 없어 라벨이 서로 별개입니다(프롬프트에서 고려하도록 안내).

**RAG·평가**
- **전체 평가는 36문항 중 3문항만 실행**했습니다(전부 통과, 표본이라 일반화 불가). Cypher 성공률 등 전체 수치는 아직 없습니다.
- 변경이 50건을 넘는 질문(예: 에르다 샤워 62건)은 `LIMIT 50` 에서 잘려 일부 직업이 빠질 수 있습니다. 답에서 "직업별"로 말할 때 행과 직업의 짝이 틀릴 위험이 있어 근거 행을 확인해야 합니다.
- 평가의 `answer_contains` 는 문자열 포함 기준이라 의미가 맞아도 표기가 다르면 실패로 보일 수 있습니다.
- 캐릭터 조회는 키워드로 질문 유형 4가지(기본·스타포스·잠재능력·장비)만 구분하는 **규칙 기반**이며, RAG 와 자동 라우팅되지 않습니다. 개인 조회 질문은 RAG 에서 거절합니다.
- **챗 UI 에서 실제 모델로 질문을 보내는 경로는 브라우저로 확인하지 않았습니다.** 화면·캐릭터 조회는 브라우저로, 질문 흐름은 가짜 서비스로 자동 테스트했습니다(모델 호출 비용 때문).
- 노트북의 코드 셀은 `tests/check_notebooks.py` 로 `RUN_LLM=False` 상태에서만 실행 검증했습니다.

**비용·수치의 신뢰도**
- 토큰 단가($2 / $10 per 1M)는 스킬 문서의 캐시된 가격표이며 **실제 청구액과 다를 수 있습니다.** 추출 약 $8.29, 질문 1개 약 $0.02 는 이 단가로 계산한 추정치입니다.
- 지금까지 지출한 모델 호출은 추출 195청크, 시범 실행 몇 회, 3문항 평가가 전부입니다.

## 9. 저장소 구조

```
src/maplerag/
  config.py              환경변수 로드 (키는 존재 여부만 노출)
  ingest/                수집 (Open API 클라이언트, 캐시, throttle, 캐릭터 수집기)
  extract/               규칙 매퍼 · LLM 추출 · 정규화 · 수동 보충
  graph/                 SHACL 검증 · n10s 적재 · Neo4j 연결
  rag/                   LangGraph 파이프라인 · 가드 · 프롬프트(+Langfuse 등록) · 트레이싱 · 진입점
  ui/                    Streamlit 챗 UI (app.py, 표시 로직 view.py)
  character/             캐릭터 조회 경로 (임시 그래프 + SPARQL)
  evaluation/            질문 로드·정답 검증 · 지표 · 실험 실행기
ontology/                maple.ttl (온톨로지), shapes.ttl (SHACL)
eval/questions.jsonl     평가 질문 36개 (코드와 분리, 사람이 검토·수정)
notebooks/               01_build_kg, 02_rag_demo (얇은 노트북)
tests/                   check_*.py (모델 호출 없음) + run_all.py
data/rdf/                생성된 RDF (공지·추출).   data/raw/ 는 수집 원본·캐시(커밋 금지)
```

## 10. 테스트

```bash
uv run python tests/run_all.py     # 온톨로지·SHACL, 수집 모킹, 규칙 매퍼, 추출 검증, 정규화 결정성, 가드, 평가 지표, 캐릭터, 파이프라인(가짜 LLM), 노트북, 챗 UI(Streamlit AppTest)
```

Neo4j 가 떠 있어야 하는 테스트가 있습니다. 캐릭터 테스트는 수집해 둔 샘플이 없으면 건너뜁니다(개인정보라 저장소에 없음).
