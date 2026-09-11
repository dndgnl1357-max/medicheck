# Medicheck

시민 맞춤형 복약 안전 가이드 — 성분 기반 약물·영양제 상호작용 위험도 분석 API.

## 빠른 시작

```powershell
# 의존성 (이미 .venv 가 만들어져 있음)
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 환경변수
Copy-Item .env.example .env    # 그리고 키를 채운다

# 서버
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
# → http://127.0.0.1:8000/      데모 화면
# → http://127.0.0.1:8000/docs  API 문서

# 테스트
.\.venv\Scripts\python.exe -m pytest
```

## 현재 상태

**식약처 DUR 병용금기 데이터가 적재되어 있다.** (오픈API, 2026-09 수집)

| | |
|---|---|
| 상호작용 | 1,275쌍 (DUR 1,250 + 시드 보조 25) |
| 성분 | 482개 |
| `using_seed_data` | `false` |

`data/processed/interactions.csv` 가 없으면 시드 29쌍으로 폴백하고,
그때는 `using_seed_data` 가 `true` 로 뜨며 `disclaimer` 앞에 경고가 붙는다.
데이터를 다시 받는 방법은 아래 "실제 데이터 넣기" 참고.

### 성분명 매칭이 실제로 얼마나 닿는가

DUR 데이터를 넣자마자 드러난 문제가 있다. 상호작용은 1,275쌍인데 동의어 사전에는
성분이 31개뿐이라, **처음에는 데이터의 대부분이 조회되지 않았다.** 지금은
`IngredientIndex` 가 동의어 사전과 상호작용 데이터의 성분명을 **합쳐서** 색인한다
(`deps.get_index()`).

별칭은 **세 소스를 합쳐** 쓴다 (`deps.get_index()`, 순서가 곧 우선순위).

| 소스 | 개수 | 만드는 법 |
|---|---|---|
| `data/seed/ingredient_synonyms.csv` | 94 | 손으로. 상품명·구어체·흔한 오타처럼 기계가 못 만드는 것 |
| `data/processed/aliases_generated.csv` | 458 | `scripts/build_aliases.py` — 원본의 영문명에서 자동 추출 |
| 상호작용 데이터의 성분명 자체 | 488 | 자동 |

같은 별칭이 겹치면 **손으로 쓴 쪽이 이긴다.** 사람이 일부러 넣은 매핑을 기계가
덮어쓰면, 고쳐도 다음 생성 때 되돌아가기 때문이다.

영문명 458개가 붙어서 `warfarin`, `Simvastatin` 같은 입력이 해석된다.
아직 없는 것은 **상품명**이다 (`타이레놀` → `아세트아미노펜`). 아래 "상품명 검색" 참고.

오타 구제는 유사도 0.8 이상에서만 된다. 한글 5글자에서 1글자 오타는 0.800 이라
걸리지만(`레보티록씬` → `레보티록신`), **4글자에서 1글자 오타는 0.75 라 안 걸린다**
(`아스피링` → 실패). 짧은 성분명일수록 오타에 약하다는 뜻이다.

## 구조

```
app/
  main.py            FastAPI 진입점
  config.py          .env 설정
  schemas.py         요청/응답 스키마 + 위험도 라벨
  api/
    routes.py        엔드포인트
    deps.py          의존성 주입 (데이터는 프로세스당 1회 로딩)
  core/
    normalize.py     성분명 정규화·매칭  ← 이 프로젝트의 핵심
    repository.py    상호작용 데이터 저장소
    scoring.py       종합 위험도 산출
    explain.py       LLM 설명 레이어 (근거 밖으로 못 나가게 막는 검증 포함)
    products.py      제품명 → 성분 확장 (타이레놀 → 아세트아미노펜)
    vision.py        약봉투 사진 → 약 이름 읽기 (OCR)
    graph.py         NetworkX 상호작용 그래프
    service.py       위 조각들을 묶는 서비스 계층
data/
  raw/               공공데이터 원본 (저장소에 안 올림)
  interim/           전처리 중간 산출물
  processed/         정제된 interactions.csv
  seed/              보조 시드 데이터 + SOURCES.md (출처 근거)
web/                 설치 가능한 웹앱(PWA). 빌드 도구·프레임워크·CDN 없음
  index.html         화면 전체 (CSS·JS 인라인)
  manifest.json      홈화면 설치 정보
  sw.js              서비스 워커 — 껍데기만 캐시하고 /api 는 절대 캐시 안 함
  icon.svg           앱 아이콘
scripts/
  fetch_dur_api.py      식약처 DUR 오픈API → 원본 수집 (키 필요)
  build_aliases.py      원본의 영문명 → 별칭 자동 생성 (API 호출 없음)
  build_ddi_matrix.py   원본 → interactions.csv 변환
  build_products.py     e약은요 원본 → products.csv (제품명 검색)
  check_llm.py          실제 OpenAI 호출 경로 점검 (키 필요)
tests/
```

