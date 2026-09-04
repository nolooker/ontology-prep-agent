"""
④ Graph 반영 단계: ③에서 사람이 검증(승인/거부/수정)한 관계 후보를
Graph/Ontology 시스템에 반영하는 목업 구현.

실제 Change360 / Graph Studio API가 확정되기 전까지는:
  1. 승인(approved) + 수정(edited) 상태인 관계만 그래프로 반영
  2. 그래프를 JSON 노드/엣지 구조로 저장 (mock graph store)
  3. 실제 Neo4j 등에 나중에 그대로 옮길 수 있도록 Cypher 임포트 스크립트도 생성

--relations는 CSV 기반 항목({"row_index": ..., "relations": [...]})과
텍스트 기반 항목({"source_type": "text", "source_id": ..., "relations": [...]})이
섞여 있어도 됩니다 (review_ui/app.py가 두 원천을 하나의 파일로 저장함).

엔티티 해석 병합(--resolution): entity_resolution.py + review_ui 탭2에서
사람이 확정한 결과(entity_resolution_resolved.json)를 넘기면, 텍스트에서
뭉뚱그려 표현된 값(예: "성수동")을 사람이 확정한 실제 값(예: "성수동1가")의
별칭으로 취급해 같은 노드로 병합합니다. "신규 엔티티로 추가"를 선택한
항목은 병합하지 않고 별도 노드로 둡니다.

사용법:
    python graph_sink/commit.py \
        --relations data/samples/relations_reviewed.json \
        --entities data/samples/entities_candidates.json \
        --entities_text data/samples/entities_candidates_text.json \
        --resolution data/samples/entity_resolution_resolved.json \
        --graph_output data/samples/graph_mock.json \
        --cypher_output graph_sink/import.cypher

주의: --relations에 relations_candidates.json(검증 전)을 넣으면
     pending 상태만 있어서 반영될 게 없다는 안내만 나옵니다.
     반드시 review_ui/app.py에서 저장한 relations_reviewed.json을 써야 합니다.
"""
import argparse
import json
import os
from collections import defaultdict


ENTITY_TYPE_LOOKUP = {}  # value -> type, entities_candidates*.json에서 채움
ENTITY_SOURCE_LOOKUP = {}  # value -> generated_by (거버넌스/추적성: 어떤 모델이 만들었는지)

NO_MERGE_DECISIONS = {"신규 엔티티로 추가"}


def load_entity_types(entities_path):
    if not entities_path or not os.path.exists(entities_path):
        return
    with open(entities_path, "r", encoding="utf-8") as f:
        entity_results = json.load(f)
    for item in entity_results:
        for ent in item["entities"]:
            # 같은 값이 여러 타입으로 잡히면 먼저 본 것 유지 (실전에선 충돌 해결 로직 필요)
            ENTITY_TYPE_LOOKUP.setdefault(ent["value"], ent["type"])
            if ent.get("generated_by"):
                ENTITY_SOURCE_LOOKUP.setdefault(ent["value"], ent["generated_by"])


def load_alias_map(resolution_path):
    """entity_resolution_resolved.json -> {텍스트 표현: 확정된 실제 값} 별칭 매핑.

    사람이 "신규 엔티티로 추가"를 선택했거나 아직 확정하지 않은 항목은
    병합하지 않는다 (별도 노드로 남겨둠).
    """
    alias_map = {}
    if not resolution_path or not os.path.exists(resolution_path):
        return alias_map
    with open(resolution_path, "r", encoding="utf-8") as f:
        resolution_items = json.load(f)
    for item in resolution_items:
        if item.get("decision_status") != "resolved":
            continue
        decision = item.get("human_decision")
        if not decision or decision in NO_MERGE_DECISIONS:
            continue
        text_value = item["text_entity"]
        if decision != text_value:
            alias_map[text_value] = decision
    return alias_map


