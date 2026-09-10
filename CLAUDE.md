# Think-Forest Backend (자람마을 API)

아동용 AI 학습 서비스 **자람마을**의 백엔드. 프론트엔드(`../frontend`, Vite)의 AI 호출을 대신 수행한다.

## 소통

- **항상 한국어로 소통한다.** 응답, 커밋 메시지, PR 설명, 문서, 코드 주석 모두 한국어를 기본으로 한다.

## 이 레포가 존재하는 이유

**API 키를 브라우저에 노출하지 않기 위해서다.** 모든 Claude 호출은 여기서 서버 사이드로 나간다. 프론트엔드는 절대 Anthropic SDK를 직접 부르지 않는다.

## 스택

FastAPI / Python 3.11+ / anthropic SDK / pydantic v2 / python-dotenv

## 명령어

```bash
source .venv/bin/activate        # Windows Git Bash: source .venv/Scripts/activate
fastapi dev app/main.py          # 개발 서버 http://127.0.0.1:8000
# 자동 문서: http://127.0.0.1:8000/docs

pip install -r requirements.txt
pip freeze > requirements.txt     # 패키지 추가 후 반드시 갱신
```

## 폴더 구조

```
app/
├── main.py            # 앱 생성, CORS, 라우터 등록
├── config.py          # 환경변수 로딩
├── routers/           # theater.py, lab.py, rubric.py, report.py
├── services/claude.py # Anthropic 호출 · 호출 로그 · 폴백
├── safety/
│   ├── blocklist.py   # 1차 금칙어 필터 (AI 호출 전)
│   └── pii.py         # 저장 직전 개인식별정보 마스킹
└── schemas/           # Pydantic 요청·응답 모델
```

## API 계약

프론트엔드가 의존하므로 **응답 스키마를 바꾸면 `../frontend/src/lib/api.ts`도 같이 고친다.**

| 엔드포인트 | 입력 | 출력 |
|---|---|---|
| `GET /health` | — | `{ok: true}` |
| `POST /diagnostic/assess` | 진단 답변 목록, 아이 컨텍스트 | `{followupIntensity, vocabLevel, rationale, ai}` |
| `POST /rubric/score` | 질문, 아이 답변, 아이 컨텍스트, (동의) | `{observe, reason, express, quote, followup, comment, ai}` |
| `POST /theater/script` | 키워드, 아이 컨텍스트 | `{safe, reason, title, scenes[5], learn, ai}` |
| `GET /theater/library` `GET /theater/library/{id}` | — | 검수 대본 6종 `{id, keyword, title, scenes[5], learn, parentNote}` |
| `POST /lab/activity` | 주제, 아이 컨텍스트 | `{title, ctrlLabel, ask, concept, quiz, ai}` |
| `POST /report/summary` | 아이 문장 목록, 주별 점수 | `{summary, next, ai}` |
| `GET /tech/panel` | — | `{aiEnabled, model, callCount, avgLatencyMs, calls[], blocks[]}` (심사용 진단) |

요청·응답 키는 camelCase. 모든 AI 응답에 `error: string | null` 이 함께 실린다(성공 시 null).

**모든 응답에 `ai: bool`을 넣는다.** 실제 Claude 호출이면 `true`, 규칙 기반 폴백이면 `false`. 프론트엔드가 이 값으로 화면 배지를 바꾼다. 이 필드를 빠뜨리면 "정직한 표기" 원칙이 깨진다.

## 안전 파이프라인 — 순서가 중요하다

1. **1차 금칙어 필터** (`safety/blocklist.py`) — 문자열 포함 검사. **반드시 Claude 호출 전에** 돌린다. 차단되면 토큰 비용 0.
2. **2차 생성 단계 AI 심사** — 대본 프롬프트가 `safe: false`를 반환하면 결과 폐기.
3. **3차 부모 먼저보기** — 프론트엔드 담당.
4. **4차 차단 로그** — 차단 건을 기록해 반환한다.

**1차와 2차 순서를 바꾸지 말 것.** 비용 설계이자 안전 설계다.

## 개인정보

- 아이 발화는 **저장 직전에 `safety/pii.py`로 마스킹**한다 — 전화번호, 학교·기관명, 집 주소, 생년월일, 이름 패턴.
- 보호자 동의(`consent.guardian`)가 없으면 아이 발화를 저장하지 않는다. 단 **체험 자체는 막지 않는다.**
- 보관기간 기본 90일. 만료 건은 자동 파기.
- 음성 원본, 사진·영상, 위치, 생년월일은 **수집하지 않는다** (연령대만 저장).

## 코딩 규칙

- 요청·응답은 전부 Pydantic 모델로 정의한다. `dict`를 그대로 반환하지 않는다.
- Claude 호출은 `services/claude.py`의 래퍼 한 곳만 지난다. 라우터에서 SDK를 직접 부르지 않는다.
- 래퍼는 목적·모델·지연시간·성공/실패 코드를 로깅한다. 이 로그가 프론트 기술 패널에 노출된다.
- **모든 AI 경로에 규칙 기반 폴백이 있어야 한다.** 예외는 마음극장 대본 — 여기는 폴백으로 템플릿을 만들지 않고 실패를 정직하게 알린다.
- 예외를 삼키지 않는다. 실패는 코드와 함께 응답에 담아 프론트가 표시할 수 있게 한다.
- 프롬프트 문자열은 라우터가 아니라 별도 모듈에 모은다.

## 절대 하지 말 것

- ❌ **`.env`를 커밋하기.** push 전 `git status --short`로 확인.
- ❌ **API 키를 코드·로그·응답에 넣기.**
- ❌ **`allow_origins=["*"]`로 두기.** `CORS_ORIGINS` 환경변수로 명시한다.
- ❌ **확인되지 않은 사실을 프롬프트나 콘텐츠에 넣기.** 교육과정 코드·연도·수치는 출처가 확인된 것만.
- ❌ 아이 발화를 마스킹 없이 저장하거나 로그에 남기기.

## 환경변수

```
ANTHROPIC_API_KEY=      # 필수
CORS_ORIGINS=http://localhost:5173
```

`.env.example`은 커밋한다. `.env`는 절대 커밋하지 않는다.

## 작업 방식

- 엔드포인트를 만들면 `http://127.0.0.1:8000/docs`에서 직접 호출해 확인한다.
- 응답 스키마를 바꾸면 프론트엔드 `lib/api.ts`도 같이 고친다.
- 커밋 메시지: `feat:` `fix:` `refactor:` `chore:` `docs:` 접두사. 한글 가능.
- `main`에 직접 커밋하지 않는다. `feat/<이름>-<기능>` 브랜치에서 작업 후 PR.
