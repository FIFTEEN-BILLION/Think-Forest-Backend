# 자람마을 API (Think-Forest Backend)

아동용 AI 학습 서비스 **자람마을**의 백엔드. 프론트엔드(`../frontend`)가 브라우저에서
Anthropic SDK 를 직접 부르지 않도록, 모든 Claude 호출을 이 서버가 대신한다.

설계 원칙과 규칙은 [`CLAUDE.md`](./CLAUDE.md)에 있다. 이 문서는 **무엇이 되어 있고
무엇이 남았는지**만 정리한다.

## 빠른 시작

```bash
# 가상환경 (Windows Git Bash 기준)
python -m venv .venv
source .venv/Scripts/activate          # macOS/Linux: source .venv/bin/activate

pip install -r requirements-dev.txt     # 운영만 필요하면 requirements.txt

cp .env.example .env                     # ANTHROPIC_API_KEY 를 채운다 (없어도 실행됨)

fastapi dev app/main.py                  # http://127.0.0.1:8000  ·  문서 /docs

ruff check .                             # 린트
pytest                                   # 24개 통과
```

> **Python 버전** — `CLAUDE.md` 는 3.11+ 를 명시하지만 현재 개발 PC에는 3.10 만
> 설치되어 있어 3.10 으로 venv 를 만들었다. 코드는 `from __future__ import annotations`
> 를 써서 3.10 에서 동작한다. 3.11+ 설치 후 venv 재생성 권장.

## API 계약

`ai: bool` 은 **모든 응답에 들어간다.** 실제 Claude 호출이면 `true`, 규칙 기반
폴백이면 `false`. 실패 사유는 `error` 필드(성공 시 `null`)에 담긴다.

| 엔드포인트 | 입력 | 출력 | 폴백 |
|---|---|---|---|
| `GET /health` | — | `{ok:true}` | — |
| `POST /diagnostic/assess` | `answers[], child` | `{followupIntensity, vocabLevel, rationale, ai, error}` | ✅ 분량·표지어 휴리스틱 |
| `POST /rubric/score` | `question, answer, child, consent?` | `{observe, reason, express, quote, followup, comment, ai, error}` | ✅ 규칙 채점 |
| `POST /theater/script` | `keyword, child` | `{safe, reason, title, scenes[], learn, ai, error}` | ❌ 없음 — 실패를 정직하게 알림 |
| `GET /theater/library` · `/theater/library/{id}` | — | 검수 대본 6종 `{id, keyword, title, scenes[], learn, parentNote}` | — (정적 데이터) |
| `POST /lab/activity` | `topic, child` | `{title, ctrlLabel, ask, concept, quiz[], ai, error}` | ✅ 파라메트릭 템플릿 |
| `POST /report/summary` | `sentences[], weeklyScores[]` | `{summary, next, ai, error}` | ✅ 규칙 요약 |
| `GET /tech/panel` | — | `{aiEnabled, model, callCount, avgLatencyMs, calls[], blocks[]}` | — (진단용) |

- 요청·응답 키는 **camelCase**. 파이썬 필드는 snake_case + alias.
- `child` = `ChildContext` (`name?, ageBand?, grade?, interests[], followupIntensity, vocabLevel`).
  이름·연령대만 받고 생년월일·음성원본·사진은 받지 않는다.
- `POST /rubric/score` 의 `consent.guardian` 가 `true` 일 때만 아이 발화를 마스킹 후 저장한다.

계약을 바꾸면 프론트엔드의 API 레이어(`../frontend/packages/app/src/api/`)도 같이 고친다.

## 생각 친구 대화 엔진 (v0.2, OpenAI)

8~10살 아이가 AI '생각 친구'와 **사람과 이야기하듯** 대화하며 생각을 문장으로 말하고 넓히는 흐름이다.
AI 는 문장만 만들고, 어떤 질문을 던질지·언제 정리하고 끝낼지·성취 기준은 규칙(`app/talks/`)이 정한다.

- **주제를 먼저 던진다** — 요일 테마(월 과학 · 화 수학 · 수 역사 · 목 생각놀이 · 금 상상 실험 · 토 오늘 일기 · 일 내가 고른 주제)
- **흐름** — 주제 → 꼬리질문 → 생활 연결 → '진짜일까?' → 상상 장면 → 꼬리질문 → 생각 지키기/바꾸기(이유 다시 말하기) → 긴 문장으로 정리 → 이야기 플롯 완성본
- **답은 문장으로** — 단답이면 앞으로 나아가지 않고 문장 틀을 주며 다시 말하게 한다. 글자 수 난이도는 없다
- **한 이야기 15분 필수** — 실제 대화 시간 기준(자리를 비운 시간 제외). 원하면 계속 이어 간다
- **점수 없음** — 빈도(이야기 수·대화 시간·활동한 날)와 성취 기준(이유 말하기·새 생각 보태기·상상·생각 다시 보기·정리·끝까지·새 단어 쓰기)만 센다
- **아이 혼자, 보호자는 권한으로** — 보호자가 기기 토큰과 권한(음성·공유 둘러보기·공유 요청)을 준다