def build_graph(relations_data, committable_statuses, alias_map=None):
    alias_map = alias_map or {}
    nodes = {}  # value(별칭 해석 후) -> node dict
    edges = []
    skipped = defaultdict(int)

    def resolve(value):
        return alias_map.get(value, value)

    def get_or_create_node(raw_value):
        value = resolve(raw_value)
        if value not in nodes:
            # 별칭 해석 후 값의 타입/출처를 모르면(예: CSV 샘플에 없는 법정동),
            # 원래 텍스트 표현의 타입/출처로 대체 추정한다.
            node_type = ENTITY_TYPE_LOOKUP.get(value) or ENTITY_TYPE_LOOKUP.get(raw_value, "미분류")
            generated_by = ENTITY_SOURCE_LOOKUP.get(value) or ENTITY_SOURCE_LOOKUP.get(raw_value)
            nodes[value] = {
                "id": value,
                "type": node_type,
            }
            if generated_by:
                nodes[value]["generated_by"] = generated_by
        node = nodes[value]
        if raw_value != value:
            merged_from = node.setdefault("merged_from", [])
            if raw_value not in merged_from:
                merged_from.append(raw_value)
        return node

    for item in relations_data:
        source_type = item.get("source_type", "csv")
        source_id = item.get("source_id", item.get("row_index"))
        for rel in item["relations"]:
            if rel["status"] not in committable_statuses:
                skipped[rel["status"]] += 1
                continue
            get_or_create_node(rel["subject"])
            get_or_create_node(rel["object"])
            edges.append({
                "source": resolve(rel["subject"]),
                "predicate": rel["predicate"],
                "target": resolve(rel["object"]),
                "confidence": rel["confidence"],
                "provenance": {
                    "source_type": source_type,
                    "source_id": source_id,
                    "evidence": rel["evidence"],
                    "review_status": rel["status"],
                    "generated_by": rel.get("generated_by", "미기록"),
                },
            })

    return list(nodes.values()), edges, skipped


def to_cypher(nodes, edges):
    """실제 Neo4j로 옮길 때 쓸 수 있는 MERGE 기반 Cypher 스크립트 생성.
    MERGE를 쓰는 이유: 같은 노드/관계가 여러 행에서 중복 생성되는 것을 방지.
    """
    lines = ["// 자동 생성된 Cypher 임포트 스크립트 (온톨로지 목업)", ""]

    for node in nodes:
        label = node["type"].replace(" ", "_").replace("·", "_")
        safe_id = node["id"].replace("'", "\\'")
        lines.append(f"MERGE (n:`{label}` {{name: '{safe_id}'}});")

    lines.append("")
    for edge in edges:
        rel_type = edge["predicate"].replace(" ", "_")
        s = edge["source"].replace("'", "\\'")
        t = edge["target"].replace("'", "\\'")
        lines.append(
            f"MATCH (a {{name: '{s}'}}), (b {{name: '{t}'}}) "
            f"MERGE (a)-[:`{rel_type}` {{confidence: {edge['confidence']}}}]->(b);"
        )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--relations", required=True, help="relations_reviewed.json 경로 (검증 완료 파일, CSV/텍스트 혼합 가능)")
    parser.add_argument("--entities", default=None, help="entities_candidates.json 경로 (노드 타입 매핑용, 선택)")
    parser.add_argument("--entities_text", default=None, help="entities_candidates_text.json 경로 (텍스트 기반 노드 타입 매핑용, 선택)")
    parser.add_argument("--resolution", default=None, help="entity_resolution_resolved.json 경로 (텍스트↔CSV 엔티티 병합용, 선택)")
    parser.add_argument("--graph_output", required=True, help="목업 그래프 JSON 저장 경로")
    parser.add_argument("--cypher_output", required=True, help="Cypher 임포트 스크립트 저장 경로")
    parser.add_argument("--include_edited", action="store_true", default=True,
                         help="edited 상태도 반영 대상에 포함 (기본 True)")
    args = parser.parse_args()

    with open(args.relations, "r", encoding="utf-8") as f:
        relations_data = json.load(f)

    load_entity_types(args.entities)
    load_entity_types(args.entities_text)
    alias_map = load_alias_map(args.resolution)

    committable = {"approved"}
    if args.include_edited:
        committable.add("edited")

    nodes, edges, skipped = build_graph(relations_data, committable, alias_map)

    os.makedirs(os.path.dirname(args.graph_output) or ".", exist_ok=True)
    with open(args.graph_output, "w", encoding="utf-8") as f:
        json.dump({"nodes": nodes, "edges": edges}, f, ensure_ascii=False, indent=2)

    os.makedirs(os.path.dirname(args.cypher_output) or ".", exist_ok=True)
    with open(args.cypher_output, "w", encoding="utf-8") as f:
        f.write(to_cypher(nodes, edges))

    print(f"그래프 반영 완료: 노드 {len(nodes)}개, 엣지 {len(edges)}개")
    print(f"  -> {args.graph_output}")
    print(f"  -> {args.cypher_output}")
    if alias_map:
        print(f"엔티티 해석 병합 적용됨 ({len(alias_map)}건): " +
              ", ".join(f"'{k}'->'{v}'" for k, v in alias_map.items()))
    if skipped:
        skip_summary = ", ".join(f"{status}: {count}건" for status, count in skipped.items())
        print(f"반영 제외됨 ({skip_summary}) - pending/rejected 상태는 그래프에 반영되지 않습니다.")


if __name__ == "__main__":
    main()
