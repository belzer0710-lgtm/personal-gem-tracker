# 로스트아크 4티어 보석 시세 자동 기록 (초보자용)

**목표:** 컴퓨터를 꺼도 GitHub Actions가 매일 밤 보석 가격과 현재 등록 매물 수를 수집해서 CSV로 저장.

- 수집 시각: 매일 **한국시간 21:17** (GitHub 일정 지연 가능)
- 보석: 4티어 **8·9·10레벨 겁화/작열** 총 6종
- 데이터: 즉시구매 최저 호가, 현재 등록 건수(`TotalCount`), 첫 페이지 최저 3개 호가의 중앙값
- 저장 위치: `data/gems.csv` (누적 기록), `data/latest.md` (최근 보고서)
- 자동 수집 종료: **2026-11-15** 마지막 수집 후 다음 날부터 코드가 수집을 건너뜀. **GitHub의 워크플로 자체를 끄려면 Actions > Lost Ark Gem Snapshot > ··· > Disable workflow**를 눌러야 함.
- 데이터 출처: [로스트아크 공식 Open API](https://developer-lostark.game.onstove.com/)

> **중요:** 공식 API `TotalCount`는 조회 시점에 *등록된 경매 항목 건수*야. 하루 거래량/신규등록량/체결 수량이 아니야. 가격도 거래 체결가가 아니라 즉시구매 호가야. API 호출이 실패하면 그 날 데이터는 기록하지 않음.

## 1) STOVE에서 공식 API 키 만들기

1. [로스트아크 API 개발자 포털](https://developer-lostark.game.onstove.com/)에서 로그인.
2. **MY CLIENTS → CREATE A NEW CLIENT**로 새 클라이언트를 생성.
3. 클라이언트 이름 예시: `personal-gem-tracker`.
4. 설명: `개인용 보석 경매장 가격 기록`. Client URL은 공식 FAQ상 선택 사항.
5. 만든 클라이언트의 **API KEY(JWT)**를 발급/복사해서 본인만 보관.

키를 **여기 채팅이나 GitHub 코드·README·CSV·스크린샷에 넣지 마.** 아래 GitHub의 Secret 저장소에만 넣으면 돼.

## 2) GitHub에서 저장소 만들기

1. [GitHub](https://github.com/)에서 로그인(처음이면 가입).
2. 우측 상단 `+` → **New repository**.
3. Repository name: `lostark-gem-tracker` (다른 이름도 가능).
4. **Private**를 추천(개인 기록 보호). `Add a README file` 선택하지 않고 **Create repository**.

## 3) 이 ZIP 안 파일 올리기

**초보자에게 쉬운 방법: GitHub Desktop**

1. [GitHub Desktop](https://desktop.github.com/)을 설치하고 GitHub로 로그인.
2. GitHub 웹페이지에서 방금 만든 빈 저장소를 열고 **Set up in Desktop** 또는 GitHub Desktop에서 `File → Clone repository`로 저장소를 PC에 복제.
3. 이 ZIP을 압축 해제하고, 안쪽 `lostark_gem_tracker` 폴더에 있는 파일과 폴더를 **모두** 복제된 저장소 폴더로 복사. 폴더 구조를 유지해야 해. `.github/workflows/collect.yml` 경로가 중요함.
4. GitHub Desktop으로 돌아와 `Summary`에 `initial gem tracker`를 적고 **Commit to main** 클릭.
5. **Publish branch** 또는 **Push origin** 클릭해서 업로드.

**웹만으로 할 수도 있어:** 저장소에서 `Add file → Upload files`로 `collect.py`, `README.md`, `tests` 등 올려. `.github/workflows/collect.yml` 파일은 `Add file → Create new file`을 누른 뒤 파일 이름에 `.github/workflows/collect.yml`을 입력하고 ZIP 안의 내용을 붙여넣어 커밋하면 돼. GitHub 웹 UI에서 폴더 업로드가 제한된다면 GitHub Desktop 방식을 써.

## 4) GitHub에 API 키를 비밀값으로 등록

1. 저장소 페이지 상단의 **Settings**.
2. 좌측 메뉴 **Secrets and variables → Actions**.
3. **New repository secret** 클릭.
4. Name: `LOSTARK_API_KEY` (대문자·밑줄 정확하게).
5. Secret: STOVE에서 발급한 API 키(JWT). `Bearer ` 글자를 붙이지 않고 **JWT 자체만** 넣으면 돼.
6. **Add secret** 클릭.

`collect.py` 코드는 해당 Secret을 실행할 때만 읽도록 되어 있어. 저장소 파일에는 API 키가 없어.

## 5) 수동으로 첫 실행하기

1. 저장소의 **Actions** 탭 클릭. 활성화 안내가 뜨면 허용.
2. 왼쪽에서 **Lost Ark Gem Snapshot** 선택.
3. 우측 **Run workflow → Branch: main → Run workflow** 클릭.
4. 실행이 완료되면 초록색 체크 표시가 나와야 정상. 클릭하면 각 단계의 로그를 확인할 수 있어.
5. 저장소 메인 페이지로 돌아가 `data/gems.csv`, `data/latest.md` 파일이 생성됐는지 확인.

테스트는 실제 API 접속 없이 코드 구조를 시험하고, `Collect gem prices` 단계에서 실제 API 요청을 보내.

### CSV는 이런 열이 생겨

| 열 | 의미 |
|---|---|
| `timestamp_kst` | 수집일시(KST) |
| `level`, `kind` | 보석 레벨·겁화/작열 |
| `min_buy_gold` | 등록 매물 중 즉시구매 최저가(골드) |
| `total_listings` | 조회 시점 등록 경매 건수 |
| `top3_median_gold` | 첫 페이지 최저 3개 호가의 중앙값 |
| `sampled_listings` | 첫 페이지에서 유효하게 읽은 즉시구매가 개수 |

**데이터는 처음 실행한 날부터 쌓여.** 로스트아크 공식 API로 2025년 과거 경매장 스냅샷을 소급 수집할 수는 없어.

## 6) 이후 매일 자동 수집

GitHub Actions가 매일 한국시간 21:17에 실행을 시도해. PC가 꺼져 있어도 돼.

- GitHub 스케줄은 시간대나 서비스 부하에 따라 늦게 실행되거나 누락될 수 있어. 금융 거래용 실시간 알람이 아니야.
- `data/gems.csv`에 누적 저장되므로 나중에 ChatGPT에 올려주면 가격 추세와 등록 매물 건수 변화를 분석할 수 있어.
- 11월 15일 이후 코드는 아무 자료도 추가하지 않지만 **워크플로 자체의 주기 실행은 계속됨**. 필요 없으면 Actions 탭에서 워크플로를 수동으로 비활성화해.
- GitHub 정책·요금제에 따라 Actions 사용량 한도가 있을 수 있어. 일반적으로 이 정도의 소규모 일일 수집은 부담이 작지만 무료를 보장하진 않아.

## 7) 에러 해결

| 증상 | 원인/해결 |
|---|---|
| `LOSTARK_API_KEY ... 비어 있어` | Repository Secret 이름이 틀렸거나 미등록. 4단계 다시 확인 |
| `HTTP 401` | STOVE JWT 만료·오기입 여부 확인 및 Secret 갱신 |
| `HTTP 429` | API 호출 한도 초과. 잠시 뒤 재시도. 코드는 일부 자동 재시도를 지원 |
| `HTTP 503` | 점검 또는 일시적인 서버 장애. 추후 수동 재실행 |
| `Items/TotalCount 확인 필요` | 공식 API 응답 구조 변화 가능. 수집 코드를 점검 |
| `다른 이름 포함` | 검색 조건에 예상 외 품목이 포함됨. 잘못된 수량 기록 방지를 위해 중지 |
| `Permission denied` / `403` on git push | Repository **Settings → Actions → General → Workflow permissions**에서 권한·조직 정책 확인. YAML에 `contents: write` 있음 |
| **Actions 목록에 워크플로가 없음** | `.github/workflows/collect.yml` 경로 및 기본 브랜치가 `main`인지 확인 |
| **CSV가 생기지 않음** | Actions 실행 로그의 `Collect gem prices` 단계 확인. 실패 시 빈 가짜 자료를 남기지 않음 |

## 8) 일정 변경

`.github/workflows/collect.yml`에서 `cron` 한 줄을 바꾸면 돼. 현재 `17 12 * * *`은 한국시간 매일 21:17이야 (UTC +9). 예: 1일 2회 수집하려면 `17 3,12 * * *`(12:17, 21:17 KST).

11월 15일 이후에도 계속 수집하고 싶다면 워크플로의 `STOP_AFTER_KST`를 원하는 종료일로 수정해.

---

이 저장소는 공개 페이지의 가격을 긁어오는 방식이 아니라 공식 로스트아크 Open API를 직접 사용하는 방식이야. LOAGAP·천장.com은 추후 별도로 가격을 교차검증할 때 사용할 수 있어. 비공식 현금 거래 가격은 수집하지 않아.
