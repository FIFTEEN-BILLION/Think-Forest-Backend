# 키 없이 AI 경로 확인하기

실제 OpenAI 키가 없어도 "AI 경로가 끝까지 도는지"만 확인하는 도구다.
응답 품질은 확인할 수 없다 — 그건 실제 키로만 알 수 있다.

```bash
# 1) 가짜 OpenAI 서버 (Responses API 모양으로만 답한다)
uvicorn scripts.dev.fake_openai:app --port 8099

# 2) 백엔드를 가짜 서버로 붙여서 띄운다
OPENAI_API_KEY=sk-fake-local-stub OPENAI_BASE_URL=http://127.0.0.1:8099/v1 \
AUTH_DEV_LOGIN=true AUTH_COOKIE_SECURE=false \
CONVERSATION_MIN_SECONDS=5 CONVERSATION_MIN_RESPONSES=3 \
DATABASE_URL=sqlite:///./data/ai-check.db \
uvicorn app.main:app --port 8000

# 3) 확인
python scripts/dev/ai_path_check.py            # 첫인사·이야기 응답의 source 가 ai 인지
python scripts/dev/consent_gate_check.py ./data/ai-check.db   # 동의 전 fallback → 동의 뒤 ai
```

확인한 것(2026-09-18): 첫인사 `first_greeting.extract`, 이야기 `conversation.turn`,
`moderation` 이 실제 OpenAI SDK → HTTP → 구조화 응답 파싱까지 돌고 응답에 `source: "ai"` 가 붙는다.
테스터가 아닌 계정은 보호자 `ai_conversation` 동의 전에는 `fallback`, 동의 뒤에는 `ai` 다.
