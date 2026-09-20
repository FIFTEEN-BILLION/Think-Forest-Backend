"""AI 프로필 변경 적용. 문장 의미는 해석하지 않고 자료형·길이·출처·개인정보만 검사한다."""

from copy import deepcopy

from ..safety import pii
from ..safety import topics as sensitive
from .schemas_conversation import FirstGreetingLLM

FIELD_LIMITS = {
    "nickname": (20, 1),
    "schoolOrGroup": (20, 1),
    "gradeOrAgeBand": (30, 1),
    "interests": (40, 5),
    "interestDetails": (120, 3),
    "growthGoal": (60, 1),
}
LIST_FIELDS = {"interests", "interestDetails"}
SCHOOL_KINDS = {"초등학교", "홈스쿨", "유치원", "기타"}


class InvalidGreetingOutput(ValueError):
    pass


def safe_output(text: str, *, school: bool = False) -> str:
    if not text.strip() or "●" in text or sensitive.detect(text):
        raise InvalidGreetingOutput("unsafe_or_empty_output")
    if not school and pii.mask(text, names=False, preserve_school_types=True).text != text:
        raise InvalidGreetingOutput("personal_information")
    return text.strip()


def apply_changes(
    draft: dict, deferred: list[str], out: FirstGreetingLLM, *, messages: dict[str, str], current_id: str | None
):
    updated, pending = deepcopy(draft), set(deferred)
    provenance: dict[str, str] = {}
    safe_output(out.message)
    if out.context_summary:
        safe_output(out.context_summary)
    if out.profile_summary:
        safe_output(out.profile_summary)
    for change in out.changes:
        # AI가 만든 인용문은 현재 세션의 실제 사용자 메시지에 존재해야 한다.
        if not change.evidence or not any(e.message_id == current_id for e in change.evidence):
            raise InvalidGreetingOutput("missing_current_evidence")
        for evidence in change.evidence:
            if evidence.message_id not in messages or evidence.quote not in messages[evidence.message_id]:
                raise InvalidGreetingOutput("invalid_evidence")
        field, operation = change.field, change.operation
        is_list = field in LIST_FIELDS
        if operation in {"DEFER", "RESUME", "CLEAR"}:
            if change.value is not None or change.values:
                raise InvalidGreetingOutput("unexpected_value")
            if operation == "DEFER":
                pending.add(field)
                continue
            pending.discard(field)
            if operation == "RESUME":
                continue
            updated[field] = [] if is_list else None
        else:
            if operation != "SET" and not is_list:
                raise InvalidGreetingOutput("invalid_scalar_operation")
            if (is_list and change.value is not None) or (not is_list and change.values):
                raise InvalidGreetingOutput("invalid_value_type")
            values = change.values if is_list else [change.value]
            if not values or any(not isinstance(v, str) or not v.strip() for v in values):
                raise InvalidGreetingOutput("empty_value")
            max_length, max_count = FIELD_LIMITS[field]
            values = list(dict.fromkeys(safe_output(v, school=field == "schoolOrGroup") for v in values))
            if any(len(v) > max_length for v in values):
                raise InvalidGreetingOutput("value_too_long")
            if field == "schoolOrGroup" and values[0] not in SCHOOL_KINDS:
                raise InvalidGreetingOutput("invalid_school_kind")
            if operation == "SET":
                updated[field] = values if is_list else values[0]
            elif operation == "ADD":
                updated[field] = list(dict.fromkeys([*updated[field], *values]))
            elif operation == "REMOVE":
                updated[field] = [v for v in updated[field] if v not in values]
            if is_list and len(updated[field]) > max_count:
                raise InvalidGreetingOutput("too_many_values")
            pending.discard(field)
        provenance[field] = current_id
    return updated, sorted(pending), provenance