| 엔드포인트 | 누가 | 하는 일 |
|---|---|---|
| `POST /families` | — | 가족 생성, 보호자 토큰(개발용) |
| `POST /guardian/children` · `PUT …/{id}/permissions` · `POST/DELETE …/{id}/devices` | 보호자 | 아이 생성, 권한, 태블릿 토큰 발급·회수 |
| `GET /guardian/children/{id}/talks[/{talkId}]` · `…/safety-events` · `…/progress` · `…/words` | 보호자 | 대화 확인, 안전 사건, 성장 기록, 단어 |
| `GET /onboarding` · `POST /onboarding/messages` · `POST /onboarding/confirm` | 아이 | 첫 만남 채팅으로 별명·소속+학년·좋아하는 것·키우고 싶은 것 추출 |
| `GET /talks/today` · `POST /talks` · `POST /talks/{id}/turns` · `POST /talks/{id}/finish` | 아이 | 오늘 테마·주제 제안, 대화 시작(주제/일기/내 카테고리/공유 모험), 대화 한 번, 마치기 |
| `GET/POST/DELETE /children/me/categories` · `POST …/{id}/topics` | 아이 | '!' 버튼 카테고리, 주제 제안 |
| `POST /words/explain` · `/children/me/words` · `/children/me/word-quizzes` | 아이 | 어려운 낱말 풀이(호버), 단어 보관함, 퀴즈 |
| `POST/GET /guardian/children/{id}/word-tests` | 보호자 | 단어 검사 내기·결과 |
| `/children/me/stories` · `/children/me/books` | 아이 | 이야기, 이야기책 묶기 |
| `POST /children/me/shares` · `POST /guardian/shares/{id}/decision` · `POST /guardian/adventures` · `GET /shares` · `POST /shares/{id}/reports` · `/guardian/circles` | 아이·보호자 | 공유 요청 → 보호자 승인 → 가족/모임/전체 공개, 보호자 모험 만들기, 신고 |
| `POST /speech/realtime-sessions` · `POST /speech/transcriptions` | 아이 | 말하는 동안 바로 보이는 실시간 인식 임시 키, 녹음 파일 인식 |
| `POST/GET /guardian/children/{id}/consultations` | 보호자 | 1달 이용 뒤 AI 상담 요약(집계만 사용) |

안전 순서(대화 한 번): 민감 주제(선정·정치·폭력·외모·개인정보 요청·자해 신호) → 문장 확인 → 개인정보 가림 →
Moderation(허용 시) → 흐름 결정 → AI 문장 생성 → 출력 검사(민감 주제·낱말 존재·그림 키) → 실패 시 규칙 대사.
자해 신호는 원문 없이 `safety_events` 로 남겨 보호자가 확인한다.

**아동 데이터 원칙** — OpenAI 18세 미만 지침상 13세 미만 개인정보는 ZDR 승인 뒤에만 처리한다.
`CHILD_DATA_MODE=demo`(기본)에서는 실제 아이의 문장·음성을 OpenAI(대화·음성·Moderation 모두)로 보내지 않고
규칙 기반으로 동작한다. 성인 테스터 계정(`tester: true`)만 실제 AI 를 쓴다.
학교 이름·주소·실명은 저장하지 않는다(소속 종류와 학년만). 공유는 개인정보를 가린 스냅숏만 게시한다.

**미검증** — 실제 OpenAI 호출(키 없음), Realtime 전사 세션 안의 한국어 지정 필드 이름, 운영 DB(PostgreSQL) 이전.

## 안전 파이프라인 (순서 고정)

1. **1차 금칙어 필터** — `safety/blocklist.py`. 정규화(공백·구두점·반복문자 제거) 후
   포함 검사, **Claude 호출 전**. `theater`·`lab` 의 키워드/주제에 적용.
   걸리면 토큰 비용 0, 차단 로그 기록. "칼 로", "죽.여", "죽이이이" 같은 우회도 차단.
2. **2차 생성 단계 AI 심사** — 마음극장 대본 프롬프트가 `safe:false` 를 반환하면
   `title/scenes/learn` 을 폐기하고 사유만 반환. 차단 로그 기록.
