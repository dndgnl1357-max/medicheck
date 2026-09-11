from app.core.repository import Interaction, InteractionRepository
from app.core.scoring import overall_risk, timing_advice


def make(a: str, b: str, severity: int, interval: float | None = None) -> Interaction:
    return Interaction(a, b, severity, min_interval_hours=interval)


class TestOverallRisk:
    def test_상호작용이_없으면_1등급_정보없음(self):
        assert overall_risk([]) == 1

    def test_평균이_아니라_최댓값을_쓴다(self):
        assert overall_risk([make("a", "b", 1), make("c", "d", 4)]) == 4

    def test_중등도_이상이_3건_겹치면_한_단계_올린다(self):
        items = [make("a", "b", 3), make("c", "d", 3), make("e", "f", 3)]
        assert overall_risk(items) == 4

    def test_경미한_상호작용은_여러_건이어도_올리지_않는다(self):
        items = [make("a", "b", 2)] * 5
        assert overall_risk(items) == 2

    def test_5등급을_넘지_않는다(self):
        items = [make("a", "b", 5), make("c", "d", 5), make("e", "f", 5)]
        assert overall_risk(items) == 5


class TestTimingAdvice:
    def test_간격_정보가_있는_쌍만_문장을_만든다(self):
        items = [make("레보티록신", "칼슘", 3, 4), make("와파린", "아스피린", 4)]
        lines = timing_advice(items)
        assert len(lines) == 1
        assert "4시간" in lines[0]


class TestRepository:
    def repo(self) -> InteractionRepository:
        return InteractionRepository(
            [make("와파린", "아스피린", 4), make("철분", "칼슘", 2, 2)]
        )

    def test_순서에_상관없이_같은_쌍을_찾는다(self):
        r = self.repo()
        assert r.get("와파린", "아스피린") is not None
        assert r.get("아스피린", "와파린") is not None

    def test_같은_쌍이_중복되면_더_심각한_등급이_남는다(self):
        r = InteractionRepository([make("a", "b", 2), make("b", "a", 5)])
        assert len(r) == 1
        assert r.get("a", "b").severity == 5

    def test_find_all_은_심각한_순으로_정렬한다(self):
        found = self.repo().find_all(["칼슘", "철분", "아스피린", "와파린"])
        assert [i.severity for i in found] == [4, 2]

    def test_중복_입력은_한_번만_센다(self):
        found = self.repo().find_all(["와파린", "와파린", "아스피린"])
        assert len(found) == 1
