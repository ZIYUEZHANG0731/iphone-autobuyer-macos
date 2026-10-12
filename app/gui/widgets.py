"""Small, reusable native-Qt visual elements; no external images or fonts."""
from __future__ import annotations

from functools import lru_cache
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton


PATHS={
    'box':'<path d="m12 3 9 5v9l-9 5-9-5V8zM3 8l9 5 9-5M12 13v9M7.5 5.5l9 5"/>',
    'store':'<path d="M4 10v10h16V10M7 20v-7h5v7M3 10l2-7h14l2 7M3 10c0 3 4 3 4 0 0 3 5 3 5 0 0 3 5 3 5 0 0 3 4 3 4 0M8 3l-1 7M16 3l1 7M12 3v7"/>',
    'person':'<circle cx="12" cy="7" r="4"/><path d="M4 21v-3c0-7 16-7 16 0v3z"/>',
    'card':'<rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 10h18M7 15h3"/>',
    'sliders':'<path d="M3 5h4m4 0h10M3 12h10m4 0h4M3 19h4m4 0h10"/><circle cx="9" cy="5" r="2"/><circle cx="15" cy="12" r="2"/><circle cx="9" cy="19" r="2"/>',
    'document':'<path d="M14 3H5v18h14V8zM14 3v6h5M8 13h8M8 17h6"/>',
    'phone':'<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M10 3h4"/><circle cx="12" cy="19" r=".5"/>',
    'help':'<circle cx="12" cy="12" r="9"/><path d="M9.5 8.5c0-3 5-3 5 0 0 2-2.5 2-2.5 4M12 16h.01"/>',
    'clock':'<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
    'check':'<path d="m5 12 4 4L19 6"/>',
}


@lru_cache(maxsize=48)
def outline_icon(name, color='#3a3a3c', size=18):
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><g fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{PATHS[name]}</g></svg>'
    pixmap=QPixmap(size*2,size*2);pixmap.fill(Qt.GlobalColor.transparent)
    painter=QPainter(pixmap);renderer=QSvgRenderer(QByteArray(svg.encode()));renderer.render(painter);painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def divider():
    frame=QFrame();frame.setObjectName('divider');frame.setFixedHeight(1)
    return frame


def section_header(title, action=None, callback=None):
    row=QHBoxLayout();row.setSpacing(8)
    label=QLabel(title);label.setObjectName('sectionTitle');row.addWidget(label);row.addStretch()
    if action:
        button=QPushButton(action);button.setObjectName('link')
        button.clicked.connect(callback);row.addWidget(button)
    return row
