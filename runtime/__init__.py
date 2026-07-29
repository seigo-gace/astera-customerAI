from .bundled_snapshot import install_bundled_notion_fallback

install_bundled_notion_fallback()

from .service import CustomerAIService

__all__ = ["CustomerAIService"]
