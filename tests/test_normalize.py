from app.core.normalize import IngredientIndex, normalize


class TestNormalize:
    def test_염_접미사와_용량_표기를_떼어낸다(self):
        assert normalize("와파린나트륨 5mg (정제)") == "와파린"
        assert normalize("Warfarin  Sodium") == "warfarin"

    def test_대소문자와_공백_차이를_흡수한다(self):
        assert normalize("Vitamin  K") == normalize("vitamink")

    def test_전각문자를_반각으로_바꾼다(self):
        assert normalize("ＷＡＲＦＡＲＩＮ") == "warfarin"
        assert normalize("아스피린　１００") == "아스피린100"

    def test_한_글자_단위기호_용량을_제거한다(self):
        # 약 포장·처방전에 ㎎ ㎍ ㎖ 가 실제로 쓰인다.
        # ㎍ 는 NFKC 로 'μg'(그리스 문자 뮤)가 되므로 'ug' 만으로는 안 걸린다.
        assert normalize("타이레놀 500㎎") == "타이레놀"
        assert normalize("비타민D 25㎍") == "비타민d"
        assert normalize("마그네슘 10㎖") == "마그네슘"
        assert normalize("비타민K 1000IU") == "비타민k"

    def test_그_자체가_성분인_염은_자르지_않는다(self):
        assert normalize("탄산수소나트륨") == "탄산수소나트륨"
        assert normalize("탄산칼슘") == "탄산칼슘"

    def test_짧은_이름은_통째로_잘리지_않는다(self):
        assert normalize("나트륨") == "나트륨"

    def test_빈_입력은_빈_문자열(self):
        assert normalize("") == ""
        assert normalize("!!!") == ""


class TestIngredientIndex:
    def index(self) -> IngredientIndex:
        return IngredientIndex(
            {
                "와파린": "와파린",
                "warfarin": "와파린",
                "쿠마딘": "와파린",
                "아스피린": "아스피린",
                "레보티록신": "레보티록신",
            }
        )

    def test_표준명은_exact_로_매칭된다(self):
        m = self.index().resolve("와파린")
        assert m.canonical == "와파린"
        assert m.method == "exact"
        assert m.confidence == 1.0

    def test_별칭은_synonym_으로_매칭된다(self):
        m = self.index().resolve("쿠마딘")
        assert m.canonical == "와파린"
        assert m.method == "synonym"

    def test_염_형태_표기도_같은_성분으로_본다(self):
        assert self.index().resolve("와파린나트륨").canonical == "와파린"

    def test_오탈자는_fuzzy_로_구제한다(self):
        m = self.index().resolve("레보티록씬")
        assert m.canonical == "레보티록신"
        assert m.method == "fuzzy"
        assert 0.8 <= m.confidence < 1.0

    def test_비슷한_후보가_갈리면_매칭하지_않는다(self):
        # 비타민K / 비타민D 처럼 한 글자만 다른 성분이 함께 있으면
        # 아무거나 고르지 말고 모른다고 답해야 한다.
        index = IngredientIndex(
            {"비타민K": "비타민K", "비타민D": "비타민D", "비타민E": "비타민E"},
            fuzzy_cutoff=0.6,
        )
        m = index.resolve("비타민")
        assert m.canonical is None
        assert m.method == "none"

    def test_모르는_성분은_none(self):
        m = self.index().resolve("존재하지않는성분이름")
        assert m.canonical is None
        assert m.method == "none"
        assert not m.ok

    def test_resolve_many_는_입력_순서를_유지한다(self):
        results = self.index().resolve_many(["아스피린", "쿠마딘"])
        assert [r.canonical for r in results] == ["아스피린", "와파린"]


def test_상호작용_데이터의_성분도_색인에_들어간다(tmp_path):
    """동의어 사전에만 의존하면 적재한 데이터의 대부분이 조회되지 않는다.

    실제로 DUR 데이터 478개 성분 중 사전에 있는 31개만 잡혔다.
    """
    csv = tmp_path / "syn.csv"
    csv.write_text("alias,canonical\n와파린,와파린\n쿠마딘,와파린\n", encoding="utf-8")

    index = IngredientIndex.from_csv(csv, extra_canonicals=["에르고타민", "수마트립탄"])

    assert index.canonicals == {"와파린", "에르고타민", "수마트립탄"}
    assert index.resolve("에르고타민").canonical == "에르고타민"
    assert index.resolve("쿠마딘").canonical == "와파린"  # 기존 별칭도 그대로


def test_추가_성분이_기존_별칭을_덮어쓰지_않는다(tmp_path):
    """사전이 우선이다. 데이터에 있는 이름이 별칭 매핑을 밀어내면 안 된다."""
    csv = tmp_path / "syn.csv"
    csv.write_text("alias,canonical\n쿠마딘,와파린\n", encoding="utf-8")

    index = IngredientIndex.from_csv(csv, extra_canonicals=["쿠마딘"])
    assert index.resolve("쿠마딘").canonical == "와파린"


def test_손으로_쓴_사전이_자동_생성물을_이긴다(tmp_path):
    """사람이 일부러 넣은 매핑을 기계가 덮어쓰면 고쳐도 다음 생성 때 되돌아간다."""
    hand = tmp_path / "seed.csv"
    hand.write_text("alias,canonical\naspirin,아스피린\n", encoding="utf-8")
    generated = tmp_path / "gen.csv"
    generated.write_text("alias,canonical\naspirin,엉뚱한성분\n", encoding="utf-8")

    index = IngredientIndex.from_csvs([hand, generated])
    assert index.resolve("aspirin").canonical == "아스피린"


def test_자동_생성물의_새_별칭은_들어온다(tmp_path):
    hand = tmp_path / "seed.csv"
    hand.write_text("alias,canonical\n와파린,와파린\n", encoding="utf-8")
    generated = tmp_path / "gen.csv"
    generated.write_text("alias,canonical\nWarfarin,와파린\n", encoding="utf-8")

    index = IngredientIndex.from_csvs([hand, generated])
    assert index.resolve("warfarin").canonical == "와파린"


def test_없는_별칭_파일은_건너뛴다(tmp_path):
    """자동 생성물은 아직 안 만들어졌을 수 있다. 그래도 서버는 떠야 한다."""
    hand = tmp_path / "seed.csv"
    hand.write_text("alias,canonical\n와파린,와파린\n", encoding="utf-8")

    index = IngredientIndex.from_csvs([hand, tmp_path / "없는파일.csv"])
    assert index.resolve("와파린").canonical == "와파린"
