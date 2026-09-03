"""
① 추출 단계: 샘플 CSV의 각 행에서 온톨로지 후보 엔티티/속성을 LLM으로 추출한다.
Anthropic(Claude)과 Gemini 두 provider를 --provider로 선택할 수 있다 (llm_client.py 참고).

사용법:
    export ANTHROPIC_API_KEY=your_key_here   # --provider anthropic (기본값)
    python extractor/extract.py --input data/samples/sample_30.csv --output data/samples/entities_candidates.json

    export GEMINI_API_KEY=your_key_here      # --provider gemini
    python extractor/extract.py --input data/samples/sample_30.csv --output data/samples/entities_candidates.json \
        --provider gemini

출력 형식 (JSON):
[
  {
    "row_index": 0,
    "source_row": {...원본 컬럼...},
    "entities": [
      {"type": "상가", "value": "OO분식", "confidence": 0.95},
      {"type": "소분류업종", "value": "한식음식점", "confidence": 0.9},
      ...
    ]
  },
  ...
]
"""
import argparse
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from llm_client import call_llm, default_model, resolve_api_key, strip_json_fence

SYSTEM_PROMPT = """당신은 온톨로지 구축을 위한 정보 추출 엔진입니다.
입력으로 상가(상권) 데이터 한 행이 주어지면, 그 행에서 식별 가능한
엔티티 후보들을 JSON으로만 출력하세요. 설명, 코드블록 마크다운 없이
순수 JSON 배열만 출력합니다.

엔티티 타입은 다음 중에서 선택하세요 (해당 정보가 행에 있을 때만):
- 상가 (개별 업소, 상호명 기준)
- 대분류업종 / 중분류업종 / 소분류업종
- 표준산업분류
- 시도 / 시군구 / 행정동 / 법정동
- 건물

각 엔티티는 다음 필드를 가집니다:
- type: 위 타입 중 하나
- value: 실제 값 (원본 텍스트 그대로, 가공하지 말 것)
- confidence: 0~1 사이 신뢰도 (원본 컬럼에서 명확히 읽힌 경우 0.9 이상,
  유추가 필요한 경우 낮게)

같은 행에서 여러 엔티티가 나올 수 있습니다. 원본에 없는 정보를
지어내지 마세요.
"""


def build_user_prompt(row: dict) -> str:
    lines = [f"{col}: {val}" for col, val in row.items() if pd.notna(val)]
    return "다음 행에서 엔티티 후보를 추출하세요:\n" + "\n".join(lines)


def extract_row(provider: str, api_key: str, model: str, row: dict) -> list:
    try:
        raw = call_llm(provider, api_key, model, SYSTEM_PROMPT, build_user_prompt(row), max_tokens=2048)
    except Exception as e:
        # 재시도까지 소진한 뒤에도 실패하면 이 행만 건너뛰고 나머지 배치는 계속 진행한다.
        print(f"[경고] API 호출 실패, 이 행은 건너뜀: {e}")
        return [{"type": "API_ERROR", "value": str(e), "confidence": 0.0}]
    text = strip_json_fence(raw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print(f"[경고] JSON 파싱 실패, 원본 응답 보존: {text[:200]}")
        return [{"type": "PARSE_ERROR", "value": text, "confidence": 0.0}]


def main():
    parser = argparse.ArgumentParser(description="LLM 기반 엔티티 추출")
    parser.add_argument("--input", required=True, help="샘플 CSV 경로")
    parser.add_argument("--output", required=True, help="결과 JSON 경로")
    parser.add_argument("--provider", choices=["anthropic", "gemini", "grok"], default="anthropic", help="LLM provider")
    parser.add_argument("--model", default=None, help="사용할 모델 (생략 시 provider별 기본값)")
    parser.add_argument("--limit", type=int, default=None, help="테스트용 처리 행 수 제한")
    args = parser.parse_args()

    api_key = resolve_api_key(args.provider)
    if not api_key:
        from llm_client import API_KEY_ENV
        raise SystemExit(f"{API_KEY_ENV[args.provider]} 환경변수를 설정하세요.")
    model = args.model or default_model(args.provider)

    df = pd.read_csv(args.input)
    if args.limit:
        df = df.head(args.limit)

    results = []
    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        entities = extract_row(args.provider, api_key, model, row_dict)
        results.append({
            "row_index": int(idx),
            "source_row": row_dict,
            "entities": entities,
        })
        print(f"[{idx + 1}/{len(df)}] 엔티티 {len(entities)}개 추출")
        # 행마다 중간 저장: 중간에 죽어도(예: 네트워크 단절) 여기까지 결과는 보존됨
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"완료: {args.output}에 {len(results)}건 저장")


if __name__ == "__main__":
    main()
