"""Optional image export: a rendering problem must not discard a valid result."""
from datetime import datetime
import os

from output import SOURCES
from planning import CHINA_TZ


def attach_image(result, kind, selected, path):
    try:
        from image_cards import render_card
        payload = dict(selected)
        payload.setdefault("sources", SOURCES)
        result["image"] = render_card(kind, payload, path, font_path=os.environ.get("TRAVEL_CARD_FONT"),
                                      generated_at=payload.get("generated_at") or datetime.now(CHINA_TZ).isoformat(timespec="seconds"))
    except Exception:
        # Do not echo a dependency or filesystem exception: it can contain private paths/data.
        result["image_error"] = {"code": "IMAGE_UNAVAILABLE", "message": "图片生成失败，天气/行程文字仍可使用；请检查 Pillow、中文字体及 PNG 输出路径。"}
