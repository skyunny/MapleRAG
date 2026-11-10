"""RAG 프롬프트와 그래프 스키마 설명. 8단계에서 Langfuse Prompt Management 에 같은 이름으로 등록한다({{변수}} 문법 사용).

주의: 이 그래프는 'n10s 로 적재된 RDF' 라서 속성/관계/라벨 이름이 maple__xxx, rdfs__label 형태다.
Equipment/Consumable 은 Item 의 하위 클래스지만 추론이 적용되지 않아 노드에는 하위 라벨만 붙어 있다(Item 라벨 노드와 별개).
"""

GRAPH_SCHEMA = """[노드 라벨] (모든 노드는 Resource 라벨도 가짐. 이름은 rdfs__label, 별칭 배열은 skos__altLabel(없을 수 있음))
- maple__Job(직업), maple__Skill(스킬), maple__Item / maple__Equipment(장비) / maple__Consumable(소비)  ← 서로 별개 라벨. 아이템 전반은 세 라벨을 모두 고려
- maple__Monster / maple__Boss(보스)  ← 별개 라벨, maple__Map, maple__Quest, maple__NPC, maple__Event(이벤트)
- maple__Patch(패치노트 1건): maple__patchVersion(문자열), maple__patchDate(DATE), rdfs__label(제목)
- maple__PatchChange(패치가 개체에 가한 변경 1건) 속성: maple__attribute(속성명), maple__beforeValue, maple__afterValue,
  maple__value, maple__evidence(원문 근거 문장), maple__validFrom(DATE, 적용일), maple__extractedBy('llm'|'manual'|'rule')
- maple__Fact(관계의 근거 기록): maple__evidence, maple__validFrom
- maple__SourceDocument: maple__url, maple__docTitle
- maple__TimeInterval: maple__startDate, maple__endDate (DATETIME)

[관계]
- (c:maple__PatchChange)-[:maple__changes]->(대상 개체)
- (c)-[:maple__inPatch]->(p:maple__Patch)
- (c)-[:maple__changeType]->(ct)   ← ct 는 노드이며 ct.uri 가 'https://example.org/maple#Buff' | '#Nerf' | '#Added' | '#Removed' | '#Adjusted' 중 하나
  (Buff=유저에게 유리한 상향, Nerf=하향, Added=신규, Removed=삭제/종료, Adjusted=방향 불명)
- (c)-[:prov__wasDerivedFrom]->(d:maple__SourceDocument)
- (j:maple__Job)-[:maple__hasSkill]->(s:maple__Skill)   ← 패치노트 소제목 문맥으로 추정한 값이라 신뢰도 낮음
- (e:maple__Event)-[:maple__rewards]->(아이템), (e)-[:maple__during]->(i:maple__TimeInterval)
- (f:maple__Fact)-[:rdf__subject]->(주어), (f)-[:rdf__object]->(목적어), (f)-[:maple__inPatch]->(p)
- (m:maple__Monster|maple__Boss)-[:maple__dropsItem]->(아이템), -[:maple__appearsIn]->(맵), (q:maple__Quest)-[:maple__givenBy]->(npc)

[데이터 범위]
- 공식 Open API 의 최신 패치노트 20건(2026-01 ~ 2026-10)과 이벤트 공지에서 뽑은 변경 사항뿐이다.
- 확률표, 직업/몬스터 도감, 드롭 테이블은 거의 없다(드롭·출현 관계는 합쳐서 수 건)."""

CYPHER_EXAMPLES = """[예시 1] 질문: 아크메이지(불,독) 스킬 중 가장 최근에 상향된 것은?
MATCH (j:maple__Job)-[:maple__hasSkill]->(t:maple__Skill)<-[:maple__changes]-(c:maple__PatchChange)-[:maple__changeType]->(ct),
      (c)-[:maple__inPatch]->(p:maple__Patch), (c)-[:prov__wasDerivedFrom]->(d)
WHERE j.rdfs__label CONTAINS '아크메이지' AND ct.uri = 'https://example.org/maple#Buff'
RETURN t.rdfs__label AS target, c.maple__attribute AS attribute, c.maple__beforeValue AS before, c.maple__afterValue AS after,
       'Buff' AS change_type, p.rdfs__label AS patch, toString(p.maple__patchDate) AS patch_date, c.maple__evidence AS evidence, d.maple__url AS source_url
ORDER BY p.maple__patchDate DESC LIMIT 10

[예시 2] 질문: 에르다 샤워는 어떻게 바뀌었나?
MATCH (t:maple__Skill)<-[:maple__changes]-(c:maple__PatchChange)-[:maple__changeType]->(ct),
      (c)-[:maple__inPatch]->(p:maple__Patch), (c)-[:prov__wasDerivedFrom]->(d)
WHERE t.rdfs__label = '에르다 샤워'
RETURN t.rdfs__label AS target, c.maple__attribute AS attribute, c.maple__beforeValue AS before, c.maple__afterValue AS after,
       replace(ct.uri, 'https://example.org/maple#', '') AS change_type, p.rdfs__label AS patch, toString(p.maple__patchDate) AS patch_date,
       c.maple__evidence AS evidence, d.maple__url AS source_url
ORDER BY p.maple__patchDate DESC LIMIT 20

[예시 3] 질문: 렌 레벨 달성 이벤트 보상은?   (이벤트·보상은 변경이 아니므로 evidence 컬럼이 없어도 됨)
MATCH (e:maple__Event)-[:maple__rewards]->(i)
WHERE e.rdfs__label CONTAINS '렌 레벨 달성'
RETURN e.rdfs__label AS event, i.rdfs__label AS reward LIMIT 30"""

