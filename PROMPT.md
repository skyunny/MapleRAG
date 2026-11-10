# 메이플스토리 온톨로지 지식그래프 RAG 구축 프롬프트

> 아래 `---` 이하 전체를 Claude Code 첫 메시지로 붙여넣으세요.
> 참고 파일 `D04_2_Regulations_RAG_Pipeline[Lab].ipynb`는 `@`로 함께 첨부합니다.

---

## 역할
너는 지식그래프와 RAG 파이프라인을 설계하고 구현하는 시니어 AI 엔지니어야.
나는 AI 에이전트 개발자 양성과정 수강생이고, 코드를 이해하며 따라가야 해. 그래서 **왜 그렇게 설계했는지**를 짧게 설명하면서 단계별로 진행해줘.

## 목표
수업에서 만든 "벡터DB(Milvus) 기반 연구행정 규정 RAG"를 참고하되, **벡터DB를 쓰지 않고 RDF triple 기반 온톨로지 지식그래프(Neo4j)로** "메이플스토리 게임 정보 질의응답 RAG"를 만든다. 전체 과정을 LLMOps(Langfuse)로 관측한다.

## 참고 노트북에서 가져올 것과 바꿀 것
| 수업 노트북 | 이번 프로젝트 |
|---|---|
| HWP 로더 → `LegalArticleSplitter` → 임베딩 → Milvus | 소스 수집기 → **트리플 추출기(Claude)** → **RDF 검증/정규화** → Neo4j(n10s) |
| `retriever`(유사도 검색) | **엔티티 링킹 + Text2Cypher(그래프 질의) 검색** |
| LCEL 단일 체인 (`retriever \| prompt \| llm`) | **LangGraph** 상태 그래프 (분석 → 링킹 → 질의 생성 → 실행 → 검증 → 답변) |
| 출처 반환 (`source_docs`) | 답변에 쓴 **트리플과 원본 출처(URL·문서·패치 버전)** 반환 |
| "문서에 없으면 찾을 수 없다고 답변" 규칙 | **그대로 유지** (그래프에 없으면 추측 금지) |

노트북에서 그대로 따를 습관: `.env` 로 키 관리, 단계마다 건수·샘플을 출력해 검증, 실패 목록을 따로 보고, 중복 제거와 품질 필터, 정답 없는 질문(범위 밖)으로 테스트.

## 기술 스택 (확정)
- LLM: `ChatAnthropic(model="claude-sonnet-5-5")` — 트리플 추출, Text2Cypher, 답변 생성 모두 사용. (노트북 주석처럼 `temperature` 등 샘플링 파라미터는 지정하지 않는다.)
- 오케스트레이션: LangChain + **LangGraph**
- 그래프DB: **Neo4j + n10s(neosemantics) 플러그인** (Docker). RDF/OWL 임포트, SHACL 검증 사용.
- 트리플 생성/검증: `rdflib`(필요하면 `pyshacl`)
- LLMOps: **Langfuse** (트레이싱, 프롬프트 관리, 평가 데이터셋). 클라우드 무료 플랜을 기본으로 하되 `LANGFUSE_HOST` 환경변수로 셀프호스팅 전환 가능하게 한다.
- 패키지 관리: `uv` (이미 `pyproject.toml`, `.python-version` 존재. 기존 설정을 먼저 읽고 이어서 사용)
- 벡터DB와 임베딩 모델은 **사용하지 않는다.**

## 데이터 범위
다음 네 영역을 모두 포함한다.
1. **게임 도감**: 직업, 스킬, 아이템/장비, 몬스터, 보스, 맵, 퀘스트
2. **패치노트·이벤트 이력**: 패치 버전, 날짜, 변경 사항(버프/너프/신규/삭제), 이벤트 기간과 보상 (시간축 관계로 연결)
3. **캐릭터 조회**: NEXON Open API로 캐릭터 기본정보·능력치·장비·유니온·길드 등 (사용자가 입력한 닉네임 기준)
4. **심화 정보**: 강화/스타포스/큐브 확률, 보스 난이도·패턴·드롭

