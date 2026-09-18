"""AI 경로 확인 — 가짜 OpenAI 서버를 붙여 놓고 응답의 source 가 ai 로 바뀌는지 본다."""
import httpx, uuid, time, json
B="http://127.0.0.1:8000/api/v1"; c=httpx.Client(timeout=30)
d=c.post(B+"/auth/dev/login", json={"deviceKey":"ai-"+uuid.uuid4().hex, "nickname":"별"}).json()
H={"Authorization":"Bearer "+d["accessToken"]}
s=c.post(B+"/first-greeting/sessions", headers={**H,"Idempotency-Key":uuid.uuid4().hex}).json()
sid=s["sessionId"]
m=c.post(B+f"/first-greeting/sessions/{sid}/messages", headers=H,
         json={"clientMessageId":uuid.uuid4().hex,"input":{"type":"TEXT","text":"나는 별이라고 불러 줘. 2학년이야"}}).json()
a=m.get("assistantMessage") or {}
print("첫인사 응답 source:", a.get("source"), "| 티키:", a.get("content","")[:60])
for t in ["공룡 좋아해","이빨이 커서 신기해","질문하는 힘을 키우고 싶어"]:
    c.post(B+f"/first-greeting/sessions/{sid}/messages", headers=H, json={"clientMessageId":uuid.uuid4().hex,"input":{"type":"TEXT","text":t}})
c.post(B+f"/first-greeting/sessions/{sid}/complete", headers={**H,"Idempotency-Key":uuid.uuid4().hex}, json={"trigger":"BUTTON"})
cv=c.post(B+"/conversations", headers={**H,"Idempotency-Key":uuid.uuid4().hex},
          json={"topicId":"topic_ice_cup","inputMode":"TEXT","locale":"ko-KR"}).json()
cid=cv["conversationId"]; inter=cv.get("nextInteraction") or {}
for t in ["놀이터에서 봤어","내 생각에는 공기 속 물이 붙은 것 같아","왜냐하면 차가운 컵에만 생기니까","만약 컵이 따뜻하면 안 생길 것 같아"]:
    time.sleep(2)
    body={"clientMessageId":uuid.uuid4().hex,"questionId":inter.get("questionId")}
    body["input"]={"type":"SINGLE_CHOICE","optionId":inter["options"][0]["id"]} if inter.get("type")=="SINGLE_CHOICE" and inter.get("options") else {"type":"TEXT","text":t}
    r=c.post(B+f"/conversations/{cid}/messages", headers=H, json=body).json()
    inter=r.get("nextInteraction") or inter
    am=r.get("assistantMessage") or {}
    print("이야기 응답 source:", am.get("source"), "| 티키:", am.get("content","")[:60])
panel=httpx.get("http://127.0.0.1:8000/tech/panel").json()
calls=[x for x in panel.get("calls",[]) if x.get("ok")]
print("실제 호출 기록:", len(calls), "건 |", json.dumps([x.get("purpose") for x in calls][:8], ensure_ascii=False))
