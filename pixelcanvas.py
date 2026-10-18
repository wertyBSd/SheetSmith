"""Мини-библиотека пиксель-арта: растеризация масок и запись PNG.

Зависимостей нет (только stdlib: zlib + struct), чтобы генератор спрайтов
работал на любой машине без установки Pillow/numpy.

Модель: спрайт собирается из «масок» (наборов пикселей). Сначала рисуются
контуры частей тела, затем они сливаются в один силуэт, заливаются тёмным
металлом и обводятся неоном по внешнему контуру.
"""

import math
import struct
import zlib


# --- PNG -------------------------------------------------------------------

def png_bytes(width, height, rows):
    """PNG как bytes: rows — список height списков width кортежей (r, g, b, a)."""
    raw = bytearray()
    for row in rows:
        raw.append(0)  # filter type 0 (None)
        for px in row:
            raw.extend(px[:4])

    def chunk(tag, data):
        out = struct.pack(">I", len(data)) + tag + data
        return out + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", ihdr)
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    return png


def write_png(path, width, height, rows):
    """Та же кодировка, что в png_bytes, но сразу в файл."""
    with open(path, "wb") as fh:
        fh.write(png_bytes(width, height, rows))


# --- Палитра ---------------------------------------------------------------

def hex_rgba(value, alpha=255):
    """'#00FFFF' или '#00FFFFAA' -> (r, g, b, a)."""
    value = value.lstrip("#")
    if len(value) == 6:
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha)
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16),
            int(value[6:8], 16))


def scale_rgb(rgb, k):
    """Масштабирует цвет по яркости с сохранением alpha (для дальних конечностей)."""
    r, g, b, a = rgb
    return (max(0, min(255, int(r * k))), max(0, min(255, int(g * k))),
            max(0, min(255, int(b * k))), a)


# --- Холст -----------------------------------------------------------------

class Canvas:
    """RGBA-холст с целочисленным заполнением (без сглаживания)."""

    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.px = [[(0, 0, 0, 0) for _ in range(w)] for _ in range(h)]

    def set(self, x, y, color):
        x = int(round(x))
        y = int(round(y))
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y][x] = color

    def get(self, x, y):
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.px[y][x]
        return (0, 0, 0, 0)

    def is_opaque(self, x, y):
        return self.get(x, y)[3] > 0

    def blit(self, other, ox, oy):
        for y in range(other.h):
            for x in range(other.w):
                c = other.px[y][x]
                if c[3] > 0:
                    self.set(ox + x, oy + y, c)

    def fill_rect(self, x0, y0, x1, y1, color):
        for y in range(int(y0), int(y1) + 1):
            for x in range(int(x0), int(x1) + 1):
                self.set(x, y, color)

    def frame_rect(self, x0, y0, x1, y1, color):
        for x in range(int(x0), int(x1) + 1):
            self.set(x, y0, color)
            self.set(x, y1, color)
        for y in range(int(y0), int(y1) + 1):
            self.set(x0, y, color)
            self.set(x1, y, color)

    def save(self, path):
        write_png(path, self.w, self.h, self.px)

    def to_png_bytes(self):
        """PNG в памяти — для отдачи по HTTP без записи на диск."""
        return png_bytes(self.w, self.h, self.px)

    def scaled(self, k):
        """Целочисленное увеличение (для превью/контактных листов)."""
        out = Canvas(self.w * k, self.h * k)
        for y in range(self.h):
            for x in range(self.w):
                c = self.px[y][x]
                if c[3] == 0:
                    continue
                for dy in range(k):
                    for dx in range(k):
                        out.set(x * k + dx, y * k + dy, c)
        return out


# --- Маски (булевы сетки пикселей) ----------------------------------------

