# SAA — 주식 트렌드 및 예상 주가 분석 멀티 에이전트

「주식 트렌드 및 예상 주가 분석 멀티 에이전트 팀 구축 계획서 v2」의 구현입니다.
iPad a-Shell에서 단축어 자동화로 평일 08:30 / 15:40에 실행되고, 결과를 Gmail로 받습니다. 운영 비용은 0원(무료 API 티어)입니다.

## 계획서 ↔ 코드 대응

| 계획서 | 구현 |
|---|---|
| Agent 1 Orchestrator (스케줄·취합·렌더링·에러 알림) | `stock_agent/agents/orchestrator.py` |
| Agent 2 News & Disclosure (뉴스 정제, DART 공시, 감성 스코어, 기업 이미지 긍정/중립/부정) | `stock_agent/agents/news.py` |
| Agent 3 Quant & Forecast (정배열·거래량·RSI·수급·지지/저항·손절, Gemini 시나리오) | `stock_agent/agents/quant.py`, `stock_agent/indicators.py` |
| 종목 선정: 코스피 시총 TOP 10 + 업종별 코스닥 연관주 2개 (30종목) | `stock_agent/funnel.py` |
| Stage 5 Theme Cluster Payload, 관찰형 프롬프트 | `quant.py` `build_payloads` / `SYSTEM_EVENING` |
| Stage 6 전일 시나리오 적중 태깅 | `stock_agent/validation.py` → `data/accuracy_log.csv` |
| 하트비트 메일 | `heartbeat.py` |
| 단축어 배치 스크립트 | `run_stock.sh` |
| 모델명 설정 파일 분리 | `config.json` → `llm.models` |
| 축소 리포트 (degraded mode) | LLM 실패 시 정량 데이터만 발송 (`quant.py` `forecast`) |
| 컴플라이언스 문구 | `stock_agent/compliance.py` (지시형 문구 자동 치환 + 면책 문구) |
| 3회 재시도 / 에러 로그 발송 | `stock_agent/http.py`, `orchestrator.py` `_alert_admin` |

## 계획서와 달라진 점 (2026-09 기준 확인)

1. **KRX 데이터: 네이버 증권이 기본.** KRX 정보데이터시스템이 2025-12-27부터 로그인 필수로 바뀌어, pykrx 1.2.9는 KRX 회원 계정(`KRX_ID`/`KRX_PW`)이 있어야 동작합니다. 그래서 KRX 계정이 있으면 pykrx를 쓰고, 없거나 실패하면 네이버 증권 데이터로 자동 대체합니다. pykrx 경로는 계정이 없어 실제로 테스트하지 못했습니다.
2. **Gemini 모델: 순서대로 대체.** Google이 2.5 모델 접근을 "과거 사용 이력이 있는 사용자"로 제한했습니다. 그래서 `config.json`의 `llm.models`를 `gemini-2.5-flash → gemini-3.8-flash → gemini-3.5-flash-lite` 순서로 시도합니다. 실제로 쓸 수 있는 모델은 `python3 check_setup.py`로 확인하세요.
3. **Gemini SDK 대신 REST 직접 호출.** a-Shell에는 컴파일된 확장이 필요한 `google-genai`(pydantic, grpc)를 설치할 수 없어서 `requests`로 호출합니다.
4. **투자주의환기종목은 별도로 제외하지 않습니다.** KIND 관리종목과 거래정지 종목만 제외합니다. 환기종목은 코스닥 소형주라 대부분 연관주 조건(시총 1,000억 이상, 거래대금 1억 이상)에서 걸러집니다. 추가로 뺄 종목은 `config.json`의 `funnel.exclude_codes`에 넣으세요.
5. **테마 이름은 네이버 업종명입니다**(예: "반도체와반도체장비"). 업종 조회에 실패하면 KIND 상장법인 업종표로 대체합니다.
6. **연관 코스닥 종목 = 같은 네이버 업종의 코스닥 보통주 중 시총 상위**이고, 종목은 중복되지 않습니다. 은행·생명보험·복합기업·자동차처럼 코스닥 종목이 부족한 업종은 `config.json`의 `funnel.related_industries`에 적은 인접 업종(증권·기타금융·건설·자동차부품 등)을 순서대로 찾으며, 리포트에 "인접 업종"으로 표시됩니다.
7. **기업 이미지는 최근 14일 기사 제목(종목당 최대 10건)을 Gemini 1회 호출로 분류**한 결과입니다. 기사가 3건 미만이면 중립으로 두고, AI 호출이 실패하면 키워드 사전으로 대체합니다.