## 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/health` | 상태 + 적재된 데이터 규모 |
| POST | `/api/analyze` | 성분 목록 → 위험도 분석 |
| GET | `/api/ingredients?q=` | 자동완성용 성분 검색 |
| GET | `/api/examples` | 적재된 데이터에서 실제 조합을 뽑아 준다 |
| GET | `/api/products?q=` | 제품명 검색 (시민은 성분명을 모른다) |
| POST | `/api/photo` | 약봉투 사진 → 약 이름 읽기 (multipart) |
| POST | `/api/explain` | 분석 결과를 시민의 언어로 풀어 설명 (LLM) |
| POST | `/api/graph` | 상호작용 네트워크 그래프 (cytoscape 형식) |

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"ingredients":["쿠마딘","와파린나트륨","레보티록씬","칼슘"]}'

# 같은 입력을 시민의 언어로 (키가 없으면 template 설명이 나온다)
curl -X POST http://127.0.0.1:8000/api/explain   -H "Content-Type: application/json"   -d '{"ingredients":["쿠마딘","아스피린"]}'
```

## 설계 메모

### 성분명 매칭 (`core/normalize.py`)

공공데이터의 성분 표기가 제각각(`와파린나트륨`, `Warfarin Sodium`, `와파린(나트륨)`)이라
정규화가 없으면 매칭률이 바닥난다. 3단계로 처리한다.

1. **정규화** — 전각→반각, 소문자화, 괄호·용량 표기 제거, 염 접미사 제거
2. **사전 조회** — `data/seed/ingredient_synonyms.csv` 의 별칭/영문명
3. **유사도 매칭** — difflib, 오타 구제용

염 접미사 제거에는 예외가 있다. `탄산수소나트륨`에서 `나트륨`을 떼면 `탄산수소`라는
다른 성분이 되므로 `_SALT_EXCEPTIONS` 로 막는다.

유사도 임계값은 0.8이다. 한글 5글자 성분명에서 오타 한 글자면 유사도가 0.8까지
떨어지기 때문에 그 이상으로 두면 흔한 오타를 놓친다. 대신 1·2위 후보가
다른 성분인데 점수 차가 0.05 미만이면 **매칭을 포기한다**. 약 이름은 한 글자 차이로
다른 약이 되는 경우가 많아, 애매할 때 아무거나 고르는 것보다 모른다고 답하는 편이 안전하다.

### 설명 생성 (`core/explain.py`)

`/api/analyze` 는 "와파린 × 아스피린 = 4등급"까지만 알려준다. 시민에게 필요한 건
"그래서 오늘 뭘 해야 하나"이므로, 그 위에 LLM 설명 레이어를 얹었다.

**LLM 은 새로운 상호작용을 만들어낼 수 없다.** 결정론적 레이어가 찾아낸 쌍만 근거로
넘기고, 모델은 그것을 풀어쓰기만 한다. 등급과 성분쌍은 모델 응답에서 다시 읽지 않고
원본을 쓰며, 모델이 근거에 없는 `pair_index` 를 지어내면 그 항목은 버린다. 약 정보에서
환각은 오답이 아니라 사고라서, 검증을 관대하게 두지 않았다.

`OPENAI_API_KEY` 가 없거나 호출이 실패하면 **템플릿 설명으로 내려앉는다.** 근거 데이터는
이미 손에 있으므로 말을 못 다듬는 것이 서비스를 멈출 이유는 되지 않는다. 설명 생성
실패로 이 엔드포인트가 5xx 를 내는 일은 없다. 어느 경로로 만들어진 설명인지는 응답의
`source` (`llm` / `template`) 와 `/api/health` 의 `llm_enabled` 로 확인한다.

실패 원인은 스택 트레이스가 아니라 조치 가능한 한 줄로 로그에 남는다
(크레딧 없음 / 키 오류 / 한도 초과 / 모델 없음). **실패는 캐시하지 않는다** —
결제한 뒤 다시 물었을 때 계속 템플릿만 나오면 안 되기 때문이다.

**키를 만드는 것은 무료지만 호출에는 크레딧이 든다.** 결제 전에 프롬프트를
검토하려면:

```powershell
.\.venv\Scripts\python.exe scripts\check_llm.py --preview   # 네트워크 안 탐, 0원
```

같은 근거에는 같은 설명이 나가도록 결과를 캐시한다(키: 근거 JSON + 모델명).
`temperature` 를 0.2 로 둔 것도 같은 이유다 — 같은 약 조합에 매번 다른 말이 나오면
신뢰를 잃는다.

### 데모 화면 (`web/index.html`)

빌드 도구도 프레임워크도 없는 정적 파일 하나다. `app.main` 이 `/` 에 mount 하며,
API 라우터보다 **뒤에** 붙여야 `/api` 와 `/docs` 를 삼키지 않는다.

화면은 성분 매칭이나 위험도 계산을 JS 로 다시 구현하지 않고 `/api/explain` 을
호출해 그 결과만 그린다(`/explain` 응답에 근거가 된 분석 결과가 함께 실려 있어
호출 한 번이면 된다). 이미 Python·Dart 로 `normalize()` 사본이 둘인 상황에서
세 번째를 만들지 않기 위해서다.

색 팔레트에서 **1등급은 회색이다. 초록이 아니다.** "정보 없음"을 초록으로 칠하는
순간 사용자는 그것을 "안전 확인됨"으로 읽는다. 상호작용이 0건일 때도
"등록된 상호작용이 없습니다"에서 끝내지 않고 그것이 안전을 뜻하지 않는다고 덧붙인다.

### 위험도 산출 (`core/scoring.py`)

- **최댓값 기반.** 위험은 평균되지 않는다. 4등급 하나가 1등급 열 개보다 위험하다.
- 3등급 이상이 3건 이상 겹치면 한 단계 올린다.
- 데이터에 없는 조합은 "안전"이 아니라 **"정보 없음"(1등급)** 이다. 이 구분을
  UI에서도 흐리지 말 것 — 없는 데이터를 안전으로 보이게 하면 서비스의 존재 이유가 무너진다.

## 실제 데이터 넣기

두 가지 경로가 있다. **성분 기반**이라는 점에서 둘 다 이 프로젝트에 맞는다.

### 경로 A — 파일 내려받기 (빠름)

[한국의약품안전관리원_병용금기약물](https://www.data.go.kr/data/15089525/fileData.do) ·
CSV 542,996행 · `성분명1` / `성분명2` / `금기사유` · **로그인·활용신청 불필요**

```powershell
# 받은 CSV 를 data\raw\ 에 두고
.\.venv\Scripts\python.exe scripts\build_ddi_matrix.py --input data\raw\병용금기약물.csv
```

⚠️ **이용허락범위 제2유형 — 출처표시 + 상업적 이용금지.** 상업 서비스로 쓰려면
한국의약품안전관리원 사전승인이 필요하다. 갱신주기도 "수시(1회성)"라 최신성이 보장되지 않는다.

### 경로 B — 오픈API (계속 갱신됨)

[식품의약품안전처_DUR성분정보](https://www.data.go.kr/data/15056780/openapi.do) ·
`DURIrdntInfoService03` / `getUsjntTabooInfoList02` · 무료, 개발계정 10,000건/일

```powershell
# 1. 위 링크에서 활용신청 (자동승인) → .env 의 DATA_GO_KR_SERVICE_KEY 에 '디코딩' 키
# 2. 키가 통하는지 한 페이지만
.\.venv\Scripts\python.exe scripts\fetch_dur_api.py --max-pages 1
# 3. 전체 수집 (트래픽 초과 시 다음 날 --resume)
.\.venv\Scripts\python.exe scripts\fetch_dur_api.py
# 4. 변환
.\.venv\Scripts\python.exe scripts\build_ddi_matrix.py --input data\interim\dur_usjnt_taboo.csv
```

수집과 해석을 나눠 둔 이유는 `fetch_dur_api.py` 의 docstring 에 적어 뒀다.
응답 필드명이 API 버전마다 흔들려서, 어느 컬럼이 성분 A/B 인지 판단하는 곳을
`build_ddi_matrix.py` 한 군데로 모았다.

### 공통

변환이 끝나면 서버를 재시작한다. `/api/health` 의 `using_seed_data` 가 `false` 가 되면 성공.

컬럼명 자동 추정은 지금까지 확인한 세 가지 표기(`성분명1/성분명2`,
`INGR_KOR_NAME/MIXTURE_INGR_KOR_NAME`, `주성분명/병용금기성분명`)를 알아본다.
새 표기를 만나면 `COLUMN_HINTS` 에 한 줄 추가하고 `tests/test_ingest.py` 의
`SOURCES` 에도 넣을 것. 실패하면 `--col-a`, `--col-b` 로 직접 지정할 수 있다.

### 별칭 생성 (API 호출 없음)

수집한 원본에는 성분마다 영문명이 함께 들어 있다. 다시 호출하지 않고 뽑아 쓴다.

```powershell
.\.venv\Scripts\python.exe scripts\build_aliases.py
# → data/processed/aliases_generated.csv (458개)
```

### 상품명 검색 — 아직 안 됨, 그리고 왜

시민은 성분명을 모른다. "아세트아미노펜"이 아니라 "타이레놀"을 안다.
지금은 상품명이 하나도 없어서 이런 입력이 전부 실패한다.

**DUR품목정보 API**(`DURPrdlstInfoService03`, 796,944건)에 `ITEM_NAME`(제품명) →
`INGR_KOR_NAME`(성분명) 매핑이 있고 **현재 키로 호출된다.** 그런데 확인해 보니:

| 검색어 | 결과 |
|---|---|
| 판콜 / 게보린 / 이지엔 | 있음 (이부프로펜·덱시부프로펜 = DUR 대상) |
| **타이레놀** | **0건** |
| **아로나민** | **0건** |

DUR품목정보는 **DUR 항목이 있는 성분의 제품만** 담는다. 아세트아미노펜은 병용금기가
없어서 타이레놀이 통째로 빠진다. 영양제도 마찬가지다. 게다가 `numOfRows` 상한이
100이라 전량을 받으려면 **7,970 페이지 = 일일 한도(10,000)의 80%** 를 쓴다.

제대로 된 출처는 **의약품 허가정보** 또는 **e약은요**인데, 둘 다 data.go.kr 에서
**별도 활용신청**이 필요하다 (현재 키로는 `SERVICE_KEY_IS_NOT_REGISTERED_ERROR`).
신청은 무료·자동승인이다.

정리하면 상품명 검색은 **API 접근 문제이지 설계 문제가 아니다.** 신청만 되면 붙는다.

### 아직 안 받은 DUR 데이터

같은 키로 호출되는 것을 확인만 해 두었다. 지금 적재된 것은 **병용금기 하나뿐**이라
등급 분포가 5등급 1,250쌍 / 나머지 25쌍으로 극단적으로 치우쳐 있다.

| 오퍼레이션 | 건수 | 성격 |
|---|---|---|
| `getEfcyDplctInfoList02` 효능군중복 | 405 | **쌍**. 종합감기약+타이레놀 같은 성분 중복 |
| `getPwnmTabooInfoList02` 임부금기 | 1,459 | 단일 성분 |
| `getCpctyAtentInfoList02` 용량주의 | 708 | 단일 성분 |
| `getSpcifyAgrdeTabooInfoList02` 특정연령대금기 | 233 | 단일 성분 |
| `getOdsnAtentInfoList02` 노인주의 | 112 | 단일 성분 |
| `getMdctnPdAtentInfoList02` 투여기간주의 | 98 | 단일 성분 |

**효능군중복만 쌍이고 나머지는 단일 성분 경고다.** 지금 스키마(`interactions` 는
성분 쌍 전용)로는 못 담는다. 임부·노인·연령 같은 맥락 경고를 넣으려면
새 개념이 필요하다.

### 시드는 자동으로 병합된다

**DUR 에는 영양제·건강기능식품 상호작용이 거의 없다.** 와파린 × 오메가3,
와파린 × 은행잎추출물 같은 조합이다. 그런데 그게 이 서비스의 차별점이라,
실제 데이터가 들어왔다고 시드를 통째로 버리면 기능이 후퇴한다.

그래서 `repository.load()` 는 정제 데이터를 1순위로 깔고 **시드를 보조로 병합한다.**
같은 쌍이 양쪽에 있으면 더 심각한 등급이 남고(출처가 아니라 심각도로 정한다),
정제 데이터에 없던 조합은 시드 것이 그대로 남는다.
`/api/health` 의 `supplementary_pairs` 가 그렇게 채워진 쌍의 수다.

각 상호작용의 `source` 는 응답에 그대로 실려 나가고 데모 화면에도 표시된다.
데이터가 섞이기 시작하면 등급만큼이나 출처가 중요해진다.

시드 29행의 `source` 는 근거 유형으로 붙여 두었다 (`제품 허가사항`,
`NIH ODS (오메가-3)`, `NCCIH (허브-약물 상호작용)`, `FDA 안전성 서한 (2009)` 등).
각 라벨이 무엇을 뜻하고 어디서 확인할 수 있는지는
**[data/seed/SOURCES.md](data/seed/SOURCES.md)** 에 정리해 두었다.
검증 범위와 다시 봐야 할 항목(메트포르민 × 조영제 등)도 같은 문서에 적혀 있다.

### 쓰지 않기로 한 것

Kaggle 의 DDI 데이터셋들은 대부분 DrugBank 파생본이라 재배포 라이선스가 불분명하고,
severity 가 아니라 상호작용 "유형"이라 등급으로 못 쓴다. 무엇보다 원본이 언제
어느 버전에서 떠졌는지 추적이 안 된다. **의료 정보에서 출처를 못 대는 데이터는 쓰지 않는다** —
`interactions.csv` 에 `source` 컬럼을 둔 것도 같은 이유다.

[DDInter 2.0](http://ddinter.scbdd.com/download/) 은 severity·기전·관리전략까지 갖춘
공개 DB(약 24만 DDI)지만 영문 일반명이라 `ingredient_synonyms.csv` 를 통해 붙여야 하고,
상업적 이용 가능 여부는 별도 약관을 확인해야 한다. 보조 출처 후보로만 남겨 둔다.

## 앱 (Flutter, 오프라인 우선)

앱은 서버 없이 내장 SQLite 하나로 성분 매칭과 상호작용 조회를 처리한다.
서버는 RAG·OCR 같은 "꼭 네트워크가 필요한 기능"에만 쓴다 — `/api/explain` 이 그 첫 기능이다.

```powershell
# 앱에 넣을 DB 굽기
.\.venv\Scripts\python.exe scripts\build_app_db.py
# → data/app/medicheck.db, data/app/normalize_fixture.json
```

DB 스키마는 `scripts/build_app_db.py` 의 `SCHEMA` 참고.

- `aliases.alias_norm` 은 **미리 정규화해서** 넣는다. 앱은 사용자 입력만 정규화하면
  되고, 염 접미사 처리를 사전 전체에 다시 돌릴 필요가 없다.
- `interactions` 의 성분 쌍은 항상 `(작은 id, 큰 id)` 순으로 저장한다.
  입력 순서에 상관없이 한 번의 조회로 찾기 위함.
- `meta` 테이블의 `is_seed` 가 `1` 이면 앱이 "개발용 데이터" 경고를 띄워야 한다.

### normalize() 이중 구현 주의

성분명 정규화는 Python(백엔드)과 Dart(앱) 양쪽에 존재하게 된다. 두 구현이 조용히
어긋나면 매칭이 통째로 틀어지므로, `data/app/normalize_fixture.json` 을 양쪽
테스트가 공유한다. Python 쪽 규칙을 바꾸면 픽스처를 다시 굽고 Dart 테스트를 돌릴 것.

## 아직 안 만든 것

- 이미지 OCR (GPT-4o Vision)
- 설명 레이어의 RAG 확장 (지금은 우리 DB 의 상호작용만 근거로 쓴다.
  식약처 허가사항·복약지도 문서를 검색해 근거에 얹는 것이 다음 단계)
- 앱(Flutter) 본체 — 지금 있는 건 웹 데모까지다
- DB (현재는 CSV를 메모리에 올려 쓴다. 데이터가 수십만 행이 되면 PostgreSQL로 옮길 것)
- 사용자/프로필/복약 알림

## 앱 (PWA)

`web/` 이 그대로 설치 가능한 웹앱이다. 휴대폰 브라우저에서 열고 "홈 화면에 추가"
하면 앱처럼 뜬다. **설치에는 HTTPS 또는 localhost 가 필요하다** — 같은 와이파이의
`http://<PC주소>:8000` 으로는 보이기는 해도 설치는 되지 않는다.

