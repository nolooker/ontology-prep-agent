"""
③ 검증 UI: 탭 4개로 구성된다.
  - 탭 0: 사용 가이드 — 비개발자를 위한 프로젝트 설명 및 사용법 안내
  - 탭 1: 관계 후보 검증 (승인/거부/수정) — CSV 기반 + 텍스트 기반 후보를
          하나의 목록에서 함께 검증 (source_type으로 구분 표시)
  - 탭 2: 엔티티 해석 검증 — 텍스트에서 뽑힌 위치 등의 엔티티가 실제 어떤
          행정구역/엔티티를 가리키는지 다중 후보 중에서 사람이 확정
  - 탭 3: 그래프 보기 — graph_sink/commit.py 결과물을 인터랙티브하게 탐색

사용법:
    pip install streamlit
    streamlit run review_ui/app.py -- \
        --entities data/samples/entities_candidates.json \
        --relations data/samples/relations_candidates.json \
        --entities_text data/samples/entities_candidates_text.json \
        --relations_text data/samples/relations_candidates_text.json \
        --output data/samples/relations_reviewed.json \
        --resolution_report data/samples/entity_resolution_report.json \
        --resolution_output data/samples/entity_resolution_resolved.json

주의: streamlit run은 스크립트 인자를 `--` 뒤에 붙여야 합니다.
인자를 생략하면 기본 경로(위 예시와 동일)를 사용합니다.
--entities_text/--relations_text 파일이 없으면 CSV 후보만 표시됩니다.

저장되는 --output 파일의 각 항목은 다음 공통 스키마를 따른다:
    {"source_type": "csv" | "text", "source_id": <row_index 또는 source_doc>,
     "relations": [...]}
graph_sink/commit.py가 이 스키마를 그대로 읽어 그래프에 반영한다.
"""
import argparse
import json
import os
import sys

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import DEFAULT_MODELS, call_llm, strip_json_fence

try:
    import anthropic  # noqa: F401
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    import google.genai  # noqa: F401
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

try:
    import openai  # noqa: F401 (Grok/xAI가 OpenAI 호환 API라 이걸 씀)
    GROK_AVAILABLE = True
except ImportError:
    GROK_AVAILABLE = False

DEFAULT_ENTITIES = "data/samples/entities_candidates.json"
DEFAULT_RELATIONS = "data/samples/relations_candidates.json"
DEFAULT_ENTITIES_TEXT = "data/samples/entities_candidates_text.json"
DEFAULT_RELATIONS_TEXT = "data/samples/relations_candidates_text.json"
DEFAULT_OUTPUT = "data/samples/relations_reviewed.json"
DEFAULT_RESOLUTION_REPORT = "data/samples/entity_resolution_report.json"
DEFAULT_RESOLUTION_OUTPUT = "data/samples/entity_resolution_resolved.json"
DEFAULT_GRAPH_OUTPUT = "data/samples/graph_mock.json"
GRAPH_VIEW_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "graph_view_template.html")
DEFAULT_TEXT_SOURCE = "data/raw_unstructured/seongsu_cafe_street_demo.txt"
DEFAULT_TEXT_LLM_OUTPUT = "data/samples/entities_candidates_text_llm.json"

