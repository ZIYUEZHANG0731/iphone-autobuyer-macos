"""Create the application's own simple icon; contains no external media."""
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.utils.qt import prepare_plugins
from PySide6.QtGui import QGuiApplication,QImage,QPainter,QColor,QFont
from PySide6.QtCore import Qt,QRectF

prepare_plugins()
qt=QGuiApplication([])
root=Path(__file__).resolve().parents[1]
icons=root/'build/AutoBuyer.iconset';icons.mkdir(parents=True,exist_ok=True)
for base in (16,32,128,256,512):
    for scale in (1,2):
        n=base*scale
        im=QImage(n,n,QImage.Format.Format_ARGB32);im.fill(Qt.GlobalColor.transparent)
        painter=QPainter(im);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('#007aff'))
        painter.drawRoundedRect(QRectF(n*.05,n*.05,n*.9,n*.9),n*.21,n*.21)
        painter.setPen(QColor('#ffffff'));font=QFont('Helvetica Neue');font.setPixelSize(int(n*.60));font.setBold(True);painter.setFont(font)
        painter.drawText(im.rect(),Qt.AlignmentFlag.AlignCenter,'A');painter.end()
        im.save(str(icons/(f'icon_{base}x{base}'+('@2x' if scale==2 else '')+'.png')))
subprocess.run(['iconutil','-c','icns',str(icons),'-o',str(root/'app/gui/icon.icns')],check=True)
