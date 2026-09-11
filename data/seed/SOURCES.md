# 시드 데이터 출처

`interactions_seed.csv` 의 `source` 컬럼에 쓰이는 라벨이 각각 무엇을 뜻하는지,
그리고 어디서 확인할 수 있는지 적어 둔다.

이 파일이 있는 이유는 하나다. **출처를 못 대는 의료 정보는 쓰지 않는다.**
라벨만 붙이고 근거 문서를 안 남기면 "개발용 시드"라고 써 두는 것과 다를 게 없다.

## 라벨

| 라벨 | 뜻 | 확인처 |
|---|---|---|
| `제품 허가사항` | 해당 의약품의 허가된 사용상 주의사항 '상호작용' 항에 기재된 조합 | [의약품안전나라](https://nedrug.mfds.go.kr) 에서 성분명으로 검색 |
| `제품 허가사항 (병용금기)` | 허가사항에서 병용을 **금기**로 명시한 조합 | 위와 같음 · [DUR품목검색](https://nedrug.mfds.go.kr/searchDur) |
| `FDA 라벨 (4시간 분리 권고)` | 레보티록신 라벨이 4시간 이상 간격을 명시한 조합 | [Levothyroxine 처방정보](https://www.accessdata.fda.gov/scripts/cder/daf/) |
| `FDA 안전성 서한 (2009)` | 클로피도그렐 × 오메프라졸 — CYP2C19 억제로 활성대사체 생성 저해 | [FDA DSC](https://www.fda.gov/drugs/drug-safety-and-availability) · [ACCF/AHA Clinical Alert](https://www.ahajournals.org/doi/10.1161/cir.0b013e3181ee08ed) |
| `NIH ODS (오메가-3)` | 미국 국립보건원 식이보충제국 오메가-3 팩트시트 | [ODS Omega-3 Health Professional Fact Sheet](https://ods.od.nih.gov/factsheets/Omega3FattyAcids-HealthProfessional/) |
| `NIH ODS (미네랄)` | 철·아연·칼슘·마그네슘 팩트시트의 흡수 경쟁 기술 | [ODS Fact Sheets](https://ods.od.nih.gov/factsheets/list-all/) |
| `NCCIH (허브-약물 상호작용)` | 은행잎·세인트존스워트 등 생약 성분 | [Herb-Drug Interactions: What the Science Says](https://www.nccih.nih.gov/health/providers/digest/herb-drug-interactions-science) · [Ginkgo](https://www.nccih.nih.gov/health/ginkgo) |
| `ACR 조영제 지침` | 미국영상의학회 조영제 매뉴얼 (메트포르민) | [ACR Manual on Contrast Media](https://www.acr.org/Clinical-Resources/Contrast-Manual) |

## 검증 범위에 대한 정직한 고지

라벨은 **근거의 유형**을 가리킨다. 개별 행이 특정 문서의 몇 페이지에 있는지까지
대조하지는 않았다. 클러스터 단위(항응고제-보충제, 갑상선호르몬-미네랄,
퀴놀론/테트라사이클린 킬레이트, CYP3A4 스타틴, 미네랄 흡수 경쟁)로 확인했다.

**국내 DUR 고시 여부는 아직 확정되지 않았다.** `제품 허가사항 (병용금기)` 라벨은
해당 조합이 허가사항상 금기라는 뜻이지, 심평원/식약처 DUR 병용금기 고시 목록에
올라 있다는 뜻이 아니다. 실제 DUR 데이터를 적재하면(README "실제 데이터 넣기")
그 조합들은 정식 출처로 덮어써진다.

## 다시 봐야 할 항목

- **메트포르민 × 조영제 — 4등급에서 3등급으로 내렸다 (2026-09).** ACR 지침이
  바뀌어 eGFR ≥ 30 이고 AKI 가 없으면 조영제 전후로 메트포르민을 **중단하지 않는다**.
  eGFR 30–44 구간의 일괄 중단 권고는 철회됐다. 모두에게 "높은 주의"를 띄우는 건
  과했다. 기전·권고 문구도 현행 지침에 맞춰 다시 썼다.
  다만 **조영제는 시민이 스스로 복용하는 것이 아니다.** 이 조합을 앱에 남길지는
  아직 결정하지 않았다.

- **와파린 × 오메가-3 (현재 3등급)** — ODS 는 2~15 g/일에서 출혈시간이 늘 수 있다고
  하지만, 3~6 g/일에서는 항응고 상태에 유의한 영향이 없다는 연구가 다수다.
  기전 문구에 "고용량"이 들어 있어 완전히 틀리진 않으나, 3등급이 다소 높을 수 있다.

- **레보티록신 × 마그네슘 (현재 `제품 허가사항`)** — FDA 라벨이 4시간 분리를 명시한
  것은 칼슘탄산염·황산철·세벨라머·란타넘이다. 마그네슘은 제산제 항목으로 다뤄지므로
  같은 `FDA 라벨 (4시간 분리 권고)` 라벨을 붙이지 않았다.

## 라벨을 고칠 때

`source` 를 바꾸면 앱 DB 도 다시 구워야 한다.

```powershell
.\.venv\Scripts\python.exe scripts\build_app_db.py
```