## 폴더 구조

```
main_pipeline.py      메인 엔진 (--mode morning|evening, --dry-run, --force, --no-llm)
heartbeat.py          실행 시작 하트비트 메일
check_setup.py        설정·키·모델·메일 점검
run_stock.sh          단축어 → a-Shell 배치
config.json           공개 설정 (모델명, 필터 임계값, 휴장일)
secrets.example.json  → secrets.json 으로 복사해 키 입력 (git 제외)
stock_agent/
  agents/             orchestrator · news · quant
  sources/            naver · krx(pykrx) · kind · dart
  funnel.py indicators.py validation.py report.py llm.py mailer.py compliance.py ...
tests/                python3 -m unittest discover -s tests -t .
data/                 실행 산출물 (git 제외): outbox/ runs/ scenarios/ cache/ accuracy_log.csv
```

## 1. 키 발급

| 항목 | 발급처 | 비고 |
|---|---|---|
| `GEMINI_API_KEY` | aistudio.google.com → Get API key | 무료 티어 |
| `DART_API_KEY` | opendart.fss.or.kr → 인증키 신청 | 없으면 공시 섹션만 생략 |
| `GMAIL_APP_PASSWORD` | Google 계정 → 보안 → 2단계 인증 → 앱 비밀번호 | 일반 비밀번호로는 SMTP 로그인 불가 |
| `KRX_ID` / `KRX_PW` | data.krx.co.kr 회원가입 | 선택 |

`secrets.example.json`을 `secrets.json`으로 복사한 뒤 값을 채웁니다. 환경변수로 넣어도 되고, 환경변수가 파일보다 우선합니다.

## 2. Mac 개발 환경

```bash
brew install python@3.13
cd ~/SAA
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 check_setup.py
python3 -m unittest discover -s tests -t .
python3 main_pipeline.py --mode evening --dry-run --force   # 메일 없이 data/outbox/*.html 생성
```

## 3. iPad (a-Shell) 설치

1. App Store에서 a-Shell을 설치합니다.
2. 이 폴더를 a-Shell의 `~/Documents/stock_agent/`로 복사합니다. 파일 앱의 "a-Shell" 위치에 넣거나, GitHub에 올렸다면 a-Shell에서 `lg2 clone <repo-url> ~/Documents/stock_agent`로 받습니다.
3. a-Shell에서 다음을 실행합니다.
   ```sh
   cd ~/Documents/stock_agent
   pip install requests beautifulsoup4      # pandas·numpy는 a-Shell 내장
   python3 check_setup.py --send-test       # 테스트 메일 수신 확인
   python3 main_pipeline.py --mode evening --force   # 첫 실제 발송
   ```
4. a-Shell에서 스레드 관련 오류가 나면 `config.json`의 `data.max_workers`를 `1`로 바꿉니다(순차 실행).

## 4. 단축어 자동화

단축어 앱 → 자동화 → 새로운 자동화 → **특정 시간**:

| 시각 | 반복 | 동작 (a-Shell "Execute Command") |
|---|---|---|
| 08:30 | 평일 | `sh ~/Documents/stock_agent/run_stock.sh morning` |
| 15:40 | 평일 | `sh ~/Documents/stock_agent/run_stock.sh evening` |

- **"즉시 실행"을 선택하고 "실행 시 알림"을 끕니다.** 완전 무인 구동을 위한 설정입니다.
- 앱 확장(extension) 모드에서 메모리 한도로 멈추면, a-Shell 앱을 열어서 실행하는 옵션을 켭니다.
- 휴장일에는 짧은 "휴장일 안내" 메일만 발송합니다.
  - 모닝 브리프는 `config.json`의 `market_holidays`로 휴장일을 판단합니다. **매년 갱신이 필요합니다.**
  - 결산 리포트는 실제 마지막 거래일 데이터로 판단합니다.

### 무인 운영 점검 규칙

- **하트비트 메일 없음**: 단축어 자동화가 실행되지 않은 것입니다. 저전력 모드, iPad 잠김, Wi-Fi 상태를 점검하세요.
- **하트비트만 오고 결과 메일 없음**: 파이프라인이 멈춘 것입니다. a-Shell에서 `tail -50 ~/Documents/stock_agent/pipeline.log`로 로그를 확인하세요.
- **"❗ 파이프라인 실패" 메일**: 첨부된 pipeline.log를 확인하세요.
- **제목에 "(축소)" 표시**: Gemini 호출이 실패해 정량 데이터만 발송한 것입니다. 본문 상단에 사유가 표시됩니다.

