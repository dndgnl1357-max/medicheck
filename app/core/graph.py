"""NetworkX 기반 상호작용 그래프.

노드 = 성분, 엣지 = 상호작용(가중치 = 위험 등급).
당장은 화면에 뿌릴 그래프 JSON을 만드는 용도지만,
확장하면 "내가 먹는 약과 2단계 이내로 연결된 위험 성분" 같은 탐색도 가능하다.
"""

from __future__ import annotations

import networkx as nx

from app.core.repository import Interaction, InteractionRepository


def build_graph(interactions: list[Interaction]) -> nx.Graph:
    g = nx.Graph()
    for item in interactions:
        g.add_node(item.ingredient_a)
        g.add_node(item.ingredient_b)
        g.add_edge(
            item.ingredient_a,
            item.ingredient_b,
            severity=item.severity,
            mechanism=item.mechanism,
            source=item.source,
        )
    return g


def to_cytoscape(g: nx.Graph) -> dict:
    """프론트엔드 시각화 라이브러리가 바로 먹는 형태로 변환한다."""
    return {
        "nodes": [
            {"data": {"id": n, "label": n, "degree": g.degree(n)}} for n in g.nodes
        ],
        "edges": [
            {
                "data": {
                    "source": u,
                    "target": v,
                    "severity": d.get("severity", 1),
                    "mechanism": d.get("mechanism", ""),
                }
            }
            for u, v, d in g.edges(data=True)
        ],
    }


def neighbors_within(
    repo: InteractionRepository, ingredient: str, depth: int = 1
) -> set[str]:
    """해당 성분에서 depth 단계 이내로 연결된 성분들."""
    g = build_graph(repo.all())
    if ingredient not in g:
        return set()
    return set(nx.single_source_shortest_path_length(g, ingredient, cutoff=depth)) - {
        ingredient
    }
