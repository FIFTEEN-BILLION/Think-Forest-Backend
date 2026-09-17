# 자람마을 API (Think-Forest Backend)

초등 2~4학년(8~10살) 아이를 위한 AI 학습 서비스 **자람마을 · 생각숲**의 백엔드입니다.
브라우저에 API 키를 노출하지 않도록 모든 AI 호출을 이 서버가 대신합니다.

- **생각 친구 대화 엔진 · 그림자 첫 탐구** — OpenAI (`gpt-5.6-luna` 기본)
- **초기 체험 기능**(진단·채점·실험실·마음극장·리포트) — Anthropic Claude

- 🔗 **백엔드 API (Vercel)**: <https://think-forest-backend.vercel.app> (Swagger: `/docs`)
- 🌐 **웹 프론트엔드 (Netlify)**: <https://think-kids.netlify.app>

설계 원칙과 코딩 규칙은 [`CLAUDE.md`](./CLAUDE.md)에 있습니다.

---

## 지금까지 진행한 내용 (2026-09-17 기준)

| 영역 | 내용 | 상태 |
|---|---|---|
| 생각 친구 대화 | 요일 테마 주제 → 꼬리질문 → 생활 연결 → "진짜일까?" → 상상 → 생각 지키기/바꾸기 → 긴 문장 정리 → 이야기 완성 | ✅ 구현·테스트 |
| 문장으로 답하기 | 단답이면 다음으로 넘어가지 않고 문장 틀 제공. 글자 수 난이도 없음 | ✅ |
| 한 이야기 15분 | 실제 대화 시간 기준(자리 비운 시간 제외), 원하면 계속 이어 가기 | ✅ |
| 첫 만남 대화 | 채팅으로 별명·소속+학년·좋아하는 것·키우고 싶은 것 추출, 학교 이름 미저장 | ✅ |
| 내 카테고리 | `!` 버튼 카테고리와 주제 제안 | ✅ |
| 가족·권한 | 보호자 토큰, 아이 태블릿 토큰, 권한(음성·공유 둘러보기·공유 요청) | ✅ (개발용 인증) |
| 안전 | 선정·정치·폭력·외모·개인정보 요청·자해 신호 차단, 자해 신호 보호자 알림, ZDR 전 아동 데이터 AI 전송 차단 | ✅ |
| 단어 교육 | 어려운 낱말 풀이(호버용), 단어 보관함, 퀴즈, 보호자 단어 검사 | ✅ |
| 이야기·책 | 대화·일기로 만든 이야기 플롯, 이야기책 묶기 | ✅ |
| 공유 | 공유 요청 → 보호자 승인 → 가족/친구 모임/전체 공개, 보호자 모험, 신고 자동 숨김 | ✅ |
| 성장 기록 | 점수 없이 빈도·성취 기준 7가지, 1달 뒤 보호자 AI 상담 | ✅ |
| 음성 입력 | 녹음 파일 인식, 실시간 인식 임시 키 | ✅ (실제 호출 미검증) |
| 음성 출력(TTS) | `POST /voice/synthesize` — 문장을 Typecast 로 읽어 줌(voice_id 고정) | ✅ (실제 호출 로컬 검증 완료, 프론트 미연동) |
| 그림자 첫 탐구 | 헷갈리는 생각 친구를 공정한 실험 증거로 설득 (실제 OpenAI 연동) | ✅ 실시간 AI 연동·배포 완료 |
| 배포 & 인프라 | Vercel 서버리스 배포, Supabase Postgres 연동, Netlify 도메인 CORS 허용 | ✅ 배포 완료 |
| API 문서 | `/docs` 한국어 설명·순서·예시 본문 | ✅ |

**실제 OpenAI 호출 및 배포 연동 검증 완료(2026-09-17)** — 성인 테스터 계정으로 `POST /talks` → `POST /talks/{id}/turns` 대화 완주 검증 및 Netlify 웹 프론트엔드(`https://think-kids.netlify.app`)와 Vercel 백엔드(`https://think-forest-backend.vercel.app`)를 연동하여 첫 탐구 v2(그림자 생각친구 설득) 전 과정을 실제 OpenAI 호출로 완주 확인.