3. **3차 부모 먼저보기** — 프론트엔드 담당.
4. **4차 차단 로그** — `GET /tech/panel` 의 `blocks[]` 로 반환.

개인식별정보는 `safety/pii.py` 가 저장 직전 마스킹한다(전화번호·학교/기관명·집주소·
생년월일·이름). 가린 항목의 *종류*만 기록한다.

## 폴더 구조

```
app/
├── main.py              앱 생성 · CORS · 라우터 등록 · GET /health
├── config.py            환경변수 로딩 (get_settings)
├── routers/             diagnostic.py · rubric.py · theater.py · lab.py · report.py · tech.py
├── prompts/             shared.py + 엔드포인트별 SYSTEM/build_user (라우터에 문자열 두지 않음)
├── data/
│   └── theater_library.py  검수 대본 6종 (TH-09, 정적)
├── services/
│   ├── claude.py        Anthropic 호출 유일 통로 · call_json(동기) · generate_script(AsyncAnthropic) · ClaudeError
│   ├── diagnostics.py   호출 로그 · 차단 로그 (인메모리 링버퍼)
│   └── storage.py       아이 발화 저장 (동의 게이트 · 마스킹 · 90일 파기) — 인메모리
├── safety/
│   ├── blocklist.py     1차 금칙어 (정규화 + 포함 검사)
│   └── pii.py           개인식별정보 마스킹
└── schemas/             common.py(CamelModel/ChildContext/AiMeta) + 엔드포인트별 모델
tests/                   test_safety.py · test_api.py · test_diagnostic.py
pyproject.toml           ruff · pytest 설정
.github/workflows/ci.yml  ruff + pytest 자동화
```

---

## ✅ 실시한 작업

### 스캐폴딩
- FastAPI 앱, CORS(`CORS_ORIGINS` 환경변수, `*` 금지), 라우터 등록, `GET /health`
- `config.py` — `.env` 로딩, `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL`/`CORS_ORIGINS`/`DATA_RETENTION_DAYS`, `ai_enabled` 플래그
- `.env.example`, `.gitignore`(`.env` 커밋 차단), `requirements.txt` / `requirements-dev.txt`
- `pyproject.toml`(ruff·pytest), `.github/workflows/ci.yml`(ruff + pytest)
- 3.10 venv 구성 및 의존성 설치

### 엔드포인트 6종 + 정적 2종 + 진단 패널 (계약대로, `ai`/`error` 포함)
- `POST /diagnostic/assess` — 첫 만남 진단(DG-01~02). Claude 평가 + **규칙 기반 폴백**(분량·표지어·건너뛴 수 → `followupIntensity`/`vocabLevel`/근거 문장)
- `POST /rubric/score` — Claude 채점 + **규칙 기반 폴백**(분량·표지어 휴리스틱), 동의 시 발화 저장
- `POST /theater/script` — `ScriptRequest`/`ScriptResponse`, async 라우터. ① `find_blocked()` 1차 금칙어(Claude 호출보다 먼저) → ② `generate_script()`(AsyncAnthropic) 로 2차 AI 심사 겸 대본 생성. **폴백 없음** — 실패 시 예외를 삼키지 않고 로깅한 뒤 `ai=False` + 안내 문구 반환, 템플릿 대본 없음
- `GET /theater/library` · `/theater/library/{id}` — 검수 대본 6종(노인공경·공손함·정직·배려·용기·약속), 5장면 + 부모용 해설. AI 불필요
- `POST /lab/activity` — 1차 금칙어 → Claude 활동 생성 + **파라메트릭 폴백 템플릿**
- `POST /report/summary` — Claude 요약 + **규칙 기반 폴백**(추이 델타)
- `GET /tech/panel` — AI 연결 상태·호출 수·평균 지연·호출 로그·차단 로그

### Claude 래퍼 (`services/claude.py`)
- 라우터가 SDK 를 직접 부르지 않는 단일 통로
- `call_json()` — 동기(`Anthropic`), rubric·lab·report 용. 실패 시 `ClaudeError(code, message)` 로 올림
- `generate_script()` — 비동기(`AsyncAnthropic`), 마음극장 전용. 실패를 삼키지 않고 로깅 후 `ScriptResponse(ai=False, error=code, reason=안내문구)` 반환
- 목적·모델·지연시간(ms)·성공 여부 로깅 → `/tech/panel` 노출
- 코드펜스·잡담을 견디는 JSON 추출
- 키 없으면 `no_api_key` 로 즉시 반환 (SDK 는 함수 안에서 지연 import → 키 없어도 앱 기동)