class Mask:
    """Набор пикселей: булева сетка с операциями фигур."""

    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.cells = [[False] * w for _ in range(h)]

    def add(self, x, y):
        x = int(round(x))
        y = int(round(y))
        if 0 <= x < self.w and 0 <= y < self.h:
            self.cells[y][x] = True

    def has(self, x, y):
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.cells[y][x]
        return False

    def union(self, other):
        for y in range(self.h):
            for x in range(self.w):
                if other.has(x, y):
                    self.add(x, y)
        return self

    def taper(self, a, b, r0, r1, step=1.0):
        """Конечность переменной толщины: радиус r0 в точке a, r1 в точке b.

        limb() рисует капсулу постоянной ширины, поэтому бедро и голень
        выходили одинаковыми и нога читалась как труба. На пятиголовах
        figure это особенно заметно: глаз ждёт сужения к щиколотке.
        """
        (x0, y0), (x1, y1) = a, b
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 1e-6:
            self.disc(x0, y0, r0)
            return
        n = max(2, int(length / step))
        for i in range(n + 1):
            t = i / float(n)
            self.disc(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, r0 + (r1 - r0) * t)

    def rect(self, x0, y0, x1, y1):
        for y in range(int(y0), int(y1) + 1):
            for x in range(int(x0), int(x1) + 1):
                self.add(x, y)

    def disc(self, cx, cy, r):
        for y in range(int(cy - r) - 1, int(cy + r) + 2):
            for x in range(int(cx - r) - 1, int(cx + r) + 2):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    self.add(x, y)

    def ellipse(self, cx, cy, rx, ry):
        for y in range(int(cy - ry) - 1, int(cy + ry) + 2):
            for x in range(int(cx - rx) - 1, int(cx + rx) + 2):
                if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0:
                    self.add(x, y)

    def limb(self, p0, p1, width):
        """Конечность-капсула: цепочка дисков по отрезку (круглые стыки)."""
        x0, y0 = p0
        x1, y1 = p1
        steps = int(max(abs(x1 - x0), abs(y1 - y0)) * 2) + 1
        for i in range(steps + 1):
            t = i / steps
            self.disc(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, width)

    def poly(self, points):
        """Заливка многоугольника scanline-алгоритмом."""
        pts = list(points)
        if len(pts) < 3:
            return
        ys = [p[1] for p in pts]
        for y in range(int(min(ys)), int(max(ys)) + 1):
            xs = []
            n = len(pts)
            for i in range(n):
                x1p, y1p = pts[i]
                x2p, y2p = pts[(i + 1) % n]
                if y1p == y2p:
                    continue
                if min(y1p, y2p) <= y <= max(y1p, y2p):
                    xs.append(x1p + (y - y1p) * (x2p - x1p) / (y2p - y1p))
            xs.sort()
            for i in range(0, len(xs) - 1, 2):
                for x in range(int(round(xs[i])), int(round(xs[i + 1])) + 1):
                    self.add(x, y)

    def tri(self, p0, p1, p2):
        self.poly([p0, p1, p2])


def outline(mask):
    """Пиксели маски, у которых хотя бы один 4-сосед вне маски (внешний контур)."""
    out = Mask(mask.w, mask.h)
    for y in range(mask.h):
        for x in range(mask.w):
            if not mask.has(x, y):
                continue
            if (not mask.has(x - 1, y) or not mask.has(x + 1, y)
                    or not mask.has(x, y - 1) or not mask.has(x, y + 1)):
                out.add(x, y)
    return out


def dilate(mask, radius=1):
    """Расширение маски на radius px по 8-связности.

    В отличие от outline() работает СНАРУЖИ: нужно для обводки силуэта,
    которую рисуют под заливкой — иначе контур съедает пиксель формы.
    """
    out = Mask(mask.w, mask.h)
    for y in range(mask.h):
        for x in range(mask.w):
            if not mask.has(x, y):
                continue
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    out.add(x + dx, y + dy)
    return out


def contour(mask, radius=1):
    """Кольцо ТОЛЬКО СНАРУЖИ маски: расширение минус исходная форма."""
    grown = dilate(mask, radius)
    out = Mask(mask.w, mask.h)
    for y in range(mask.h):
        for x in range(mask.w):
            if grown.has(x, y) and not mask.has(x, y):
                out.add(x, y)
    return out


def paint(canvas, mask, color):
    for y in range(mask.h):
        for x in range(mask.w):
            if mask.has(x, y):
                canvas.set(x, y, color)