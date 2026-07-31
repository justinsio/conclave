from fastapi import APIRouter

from app.config import settings
from app.services.rules_loader import load_rules

router = APIRouter(prefix="/v1", tags=["rules"])

_rules: list[str] | None = None


def get_rules_text() -> list[str]:
    """Cached rules list. Loaded from RULES_FILE on first use."""
    global _rules
    if _rules is None:
        _rules = load_rules(settings.rules_file, settings.rules_text)
    return _rules


def reset_rules_cache() -> None:
    """Test/reload hook — drops the cached rules."""
    global _rules
    _rules = None


@router.get("/rules")
async def get_rules():
    return {
        "version": settings.rules_version,
        "published_at": settings.rules_published_at,
        "rules": get_rules_text(),
        "changelog": [
            {
                "version": settings.rules_version,
                "date": settings.rules_published_at[:10],
                "summary": "Initial ruleset",
            }
        ],
    }
