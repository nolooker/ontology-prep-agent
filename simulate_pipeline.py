"""
데모용 스크립트: 실제 ANTHROPIC_API_KEY 없이도 파이프라인 산출물 형태를
확인할 수 있도록, extract.py/generate.py와 동일한 판단 로직을
(LLM 호출 대신) 직접 코드로 구현한 것입니다.

실전에서는 이 로직 대부분을 LLM이 대체하지만, 정형 컬럼 데이터라
매핑 자체는 규칙 기반으로도 커버됩니다. LLM이 진짜 가치를 더하는
지점은 다음과 같은 '판단이 필요한 케이스'입니다:
  - 상권업종분류와 표준산업분류가 불일치하는 경우 (예: 버거집인데
    표준산업분류는 '비주거용 건물 임대업') → confidence를 낮추고
    evidence에 불일치 사실을 명시
  - 상호명에서 지점 정보가 섞여 있는 경우 등

사용법:
    python simulate_pipeline.py --input data/samples/sample_30.csv \
        --entities_output data/samples/entities_candidates.json \
        --relations_output data/samples/relations_candidates.json
"""
import argparse
import json

import pandas as pd

GENERATED_BY = "simulate_pipeline (규칙 기반)"


def extract_entities(row: dict) -> list:
    entities = []

    def add(etype, value, confidence):
        if pd.notna(value) and str(value).strip():
            entities.append({
                "type": etype, "value": str(value).strip(), "confidence": confidence,
                "generated_by": GENERATED_BY,
            })

    add("상가", row.get("상호명"), 0.95)
    add("대분류업종", row.get("상권업종대분류명"), 0.95)
    add("중분류업종", row.get("상권업종중분류명"), 0.95)
    add("소분류업종", row.get("상권업종소분류명"), 0.95)
    add("표준산업분류", row.get("표준산업분류명"), 0.9)
    add("시도", row.get("시도명"), 0.95)
    add("시군구", row.get("시군구명"), 0.95)
    add("행정동", row.get("행정동명"), 0.95)
    add("법정동", row.get("법정동명"), 0.9)
    if pd.notna(row.get("건물명")) and str(row.get("건물명")).strip():
        add("건물", row.get("건물명"), 0.9)

    return entities


def generate_relations(row: dict) -> list:
    relations = []

    def add(subj, pred, obj, confidence, evidence):
        if pd.notna(subj) and pd.notna(obj) and str(subj).strip() and str(obj).strip():
            relations.append({
                "subject": str(subj).strip(), "predicate": pred, "object": str(obj).strip(),
                "confidence": confidence, "evidence": evidence, "status": "pending",
                "generated_by": GENERATED_BY,
            })

    name = row.get("상호명")
    add(name, "속한업종", row.get("상권업종소분류명"), 0.95,
        "원본 컬럼 '상권업종소분류명'에서 직접 확인됨")
    add(row.get("상권업종소분류명"), "상위분류", row.get("상권업종중분류명"), 0.95,
        "상권업종분류 체계상 소분류의 상위 중분류")
    add(row.get("상권업종중분류명"), "상위분류", row.get("상권업종대분류명"), 0.95,
        "상권업종분류 체계상 중분류의 상위 대분류")

    # 표준산업분류 매핑 — 소분류명과 표준산업분류명이 의미적으로 일치하는지 확인
    sub = str(row.get("상권업종소분류명", ""))
    std = str(row.get("표준산업분류명", ""))
    # 아주 단순한 휴리스틱: 핵심 키워드 겹침 여부 (실전에서는 LLM이 의미 유사도로 판단)
    mismatch_flag = False
    if sub and std:
        # 명백한 불일치 패턴 예시 처리 (버거 vs 건물 임대업 등)
        suspicious_std_keywords = ["임대업", "부동산"]
        if any(k in std for k in suspicious_std_keywords) and not any(k in sub for k in suspicious_std_keywords):
            mismatch_flag = True

    if mismatch_flag:
        add(name, "표준산업분류매핑", std, 0.35,
            f"경고: 상권업종소분류('{sub}')와 표준산업분류('{std}')가 의미적으로 불일치함 — "
            f"원본 데이터의 분류 오류 가능성, 사람 검증 필수")
    elif sub and std:
        add(name, "표준산업분류매핑", std, 0.8,
            "상권업종분류와 표준산업분류 간 매핑, 분류체계가 달라 세부 표현은 다를 수 있음")

    add(name, "위치", row.get("행정동명"), 0.9, "원본 컬럼 '행정동명'에서 직접 확인됨")
    add(row.get("행정동명"), "소속", row.get("시군구명"), 0.9, "행정구역 상위 관계")
    add(row.get("시군구명"), "소속", row.get("시도명"), 0.9, "행정구역 상위 관계")

    if pd.notna(row.get("건물명")) and str(row.get("건물명")).strip():
        add(name, "소재", row.get("건물명"), 0.85, "원본 컬럼 '건물명'에서 직접 확인됨")

    return relations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--entities_output", required=True)
    parser.add_argument("--relations_output", required=True)
    args = parser.parse_args()

    df = pd.read_csv(args.input)

    entity_results = []
    relation_results = []
    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        entities = extract_entities(row_dict)
        relations = generate_relations(row_dict)
        entity_results.append({"row_index": int(idx), "source_row": row_dict, "entities": entities})
        relation_results.append({"row_index": int(idx), "relations": relations})

    with open(args.entities_output, "w", encoding="utf-8") as f:
        json.dump(entity_results, f, ensure_ascii=False, indent=2)
    with open(args.relations_output, "w", encoding="utf-8") as f:
        json.dump(relation_results, f, ensure_ascii=False, indent=2)

    n_mismatch = sum(1 for r in relation_results for rel in r["relations"] if rel["confidence"] < 0.5)
    print(f"완료: 엔티티 {len(entity_results)}행, 관계 저장 완료. 저신뢰(<0.5) 관계 {n_mismatch}건 발견")


if __name__ == "__main__":
    main()