ANALYZE_TEMPLATE = """당신은 메이플스토리 게임 정보 질의응답 시스템의 질문 분류기입니다.
이 시스템의 지식그래프가 가진 범위는 다음과 같습니다.
{{scope}}

질문을 분석해 JSON 으로 답하세요.
- in_scope: 메이플스토리 게임 정보(패치·스킬·아이템·이벤트·보스 등)에 대한 질문이면 true. 게임과 무관한 질문(날씨, 코딩, 다른 게임, 잡담)이거나,
  특정 캐릭터의 개인 정보 조회('내 캐릭터', 닉네임 스펙 확인)처럼 이 파이프라인이 다루지 않는 요청이면 false.
  그래프에 데이터가 없을 것 같은 게임 질문(예: 확률표)도 true 입니다(없다는 답은 이후 단계가 합니다).
- reason: 판단 이유 한 문장
- entity_mentions: 질문에 나온 게임 개체 이름(직업·스킬·아이템·보스·이벤트·패치 버전)을 본문 표기 그대로 최대 5개. 없으면 빈 배열.

질문: {{question}}"""

SCOPE_TEXT = ("- 공식 Open API 에서 수집한 최신 패치노트 20건(2026-01~2026-10)의 스킬/아이템/보스 등 변경 내역과 이벤트 공지의 보상 정보\n"
              "- 도감(직업·몬스터 전체), 확률표, 드롭 테이블은 거의 없음. 캐릭터 개인 정보는 이 그래프에 없음")

CYPHER_TEMPLATE = """당신은 Neo4j(Cypher 5) 전문가입니다. 아래 그래프 스키마에 맞는 **읽기 전용** Cypher 한 개를 만드세요.

{{schema}}

[규칙]
- MATCH/WHERE/RETURN/ORDER BY/LIMIT 만 사용합니다. CREATE/MERGE/SET/DELETE/CALL 은 금지입니다(실행 전 코드가 차단합니다).
- 이름 비교는 정확히 아는 표기면 =, 부분 일치는 CONTAINS 를 씁니다.
- [연결된 후보]는 label 별 요약입니다. n_nodes 가 2 이상이면 같은 이름의 노드가 여러 개(예: 직업별 동명 스킬)라는 뜻이므로,
  uri 몇 개만 골라 쓰지 말고 `t.rdfs__label = '...'` 로 **모두** 포함하세요. 질문이 직업을 지정하면 직업 조건을 더합니다.
- 같은 이름이 여러 노드면 결과가 많아지므로, 변경 질문에서는 직업명(j.rdfs__label)도 컬럼으로 함께 반환하고 LIMIT 은 50 으로 둡니다.
- 변경(패치) 질문이면 RETURN 에 다음 별칭을 반드시 포함합니다: target, attribute, before, after, change_type, patch, patch_date, evidence, source_url
- 결과가 많을 수 있으면 ORDER BY 와 LIMIT(최대 50)을 둡니다. 존재하지 않는 라벨/속성/관계를 만들지 않습니다.
- OPTIONAL MATCH 는 쓰지 않습니다(그 뒤의 WHERE 는 선택 매치에만 적용되어 개체 필터가 무시됩니다). 모든 패턴은 필수 MATCH 로 쓰고, WHERE 는 첫 MATCH 묶음 바로 뒤에 둡니다.
- 같은 이름의 스킬이 여러 직업에 있어 [연결된 후보]가 여러 개일 수 있습니다. 그때는 `t.uri IN [...]` 로 모두 포함하고, 직업을 구분할 수 있게 `(j:maple__Job)-[:maple__hasSkill]->(t)` 의 직업명을 별도 컬럼으로 필요하면 추가합니다.
- 설명 없이 JSON 의 cypher 필드에 쿼리만 넣습니다.

{{examples}}

[질문 분석] {{analysis}}
[연결된 후보 개체] {{candidates}}
{{feedback}}
질문: {{question}}"""

ANSWER_TEMPLATE = """당신은 메이플스토리 게임 정보 질의응답 도우미입니다. 아래 [조회 결과 행]에 있는 내용만 근거로 한국어로 답하세요.

규칙
- 행에 없는 수치·사실은 쓰지 않습니다. 근거가 부족하면 sufficient=false 로 하고, answer 에 '그래프에서 확인되지 않는다'는 취지와 이유를 짧게 씁니다.
- 변경 내용은 적용 패치 날짜(patch_date)와 함께 설명하고, 변경 전/후 값이 있으면 함께 씁니다.
- 답에 사용한 행 번호를 cited_rows 에 넣습니다(예: [1,3]). 본문에는 [1] 처럼 번호를 붙입니다.
- 이 데이터는 최신 패치노트 20건에서 추출한 것이라, 이력이 전부가 아닐 수 있음을 필요하면 한 줄로 알려 줍니다.

질문: {{question}}
[조회 결과 행] (총 {{n_rows}}행 중 앞 {{n_shown}}행)
{{rows}}"""
