"""Render the eighteen approved quotation fields as an opaque, printable PNG."""
from __future__ import annotations

import os
import re
from pathlib import Path

from PyQt6.QtCore import QIODevice, QPointF, QRectF, QSaveFile, Qt
from PyQt6.QtGui import (
    QFont, QFontDatabase, QFontMetricsF, QGuiApplication, QImage, QPainter, QPen,
    QRawFont, QTextLayout, QTextOption,
)

from .nailok_flow import FEATURES


DPI = 300
WIDTH = round(DPI * 100 / 25.4)
MARGIN = round(DPI * 6 / 25.4)
MAX_FIELD_LENGTH = 4000
MAX_HEIGHT = 16000


def chinese_font():
    families = QFontDatabase.families()
    preferred = ('Microsoft JhengHei', 'Noto Sans CJK TC', 'Noto Sans TC', 'PingFang TC', 'Source Han Sans TC')
    for family in preferred:
        if family in families:
            return family
    if os.name == 'nt':
        fonts_dir = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
        regular = QFontDatabase.addApplicationFont(str(fonts_dir / 'msjh.ttc'))
        if regular >= 0:
            QFontDatabase.addApplicationFont(str(fonts_dir / 'msjhbd.ttc'))
            return QFontDatabase.applicationFontFamilies(regular)[0]
    for family in families:
        font = QRawFont.fromFont(QFont(family))
        if all(font.supportsCharacter(char) for char in '報價標籤聯絡傳真'):
            return family
    raise ValueError('系統缺少可列印繁體中文的字型')


def make_font(family, pixels, *, bold=False):
    font = QFont(family)
    font.setPixelSize(pixels)
    font.setBold(bold)
    return font


def field_text(key, value, unit):
    """Return the printable value and whether a blank amount line precedes it."""
    if not unit:
        return value, not value
    if key == 'quotation.price':
        parts = [part.strip() for part in unit.split('/') if part.strip()]
        if not parts:
            return value, not value
        tail = r'\s*/\s*' + r'\s*/\s*'.join(re.escape(part) for part in parts) + '$'
        amount = re.sub(tail, '', value, flags=re.I).strip()
        format_text = '/ ' + ' / '.join(parts)
        return (f'{amount} {format_text}' if amount else format_text), not amount
    if re.search(r'\s' + re.escape(unit) + '$', value, re.I):
        return value, False
    return ' '.join(part for part in (value, unit) if part), not value


def wrap_text(text, font, width, line_height):
    """Prelayout all lines before allocating the image, including long words."""
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layouts = []
    height = 0.0
    for paragraph in text.split('\n'):
        if not paragraph:
            height += line_height
            continue
        layout = QTextLayout(paragraph, font)
        layout.setTextOption(option)
        layout.beginLayout()
        while (line := layout.createLine()).isValid():
            line.setLineWidth(width)
            line.setPosition(QPointF(0, height))
            height += max(line_height, line.height())
        layout.endLayout()
        layouts.append(layout)
    return layouts, height


def render_label(fields):
    """Render approved rows from the current label snapshot in template order."""
    if QGuiApplication.instance() is None:
        raise RuntimeError('請先初始化 Qt，再產生報價標籤')
    entries = {row['key']: row for row in fields}
    family = chinese_font()
    title_font = make_font(family, 58, bold=True)
    label_font = make_font(family, 39, bold=True)
    value_font = make_font(family, 39)
    title_metrics = QFontMetricsF(title_font)
    label_metrics = QFontMetricsF(label_font)
    line_height = max(label_metrics.height(), QFontMetricsF(value_font).height())
    label_width = max(label_metrics.horizontalAdvance(label + '：') for _, label, _, _ in FEATURES)
    value_x = MARGIN + label_width + 18
    width = WIDTH - MARGIN - value_x
    blank_width = 175
    y = MARGIN + title_metrics.height() + 35
    rows = []
    for key, label, _, _ in FEATURES:
        entry = entries.get(key, {})
        raw_value = entry.get('value')
        value = '' if raw_value is None else str(raw_value).strip()
        unit = str(entry.get('unit') or '').strip()
        if len(value) + len(unit) > MAX_FIELD_LENGTH:
            raise ValueError(f'「{label}」過長，請縮短後再產生圖片')
        text, blank = field_text(key, value, unit)
        x = value_x + (blank_width + 18 if blank and text else 0)
        layouts, height = wrap_text(text, value_font, width - (x - value_x), line_height)
        rows.append((label, y, x, blank, layouts))
        y += height + 10
        if y + MARGIN > MAX_HEIGHT:
            raise ValueError('標籤內容過長，請縮短後再產生圖片')

    image = QImage(WIDTH, int(y + MARGIN + .999), QImage.Format.Format_RGB32)
    if image.isNull():
        raise RuntimeError('無法配置報價標籤圖片')
    density = round(DPI / .0254)
    image.setDotsPerMeterX(density)
    image.setDotsPerMeterY(density)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setPen(QPen(Qt.GlobalColor.black, 2))
        painter.setFont(title_font)
        painter.drawText(QRectF(MARGIN, MARGIN, WIDTH - 2 * MARGIN, title_metrics.height()),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, '報價標籤')
        painter.setFont(label_font)
        for label, top, x, blank, layouts in rows:
            baseline = top + label_metrics.ascent()
            painter.drawText(QPointF(MARGIN, baseline), label + '：')
            if blank:
                painter.drawLine(QPointF(value_x, baseline + 3), QPointF(value_x + blank_width, baseline + 3))
            for layout in layouts:
                layout.draw(painter, QPointF(x, top))
    finally:
        painter.end()
    return image


def write_label_png(image, path):
    """Write a PNG atomically and return its final path."""
    if image.isNull():
        raise ValueError('報價標籤沒有可寫入的圖片')
    target = Path(path)
    temporary = QSaveFile(str(target))
    temporary.setDirectWriteFallback(False)
    if not temporary.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(f'無法寫入報價標籤：{temporary.errorString()}')
    if not image.save(temporary, 'PNG'):
        temporary.cancelWriting()
        raise OSError('無法編碼報價標籤 PNG')
    if not temporary.commit():
        raise OSError(f'無法保存報價標籤：{temporary.errorString()}')
    return target
