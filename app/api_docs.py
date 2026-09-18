"""API 문서(/docs) 한국어 설명 — 엔드포인트마다 무엇을 하고, 누가, 어떤 토큰으로 부르는지.

라우터 코드를 건드리지 않고 생성된 OpenAPI 스키마에 설명·순서·예시 본문을 덧붙인다.
새 엔드포인트를 만들면 OPERATIONS 에 한 줄을 추가한다(tests/test_api_docs.py 가 빠진 것을 잡는다).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

DESCRIPTION = """
초등 2~4학년(8~10살) 아이가 AI **생각 친구**와 대화하며 생각을 문장으로 말하고 넓히는 서비스의 API 입니다.

## 5분 만에 직접 해 보기
1. **`POST /families`** 실행 → 응답의 `guardianToken`(`gt_…`)을 복사합니다. *보호자 토큰*
2. **`POST /guardian/children`** → `authorization` 칸에 `Bearer gt_…` 를 넣고 실행 → 응답의 `id` 복사
3. **`POST /guardian/children/{child_id}/devices`** → 응답의 `childToken`(`ct_…`) 복사. *아이 태블릿 토큰*
4. **`GET /onboarding`**, **`POST /onboarding/messages`** → 이제부터 `Bearer ct_…` 로 첫 만남 대화
5. **`POST /talks`** 로 대화를 시작하고 **`POST /talks/{talk_id}/turns`** 로 한 번씩 이야기합니다

> Swagger 상단에 전체 인증 버튼은 없습니다. 각 요청의 **`authorization`** 칸에 `Bearer 토큰` 을 직접 넣어 주세요.

## 토큰 규칙
| 경로 | 필요한 토큰 |
|---|---|
| `/guardian/...` | 보호자 토큰 `gt_…` |
| `/me`, `/onboarding`, `/talks`, `/children/me/...`, `/words/...`, `/speech/...` | 아이 토큰 `ct_…` |
| `/shares` (둘러보기·신고) | 보호자 또는 아이 토큰 |
| `/families`, `/missions/...`, `/inquiry/...`, `/path/...`, 기존 체험 기능 | 토큰 없음 |

## 응답에서 꼭 볼 필드
- **`ai`** — `true` 면 실제 AI(OpenAI `gpt-5.6-luna`)가 만든 문장, `false` 면 규칙 기반 대사입니다.
- **`error`** — AI 를 못 쓴 이유. `no_api_key`(키 없음), `child_data_mode_off`(ZDR 승인 전이라 아이 문장을 AI 로 보내지 않음) 등.
- **`accepted`** (대화) — 문장으로 인정되어 대화가 앞으로 나아갔는지. 단답이면 `false`.
- **`move`** (대화) — 친구가 방금 던진 질문 종류. `tail` 꼬리질문 · `connect` 생활 연결 · `challenge` "진짜일까?" ·
  `imagine` 상상 · `reason_check` 생각 지키기/바꾸기 · `compose` 긴 문장으로 정리 · `continue` 이야기 완성 뒤 이어 가기

