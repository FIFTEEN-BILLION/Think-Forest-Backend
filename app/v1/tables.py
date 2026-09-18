"""v1 테이블 등록 — create_all 전에 import 한다."""

from __future__ import annotations

from . import (  # noqa: F401
    models,
    models_accounts,
    models_activity,
    models_auth,
    models_conversation,
    models_library,
    models_ops,
    models_social,
)
