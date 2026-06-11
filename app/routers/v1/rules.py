from fastapi import APIRouter
from app.config import settings

router = APIRouter(prefix="/v1", tags=["rules"])


@router.get("/rules")
async def get_rules():
    return {
        "version": settings.rules_version,
        "published_at": settings.rules_published_at,
        "rules": settings.rules_text,
        "changelog": [
            {
                "version": settings.rules_version,
                "date": settings.rules_published_at[:10],
                "summary": "Initial ruleset",
            }
        ],
    }
