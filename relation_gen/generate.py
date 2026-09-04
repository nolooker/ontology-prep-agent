"""
② 관계 후보 생성 단계: ①에서 추출된 엔티티들 사이의 관계를
LLM으로 트리플(주어-관계-목적어) 형태로 제안한다.

사용법:
    export ANTHROPIC_API_KEY=your_key_here   # --provider anthropic (기본값)
    python relation_gen/generate.py --input data/samples/entities_candidates.json \
                                     --output data/samples/relations_candidates.json

    export GEMINI_API_KEY=your_key_here      # --provider gemini
    python relation_gen/generate.py --input data/samples/entities_candidates.json \
                                     --output data/samples/relations_candidates.json --provider gemini

출력 형식 (JSON):
[
  {
    "row_index": 0,
    "relations": [
      {
        "subject": "OO분식", "predicate": "속한업종", "object": "한식음식점",
        "confidence": 0.9, "evidence": "원본 컬럼 '상권업종소분류명'에서 직접 확인됨",
        "status": "pending"
      },
      ...
    ]
  },
  ...
]

"status"는 ③ 검증 단계에서 pending -> approved/rejected/edited 로 갱신될 필드입니다.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import call_llm, default_model, provider_label, resolve_api_key, strip_json_fence, tag_generated_by

SYSTEM_PROMPT = """당신은 온톨로지 구축을 위한 관계 추론 엔진입니다.
입력으로 한 행에서 추출된 엔티티 후보 목록이 주어지면, 그 엔티티들
사이에 존재할 수 있는 의미적 관계를 트리플(주어-관계-목적어) 형태의
JSON 배열로만 출력하세요. 설명이나 마크다운 없이 순수 JSON만 출력합니다.

관계(predicate) 후보 예시 (상황에 맞게 자유롭게 명명 가능):
- 속한업종 (상가 -> 소분류업종)
- 상위분류 (소분류업종 -> 중분류업종 -> 대분류업종)
- 표준산업분류매핑 (상가 또는 업종 -> 표준산업분류)
- 위치 (상가 -> 행정동/법정동)
- 소속 (행정동 -> 시군구 -> 시도)
- 소재 (상가 -> 건물)

각 관계는 다음 필드를 가집니다:
- subject, predicate, object: 트리플 구성 요소 (엔티티 목록의 value를 그대로 사용)
- confidence: 0~1. 두 엔티티가 같은 행에서 명확히 연결되면 0.9 이상,
  간접 추론이면 낮게.
- evidence: 왜 이 관계라고 판단했는지 한 문장 근거 (원본 컬럼 인용 가능)

엔티티 목록에 없는 값을 만들어내지 마세요. 확실하지 않은 관계는
confidence를 낮게 주되 포함은 시키세요 (사람이 검증 단계에서 판단).
"""


def build_user_prompt(entities: list) -> str:
    entity_lines = [f"- {e.get('type')}: {e.get('value')} (신뢰도 {e.get('confidence')})" for e in entities]
    return "다음 엔티티들 사이의 관계 후보를 생성하세요:\n" + "\n".join(entity_lines)


def generate_relations(provider: str, api_key: str, model: str, entities: list) -> list:
    if not entities:
        return []
    try:
        raw = call_llm(provider, api_key, model, SYSTEM_PROMPT, build_user_prompt(entities), max_tokens=2048)
    except Exception as e:
        # 재시도까지 소진한 뒤에도 실패하면 이 행만 건너뛰고 나머지 배치는 계속 진행한다.
        print(f"[경고] API 호출 실패, 이 행은 건너뜀: {e}")
        return []
    text = strip_json_fence(raw)
    try:
        relations = json.loads(text)
    except json.JSONDecodeError:
        print(f"[경고] JSON 파싱 실패: {text[:200]}")
        return []

    for rel in relations:
        rel.setdefault("status", "pending")
    return relations


def main():
    parser = argparse.ArgumentParser(description="LLM 기반 관계 후보 생성")
    parser.add_argument("--input", required=True, help="entities_candidates.json 경로")
    parser.add_argument("--output", required=True, help="결과 JSON 경로")
    parser.add_argument("--provider", choices=["anthropic", "gemini", "grok"], default="anthropic", help="LLM provider")
    parser.add_argument("--model", default=None, help="사용할 모델 (생략 시 provider별 기본값)")
    args = parser.parse_args()

    api_key = resolve_api_key(args.provider)
    if not api_key:
        from llm_client import API_KEY_ENV
        raise SystemExit(f"{API_KEY_ENV[args.provider]} 환경변수를 설정하세요.")
    model = args.model or default_model(args.provider)
    label = provider_label(args.provider, model)

    with open(args.input, "r", encoding="utf-8") as f:
        entity_results = json.load(f)

    results = []
    for item in entity_results:
        relations = tag_generated_by(generate_relations(args.provider, api_key, model, item["entities"]), label)
        results.append({
            "row_index": item["row_index"],
            "relations": relations,
        })
        print(f"[row {item['row_index']}] 관계 후보 {len(relations)}개 생성")
        # 행마다 중간 저장: 중간에 죽어도(예: 네트워크 단절) 여기까지 결과는 보존됨
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"완료: {args.output}에 {len(results)}건 저장")


if __name__ == "__main__":
    main()
