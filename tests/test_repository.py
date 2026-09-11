"""저장소 적재 규칙 테스트.

여기서 확인하는 건 "실제 데이터가 들어온 뒤에도 시드가 채우던 빈칸이 남는가" 이다.
이 동작은 data/processed/interactions.csv 가 생기기 전까지는 한 번도 실행되지 않아서,
지금 고정해 두지 않으면 실제 데이터를 넣는 날 조용히 기능이 후퇴한다.
"""

import pandas as pd
import pytest

from app.core.repository import Interaction, InteractionRepository

COLUMNS = [
    "ingredient_a",
    "ingredient_b",
    "severity",
    "mechanism",
    "advice",
    "min_interval_hours",
    "source",
]


def write_csv(path, rows: list[dict]):
    df = pd.DataFrame(rows)
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df[COLUMNS].to_csv(path, index=False, encoding="utf-8")
    return path


@pytest.fixture
def dirs(tmp_path):
    processed = tmp_path / "processed"
    seed = tmp_path / "seed"
    processed.mkdir()
    seed.mkdir()
    return processed, seed


def seed_rows():
    return [
        # 영양제 조합 — DUR 에는 없고 시드에만 있다
        {"ingredient_a": "와파린", "ingredient_b": "오메가3", "severity": 3, "source": "시드"},
        # 의약품 조합 — 실제 데이터에도 있을 법한 쌍
        {"ingredient_a": "와파린", "ingredient_b": "아스피린", "severity": 4, "source": "시드"},
    ]


# --- 폴백 (실제 데이터 없음) ---------------------------------------------------


def test_정제_데이터가_없으면_시드로_폴백한다(dirs):
    processed, seed = dirs
    write_csv(seed / "interactions_seed.csv", seed_rows())

    repo = InteractionRepository.load(processed, seed)

    assert repo.is_seed is True
    assert len(repo) == 2
    assert repo.supplementary_pairs == 0  # 전부가 시드라 '보조'라는 구분이 무의미하다


# --- 병합 (실제 데이터 있음) ---------------------------------------------------


def test_실제_데이터가_들어와도_영양제_조합이_남는다(dirs):
    """이 테스트가 이 파일의 존재 이유다."""
    processed, seed = dirs
    write_csv(
        processed / "interactions.csv",
        [{"ingredient_a": "와파린", "ingredient_b": "아스피린", "severity": 5, "source": "DUR"}],
    )
    write_csv(seed / "interactions_seed.csv", seed_rows())

    repo = InteractionRepository.load(processed, seed)

    assert repo.is_seed is False  # 1순위 데이터가 실제 데이터이므로
    assert repo.get("와파린", "오메가3") is not None
    assert repo.supplementary_pairs == 1


def test_같은_쌍은_더_심각한_등급이_남는다(dirs):
    processed, seed = dirs
    write_csv(
        processed / "interactions.csv",
        [{"ingredient_a": "와파린", "ingredient_b": "아스피린", "severity": 5, "source": "DUR"}],
    )
    write_csv(seed / "interactions_seed.csv", seed_rows())  # 같은 쌍이 4등급

    hit = InteractionRepository.load(processed, seed).get("와파린", "아스피린")

    assert hit.severity == 5
    assert hit.source == "DUR"


def test_시드가_더_심각하면_시드가_남는다(dirs):
    """등급 규칙은 출처가 아니라 심각도로 정한다. 안전한 쪽으로 기운다."""
    processed, seed = dirs
    write_csv(
        processed / "interactions.csv",
        [{"ingredient_a": "와파린", "ingredient_b": "아스피린", "severity": 2, "source": "DUR"}],
    )
    write_csv(seed / "interactions_seed.csv", seed_rows())

    hit = InteractionRepository.load(processed, seed).get("와파린", "아스피린")
    assert hit.severity == 4


def test_출처는_상호작용마다_따라간다(dirs):
    """섞인 데이터에서 어느 쪽에서 온 정보인지 응답으로 알 수 있어야 한다."""
    processed, seed = dirs
    write_csv(
        processed / "interactions.csv",
        [{"ingredient_a": "와파린", "ingredient_b": "아스피린", "severity": 5, "source": "DUR"}],
    )
    write_csv(seed / "interactions_seed.csv", seed_rows())

    repo = InteractionRepository.load(processed, seed)
    assert repo.get("와파린", "아스피린").source == "DUR"
    assert repo.get("와파린", "오메가3").source == "시드"


def test_시드_파일이_없어도_동작한다(dirs):
    processed, seed = dirs
    write_csv(
        processed / "interactions.csv",
        [{"ingredient_a": "와파린", "ingredient_b": "아스피린", "severity": 5, "source": "DUR"}],
    )

    repo = InteractionRepository.load(processed, seed)
    assert len(repo) == 1
    assert repo.supplementary_pairs == 0


def test_병합해도_쌍_조회는_순서에_무관하다(dirs):
    processed, seed = dirs
    write_csv(processed / "interactions.csv", [])
    write_csv(seed / "interactions_seed.csv", seed_rows())

    repo = InteractionRepository.load(processed, seed)
    assert repo.get("오메가3", "와파린") is not None
    assert repo.get("와파린", "오메가3") is not None


# --- 행 읽기 -----------------------------------------------------------------


def test_빈_행과_자기자신_쌍은_버린다(tmp_path):
    path = write_csv(
        tmp_path / "x.csv",
        [
            {"ingredient_a": "", "ingredient_b": "와파린", "severity": 3},
            {"ingredient_a": "와파린", "ingredient_b": "", "severity": 3},
            {"ingredient_a": "와파린", "ingredient_b": "와파린", "severity": 3},
            {"ingredient_a": "와파린", "ingredient_b": "아스피린", "severity": 3},
        ],
    )
    assert len(InteractionRepository.read_rows(path)) == 1


def test_등급은_1에서_5로_잘린다(tmp_path):
    path = write_csv(
        tmp_path / "x.csv",
        [
            {"ingredient_a": "가", "ingredient_b": "나", "severity": 9},
            {"ingredient_a": "다", "ingredient_b": "라", "severity": 0},
        ],
    )
    rows = InteractionRepository.read_rows(path)
    assert {r.severity for r in rows} == {5, 1}


def test_필수_컬럼이_없으면_읽기_전에_실패한다(tmp_path):
    path = tmp_path / "bad.csv"
    pd.DataFrame({"성분1": ["와파린"]}).to_csv(path, index=False, encoding="utf-8")

    with pytest.raises(ValueError, match="필수 컬럼"):
        InteractionRepository.read_rows(path)


def test_직접_생성한_저장소도_최고_등급을_남긴다():
    repo = InteractionRepository(
        [
            Interaction("와파린", "아스피린", 3),
            Interaction("아스피린", "와파린", 5),  # 순서만 뒤집힌 같은 쌍
        ]
    )
    assert len(repo) == 1
    assert repo.get("와파린", "아스피린").severity == 5
