"""제품명 검색과 성분 확장.

시민은 성분명을 모른다. "타이레놀"을 치면 "아세트아미노펜"으로 바꿔서 분석해야 한다.

## 왜 성분 별칭 사전에 넣지 않았는가

`IngredientIndex` 는 별칭 하나가 성분 하나로 가는 구조다. 그런데 제품은 성분을
여러 개 가진다(복합제). 종합감기약 하나를 성분 하나로 매핑하면 나머지 성분의
상호작용을 통째로 놓친다.

그래서 제품은 **입력을 성분 목록으로 펼치는 앞단**으로 분리했다.
`AnalysisService` 가 매칭 전에 여기를 먼저 거친다.

## 애매하면 펼치지 않는다

"타이레놀"처럼 여러 품목에 걸치는 이름이 흔하다. 걸린 품목들의 성분 구성이
서로 다르면 **어느 쪽인지 모른다는 뜻**이므로 확장하지 않고 후보만 돌려준다.
약은 한 글자 차이로 다른 약이 되고, 잘못 펼치면 없는 위험을 경고하거나
있는 위험을 놓친다. `normalize.py` 의 애매성 처리와 같은 원칙이다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from app.core.normalize import normalize

logger = logging.getLogger(__name__)

# 검색어가 이보다 짧으면 부분 일치가 너무 많이 걸린다
MIN_QUERY_LEN = 2


@dataclass(frozen=True)
class Product:
    item_seq: str
    name: str
    search_name: str
    ingredients: list[str] = field(default_factory=list)
    maker: str = ""
    image: str = ""
    interaction_text: str = ""

    @property
    def has_ingredients(self) -> bool:
        return bool(self.ingredients)


class ProductIndex:
    def __init__(self, products: list[Product]) -> None:
        self._products = products
        # 정규화한 검색명 → 제품들. 정규화는 사용자 입력에도 똑같이 적용된다.
        self._by_key: dict[str, list[Product]] = {}
        for p in products:
            for name in {p.search_name, p.name}:
                key = normalize(name)
                if key:
                    self._by_key.setdefault(key, []).append(p)

    @classmethod
    def from_csv(cls, path: Path) -> ProductIndex:
        """제품 파일이 없으면 빈 색인을 준다. 제품 검색만 안 될 뿐 서버는 뜬다."""
        if not Path(path).exists():
            logger.info(
                "제품 파일(%s)이 없어 제품명 검색이 비활성화됩니다. "
                "scripts/build_products.py 로 만들 수 있습니다.",
                path,
            )
            return cls([])

        df = pd.read_csv(path, dtype=str).fillna("")
        products = [
            Product(
                item_seq=str(r.item_seq).strip(),
                name=str(r.product_name).strip(),
                search_name=str(r.search_name).strip(),
                ingredients=[i for i in str(r.ingredients).split("|") if i],
                maker=str(r.maker).strip(),
                image=str(r.image).strip(),
                interaction_text=str(r.interaction_text).strip(),
            )
            for r in df.itertuples(index=False)
        ]
        return cls(products)

    def __len__(self) -> int:
        return len(self._products)

    @property
    def with_ingredients(self) -> int:
        return sum(1 for p in self._products if p.has_ingredients)

    def search(self, query: str, limit: int = 8) -> list[Product]:
        """자동완성용 부분 일치 검색. 짧은 이름부터 보여준다."""
        key = normalize(query)
        if len(key) < MIN_QUERY_LEN:
            return []

        hits = [p for p in self._products if key in normalize(p.name)]
        # 검색어에 가까운(=군더더기가 적은) 이름을 위로
        hits.sort(key=lambda p: (len(p.search_name), p.name))

        seen: set[str] = set()
        unique: list[Product] = []
        for p in hits:
            if p.search_name in seen:
                continue
            seen.add(p.search_name)
            unique.append(p)
            if len(unique) >= limit:
                break
        return unique

    def expand(self, raw: str) -> tuple[Product | None, list[Product]]:
        """입력을 제품으로 해석한다.

        Returns:
            (확정된 제품 또는 None, 후보 목록)
            확정은 걸린 제품들의 **성분 구성이 모두 같을 때만** 한다.
        """
        key = normalize(raw)
        if len(key) < MIN_QUERY_LEN:
            return None, []

        exact = self._by_key.get(key)
        candidates = exact or [
            p for p in self._products if key in normalize(p.name)
        ]
        if not candidates:
            return None, []

        with_ingr = [p for p in candidates if p.has_ingredients]
        if not with_ingr:
            # 이름은 찾았지만 성분을 모른다. 확장하지 못한다는 사실이 드러나야 한다.
            return None, candidates[:5]

        ingredient_sets = {tuple(sorted(p.ingredients)) for p in with_ingr}
        if len(ingredient_sets) > 1:
            # 같은 이름인데 성분 구성이 갈린다 → 어느 제품인지 모른다
            return None, with_ingr[:5]

        return with_ingr[0], with_ingr[:5]