## 4-2. GitHub Actions 자동 실행

`.github/workflows/saa-report.yml` 이 평일 08:30 모닝 브리프와 15:30 결산(KST)을 자동 실행합니다. Claude Code 클라우드 루틴은 Gmail SMTP 접속이 막혀 메일을 보낼 수 없어서 쓰지 않습니다.

1. 저장소 Settings > Secrets and variables > Actions > Repository secrets 에 `MY_SECRET_KEY` 하나를 만들고, 값에 `secrets.json` 파일 내용을 통째로 붙여넣습니다. 워크플로가 실행할 때 이 값으로 `secrets.json` 을 만들어 씁니다.
2. Actions 탭 > SAA 리포트 > Run workflow 로 수동 실행합니다. 모드와 dry-run(메일 미발송, 리포트 HTML만 보관)을 고를 수 있습니다.

- 전일 시나리오 검증 기록(`data/`)은 Actions 캐시로 이어받습니다. 7일 넘게 실행이 없으면 초기화됩니다.
- GitHub 예약 실행은 몇 분에서 수십 분 늦게 시작될 수 있고, 저장소에 60일간 활동이 없으면 예약이 꺼집니다.

## 4-3. Google Drive 저장 (선택)

`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` 이 모두 있으면 실행 끝에 내 드라이브의 `SAA` 폴더(앱이 직접 생성)에 올립니다. 하나라도 비어 있으면 건너뜁니다.

- 올리는 파일: 리포트 HTML(`날짜_evening.html`, `날짜_morning.html`), 결과·뉴스 JSON(`날짜_evening.json`), 시나리오(`날짜_scenarios.json`), `accuracy_log.csv`, 상태 압축본(`saa_state.zip`).
- `saa_state.zip`(runs, scenarios, accuracy_log.csv)은 다음 실행 시작 때 내려받아 복원합니다. Actions 러너가 매번 새로 시작해도 적중 기록이 이어집니다.
- 처음 1회 `python3 drive_auth.py` 로 refresh token 을 받아 `secrets.json` 과 `MY_SECRET_KEY` 에 넣습니다. OAuth 동의 화면은 게시 상태를 "프로덕션"으로 해야 토큰이 7일 만에 만료되지 않습니다.
- 드라이브 복원에 실패하면 그 실행은 저장도 건너뛰어 기존 기록을 덮어쓰지 않으며, 저장 실패는 리포트 발송을 막지 않습니다.

## 5. 리포트 구성

- **08:30 모닝 브리프**
  - 전일 미국 증시(다우, S&P 500, 나스닥, 필라델피아 반도체)
  - 원/달러 환율, 전일 코스피·코스닥
  - AI 관찰 포인트
  - 개장 전 특징 공시(DART)
  - 갭 상승 관찰 후보: 호재 공시와 전일 결산의 강세·수급 종목
- **15:40 결산**
  - 시장 개요
  - 코스피 시총 1~10위 순서의 클러스터 10개: 대장주 1개 + 연관 코스닥 2개. 종목명 옆에 시총(시장 내 순위 포함)과 기업 이미지(긍정/중립/부정)를 보여 주고, 지표, 수급, 지지/저항, 손절 참고가도 함께 보여 줍니다.
  - AI 대장주 평가와 관찰 포인트, 지목 종목의 예상 밴드와 핵심 가격
  - 52주 신고가 근접·눌림목 Top 5
  - 전일 시나리오 적중 검증과 누적 정확도

## 6. 데이터 소스와 한계

- 네이버 증권 API는 **비공식**이라 예고 없이 바뀔 수 있습니다. 실패하면 3회 재시도 후 해당 섹션을 생략하거나 에러 알림을 보냅니다.
- 15:40 시점의 외국인·기관 순매수는 잠정치일 수 있습니다. 당일 수치가 아직 없으면 리포트 하단에 "수급 기준일"을 따로 표시합니다.
- 네이버 경로의 순매수 금액은 "순매수 수량 × 종가"로 계산한 근사값입니다.
- 예상 밴드와 시나리오는 참고용 관찰 결과이며 투자 권유가 아닙니다. 적중률은 `data/accuracy_log.csv`에 쌓이니 1주 이상 누적한 뒤 판단하세요.
