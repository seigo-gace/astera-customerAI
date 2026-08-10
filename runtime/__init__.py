from .notion_v3 import install_v3_notion_adapter

install_v3_notion_adapter()

from .bundled_snapshot import install_bundled_notion_fallback

install_bundled_notion_fallback()

from .service import CustomerAIService

__all__ = ["CustomerAIService"]
