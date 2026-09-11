# 배포

로컬 터널(cloudflared)은 **PC 가 서버**다. 노트북을 끄면 앱도 죽고, 터널을 다시 켤
때마다 주소가 바뀌어서 폰에 설치해 둔 앱이 깨진다. 그래서 배포가 필요하다.

기본 대상은 **Hugging Face Spaces**다. 신용카드가 필요 없고, 2 vCPU / 16GB RAM 이며,
Render 무료 티어처럼 15분마다 잠들지 않는다.

## 배포 전에 반드시 확인할 것

**`data/processed/` 의 세 파일이 저장소에 들어 있어야 한다.**

| 파일 | 없으면 |
|---|---|
| `interactions.csv` | 1,275쌍 → 시드 29쌍으로 추락 |
| `products.csv` | 제품명 검색 전멸 |
| `aliases_generated.csv` | 영문명 검색 불가 |

`.gitignore` 는 `data/processed/*` 를 막지만 이 셋만 예외로 뚫어 두었다.
배포 환경에는 공공데이터 API 키도 수집 스크립트도 없어서, 여기서 빠지면
되돌릴 방법이 없다. 커밋 전에 확인:

```bash
git ls-files data/processed
```

세 줄이 나와야 한다.

## Hugging Face Spaces

### 1. 계정과 Space

1. <https://huggingface.co/join> 가입 (이메일만)
2. <https://huggingface.co/new-space> 에서 Space 생성
   - **Space SDK: Docker** (Blank template)
   - Hardware: CPU basic (무료)
3. Settings → **Access Tokens** → New token → **Write** 권한

### 2. 환경변수

Space → Settings → **Variables and secrets**

| 이름 | 종류 | 값 |
|---|---|---|
| `SUPABASE_URL` | Variable | `https://<project>.supabase.co` |
| `SUPABASE_ANON_KEY` | Variable | `sb_publishable_…` (공개돼도 되는 키) |
| `APP_ENV` | Variable | `production` |
| `OPENAI_API_KEY` | **Secret** | 있으면. 없으면 템플릿 설명으로 동작 |

`SUPABASE_ANON_KEY` 는 브라우저에 노출되도록 설계된 공개 키라 Variable 로 둔다.
**`service_role` 키는 절대 넣지 마라** — `/api/config` 로 그대로 새어 나간다.

`DATA_GO_KR_SERVICE_KEY` 는 **넣을 필요가 없다.** 수집 스크립트에서만 쓰고
서버 런타임에는 안 쓴다.

### 3. push

```bash
git remote add space https://huggingface.co/spaces/<USER>/<SPACE>
git push space main
```

계정/비밀번호를 물으면 사용자명과 **토큰**(비밀번호 아님)을 넣는다.

빌드는 2~3분 걸린다. Space 페이지의 **Logs** 에서 진행을 볼 수 있다.

### 4. 확인

```
https://<USER>-<SPACE>.hf.space/api/health
```

`using_seed_data: false`, `interaction_pairs: 1275`, `products: 4763` 이면 성공.
`using_seed_data: true` 로 뜨면 위의 "배포 전에 확인할 것"을 놓친 것이다.

### 5. 폰에 설치

`https://<USER>-<SPACE>.hf.space` 를 폰에서 열고 **홈 화면에 추가**.
HTTPS 이고 주소가 바뀌지 않으므로 이번엔 설치가 유지된다.

## Render (대안)

`render.yaml` 이 이미 있다. GitHub/GitLab 저장소를 연결하면 자동으로 읽는다.
무료 티어는 15분 무활동 시 잠들고 깨는 데 ~50초 걸린다 — 시연 중에 걸리면 곤란하다.

## 데이터를 갱신한 뒤

수집·변환은 로컬에서 하고, 결과물만 커밋해서 다시 push 한다.

```bash
python scripts/fetch_dur_api.py
python scripts/build_ddi_matrix.py --input data/interim/dur_usjnt_taboo.csv
python scripts/build_products.py
git add data/processed && git commit -m "데이터 갱신" && git push space main
```
