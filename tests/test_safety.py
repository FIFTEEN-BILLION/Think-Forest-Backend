"""안전 모듈 단위 테스트."""

from app.safety import blocklist, pii


def test_blocklist_catches_violent_keyword():
    assert blocklist.check("친구를 죽여버리는 이야기").blocked


def test_blocklist_passes_normal_keyword():
    assert not blocklist.check("친구와 사이좋게 나눠 쓰기").blocked


def test_find_blocked_returns_term_or_none():
    assert blocklist.find_blocked("친구를 죽이는 방법") == "죽이"
    assert blocklist.find_blocked("노인공경 이야기") is None


def test_blocklist_defeats_spacing_and_punctuation_bypass():
    assert blocklist.find_blocked("칼 로 찌르기")
    assert blocklist.find_blocked("죽.여.버려")
    assert blocklist.find_blocked("자   살 이야기")


def test_blocklist_defeats_char_repetition_bypass():
    assert blocklist.find_blocked("죽이이이는 법")
    assert blocklist.find_blocked("때려죽여여여")


def test_pii_masks_phone_and_name():
    result = pii.mask("제 이름은 김민준이고 전화번호는 010-1234-5678이에요")
    assert "김민준" not in result.text
    assert "010-1234-5678" not in result.text
    assert "이름" in result.categories
    assert "전화번호" in result.categories


def test_pii_masks_school():
    result = pii.mask("행복초등학교 2학년이에요")
    assert "행복초등학교" not in result.text
    assert "학교·기관명" in result.categories


def test_pii_leaves_clean_text_alone():
    result = pii.mask("오늘 그림자가 길어지는 걸 봤어요")
    assert result.categories == []
    assert result.text == "오늘 그림자가 길어지는 걸 봤어요"


def test_pii_masks_birthdate_forms():
    for text in ("2018년 3월 1일에 태어났어요", "생일은 2018-03-01이에요"):
        result = pii.mask(text)
        assert "생년월일" in result.categories
        assert "2018" not in result.text


def test_pii_masks_address():
    result = pii.mask("서울특별시 강남구 삼성동에 살아요")
    assert "집주소" in result.categories
    assert "삼성동" not in result.text


def test_pii_does_not_flag_ordinary_numbers():
    # 학년·나이·개수 같은 짧은 숫자는 개인정보가 아니다
    result = pii.mask("나는 8살이고 사탕을 3개 먹었어요")
    assert result.categories == []


def test_pii_does_not_flag_generic_words():
    result = pii.mask("학교에서 그림자 실험을 했어요")
    assert result.categories == []