### 수집 정책 (중요)
- **1단계는 공식 소스만 사용한다**: NEXON Open API, 메이플스토리 공식 홈페이지의 패치노트·공지·이벤트·확률 공개 페이지.
- 나무위키, 메이플 인벤 등 비공식 문서는 **이번 작업에서 크롤링하지 않는다.** 대신 나중에 `data/raw/unofficial/` 에 사용자가 직접 저장한 파일을 같은 파이프라인에 넣을 수 있도록 **수집기 인터페이스만 열어두고**, 소스 신뢰도(`source_tier`: official / user-curated / community)를 모든 트리플에 기록한다. 공식과 비공식 정보가 충돌하면 공식을 우선한다.
- 공식 사이트 수집 시: `robots.txt` 확인, 요청 간 지연, User-Agent 명시, 원본 응답 캐시(`data/raw/`)로 재요청 최소화. 이용약관상 문제가 의심되면 수집 전에 나에게 물어본다.
- **Open API 주의**: 키는 `.env` 의 `NEXON_API_KEY`(헤더 `x-nxopen-api-key`)로 읽는다. 키 값을 코드·로그·출력에 노출하지 않는다. 엔드포인트·파라미터·호출 제한은 **공식 문서를 먼저 확인**하고 구현하고, 기억에 의존해서 추측하지 않는다. Open API는 게임 전체 도감(아이템·몬스터·맵)을 제공하지 않으므로, 도감 정보는 공식 홈페이지의 가이드·공지에서 얻을 수 있는 범위로 한정하고 **부족한 범위는 보고서에 명시**한다.

## 온톨로지 설계 요구사항
- 먼저 `ontology/maple.ttl` 로 **스키마(클래스·속성)를 설계하고 나의 확인을 받은 뒤** 데이터 적재로 넘어간다.
- 네임스페이스 예: `maple:` (`https://example.org/maple#`), 가능하면 `rdfs`, `owl`, `skos`(별칭), `prov`(출처), `xsd` 를 재사용.
- 핵심 클래스 초안 (검토 후 조정): `Job`, `Skill`, `Item`(하위: `Equipment`, `Consumable`), `Monster`, `Boss`, `Map`, `Quest`, `NPC`, `Patch`, `Event`, `Character`, `Guild`, `Union`, `Probability`(강화/큐브)
- 핵심 관계 초안: `Job -hasSkill-> Skill`, `Monster -dropsItem-> Item`, `Boss -appearsIn-> Map`, `Quest -rewards-> Item`, `Patch -changes-> (Skill|Item|Boss …)` (변경 종류·전후 값 포함), `Event -during-> TimeInterval`, `Character -hasJob-> Job`, `Character -equips-> Item`
- **별칭/약어 처리**: 한국어 게임 용어는 별칭이 많다(예: 약칭, 영문명, 구명칭). `skos:altLabel` 로 저장해 엔티티 링킹에 사용한다.
- **시간·버전 처리**: 수치는 패치에 따라 바뀐다. 값 자체를 덮어쓰지 말고 `validFrom / validTo / patchVersion` 을 붙여 "현재 기준"과 "과거 기준"을 모두 질의할 수 있게 한다. (n10s에서 reification 또는 named graph 중 어떤 방식이 적합한지 장단점을 비교해서 추천하고 나에게 확인)
- **출처(provenance)**: 모든 트리플은 `prov:wasDerivedFrom`(URL/문서/API 엔드포인트), `source_tier`, 수집 시각을 추적할 수 있어야 한다.
- 고유 URI 규칙을 정한다: 한글 이름 그대로가 아니라 안정적인 ID(가능하면 공식 ID, 없으면 슬러그+타입)를 쓰고, 이름은 `rdfs:label` 로 둔다.
- SHACL 셰이프로 필수 속성, 타입, 값 범위를 검증하고 위반 건수를 보고한다.

