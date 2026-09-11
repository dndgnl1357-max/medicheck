"""식약처 DUR 성분정보 오픈API → 원본 수집.

공공데이터포털의 "의약품안전사용서비스(DUR)성분정보" 에서 병용금기 목록을 받아
data/raw/ 에 원본 JSON 을, data/interim/ 에 평탄화한 CSV 를 남긴다.
여기서 만든 CSV 를 build_ddi_matrix.py 에 넣으면 interactions.csv 가 된다.

    # 키 확인용 한 페이지만
    python scripts/fetch_dur_api.py --max-pages 1

    # 전체 수집
    python scripts/fetch_dur_api.py

    # 이어받기 (이미 받은 페이지는 건너뛴다)
    python scripts/fetch_dur_api.py --resume

사전 준비:
  1. https://www.data.go.kr/data/15056780/openapi.do 에서 활용신청 (자동승인)
  2. .env 의 DATA_GO_KR_SERVICE_KEY 에 **디코딩된** 일반 인증키를 넣는다

## 이 스크립트가 의미를 해석하지 않는 이유

응답 필드명(INGR_KOR_NAME 인지 ingrKorName 인지)은 API 버전에 따라 흔들린다.
여기서는 받은 키를 그대로 CSV 컬럼으로 흘려보내고, "어느 컬럼이 성분 A/B 인가"
같은 판단은 build_ddi_matrix.py 한 곳에서만 한다. 수집과 해석을 섞으면
API 가 바뀔 때마다 두 군데를 고쳐야 한다.

그래서 첫 페이지의 필드명을 항상 화면에 찍는다 — 이름이 바뀌었으면 거기서 보인다.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # 윈도우 cp949 콘솔 대응

from app.config import get_settings  # noqa: E402

# 공공데이터포털 1471000(식약처) 서비스들. 페이지 구조와 오류 형식이 같아서
# 수집기 하나로 다 받는다. 필드명 해석은 여기서 하지 않는다(모듈 docstring 참고).
PRESETS = {
    "dur-usjnt": (
        "DURIrdntInfoService03",
        "getUsjntTabooInfoList02",
        "dur_usjnt_taboo",
    ),
    "dur-efcy": (
        "DURIrdntInfoService03",
        "getEfcyDplctInfoList02",
        "dur_efcy_dplct",
    ),
    "dur-pwnm": (
        "DURIrdntInfoService03",
        "getPwnmTabooInfoList02",
        "dur_pwnm_taboo",
    ),
    "easydrug": (
        "DrbEasyDrugInfoService",
        "getDrbEasyDrugList",
        "easydrug",
    ),
}
DEFAULT_PRESET = "dur-usjnt"

# 포털이 돌려주는 정상 코드. 그 외에는 전부 실패로 본다.
OK_CODE = "00"


class ApiError(RuntimeError):
    pass


def request_page(
    client: httpx.Client, url: str, key: str, page: int, rows: int
) -> dict[str, Any]:
    """한 페이지를 받아 body 를 돌려준다."""
    params = {
        "serviceKey": key,
        "type": "json",
        "pageNo": page,
        "numOfRows": rows,
    }
    r = client.get(url, params=params, timeout=30.0)
    r.raise_for_status()

    # 키가 틀리거나 트래픽을 초과하면 type=json 이어도 XML 에러를 돌려준다.
    text = r.text.lstrip()
    if text.startswith("<"):
        raise ApiError(_explain_xml_error(text))

    try:
        payload = r.json()
    except json.JSONDecodeError as exc:
        raise ApiError(f"JSON 으로 읽을 수 없는 응답입니다: {text[:200]}") from exc

    header = payload.get("header") or {}
    code = str(header.get("resultCode", "")).strip()
    if code and code != OK_CODE:
        raise ApiError(f"[{code}] {header.get('resultMsg', '알 수 없는 오류')}")

    body = payload.get("body")
    if body is None:
        raise ApiError(f"body 가 없습니다: {str(payload)[:200]}")
    return body


def _explain_xml_error(text: str) -> str:
    """포털 에러 XML 을 사람이 읽을 수 있는 안내로 바꾼다."""
    known = {
        "SERVICE_KEY_IS_NOT_REGISTERED_ERROR": (
            "등록되지 않은 서비스 키입니다. 활용신청이 승인됐는지, "
            "그리고 '인코딩' 키가 아니라 '디코딩' 키를 넣었는지 확인하세요."
        ),
        "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR": (
            "일일 트래픽(개발계정 10,000건)을 초과했습니다. 내일 --resume 로 이어받으세요."
        ),
        "SERVICE_ACCESS_DENIED_ERROR": "이 API 에 대한 접근 권한이 없습니다.",
    }
    for token, message in known.items():
        if token in text:
            return message
    return f"API 가 XML 오류를 돌려줬습니다: {text[:300]}"


def _unwrap(entry: Any) -> dict[str, Any] | None:
    """{"item": {...}} 처럼 한 겹 더 감싸인 경우를 벗긴다.

    이 API 는 실제로 items: [{"item": {...}}, ...] 로 온다 (XML 을 JSON 으로
    옮기면서 남은 껍데기). 벗기지 않으면 컬럼이 'item' 하나뿐인 CSV 가 나온다.
    """
    if not isinstance(entry, dict):
        return None
    if set(entry.keys()) == {"item"} and isinstance(entry["item"], dict):
        return entry["item"]
    return entry


def extract_items(body: dict[str, Any]) -> list[dict[str, Any]]:
    """items 가 dict 하나로 오기도 하고 리스트로 오기도 한다."""
    items = body.get("items")
    if not items:
        return []
    if isinstance(items, dict):  # 결과가 1건이면 dict 로 온다
        items = items.get("item", items)
    if isinstance(items, dict):
        items = [items]
    return [u for i in items if (u := _unwrap(i)) is not None]


def fetch(
    url: str,
    raw_dir: Path,
    key: str,
    rows: int,
    max_pages: int | None,
    resume: bool,
    delay: float,
) -> list[dict]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    collected: list[dict] = []
    total: int | None = None
    page = 1

    with httpx.Client(headers={"User-Agent": "medicheck/dev"}) as client:
        while True:
            if max_pages is not None and page > max_pages:
                print(f"--max-pages {max_pages} 도달, 중단합니다.")
                break

            cached = raw_dir / f"page_{page:05d}.json"
            if resume and cached.exists():
                body = json.loads(cached.read_text(encoding="utf-8"))
            else:
                body = request_page(client, url, key, page, rows)
                cached.write_text(
                    json.dumps(body, ensure_ascii=False), encoding="utf-8"
                )
                if delay:
                    time.sleep(delay)

            items = extract_items(body)
            if total is None:
                total = int(body.get("totalCount") or 0)
                print(f"전체 {total:,}건 / 페이지당 {rows}건")
                if items:
                    print(f"응답 필드: {list(items[0].keys())}\n")
            collected.extend(items)

            print(f"  page {page:>4} — 누적 {len(collected):,}건", end="\r")
            if not items or (total and len(collected) >= total):
                break
            page += 1

    print()
    return collected


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        print("수집된 행이 없습니다. CSV 를 만들지 않았습니다.")
        return

    # 페이지마다 필드가 빠질 수 있으므로 전체를 훑어 컬럼을 모은다.
    columns: list[str] = []
    for row in rows:
        for k in row:
            if k not in columns:
                columns.append(k)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in columns})

    print(f"\n저장 완료: {path} ({len(rows):,}행 / 컬럼 {len(columns)}개)")
    print(f"  컬럼: {columns}")
    print("\n다음 단계:")
    print(f"  python scripts/build_ddi_matrix.py --input {path} --source \"식약처 DUR 병용금기\"")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        default=DEFAULT_PRESET,
        help=f"받을 데이터 (기본 {DEFAULT_PRESET})",
    )
    parser.add_argument("--rows", type=int, default=100, help="페이지당 건수 (기본 100)")
    parser.add_argument("--max-pages", type=int, help="이 페이지 수까지만 (점검용)")
    parser.add_argument("--resume", action="store_true", help="이미 받은 페이지는 건너뛴다")
    parser.add_argument("--delay", type=float, default=0.2, help="페이지 사이 대기 초")
    parser.add_argument("--out", type=Path, help="기본값은 preset 이름을 따른다")
    args = parser.parse_args()

    service, operation, slug = PRESETS[args.preset]
    url = f"https://apis.data.go.kr/1471000/{service}/{operation}"
    raw_dir = Path("data/raw") / slug
    out_path = args.out or Path("data/interim") / f"{slug}.csv"
    print(f"[{args.preset}] {service}/{operation}")

    key = get_settings().data_go_kr_service_key
    if not key:
        print("DATA_GO_KR_SERVICE_KEY 가 비어 있습니다.")
        print("  1. https://www.data.go.kr/data/15056780/openapi.do 에서 활용신청")
        print("  2. .env 의 DATA_GO_KR_SERVICE_KEY 에 '디코딩' 일반 인증키를 넣기")
        return 1

    try:
        rows = fetch(
            url, raw_dir, key, args.rows, args.max_pages, args.resume, args.delay
        )
    except ApiError as exc:
        print(f"\n수집 실패: {exc}")
        return 1
    except httpx.HTTPError as exc:
        print(f"\n네트워크 오류: {exc}")
        return 1

    write_csv(rows, out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
