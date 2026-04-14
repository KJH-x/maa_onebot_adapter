import json
import os
import logging
import textwrap
import math
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger("layout_engine")


class LayoutConfigManager:
    """布局配置管理器，支持热载。"""

    DEFAULT_CONFIG = {
        "canvas": {
            "width": 1200,
            "height": 600,
            "background": "#EEF2F7"
        },
        "theme": {
            "surface": "#FFFFFF",
            "surface_alt": "#F7F9FC",
            "text": "#111827",
            "muted": "#6B7280",
            "primary": "#4F46E5",
            "success": "#16A34A",
            "warning": "#D97706",
            "danger": "#DC2626",
            "divider": "#E5E7EB",
            "track": "#E5E7EB",
            "shadow": "#00000018"
        },
        "elements": [
            {
                "id": "main_status_card",
                "type": "panel",
                "rect": {"x": 42, "y": 32, "width": 744, "height": 404},
                "fill": "${theme.surface}",
                "radius": 28,
                "shadow": {"dx": 0, "dy": 10, "blur": 24, "color": "${theme.shadow}"}
            },
            {
                "id": "capture_header",
                "type": "text",
                "content": "Emulator Preview",
                "position": {"x": 72, "y": 58},
                "style": "section_title"
            },
            {
                "id": "capture_subtitle",
                "type": "text",
                "content": "运行状态与画面预览",
                "position": {"x": 72, "y": 90},
                "style": "section_subtitle"
            },
            {
                "id": "capture_panel",
                "type": "image_panel",
                "source": "capture",
                "rect": {"x": 72, "y": 120, "width": 684, "height": 272},
                "radius": 24,
                "border_color": "#E5E7EB",
                "border_width": 1,
                "background": "${theme.surface_alt}",
                "offline": {
                    "accent": "${theme.danger}",
                    "title": "模拟器离线",
                    "subtitle": "请检查连接状态，或重新启动模拟器服务",
                    "badge": "OFFLINE"
                }
            },
            {
                "id": "user_card",
                "type": "panel",
                "rect": {"x": 42, "y": 458, "width": 744, "height": 110},
                "fill": "${theme.surface}",
                "radius": 24,
                "shadow": {"dx": 0, "dy": 8, "blur": 18, "color": "${theme.shadow}"}
            },
            {
                "id": "user_avatar",
                "type": "avatar",
                "rect": {"x": 70, "y": 482, "width": 60, "height": 60},
                "fill": "#EDE9FE",
                "text": "U",
                "text_color": "${theme.primary}"
            },
            {
                "id": "user_label",
                "type": "text",
                "content": "Current User",
                "position": {"x": 152, "y": 484},
                "style": "eyebrow"
            },
            {
                "id": "user_id",
                "type": "text",
                "content": "${maa.CurruentUser}",
                "position": {"x": 152, "y": 512},
                "style": "user_id"
            },
            {
                "id": "user_hint",
                "type": "text",
                "content": "当前执行账号 / Session ID",
                "position": {"x": 310, "y": 518},
                "style": "meta_text"
            },
            {
                "id": "metrics_card",
                "type": "panel",
                "rect": {"x": 812, "y": 32, "width": 346, "height": 536},
                "fill": "${theme.surface}",
                "radius": 28,
                "shadow": {"dx": 0, "dy": 10, "blur": 24, "color": "${theme.shadow}"}
            },
            {
                "id": "metrics_title",
                "type": "text",
                "content": "System Monitor",
                "position": {"x": 842, "y": 58},
                "style": "section_title"
            },
            {
                "id": "metrics_subtitle",
                "type": "text",
                "content": "资源占用概览",
                "position": {"x": 842, "y": 90},
                "style": "section_subtitle"
            },
            {
                "id": "cpu_ring",
                "type": "metric_ring",
                "label": "CPU",
                "sub_label": "Processor Usage",
                "position": {"x": 842, "y": 152},
                "value_source": "${system.cpu}",
                "color": "#2563EB"
            },
            {
                "id": "divider_1",
                "type": "divider",
                "x1": 842,
                "y1": 254,
                "x2": 1128,
                "y2": 254,
                "color": "${theme.divider}"
            },
            {
                "id": "gpu_ring",
                "type": "metric_ring",
                "label": "GPU",
                "sub_label": "Graphics Usage",
                "position": {"x": 842, "y": 282},
                "value_source": "${system.gpu}",
                "color": "#16A34A"
            },
            {
                "id": "divider_2",
                "type": "divider",
                "x1": 842,
                "y1": 384,
                "x2": 1128,
                "y2": 384,
                "color": "${theme.divider}"
            },
            {
                "id": "mem_ring",
                "type": "metric_ring",
                "label": "Memory",
                "sub_label": "RAM Usage",
                "position": {"x": 842, "y": 412},
                "value_source": "${system.mem.percent}",
                "detail_template": "${system.mem.used} / ${system.mem.total} GB",
                "color": "#D97706"
            },
            {
                "id": "step_card",
                "type": "panel",
                "rect": {"x": 42, "y": 576, "width": 1116, "height": 18},
                "fill": "#D9C3A0",
                "radius": 9
            },
            {
                "id": "step_progress",
                "type": "progress_modern",
                "rect": {"x": 42, "y": 576, "width": 1116, "height": 18},
                "segments_source": "${maa.TotalSteps}",
                "current_source": "${maa.Step}",
                "fill_color": "#16A34A",
                "track_color": "#D9C3A0",
                "divider_color": "#F8FAFC",
                "marker_color": "#DC2626",
                "show_pointer": True
            }
        ],
        "styles": {
            "default": {"font": "msyh.ttc", "size": 24, "color": "#111827"},
            "section_title": {"font": "msyhbd.ttc", "size": 26, "color": "#111827"},
            "section_subtitle": {"font": "msyh.ttc", "size": 15, "color": "#6B7280"},
            "eyebrow": {"font": "arial.ttf", "size": 15, "color": "#6B7280"},
            "user_id": {"font": "arialbd.ttf", "size": 38, "color": "#4F46E5"},
            "meta_text": {"font": "msyh.ttc", "size": 16, "color": "#6B7280"},
            "offline_title": {"font": "msyhbd.ttc", "size": 34, "color": "#DC2626"},
            "offline_subtitle": {"font": "msyh.ttc", "size": 18, "color": "#6B7280"},
            "badge": {"font": "arialbd.ttf", "size": 16, "color": "#FFFFFF"},
            "metric_label": {"font": "arialbd.ttf", "size": 24, "color": "#111827"},
            "metric_sub": {"font": "arial.ttf", "size": 14, "color": "#6B7280"},
            "metric_value": {"font": "arialbd.ttf", "size": 20, "color": "#111827"},
            "metric_detail": {"font": "arial.ttf", "size": 16, "color": "#6B7280"}
        },
        "data_sources": {
            "maa.CurruentUser": {"type": "cache", "field": "CurruentUser", "default": "Unknown"},
            "maa.NextUser": {"type": "cache", "field": "NextUser", "default": ""},
            "maa.Step": {"type": "cache", "field": "Step", "default": "1"},
            "maa.TotalSteps": {"type": "cache", "field": "TotalSteps", "default": "1"},
            "maa.Status": {"type": "cache", "field": "Status", "default": "Idle"},
            "system.cpu": {"type": "function", "name": "get_cpu_usage"},
            "system.gpu": {"type": "function", "name": "get_gpu_usage"},
            "system.mem.percent": {"type": "function", "name": "get_mem_percent"},
            "system.mem.used": {"type": "function", "name": "get_mem_used"},
            "system.mem.total": {"type": "function", "name": "get_mem_total"}
        }
    }

    def __init__(self, config_path: str = "layout_config.json"):
        self.config_path = config_path
        self._config: Optional[Dict] = None
        self._mtime: float = 0
        self._ensure_default_config()

    def _ensure_default_config(self):
        if not os.path.exists(self.config_path):
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)

    def _load_config(self) -> Dict:
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("加载配置失败: %s，使用默认配置", e)
            return json.loads(json.dumps(self.DEFAULT_CONFIG))

    def get_config(self) -> Dict:
        try:
            current_mtime = os.path.getmtime(self.config_path)
        except OSError:
            current_mtime = 0

        if self._config is None or current_mtime != self._mtime:
            self._config = self._load_config()
            self._mtime = current_mtime
        return self._config


