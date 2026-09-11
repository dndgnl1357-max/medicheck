from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["interaction_pairs"] > 0


def test_분석은_별칭_입력도_해석한다():
    r = client.post("/api/analyze", json={"ingredients": ["쿠마딘", "aspirin"]})
    assert r.status_code == 200
    body = r.json()
    assert [m["canonical"] for m in body["matched"]] == ["와파린", "아스피린"]
    assert body["overall_risk"] == 4
    assert len(body["interactions"]) == 1


def test_모르는_성분은_unmatched_로_돌아온다():
    r = client.post("/api/analyze", json={"ingredients": ["와파린", "듣도보도못한것"]})
    body = r.json()
    assert body["unmatched"] == ["듣도보도못한것"]


def test_상호작용이_없으면_1등급():
    r = client.post("/api/analyze", json={"ingredients": ["비타민C"]})
    body = r.json()
    assert body["overall_risk"] == 1
    assert body["interactions"] == []


def test_복용_간격_안내가_포함된다():
    r = client.post("/api/analyze", json={"ingredients": ["레보티록신", "칼슘"]})
    body = r.json()
    assert body["timing_advice"]
    assert "4시간" in body["timing_advice"][0]


def test_시드_데이터_사용_시_경고가_붙는다():
    r = client.post("/api/analyze", json={"ingredients": ["와파린"]})
    body = r.json()
    if client.get("/api/health").json()["using_seed_data"]:
        assert "개발용 시드" in body["disclaimer"]


def test_빈_입력은_422():
    assert client.post("/api/analyze", json={"ingredients": []}).status_code == 422


def test_그래프_엔드포인트():
    r = client.post("/api/graph", json={"ingredients": ["와파린", "아스피린", "오메가3"]})
    assert r.status_code == 200
    body = r.json()
    # 와파린-아스피린, 와파린-오메가3 두 쌍만 데이터에 있다
    assert len(body["nodes"]) == 3
    assert len(body["edges"]) == 2


def test_데모_화면이_루트에서_뜬다():
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Medicheck" in r.text


def test_정적_mount가_api를_삼키지_않는다():
    """StaticFiles 를 "/" 에 붙였으므로 순서가 틀어지면 API 가 먼저 죽는다."""
    assert client.get("/api/health").status_code == 200
    assert client.get("/docs").status_code == 200


def test_예시는_실제_데이터에서_나온다():
    r = client.get("/api/examples?count=3&min_severity=4")
    assert r.status_code == 200
    body = r.json()
    assert body["total_available"] > 0
    assert len(body["examples"]) == 3
    for ex in body["examples"]:
        assert len(ex["ingredients"]) == 2
        assert ex["severity"] >= 4
        # 예시로 준 조합은 실제로 분석되어야 한다 (장식이 아니어야 한다)
        a = client.post("/api/analyze", json={"ingredients": ex["ingredients"]}).json()
        assert a["unmatched"] == []
        assert len(a["interactions"]) == 1


def test_예시_개수는_요청한_만큼():
    assert len(client.get("/api/examples?count=1").json()["examples"]) == 1
    assert len(client.get("/api/examples?count=8").json()["examples"]) == 8


def test_예시_개수_상한을_넘기면_422():
    assert client.get("/api/examples?count=99").status_code == 422


def test_PWA_파일들이_서빙된다():
    """설치 가능한 앱이 되려면 manifest 와 service worker 가 열려야 한다."""
    for path, token in [
        ("/manifest.json", "Medicheck"),
        ("/sw.js", "medicheck-shell"),
        ("/icon.svg", "<svg"),
    ]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert token in r.text, path


def test_서비스워커는_api를_캐시하지_않는다():
    """오래된 위험도를 최신인 것처럼 보여주면 안 된다."""
    sw = client.get("/sw.js").text
    assert 'url.pathname.startsWith("/api/")' in sw


def test_config는_공개_설정만_준다():
    """service_role 키가 실수로 새어 나가면 RLS 가 통째로 무력화된다."""
    body = client.get("/api/config").json()
    assert set(body) == {"auth"}
    assert set(body["auth"]) == {"enabled", "url", "anon_key"}


def test_로그인_미설정이면_키를_비워_보낸다():
    from app.config import get_settings

    settings = get_settings()
    body = client.get("/api/config").json()["auth"]
    if not settings.auth_enabled:
        assert body["enabled"] is False
        assert body["url"] == "" and body["anon_key"] == ""


def test_config에_비밀값이_섞이지_않는다():
    """OpenAI 키나 공공데이터 키가 브라우저로 나가면 안 된다."""
    raw = client.get("/api/config").text
    for secret in ("sk-", "OPENAI", "DATA_GO_KR", "service_role"):
        assert secret not in raw


def test_안전_정보는_로그인_없이도_열린다():
    """복약 안전 정보를 로그인 뒤에 숨기지 않는다."""
    assert client.post("/api/analyze", json={"ingredients": ["와파린"]}).status_code == 200
    assert client.get("/").status_code == 200
