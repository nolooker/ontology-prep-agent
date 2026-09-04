"""
① 추출 단계 (비정형 텍스트용): CSV처럼 컬럼이 있는 정형 데이터와 달리,
자유 문장에서는 엔티티 타입/경계를 LLM이 문맥으로 판단해야 한다.

extractor/extract.py(정형 CSV용)와의 차이:
- 입력이 "행(row)"이 아니라 "문단(paragraph)"
- 엔티티 타입 목록에 정형 데이터에 없던 유형(기관, 정책/계획, 도로, 통계수치)이 추가됨
- confidence는 "원본에 명시적으로 있는가"뿐 아니라 "특정 개체를 가리키는지 모호한가"도 반영
  (예: "스페셜티 커피 브랜드 1호점"처럼 실제 상호명이 아닌 경우 신뢰도를 낮게)

사용법:
    export ANTHROPIC_API_KEY=your_key_here   # --provider anthropic (기본값)
    python extractor/extract_text.py --input data/raw_unstructured/seongsu_cafe_street_demo.txt \
        --output data/samples/entities_candidates_text.json

    export GEMINI_API_KEY=your_key_here      # --provider gemini
    python extractor/extract_text.py --input data/raw_unstructured/seongsu_cafe_street_demo.txt \
        --output data/samples/entities_candidates_text.json --provider gemini
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import (
    call_llm, default_model, provider_label, resolve_api_key, strip_json_fence, tag_generated_by, tag_latency,
)

SYSTEM_PROMPT = """당신은 온톨로지 구축을 위한 정보 추출 엔진입니다.
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


def build_user_prompt(text: str) -> str:
    return "다음 문단에서 엔티티 후보를 추출하세요:\n\n" + text


def extract_paragraph(provider: str, api_key: str, model: str, text: str):
    """반환값: (엔티티 리스트, API 호출 소요 시간(초))"""
    raw, elapsed = call_llm(provider, api_key, model, SYSTEM_PROMPT, build_user_prompt(text), max_tokens=1500)
    out = strip_json_fence(raw)
    try:
        return json.loads(out), elapsed
    except json.JSONDecodeError:
        print(f"[경고] JSON 파싱 실패: {out[:200]}")
        return [{"type": "PARSE_ERROR", "value": out, "confidence": 0.0}], elapsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="비정형 텍스트 파일 경로")
    parser.add_argument("--output", required=True)
    parser.add_argument("--provider", choices=["anthropic", "gemini", "grok"], default="anthropic", help="LLM provider")
    parser.add_argument("--model", default=None, help="사용할 모델 (생략 시 provider별 기본값)")
    args = parser.parse_args()

    api_key = resolve_api_key(args.provider)
    if not api_key:
        from llm_client import API_KEY_ENV
        raise SystemExit(f"{API_KEY_ENV[args.provider]} 환경변수를 설정하세요.")
    model = args.model or default_model(args.provider)

    with open(args.input, "r", encoding="utf-8") as f:
        text = f.read()

    entities, elapsed = extract_paragraph(args.provider, api_key, model, text)
    entities = tag_latency(tag_generated_by(entities, provider_label(args.provider, model)), elapsed)

    result = [{"source_doc": args.input, "entities": entities}]
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"완료: 엔티티 {len(entities)}개 추출 -> {args.output}")


if __name__ == "__main__":
    main()
