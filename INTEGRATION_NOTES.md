# develop 통합 기록 — 2026-09-18

- 대상: 백엔드 `feat/hyunsu-backend`에 `origin/develop`의 `810946f`를 fast-forward로 반영.
- 기준: 사용자 선택에 따라 겹치는 API·계정 모델·테이블 정의는 develop 구현을 사용.
- 유지: `DATABASE_URL` 미설정·빈 문자열·공백이면 `sqlite:///./data/thinkforest.db`. 상대 SQLite 경로는 백엔드 디렉터리 기준.
- 프론트 소스와 기존 `.env`, `data/thinkforest.db`는 이번 통합에서 변경하지 않음.
- 검증: 전체 테스트 310개, `ruff check .`, Git 공백 검사 통과. 테스트는 메모리 또는 임시 SQLite 사용.

## 기존 로컬 작업 백업

통합 전 변경 파일과 미추적 파일은 다음 Git stash에 함께 보관했다. 삭제하거나 pop하지 않았다.

- 메시지: `backup: local backend before develop merge 2026-09-18`
- 객체 ID: `9edec525239265057dae6de92cd6171e54cc0719`
- 생성 시 이름: `stash@{0}`. 이후 다른 stash를 만들면 번호가 바뀔 수 있으므로 객체 ID로 식별한다.

기존 작업을 검토할 때는 `git stash show --include-untracked --stat 9edec525239265057dae6de92cd6171e54cc0719`를 사용한다. 기존 API·마이그레이션 전체를 현재 브랜치에 그대로 적용하면 develop과 다시 충돌하므로 필요한 변경만 개별 이관한다.

## 기존 SQLite 데이터 이관 필요

기존 `data/thinkforest.db`를 읽기 전용으로 검사한 결과, 다음 9개 테이블에 develop 모델이 요구하는 컬럼이 부족하다.

| 테이블 | 추가로 요구하는 컬럼 |
| --- | --- |
| activity_sessions | cancelled_at, min_characters, profile_id, result, started_at, story_id, title, track |
| data_jobs | byte_size, download_token_hash, downloaded_at, format, include, payload, type, updated_at, user_id |
| guardian_invitations | accepted_user_id, inviter_user_id, permissions, profile_id, used_at |
| notifications | body, data, title, type |
| profile_settings | created_at, id |
| public_stories | audience, author_user_id, body_version, created_at, display_name, recommendation_count, report_count, share_request_id, status, story_id, thought_journey, updated_at |
| push_devices | app_version, created_at, installation_id, locale, push_token, updated_at |
| report_summaries | body, period_from, period_to, source, source_version, user_id |
| topic_schedules | category, created_at, created_by, sort_order, topic_title, updated_at, weekday |

이 검사는 컬럼 이름 비교이며 데이터 변환이나 제약조건 호환성을 검증한 마이그레이션은 아니다. `create_all()`은 기존 테이블의 컬럼·제약조건을 바꾸지 않는다. **기존 기본 DB로 API를 실행하기 전에는 데이터 백업과 별도 스키마 이관이 필요하다.** 이번 통합에서 DB를 초기화하거나 데이터를 삭제하지 않았다.

develop의 계정 모델과 API 응답이 기존 로컬 구현과 다르므로 프론트 API 계약도 별도 확인이 필요하다. 이번 요청 범위는 백엔드 통합이다.
