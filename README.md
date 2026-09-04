# Ontology Data Preparation Agent (샘플 프로젝트)

소상공인시장진흥공단_상가(상권)정보 데이터를 이용해
"추출 → 관계 후보 생성 → 사람 검증 → Graph 반영" 파이프라인의
①②단계 프로토타입을 구현한 것입니다.

## 데이터 준비

1. https://www.data.go.kr/data/15083033/fileData.do 에서 CSV를 다운로드합니다.
   (로그인 및 활용신청이 필요할 수 있습니다.)
2. 다운로드한 파일을 `data/raw/` 폴더에 넣습니다. (파일명 예: `상가업소_전체.csv`)
3. 아래 명령으로 무작위 샘플을 추출합니다.

```bash
python sample_data.py --input data/raw/상가업소_전체.csv --n 30 --output data/samples/sample_30.csv
```

## LLM Provider (Anthropic / Gemini / Grok)

`llm_client.py`가 세 provider를 공통 인터페이스로 감싸며, 추출/관계생성
스크립트와 Streamlit UI 모두 `--provider anthropic`(기본값) /
`--provider gemini` / `--provider grok`를 선택할 수 있습니다. API 키는
provider별로 다른 환경변수를 씁니다. Grok(xAI)은 OpenAI 호환 API라
`openai` 패키지를 base_url만 바꿔서 사용합니다.

| provider  | 환경변수            | 기본 모델            |
|-----------|--------------------|----------------------|
| anthropic | `ANTHROPIC_API_KEY`| `claude-sonnet-4-6`  |
| gemini    | `GEMINI_API_KEY`   | `gemini-3.6-flash`   |
| grok      | `XAI_API_KEY`      | `grok-4-fast`        |

무료 티어는 provider마다 일일/분당 요청 한도가 다르고 자주 바뀝니다 —
429(rate limit) 응답을 받으면 `llm_client.py`가 자동으로 지수 백오프
재시도를 하지만, 한도 자체를 다 쓰면 재시도로도 해결되지 않으니 그럴 땐
다른 provider로 전환하거나 시간을 두고 다시 시도하세요.

## 추출(①) 프로토타입 실행

```bash
export ANTHROPIC_API_KEY=your_key_here
python extractor/extract.py --input data/samples/sample_30.csv --output data/samples/entities_candidates.json

# 또는 Gemini로 (무료 티어)
export GEMINI_API_KEY=your_key_here
python extractor/extract.py --input data/samples/sample_30.csv --output data/samples/entities_candidates.json --provider gemini

# 또는 Grok으로 (무료 티어)
export XAI_API_KEY=your_key_here
python extractor/extract.py --input data/samples/sample_30.csv --output data/samples/entities_candidates.json --provider grok
```

## 관계 후보 생성(②) 실행

```bash
python relation_gen/generate.py --input data/samples/entities_candidates.json --output data/samples/relations_candidates.json --provider gemini
```

## 검증(③) UI 실행

Streamlit 기반 화면이며, **탭 5개**로 구성되어 있습니다.

```bash
pip install streamlit
streamlit run review_ui/app.py -- \
    --entities data/samples/entities_candidates.json \
    --relations data/samples/relations_candidates.json \
    --entities_text data/samples/entities_candidates_text.json \
    --relations_text data/samples/relations_candidates_text.json \
    --output data/samples/relations_reviewed.json \
    --resolution_report data/samples/entity_resolution_report.json \
    --resolution_output data/samples/entity_resolution_resolved.json \
    --graph_output data/samples/graph_mock.json
```

**탭 0. 📖 사용 가이드**
- 비개발자/비IT인 검토자를 위한 프로젝트 설명 + 화면별 사용법을 담은 안내 탭.
  전문 용어 없이 "무엇을 눌러야 하는지"를 단계별로 설명하고, 용어 사전과 FAQ도 포함.

**탭 1. 🔗 관계 후보 검증**
- CSV 기반 후보와 텍스트 기반 후보(`--entities_text`/`--relations_text`)를 하나의
  목록에서 함께 검증합니다. 각 항목에 🧾 CSV / 📰 텍스트 배지가 붙어 원천을 구분합니다.
  `--entities_text`/`--relations_text` 파일이 없으면 CSV 후보만 표시됩니다.
- 사이드바에서 승인/거부/대기 건수와 진행률을 확인할 수 있습니다.
- 필터로 "미검토만", "저신뢰(<0.5)만", "CSV만", "텍스트만" 등을 골라 우선순위 높은
  항목부터 검토할 수 있습니다.
- 각 관계 후보를 승인/거부하거나, 직접 주어·관계·목적어를 수정 후 저장할 수 있습니다.
- 저장되는 `--output` 파일의 각 항목은 `{"source_type": "csv"|"text", "source_id": ...,
  "relations": [...]}` 공통 스키마를 따르며, `graph_sink/commit.py`가 이를 그대로 읽습니다.

