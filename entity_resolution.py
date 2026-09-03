"""
엔티티 해석(Entity Resolution): 비정형 텍스트에서 뽑힌 위치 엔티티가
실제 행정구역 체계(법정동/행정동)의 어떤 값과 같은 것을 가리키는지
판단한다.

문제 상황 예시: 뉴스 텍스트는 "성수동"이라고 뭉뚱그려 표현하지만,
실제 행정구역 데이터에는 "성수동"이라는 단독 법정동이 없고
"성수동1가"/"성수동2가"로 세분화되어 있다. 이런 경우 자동으로
하나를 골라 확정하면 안 되고, 후보들을 제시해서 사람이 판단하게
해야 한다 (이 스크립트는 그 후보 목록까지만 만든다).

매칭 전략 (간단한 버전, 실전에서는 LLM 의미 매칭이나 임베딩
유사도로 고도화 가능):
  1. 완전 일치 -> confidence 1.0, 자동 확정 가능
  2. 접두어 일치 (텍스트 값이 실제 값의 접두어) -> 여러 후보면
     "다중 후보, 사람 확인 필요"로 표시
  3. 매칭 없음 -> "신규 엔티티로 그래프에 추가 필요"

사용법:
    python entity_resolution.py \
        --text_entities data/samples/entities_candidates_text.json \
        --reference data/ground_truth/seongdong_locations.json \
        --output data/samples/entity_resolution_report.json
"""
import argparse
import json

LOCATION_TYPES = {"행정동", "법정동", "시군구", "시도"}


def resolve_value(value, reference_values):
    exact = [v for v in reference_values if v == value]
    if exact:
        return {"match_type": "exact", "candidates": exact, "confidence": 1.0}

    prefix_matches = [v for v in reference_values if v.startswith(value)]
    if len(prefix_matches) == 1:
        return {"match_type": "prefix_unique", "candidates": prefix_matches, "confidence": 0.7}
    if len(prefix_matches) > 1:
        return {
            "match_type": "prefix_ambiguous",
            "candidates": prefix_matches,
            "confidence": 0.3,
            "note": "동일 접두어를 가진 후보가 여러 개 — 사람이 어느 세부 지역인지 확인해야 함",
        }

    return {"match_type": "no_match", "candidates": [], "confidence": 0.0,
            "note": "참조 목록에 없음 — 신규 엔티티로 그래프에 추가하거나 오탈자 여부 확인 필요"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text_entities", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    with open(args.text_entities, "r", encoding="utf-8") as f:
        text_docs = json.load(f)
    with open(args.reference, "r", encoding="utf-8") as f:
        reference = json.load(f)

    all_reference_values = reference.get("법정동_목록", []) + reference.get("행정동_목록", [])

    report = []
    for doc in text_docs:
        for ent in doc["entities"]:
            if ent["type"] not in LOCATION_TYPES:
                continue
            resolution = resolve_value(ent["value"], all_reference_values)
            report.append({
                "text_entity": ent["value"],
                "text_entity_type": ent["type"],
                **resolution,
            })

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    ambiguous = sum(1 for r in report if r["match_type"] == "prefix_ambiguous")
    print(f"완료: {len(report)}건 검토, 그중 다중 후보(사람 확인 필요) {ambiguous}건")
    for r in report:
        if r["match_type"] == "prefix_ambiguous":
            print(f"  ⚠️  '{r['text_entity']}' -> 후보: {r['candidates']}")


if __name__ == "__main__":
    main()