# extractor/extract_text.py 의 SYSTEM_PROMPT와 동일 (import 경로 문제 방지용으로 여기 복제)
EXTRACT_SYSTEM_PROMPT = """당신은 온톨로지 구축을 위한 정보 추출 엔진입니다.
입력으로 비정형 한국어 문단이 주어지면, 그 안에서 식별 가능한 엔티티
후보들을 JSON 배열로만 출력하세요. 설명, 코드블록 마크다운 없이 순수
JSON만 출력합니다.

엔티티 타입은 다음 중에서 선택하되, 문맥상 필요하면 새 타입도
자유롭게 만드세요 (기존 정형 데이터 파이프라인과 이어질 수 있도록
아래 타입은 최대한 재사용):
- 상가/브랜드, 상권, 시도, 시군구, 행정동, 법정동, 도로
- 기관, 정책/계획, 통계수치

각 엔티티는 다음 필드를 가집니다:
- type: 위 타입 중 하나 또는 새로 정의한 타입
- value: 문서에 실제로 등장한 표현 그대로 (가공하지 말 것)
- confidence: 0~1. 구체적인 고유명사로 명확히 지칭되면 0.9 이상,
  "OO 브랜드"처럼 일반명사로 모호하게 지칭되거나 특정 개체를
  확정하기 어려우면 0.5 이하로 낮게 매기세요.

원문에 없는 정보를 지어내지 마세요.
"""


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entities", default=DEFAULT_ENTITIES)
    parser.add_argument("--relations", default=DEFAULT_RELATIONS)
    parser.add_argument("--entities_text", default=DEFAULT_ENTITIES_TEXT)
    parser.add_argument("--relations_text", default=DEFAULT_RELATIONS_TEXT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--resolution_report", default=DEFAULT_RESOLUTION_REPORT)
    parser.add_argument("--resolution_output", default=DEFAULT_RESOLUTION_OUTPUT)
    parser.add_argument("--graph_output", default=DEFAULT_GRAPH_OUTPUT, help="graph_sink/commit.py가 생성한 그래프 JSON 경로")
    args, _ = parser.parse_known_args()
    return args


def save_json(data, output_path):
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 탭 0: 사용 가이드 (비개발자용)
# ---------------------------------------------------------------------------

def render_guide_tab():
    st.markdown("""
## 이 화면은 무엇을 하는 곳인가요?

한 줄로 말하면: **"컴퓨터(AI)가 자동으로 찾아낸 정보가 맞는지, 사람이 눈으로 확인하고 승인해주는 화면"** 입니다.

전문 용어를 몰라도 괜찮습니다. 아래를 천천히 읽어보시면 지금 화면에서
무엇을 눌러야 하는지 알 수 있도록 설명해두었습니다.
""")

    st.info(
        "💡 **바쁘시면 이것만 읽으세요**: 위쪽 탭에서 **🔗 관계 후보 검증**을 클릭 → "
        "카드를 하나 펼쳐서 내용을 읽고 → 맞으면 **✅ 승인**, 틀리면 **❌ 거부** 클릭 → "
        "다 하셨으면 화면 왼쪽(사이드바)의 **💾 저장** 버튼 클릭. 이게 전부입니다."
    )

    st.markdown("""
---
## 왜 이런 작업이 필요한가요?

이 프로젝트는 두 종류의 원본 자료를 다룹니다.

- 📋 **정형 자료**: 전국 상가(가게) 정보가 담긴 표 형태 자료 (엑셀 파일이라고 생각하시면 됩니다).
  "가게 이름", "업종", "주소" 같은 항목이 칸마다 깔끔하게 정리되어 있습니다.
- 📰 **비정형 자료**: 뉴스 기사처럼 줄글로 쓰인 자료. "성수동 카페거리에 이런 변화가 있었다"
  같은 문장 안에서 필요한 정보를 직접 찾아내야 합니다.

이 자료들 속에는 "이 가게는 이 업종에 속한다", "이 동네는 이 구에 속한다" 같은
**사실관계(관계)** 가 숨어 있습니다. 이걸 컴퓨터가 알아볼 수 있는 형태로
차곡차곡 정리해두면, 나중에 "성동구에 있는 카페는 몇 개인가요?" 같은 질문에
컴퓨터가 바로 답할 수 있게 됩니다. 이렇게 정리된 지식 창고를 **그래프**
(또는 지식 지도)라고 부릅니다.

문제는, 이 관계를 AI가 자동으로 찾아내긴 하지만 **가끔 틀린다는 것**입니다.
예를 들어 원본 자료 자체에 오타나 모순이 있으면 AI도 헷갈립니다. 그래서
AI가 찾아낸 결과를 사람이 한 번 확인하고 "이건 맞다 / 이건 아니다"를
판단해주는 과정이 꼭 필요합니다. **그 확인 작업을 하는 곳이 바로 이 화면**입니다.
""")

    st.markdown("### 🔄 전체 작업은 4단계로 진행됩니다")
    st.markdown("""
| 순서 | 단계 | 하는 일 | 비유 |
|---|---|---|---|
| ① | 정보 뽑아내기 | AI가 원본 자료에서 "가게 이름", "지역", "업종" 같은 낱개 정보를 뽑아냄 | 요리 재료 손질 |
| ② | 관계 만들기 | AI가 뽑아낸 낱개 정보들 사이의 관계를 추측함 (예: "A가게 → 치킨집") | 재료끼리 연결하기 |
| **③** | **사람이 확인 (탭 1·2)** | **사람이 AI의 추측이 맞는지 하나씩 확인·승인** | **맛보고 간 맞추기** |
| **④** | **지식 지도 완성 (탭 3)** | **사람이 승인한 것만 모아 최종 그래프를 만듦** | **완성해서 그릇에 담기** |

즉, AI가 ①②단계에서 초안을 만들어두면, ③단계인 **탭 1·2에서 사람이 검수**하고,
검수를 통과한 내용만 **탭 3(그래프 보기)에 ④단계 결과물**로 반영됩니다. 이 화면
하나에서 ③단계(검수)와 ④단계(완성된 그래프 확인)를 모두 하실 수 있는 셈입니다.
""")

    st.markdown("""
---
## 화면 구성 안내

화면 맨 위에 있는 **"🔑 API 키로 실제 LLM 추출 테스트"** 는 개발/실험용 기능이라
일반적인 검토 작업에는 필요 없습니다. 펼쳐보지 않으셔도 됩니다.

그 아래 탭 3개가 실제로 쓰실 화면입니다 (탭 1·2는 ③단계 검수용, 탭 3은 ④단계
결과물 확인용입니다).
""")

    with st.expander("🔗 탭 1. 관계 후보 검증 — 이게 핵심 작업입니다", expanded=True):
        st.markdown("""
AI가 "A는 B다"라는 형태로 만든 **관계 후보**들을 하나씩 보여줍니다.

**화면에 보이는 것들:**
- 각 카드(회색 박스)는 원본 데이터 한 건(가게 하나, 또는 기사 한 편)을 나타냅니다.
  카드를 클릭하면 펼쳐지고, 그 안에 관계 후보 목록이 나옵니다.
- 각 줄은 `주어` → [관계] → `목적어` 형태로 되어 있습니다.
  예: `치킨플러스동` —[속한업종]→ `치킨` (= "치킨플러스동은 치킨 업종에 속한다"는 뜻)
- 줄 옆에 있는 색깔 동그라미의 의미:
  - 🟢 승인됨 · 🔴 거부됨 · 🟡 수정됨 · ⚪ 아직 확인 안 함(대기)
- **신뢰도** 숫자(0~1)는 AI가 이 관계에 얼마나 자신 있는지를 나타냅니다.
  1에 가까울수록 확실하고, 0.5 미만이면 ⚠️ 표시가 붙어 "이건 특히 잘 확인해보세요"라는 뜻입니다.
- **근거** 문구는 AI가 왜 그렇게 판단했는지 설명한 것입니다. 이걸 읽고 맞는지 판단하시면 됩니다.
- 카드 제목 옆 **🧾 CSV** / **📰 텍스트** 표시는 이 정보가 표(엑셀형) 자료에서 왔는지,
  기사(줄글) 자료에서 왔는지를 구분해줍니다.

**할 일:**
1. 관계 내용과 근거를 읽어봅니다.
2. 맞다고 생각되면 **✅ 승인** 버튼을 누릅니다.
3. 틀렸다고 생각되면 **❌ 거부** 버튼을 누릅니다.
4. 내용이 살짝 틀렸지만 고치면 맞는 경우 **✏️ 수정** 버튼을 눌러 직접 값을 고친 뒤 저장합니다.
5. 화면 왼쪽 사이드바 맨 위에서 전체 진행 상황(승인/거부/대기 건수)을 확인할 수 있습니다.
6. 검토가 끝났으면 (전부 다 안 해도 괜찮습니다, 중간에 저장해도 됩니다)
   **사이드바의 💾 관계 검증 저장** 버튼을 꼭 눌러주세요. 이 버튼을 눌러야 결과가 실제로 저장됩니다.

**검색이나 필터가 필요할 때:**
- 탭 바로 아래 **🔍 관계 후보 검색** 칸에 가게 이름이나 지역명을 입력하면 해당 카드만 보입니다.
- 사이드바의 **표시 필터**에서 "미검토만"을 고르면 아직 확인 안 한 것만, "저신뢰(<0.5)만"을 고르면
  AI가 자신 없어 한 것만 골라볼 수 있습니다. 우선순위 높은 것부터 보고 싶을 때 유용합니다.
""")

    with st.expander("📍 탭 2. 엔티티 해석 검증 — 지역명이 애매할 때 확인하는 곳"):
        st.markdown("""
뉴스 기사 같은 줄글에는 지역 이름이 뭉뚱그려 나오는 경우가 많습니다.
예를 들어 기사에는 그냥 **"성수동"**이라고만 쓰여 있는데, 실제 행정구역
데이터에는 "성수동"이라는 이름이 없고 **"성수동1가"**와 **"성수동2가"**로
나뉘어 있는 경우가 있습니다. 이럴 때 AI가 마음대로 둘 중 하나를 고르면
틀릴 위험이 있으니, **사람이 직접 어느 쪽인지 확정**해주는 화면입니다.

**화면에 보이는 것들 (앞의 색깔 동그라미 의미):**
- 🟢 완전 일치 — 이미 정확히 일치해서 사실 확인이 거의 필요 없음
- 🟡 단일 후보 — 후보가 하나뿐이라 맞을 가능성이 높지만 한 번 확인 권장
- 🔴 다중 후보 — 후보가 여러 개라서 **반드시 사람이 골라줘야 함** (가장 중요)
- ⚪ 매칭 없음 — 참고 목록에 아예 없는 이름 (오타이거나, 새로 추가해야 할 이름)

**할 일:**
1. 🔴(다중 후보) 항목부터 확인하는 게 좋습니다 (화면에 기본으로 펼쳐져 있습니다).
2. "실제로 어느 엔티티를 가리키나요?" 질문 아래 후보 중 정답을 고릅니다.
   - 목록에 정답이 없으면 **"신규 엔티티로 추가"**를 고르세요.
   - 후보와 다른 이름을 직접 쓰고 싶으면 **"직접 입력"**을 고르고 텍스트를 입력하세요.
3. **✔️ 확정** 버튼을 누릅니다.
4. 다 하셨으면 **사이드바의 💾 엔티티 해석 저장** 버튼을 눌러주세요.
""")

    with st.expander("🕸️ 탭 3. 그래프 보기 (④단계) — 최종 결과물을 눈으로 확인하는 곳"):
        st.markdown("""
탭 1, 탭 2에서 사람이 확인·승인한 내용들이 실제로 어떤 "지식 지도"로
만들어졌는지 그림으로 보여주는 화면입니다. (아직 승인 전이라면 이 화면에는
가장 최근에 만들어진 결과물이 보입니다.)

- 동그라미 하나하나가 "가게", "업종", "지역" 같은 정보 하나(**노드**)이고,
  선으로 이어진 것이 둘 사이의 관계(**관계선**)입니다.
- 동그라미 색깔은 정보의 종류(상가, 업종, 지역 등)를 나타냅니다. 왼쪽 범례에서
  색깔별 의미를 확인할 수 있고, 클릭하면 그 종류만 화면에서 껐다 켰다 할 수 있습니다.
- 점선 테두리가 있는 동그라미는, 원래 애매하게 쓰여 있던 이름(탭 2에서 확정한 것)이
  올바른 이름으로 합쳐진 것입니다.
- 마우스로 화면을 끌면 이동하고, 스크롤하면 확대/축소됩니다.
- 동그라미를 클릭하면 오른쪽에 그 항목과 연결된 모든 관계, 근거, 신뢰도가 자세히 나옵니다.
- 왼쪽 위 검색창에 이름을 입력하면 그 항목을 바로 찾아줍니다.
""")

    st.markdown("""
---
## 📖 용어가 헷갈릴 때 (간단 사전)

| 용어 | 쉬운 설명 |
|---|---|
| AI / LLM | 사람 말을 이해하고 글을 읽어서 정보를 찾아내는 인공지능 프로그램 |
| 엔티티 | 자료 속에서 뽑아낸 낱개 정보 하나 (예: 가게 이름, 지역명, 업종명) |
| 관계(트리플) | "A는 B다" 형태로 엔티티 두 개를 이어주는 문장 |
| 신뢰도 | AI가 이 판단에 얼마나 확신하는지 (0~1, 높을수록 확실함) |
| 근거 | AI가 왜 그렇게 판단했는지에 대한 설명 |
| 그래프 | 확인이 끝난 관계들을 모아 만든 최종 지식 지도 |
| CSV | 표(엑셀) 형태로 정리된 자료 파일 |
| 승인 / 거부 / 수정 | 사람이 AI 결과에 대해 내리는 판단 (맞음 / 틀림 / 고쳐서 맞음) |

---
### ❓ 자주 묻는 질문
""")

    with st.expander("이미 카드마다 내용이 다 채워져 있는데, 왜 그런가요? 제가 입력해야 하는 게 아닌가요?"):
        st.markdown(
            "카드 안의 내용은 이미 AI(①②단계)가 미리 만들어둔 **초안**입니다. "
            "여러분이 새로 입력하실 필요는 없고, **이 초안이 맞는지 확인만** 하시면 됩니다."
        )
    with st.expander("승인/거부를 잘못 눌렀어요. 되돌릴 수 있나요?"):
        st.markdown(
            "네, 같은 항목에서 다른 버튼(✅/❌)을 다시 누르면 상태가 바뀝니다. "
            "저장 버튼을 누르기 전까지는 몇 번이든 바꿀 수 있습니다."
        )
    with st.expander("저장을 안 누르고 화면을 닫으면 어떻게 되나요?"):
        st.markdown(
            "그동안 체크한 승인/거부 내용이 파일에 반영되지 않습니다. "
            "작업을 마칠 때마다(또는 중간중간) 사이드바의 **💾 저장** 버튼을 눌러주시는 게 안전합니다."
        )
    with st.expander("모든 항목을 다 확인해야 하나요?"):
        st.markdown(
            "가능하면 전부 확인하는 게 가장 좋지만, 시간이 없다면 🔴다중 후보나 "
            "⚠️저신뢰(<0.5) 항목처럼 AI가 자신 없어 한 것부터 우선 확인하시는 걸 권장합니다. "
            "사이드바 필터에서 골라볼 수 있습니다."
        )


# ---------------------------------------------------------------------------
# 상단: API 키로 실제 LLM 추출 테스트
# ---------------------------------------------------------------------------

def call_llm_extract(provider, api_key, model, text):
    raw = call_llm(provider, api_key, model, EXTRACT_SYSTEM_PROMPT,
                    "다음 문단에서 엔티티 후보를 추출하세요:\n\n" + text, max_tokens=1500)
    out = strip_json_fence(raw)
    return json.loads(out), out


PROVIDER_LABELS = {
    "anthropic": "Anthropic (Claude)",
    "gemini": "Google (Gemini)",
    "grok": "xAI (Grok)",
}
PROVIDER_KEY_LABELS = {
    "anthropic": "Anthropic API 키",
    "gemini": "Gemini API 키",
    "grok": "xAI API 키",
}
PROVIDER_KEY_PLACEHOLDERS = {
    "anthropic": "sk-ant-...",
    "gemini": "AIza...",
    "grok": "xai-...",
}


def render_api_test_section():
    with st.expander("🔑 API 키로 실제 LLM 추출 테스트", expanded=False):
        provider_options = []
        if ANTHROPIC_AVAILABLE:
            provider_options.append("anthropic")
        if GEMINI_AVAILABLE:
            provider_options.append("gemini")
        if GROK_AVAILABLE:
            provider_options.append("grok")
        if not provider_options:
            st.error("anthropic, google-genai, openai 패키지가 하나도 설치되어 있지 않습니다. "
                     "`pip install anthropic google-genai openai` 중 필요한 것을 먼저 실행하세요.")
            return

        st.caption(
            "여기에 입력한 API 키는 이 앱을 실행 중인 서버의 메모리에서 이번 요청에만 사용되고, "
            "파일이나 로그에 저장되지 않습니다. 다만 이 화면이 공유 서버(예: Streamlit Cloud)에 "
            "배포되어 있다면 그 서버를 거쳐 전송되는 구조이니, 회사 전체가 함께 쓰는 배포판에서는 "
            "가급적 개인 실서비스용 키 대신 테스트용 키를 사용하세요.")

        col0, col1, col2 = st.columns([1, 2, 1])
        provider = col0.selectbox("Provider", provider_options, key="api_provider_input",
                                   format_func=lambda p: PROVIDER_LABELS[p])
        key_label = PROVIDER_KEY_LABELS[provider]
        key_placeholder = PROVIDER_KEY_PLACEHOLDERS[provider]
        api_key = col1.text_input(key_label, type="password", key="api_key_input", placeholder=key_placeholder)
        model = col2.text_input("모델", value=DEFAULT_MODELS[provider], key=f"api_model_input_{provider}")

        default_text = ""
        if os.path.exists(DEFAULT_TEXT_SOURCE):
            with open(DEFAULT_TEXT_SOURCE, "r", encoding="utf-8") as f:
                default_text = f.read()

        input_text = st.text_area("추출할 텍스트", value=default_text, height=180, key="api_test_text")

        if st.button("🚀 추출 실행 (LLM 호출)", key="run_llm_extract"):
            if not api_key:
                st.warning("API 키를 입력하세요.")
            elif not input_text.strip():
                st.warning("텍스트를 입력하세요.")
            else:
                with st.spinner("LLM 호출 중..."):
                    try:
                        entities, raw = call_llm_extract(provider, api_key, model, input_text)
                        st.session_state["last_llm_entities"] = entities
                        st.success(f"엔티티 {len(entities)}개 추출 완료")
                    except json.JSONDecodeError:
                        st.error("모델 응답을 JSON으로 파싱하지 못했습니다. 아래 원본 응답을 확인하세요.")
                        st.code(raw)
                    except Exception as e:
                        st.error(f"API 호출 중 오류가 발생했습니다: {e}")

        if st.session_state.get("last_llm_entities"):
            entities = st.session_state["last_llm_entities"]
            st.table(entities)
            if st.button("💾 결과를 파일로 저장", key="save_llm_result"):
                result = [{"source_doc": "api_test_input", "entities": entities}]
                save_json(result, DEFAULT_TEXT_LLM_OUTPUT)
                st.success(f"{DEFAULT_TEXT_LLM_OUTPUT}에 저장했습니다.")


# ---------------------------------------------------------------------------
# 탭 1: 관계 후보 검증 (CSV 기반 + 텍스트 기반 통합)
# ---------------------------------------------------------------------------

def csv_row_summary(entities_item):
    if not entities_item:
        return None
    row = entities_item.get("source_row", {})
    parts = []
    for key in ["상호명", "상권업종소분류명", "시도명", "시군구명", "행정동명"]:
        val = row.get(key)
        if val and str(val) != "nan":
            parts.append(str(val))
    return " · ".join(parts) if parts else None


def text_doc_summary(entities_item, doc_id):
    if not entities_item:
        return os.path.basename(doc_id)
    values = [ent["value"] for ent in entities_item.get("entities", []) if ent.get("confidence", 0) >= 0.7]
    return " · ".join(values[:5]) if values else os.path.basename(doc_id)


@st.cache_data
def load_relation_data(entities_path, relations_path, entities_text_path, relations_text_path):
    """CSV 기반과 텍스트 기반 관계 후보를 공통 스키마로 통합한다:
    {"source_type": "csv"|"text", "source_id": row_index 또는 source_doc, "relations": [...]}
    """
    unified = []
    summaries = {}  # (source_type, source_id) -> 요약 문자열

    if os.path.exists(entities_path) and os.path.exists(relations_path):
        with open(entities_path, "r", encoding="utf-8") as f:
            entities = json.load(f)
        with open(relations_path, "r", encoding="utf-8") as f:
            relations = json.load(f)
        entities_by_row = {item["row_index"]: item for item in entities}
        for item in relations:
            row_idx = item["row_index"]
            unified.append({"source_type": "csv", "source_id": row_idx, "relations": item["relations"]})
            summary = csv_row_summary(entities_by_row.get(row_idx))
            summaries[("csv", row_idx)] = summary or f"row {row_idx}"

    if os.path.exists(entities_text_path) and os.path.exists(relations_text_path):
        with open(entities_text_path, "r", encoding="utf-8") as f:
            entities_text = json.load(f)
        with open(relations_text_path, "r", encoding="utf-8") as f:
            relations_text = json.load(f)
        entities_by_doc = {item["source_doc"]: item for item in entities_text}
        for item in relations_text:
            doc = item["source_doc"]
            unified.append({"source_type": "text", "source_id": doc, "relations": item["relations"]})
            summaries[("text", doc)] = text_doc_summary(entities_by_doc.get(doc), doc)

    return unified, summaries


def render_relation_tab(args):
    has_csv = os.path.exists(args.relations) and os.path.exists(args.entities)
    has_text = os.path.exists(args.relations_text) and os.path.exists(args.entities_text)

    if not has_csv and not has_text:
        st.error(
            f"입력 파일을 찾을 수 없습니다.\nCSV — entities: {args.entities}, relations: {args.relations}\n"
            f"텍스트 — entities: {args.entities_text}, relations: {args.relations_text}"
        )
        st.info("먼저 extractor/extract.py 와 relation_gen/generate.py (또는 simulate_pipeline.py)를 실행하세요.")
        return
    if not has_text:
        st.caption(f"ℹ️ 텍스트 기반 후보 파일을 찾지 못해 CSV 후보만 표시합니다 ({args.relations_text}).")

    search_query = st.text_input(
        "🔍 관계 후보 검색", key="relation_search",
        placeholder="상가명, 업종, 지역명 등으로 검색",
    ).strip().lower()

    unified_data, summaries = load_relation_data(args.entities, args.relations, args.entities_text, args.relations_text)

    if "relations_state" not in st.session_state:
        st.session_state.relations_state = json.loads(json.dumps(unified_data))

    state = st.session_state.relations_state

    all_rels = [rel for item in state for rel in item["relations"]]
    total = len(all_rels)
    approved = sum(1 for r in all_rels if r["status"] == "approved")
    rejected = sum(1 for r in all_rels if r["status"] == "rejected")
    pending = sum(1 for r in all_rels if r["status"] == "pending")

    with st.sidebar:
        st.header("관계 검증 진행 현황")
        st.metric("전체 관계 후보", total)
        col1, col2, col3 = st.columns(3)
        col1.metric("승인", approved)
        col2.metric("거부", rejected)
        col3.metric("대기", pending)
        st.progress(0 if total == 0 else (approved + rejected) / total)

        st.divider()
        filter_option = st.radio(
            "표시 필터",
            ["전체", "미검토만", "저신뢰(<0.5)만", "승인됨", "거부됨", "CSV만", "텍스트만"],
            key="relation_filter",
        )
        st.divider()
        if st.button("💾 관계 검증 저장", use_container_width=True, key="save_relations"):
            save_json(state, args.output)
            st.success(f"{args.output}에 저장했습니다.")

    shown_count = 0
    for item_idx, item in enumerate(state):
        source_type = item["source_type"]
        source_id = item["source_id"]
        summary = summaries.get((source_type, source_id), str(source_id))

        if search_query:
            haystack = (summary + " " + " ".join(
                f"{r['subject']} {r['predicate']} {r['object']}" for r in item["relations"]
            )).lower()
            if search_query not in haystack:
                continue

        def matches_filter(rel):
            if filter_option == "전체":
                return True
            if filter_option == "미검토만":
                return rel["status"] == "pending"
            if filter_option == "저신뢰(<0.5)만":
                return rel["confidence"] < 0.5
            if filter_option == "승인됨":
                return rel["status"] == "approved"
            if filter_option == "거부됨":
                return rel["status"] == "rejected"
            if filter_option == "CSV만":
                return source_type == "csv"
            if filter_option == "텍스트만":
                return source_type == "text"
            return True

        visible_rels = [(i, r) for i, r in enumerate(item["relations"]) if matches_filter(r)]
        if not visible_rels:
            continue

        shown_count += 1
        source_badge = "🧾 CSV" if source_type == "csv" else "📰 텍스트"
        label = f"row {source_id}" if source_type == "csv" else os.path.basename(str(source_id))
        with st.expander(f"**{summary}**  ({source_badge} · {label})", expanded=True):
            for rel_idx, rel in visible_rels:
                cols = st.columns([3, 1.2, 1, 1, 1])
                triple_text = f"`{rel['subject']}` —[{rel['predicate']}]→ `{rel['object']}`"

                status_color = {
                    "approved": "🟢", "rejected": "🔴", "pending": "⚪", "edited": "🟡",
                }.get(rel["status"], "⚪")

                low_conf_flag = " ⚠️" if rel["confidence"] < 0.5 else ""

                cols[0].markdown(f"{status_color} {triple_text}{low_conf_flag}")
                cols[0].caption(f"근거: {rel['evidence']}")
                cols[1].caption(f"신뢰도 {rel['confidence']:.2f} · 상태: {rel['status']}")

                key_prefix = f"item{item_idx}_rel{rel_idx}"
                if cols[2].button("✅ 승인", key=f"{key_prefix}_approve"):
                    rel["status"] = "approved"
                    st.rerun()
                if cols[3].button("❌ 거부", key=f"{key_prefix}_reject"):
                    rel["status"] = "rejected"
                    st.rerun()
                if cols[4].button("✏️ 수정", key=f"{key_prefix}_edit_toggle"):
                    st.session_state[f"{key_prefix}_editing"] = not st.session_state.get(f"{key_prefix}_editing", False)

                if st.session_state.get(f"{key_prefix}_editing"):
                    ec1, ec2, ec3, ec4 = st.columns([2, 1.5, 2, 1])
                    new_subj = ec1.text_input("주어", value=rel["subject"], key=f"{key_prefix}_subj")
                    new_pred = ec2.text_input("관계", value=rel["predicate"], key=f"{key_prefix}_pred")
                    new_obj = ec3.text_input("목적어", value=rel["object"], key=f"{key_prefix}_obj")
                    ec4.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
                    if ec4.button("저장", key=f"{key_prefix}_save_edit", use_container_width=True):
                        rel["subject"], rel["predicate"], rel["object"] = new_subj, new_pred, new_obj
                        rel["status"] = "edited"
                        st.session_state[f"{key_prefix}_editing"] = False
                        st.rerun()

                st.divider()

    if shown_count == 0:
        st.info("검색/필터 조건에 맞는 항목이 없습니다.")


# ---------------------------------------------------------------------------
# 탭 2: 엔티티 해석 검증
# ---------------------------------------------------------------------------

@st.cache_data
def load_resolution_report(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def render_resolution_tab(args):
    if not os.path.exists(args.resolution_report):
        st.warning(f"엔티티 해석 리포트를 찾을 수 없습니다: {args.resolution_report}")
        st.info("먼저 entity_resolution.py를 실행해서 리포트를 생성하세요.")
        return

    search_query = st.text_input(
        "🔍 엔티티 해석 검색", key="resolution_search",
        placeholder="엔티티명, 후보명 등으로 검색",
    ).strip().lower()

    report = load_resolution_report(args.resolution_report)

    if "resolution_state" not in st.session_state:
        state = json.loads(json.dumps(report))
        for item in state:
            item.setdefault("human_decision", None)
            item.setdefault("decision_status", "pending")
        st.session_state.resolution_state = state

    state = st.session_state.resolution_state

    total = len(state)
    resolved = sum(1 for r in state if r["decision_status"] == "resolved")
    ambiguous = sum(1 for r in state if r["match_type"] == "prefix_ambiguous")

    with st.sidebar:
        st.divider()
        st.header("엔티티 해석 진행 현황")
        st.metric("전체 검토 대상", total)
        col1, col2 = st.columns(2)
        col1.metric("확정 완료", resolved)
        col2.metric("다중 후보", ambiguous)
        st.progress(0 if total == 0 else resolved / total)
        if st.button("💾 엔티티 해석 저장", use_container_width=True, key="save_resolution"):
            save_json(state, args.resolution_output)
            st.success(f"{args.resolution_output}에 저장했습니다.")

    match_type_label = {
        "exact": ("🟢", "완전 일치 — 자동 확정 가능"),
        "prefix_unique": ("🟡", "단일 후보 — 확인 권장"),
        "prefix_ambiguous": ("🔴", "다중 후보 — 반드시 확인 필요"),
        "no_match": ("⚪", "매칭 없음 — 신규 엔티티 여부 확인"),
    }

    shown_count = 0
    for idx, item in enumerate(state):
        if search_query:
            haystack = (item["text_entity"] + " " + " ".join(item.get("candidates", []))).lower()
            if search_query not in haystack:
                continue
        shown_count += 1

        icon, desc = match_type_label.get(item["match_type"], ("⚪", item["match_type"]))
        decided_mark = " ✔️ 확정됨" if item["decision_status"] == "resolved" else ""

        with st.expander(
            f"{icon} `{item['text_entity']}` ({item['text_entity_type']}) — {desc}{decided_mark}",
            expanded=(item["decision_status"] != "resolved"),
        ):
            st.caption(f"매칭 유형: {item['match_type']} · 신뢰도 {item['confidence']:.2f}")
            if item.get("note"):
                st.caption(f"참고: {item['note']}")

            candidates = item.get("candidates", [])
            options = candidates + ["신규 엔티티로 추가", "직접 입력"]

            current = item.get("human_decision")
            default_idx = options.index(current) if current in options else 0

            key_prefix = f"resolution_{idx}"
            choice = st.radio(
                "실제로 어느 엔티티를 가리키나요?",
                options,
                index=default_idx,
                key=f"{key_prefix}_choice",
                horizontal=True,
            )

            custom_value = None
            if choice == "직접 입력":
                custom_value = st.text_input("직접 입력", key=f"{key_prefix}_custom")

            if st.button("✔️ 확정", key=f"{key_prefix}_confirm"):
                if choice == "직접 입력":
                    item["human_decision"] = custom_value or ""
                else:
                    item["human_decision"] = choice
                item["decision_status"] = "resolved"
                st.rerun()

    if shown_count == 0:
        st.info("검색 조건에 맞는 항목이 없습니다.")


# ---------------------------------------------------------------------------
# 탭 3: 그래프 보기 (④ graph_sink/commit.py 결과물)
# ---------------------------------------------------------------------------

def render_graph_tab(args):
    if not os.path.exists(args.graph_output):
        st.warning(f"그래프 파일을 찾을 수 없습니다: {args.graph_output}")
        st.info(
            "먼저 graph_sink/commit.py를 실행해서 그래프를 생성하세요. 예:\n\n"
            "```bash\npython graph_sink/commit.py \\\n"
            f"    --relations {args.output} \\\n"
            f"    --entities {args.entities} \\\n"
            f"    --entities_text {args.entities_text} \\\n"
            f"    --resolution {args.resolution_output} \\\n"
            f"    --graph_output {args.graph_output} \\\n"
            "    --cypher_output graph_sink/import.cypher\n```"
        )
        return

    with open(args.graph_output, "r", encoding="utf-8") as f:
        graph_json = f.read()  # 이미 JSON 텍스트이므로 그대로 삽입 (재직렬화 불필요)

    with open(GRAPH_VIEW_TEMPLATE, "r", encoding="utf-8") as f:
        template = f.read()

    html = template.replace("__GRAPH_DATA_JSON__", graph_json)
    st.caption(f"{args.graph_output} 기준 (graph_sink/commit.py로 새로 반영하면 새로고침 시 갱신됩니다)")
    components.html(html, height=780, scrolling=False)


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    st.set_page_config(page_title="온톨로지 검증 UI", layout="wide")
    st.title("온톨로지 데이터 준비 — 사람 검증(③) 및 그래프 확인(④)")

    render_api_test_section()
    st.divider()

    tab0, tab1, tab2, tab3 = st.tabs(
        ["📖 사용 가이드", "🔗 관계 후보 검증", "📍 엔티티 해석 검증", "🕸️ 그래프 보기"]
    )
    with tab0:
        render_guide_tab()
    with tab1:
        render_relation_tab(args)
    with tab2:
        render_resolution_tab(args)
    with tab3:
        render_graph_tab(args)


if __name__ == "__main__":
    main()