## 파이프라인 단계
1. **환경**: Docker Compose 로 Neo4j(+n10s) 구성, `.env.example` 작성(키 이름만, 값은 비움), 연결 확인.
2. **수집(ingest)**: 소스별 수집기 → `data/raw/` 저장 → 공통 `RawDocument`(source, url, fetched_at, text, tier) 로 정규화. 실패 목록 별도 보고.
3. **구조화 데이터 → 트리플**: Open API JSON은 LLM 없이 **규칙 기반 매핑**으로 변환(비용·정확도 때문).
4. **비정형 텍스트 → 트리플**: 패치노트·공지 본문은 Claude 로 추출. 온톨로지에 정의된 클래스·관계만 쓰도록 **구조화 출력(Pydantic 스키마)** 을 강제하고, 추출 근거 문장(evidence)을 함께 저장한다. 스키마에 없는 관계를 만들어내면 버린다. 추출 프롬프트는 Langfuse 프롬프트 관리에 등록한다.
5. **정규화·검증**: 엔티티 중복 병합(별칭 기반), 중복 트리플 제거, SHACL 검증, 통계 출력(클래스별 노드 수, 관계별 엣지 수).
6. **적재**: n10s 로 Neo4j 에 임포트. 재실행해도 중복되지 않게 **멱등성** 보장. `drop_old` 같은 초기화 옵션은 노트북처럼 명시적으로 제공.
7. **RAG 그래프(LangGraph)**: 아래 설계를 따른다.
8. **평가**: Langfuse 데이터셋 + 자동 평가.

### LangGraph 설계 (초안, 개선 제안 환영)
상태: `question, intent, entities, cypher, rows, attempts, answer, sources`
노드:
1. `analyze_question` — 질문 유형 분류(도감 / 패치 이력 / 캐릭터 / 확률 / 범위 밖). **범위 밖이면 즉시 "제공된 데이터에서 찾을 수 없습니다" 응답.**
2. `link_entities` — 질문 속 용어를 `rdfs:label`/`skos:altLabel` 로 그래프에서 찾아 URI 로 연결. 모호하면 후보를 되물음.
3. `generate_cypher` — 온톨로지 스키마와 링킹 결과를 프롬프트에 주입해 Text2Cypher 생성. **읽기 전용 쿼리만 허용**(`CREATE/MERGE/DELETE/SET/CALL` 차단, 결과 행 수 `LIMIT`, 타임아웃).
4. `execute_query` — Neo4j 실행. 오류나 빈 결과면 `generate_cypher` 로 되돌아가 **최대 2회 재시도**(오류 메시지 피드백).
5. `generate_answer` — 결과 행과 출처만 근거로 답변. 수치·패치 버전·날짜는 정확히 인용하고, 근거 트리플과 출처 URL 을 함께 반환. `stream()` 지원.

캐릭터 조회형 질문은 그래프에 없는 최신 정보일 수 있으므로 닉네임이 주어지면 Open API 호출 → 임시 트리플화 → 질의하는 경로를 별도로 설계하고, 개인정보·API 호출 제한 측면의 주의점을 정리해줘.

## Langfuse 요구사항
- `langfuse.langchain.CallbackHandler`(또는 현재 권장 방식을 공식 문서로 확인)를 LangGraph 실행에 연결해 **노드별 트레이스**가 보이게 한다.
- 모든 트레이스에 `session_id`, `user_id`(익명), `tags`(예: `intent`, `graph_version`) 부여.
- 추출·Text2Cypher·답변 프롬프트를 Langfuse **Prompt Management** 로 버전 관리.
- **평가 데이터셋**: 질문-정답(또는 기대 Cypher/기대 엔티티) 30문항 이상. 유형별(도감·패치·확률·범위 밖) 균형. 정답은 내가 검수할 수 있게 별도 파일로 제공.
- 자동 평가 지표: Cypher 실행 성공률, 정답 포함 여부, 근거 트리플 일치, 범위 밖 질문 거절률, 지연 시간·토큰·비용.
- 개선 전/후를 비교할 수 있도록 실험(run) 이름 규칙을 정한다.
- Langfuse 키는 `.env` 의 `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`.