**탭 2. 📍 엔티티 해석 검증** (신규)
- `entity_resolution.py` 실행 결과를 불러와서, "성수동" 같이 다중 후보가 있는
  엔티티를 사람이 직접 후보 중 하나로 확정하거나, "신규 엔티티로 추가", "직접 입력" 중 선택할 수 있습니다.
- 🔴(다중 후보) 항목이 기본으로 펼쳐져서 우선 처리하도록 되어 있습니다.

**탭 3. 🕸️ 그래프 보기** (신규)
- `graph_sink/commit.py`가 생성한 `--graph_output`(기본 `graph_mock.json`)을
  인터랙티브 노드-엣지 그래프로 렌더링합니다. 노드 타입별 색상, 병합된
  노드 표시, 검색, 타입/소스 필터, 노드 클릭 시 연결된 관계·근거·신뢰도를
  보여주는 상세 패널을 제공합니다.
- 파일을 매번 새로 읽으므로 `commit.py`를 다시 돌린 뒤 브라우저를
  새로고침하면 최신 그래프가 반영됩니다. 파일이 없으면 안내 메시지가 뜹니다.

**탭 4. 🧠 모델 거버넌스** (신규)
- 엔티티/관계마다 붙는 `generated_by`(어떤 provider:model 또는
  `simulate_pipeline`이 만들었는지)와 `latency_ms`(API 호출 소요 시간)
  태그를 집계해서 보여줍니다: 소스별 분포, 모델별 평균 신뢰도·응답
  속도·승인율(승인율은 탭 1에서 검토를 시작해야 실시간 계산됨).
- 실측 응답 속도를 바탕으로 "전국 데이터(약 277만 건) 전체에 이
  파이프라인을 순차로 돌리면 얼마나 걸릴지"도 추정해서 보여줍니다
  (실측 건수가 적을수록 참고용 수치임을 화면에 명시).
- Mendix AI Studio가 강조하는 모델 거버넌스·추적성 개념을 가볍게
  흉내 낸 것으로, 실제 모델 배포/모니터링 기능은 없습니다.

각 탭 하단(사이드바)의 **저장 버튼**을 눌러야 결과가 파일로 반영됩니다.

## 두 번째 원천: 비정형 텍스트 (뉴스 기반, 데모용 재구성 텍스트)

정형 CSV 하나만으로는 "다양한 원천"이라는 원래 요구사항을 충족하지 못하므로,
성수동 카페거리 관련 뉴스 내용을 재구성한 짧은 텍스트를 두 번째 원천으로 추가했습니다.
(실제 기사 원문이 아니라 같은 사실관계를 직접 재구성한 텍스트입니다 — 저작권 문제 방지)

```
data/raw_unstructured/seongsu_cafe_street_demo.txt   # 원천 텍스트
extractor/extract_text.py                            # 비정형 텍스트용 추출 스크립트 (LLM 프롬프트)
data/samples/entities_candidates_text.json           # 추출 결과 (데모)
data/samples/relations_candidates_text.json          # 관계 후보 결과 (데모)
```

**정형 데이터와의 차이점**:
- 엔티티 타입이 늘어남 (기관, 정책/계획, 도로, 통계수치 등 — CSV엔 없던 유형)
- "스페셜티 커피 브랜드 1호점"처럼 실제 고유명사가 아닌 모호한 지칭은 confidence를 낮게(0.35~0.4) 매김

### 엔티티 해석(Entity Resolution) 문제 발견

텍스트는 "성수동"이라고 뭉뚱그려 표현하지만, 실제 CSV 데이터의 법정동은
"성수동1가"/"성수동2가"로 나뉘어 있어서 정확히 일치하지 않습니다.
이런 경우를 자동 판별하는 스크립트:

```bash
python entity_resolution.py \
    --text_entities data/samples/entities_candidates_text.json \
    --reference data/ground_truth/seongdong_locations.json \
    --output data/samples/entity_resolution_report.json
```

실행 결과: "성수동" → 후보 ['성수동1가', '성수동2가'] 다중 매칭으로 플래그됨 →
사람이 어느 세부 지역을 가리키는지 확인해야 한다고 표시.
(`data/ground_truth/seongdong_locations.json`은 실제 서울 CSV에서 추출한
성동구 법정동/행정동 전체 목록입니다.)

`review_ui/app.py` 탭 2에서 사람이 후보 중 하나(예: "성수동1가")를 확정하면
`entity_resolution_resolved.json`에 `human_decision`/`decision_status`가 채워집니다.
이 파일을 `graph_sink/commit.py --resolution`에 넘기면, 텍스트에서 뭉뚱그려
표현된 "성수동" 노드가 CSV 기반 "성수동1가" 노드로 자동 병합됩니다(아래 ④ 참고).

## Graph 반영(④)

`graph_sink/commit.py`는 ③에서 승인(approved)/수정(edited)된 관계만 골라
노드/엣지 JSON과 Neo4j용 Cypher 임포트 스크립트로 변환합니다.

