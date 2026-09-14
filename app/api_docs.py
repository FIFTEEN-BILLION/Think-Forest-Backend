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
| `/families`, `/missions/...`, `/inquiry/...`, 기존 체험 기능 | 토큰 없음 |

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
    "diagnostic": ("10. 기존 체험 기능", "초기 버전의 진단·채점·실험실·마음극장·리포트 API (Claude 경로)."),
    "rubric": ("10. 기존 체험 기능", ""),
    "lab": ("10. 기존 체험 기능", ""),
    "theater": ("10. 기존 체험 기능", ""),
    "report": ("10. 기존 체험 기능", ""),
    "tech": ("11. 서버 상태", "서버 동작 확인과 AI 호출·차단 로그."),
    "health": ("11. 서버 상태", ""),
}

G, C, V, N = "보호자 토큰 `gt_…`", "아이 토큰 `ct_…`", "보호자 또는 아이 토큰", "토큰 없음"

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
    ("POST", "/inquiry/challenge"): {
        "beliefId": "brightness_longer",
        "convinced": True,
        "experiments": [{"base": _BASE_SETUP, "compare": {**_BASE_SETUP, "lightHeight": "high"}}],
        "finalText": "빛이 높으면 그림자가 짧아져",
        "finalReason": "빛의 높이만 바꾼 실험에서 봤어",
        "inputOrigin": "example",
    },
}

AUTH_HELP = {
    G: "보호자 토큰. `Bearer gt_…` 형식으로 넣으세요 (`POST /families` 응답의 guardianToken).",
    C: "아이 토큰. `Bearer ct_…` 형식으로 넣으세요 (`POST /guardian/children/{child_id}/devices` 응답의 childToken).",
    V: "보호자 토큰(`Bearer gt_…`) 또는 아이 토큰(`Bearer ct_…`).",
}


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
