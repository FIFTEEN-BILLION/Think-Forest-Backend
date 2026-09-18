"""동의 관문 확인 — 테스터가 아닌 계정은 동의 전 fallback, 동의 뒤 ai 가 되는지."""

import sqlite3
import sys
import uuid

import httpx

B = "http://127.0.0.1:8000/api/v1"
DB = sys.argv[1]
c = httpx.Client(timeout=30)
d = c.post(B + "/auth/dev/login", json={"deviceKey": "consent-" + uuid.uuid4().hex, "nickname": "달"}).json()
H = {"Authorization": "Bearer " + d["accessToken"]}
prof = c.post(B + "/profiles", headers=H, json={"nickname": "달"}).json()["profile"]["id"]
# 테스터 표시를 떼서 '보통 아이 계정'으로 만든다
con = sqlite3.connect(DB)
con.execute("update children set is_tester=0")
con.commit()
con.close()


def one_turn(label):
    s = c.post(B + "/first-greeting/sessions", headers={**H, "Idempotency-Key": uuid.uuid4().hex}).json()
    r = c.post(
        B + f"/first-greeting/sessions/{s['sessionId']}/messages",
        headers=H,
        json={
            "clientMessageId": uuid.uuid4().hex,
            "input": {"type": "TEXT", "text": "나는 달이라고 불러 줘. 3학년이야"},
        },
    ).json()
    print(f"{label}: source={(r.get('assistantMessage') or {}).get('source')}")


one_turn("동의 전")
docs = c.get(B + "/legal-documents?locale=ko-KR", headers=H).json()["items"]
ai_doc = [x for x in docs if x["id"] == "ai_conversation"][0]
c.post(
    B + "/consents",
    headers=H,
    json={
        "profileId": prof,
        "items": [{"documentId": ai_doc["id"], "version": ai_doc["version"], "agreed": True}],
        "actor": "GUARDIAN",
    },
)
one_turn("동의 후")
