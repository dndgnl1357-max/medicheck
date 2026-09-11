"""앱에 내장할 SQLite DB가 제대로 구워지는지 검증한다.

이 DB는 앱에 통째로 실려 나가므로, 여기서 틀리면 사용자 기기에서 틀린다.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from build_app_db import build, write_fixture  # noqa: E402

DATA_DIR = PROJECT_ROOT / "data"


@pytest.fixture(scope="module")
def db(tmp_path_factory) -> sqlite3.Connection:
    out = tmp_path_factory.mktemp("appdb") / "medicheck.db"
    build(out, DATA_DIR)
    conn = sqlite3.connect(out)
    yield conn
    conn.close()


def meta(db: sqlite3.Connection) -> dict[str, str]:
    return dict(db.execute("SELECT key, value FROM meta"))


class TestSchema:
    def test_필요한_테이블이_모두_있다(self, db):
        tables = {
            r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"ingredients", "aliases", "interactions", "meta"} <= tables

    def test_메타에_스키마_버전과_시드_여부가_있다(self, db):
        m = meta(db)
        assert m["schema_version"] == "1"
        assert m["is_seed"] in {"0", "1"}

    def test_메타의_건수가_실제_행수와_일치한다(self, db):
        m = meta(db)
        ingredients = db.execute("SELECT COUNT(*) FROM ingredients").fetchone()[0]
        interactions = db.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
        assert int(m["ingredient_count"]) == ingredients
        assert int(m["interaction_count"]) == interactions


class TestIntegrity:
    def test_상호작용의_성분_id는_항상_존재한다(self, db):
        orphan = db.execute(
            "SELECT COUNT(*) FROM interactions x"
            " WHERE x.ingredient_a_id NOT IN (SELECT id FROM ingredients)"
            "    OR x.ingredient_b_id NOT IN (SELECT id FROM ingredients)"
        ).fetchone()[0]
        assert orphan == 0

    def test_성분_쌍은_항상_작은_id가_앞에_온다(self, db):
        bad = db.execute(
            "SELECT COUNT(*) FROM interactions WHERE ingredient_a_id >= ingredient_b_id"
        ).fetchone()[0]
        assert bad == 0

    def test_같은_정규화_키가_두_성분을_가리키지_않는다(self, db):
        # 이게 깨지면 앱에서 같은 입력이 두 성분으로 조회돼 결과가 갈린다.
        dupes = db.execute(
            "SELECT alias_norm, COUNT(DISTINCT ingredient_id) c"
            " FROM aliases GROUP BY alias_norm HAVING c > 1"
        ).fetchall()
        assert dupes == []

    def test_모든_표준명은_자기_자신으로_검색된다(self, db):
        missing = db.execute(
            "SELECT i.name FROM ingredients i"
            " WHERE NOT EXISTS (SELECT 1 FROM aliases a"
            "   WHERE a.ingredient_id = i.id AND a.alias_norm = i.name_norm)"
        ).fetchall()
        assert missing == []

    def test_위험도는_1에서_5_사이다(self, db):
        bad = db.execute(
            "SELECT COUNT(*) FROM interactions WHERE severity NOT BETWEEN 1 AND 5"
        ).fetchone()[0]
        assert bad == 0


class TestAppQueries:
    """앱이 실제로 던질 쿼리를 그대로 흉내낸다."""

    def resolve(self, db, alias_norm: str):
        return db.execute(
            "SELECT i.id, i.name FROM aliases a"
            " JOIN ingredients i ON i.id = a.ingredient_id"
            " WHERE a.alias_norm = ?",
            (alias_norm,),
        ).fetchone()

    def test_별칭으로_표준명을_찾는다(self, db):
        assert self.resolve(db, "쿠마딘")[1] == "와파린"

    def test_염_접미사가_정규화된_키로_찾힌다(self, db):
        # 앱은 "와파린나트륨" 입력을 normalize 해 "와파린" 으로 조회한다
        assert self.resolve(db, "와파린")[1] == "와파린"

    def test_두_성분의_상호작용을_한_번에_조회한다(self, db):
        a = self.resolve(db, "레보티록신")[0]
        b = self.resolve(db, "칼슘")[0]
        lo, hi = min(a, b), max(a, b)
        row = db.execute(
            "SELECT severity, min_interval_hours FROM interactions"
            " WHERE ingredient_a_id = ? AND ingredient_b_id = ?",
            (lo, hi),
        ).fetchone()
        assert row == (3, 4.0)


def test_정규화_픽스처가_생성된다(tmp_path):
    """Dart 구현이 Python 과 어긋나지 않게 하는 공유 픽스처."""
    path = tmp_path / "normalize_fixture.json"
    write_fixture(path)

    cases = json.loads(path.read_text(encoding="utf-8"))
    assert len(cases) > 5
    assert all({"input", "expected"} == set(c) for c in cases)

    by_input = {c["input"]: c["expected"] for c in cases}
    assert by_input["와파린나트륨 5mg (정제)"] == "와파린"
    assert by_input["탄산수소나트륨"] == "탄산수소나트륨"