### 안전
- `safety/blocklist.py` — 6개 카테고리 금칙어(40여 개), Claude 호출 전. NFKC 정규화 + 공백·구두점·반복문자 제거로 우회 대응. `find_blocked() -> str | None`, `check() -> BlockResult`
- `safety/pii.py` — 전화번호·학교/기관명·집주소(시→구→동 체인, 아파트 동/호)·생년월일·이름 정규식 마스킹, 종류만 기록. 학년·나이·개수 같은 짧은 숫자는 오탐하지 않음
- `services/storage.py` — 보호자 동의 없으면 저장 안 함(체험은 허용), 마스킹 후 저장, 90일 만료 자동 파기
- 차단 건을 `diagnostics` 에 기록해 `/tech/panel` 로 반환

### 프롬프트 (`app/prompts/`)
- 라우터에서 분리. 엔드포인트별 `SYSTEM` + `build_user(...)`
- 공통 규칙 주입: 사실성(교육과정 코드·연도·수치 금지), 아동 안전, JSON-only
- 마음극장: 품질 기준 8개 동봉, 안전 심사 겸용

### 테스트 · 린트
- `tests/test_safety.py` — 금칙어 차단/통과, 우회(공백·구두점·반복문자) 차단, PII 마스킹(전화·이름·학교·주소·생년월일), 오탐 없음 확인
- `tests/test_api.py` — 응답 스키마 계약, 마음극장 폴백 부재(정직한 실패), 1차 금칙어가 Claude 호출보다 먼저, 실패도 로깅됨, 검수 라이브러리, 기술 패널 로그
- `tests/test_diagnostic.py` — 건너뜀 시 보수적 설정, 풍부한 답변 시 강도 상향, 스키마 키
- `ruff check .` 통과 · `pytest` → **24 passed**

---

## ⬜ 남은 작업

### 즉시 필요
- [ ] **프론트엔드 API 레이어 연결** — `../frontend/packages/app/src/api/` 에 도메인 함수 + 훅 추가. `CLAUDE.md` 가 언급하는 `src/lib/api.ts` 는 아직 없음(프론트는 스켈레톤 상태)
- [ ] **실제 Claude 호출 검증** — `ANTHROPIC_API_KEY` 를 넣고 4개 엔드포인트를 `/docs` 에서 호출, 응답 스키마·모델 ID(`claude-sonnet-5`) 확인
- [ ] `ANTHROPIC_MODEL` 실측 후 기본값 확정
- [ ] Python 3.11+ 설치 후 venv 재생성, `CLAUDE.md` 와 실제 버전 일치

### 영속화 (현재 전부 인메모리)
- [ ] 발화 저장소를 **PostgreSQL** 로 (`services/storage.py` 인터페이스는 고정됨).
  후보: SQLAlchemy 2.x + Alembic, 비동기 드라이버(`asyncpg`). `DATABASE_URL` 환경변수 추가.
  마스킹된 텍스트·가린 항목 종류·저장 시각만 컬럼으로. 원문·이름·연락처는 저장 금지.
- [ ] 보관기간(30/90/180일) 아이별 설정 저장, 만료 파기 배치(또는 `DELETE ... WHERE at < now() - interval`)
- [ ] 호출/차단 로그 영속화 또는 관측 도구 연동

### 안전 강화
- [x] 금칙어 우회 표현(띄어쓰기·구두점·반복문자) 대응 — 정규화 후 매칭
- [ ] 자모 분리("ㅈㅜㄱ여")·초성 우회 대응, 금칙어 목록 지속 확장
- [ ] PII — 자모 조합 이름, "○○시 ○○구 ○○동" 4단계 전체 주소, 영문 주소 대응
- [ ] `rubric.answer` 저장 외에 로그 경로에도 마스킹 적용 여부 감사

### 미착수 화면 대응
- [ ] `POST /report/summary` 에 "자람 플러스" 가치 문구 — 카피 확정 후 프롬프트 반영
- [x] 첫 만남 진단(DG-01~02) → `POST /diagnostic/assess`
- [x] 검수 대본 라이브러리(TH-09) 6종 → `GET /theater/library` (정적 데이터)
- [ ] 검수 대본 콘텐츠 자체를 교육팀이 검수·확정 (현재는 초안)
- [ ] 결제(PY-02), 스플래시(SP-01) — 구조도상 미구현, 백엔드 관여 범위 확정 필요

### 운영
- [x] CI — `pytest` + `ruff` 자동화 (`.github/workflows/ci.yml`)
- [ ] 요청 레이트리밋·토큰 예산 상한 (배포 맥락 확정 후)
- [ ] 배포 설정(호스팅, HTTPS, 환경변수 주입)
- [ ] `CORS_ORIGINS` 에 배포 도메인 추가
