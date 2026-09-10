"""검수 대본 라이브러리 (TH-09) — 6종.

사람이 검수한 고정 대본. 마음극장 생성 품질의 기준선이자, 즉시 재생용.
각 대본은 5장면, 훈계 없이 열린 질문으로 마무리한다.
콘텐츠에 확인되지 않은 사실(연도·수치·기관명)을 넣지 않는다.
"""

from __future__ import annotations

from ..schemas.theater import LibraryScript, Scene

LIBRARY: tuple[LibraryScript, ...] = (
    LibraryScript(
        id="respect-elders",
        keyword="노인공경",
        title="버스에서 만난 할머니",
        scenes=[
            Scene(narration="지호가 버스에 올라 딱 하나 남은 자리에 앉는다.", line="아, 다행이다. 자리 있다!", emotion="안도"),
            Scene(narration="다음 정류장에서 무거운 장바구니를 든 할머니가 천천히 오른다.", line="(작게) …자리가 없네.", emotion="망설임"),
            Scene(narration="지호는 할머니의 흔들리는 손잡이를 본다.", line="할머니, 여기 앉으세요. 저는 금방 내려요.", emotion="용기"),
            Scene(narration="할머니가 환하게 웃으며 앉는다.", line="고마워라. 마음이 참 곱구나.", emotion="따뜻함"),
            Scene(narration="지호는 조금 피곤하지만 서서 창밖을 본다.", line="서 있어도 기분은 안 무겁네.", emotion="뿌듯함"),
        ],
        learn="지호는 왜 '기분은 안 무겁다'고 했을까? 너라면 그 자리에서 어떤 마음이었을까?",
        parent_note="양보를 '착한 행동'으로 가르치기보다, 아이가 스스로 느낀 감정(뿌듯함)을 짚어 주세요. "
        "억지로 시키면 남지 않지만, 스스로 고른 경험은 오래갑니다.",
    ),
    LibraryScript(
        id="politeness",
        keyword="공손함",
        title="놀이터의 부탁",
        scenes=[
            Scene(narration="세아가 그네를 타고 싶은데 다른 아이가 타고 있다.", line="야, 비켜. 내 차례야.", emotion="조급함"),
            Scene(narration="그 아이는 못 들은 척 계속 탄다.", line="(속으로) 왜 안 비켜 주지?", emotion="서운함"),
            Scene(narration="엄마가 다가와 조용히 말한다.", line="세아야, 부탁은 어떻게 하면 좋을까?", emotion="차분함"),
            Scene(narration="세아가 다시 다가가 말한다.", line="다 타면 나도 한 번 타도 될까? 기다릴게.", emotion="공손함"),
            Scene(narration="아이가 고개를 끄덕이고 곧 자리를 내준다.", line="응, 이제 네 차례야!", emotion="반가움"),
        ],
        learn="처음 말과 두 번째 말은 무엇이 달랐을까? 어떤 말이 더 잘 통했을까?",
        parent_note="'공손하게 말해'라는 지시 대신, 두 가지 말투가 만든 결과 차이를 아이가 비교하게 해 주세요. "
        "말투는 규칙이 아니라 도구라는 걸 경험으로 알게 됩니다.",
    ),
    LibraryScript(
        id="honesty",
        keyword="정직",
        title="깨진 화분",
        scenes=[
            Scene(narration="공놀이를 하던 준이 실수로 거실 화분을 깬다.", line="어… 어떻게 하지?", emotion="당황"),
            Scene(narration="준은 깨진 조각을 소파 뒤로 슬쩍 민다.", line="아무도 못 봤으니까… 괜찮겠지.", emotion="불안"),
            Scene(narration="저녁 내내 준은 아빠와 눈을 잘 못 맞춘다.", line="(속으로) 자꾸 생각나.", emotion="찜찜함"),
            Scene(narration="준이 아빠에게 다가간다.", line="아빠, 사실 아까 화분 제가 깼어요. 죄송해요.", emotion="용기"),
            Scene(narration="아빠가 준의 어깨를 토닥인다.", line="말해 줘서 고마워. 같이 치우자.", emotion="안심"),
        ],
        learn="숨겼을 때와 말했을 때, 준의 마음은 어떻게 달라졌을까? 너는 언제 마음이 가벼워졌던 것 같아?",
        parent_note="거짓말을 혼내는 장면을 넣지 않았습니다. 아이가 '숨김'이 만든 불편함과 '말함'이 준 안심을 "
        "대비해 느끼도록 하는 것이 핵심입니다. 고백했을 때 먼저 고마움을 표현해 주세요.",
    ),
    LibraryScript(
        id="caring",
        keyword="배려",
        title="혼자 앉은 친구",
        scenes=[
            Scene(narration="점심시간, 새로 온 친구 민서가 구석에 혼자 앉아 있다.", line="(민서) …다들 아는 사이인가 봐.", emotion="외로움"),
            Scene(narration="하율이 그 모습을 본다.", line="같이 앉자고 할까? 좀 쑥스러운데.", emotion="망설임"),
            Scene(narration="하율이 도시락을 들고 민서 옆으로 간다.", line="여기 앉아도 돼? 나 김밥 많은데 나눠 먹을래?", emotion="다정함"),
            Scene(narration="민서의 표정이 조금 풀린다.", line="응! 고마워. 나 계란말이 좋아해.", emotion="반가움"),
            Scene(narration="둘이 도시락을 가운데 두고 웃는다.", line="내일은 내가 반찬 가져올게.", emotion="설렘"),
        ],
        learn="하율이는 쑥스러움을 어떻게 넘겼을까? 네가 민서였다면 어떤 말이 제일 고마웠을까?",
        parent_note="배려를 '해야 하는 일'로 두면 부담이 됩니다. 하율이도 쑥스러웠다는 점을 보여 줘서, "
        "용기와 배려가 함께 간다는 걸 자연스럽게 전합니다.",
    ),
    LibraryScript(
        id="courage",
        keyword="용기",
        title="발표 차례",
        scenes=[
            Scene(narration="내일 조사한 것을 발표해야 하는 다은.", line="사람들 앞에서 말하면 목소리가 떨려.", emotion="두려움"),
            Scene(narration="다은은 거울 앞에서 작게 연습한다.", line="안녕하세요, 제가 조사한 건…", emotion="긴장"),
            Scene(narration="발표 순간, 다은의 손이 떨린다.", line="(숨을 크게 쉬고) 시작할게요.", emotion="결심"),
            Scene(narration="중간에 한 번 말이 막히지만 다은은 다시 이어 간다.", line="어… 다시 말하면, 여기가 제일 신기했어요.", emotion="집중"),
            Scene(narration="발표가 끝나고 친구들이 박수를 친다.", line="떨렸는데, 끝까지 했다!", emotion="뿌듯함"),
        ],
        learn="다은은 무섭지 않아서 용기를 낸 걸까, 무서운데도 낸 걸까? 그 차이는 뭘까?",
        parent_note="용기를 '두려움이 없는 상태'로 오해하기 쉽습니다. 다은이 떨면서도 이어 간 장면을 통해 "
        "'무서워도 한다'가 용기임을 짚어 주세요. 결과보다 끝까지 한 과정을 칭찬해 주세요.",
    ),
    LibraryScript(
        id="promise",
        keyword="약속",
        title="토요일의 약속",
        scenes=[
            Scene(narration="지우가 친구 서준과 토요일에 도서관에서 만나기로 한다.", line="10시에 입구에서 봐. 꼭이야!", emotion="기대"),
            Scene(narration="토요일 아침, 새로 산 게임이 너무 하고 싶다.", line="한 판만… 하고 갈까?", emotion="유혹"),
            Scene(narration="시계를 보니 9시 40분. 지우는 게임기를 내려놓는다.", line="서준이 혼자 기다리면 속상하겠지.", emotion="고민"),
            Scene(narration="지우가 도서관 입구에 도착하자 서준이 손을 흔든다.", line="딱 맞게 왔네! 기다릴 뻔했잖아.", emotion="반가움"),
            Scene(narration="둘이 나란히 책을 고른다.", line="게임은 이따 같이 하자. 그게 더 재밌겠다.", emotion="만족"),
        ],
        learn="지우가 게임기를 내려놓게 한 생각은 무엇이었을까? 약속을 지키면 나한테는 뭐가 남을까?",
        parent_note="약속을 어겼을 때의 벌이 아니라, 지켰을 때 관계에 남는 신뢰를 보여 줍니다. "
        "아이가 유혹과 고민을 거쳐 스스로 정했다는 점을 인정해 주세요.",
    ),
)

BY_ID: dict[str, LibraryScript] = {s.id: s for s in LIBRARY}