## 자주 보는 오류
| 코드 | 뜻 |
|---|---|
| `401 missing_token` / `invalid_*_token` | `Bearer ` 를 빠뜨렸거나 토큰 종류가 다릅니다 |
| `403 permission_required:voice` 등 | 보호자가 권한을 주지 않았습니다 (`PUT /guardian/children/{id}/permissions`) |
| `403 child_data_mode_off` | ZDR 승인 전에는 아이 음성을 AI 로 보내지 않습니다. 성인 테스터 계정(`tester: true`)으로 시험하세요 |
| `409 compose_first` / `min_time` | 아직 정리(이야기 완성) 전이거나 대화 시간 15분이 안 됐습니다 |
| `422 unsafe_*` | 민감한 표현(선정·정치·폭력·외모·개인정보)이 들어 있습니다 |
"""

# 원래 태그 → (보이는 이름, 설명). 이 순서대로 문서에 나온다.
TAGS: dict[str, tuple[str, str]] = {
    "family": (
        "1. 가족·아이·권한 (보호자)",
        "보호자가 가족을 만들고, 아이를 등록하고, 태블릿 토큰과 권한(음성·공유)을 줍니다. 친구 모임도 여기서 만듭니다.",
    ),
    "onboarding": (
        "2. 첫 만남 대화 (아이)",
        "아이와 채팅하며 별명·소속+학년·좋아하는 것·키우고 싶은 것을 뽑습니다. 학교 이름은 저장하지 않습니다.",
    ),
    "talks": (
        "3. 생각 친구 대화 (아이)",
        "주제를 던지고 → 꼬리질문 → 생활 연결 → '진짜일까?' → 상상 → 생각 지키기/바꾸기 → 긴 문장 정리 → 이야기 완성. "
        "답은 문장이어야 하고, 한 이야기는 실제 대화 시간 15분이 필수입니다.",
    ),
    "categories": ("4. 내가 만든 카테고리 (아이)", "아이가 '!' 버튼으로 관심 카테고리를 만들고 대화 주제를 제안받습니다."),
    "speech": (
        "5. 음성 입력 (아이)",
        "말하는 동안 글자를 바로 보여 주는 실시간 인식 키, 녹음 파일 인식. 보호자의 음성 권한이 필요합니다.",
    ),
    "voice": (
        "5. 음성 출력 (아이)",
        "문장을 Typecast 음성으로 읽어 줍니다. 보호자의 음성 권한이 필요합니다.",
    ),
    "library": (
        "6. 단어·이야기책 (아이·보호자)",
        "어려운 낱말 풀이, 단어 보관함, 단어 퀴즈와 보호자 단어 검사, 완성한 이야기와 이야기책.",
    ),
    "shares": (
        "7. 모험 이야기 공유",
        "아이 공유 요청 → 보호자 승인 → 가족/친구 모임/전체 공개. 보호자가 만든 모험으로 대화를 시작할 수도 있습니다.",
    ),
    "progress": (
        "8. 성장 기록·보호자 상담",
        "점수 없이 빈도와 성취 기준만 셉니다. 1달 이용 뒤 보호자용 AI 상담 요약을 받을 수 있습니다.",
    ),
    "inquiry": (
        "9. 첫 탐구: 그림자 실험",
        "헷갈리는 생각 친구를 공정한 실험 증거로 설득하는 첫 탐구(프론트 화면과 연결됨). 토큰이 필요 없습니다.",
    ),
    "path": (
        "9. 첫 탐구: 티키 말로 가르치기",
        "아이가 AI 캐릭터 티키에게 편지 배달 길을 말로 가르칩니다. 티키는 말을 글자 그대로 프로그램으로 옮기고(모호하면 되물음), "
        "실행 뒤 반응하며, 도착하면 도전 지도를 골라 도발합니다. 실행은 프론트 엔진이 합니다. 토큰이 필요 없습니다.",
    ),
    "diagnostic": ("10. 기존 체험 기능", "초기 버전의 진단·채점·실험실·마음극장·리포트 API (Claude 경로)."),
    "rubric": ("10. 기존 체험 기능", ""),
    "lab": ("10. 기존 체험 기능", ""),
    "theater": ("10. 기존 체험 기능", ""),
    "report": ("10. 기존 체험 기능", ""),
    "tech": ("11. 서버 상태", "서버 동작 확인과 AI 호출·차단 로그."),
    "health": ("11. 서버 상태", ""),
    # --- v1 auth ---
    "v1-auth": (
        "12. JJCP v1 로그인",
        "카카오 웹·모바일 로그인, JJCP access/refresh token 갱신·로그아웃, 개발용 로그인. 경로는 `/api/v1/auth/...`.",
    ),
    # --- /v1 auth ---
}

G, C, V, N = "보호자 토큰 `gt_…`", "아이 토큰 `ct_…`", "보호자 또는 아이 토큰", "토큰 없음"
# --- v1 auth ---
V1_ACCESS = "JJCP access token `jat_…`"
# --- /v1 auth ---

# (메서드, 경로) → (요약, 설명, 토큰)
OPERATIONS: dict[tuple[str, str], tuple[str, str, str]] = {
    # 1. 가족·아이·권한
    ("POST", "/families"): (
        "① 가족 만들기 (여기서 시작)",
        "새 가족을 만들고 **보호자 토큰**(`guardianToken`)을 한 번만 보여 줍니다. 복사해 두세요.\n\n"
        "개발용 토큰이며 실제 보호자 본인 확인이 아닙니다.",
        N,
    ),
    ("POST", "/guardian/children"): (
        "② 아이 등록",
        "가족에 아이를 등록합니다. `nickname` 은 비워도 되고(첫 만남 대화에서 받음), "
        "`tester: true` 는 **성인 테스터 계정**이라 ZDR 승인 전에도 실제 AI 를 씁니다.",
        G,
    ),
    ("GET", "/guardian/children"): ("우리 가족 아이 목록", "등록한 아이들의 프로필과 권한을 봅니다.", G),
    ("GET", "/guardian/children/{child_id}"): ("아이 한 명 보기", "프로필(별명·학년·좋아하는 것 등)과 권한.", G),
    ("PUT", "/guardian/children/{child_id}/permissions"): (
        "권한 주기",
        "보호자 화면 대신 권한으로 관리합니다.\n\n"
        "- `voice`: 음성 입력\n- `browseShared`: 다른 가족·친구의 공유 이야기 보기\n- `publishRequest`: 내 이야기 공유 요청",
        G,
    ),
    ("POST", "/guardian/children/{child_id}/devices"): (
        "③ 아이 태블릿 토큰 발급",
        "아이가 혼자 쓸 기기용 **아이 토큰**(`childToken`)을 발급합니다. 아이용 API 는 모두 이 토큰을 씁니다.",
        G,
    ),
    ("DELETE", "/guardian/children/{child_id}/devices"): (
        "아이 토큰 모두 회수",
        "잃어버린 기기 등으로 발급한 아이 토큰을 모두 사용할 수 없게 만듭니다.",
        G,
    ),
    ("GET", "/guardian/children/{child_id}/safety-events"): (
        "안전 알림 보기",
        "대화 중 민감한 말(외모·정치·폭력·자해 신호 등)이 나온 기록. 아이 원문은 남기지 않고 **종류만** 보여 줍니다. "
        "`escalate: true` 는 자해 신호라 꼭 확인이 필요합니다.",
        G,
    ),
    ("GET", "/me"): ("내 프로필 (아이)", "아이 토큰이 맞는지 확인하고 내 프로필·권한을 봅니다.", C),
    ("POST", "/guardian/circles"): (
        "친구 모임 만들기",
        "친구·또래 가족끼리 이야기를 나눌 모임을 만들고 **초대 코드**(`code`)를 받습니다.",
        G,
    ),
    ("GET", "/guardian/circles"): ("내가 속한 모임 목록", "우리 가족이 들어가 있는 친구 모임.", G),
    ("POST", "/guardian/circles/join"): ("초대 코드로 모임 들어가기", "다른 가족이 준 초대 코드로 모임에 들어갑니다.", G),
    # 2. 첫 만남 대화
    ("GET", "/onboarding"): (
        "첫 만남 대화 시작",
        "생각 친구의 첫 인사와 질문을 받습니다. `missing` 은 아직 모르는 항목입니다.",
        C,
    ),
    ("POST", "/onboarding/messages"): (
        "첫 만남 대화에 답하기",
        "아이의 답에서 별명·소속+학년·좋아하는 것·키우고 싶은 것을 뽑고 다음 질문(`reply`)을 줍니다.\n\n"
        "학교 이름을 말해도 **소속 종류와 학년만** 저장합니다(`notes: school_name_not_saved`). `done: true` 면 끝.",
        C,
    ),
    ("POST", "/onboarding/confirm"): (
        "프로필 확정",
        "뽑은 프로필을 아이가 확인해 확정합니다. 빈 본문 `{}` 이면 그대로 확정, 값을 넣으면 고쳐서 확정합니다.",
        C,
    ),
    # 3. 생각 친구 대화
    ("GET", "/talks/today"): (
        "오늘의 테마와 주제 제안",
        "요일마다 테마가 바뀝니다(월 과학 · 화 수학 · 수 역사 · 목 생각놀이 · 금 상상 실험 · 토 일기 · 일 내 주제). "
        "추천 주제 3개와 카테고리 목록을 줍니다. `visual`·`color`·`mood` 는 화면 분위기 키입니다.",
        C,
    ),
    ("POST", "/talks"): (
        "④ 대화 시작",
        "빈 본문 `{}` 이면 오늘 테마로 시작합니다. 선택지:\n\n"
        "- `topicId`: `ice_cup` `airplane` `snow` `pizza_share` `stairs_pattern` `old_fire` `hangul` `what_is_car` "
        "`no_rules_class` `today`\n"
        "- `mode: \"diary\"`: 오늘 있었던 일로 일기 대화\n"
        "- `customCategoryId` (+ `customTopic`): 내가 만든 카테고리\n"
        "- `sharedItemId`: 보호자·친구가 공유한 모험\n\n"
        "응답의 첫 턴이 생각 친구의 첫 질문입니다.",
        C,
    ),
    ("GET", "/talks"): ("내 대화 목록", "시작한 대화들(진행 중·완료)을 최신순으로 봅니다.", C),
    ("GET", "/talks/{talk_id}"): ("대화 전체 보기", "모든 턴, 대화 시간(`time`), 완성한 이야기(`story`)를 봅니다.", C),
    ("POST", "/talks/{talk_id}/turns"): (
        "⑤ 대화 한 번 하기",
        "아이의 말(`text`)을 보내면 생각 친구가 반응하고 다음 질문을 합니다.\n\n"
        "- **단답이면** `accepted: false` — 대화가 넘어가지 않고 문장 틀(`sentenceStarters`)을 줍니다\n"
        "- **민감한 말이면** `safety` 에 종류가 오고 주제로 돌아갑니다(원문은 저장하지 않음)\n"
        "- `move` 가 `compose` 가 되면 세 문장 이상으로 정리해서 보내세요 → `story` 에 이야기 완성본이 옵니다\n"
        "- `friendTurn.words` 는 어려운 낱말 뜻(호버용), `friendTurn.visual` 은 그림 키\n"
        "- `time.canFinish` 가 `true` 면 마칠 수 있습니다",
        C,
    ),
    ("POST", "/talks/{talk_id}/finish"): (
        "⑥ 대화 마치기",
        "이야기가 완성되고 실제 대화 시간이 15분 이상이어야 마칠 수 있습니다.\n\n"
        "- `409 compose_first`: 아직 정리(이야기 완성) 전\n- `409 min_time`: `remainingSeconds` 만큼 더 이야기해야 함\n\n"
        "이야기를 만든 뒤 더 나눈 생각이 있으면 이야기를 다시 만듭니다.",
        C,
    ),
    ("GET", "/guardian/children/{child_id}/talks"): ("아이 대화 목록 (보호자 확인)", "보호자가 아이의 대화를 확인합니다.", G),
    ("GET", "/guardian/children/{child_id}/talks/{talk_id}"): (
        "아이 대화 내용 보기 (보호자 확인)",
        "대화의 모든 턴과 완성한 이야기.",
        G,
    ),
    # 4. 카테고리
    ("GET", "/children/me/categories"): ("카테고리 목록", "기본 카테고리(과학·수학·역사·생각놀이·일기) + 내가 만든 카테고리.", C),
    ("POST", "/children/me/categories"): (
        "'!' 버튼 — 카테고리 만들기",
        "아이가 관심 있는 카테고리(최대 12자, 최대 20개)를 만듭니다. 민감한 이름은 `422 unsafe_category`.",
        C,
    ),
    ("DELETE", "/children/me/categories/{category_id}"): ("카테고리 지우기", "내가 만든 카테고리를 지웁니다.", C),
    ("POST", "/children/me/categories/{category_id}/topics"): (
        "카테고리로 대화 주제 제안받기",
        "주제 3개(`title`, 첫 질문 `hook`)를 제안합니다. 마음에 드는 것을 `POST /talks` 의 `customTopic` 으로 보내세요.",
        C,
    ),
    # 5. 음성
    ("POST", "/speech/transcriptions"): (
        "녹음 파일 → 문장",
        "녹음이 끝난 짧은 음성(webm·mp4·m4a·wav·mp3·ogg, 5MB 이하)을 한국어 문장으로 바꿉니다. "
        "아이가 확인·수정한 뒤 대화로 보냅니다.\n\n"
        "필요: 보호자 `voice` 권한 + (ZDR 승인 모드 또는 성인 테스터 계정). 음성은 저장하지 않습니다.",
        C,
    ),
    ("POST", "/speech/realtime-sessions"): (
        "실시간 음성 인식 키 받기",
        "말하는 동안 글자를 **바로 화면에 보여 주기** 위한 임시 키(2분 유효)입니다. "
        "브라우저가 이 키로 OpenAI Realtime 에 직접 연결합니다. 필요 조건은 녹음 파일 인식과 같습니다.",
        C,
    ),
    ("POST", "/voice/synthesize"): (
        "문장을 음성으로 읽어주기",
        "`text`(최대 2000자)를 Typecast 로 합성해 오디오(mp3)를 그대로 돌려줍니다(JSON 아님). "
        "voice_id 는 서버 설정에 고정되어 있습니다. 필요: 보호자 `voice` 권한. "
        "금칙어가 섞이면 `403 blocked_content`, 하루 호출 한도를 넘으면 `429 daily_limit`.",
        C,
    ),
    # 6. 단어·이야기책
    ("POST", "/words/explain"): (
        "어려운 낱말 풀이",
        "글 속에서 8~10살이 어려워할 낱말과 뜻·예문을 줍니다(호버·탭 설명용). AI 를 못 쓰면 검수된 주제 사전에서만 찾습니다.",
        C,
    ),
    ("GET", "/children/me/words"): ("단어 보관함", "보관한 단어와 퀴즈에서 본 횟수·맞힌 횟수. 두 번 맞히면 `learned: true`.", C),
    ("POST", "/children/me/words"): ("단어 보관하기", "대화에서 만난 낱말을 보관함에 넣습니다. 이미 있으면 그대로 돌려줍니다.", C),
    ("DELETE", "/children/me/words/{word_id}"): ("단어 빼기", "보관함에서 단어를 뺍니다.", C),
    ("POST", "/children/me/word-quizzes"): (
        "단어 퀴즈 만들기",
        "보관한 단어로 '뜻 고르기' 퀴즈(보기 3개)를 만듭니다. 정답 번호는 응답에 나오지 않습니다.",
        C,
    ),
    ("GET", "/children/me/word-quizzes"): (
        "내 퀴즈 목록",
        "`pending=true` 면 안 끝난 퀴즈만. 보호자가 낸 단어 검사는 `assignedBy: guardian`.",
        C,
    ),
    ("POST", "/children/me/word-quizzes/{quiz_id}/answers"): (
        "퀴즈 답하기",
        "`index`(몇 번째 문제)와 `chosen`(고른 보기 번호, 0부터)을 보내면 맞았는지 알려 줍니다.",
        C,
    ),
    ("POST", "/guardian/children/{child_id}/word-tests"): (
        "보호자 단어 검사 내기",
        "아이의 단어 보관함으로 검사를 냅니다. 아이는 `GET /children/me/word-quizzes?pending=true` 에서 풉니다.",
        G,
    ),
    ("GET", "/guardian/children/{child_id}/word-tests"): ("단어 검사 결과 보기", "보호자가 낸 검사와 아이의 답.", G),
    ("GET", "/guardian/children/{child_id}/words"): ("아이 단어 보관함 보기", "보호자가 아이가 모은 단어를 봅니다.", G),
    ("GET", "/children/me/stories"): ("내 이야기 목록", "대화·일기로 완성한 이야기 플롯들.", C),
    ("GET", "/children/me/stories/{story_id}"): (
        "이야기 한 편 보기",
        "장면(`scenes`)마다 근거가 된 아이 발화 id(`fromTurnIds`)가 달려 있습니다.",
        C,
    ),
    ("POST", "/children/me/books"): ("이야기책 만들기", "완성한 이야기 여러 편을 골라 한 권의 책으로 묶습니다.", C),
    ("GET", "/children/me/books"): ("내 이야기책 목록", "만든 이야기책들.", C),
    ("GET", "/children/me/books/{book_id}"): ("이야기책 보기", "책에 담긴 이야기를 순서대로 봅니다.", C),
    # 7. 공유
    ("POST", "/children/me/shares"): (
        "내 이야기 공유 요청",
        "`publishRequest` 권한이 필요합니다. 개인정보를 가린 복사본으로 **보호자 승인 대기**(`pending_guardian`)가 됩니다.\n\n"
        "`visibility`: `family` 우리 가족 · `circle` 친구 모임(`circleId` 필요) · `community` 전체",
        C,
    ),
    ("GET", "/guardian/shares"): ("우리 가족 공유 목록", "`status=pending_guardian` 으로 승인 대기만 볼 수 있습니다.", G),
    ("POST", "/guardian/shares/{item_id}/decision"): (
        "공유 승인·거절",
        "`approve: true` 면 가족·모임은 바로 게시됩니다. 전체 공개는 콘텐츠 검사를 통과해야 하고, "
        "검사할 수 없으면 사람 검토 대기(`pending_review`)가 됩니다.",
        G,
    ),
    ("POST", "/guardian/adventures"): (
        "보호자 모험 만들기",
        "주제 제목, 첫 질문(`hook`), 이어 갈 질문(`followUps`: 생활 연결 · 진짜일까? · 상상 순서, 최대 3개)으로 모험을 만듭니다. "
        "아이들은 `POST /talks` 의 `sharedItemId` 로 이 모험 대화를 시작합니다.",
        G,
    ),
    ("DELETE", "/guardian/shares/{item_id}"): ("공유 내리기", "우리 가족이 올린 공유를 숨깁니다.", G),
    ("GET", "/shares"): (
        "공유 이야기 둘러보기",
        "게시된 이야기·모험 중 볼 수 있는 것(우리 가족 · 내 모임 · 전체 공개). 아이는 `browseShared` 권한이 필요합니다. "
        "`kind`(story·book·adventure), `scope`(family·circle·community)로 거를 수 있습니다.",
        V,
    ),
    ("GET", "/shares/{item_id}"): ("공유 이야기 하나 보기", "볼 수 있는 범위의 게시물만 열립니다.", V),
    ("POST", "/shares/{item_id}/reports"): ("신고하기", "가족마다 한 번씩. 신고가 3건 쌓이면 자동으로 숨깁니다.", V),
    # 8. 성장 기록·상담
    ("GET", "/children/me/progress"): (
        "내 성장 기록",
        "점수 없이 **빈도**(이야기 수·대화 시간·최근 7일 활동)와 **성취 기준 7가지**"
        "(이유 말하기·새 생각 보태기·상상·생각 다시 보기·긴 문장 정리·끝까지 하기·새 단어 쓰기)를 씨앗·새싹·나무 단계로 보여 줍니다.",
        C,
    ),
    ("GET", "/guardian/children/{child_id}/progress"): ("아이 성장 기록 보기", "아이 성장 기록과 같은 내용을 보호자가 봅니다.", G),
    ("POST", "/guardian/children/{child_id}/consultations"): (
        "1달 이용 뒤 보호자 AI 상담 받기",
        "첫 대화 뒤 30일이 지나야 열립니다(`409 not_yet` + `daysRemaining`). "
        "AI 에는 아이 문장이 아니라 **집계 숫자만** 보냅니다.",
        G,
    ),
    ("GET", "/guardian/children/{child_id}/consultations"): ("상담 기록 보기", "받은 상담 요약들.", G),
    # 9. 첫 탐구
    ("GET", "/missions/shadow"): (
        "그림자 실험 준비물",
        "빛 높이·막대기 키·거리·밝기 24가지 조합의 그림자 길이표, 친구 생각 목록, 확인된 사실. 프론트는 이 표로만 그림을 그립니다.",
        N,
    ),
    ("POST", "/inquiry/interpret"): (
        "처음 생각 이해 + 친구 생각",
        "아이의 예측과 이유를 읽고, 아이 생각과 **다른** 헷갈리는 친구 생각을 고릅니다.",
        N,
    ),
    ("POST", "/inquiry/teach"): (
        "친구 설득하기",
        "아이가 고른 실험 카드와 설명으로 친구가 설득됐는지 **규칙이 판정**합니다. "
        "증거 없는 주장·여러 개를 같이 바꾼 실험·반대 방향은 절대 설득되지 않습니다. 실패할수록 질문 → 힌트 → 설명으로 도움이 커집니다.",
        N,
    ),
    ("POST", "/inquiry/challenge"): (
        "친구의 새 예측 받기",
        "아이가 아직 확인하지 않은 부분을 겨냥한 새 상황을 줍니다. 친구 예측이 맞는지 아이가 판단합니다.",
        N,
    ),
    ("POST", "/path/teach"): (
        "티키에게 말로 가르치기",
        "아이 말(`text`)과 지금 프로그램(`program`, 최대 8단계)을 받아 티키가 **글자 그대로** 옮긴 결과를 줍니다.\n\n"
        "- `kind`: `program`(새 전체 프로그램) · `clarify`(뜻이 갈려 되물음, `clarify.options` 2~3개에 각자 프로그램) · `unmapped`(못 알아들음, `program` 그대로)\n"
        "- 단계: `move`(`count` 1~5 또는 `until: blocked`=쭉) · `turn`(`dir`, 제자리 돌기) · `stop` · `if`(`sensor`·`state`·`then`·`else`) · `repeat`(`body`, 우체국까지 반복)\n"
        "- \"오른쪽으로 가\" = 오른쪽으로 돌고 1칸. \"오른쪽으로 돌아\" = 돌기만\n"
        "- 아이가 **말하지 않은 조건·반복은 보태지 않습니다**(누설 검사로 벗겨 냄). 되물음에 답하면 `pendingClarify` 에 담아 보냅니다\n"
        "- `heard`: 티키가 알아들은 것(최대 4). AI 를 못 쓰면 정규식 파서로 폴백합니다(`source: fallback`)",
        N,
    ),
    ("POST", "/path/react"): (
        "실행 결과에 티키가 반응하기",
        "프론트 엔진의 실행 결과(`result.outcome`: arrived·splashed·bumped·ended·loop·tooLong)를 받아 티키 한마디(`tikiLine`)와 질문(`question`)을 줍니다.\n\n"
        "- 고칠 방법·정답 길은 말하지 않습니다(누설 문장은 템플릿으로 교체)\n"
        "- 도착했을 때 `challengeCandidates`(엔진이 지금 프로그램으로는 실패함을 확인한 지도, 최대 4)를 주면 하나를 골라 `challengeId`·`challengeLine` 으로 도발합니다\n"
        "- 후보에 없는 id 는 첫 후보로 바뀌고, 후보가 없으면 둘 다 `null` 입니다",
        N,
    ),
    # 10. 기존 체험 기능
    ("POST", "/diagnostic/assess"): ("(기존) 첫 만남 진단", "진단 답변으로 되물음 강도·어휘 수준을 정합니다.", N),
    ("POST", "/rubric/score"): ("(기존) 되물음 채점", "아이 답을 관찰·추론·표현으로 채점하고 되물음을 만듭니다.", N),
    ("GET", "/theater/library"): ("(기존) 마음극장 검수 대본 목록", "검수된 대본 6종.", N),
    ("GET", "/theater/library/{script_id}"): ("(기존) 마음극장 대본 하나", "검수 대본 한 편.", N),
    ("POST", "/theater/script"): ("(기존) 마음극장 대본 생성", "키워드로 5장면 대본을 만듭니다.", N),
    ("POST", "/lab/activity"): ("(기존) 실험실 활동 생성", "주제로 관찰 활동을 만듭니다.", N),
    ("POST", "/report/summary"): ("(기존) 주간 리포트 요약", "아이 문장과 주간 점수로 부모 편지를 만듭니다.", N),
    # 11. 서버 상태
    ("GET", "/tech/panel"): ("기술·안전 패널", "AI 호출 수·지연·실패 코드와 차단 로그.", N),
    ("GET", "/health"): ("서버 켜짐 확인", "`{\"ok\": true}` 면 정상입니다.", N),
    # --- v1 auth ---
    ("GET", "/api/v1/auth/kakao/authorize"): (
        "v1 웹 카카오 로그인 시작",
        "`returnTo`(같은 출처 상대 경로, 아니면 `/`)를 기억하고 카카오 인가 화면으로 **302** 이동합니다. "
        "state(10분·1회용)와 PKCE(S256)를 씁니다.\n\n"
        "카카오 설정이 없으면 `503 AUTH_PROVIDER_UNAVAILABLE`. Swagger 에서는 리다이렉트를 따라가지 않으니 브라우저 주소창에서 여세요.",
        N,
    ),
    ("GET", "/api/v1/auth/kakao/callback"): (
        "v1 웹 카카오 로그인 콜백",
        "카카오가 호출합니다. state 검증 → 토큰 교환 → 회원 찾기/만들기 → refresh token 을 HttpOnly 쿠키 "
        "`jjcp_refresh`(Path=/api/v1/auth, SameSite=Lax)에 넣고 `returnTo` 로 302 이동합니다.\n\n"
        "access token 은 주지 않습니다. 화면에서 `POST /api/v1/auth/token/refresh` 를 불러 받으세요. "
        "실패하면 `returnTo?loginError=INVALID_STATE|KAKAO_CANCELLED|KAKAO_LOGIN_FAILED|AUTH_PROVIDER_UNAVAILABLE` 로 이동합니다.",
        N,
    ),
    ("POST", "/api/v1/auth/kakao/mobile"): (
        "v1 모바일 카카오 로그인",
        "앱의 카카오 SDK 로그인으로 받은 카카오 access token 을 서버가 카카오에서 확인한 뒤 JJCP 토큰을 줍니다. "
        "응답 본문에 `refreshToken` 이 들어 있으니 OS 보안 저장소에 두세요.\n\n"
        "카카오 토큰이 무효면 `401 UNAUTHORIZED`, 카카오 장애·설정 없음은 `503 AUTH_PROVIDER_UNAVAILABLE`.",
        N,
    ),
    ("POST", "/api/v1/auth/token/refresh"): (
        "v1 access token 갱신",
        "refresh token 으로 새 access token(1시간)을 받습니다. refresh token 도 매번 새로 바뀝니다.\n\n"
        "- **웹**: 본문 없이 호출 → 쿠키 `jjcp_refresh` 사용, 새 쿠키로 교체, 본문에 `refreshToken` 없음\n"
        "- **모바일**: 본문 `refreshToken` → 본문에 새 `refreshToken`\n\n"
        "이미 한 번 쓴 refresh token 을 다시 보내면 그 로그인 세션 전체가 폐기됩니다(`401`).",
        N,
    ),
    ("POST", "/api/v1/auth/logout"): (
        "v1 로그아웃",
        "본문 `refreshToken` 또는 쿠키의 세션과 그 세션의 access token 을 모두 폐기하고 쿠키를 지웁니다. "
        "`logoutFromKakao: true` 는 아직 지원하지 않아 `400 INVALID_INPUT` 입니다.",
        V1_ACCESS,
    ),
    ("POST", "/api/v1/auth/dev/login"): (
        "v1 개발용 로그인 (AUTH_DEV_LOGIN=true 일 때만)",
        "카카오 없이 테스트 계정으로 로그인합니다. 같은 `deviceKey`(8~128자)는 같은 사용자입니다. "
        "성인 테스터 계정이라 demo 모드에서도 실제 AI 를 씁니다.\n\n"
        "응답은 웹 갱신과 같습니다: 본문 `accessToken`, refresh token 은 쿠키로만. 꺼져 있으면 `404`.",
        N,
    ),
    # --- /v1 auth ---
}

_BASE_SETUP = {"lightHeight": "mid", "stickHeight": "short", "distance": "near", "brightness": "dim"}

EXAMPLES: dict[tuple[str, str], dict] = {
    ("POST", "/guardian/children"): {"nickname": "하늘", "tester": False},
    ("PUT", "/guardian/children/{child_id}/permissions"): {"voice": True, "browseShared": True, "publishRequest": True},
    ("POST", "/guardian/circles"): {"name": "우리반 친구들"},
    ("POST", "/guardian/circles/join"): {"code": "A1B2C3D4"},
    ("POST", "/onboarding/messages"): {"text": "나는 초등학교 3학년이야", "inputMode": "text"},
    ("POST", "/onboarding/confirm"): {},
    ("POST", "/talks"): {"topicId": "snow"},
    ("POST", "/talks/{talk_id}/turns"): {"text": "눈으로 눈사람을 만들고 눈싸움도 할 수 있어", "inputMode": "text"},
    ("POST", "/children/me/categories"): {"name": "공룡"},
    ("POST", "/words/explain"): {"text": "수증기가 차가운 컵 겉면에 붙어서 물방울이 돼요"},
    ("POST", "/voice/synthesize"): {"text": "안녕! 오늘은 어떤 이야기를 해 볼까?"},
    ("POST", "/children/me/words"): {"word": "수증기", "meaning": "물이 눈에 안 보이는 기체가 된 것", "example": ""},
    ("POST", "/children/me/word-quizzes"): {"count": 3},
    ("POST", "/children/me/word-quizzes/{quiz_id}/answers"): {"index": 0, "chosen": 1},
    ("POST", "/guardian/children/{child_id}/word-tests"): {"count": 5},
    ("POST", "/children/me/books"): {"title": "나의 과학 이야기책", "storyIds": ["여기에_story_id"]},
    ("POST", "/children/me/shares"): {"kind": "story", "refId": "여기에_story_id", "visibility": "family"},
    ("POST", "/guardian/shares/{item_id}/decision"): {"approve": True},
    ("POST", "/guardian/adventures"): {
        "category": "science",
        "title": "그림자 놀이",
        "hook": "손으로 어떤 그림자를 만들 수 있을까?",
        "followUps": ["그림자를 어디에 쓸 수 있을까?", "그림자는 밤에도 생길까?", "그림자 나라에 갔다고 상상해 볼까?"],
        "visibility": "family",
    },
    ("POST", "/inquiry/interpret"): {
        "prediction": "shorter",
        "reason": "한낮에 해가 높이 있을 때 그림자가 작았어",
        "reasonSkipped": False,
        "inputOrigin": "example",
    },
    ("POST", "/inquiry/teach"): {
        "beliefId": "brightness_longer",
        "message": "빛의 밝기만 바꿨는데 그림자 길이가 똑같았어!",
        "cards": [{"base": _BASE_SETUP, "compare": {**_BASE_SETUP, "brightness": "bright"}}],
        "attempt": 1,
        "inputOrigin": "example",
    },
    ("POST", "/path/teach"): {
        "text": "웅덩이가 있으면 오른쪽으로 돌아, 아니면 앞으로 가",
        "program": [{"op": "move", "count": 2}],
        "mapId": "puddle_1",
        "attempt": 1,
        "inputOrigin": "example",
        "pendingClarify": None,
    },
    ("POST", "/path/react"): {
        "text": "쭉 가",
        "program": [{"op": "move", "until": "blocked"}],
        "mapId": "puddle_1",
        "attempt": 2,
        "inputOrigin": "example",
        "result": {
            "outcome": "arrived",
            "moves": 4,
            "stopStepLabel": None,
            "previousOutcome": "splashed",
            "changedSinceLast": True,
        },
        "challengeCandidates": [{"id": "puddle_2", "summary": "웅덩이가 두 개 있는 지도"}],
    },
    ("POST", "/inquiry/challenge"): {
        "beliefId": "brightness_longer",
        "convinced": True,
        "experiments": [{"base": _BASE_SETUP, "compare": {**_BASE_SETUP, "lightHeight": "high"}}],
        "finalText": "빛이 높으면 그림자가 짧아져",
        "finalReason": "빛의 높이만 바꾼 실험에서 봤어",
        "inputOrigin": "example",
    },
    # --- v1 auth ---
    ("POST", "/api/v1/auth/kakao/mobile"): {
        "platform": "ANDROID",
        "kakaoAccessToken": "카카오_SDK_로그인으로_받은_access_token",
        "device": {"installationId": "01K0EXAMPLE", "appVersion": "1.0.0"},
    },
    ("POST", "/api/v1/auth/token/refresh"): {"refreshToken": "jrt_… (웹은 비워 두면 쿠키를 씀)"},
    ("POST", "/api/v1/auth/logout"): {"refreshToken": "jrt_… (웹은 생략)", "logoutFromKakao": False},
    ("POST", "/api/v1/auth/dev/login"): {"deviceKey": "my-laptop-test-01", "nickname": "테스터"},
    # --- /v1 auth ---
}

AUTH_HELP = {
    G: "보호자 토큰. `Bearer gt_…` 형식으로 넣으세요 (`POST /families` 응답의 guardianToken).",
    C: "아이 토큰. `Bearer ct_…` 형식으로 넣으세요 (`POST /guardian/children/{child_id}/devices` 응답의 childToken).",
    V: "보호자 토큰(`Bearer gt_…`) 또는 아이 토큰(`Bearer ct_…`).",
}

# --- v1 auth ---
AUTH_HELP[V1_ACCESS] = "JJCP access token. `Bearer jat_…` 형식으로 넣으세요 (로그인·토큰 갱신 응답의 accessToken)."
# --- /v1 auth ---


# --- v1 conversation ---
_V1C_TOKEN = "JJCP access token `jat_…`"
AUTH_HELP[_V1C_TOKEN] = "JJCP access token. `Bearer jat_…` 형식으로 넣으세요 (로그인·토큰 갱신 응답의 accessToken)."
_V1C_ERRORS = (
    "오류는 `{error: {code, message, details, requestId}}` 형식입니다. "
    "다른 사람의 세션은 `404 SESSION_NOT_FOUND`, 끝났거나 취소된 세션은 `409 SESSION_CLOSED`."
)
TAGS.update(
    {
        "v1-first-greeting": (
            "v1-2. 티키와 첫인사",
            "티키가 `자기소개해볼까?`로 시작해 별명·학년/나이·좋아하는 것·그 까닭·키우고 싶은 힘을 한 번에 하나씩 묻습니다. "
            "모두 채우면 `READY_TO_FINISH`, 완료하면 프로필이 저장되고 `needsFirstGreeting` 이 false 가 됩니다.",
        ),
        "v1-conversations": (
            "v1-3. 티키와 이야기",
            "주제로 대화하며 학습 차원(EXPERIENCE·IDEA·REASON·ALTERNATIVE·REFLECTION)을 모읍니다. "
            "모든 차원 + 최소 응답 수 + 실제 대화 시간이 차면 `READY_TO_FINISH`, 끝내면 정리본이 책장에 저장됩니다.",
        ),
        "v1-topics": ("v1-4. 주제", "기본 주제 은행과 내가 만든 주제. 직접 입력한 주제는 안전 검사 뒤 저장합니다."),
        "v1-home": ("v1-5. 홈·내 정보", "홈 화면 조합(추천·이어하기·이번 주 활동)과 로그인 사용자 정보."),
        "v1-stories": ("v1-6. 나의 책장", "완성한 이야기 목록·상세와 아끼는 기록 표시."),
    }
)
OPERATIONS.update(
    {
        ("POST", "/api/v1/first-greeting/sessions"): (
            "첫인사 시작 또는 이어하기",
            "진행 중(`ACTIVE`·`READY_TO_FINISH`) 세션이 있으면 그 세션을 돌려주고(`resumed: true`), 없으면 새로 만듭니다. "
            "첫 메시지는 `자기소개해볼까?`. `Idempotency-Key` 를 보내면 같은 응답을 다시 줍니다.\n\n" + _V1C_ERRORS,
            _V1C_TOKEN,
        ),
        ("GET", "/api/v1/first-greeting/sessions/{session_id}"): (
            "첫인사 복원",
            "메시지(최신 `limit` 개, 기본 50·최대 100, 오름차순), `currentInteraction`, `profileDraft`, `readiness` 를 줍니다. "
            "`nextCursor` 를 `messageCursor` 로 보내면 더 오래된 메시지를 줍니다.",
            _V1C_TOKEN,
        ),
        ("POST", "/api/v1/first-greeting/sessions/{session_id}/messages"): (
            "첫인사 답변 보내기",
            "아이 답에서 항목을 뽑고 부족한 항목 하나만 다음 질문으로 묻습니다. 같은 `clientMessageId` 는 저장하지 않고 처음 응답을 다시 줍니다.\n\n"
            "- `endIntentDetected: true` + 준비 완료면 같은 요청에서 완료하고 `completion` 을 함께 줍니다\n"
            "- 애매한 종료 표현이면 `SINGLE_CHOICE`(END·CONTINUE)로 확인합니다\n"
            "- 안전하지 않은 입력은 `422 UNSAFE_CONTENT`(원문 저장 안 함), 지난 질문 id 는 `409 QUESTION_MISMATCH`",
            _V1C_TOKEN,
        ),
        ("POST", "/api/v1/first-greeting/sessions/{session_id}/complete"): (
            "첫인사 완료",
            "프로필을 확정해 저장합니다. 부족하면 `409 FIRST_GREETING_NOT_READY` + `details.missing`. "
            "이미 완료된 세션은 처음 결과를 그대로 줍니다.",
            _V1C_TOKEN,
        ),
        ("GET", "/api/v1/conversations"): (
            "이야기 대화 목록",
            "`status=ACTIVE,READY_TO_FINISH` 처럼 쉼표로 거릅니다. 최근 수정순, `cursor`·`limit`(기본 20·최대 50)·`nextCursor`.",
            _V1C_TOKEN,
        ),
        ("POST", "/api/v1/conversations"): (
            "이야기 대화 시작",
            "`topicId`(`topic_ice_cup` 같은 은행 주제 또는 `topic_user_…`)로 시작합니다. 첫 질문은 경험을 묻는 `SINGLE_CHOICE` 입니다. "
            "없는 주제는 `404 TOPIC_NOT_FOUND`.",
            _V1C_TOKEN,
        ),
        ("GET", "/api/v1/conversations/{conversation_id}"): (
            "이야기 대화 복원",
            "메시지(선택지 스냅숏 포함), `currentInteraction`, `readiness`, 완료했다면 `storyId`. `messageCursor`·`limit` 으로 이전 메시지를 봅니다.",
            _V1C_TOKEN,
        ),
        ("POST", "/api/v1/conversations/{conversation_id}/messages"): (
            "이야기 답변 보내기",
            "`input.type` 이 `TEXT` 면 `text`, `SINGLE_CHOICE` 면 `optionId`. `questionId` 가 지금 질문이 아니거나 선택지에 없는 `optionId` 면 "
            "`409 QUESTION_MISMATCH`. 문장이 아닌 짧은 답은 같은 질문을 다시 묻습니다. `READY_TO_FINISH` 뒤에도 계속 보낼 수 있습니다.\n\n"
            "종료 발화가 준비된 대화에서 오면 같은 요청에서 정리본을 저장하고 `completion` 을 줍니다.",
            _V1C_TOKEN,
        ),
        ("POST", "/api/v1/conversations/{conversation_id}/complete"): (
            "이야기 완료·정리본 저장",
            "준비가 안 됐으면 `409 CONVERSATION_NOT_READY` + `missingDimensions`·`remainingResponses`·`remainingSeconds`. "
            "정리본의 `thoughtJourney` 는 아이 말 인용으로 채우고, AI 정리본 원본은 따로 보관합니다.",
            _V1C_TOKEN,
        ),
        ("POST", "/api/v1/conversations/{conversation_id}/cancel"): (
            "이야기 대화 그만두기",
            "원문을 지우지 않고 `CANCELLED` 로 바꿉니다. 이미 완료된 대화는 `409 SESSION_CLOSED`.",
            _V1C_TOKEN,
        ),
        ("GET", "/api/v1/topics"): (
            "주제 목록",
            "`category`, `recommended=true`(오늘의 추천), `query`(제목·첫 질문 검색), `cursor`·`limit`.",
            _V1C_TOKEN,
        ),
        ("GET", "/api/v1/topics/{topic_id}"): ("주제 상세", "대표 질문(`hook`)과 이어 갈 질문들(`questions`).", _V1C_TOKEN),
        ("POST", "/api/v1/topics"): (
            "내 주제 만들기",
            "안전 검사를 통과하면 저장하고 `topic_user_…` id 를 줍니다. 안전하지 않으면 `422 UNSAFE_TOPIC`(저장 안 함).",
            _V1C_TOKEN,
        ),
        ("GET", "/api/v1/me"): (
            "내 정보",
            "`user{id, role, needsFirstGreeting}` 와 첫인사로 확정한 `profile`(없으면 null).",
            _V1C_TOKEN,
        ),
        ("GET", "/api/v1/home"): (
            "홈 화면",
            "추천 주제 3개(사람이 읽을 `reason` 포함), 이어하기(`resume`), 최근 7일(KST) 활동. `recentWords`·`communityStories` 는 아직 빈 배열입니다.",
            _V1C_TOKEN,
        ),
        ("GET", "/api/v1/stories"): (
            "나의 책장",
            "`query`, `category`, `favorite`, `from`·`to`(KST 날짜), `cursor`·`limit`. 최근 수정순.",
            _V1C_TOKEN,
        ),
        ("GET", "/api/v1/stories/{story_id}"): (
            "이야기 한 편",
            "제목·요약·본문·생각 과정(`thoughtJourney`)·원본 대화 id. 다른 사람 이야기는 `404 STORY_NOT_FOUND`.",
            _V1C_TOKEN,
        ),
        ("PUT", "/api/v1/stories/{story_id}/favorite"): ("아끼는 기록 등록", "여러 번 불러도 결과가 같습니다.", _V1C_TOKEN),
        ("DELETE", "/api/v1/stories/{story_id}/favorite"): ("아끼는 기록 해제", "여러 번 불러도 결과가 같습니다.", _V1C_TOKEN),
    }
)
EXAMPLES.update(
    {
        ("POST", "/api/v1/first-greeting/sessions/{session_id}/messages"): {
            "clientMessageId": "device-uuid-7",
            "input": {"type": "TEXT", "text": "나는 별이라고 불러 줘. 2학년이고 공룡을 좋아해."},
        },
        ("POST", "/api/v1/first-greeting/sessions/{session_id}/complete"): {"trigger": "BUTTON"},
        ("POST", "/api/v1/conversations"): {"topicId": "topic_ice_cup", "inputMode": "TEXT", "locale": "ko-KR"},
        ("POST", "/api/v1/conversations/{conversation_id}/messages"): {
            "clientMessageId": "device-uuid-13",
            "questionId": "여기에_currentInteraction.questionId",
            "input": {"type": "SINGLE_CHOICE", "optionId": "SEEN"},
        },
        ("POST", "/api/v1/conversations/{conversation_id}/complete"): {"trigger": "BUTTON"},
        ("POST", "/api/v1/topics"): {"title": "무지개는 왜 여러 색으로 보일까?", "category": "SCIENCE"},
    }
)
# --- end v1 conversation ---
# --- v1 social ---
_V1S_TOKEN = "JJCP access token `jat_…`"
AUTH_HELP[_V1S_TOKEN] = "JJCP access token. `Bearer jat_…` 형식으로 넣으세요 (로그인·토큰 갱신 응답의 accessToken)."
_V1S_ADMIN = "운영자 JJCP access token `jat_…`"
AUTH_HELP[_V1S_ADMIN] = "운영자 JJCP access token. 카카오 회원번호가 `ADMIN_KAKAO_IDS` 허용 목록에 있어야 합니다(아니면 403 FORBIDDEN)."
TAGS.update(
    {
        "v1-sharing": (
            "v1-7. 이야기 공유·보호자 승인",
            "아이가 공유를 요청하면 보호자가 내용을 읽고 **본문 버전**을 확인해 승인합니다. "
            "`DRAFT → PENDING_GUARDIAN → APPROVED → PUBLISHED`, 반려는 `REJECTED`, 승인 전 취소는 `CANCELLED`, 승인 뒤 중단은 `REVOKED`. "
            "승인 뒤 아이가 본문을 고치면 공개본은 그대로 둔 채 공개를 멈추고(`PAUSED`) 다시 승인 대기로 돌아갑니다.",
        ),
        "v1-community": (
            "v1-8. 친구들의 이야기",
            "보호자가 승인한 공개본만 보입니다. 작성자의 실제 이름·학교·정확한 나이는 어떤 응답에도 없습니다(별명과 넓은 나이대만). "
            "추천은 한 사람이 한 이야기에 한 번. 신고가 기준을 넘거나 개인정보 신고가 들어오면 자동으로 숨기고 운영자 검토로 보냅니다.",
        ),
        "v1-reports": (
            "v1-9. 나의 발자국·성장 리포트·보호자 상담",
            "대화에서 **관찰된 횟수**만 보여 줍니다. 점수·등급·발달 진단이 아닙니다. "
            "서술형 요약과 월간 상담은 OpenAI 로 만들고, 막히거나 실패하면 규칙 기반 문장으로 대신합니다.",
        ),
        "v1-admin": (
            "v1-10. 안전과 운영",
            "보호자에게는 사건 종류와 안내만 드리고 아이 원문은 주지 않습니다. 운영자 API 는 허용 목록 계정만 부를 수 있습니다.",
        ),
    }
)
OPERATIONS.update(
    {
        ("POST", "/api/v1/stories/{story_id}/share-requests"): (
            "공유 요청 보내기 (아이)",
            "내 이야기를 보호자에게 확인 요청합니다. `audience` 는 `PEERS`(또래)·`FAMILY`(가족)·`INVITED`(초대한 사람). "
            "`hideProfile: true` 면 별명도 감추고 `친구` 로만 보입니다.\n\n"
            "이미 진행 중인 요청이 있으면 `409 SHARE_ALREADY_REQUESTED`, 민감한 표현이 있으면 `422 UNSAFE_CONTENT`. "
            "`Idempotency-Key` 를 보내면 같은 응답을 다시 줍니다.",
            _V1S_TOKEN,
        ),
        ("GET", "/api/v1/share-requests/{request_id}"): (
            "공유 요청·공개 상태 보기",
            "요청 상태와 (공개됐다면) 공개본을 함께 줍니다. 다른 계정의 요청은 `404 SHARE_REQUEST_NOT_FOUND`.",
            _V1S_TOKEN,
        ),
        ("DELETE", "/api/v1/share-requests/{request_id}"): (
            "공유 요청 취소 (승인 전만)",
            "`PENDING_GUARDIAN` 일 때만 됩니다. 승인 뒤에는 `409 SHARE_NOT_CANCELLABLE` — 보호자의 공개 중단(revoke)을 쓰세요.",
            _V1S_TOKEN,
        ),
        ("GET", "/api/v1/guardian/share-requests"): (
            "승인 대기 목록 (보호자)",
            "기본은 `PENDING_GUARDIAN`. `status=APPROVED,PUBLISHED` 처럼 쉼표로 여러 개, `status=ALL` 이면 전부. `cursor`·`limit`.",
            _V1S_TOKEN,
        ),
        ("POST", "/api/v1/guardian/share-requests/{request_id}/approve"): (
            "공유 승인 (보호자)",
            "읽은 본문의 `confirmedBodyVersion` 을 함께 보냅니다. 지금 본문 버전과 다르면 `409 SHARE_VERSION_MISMATCH` "
            "(`details` 에 지금 버전을 담습니다). 승인하면 그 버전의 **스냅숏**이 공개본이 됩니다.",
            _V1S_TOKEN,
        ),
        ("POST", "/api/v1/guardian/share-requests/{request_id}/reject"): (
            "공유 반려 (보호자)",
            "`reason` 을 아이에게 전달합니다. 승인 대기 상태가 아니면 `409 SHARE_NOT_PENDING`.",
            _V1S_TOKEN,
        ),
        ("POST", "/api/v1/guardian/share-requests/{request_id}/revoke"): (
            "공개 중단 (보호자)",
            "이미 공개된 이야기를 내립니다. 공개 전이면 `409 SHARE_NOT_PUBLISHED`.",
            _V1S_TOKEN,
        ),
        ("GET", "/api/v1/community/stories"): (
            "친구들의 이야기 목록",
            "승인·공개된 이야기만 최근 공개순으로 줍니다. `category`, `recommendation`(`SIMILAR_AGE`·`SAME_CATEGORY`·`POPULAR`·`NEW`), `cursor`·`limit`.\n\n"
            "응답의 `author` 는 별명과 넓은 나이대뿐이고, `recommendationReason` 에 이 이야기가 보이는 까닭이 한 문장으로 들어갑니다.",
            _V1S_TOKEN,
        ),
        ("GET", "/api/v1/community/stories/{public_story_id}"): (
            "친구 이야기 한 편",
            "본문과 생각 과정까지 줍니다. 숨겨졌거나 없는 이야기는 똑같이 `404 PUBLIC_STORY_NOT_FOUND`.",
            _V1S_TOKEN,
        ),
        ("PUT", "/api/v1/community/stories/{public_story_id}/recommendation"): (
            "따뜻한 추천 남기기",
            "한 사람은 한 이야기에 한 번만 남길 수 있습니다. 여러 번 불러도 수가 올라가지 않습니다.",
            _V1S_TOKEN,
        ),
        ("DELETE", "/api/v1/community/stories/{public_story_id}/recommendation"): (
            "추천 취소",
            "남긴 적이 없어도 오류가 아닙니다(여러 번 불러도 결과가 같습니다).",
            _V1S_TOKEN,
        ),
        ("POST", "/api/v1/community/stories/{public_story_id}/reports"): (
            "불편한 내용 신고",
            "`reason` 은 `UNCOMFORTABLE_CONTENT`·`SCARY`·`PERSONAL_INFO`·`COPIED`·`MEAN_WORDS`·`OTHER`. "
            "신고가 기준 수에 닿거나 개인정보 신고가 들어오면 공개본을 바로 숨기고 운영자 검토 대기로 보냅니다(`storyStatus: HIDDEN`).",
            _V1S_TOKEN,
        ),
        ("GET", "/api/v1/reports/progress"): (
            "나의 발자국 (기간별 활동)",
            "`period` 는 `7d`·`30d`·`90d`(기본 30d), `profileId` 는 내 프로필만. 활동 일수·완성한 이야기·관찰된 표현 횟수와 날짜별 `timeline`, 분야별 `categoryBreakdown` 을 줍니다.\n\n"
            "**점수도 진단도 아닙니다.** `notice` 문장을 화면에 그대로 보여 주세요.",
            _V1S_TOKEN,
        ),
        ("POST", "/api/v1/reports/summaries"): (
            "기간 요약 만들기",
            "`from`·`to`(KST 날짜, 비우면 최근 7일)의 서술형 요약을 만듭니다. 같은 기간이고 원본 기록이 그대로면 새로 만들지 않고 기존 결과를 줍니다(`reused: true`). "
            "원본 이야기가 늘거나 바뀌면 이전 요약은 `STALE` 이 됩니다.\n\n"
            "`source` 가 `ai` 면 OpenAI 문장, `fallback` 이면 규칙 기반 문장입니다.",
            _V1S_TOKEN,
        ),
        ("GET", "/api/v1/reports/summaries/{summary_id}"): (
            "요약 한 건 보기",
            "볼 때마다 원본 기록과 견줘 `status` 를 갱신합니다(달라졌으면 `STALE`). 다른 계정 요약은 `404 SUMMARY_NOT_FOUND`.",
            _V1S_TOKEN,
        ),
        ("GET", "/api/v1/guardian/consultations/eligibility"): (
            "월간 상담 가능 여부",
            "`period`(YYYY-MM, 비우면 지난달) 기준으로 이용 기간·자료 충분성·이미 만들었는지를 알려 줍니다. `reason` 에 까닭이 한 문장으로 들어갑니다.",
            _V1S_TOKEN,
        ),
        ("POST", "/api/v1/guardian/consultations"): (
            "월간 상담 만들기",
            "한 달에 한 번입니다. 조건이 안 되면 `409 CONSULTATION_NOT_ELIGIBLE`(`details.daysRemaining`·`completedStories`). "
            "이미 만든 달이면 그 상담을 그대로 돌려줍니다.\n\n"
            "관찰된 행동·대표 사례·함께 해 볼 질문과 **근거가 된 이야기 id**(`evidenceStoryIds`)를 줍니다. 진단 표현은 쓰지 않습니다.",
            _V1S_TOKEN,
        ),
        ("GET", "/api/v1/guardian/consultations"): ("월간 상담 목록", "최근순. `cursor`·`limit`.", _V1S_TOKEN),
        ("GET", "/api/v1/guardian/consultations/{consultation_id}"): (
            "월간 상담 한 건",
            "상담 내용과 근거 이야기 id, 그동안 남긴 후속 질문·답을 함께 줍니다.",
            _V1S_TOKEN,
        ),
        ("POST", "/api/v1/guardian/consultations/{consultation_id}/questions"): (
            "상담 후속 질문",
            "상담 기록에 있는 관찰만 근거로 답합니다. AI 를 쓸 수 없으면 정해진 안내 문장으로 답합니다(`source: fallback`).",
            _V1S_TOKEN,
        ),
        ("GET", "/api/v1/guardian/safety-events"): (
            "안전 이벤트 (보호자)",
            "감지된 **종류와 안내 문구만** 드립니다. 아이가 쓴 문장은 담지 않습니다. `needsAttention: true` 는 오늘 안에 살펴봐 주시면 좋은 건입니다.",
            _V1S_TOKEN,
        ),
        ("GET", "/api/v1/admin/community/reports"): (
            "신고 검토 목록 (운영자)",
            "`status` 는 `OPEN`(기본)·`RESOLVED`·`ALL`. 허용 목록에 없는 계정은 `403 FORBIDDEN`.",
            _V1S_ADMIN,
        ),
        ("POST", "/api/v1/admin/community/reports/{report_id}/resolve"): (
            "신고 처리 (운영자)",
            "`KEEP` 은 다시 공개, `HIDE` 는 숨김, `DELETE` 는 공개본 내용을 지웁니다. **아이의 원본 이야기는 어떤 경우에도 지우지 않습니다.**",
            _V1S_ADMIN,
        ),
        ("GET", "/api/v1/admin/safety-events"): (
            "고위험 이벤트 검토 (운영자)",
            "`escalatedOnly=false` 를 보내면 전체를 봅니다. 여기에도 아이 원문은 없습니다.",
            _V1S_ADMIN,
        ),
    }
)
EXAMPLES.update(
    {
        ("POST", "/api/v1/stories/{story_id}/share-requests"): {"audience": "PEERS", "hideProfile": True},
        ("POST", "/api/v1/guardian/share-requests/{request_id}/approve"): {
            "confirmedBodyVersion": 1,
            "confirmedRedactions": True,
        },
        ("POST", "/api/v1/guardian/share-requests/{request_id}/reject"): {"reason": "조금 더 다듬은 뒤에 보여 주자."},
        ("POST", "/api/v1/guardian/share-requests/{request_id}/revoke"): {"reason": "당분간 비공개로 둘게요."},
        ("POST", "/api/v1/community/stories/{public_story_id}/reports"): {
            "reason": "UNCOMFORTABLE_CONTENT",
            "detail": "무서운 표현이 있어요.",
        },
        ("POST", "/api/v1/reports/summaries"): {"from": "2026-09-11", "to": "2026-09-17"},
        ("POST", "/api/v1/guardian/consultations"): {"period": "2026-08"},
        ("POST", "/api/v1/guardian/consultations/{consultation_id}/questions"): {
            "question": "아이가 이유를 말할 때 어떻게 도와주면 좋을까요?"
        },
    }
)
# --- end v1 social ---


# --- v1 library ---
_V1L_TOKEN = "JJCP access token `jat_…`"
AUTH_HELP[_V1L_TOKEN] = "JJCP access token. `Bearer jat_…` 형식으로 넣으세요 (로그인·토큰 갱신 응답의 accessToken)."
_V1L_ERRORS = (
    "오류는 `{error: {code, message, details, requestId}}` 형식입니다. "
    "다른 아이의 기록은 `404`(있다는 사실도 알리지 않습니다), 먼저 고쳐진 기록은 `409 VERSION_CONFLICT`."
)
TAGS.update(
    {
        "v1-wordbook": (
            "v1-7. 단어장·단어 퀴즈",
            "대화에서 만난 낱말을 담고(뜻풀이는 티키가), 복습 퀴즈로 다시 만납니다. "
            "점수는 만들지 않습니다 — 맞히면 낱말 상태(NEW→PRACTICING→FAMILIAR)가 오르고 다음 복습이 멀어집니다.",
        ),
        "v1-books": (
            "v1-8. 이야기책",
            "완성한 이야기를 골라 한 권으로 묶습니다. 책을 지워도 이야기는 책장에 그대로 남습니다.",
        ),
    }
)
OPERATIONS.update(
    {
        ("PATCH", "/api/v1/stories/{story_id}"): (
            "이야기 고쳐 쓰기",
            "아이가 제목·요약·본문·`thoughtJourney` 를 자기 말로 고칩니다. AI 정리본 원본은 따로 보관되어 바뀌지 않습니다.\n\n"
            "지금 보고 있는 판을 `If-Match: \"3\"` 헤더나 본문 `version` 으로 함께 보냅니다. 값이 다르면 `409 VERSION_CONFLICT` "
            "(`details.currentVersion`), 아예 없으면 `400 INVALID_INPUT`. 저장되면 `version` 이 1 올라갑니다.\n\n" + _V1L_ERRORS,
            _V1L_TOKEN,
        ),
        ("DELETE", "/api/v1/stories/{story_id}"): (
            "이야기 지우기",
            "이야기를 지웁니다(`204`). 담겨 있던 이야기책에서는 빠지지만 책과 다른 이야기는 남습니다. 공유된 글도 함께 내립니다.",
            _V1L_TOKEN,
        ),
        ("GET", "/api/v1/wordbook"): (
            "단어장",
            "`summary`(전체·상태별·복습할 때가 된 개수) + `items` + `nextCursor`. `status`(NEW·PRACTICING·FAMILIAR), "
            "`query`(낱말·뜻 검색), `cursor`·`limit`(기본 20·최대 50). 최근 바뀐 순.",
            _V1L_TOKEN,
        ),
        ("POST", "/api/v1/wordbook/entries"): (
            "낱말 담기",
            "대화 메시지(`messageId`)에 실제로 나온 낱말만 담을 수 있습니다(아니면 `400 INVALID_INPUT`). "
            "뜻풀이는 티키가 만들고, AI 를 못 쓰면 검수 사전이나 '내 말로 적어 보기' 문장으로 이어 갑니다(`source`).\n\n"
            "이미 담은 낱말이면 `200` 으로 그 낱말을 그대로 돌려줍니다. `Idempotency-Key` 를 보내면 같은 응답을 다시 줍니다.",
            _V1L_TOKEN,
        ),
        ("GET", "/api/v1/wordbook/entries/{entry_id}"): ("낱말 하나", "뜻·예문·내 문장·상태·다음 복습 시각.", _V1L_TOKEN),
        ("PATCH", "/api/v1/wordbook/entries/{entry_id}"): (
            "낱말 고치기",
            "`status`(NEW·PRACTICING·FAMILIAR)를 직접 바꾸거나 `mySentence`(내가 만든 문장)를 적습니다. "
            "상태를 바꾸면 다음 복습 시각도 함께 옮겨집니다.",
            _V1L_TOKEN,
        ),
        ("DELETE", "/api/v1/wordbook/entries/{entry_id}"): ("낱말 지우기", "단어장에서 지웁니다(`204`).", _V1L_TOKEN),
        ("POST", "/api/v1/word-quizzes"): (
            "단어 퀴즈 만들기",
            "내 단어장에 담긴 낱말로만 냅니다. 복습할 때가 된 낱말이 먼저 나옵니다. `count`(기본 5·최대 10), "
            "`mode`(`MEANING_TO_WORD`·`WORD_TO_MEANING`·`FILL_IN_BLANK`), `status`(그 상태의 낱말로만).\n\n"
            "담은 낱말이 없으면 `409 NO_WORDS_TO_QUIZ`. 정답 보기 id 는 응답에 들어 있지 않습니다.",
            _V1L_TOKEN,
        ),
        ("POST", "/api/v1/word-quizzes/{quiz_id}/answers"): (
            "퀴즈 한 문제 답하기",
            "문항 하나에 답합니다. 점수를 매기지 않고 낱말 상태와 다음 복습 시각만 바꿉니다(맞히면 한 칸 위, 틀리면 한 칸 아래). "
            "같은 문항에 다시 답하면 `409 ALREADY_ANSWERED`.",
            _V1L_TOKEN,
        ),
        ("GET", "/api/v1/books"): ("이야기책 목록", "`status`(DRAFT·COMPLETED), `cursor`·`limit`. 최근 바뀐 순.", _V1L_TOKEN),
        ("POST", "/api/v1/books"): (
            "이야기책 만들기",
            "`storyIds` 순서대로 담습니다. `generateIntroduction: true` 면 머리말을 티키가 쓰고, AI 를 못 쓰면 "
            "담긴 이야기 제목으로 만든 문장을 넣습니다(`introductionSource`). `Idempotency-Key` 를 지원합니다.",
            _V1L_TOKEN,
        ),
        ("GET", "/api/v1/books/{book_id}"): ("이야기책 보기", "책 정보와 담긴 이야기를 순서대로 줍니다.", _V1L_TOKEN),
        ("PATCH", "/api/v1/books/{book_id}"): (
            "이야기책 고치기",
            "`title`·`introduction`·`cover`·`storyIds`(지금 담긴 이야기들의 새 순서). 이야기를 더하고 빼는 것은 전용 엔드포인트로 합니다.\n\n"
            "`If-Match` 헤더나 본문 `version` 이 필요합니다(다르면 `409 VERSION_CONFLICT`). 완성한 책은 `409 BOOK_COMPLETED`.",
            _V1L_TOKEN,
        ),
        ("POST", "/api/v1/books/{book_id}/stories"): (
            "책에 이야기 담기",
            "`position` 을 주면 그 자리에, 없으면 맨 뒤에 담습니다. 이미 담긴 이야기는 `409 STORY_ALREADY_IN_BOOK`.",
            _V1L_TOKEN,
        ),
        ("DELETE", "/api/v1/books/{book_id}/stories/{story_id}"): (
            "책에서 이야기 빼기",
            "책에서만 빼냅니다. 이야기는 책장에 그대로 남습니다.",
            _V1L_TOKEN,
        ),
        ("POST", "/api/v1/books/{book_id}/complete"): (
            "이야기책 완성",
            "다 만들었다고 표시합니다. 빈 책은 `409 BOOK_EMPTY`. 이미 완성한 책을 다시 불러도 같은 결과를 줍니다.",
            _V1L_TOKEN,
        ),
        ("DELETE", "/api/v1/books/{book_id}"): (
            "이야기책 지우기",
            "책만 지웁니다(`204`). 담겨 있던 이야기는 책장에 그대로 남습니다.",
            _V1L_TOKEN,
        ),
    }
)
EXAMPLES.update(
    {
        ("PATCH", "/api/v1/stories/{story_id}"): {"title": "차가운 컵에 생긴 물방울", "version": 1},
        ("POST", "/api/v1/wordbook/entries"): {"word": "수증기", "messageId": "여기에_message_id"},
        ("PATCH", "/api/v1/wordbook/entries/{entry_id}"): {
            "status": "PRACTICING",
            "mySentence": "아침에 유리창에 수증기가 맺혔다.",
        },
        ("POST", "/api/v1/word-quizzes"): {"count": 3, "mode": "MEANING_TO_WORD"},
        ("POST", "/api/v1/word-quizzes/{quiz_id}/answers"): {"questionId": "여기에_question_id", "optionId": "A"},
        ("POST", "/api/v1/books"): {
            "title": "나의 과학 이야기책",
            "storyIds": ["여기에_story_id"],
            "generateIntroduction": True,
            "cover": {"theme": "밤하늘", "emoji": "🌙"},
        },
        ("PATCH", "/api/v1/books/{book_id}"): {"title": "내가 만든 과학책", "version": 1},
        ("POST", "/api/v1/books/{book_id}/stories"): {"storyId": "여기에_story_id", "position": 0},
    }
)
# --- end v1 library ---


# --- v1 activities ---
_V1A_TOKEN = "JJCP access token `jat_…`"
AUTH_HELP[_V1A_TOKEN] = "JJCP access token. `Bearer jat_…` 형식으로 넣으세요 (로그인·토큰 갱신 응답의 accessToken)."
_V1A_SCOPE = "아이 프로필 단위로 막혀 있습니다. 다른 프로필의 자료는 `404` 로만 답합니다."
TAGS.update(
    {
        "v1-activities": (
            "v1-7. 생각 모험 활동",
            "숲·실험실·마음극장 활동 목록·상세와 활동 세션(시작·복원·자동 저장·단계 이동·완료·취소). "
            "각 단계의 최소 입력·두 조건 관찰·선택 여부·보호자 확인은 서버가 다시 검사합니다. 완료하면 점수 없이 책장 기록이 하나 생깁니다.",
        ),
        "v1-topic-categories": (
            "v1-8. 주제 카테고리",
            "기본 카테고리(과학·수학·역사·생각놀이·생활)와 아이가 만든 카테고리. 기본 카테고리는 수정·삭제할 수 없습니다.",
        ),
        "v1-admin-topics": (
            "v1-9. 요일별 주제 운영 (운영자)",
            "요일·기간별 추천 편성. `ADMIN_KAKAO_IDS` 허용 목록의 계정만 쓸 수 있고, `/home` 추천 순서에만 영향을 줍니다.",
        ),
    }
)
OPERATIONS.update(
    {
        ("GET", "/api/v1/activities"): (
            "활동 목록·검색",
            f"`query`(제목·소개·태그), `track`(forest·lab·theater), `area`(영역 이름 일부), `cursor`·`limit`. {_V1A_SCOPE}",
            _V1A_TOKEN,
        ),
        ("GET", "/api/v1/activities/{activity_id}"): (
            "활동 소개·단계·시각 자료",
            "소개(`intro`), 새 단서(`clue`), 단계 이름(`steps`), 대표 질문(`questions`), 시각 자료(`visuals`)를 돌려줍니다. "
            "`minCharacters` 는 글쓰기 단계에서 공백을 뺀 최소 글자 수입니다.",
            _V1A_TOKEN,
        ),
        ("POST", "/api/v1/activity-sessions"): (
            "활동 시작",
            "`Idempotency-Key` 헤더를 넣으면 같은 키로 다시 불러도 처음 세션을 그대로 돌려줍니다. "
            "마음극장 활동은 `keyword` 로 마음 키워드를 함께 보냅니다(안전하지 않으면 `422 UNSAFE_CONTENT`).",
            _V1A_TOKEN,
        ),
        ("GET", "/api/v1/activity-sessions/{session_id}"): (
            "진행 중 초안 복원",
            f"단계·자동 저장 번호(`revision`)·초안(`draft`)·아직 못 채운 조건(`missing`)을 돌려줍니다. {_V1A_SCOPE}",
            _V1A_TOKEN,
        ),
        ("PATCH", "/api/v1/activity-sessions/{session_id}"): (
            "현재 단계 자동 저장",
            "`clientRevision` 이 서버의 `revision` 과 같을 때만 저장합니다(다르면 `409 ACTIVITY_REVISION_CONFLICT`). "
            "`event.type` 은 TEXT·HINT·TOPIC·KEYWORD·LAB_VALUE·OBSERVATION·APPROVE·SCENE·CHOICE·EMOTION·INQUIRY·RUN.",
            _V1A_TOKEN,
        ),
        ("POST", "/api/v1/activity-sessions/{session_id}/advance"): (
            "다음 단계로 (서버가 조건 재검사)",
            "최소 입력·두 조건 관찰·선택 여부·보호자 확인을 서버가 다시 봅니다. 못 채웠으면 "
            "`409 ACTIVITY_STEP_NOT_READY` 와 함께 `details.missing`(조건 코드)·`details.conditions`(안내 문장)를 돌려줍니다.",
            _V1A_TOKEN,
        ),
        ("POST", "/api/v1/activity-sessions/{session_id}/complete"): (
            "활동 완료 · 책장 기록 만들기",
            "모든 단계를 마쳤을 때만 완료됩니다. 점수는 만들지 않고 `story_records` 한 줄을 만들어 `/stories` 에 보이게 합니다. "
            "같은 세션을 다시 완료하면 처음 결과를 그대로 돌려줍니다.",
            _V1A_TOKEN,
        ),
        ("DELETE", "/api/v1/activity-sessions/{session_id}"): (
            "진행 중 활동 취소",
            "초안을 남기고 상태만 `CANCELLED` 로 바꿉니다. 이미 완료한 활동은 `409 SESSION_CLOSED`.",
            _V1A_TOKEN,
        ),
        ("GET", "/api/v1/topic-categories"): (
            "주제 카테고리 목록",
            "기본 카테고리(`kind: DEFAULT`, `editable: false`) 뒤에 내가 만든 카테고리(`kind: USER`)가 순서대로 붙습니다. "
            "기본 5개 + 사용자 20개가 최대라 한 번에 모두 주고 `nextCursor` 는 항상 null 입니다.",
            _V1A_TOKEN,
        ),
        ("POST", "/api/v1/topic-categories"): (
            "카테고리 추가",
            "이름은 20자까지, 한 프로필에 20개까지입니다. 기본 카테고리·내 카테고리와 이름이 겹치면 `409 CATEGORY_EXISTS`, "
            "민감한 이름은 `422 UNSAFE_CATEGORY`.",
            _V1A_TOKEN,
        ),
        ("PATCH", "/api/v1/topic-categories/{category_id}"): (
            "카테고리 이름·순서 수정",
            "기본 카테고리는 `403 CATEGORY_NOT_EDITABLE`. 다른 프로필의 카테고리는 `404 CATEGORY_NOT_FOUND`.",
            _V1A_TOKEN,
        ),
        ("DELETE", "/api/v1/topic-categories/{category_id}"): (
            "카테고리 삭제",
            "내가 만든 카테고리만 지울 수 있습니다. 기본 카테고리는 `403 CATEGORY_NOT_EDITABLE`.",
            _V1A_TOKEN,
        ),
        ("GET", "/api/v1/admin/topic-schedules"): (
            "요일·기간별 주제 편성 조회 (운영자)",
            "`weekday`(0=월 … 6=일), `active`(오늘 적용 여부), `cursor`·`limit`. 허용 목록 밖 계정은 `403 FORBIDDEN`.",
            _V1A_TOKEN,
        ),
        ("POST", "/api/v1/admin/topic-schedules"): (
            "추천 주제 편성 생성 (운영자)",
            "`topicId` 는 주제 은행 주제만(`GET /topics` 의 `source: BANK`). `reason` 은 홈에 그대로 보이는 문장입니다. "
            "`weekday` 를 비우면 기간 내 매일 적용합니다.",
            _V1A_TOKEN,
        ),
        ("PATCH", "/api/v1/admin/topic-schedules/{schedule_id}"): (
            "편성 기간·순서·대상 수정 (운영자)",
            "`clearWeekday: true` 면 요일 조건을 없애 기간 내 매일로 바꿉니다.",
            _V1A_TOKEN,
        ),
        ("DELETE", "/api/v1/admin/topic-schedules/{schedule_id}"): (
            "편성 취소 (운영자)",
            "편성만 지웁니다. 아이가 그 주제를 고르는 것은 그대로 가능합니다.",
            _V1A_TOKEN,
        ),
    }
)
EXAMPLES.update(
    {
        ("POST", "/api/v1/activity-sessions"): {"activityId": "kindness", "keyword": "배려"},
        ("PATCH", "/api/v1/activity-sessions/{session_id}"): {
            "clientRevision": 4,
            "event": {"type": "OBSERVATION", "field": "LOW_LIGHT", "value": "빛이 낮아지니 그림자가 흐려졌어."},
        },
        ("POST", "/api/v1/topic-categories"): {"name": "공룡", "order": 0},
        ("PATCH", "/api/v1/topic-categories/{category_id}"): {"name": "공룡 이야기", "order": 1},
        ("POST", "/api/v1/admin/topic-schedules"): {
            "topicId": "topic_ice_cup",
            "startsOn": "2026-09-14",
            "endsOn": "2026-09-20",
            "weekday": 0,
            "order": 0,
            "reason": "이번 주 월요일은 물방울 이야기의 날이에요.",
        },
        ("PATCH", "/api/v1/admin/topic-schedules/{schedule_id}"): {"order": 1, "clearWeekday": True},
    }
)
# --- end v1 activities ---


# --- v1 accounts ---
_V1A_TOKEN = "JJCP access token `jat_…`"
AUTH_HELP[_V1A_TOKEN] = "JJCP access token. `Bearer jat_…` 형식으로 넣으세요 (로그인·토큰 갱신 응답의 accessToken)."
_V1A_ERRORS = (
    "오류는 `{error: {code, message, details, requestId}}` 형식입니다. "
    "내가 볼 수 없는 프로필은 `404 PROFILE_NOT_FOUND`, 권한이 없으면 `403 FORBIDDEN`, "
    "보호자 동의가 필요하면 `403 CONSENT_REQUIRED`(`details.documentIds` 에 필요한 문서)."
)
TAGS.update(
    {
        "v1-profiles": (
            "v1-7. 아이 프로필",
            "로그인 계정(보호자·가족)이 아이 프로필을 여러 개 갖습니다. 프로필마다 별명·학년대·관심사와 "
            "화면·보관 설정(ttsEnabled·guardianPreviewEnabled·theme·retentionDays)이 따로 있습니다. "
            "수정은 `If-Match` 로 버전을 확인합니다.",
        ),
        "v1-guardian-links": (
            "v1-8. 보호자 연결",
            "다른 보호자를 1회용 초대 토큰으로 같은 아이 프로필에 연결하고, 권한(VIEW_PROFILE·VIEW_STORIES·"
            "VIEW_REPORTS·REVIEW_SHARING·MANAGE_DATA)을 조절합니다. 연결 해제는 기록을 지우지 않습니다.",
        ),
        "v1-consents": (
            "v1-9. 고지·동의",
            "아이 개인정보·AI 대화·음성·공유 동의서(법률 검토 전 초안)와 동의 기록. 동의한 사람은 본문이 아니라 "
            "로그인 계정에서 정합니다. AI 대화 동의가 있으면 demo 모드에서도 실제 AI 로 대화합니다.",
        ),
    }
)
OPERATIONS.update(
    {
        ("GET", "/api/v1/profiles"): (
            "아이 프로필 목록",
            "내가 만든 프로필과 초대로 연결된 프로필을 모두 돌려줍니다. `cursor`·`limit`(기본 20, 최대 50).",
            _V1A_TOKEN,
        ),
        ("POST", "/api/v1/profiles"): (
            "아이 프로필 만들기",
            "아이를 한 명 더 등록합니다. 프로필마다 대화 기록이 따로 쌓입니다. "
            "`makeDefault: true` 면 이 계정의 기본 프로필이 됩니다. `Idempotency-Key` 를 지원합니다.",
            _V1A_TOKEN,
        ),
        ("GET", "/api/v1/profiles/{profile_id}"): (
            "아이 프로필 상세",
            f"응답 헤더 `ETag` 에 현재 버전이 실립니다. {_V1A_ERRORS}",
            _V1A_TOKEN,
        ),
        ("PATCH", "/api/v1/profiles/{profile_id}"): (
            "아이 프로필 수정",
            "보낸 키만 바꿉니다. `If-Match: \"3\"`(또는 본문 `version`)이 필요하고, 그 사이에 바뀌었으면 "
            "`409 VERSION_CONFLICT` 와 함께 `details.currentVersion` 을 돌려줍니다. 권한: MANAGE_DATA.",
            _V1A_TOKEN,
        ),
        ("GET", "/api/v1/profiles/{profile_id}/settings"): (
            "프로필 설정 보기",
            "`ttsEnabled`(읽어 주기), `guardianPreviewEnabled`(보호자 먼저보기), `theme`, `retentionDays`(보관 기간).",
            _V1A_TOKEN,
        ),
        ("PATCH", "/api/v1/profiles/{profile_id}/settings"): (
            "프로필 설정 바꾸기",
            "보관 기간을 바꾸면 `retentionNotice` 로 **무엇이 언제 지워지는지** 함께 알려 줍니다. "
            "`If-Match` 로 설정 버전을 확인합니다. 권한: MANAGE_DATA.",
            _V1A_TOKEN,
        ),
        ("POST", "/api/v1/guardian-links/invitations"): (
            "보호자 초대 만들기",
            "1회용 초대 토큰을 만듭니다(기본 30분). 토큰은 이 응답에서만 볼 수 있습니다. "
            "아이 개인정보 동의(`privacy_child`)가 없으면 `403 CONSENT_REQUIRED`.",
            _V1A_TOKEN,
        ),
        ("POST", "/api/v1/guardian-links/invitations/{token}/accept"): (
            "보호자 초대 받기",
            "초대 토큰으로 내 계정을 그 아이 프로필에 연결합니다. 이미 쓴 초대는 `409 INVITATION_ALREADY_USED`, "
            "시간이 지났으면 `409 INVITATION_EXPIRED`.",
            _V1A_TOKEN,
        ),
        ("GET", "/api/v1/guardian/children"): (
            "연결된 아이 목록",
            "이 계정이 볼 수 있는 아이와 각 연결의 권한을 돌려줍니다.",
            _V1A_TOKEN,
        ),
        ("GET", "/api/v1/guardian-links"): (
            "보호자 연결 목록",
            "내가 만든 프로필에 붙은 연결을 모두, 초대로 연결된 프로필은 내 연결만 보여 줍니다. `profileId` 로 좁힙니다.",
            _V1A_TOKEN,
        ),
        ("PATCH", "/api/v1/guardian-links/{link_id}"): (
            "보호자 권한 바꾸기",
            "초대로 연결된 보호자의 권한만 바꿀 수 있습니다. 프로필을 만든 계정(OWNER)의 권한은 `403 FORBIDDEN`.",
            _V1A_TOKEN,
        ),
        ("DELETE", "/api/v1/guardian-links/{link_id}"): (
            "보호자 연결 해제",
            "연결만 끊습니다. 아이 기록은 지우지 않습니다(`dataDeleted: false`). 여러 번 불러도 결과가 같습니다.",
            _V1A_TOKEN,
        ),
        ("GET", "/api/v1/legal-documents"): (
            "동의서·고지 문서",
            "아이 개인정보(`privacy_child`)·AI 대화(`ai_conversation`)·음성(`voice_retention`)·공유(`community_share`). "
            "**법률 검토 전 초안**이며 `version` 은 개정일입니다.",
            _V1A_TOKEN,
        ),
        ("GET", "/api/v1/consents"): (
            "동의 기록 목록",
            "`current: true` 면 지금 쓰는 문서 버전에 대한 살아 있는 동의입니다. 권한: VIEW_PROFILE.",
            _V1A_TOKEN,
        ),
        ("POST", "/api/v1/consents"): (
            "동의하기",
            "목적이 다른 동의를 `items` 로 한 번에 기록합니다. 동의한 사람은 본문의 `actor` 가 아니라 로그인 계정에서 정합니다. "
            "`agreed: false` 는 이미 한 동의를 철회하고, 같은 버전에 이미 동의했으면 그 기록을 그대로 돌려줍니다. "
            "권한: MANAGE_DATA.",
            _V1A_TOKEN,
        ),
        ("DELETE", "/api/v1/consents/{consent_id}"): (
            "동의 철회",
            "기록은 남기고 철회 시각만 적습니다. AI 대화 동의를 철회하면 바로 규칙 기반 대화로 돌아갑니다.",
            _V1A_TOKEN,
        ),
    }
)
EXAMPLES.update(
    {
        ("POST", "/api/v1/profiles"): {"nickname": "하늘", "gradeOrAgeBand": "3학년", "interests": ["공룡"], "makeDefault": False},
        ("PATCH", "/api/v1/profiles/{profile_id}"): {"nickname": "하늘이", "version": 1},
        ("PATCH", "/api/v1/profiles/{profile_id}/settings"): {"retentionDays": 30, "ttsEnabled": True, "version": 1},
        ("POST", "/api/v1/guardian-links/invitations"): {
            "profileId": "prf_…",
            "permissions": ["VIEW_PROFILE", "VIEW_STORIES"],
            "expiresInMinutes": 30,
        },
        ("PATCH", "/api/v1/guardian-links/{link_id}"): {"permissions": ["VIEW_PROFILE", "VIEW_REPORTS"]},
        ("POST", "/api/v1/consents"): {
            "profileId": "prf_…",
            "items": [{"documentId": "privacy_child", "version": "2026-09-18", "agreed": True}],
            "actor": "GUARDIAN",
        },
    }
)
# --- /v1 accounts ---

def install(app: FastAPI) -> None:
    """생성된 스키마에 한국어 이름·설명·예시를 덧붙이는 openapi 함수로 바꾼다."""

    def openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, description=DESCRIPTION, routes=app.routes)
        for path, methods in schema.get("paths", {}).items():
            for method, op in methods.items():
                key = (method.upper(), path)
                if key in OPERATIONS:
                    summary, description, token = OPERATIONS[key]
                    op["summary"] = summary
                    op["description"] = f"{description}\n\n**필요한 토큰:** {token}"
                    for param in op.get("parameters", []):
                        if param.get("name") == "authorization" and token in AUTH_HELP:
                            param["description"] = AUTH_HELP[token]
                op["tags"] = [TAGS.get(t, (t, ""))[0] for t in op.get("tags", [])]
                if key in EXAMPLES and "application/json" in op.get("requestBody", {}).get("content", {}):
                    op["requestBody"]["content"]["application/json"]["example"] = EXAMPLES[key]
        seen: dict[str, str] = {}
        for name, description in TAGS.values():
            if name not in seen or (description and not seen[name]):
                seen[name] = description
        schema["tags"] = [{"name": name, "description": description} for name, description in seen.items()]
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