```bash
python graph_sink/commit.py \
    --relations data/samples/relations_reviewed.json \
    --entities data/samples/entities_candidates.json \
    --entities_text data/samples/entities_candidates_text.json \
    --resolution data/samples/entity_resolution_resolved.json \
    --graph_output data/samples/graph_mock.json \
    --cypher_output graph_sink/import.cypher
```

- `--entities`/`--entities_text`: 노드 타입(예: 행정동, 상가) 매핑용. 병합된 노드는
  CSV 쪽에 타입 정보가 없으면 원래 텍스트 표현의 타입으로 대체 추정합니다.
- `--resolution`(선택): 사람이 확정한 엔티티 해석 결과. "신규 엔티티로 추가"를
  선택한 항목은 병합하지 않고 별도 노드로 남기며, 병합된 노드는
  `{"id": "성수동1가", "type": "행정동", "merged_from": ["성수동"]}`처럼
  원래 텍스트 표현을 `merged_from`에 기록해 출처를 추적할 수 있게 합니다.
- `--relations`에는 CSV 기반 항목(`{"row_index": ..., "relations": [...]}`)과
  텍스트 기반 항목(`{"source_type": "text", "source_id": ..., "relations": [...]}`)이
  섞여 있어도 됩니다 — `review_ui/app.py`가 두 원천을 이미 하나의 파일로 저장합니다.

**데모 실행 결과** (`data/samples/relations_reviewed_combined_demo.json` +
`data/samples/entity_resolution_resolved_demo.json` 사용, CSV 32건 + 텍스트 1건 —
CSV 32건 중 2건은 `gemini-3.5-flash-lite`로 실제 호출해 얻은 결과):
노드 204개, 엣지 251개. "성수동"(텍스트) → "성수동1가"(CSV 법정동)로 정상 병합됨을
`data/samples/graph_mock.json`에서 확인할 수 있습니다.

## 파이프라인 흐름

```
원천 CSV (data/raw/)                     원천 텍스트 (data/raw_unstructured/)
   │  sample_data.py                        │  extractor/extract_text.py
   ▼                                        ▼
샘플 CSV → entities_candidates.json     entities_candidates_text.json
   │  extractor/extract.py                  │  entity_resolution.py (위치 엔티티 검증)
   │  relation_gen/generate.py               │  relation_gen/generate.py (또는 동등 로직)
   ▼                                        ▼
relations_candidates.json               relations_candidates_text.json
   └───────────────┬─────────────────────────┘
                    ▼
        review_ui/app.py (③ 사람 검증: 관계 승인/거부 + 엔티티 해석 확정)
                    ▼
        relations_reviewed.json + entity_resolution_resolved.json
                    │  graph_sink/commit.py (④ 엔티티 해석 병합 + 그래프 반영)
                    ▼
        graph_mock.json / import.cypher
```

## 데모 실행 결과 (API 키 없이 시뮬레이션)

전국 16개 시도 CSV(총 277만 건)에서 무작위 30건을 뽑아
`simulate_pipeline.py`로 엔티티/관계 후보를 생성해봤습니다.
(`extract.py`/`generate.py`와 동일한 스키마, LLM 호출 대신 규칙 기반으로 판단)
현재 `data/samples/entities_candidates.json`/`relations_candidates.json`에는
이 30건 뒤에 실제 `gemini-3.5-flash-lite`로 처리한 2건(row 30-31)이 추가되어
총 32건이 들어있습니다 — 모델 거버넌스 탭에서 실제 응답 속도를 보여주기 위함입니다.

**발견한 흥미로운 케이스**: "롯데리아범어" 행 — 상권업종소분류는 "버거"인데
표준산업분류명은 "비주거용 건물 임대업"으로 되어 있어 원본 데이터 자체의
분류 오류로 보입니다. 이런 케이스는 confidence를 낮게(0.35) 매기고
evidence에 불일치 사실을 남겨서, ③ 검증 단계에서 사람이 반드시
확인하도록 설계했습니다.

**인사이트**: 이 데이터는 이미 정형화되어 있어서, 엔티티 추출(①) 자체는
컬럼 매핑만으로도 상당 부분 해결됩니다. LLM이 진짜 가치를 더하는 지점은
②단계에서 **분류 불일치 같은 예외 케이스를 판단하고 자연어 근거를
남기는 것**입니다. 실제 LLM(`extract.py`/`generate.py`)을 쓰면 이런
의미적 판단을 더 정교하게(예: 유사도 기반, 문맥 고려) 내릴 수 있습니다.

실행:
```bash
python simulate_pipeline.py --input data/samples/sample_30.csv \
    --entities_output data/samples/entities_candidates.json \
    --relations_output data/samples/relations_candidates.json
```

## 왜 이 컬럼 구조를 쓰는가

- 업종분류가 대(10)/중(75)/소(247)분류 계층 구조 → is-a 관계 연습에 적합
- 행정구역이 시도/시군구/행정동/법정동 계층 구조 → 공간 계층 관계 연습에 적합
- 표준산업분류명이 상권업종분류와 별도로 존재 → 교차분류/매핑 관계 연습에 적합
