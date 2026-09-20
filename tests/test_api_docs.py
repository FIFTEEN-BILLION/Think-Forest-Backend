"""/docs 설명이 빠진 엔드포인트가 없는지 — 새 API 를 만들면 app/api_docs.py 에도 적어야 한다."""

from app.api_docs import EXAMPLES, OPERATIONS, TAGS
from app.main import app
from app.v1.models_accounts import DOCUMENT_BY_ID
from app.v1.schemas_accounts import ConsentCreateRequest
from app.v1.schemas_conversation import GreetingReadinessResponse


def test_every_operation_has_korean_docs_and_tag():
    app.openapi_schema = None
    schema = app.openapi()
    tag_names = {name for name, _ in TAGS.values()}
    missing = []
    for path, methods in schema["paths"].items():
        for method, op in methods.items():
            if (method.upper(), path) not in OPERATIONS:
                missing.append(f"{method.upper()} {path}")
            assert op["tags"] and all(t in tag_names for t in op["tags"]), (method, path, op["tags"])
            assert "필요한 토큰" in op.get("description", "")
    assert missing == []
    assert [t["name"] for t in schema["tags"]][0] == "1. 가족·아이·권한 (보호자)"


def test_docs_do_not_list_removed_operations_and_examples_attach():
    schema = app.openapi()
    live = {(m.upper(), p) for p, ms in schema["paths"].items() for m in ms}
    assert set(OPERATIONS) <= live
    assert set(EXAMPLES) <= live
    body = schema["paths"]["/talks/{talk_id}/turns"]["post"]["requestBody"]["content"]["application/json"]
    assert body["example"]["text"]


def test_onboarding_contract_and_examples_match_public_openapi():
    app.openapi_schema = None
    schema = app.openapi()
    guest = schema["paths"]["/api/v1/auth/guest"]["post"]
    assert "requestBody" not in guest
    assert guest["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/TokenResponse")
    legal = schema["paths"]["/api/v1/legal-documents"]["get"]
    assert not any(p["name"].lower() == "authorization" for p in legal.get("parameters", []))
    consents = schema["paths"]["/api/v1/consents"]
    parameter = next(p for p in consents["get"]["parameters"] if p["name"] == "currentOnly")
    assert parameter["schema"]["default"] is False
    example = consents["post"]["requestBody"]["content"]["application/json"]["example"]
    request = ConsentCreateRequest.model_validate(example)
    assert request.guardian_confirmed and {i.document_id for i in request.items} == {"privacy_child", "ai_conversation"}
    assert all(i.agreed and i.version == DOCUMENT_BY_ID[i.document_id].version for i in request.items)
    readiness = schema["paths"]["/api/v1/first-greeting/readiness"]["get"]["responses"]["200"]["content"]["application/json"]
    assert readiness["schema"]["$ref"].endswith("/GreetingReadinessResponse")
    for sample in readiness["examples"].values():
        parsed = GreetingReadinessResponse.model_validate(sample["value"])
        assert parsed.available == (parsed.reason is None)
