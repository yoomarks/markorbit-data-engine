from __future__ import annotations

# Backward-compatible module entrypoint retained for scripts and callers that
# adopted the temporary CLI adapter before dict-row support moved into the core.
from app.cn.replay_readiness import build_readiness, main

__all__ = ["build_readiness", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