**아직 검증하지 못한 것** — 음성 입력(STT) 실제 호출(`POST /speech/transcriptions`·`/speech/realtime-sessions`), 실시간 음성 인식 세션의 한국어 지정 필드, 태블릿 실기기.

**이번 검증 중 발견한 이슈** — [#10 외모 필터가 "잘 생기다/못 생기다"(동사 "생기다" 일반 활용)를 오탐](https://github.com/FIFTEEN-BILLION/Think-Forest-Backend/issues/10), [#11 실제 `.env` 키가 있으면 `no_api_key` 부정 경로 테스트가 깨짐](https://github.com/FIFTEEN-BILLION/Think-Forest-Backend/issues/11).

---

## 빠른 시작 (macOS)

```bash
cd Think-Forest-Backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # 키가 없어도 실행됩니다(규칙 기반 대사)

uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
```

- API 문서: <http://127.0.0.1:8010/docs> — 8000번 포트가 비어 있으면 `--port 8000` 도 됩니다.
- 로컬 DB: 처음 실행하면 `./data/thinkforest.db`(SQLite)가 생깁니다. Git 에는 올라가지 않습니다.
- 검사: `ruff check . && pytest`

> Python 3.10 이상에서 동작합니다(CI 는 3.11).

---

## 사용 방법

### 1. `/docs` 에서 5분 만에 해 보기

Swagger 에는 전체 인증 버튼이 없습니다. 요청마다 있는 **`authorization`** 칸에 `Bearer 토큰` 을 넣습니다.

1. `POST /families` → `guardianToken`(`gt_…`) 복사 — **보호자 토큰**
2. `POST /guardian/children` (`Bearer gt_…`) → body `{"nickname": "하늘", "tester": false}` → `id` 복사
3. `POST /guardian/children/{child_id}/devices` → `childToken`(`ct_…`) 복사 — **아이 태블릿 토큰**
4. `GET /onboarding` → `POST /onboarding/messages` `{"text": "나는 초등학교 3학년이야"}` (이제부터 `Bearer ct_…`)
5. `GET /talks/today` → `POST /talks` `{"topicId": "snow"}` → `id` 복사
6. `POST /talks/{talk_id}/turns` `{"text": "눈으로 눈사람을 만들고 눈싸움도 할 수 있어"}` 를 반복
7. 응답의 `move` 가 `compose` 가 되면 세 문장 이상으로 정리 → `story` 완성
8. `POST /talks/{talk_id}/finish` — 대화 시간 15분이 필요합니다. 빨리 보려면 `.env` 에 `TALK_MIN_SECONDS=60`

### 2. 대화 응답 읽는 법

| 필드 | 뜻 |
|---|---|
| `accepted` | 문장으로 인정되어 대화가 앞으로 나아갔는지. 단답이면 `false` + `sentenceStarters`(문장 틀) |
| `move` | 친구가 방금 던진 질문 종류: `tail` 꼬리질문 · `connect` 생활 연결 · `challenge` 진짜일까? · `imagine` 상상 · `reason_check` 생각 지키기/바꾸기 · `compose` 정리 · `continue` 이어 가기 |
| `friendTurn.question` | 지금 답해야 할 질문(화면에 계속 보여 줄 것) |
| `friendTurn.words` | 어려운 낱말과 뜻(호버·탭 설명) |
| `friendTurn.visual` | 그림 키(프론트가 삽화로 바꿈) |
| `safety` | 민감한 말이면 종류. 원문은 저장하지 않음 |
| `time.canFinish` | 마칠 수 있는지 |
| `ai` / `error` | 실제 AI 문장인지, 못 썼다면 이유(`no_api_key`, `child_data_mode_off` 등) |

### 3. 실제 AI 켜기

1. `.env` 에 `OPENAI_API_KEY` 를 넣습니다(모델 기본값 `gpt-5.6-luna`).
2. 서버를 재시작합니다.
3. `CHILD_DATA_MODE=demo`(기본)에서는 **실제 아이 계정의 문장·음성을 OpenAI 로 보내지 않습니다.**
   시험은 성인 테스터 계정(`POST /guardian/children` 에 `"tester": true`)으로 합니다.
4. 음성 입력은 `PUT /guardian/children/{child_id}/permissions` 로 `voice` 권한을 줘야 합니다.
5. OpenAI Zero Data Retention 승인을 받은 뒤에만 `CHILD_DATA_MODE=child` 로 바꿉니다.

### 4. 프론트와 함께 실행

```bash
# 프론트 저장소에서
VITE_API_BASE_URL=http://127.0.0.1:8010 npm run dev    # http://127.0.0.1:5173
```
백엔드 `.env` 의 `CORS_ORIGINS` 에 프론트 주소(`http://127.0.0.1:5173,http://localhost:5173`)를 넣습니다.

### 5. 같은 와이파이의 다른 기기에서 보기

```bash
ipconfig getifaddr en0        # 예: 172.30.1.66
CORS_ORIGINS="http://172.30.1.66:5173" uvicorn app.main:app --host 0.0.0.0 --port 8010
# 프론트: VITE_API_BASE_URL=http://172.30.1.66:8010 npm run dev
```
다른 기기에서 `http://172.30.1.66:5173`(앱), `http://172.30.1.66:8010/docs`(문서)로 접속합니다.
같은 와이파이의 누구나 접속할 수 있으니 공용 와이파이에서는 켜 두지 마세요.

---

## 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `OPENAI_API_KEY` | (없음) | 없으면 모든 대화가 규칙 기반 대사 |
| `OPENAI_MODEL` | `gpt-5.6-luna` | 대화·플롯·주제 제안 모델 |
| `OPENAI_REASONING_EFFORT` | (없음) | 지연이 길면 조정 |
| `OPENAI_TRANSCRIBE_MODEL` / `OPENAI_REALTIME_MODEL` | `gpt-transcribe` / `gpt-live-transcribe` | 음성 인식 |
| `OPENAI_MODERATION_MODEL` | `omni-moderation-latest` | 콘텐츠 검사 |
| `CHILD_DATA_MODE` | `demo` | `child` 는 ZDR 승인 뒤에만 |
| `AI_ENABLED` / `SPEECH_ENABLED` | `true` | 즉시 차단 스위치 |
| `DAILY_AI_CALL_LIMIT` | `300` | 아이별 하루 AI 호출 한도 |
| `TALK_MIN_SECONDS` | `900` | 한 이야기 필수 대화 시간(초) |
| `DATABASE_URL` | `sqlite:///./data/thinkforest.db` | 운영은 Supabase Postgres 주소(`postgresql+psycopg://...`). 서버리스에서는 Supabase **Connection Pooler**(6543 포트, `?pgbouncer=true`) 사용 권장 |
| `CORS_ORIGINS` | `http://localhost:5173` | 쉼표 구분, `*` 금지 |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | (없음) / `claude-sonnet-5` | 초기 체험 기능 |
| `TYPECAST_API_KEY` | (없음) | 없으면 음성 합성 `no_api_key` 실패 |
| `TYPECAST_VOICE_ID` | (없음) | 고정 보이스(예: 이현). [Typecast 콘솔](https://typecast.ai) 또는 `GET /v2/voices` 로 확인 |
| `TYPECAST_MODEL` | `ssfm-v30` | TTS 모델 |

---

## 배포 (Vercel + Supabase)

서버리스 함수(`api/index.py` → `app.main:app`)로 Vercel 에 올린다. Vercel 의 파일시스템은 쓰기 불가·요청 간 유지도 안 되므로 SQLite 가 아니라 Supabase Postgres 를 쓴다.

1. [Supabase](https://supabase.com) 에서 프로젝트를 만들고 `Project Settings → Database → Connection string` 에서 **Connection Pooler**(Transaction 모드, 6543 포트) 주소를 받는다.
2. Vercel 에 이 저장소를 연결하고(Root Directory 는 그대로 `backend`), 아래 환경변수를 Production/Preview 각각 등록한다: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `TYPECAST_API_KEY`, `TYPECAST_VOICE_ID`, `DATABASE_URL`(1번의 pooler 주소, `postgresql+psycopg://` 스킴), `CORS_ORIGINS`(배포된 프론트 도메인, `*` 금지).
3. `vercel --prod` 로 배포하거나 GitHub 연동 시 `main` 푸시로 자동 배포한다.
4. `https://<프로젝트>.vercel.app/health`, `/docs` 로 확인한다.

로컬에서 Supabase 를 테스트하려면 `DATABASE_URL` 을 같은 pooler 주소로 바꿔서 실행하면 된다(SQLite 와 동일하게 `init_db()` 가 테이블을 만든다). 마이그레이션 도구(Alembic)는 아직 없다.

---

## 브랜치 전략 (Git Flow)

| 브랜치 | 용도 | 만드는 곳 → 합치는 곳 |
|---|---|---|
| `main` | 배포·제출 기준 | — |
| `develop` | 다음 배포를 모으는 통합 브랜치 | — |
| `feature/<기능>` | 새 기능 | `develop` → `develop` |
| `fix/<내용>` | 버그 수정 | `develop` → `develop` |
| `docs/<내용>` · `chore/<내용>` · `refactor/<내용>` | 문서 · 설정/의존성 · 구조 개선 | `develop` → `develop` |
| `release/<버전>` | 배포 준비(버전·문서 정리) | `develop` → `main` + `develop` |
| `hotfix/<내용>` | 배포 후 긴급 수정 | `main` → `main` + `develop` |

- `main`·`develop` 에 직접 커밋하지 않습니다. 모든 변경은 PR 로 합칩니다.
- 브랜치 이름은 영어 소문자와 `-` 를 씁니다. 예: `feature/talk-finish`, `fix/onboarding-grade-parse`
- PR 전에 `ruff check . && pytest` 를 통과시킵니다. CI 가 같은 검사를 돌립니다.
- 앞 기능에 기대는 기능은 **이어 쌓은 PR**(앞 브랜치를 base 로)로 올리고, 앞 PR 부터 차례로 합칩니다.

**생각 친구 대화 엔진 PR 병합 순서** (각각 앞 브랜치 위에 쌓여 있음)

1. `chore/backend-foundation` — 의존성·설정·DB·OpenAI 래퍼
2. `feature/shadow-inquiry` — 그림자 첫 탐구
3. `feature/family-auth-safety` — 가족·권한·안전 필터
4. `feature/thinking-talk-engine` — 대화 엔진·첫 만남·카테고리
5. `feature/words-stories-progress` — 단어·이야기책·성장 기록·상담
6. `feature/sharing-circles` — 공유·친구 모임
7. `feature/speech-input` — 음성 입력
8. `docs/api-docs-readme` — `/docs` 한국어 설명·README

## 커밋 규칙

`<gitmoji> <type>: <한국어 요약>` — 본문에는 무엇을 왜 바꿨는지 `-` 목록으로 적습니다.

| gitmoji | type | 쓰는 때 |
|---|---|---|
| ✨ | `feat` | 새 기능 |
| 🐛 | `fix` | 버그 수정 |
| 🔒️ | `feat`/`fix` | 보안·개인정보·안전 |
| ♻️ | `refactor` | 동작 변화 없는 구조 개선 |
| ✅ | `test` | 테스트 |
| 📝 | `docs` | 문서 |
| 🔧 | `chore` | 설정·의존성 |
| 👷 | `ci` | CI |
| 🔀 | `merge` | 브랜치 병합 |

예: `✨ feat: 대화 마치기 API 추가`

---

## API 요약

전체 설명·예시는 `/docs` 에 있습니다. 요청·응답 키는 camelCase 입니다.

### 생각 친구 대화 엔진

| 엔드포인트 | 토큰 | 하는 일 |
|---|---|---|
| `POST /families` | — | 가족 생성, 보호자 토큰 |
| `POST /guardian/children` · `PUT …/{id}/permissions` · `POST/DELETE …/{id}/devices` | 보호자 | 아이 등록, 권한, 태블릿 토큰 발급·회수 |
| `GET /guardian/children/{id}/talks[/{talkId}]` · `…/safety-events` · `…/progress` · `…/words` | 보호자 | 대화 확인, 안전 알림, 성장 기록, 단어 |
| `GET /onboarding` · `POST /onboarding/messages` · `POST /onboarding/confirm` | 아이 | 첫 만남 대화로 프로필 추출·확정 |
| `GET /talks/today` · `POST /talks` · `POST /talks/{id}/turns` · `POST /talks/{id}/finish` | 아이 | 오늘 테마, 대화 시작(주제/일기/내 카테고리/공유 모험), 대화 한 번, 마치기 |
| `GET/POST/DELETE /children/me/categories` · `POST …/{id}/topics` | 아이 | `!` 카테고리, 주제 제안 |
| `POST /words/explain` · `/children/me/words` · `/children/me/word-quizzes` | 아이 | 낱말 풀이, 단어 보관함, 퀴즈 |
| `POST/GET /guardian/children/{id}/word-tests` | 보호자 | 단어 검사 |
| `/children/me/stories` · `/children/me/books` | 아이 | 이야기, 이야기책 |
| `POST /children/me/shares` · `POST /guardian/shares/{id}/decision` · `POST /guardian/adventures` · `GET /shares` · `POST /shares/{id}/reports` · `/guardian/circles` | 아이·보호자 | 공유 요청·승인, 보호자 모험, 둘러보기, 신고, 친구 모임 |
| `POST /speech/realtime-sessions` · `POST /speech/transcriptions` | 아이 | 실시간 인식 키, 녹음 파일 인식 |
| `POST /voice/synthesize` | 아이 | 문장을 음성(mp3/wav)으로 읽어 줌(Typecast, voice_id 고정) — JSON 아닌 오디오 바이너리 응답 |
| `POST/GET /guardian/children/{id}/consultations` | 보호자 | 1달 뒤 AI 상담 |

### 그림자·길 찾기 첫 탐구 (토큰 없음)

| 엔드포인트 | 하는 일 |
|---|---|
| `GET /missions/shadow` | 24개 조합 그림자 길이표·친구 생각 목록·확인된 사실 |
| `POST /inquiry/interpret` | 처음 생각 이해 + 아이와 다른 친구 생각 |
| `POST /inquiry/teach` | 실험 카드로 친구 설득(규칙 판정) |
| `POST /inquiry/challenge` | 친구의 새 예측 |
| `POST /path/teach` | 티키 말로 가르치기: 아이 말 → 글자 그대로 프로그램 / 되묻기 / 못 알아들음 |
| `POST /path/react` | 실행 결과에 티키 반응 + 도착 시 도전 지도 고르기 |

### 초기 체험 기능 (Claude)

| 엔드포인트 | 입력 | 출력 |
|---|---|---|
| `POST /diagnostic/assess` | `answers[], child` | `{followupIntensity, vocabLevel, rationale, ai, error}` |
| `POST /rubric/score` | `question, answer, child, consent?` | `{observe, reason, express, quote, followup, comment, ai, error}` |
| `POST /theater/script` | `keyword, child` | `{safe, reason, title, scenes[], learn, ai, error}` (폴백 없음) |
| `GET /theater/library` · `/theater/library/{id}` | — | 검수 대본 6종 |
| `POST /lab/activity` | `topic, child` | `{title, ctrlLabel, ask, concept, quiz[], ai, error}` |
| `POST /report/summary` | `sentences[], weeklyScores[]` | `{summary, next, ai, error}` |
| `GET /tech/panel` · `GET /health` | — | 호출·차단 로그, 서버 상태 |

---

## 안전 원칙

- **대화 한 번의 순서**: 민감 주제 감지 → 문장 확인 → 개인정보 가림 → Moderation(허용 시) → 흐름 결정(규칙) → AI 문장 생성 → 출력 검사(민감 주제·낱말 존재·그림 키) → 실패 시 규칙 대사
- **아동 데이터**: OpenAI 18세 미만 지침상 13세 미만 개인정보는 ZDR 승인 뒤에만 처리합니다. 승인 전(`demo`)에는 대화·음성·Moderation 어디로도 아이 문장을 보내지 않습니다.
- **저장하지 않는 것**: 학교 이름·주소·실명·생일, 음성 원본, 민감한 말의 원문(종류만 기록)
- **공유**: 개인정보를 가린 복사본만 게시, 보호자 승인 필수, 전체 공개는 검사 통과 또는 사람 검토
- **점수 없음**: 빈도와 성취 기준만 셉니다
- 초기 체험 기능: 1차 금칙어(`safety/blocklist.py`) → 마음극장 2차 AI 심사 → 차단 로그

## 폴더 구조

```
app/
├── main.py · config.py · db.py · clock.py · auth.py · models.py · api_docs.py
├── routers/     families · onboarding · talks · categories · library · shares · progress · speech · voice(TTS)
│                inquiry(그림자 첫 탐구) · diagnostic · rubric · theater · lab · report · tech
├── talks/       planner(대화 흐름) · sentences(문장 확인) · topics(주제·요일 테마) · plot(이야기) · onboarding · progress
├── missions/    shadow.py (그림자 모형·설득 판정)
├── prompts/     talk.py · inquiry.py · shared.py · 초기 기능 프롬프트
├── schemas/     family · talk · library · inquiry · common · 초기 기능 스키마
├── services/    llm(OpenAI) · speech · tts(Typecast) · moderation · sharing · usage · claude · diagnostics · storage
└── safety/      topics(민감 주제) · blocklist · pii
tests/           conftest(메모리 DB·고정 시계) + 기능별 테스트
```

## 남은 작업

**보안·인프라**
- [ ] 보호자 실제 인증(Supabase Auth 등)과 법정대리인 동의 확인 — 지금은 개발용 토큰(`gt_`/`ct_`)뿐, 실제 신원 확인 없음
- [ ] `CHILD_DATA_MODE=child` 전환을 위한 OpenAI ZDR(Zero Data Retention) 승인
- [x] 배포(HTTPS)와 `CORS_ORIGINS` 배포 도메인 — Vercel 배포 완료 (`https://think-forest-backend.vercel.app`), Netlify 프론트(`https://think-kids.netlify.app`) 연동 및 CORS 허용 완료
- [ ] 저장소 기본 브랜치를 `develop` 으로 바꾸기(저장소 관리자 설정) — 확인 결과 현재 기본 브랜치가 `feat/jinyoung-backend-init` 로 되어 있음

**데이터 영속화**
- [x] PostgreSQL 운영 DB 연결 — Supabase Postgres Connection Pooler 연결 및 `psycopg` 드라이버 적용 완료 (Vercel 배포 연동)
- [ ] Alembic 마이그레이션, 보관기간 만료 삭제 배치
- [ ] 호출 한도·사용량(`services/usage.py`, `services/diagnostics.py`)이 전부 인메모리 — 재시작하면 초기화됨, DB/Redis 로 이전 필요

**안전 필터**
- [ ] [#10](https://github.com/FIFTEEN-BILLION/Think-Forest-Backend/issues/10) 외모 필터 오탐("잘 생기다/못 생기다") 수정
- [ ] 주제 은행 사실 문장 교육 검수, 민감 주제 패턴 보강(자모 분리 우회 등)

**검증**
- [ ] 음성 입력(STT) 실제 호출 검증(`/speech/transcriptions`·`/speech/realtime-sessions`), 실시간 인식 세션의 한국어 지정 필드명 재확인
- [ ] 태블릿 실기기 테스트
- [ ] [#11](https://github.com/FIFTEEN-BILLION/Think-Forest-Backend/issues/11) `.env`에 실제 키가 있으면 깨지는 `no_api_key` 테스트 2건 수정(환경 격리)

**프론트엔드 연동**
- [x] 프론트엔드 배포 연동 — Netlify(`https://think-kids.netlify.app`)에서 Vercel 배포 백엔드를 호출하여 실제 OpenAI 연동 첫 탐구 v2 완주 검증 완료
- [ ] `POST /voice/synthesize`(Typecast TTS)를 화면 어디에 연결할지 결정 — 지금은 범용 엔드포인트만 있고 프론트 연동은 안 됨