```powershell
# 휴대폰에서도 열리게 (같은 와이파이)
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 내 약은 기기에만 저장된다

`localStorage` 에만 넣고 서버로 보내지 않는다. 복약 정보는 민감정보라, 기기 밖으로
나가지 않으면 유출 위험도 보관 책임도 생기지 않는다. 로그인을 만들지 않은 이유다.
저장이 막힌 환경(시크릿 모드 등)에서도 화면은 그대로 동작한다.

### 서비스 워커가 캐시하지 않는 것

`/api/*` 는 **절대 캐시하지 않는다.** 오래된 위험도를 최신인 것처럼 보여주면
서비스의 존재 이유가 무너진다. 오프라인이면 화면이 "지금은 확인할 수 없다" 고
말한다 — 조용히 옛 답을 주지 않는다.

README 앞부분의 "오프라인 우선"은 앱에 SQLite 를 내장하는 Flutter 설계다.
지금 PWA 는 **껍데기만 오프라인**이고 분석에는 서버가 필요하다.

### 사진 인식 (`core/vision.py`)

약봉투를 찍으면 약 이름을 읽는다. 두 가지를 지킨다.

**모델은 사진에 적힌 글자만 읽는다.** 위험도를 매기지도, 약을 설명하지도 않는다.
읽어낸 이름은 평소와 똑같이 성분 매칭을 거치므로, 분석의 근거는 여전히 공공데이터다.

**자동으로 넣지 않는다.** 읽은 결과를 버튼으로 보여주고 사용자가 눌러야 추가된다.
잘못 읽은 약이 조용히 분석에 들어가면 안 된다.

OCR 은 폴백이 없다. 설명 레이어는 템플릿으로 내려앉을 수 있지만 사진은 읽은 척할
방법이 없어서, 크레딧이 없으면 `available=false` 와 사유를 돌려준다.
**빈 목록을 성공처럼 돌려주지 않는다** — 사용자가 "약이 없다" 로 오해한다.

### 제품명 검색 (`core/products.py`)

e약은요 4,780품목에서 만든 `data/processed/products.csv` 를 쓴다.
성분은 제품명 끝 괄호에서 뽑는다 (`타이레놀정500밀리그램(아세트아미노펜)`).
4,763건 중 2,049건에서 성분이 나온다. 못 뽑은 제품은 검색은 되지만 분석에는
쓸 수 없고, 그 사실이 `analyzable: false` 로 응답에 드러난다.

**애매하면 펼치지 않는다.** "아로나민"은 골드·실버·씨플러스의 성분이 서로 달라서
자동으로 고르지 않고 후보를 돌려준다. 잘못 펼치면 없는 위험을 경고하거나 있는
위험을 놓친다. `normalize.py` 의 애매성 처리와 같은 원칙이다.

제품은 성분을 여러 개 가지므로(복합제) 성분 별칭 사전에 넣지 않고 **입력을 성분
목록으로 펼치는 앞단**으로 분리했다. 종합감기약을 성분 하나로 매핑하면 나머지
성분의 상호작용을 통째로 놓친다.

## 서브에이전트

`.claude/agents/` 에 넷을 두었다. 담당이 겹치지 않게 나눠 놓았다.

| 에이전트 | 담당 | 쓰기 권한 |
|---|---|---|
| `backend-dev` | `app/`, `scripts/`, `tests/`, `data/` | 있음 |
| `frontend-dev` | `web/` | 있음 |
| `evidence-checker` | 상호작용 근거·등급 검증 | **없음** (근거와 제안만) |
| `safety-copy-reviewer` | 사용자에게 보이는 문구 검토 | **없음** |

작업용 둘은 서로의 영역을 건드리지 않는다. 프론트가 API 응답 형식을 바꿔야 하면
직접 고치지 않고 무엇이 왜 필요한지 보고한다 — 화면 편의 때문에 서버 계약이
조용히 바뀌는 일을 막기 위함이다.

검토용 둘에 쓰기 권한을 주지 않은 이유는 다르다. 등급 변경은 임상 판단이고
문구 수정은 제품 결정이라, 사람이 보고 정해야 한다.

각 파일에 이 프로젝트에서 **실제로 데인 것들**을 적어 두었다 (적재 규칙 이중화로
앱 DB 가 영양제 데이터를 잃은 일, 영문명 컬럼을 성분명으로 고른 일 등).

## 면책

본 서비스의 결과는 공개 데이터에 기반한 참고 정보이며 의학적 진단·처방이 아니다.