class DataResolver:
    def __init__(self, cache: Dict, data_sources: Dict, theme: Optional[Dict] = None):
        self.cache = cache
        self.data_sources = data_sources
        self.theme = theme or {}

    def resolve(self, value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            return str(value)
        if value.startswith("${") and value.endswith("}"):
            return self._resolve_variable(value[2:-1])
        return value

    def resolve_template(self, text: str) -> str:
        if not isinstance(text, str):
            return str(text)
        out = text
        while "${" in out and "}" in out:
            start = out.find("${")
            end = out.find("}", start)
            if start == -1 or end == -1:
                break
            expr = out[start:end + 1]
            out = out.replace(expr, self.resolve(expr), 1)
        return out

    def _resolve_variable(self, var_path: str) -> str:
        if var_path.startswith("theme."):
            return str(self.theme.get(var_path.split(".", 1)[1], ""))

        if var_path in self.data_sources:
            source = self.data_sources[var_path]
            source_type = source.get("type")
            if source_type == "cache":
                field = source.get("field", var_path.split(".")[-1])
                default = source.get("default", "")
                return str(self.cache.get(field, default))
            if source_type == "function":
                return self._call_function(source.get("name", ""))
        return ""

    def _call_function(self, func_name: str) -> str:
        try:
            if func_name == "get_cpu_usage":
                return str(self._get_cpu_usage())
            if func_name == "get_gpu_usage":
                return str(self._get_gpu_usage())
            if func_name == "get_mem_percent":
                return str(self._get_mem_info()[0])
            if func_name == "get_mem_used":
                return str(self._get_mem_info()[1])
            if func_name == "get_mem_total":
                return str(self._get_mem_info()[2])
        except Exception as e:
            logger.warning("获取系统信息失败 %s: %s", func_name, e)
        return "0.0"

    def _get_cpu_usage(self) -> float:
        import subprocess
        try:
            result = subprocess.run(
                ["typeperf", r"\Processor(_Total)\% Processor Time", "-sc", "1"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().split("\n"):
                if '"' in line and "," in line:
                    parts = line.split('","')
                    if len(parts) >= 2:
                        try:
                            return round(float(parts[1].replace('"', '')), 1)
                        except ValueError:
                            pass
        except Exception:
            pass
        return 0.0

    def _get_gpu_usage(self) -> float:
        import subprocess
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return round(float(result.stdout.strip().split("\n")[0]), 1)
        except Exception:
            pass
        try:
            result = subprocess.run(
                [
                    "powershell", "-Command",
                    'Get-Counter "\\GPU Engine(*)\\Utilization Percentage" -MaxSamples 1 | '
                    'Select-Object -ExpandProperty CounterSamples | '
                    'Where-Object {$_.CookedValue -gt 0} | '
                    'Measure-Object CookedValue -Maximum | '
                    'Select-Object -ExpandProperty Maximum'
                ],
                capture_output=True, text=True, timeout=5
            )
            return round(float(result.stdout.strip() or 0), 1)
        except Exception:
            pass
        return 0.0

    def _get_mem_info(self) -> tuple:
        import subprocess
        try:
            result = subprocess.run(
                [
                    "powershell", "-Command",
                    '$os = Get-CimInstance Win32_OperatingSystem; '
                    '"$($os.TotalVisibleMemorySize),$($os.FreePhysicalMemory)"'
                ],
                capture_output=True, text=True, timeout=5
            )
            parts = result.stdout.strip().split(",")
            if len(parts) >= 2:
                total_kb = int(parts[0])
                free_kb = int(parts[1])
                used_kb = total_kb - free_kb
                total_gb = total_kb / (1024 ** 2)
                used_gb = used_kb / (1024 ** 2)
                percent = used_kb / total_kb * 100 if total_kb > 0 else 0
                return round(percent, 1), round(used_gb, 1), round(total_gb, 1)
        except Exception:
            pass
        return 0.0, 0.0, 0.0


class LayoutRenderer:
    def __init__(self, config_manager: LayoutConfigManager):
        self.config_manager = config_manager
        self._font_cache: Dict[Tuple[str, int], ImageFont.ImageFont] = {}

    def render(self, cache: Dict, game_img: Optional[Image.Image] = None) -> Image.Image:
        config = self.config_manager.get_config()
        canvas = config.get("canvas", {})
        theme = config.get("theme", {})
        styles = config.get("styles", {})
        elements = config.get("elements", [])
        data_sources = config.get("data_sources", {})

        width = int(canvas.get("width", 1200))
        height = int(canvas.get("height", 600))
        background = canvas.get("background", "#EEF2F7")
        img = Image.new("RGBA", (width, height), self._resolve_color(background, theme))
        resolver = DataResolver(cache, data_sources, theme)

        for element in elements:
            if not element.get("visible", True):
                continue
            element_type = element.get("type")
            if element_type == "panel":
                self._render_panel(img, element, resolver)
            elif element_type == "text":
                self._render_text(img, element, resolver, styles)
            elif element_type == "image_panel":
                self._render_image_panel(img, element, resolver, game_img, styles)
            elif element_type == "avatar":
                self._render_avatar(img, element, resolver)
            elif element_type == "metric_ring":
                self._render_metric_ring(img, element, resolver, styles, theme)
            elif element_type == "divider":
                self._render_divider(img, element, resolver)
            elif element_type == "progress_modern":
                self._render_progress_modern(img, element, resolver)

        return img.convert("RGB")

    def _get_font(self, font_name: str, size: int) -> ImageFont.ImageFont:
        key = (font_name, size)
        if key in self._font_cache:
            return self._font_cache[key]
        try:
            font = ImageFont.truetype(font_name, size)
        except Exception:
            font = ImageFont.load_default()
        self._font_cache[key] = font
        return font

    def _resolve_color(self, value: str, theme: Dict) -> str:
        if isinstance(value, str) and value.startswith("${theme.") and value.endswith("}"):
            return theme.get(value[8:-1], "#000000")
        return value

    def _rounded_mask(self, size: Tuple[int, int], radius: int) -> Image.Image:
        mask = Image.new("L", size, 0)
        d = ImageDraw.Draw(mask)
        d.rounded_rectangle([0, 0, size[0], size[1]], radius=radius, fill=255)
        return mask

    def _draw_shadow(self, base: Image.Image, rect: List[int], radius: int, shadow: Dict, resolver: DataResolver):
        dx = int(shadow.get("dx", 0))
        dy = int(shadow.get("dy", 6))
        blur = int(shadow.get("blur", 16))
        color = resolver.resolve(shadow.get("color", "#00000020"))
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        sx1, sy1, sx2, sy2 = rect[0] + dx, rect[1] + dy, rect[2] + dx, rect[3] + dy
        d.rounded_rectangle([sx1, sy1, sx2, sy2], radius=radius, fill=color)
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
        base.alpha_composite(layer)

    def _render_panel(self, img: Image.Image, element: Dict, resolver: DataResolver):
        rect_cfg = element.get("rect", {})
        x, y = rect_cfg.get("x", 0), rect_cfg.get("y", 0)
        w, h = rect_cfg.get("width", 100), rect_cfg.get("height", 100)
        rect = [x, y, x + w, y + h]
        radius = int(element.get("radius", 20))
        if element.get("shadow"):
            self._draw_shadow(img, rect, radius, element["shadow"], resolver)
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        fill = resolver.resolve(element.get("fill", "#FFFFFF"))
        outline = resolver.resolve(element.get("outline", "")) if element.get("outline") else None
        width = int(element.get("outline_width", 1))
        d.rounded_rectangle(rect, radius=radius, fill=fill, outline=outline, width=width)
        img.alpha_composite(layer)

    def _render_text(self, img: Image.Image, element: Dict, resolver: DataResolver, styles: Dict):
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        content = resolver.resolve_template(element.get("content", ""))
        position = element.get("position", {})
        x, y = int(position.get("x", 0)), int(position.get("y", 0))
        align = position.get("align", "left")
        max_width = position.get("max_width")

        style = styles.get(element.get("style", "default"), styles.get("default", {}))
        font = self._get_font(style.get("font", "arial.ttf"), int(style.get("size", 24)))
        color = resolver.resolve(style.get("color", "#111827"))
        line_spacing = int(style.get("line_spacing", 8))

        lines = self._wrap_text(draw, content, font, max_width) if max_width else content.split("\n")
        line_boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
        total_h = sum((b[3] - b[1]) for b in line_boxes) + max(0, len(lines) - 1) * line_spacing

        cursor_y = y
        if position.get("valign") == "middle":
            cursor_y = y - total_h // 2

        for line, bbox in zip(lines, line_boxes):
            line_w = bbox[2] - bbox[0]
            lx = x
            if align == "center":
                lx = x - line_w // 2
            elif align == "right":
                lx = x - line_w
            draw.text((lx, cursor_y), line, fill=color, font=font)
            cursor_y += (bbox[3] - bbox[1]) + line_spacing
        img.alpha_composite(layer)

    def _wrap_text(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
        if not text:
            return [""]
        lines: List[str] = []
        for paragraph in text.split("\n"):
            current = ""
            for ch in paragraph:
                trial = current + ch
                bbox = draw.textbbox((0, 0), trial, font=font)
                if bbox[2] - bbox[0] <= max_width or not current:
                    current = trial
                else:
                    lines.append(current)
                    current = ch
            lines.append(current)
        return lines

    def _render_image_panel(self, img: Image.Image, element: Dict, resolver: DataResolver,
                            game_img: Optional[Image.Image], styles: Dict):
        rect_cfg = element.get("rect", {})
        x, y = rect_cfg.get("x", 0), rect_cfg.get("y", 0)
        w, h = rect_cfg.get("width", 640), rect_cfg.get("height", 360)
        radius = int(element.get("radius", 18))
        background = resolver.resolve(element.get("background", "#F7F9FC"))
        border_color = resolver.resolve(element.get("border_color", "#E5E7EB"))
        border_width = int(element.get("border_width", 1))

        panel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        pd = ImageDraw.Draw(panel)
        pd.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=background, outline=border_color, width=border_width)

        if game_img is not None and element.get("source") == "capture":
            rendered = self._fit_cover(game_img.convert("RGBA"), w, h)
            mask = self._rounded_mask((w, h), radius)
            panel.paste(rendered, (0, 0), mask)
        else:
            offline = element.get("offline", {})
            accent = resolver.resolve(offline.get("accent", "#DC2626"))
            self._draw_offline_state(panel, offline, accent, styles)

        img.alpha_composite(panel, (x, y))

    def _fit_cover(self, src: Image.Image, w: int, h: int) -> Image.Image:
        sw, sh = src.size
        scale = max(w / sw, h / sh)
        nw, nh = int(sw * scale), int(sh * scale)
        resized = src.resize((nw, nh), Image.Resampling.LANCZOS)
        left = max(0, (nw - w) // 2)
        top = max(0, (nh - h) // 2)
        return resized.crop((left, top, left + w, top + h))

    def _draw_offline_state(self, panel: Image.Image, offline: Dict, accent: str, styles: Dict):
        w, h = panel.size
        d = ImageDraw.Draw(panel)
        soft = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(soft)
        sd.rounded_rectangle([18, 18, w - 18, h - 18], radius=20, fill="#FFF7F7")
        sd.rounded_rectangle([18, 18, 24, h - 18], radius=3, fill=accent)
        panel.alpha_composite(soft)

        badge_w, badge_h = 98, 30
        d.rounded_rectangle([w - 130, 26, w - 32, 26 + badge_h], radius=15, fill=accent)
        badge_font = self._get_font(styles["badge"]["font"], int(styles["badge"]["size"]))
        badge = offline.get("badge", "OFFLINE")
        bbox = d.textbbox((0, 0), badge, font=badge_font)
        d.text((w - 81 - (bbox[2] - bbox[0]) // 2, 32), badge, font=badge_font, fill=styles["badge"]["color"])

        icon_cx, icon_cy = 110, h // 2
        d.ellipse([icon_cx - 30, icon_cy - 30, icon_cx + 30, icon_cy + 30], fill="#FEE2E2")
        d.line([(icon_cx, icon_cy - 15), (icon_cx, icon_cy + 5)], fill=accent, width=6)
        d.ellipse([icon_cx - 3, icon_cy + 12, icon_cx + 3, icon_cy + 18], fill=accent)

        title_font = self._get_font(styles["offline_title"]["font"], int(styles["offline_title"]["size"]))
        sub_font = self._get_font(styles["offline_subtitle"]["font"], int(styles["offline_subtitle"]["size"]))
        title = offline.get("title", "模拟器离线")
        subtitle = offline.get("subtitle", "请检查连接状态")
        d.text((170, h // 2 - 42), title, fill=styles["offline_title"]["color"], font=title_font)

        lines = self._wrap_text(d, subtitle, sub_font, w - 220)
        sy = h // 2 + 8
        for line in lines[:2]:
            d.text((170, sy), line, fill=styles["offline_subtitle"]["color"], font=sub_font)
            sy += 28

    def _render_avatar(self, img: Image.Image, element: Dict, resolver: DataResolver):
        rect = element.get("rect", {})
        x, y = rect.get("x", 0), rect.get("y", 0)
        w, h = rect.get("width", 56), rect.get("height", 56)
        fill = resolver.resolve(element.get("fill", "#EDE9FE"))
        text = resolver.resolve(element.get("text", "U"))
        text_color = resolver.resolve(element.get("text_color", "#4F46E5"))

        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.ellipse([x, y, x + w, y + h], fill=fill)
        font = self._get_font("arialbd.ttf", 28)
        bbox = d.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text((x + w / 2 - tw / 2, y + h / 2 - th / 2 - 2), text, fill=text_color, font=font)
        img.alpha_composite(layer)

    def _render_metric_ring(self, img: Image.Image, element: Dict, resolver: DataResolver, styles: Dict, theme: Dict):
        pos = element.get("position", {})
        x, y = pos.get("x", 0), pos.get("y", 0)
        label = resolver.resolve_template(element.get("label", ""))
        sub_label = resolver.resolve_template(element.get("sub_label", ""))
        detail_template = element.get("detail_template")
        detail = resolver.resolve_template(detail_template) if detail_template else ""

        try:
            value = float(resolver.resolve(element.get("value_source", "0")))
        except Exception:
            value = 0.0
        value = max(0.0, min(100.0, value))

        # 修改：更细的圆环，更精致
        ring_r = int(element.get("radius", 36))
        ring_w = int(element.get("thickness", 8))
        color = resolver.resolve(element.get("color", "#2563EB"))
        track = resolver.resolve(element.get("track_color", theme.get("track", "#E5E7EB")))
        center_x = x + 200
        center_y = y + 34

        label_font = self._get_font(styles["metric_label"]["font"], int(styles["metric_label"]["size"]))
        sub_font = self._get_font(styles["metric_sub"]["font"], int(styles["metric_sub"]["size"]))
        value_font = self._get_font(styles["metric_value"]["font"], int(styles["metric_value"]["size"]))
        detail_font = self._get_font(styles["metric_detail"]["font"], int(styles["metric_detail"]["size"]))

        # 先绘制标签
        d_temp = ImageDraw.Draw(img)
        d_temp.text((x, y), label, fill=styles["metric_label"]["color"], font=label_font)
        d_temp.text((x, y + 30), sub_label, fill=styles["metric_sub"]["color"], font=sub_font)
        if detail:
            d_temp.text((x, y + 54), detail, fill=styles["metric_detail"]["color"], font=detail_font)

        bbox = [center_x - ring_r, center_y - ring_r, center_x + ring_r, center_y + ring_r]
        
        # 1) 先画阴影层
        shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow_layer)
        shadow_offset = 2
        shadow_color = "#00000020"
        shadow_bbox = [
            bbox[0], bbox[1] + shadow_offset,
            bbox[2], bbox[3] + shadow_offset
        ]
        sd.arc(shadow_bbox, start=0, end=359, fill=shadow_color, width=ring_w)
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(3))
        img.alpha_composite(shadow_layer)
        
        # 2) 绘制主圆环层
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        
        # 底轨
        d.arc(bbox, start=0, end=359, fill=track, width=ring_w)
        
        # 进度弧（带圆头）
        start_angle = -90
        end_angle = -90 + value * 3.6
        d.arc(bbox, start=start_angle, end=end_angle, fill=color, width=ring_w)
        
        # 圆头：起点和终点各补一个实心小圆
        cap_r = ring_w / 2
        cap_center_r = ring_r
        
        # 起点
        sx = center_x + math.cos(math.radians(start_angle)) * cap_center_r
        sy = center_y + math.sin(math.radians(start_angle)) * cap_center_r
        # 终点
        ex = center_x + math.cos(math.radians(end_angle)) * cap_center_r
        ey = center_y + math.sin(math.radians(end_angle)) * cap_center_r
        
        d.ellipse([sx - cap_r, sy - cap_r, sx + cap_r, sy + cap_r], fill=color)
        if value > 0:
            d.ellipse([ex - cap_r, ey - cap_r, ex + cap_r, ey + cap_r], fill=color)
        
        # 中心留白
        inner = ring_r - ring_w - 2
        d.ellipse([center_x - inner, center_y - inner, center_x + inner, center_y + inner], fill="#FFFFFF")
        
        img.alpha_composite(layer)

        # 绘制百分比文字
        text = f"{value:.0f}%"
        tb = d_temp.textbbox((0, 0), text, font=value_font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        d_temp.text((center_x - tw / 2, center_y - th / 2 - 1), text, fill=styles["metric_value"]["color"], font=value_font)

    def _render_divider(self, img: Image.Image, element: Dict, resolver: DataResolver):
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        color = resolver.resolve(element.get("color", "#E5E7EB"))
        width = int(element.get("width", 1))
        d.line([(element.get("x1", 0), element.get("y1", 0)), (element.get("x2", 0), element.get("y2", 0))], fill=color, width=width)
        img.alpha_composite(layer)

    def _render_progress_modern(self, img: Image.Image, element: Dict, resolver: DataResolver):
        rect = element.get("rect", {})
        x, y = rect.get("x", 0), rect.get("y", 0)
        w, h = rect.get("width", 1000), rect.get("height", 18)
        radius = h // 2

        try:
            total = max(1, int(float(resolver.resolve(element.get("segments_source", "1")))))
        except Exception:
            total = 1
        try:
            current_step = max(1, int(float(resolver.resolve(element.get("current_source", "1")))))
        except Exception:
            current_step = 1
        current_index = max(0, min(total - 1, current_step - 1))

        track_color = resolver.resolve(element.get("track_color", "#D9C3A0"))
        marker_color = resolver.resolve(element.get("marker_color", "#DC2626"))

        # 绘制底轨（带阴影和高光）
        # 先画阴影
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        sd.rounded_rectangle([x, y + 2, x + w, y + h + 2], radius=radius, fill="#00000012")
        shadow = shadow.filter(ImageFilter.GaussianBlur(2))
        img.alpha_composite(shadow)
        
        # 再画底轨
        d_temp = ImageDraw.Draw(img)
        d_temp.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=track_color)
        # 顶部高光
        d_temp.rounded_rectangle([x, y, x + w, y + h // 2], radius=radius, fill="#FFFFFF18")

        # 渐变填充
        fill_ratio = current_index / total
        fill_w = int(w * fill_ratio)
        if fill_w > 0:
            grad = Image.new("RGBA", (fill_w, h), (0, 0, 0, 0))
            gd = ImageDraw.Draw(grad)
            # 暖金色渐变
            start_rgb = (245, 158, 11)
            end_rgb = (234, 179, 8)
            for i in range(fill_w):
                t = i / max(fill_w - 1, 1)
                r = int(start_rgb[0] * (1 - t) + end_rgb[0] * t)
                g = int(start_rgb[1] * (1 - t) + end_rgb[1] * t)
                b = int(start_rgb[2] * (1 - t) + end_rgb[2] * t)
                gd.line([(i, 0), (i, h)], fill=(r, g, b, 255))
            
            # 圆角 mask
            mask = Image.new("L", (fill_w, h), 0)
            md = ImageDraw.Draw(mask)
            md.rounded_rectangle([0, 0, fill_w, h], radius=radius, fill=255)
            
            # 创建临时图层
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            layer.paste(grad, (x, y), mask)
            img.alpha_composite(layer)

        # 分隔线（更细、更柔和）
        seg_w = w / total
        for i in range(1, total):
            sx = int(x + i * seg_w)
            d_temp.rounded_rectangle([sx - 1, y + 4, sx + 1, y + h - 4], radius=1, fill="#FFFFFFCC")

        # 当前位置标记（圆头小胶囊）
        marker_x = int(x + (current_index + 0.5) * seg_w)
        # marker 主体
        d_temp.rounded_rectangle(
            [marker_x - 4, y - 1, marker_x + 4, y + h + 1],
            radius=4, fill=marker_color
        )
        # marker 高光
        d_temp.rounded_rectangle(
            [marker_x - 2, y + 2, marker_x + 2, y + h // 2],
            radius=2, fill="#FFFFFF55"
        )

        if element.get("show_pointer", True):
            self._draw_pointer(img, marker_x, y - 8, marker_color)

    def _draw_pointer(self, img: Image.Image, cx: int, bottom_y: int, color: str):
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        # 阴影
        shadow_pts = [(cx, bottom_y + 2), (cx - 12, bottom_y - 16), (cx + 12, bottom_y - 16)]
        d.polygon(shadow_pts, fill="#00000022")
        # 主体（更精致，宽度收一点）
        pts = [(cx, bottom_y), (cx - 11, bottom_y - 18), (cx + 11, bottom_y - 18)]
        d.polygon(pts, fill=color)
        img.alpha_composite(layer)


def create_layout_engine(config_path: str = "layout_config.json") -> Tuple[LayoutConfigManager, LayoutRenderer]:
    config_manager = LayoutConfigManager(config_path)
    renderer = LayoutRenderer(config_manager)
    return config_manager, renderer


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    config_manager, renderer = create_layout_engine()
    test_cache = {
        "CurruentUser": "6142",
        "NextUser": "2237",
        "Step": "3",
        "TotalSteps": "7",
        "Status": "Running"
    }
    img = renderer.render(test_cache)
    out = "C:/Users/NSLC/.openclaw/workspace-parallel-agent-executor/test_layout_output_v3.png"
    img.save(out)
    print(out)