## 프로젝트 구조 (제안, 기존 `src/` 확인 후 조정)
```
MapleRAG/
├─ docker-compose.yml          # Neo4j + n10s
├─ .env.example
├─ ontology/   maple.ttl, shapes.ttl
├─ data/raw/ , data/rdf/       # 수집 원본, 생성된 TTL
├─ src/maple_rag/
│  ├─ config.py
│  ├─ ingest/      (nexon_api.py, official_site.py, base.py)
│  ├─ extract/     (rule_mapper.py, llm_extractor.py, schemas.py)
│  ├─ graph/       (normalize.py, validate.py, loader.py, neo4j_client.py)
│  ├─ rag/         (state.py, nodes.py, workflow.py, cypher_guard.py)
│  └─ observability/ (langfuse_setup.py, evaluate.py)
├─ eval/ questions.jsonl
├─ notebooks/  01_build_kg.ipynb, 02_rag_demo.ipynb   # 얇게: 호출과 결과 확인만
└─ tests/
```
노트북은 수업 노트북처럼 마크다운 설명 + 실행 셀로 구성하되, 로직은 `src/` 모듈에 둔다.

## 진행 방식 (반드시 지켜줘)
1. **먼저 계획만 제시**하고 내 승인을 받은 뒤 구현한다. 계획에는 단계, 각 단계 산출물, 예상 비용(LLM 호출량), 위험 요소를 포함한다.
2. **단계가 끝날 때마다 멈추고** 건수·샘플·검증 결과를 보여준 뒤 다음 단계로 넘어갈지 묻는다. 한 번에 전부 만들지 않는다.
3. 아래는 **사전에 나에게 확인**한다: 온톨로지 스키마 확정, 트리플 시간 표현 방식, 수집 대상 URL 목록, LLM 일괄 추출 전 샘플 10건 결과, 대량 API/LLM 호출(예상 비용 제시 후).
4. 모르는 것이나 공식 문서로 확인이 필요한 것(Open API 스펙, n10s 설정, Langfuse SDK 버전별 사용법)은 **추측하지 말고 문서를 찾아 확인**하거나 나에게 물어본다.
5. 비밀값(API 키, 비밀번호)은 `.env` 에만 두고 커밋하지 않는다. `.gitignore` 에 `.env`, `data/raw/` 를 포함했는지 확인한다.
6. 파괴적 작업(DB 초기화, 파일 삭제)은 실행 전 확인한다.
7. 설명과 코드 주석은 **한국어**로 작성한다.
8. 확인하지 못한 것은 확인했다고 말하지 않는다. 테스트가 실패하면 출력과 함께 그대로 보고한다.

## 완료 기준
- [ ] `docker compose up` 으로 Neo4j+n10s 가 뜨고 연결 확인이 된다.
- [ ] 온톨로지(TTL)와 SHACL 검증이 통과하며 위반 건수가 보고된다.
- [ ] 모든 트리플에서 출처·신뢰도·(해당 시) 패치 버전을 추적할 수 있다.
- [ ] 재실행해도 그래프가 중복 증식하지 않는다.
- [ ] 질문 → 답변 + 근거 트리플 + 출처가 반환되고, 범위 밖 질문은 거절한다.
- [ ] Langfuse 에서 질문 1건의 노드별 트레이스, 프롬프트 버전, 데이터셋 평가 결과를 볼 수 있다.
- [ ] 현재 한계(도감 커버리지, 비공식 소스 미포함 등)를 README 에 정직하게 기록한다.

## 지금 할 일
위 내용을 모두 읽고, 먼저 `@D04_2_Regulations_RAG_Pipeline[Lab].ipynb` 와 현재 `src/`, `pyproject.toml`, `README.md` 를 확인해줘.
그다음 **(1) 이해한 내용 요약, (2) 불명확한 점 질문, (3) 단계별 구현 계획** 만 제시하고 멈춰줘. 구현은 내 승인 후에 시작한다.
