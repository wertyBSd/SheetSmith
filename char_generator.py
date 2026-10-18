"""Генератор спрайт-листов персонажей (пиксель-арт, 80x128 на кадр).

Самостоятельный проект: собирает спрайт-листы боевых персонажей из кода,
без единой внешней зависимости (только стандартная библиотека Python).

Как это устроено
----------------
Персонаж описывается двумя словарями:

  MATERIALS[id] — ПАЛИТРА: три тона села-шейдинга для каждого материала
                  (ткань рясы / броня / кожа) плюс цвет неона и глаза.
  ARCH[id]      — АРХЕТИП: пропорции (плечи, талия, толщина руки и ноги),
                  форма торса, кисти, стопы и построение головы.

Отрисовка общая для всех: объём считается из поля расстояний до края маски,
по нему же восстанавливается приближённая нормаль, свет падает сверху-слева,
тона жёсткие (полосы, а не градиент — это язык пиксель-арта).

Новый персонаж добавляется ДАННЫМИ, а не кодом: опиши его в characters.json
(палитра + архетип) и запусти с --characters characters.json.

Запуск:
  py char_generator.py --hero tetrax --scale 3 --preview-only
  py char_generator.py --hero all
  py char_generator.py --characters my_chars.json --hero my_char --write
  py char_generator.py --list

Справка по архетипам: README.md
"""

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pixelcanvas import Canvas, Mask, hex_rgba, paint, contour

# --- Геометрия кадра --------------------------------------------------------

# Кадр 80x128, а не 64: при ширине 64 выпрямленная рука (плечо на x=47,
# длина руки 30.7 px) уходила на x=78 и обрезалась краем. 80 даёт удар,
# читаемый как удар, а не как обрубок на полпути к цели.
FW, FH = 80, 128         # размер одного кадра
COLS, ROWS = 4, 6        # сетка спрайт-листа
S = 2                    # во сколько раз крупнее старого кадра (32x48)

# Пропорции: ~5.5 головы. Голова (вместе с убором) занимает ~23 px из 128.
HEAD_TOP, CHIN = 4, 27
HEAD_C = (32, 15)
FOOT_Y = 124


# --- Палитра ----------------------------------------------------------------
# Каждый материал — свой набор из трёх тонов. Тона идут от светлого к тёмному:
# HI (свет), BASE (основной), DARK (тень).

def ramp(hex_hi, hex_base, hex_dark):
    return (hex_rgba(hex_hi), hex_rgba(hex_base), hex_rgba(hex_dark))


# Характер блика по материалу. В v2 у ткани, кожи и стали был ОДИН и тот же
# трёхтональный сел-шейдинг, поэтому глаз не считывал, где броня, а где
# ряса: материал отличался только тоном. В настоящем пиксель-арте материал
# опознаётся по тому, как он ловит свет:
#
#   spec      — цвет блика; None = блика нет (ткань его не даёт)
#   spec_lam  — порог яркости: чем выше, тем меньше площадь блика.
#               Сталь — узкий всполох, кожа — широкое мягкое пятно.
#   spec_glow — ослабление блика в глубине формы (на изгибе его размывает).
#
SPEC_CLOTH = dict(spec=None, spec_lam=0.95, spec_glow=0.0)
SPEC_SKIN = dict(spec="#F0CFAE", spec_lam=0.62, spec_glow=0.9)
SPEC_STEEL = dict(spec="#E6EEFF", spec_lam=0.86, spec_glow=0.4)


def mat(hex_hi, hex_base, hex_dark, spec=None, spec_lam=0.86, spec_glow=0.4):
    """Материал: три тона села плюс необязательный блик."""
    m = {"ramp": ramp(hex_hi, hex_base, hex_dark),
         "spec": hex_rgba(spec) if spec else None,
         "spec_lam": spec_lam, "spec_glow": spec_glow}
    return m


def mat_ramp(m):
    """Три тона материала (совместимость со старыми вызовами ramp)."""
    return m["ramp"] if isinstance(m, dict) else m


def mat_spec(m):
    if isinstance(m, dict) and m["spec"] is not None:
        return m["spec"], m["spec_lam"], m["spec_glow"]
    return None, 0.0, 0.0


MATERIALS = {
    "tetrax": {
        # Монах-киборд в капюшоне: индиго- ряса, стальные наплечники,
        # тёмное лицо в тени капюшона с режущим визором-светодиодом.
        "robe": mat("#3A4468", "#242B44", "#141A2A", None, 0.95, 0.0),
        # Броня — тёмная синяя сталь. Прежняя была почти светло-серой (#8A96B4),
        # и на тёмной рясе каждая пластина (наплечник, наколенник, перчатка)
        # читалась как отдельное яркое пятно, разбивая силуэт.
        "armor": mat("#5F6C8C", "#3C465E", "#222A3C", "#E8F0FF", 0.84, 0.45),
        "skin": mat("#E0AE86", "#B98A66", "#7E5C46", "#F0CFAE", 0.60, 0.95),
        "eye": hex_rgba("#3FE0F0"),            # радужка: светлее неона, но не белая
        "neon": hex_rgba("#00FFFF"),
        "neon_soft": hex_rgba("#0A6E78"),
    },

    # MATRIX — стрит-рейсер. Фиолетово-пурпурная куртка, чёрная кожа,
    # хромированные заклёпки. Палитра холоднее и пёстрее монашеской.
    "matrix": {
        "robe": mat("#4A3A6E", "#2E2450", "#18122E", None, 0.95, 0.0),
        "armor": mat("#B08CD8", "#6E52A0", "#3A2A5C", "#E8F0FF", 0.84, 0.45),
        "skin": mat("#C89A7A", "#9A7256", "#644838", "#F0CFAE", 0.60, 0.95),
        "eye": hex_rgba("#FF6BFF"),
        "neon": hex_rgba("#FF00FF"),
        "neon_soft": hex_rgba("#6E0F6E"),
    },

    # OVERLORD — хэви-мек. Ржаво-янтарный металл, почти без органики:
    # кожи нет вовсе, лицо закрыто монокуляром.
    # Ряса поднята в светлее стали-брони: тело хэви-мека — это МЕТАЛЛ,
    # и при тёмной рясе корпус, ноги и руки сливались в чёрную массу,
    # в которой читались только светлые броневые кляксы. Броня при этом
    # осталась светлее: иначе силуэт снова стал одним янтарным пятном.
    "overlord": {
        "robe": mat("#7A5C30", "#4E3A1C", "#2A1F0E", None, 0.95, 0.0),
        "armor": mat("#C89A4E", "#8A6A2E", "#463618", "#E8F0FF", 0.84, 0.45),
        "skin": mat("#8A7048", "#5E4C2E", "#332816", "#F0CFAE", 0.60, 0.95),   # фактически не используется
        "eye": hex_rgba("#FFDD66"),
        "neon": hex_rgba("#FFAA00"),
        "neon_soft": hex_rgba("#7A5210"),
    },

    # SENTINEL — рядовой караул. Зелёный неон на серо-стальном корпусе:
    # самый «уставной» силуэт из шести, без капюшона и без рогов.
    "sentinel": {
        "robe": mat("#3E4A46", "#28302E", "#141A18", None, 0.95, 0.0),
        "armor": mat("#7A8A82", "#4E5A54", "#2A322E", "#E8F0FF", 0.84, 0.45),
        "skin": mat("#D8AC86", "#AC825E", "#705038", "#F0CFAE", 0.60, 0.95),
        "eye": hex_rgba("#6BFF8A"),
        "neon": hex_rgba("#33FF66"),
        "neon_soft": hex_rgba("#14682E"),
    },

    # WRAITH — теневой налётчик. Почти чёрный силуэт с фиолетовыми щелями
    # глаз. Поднят на тон выше, чем казалось нужным: в чистом чёрном он
    # проваливался в фон арены и деталей не оставалось вовсе.
    "wraith": {
        "robe": mat("#3E3456", "#2A2340", "#171228", None, 0.95, 0.0),
        "armor": mat("#6E5C8E", "#4A3E68", "#282038", "#E8F0FF", 0.84, 0.45),
        "skin": mat("#B89A86", "#8A7060", "#54443A", "#F0CFAE", 0.60, 0.95),
        "eye": hex_rgba("#C9A0FF"),
        "neon": hex_rgba("#9966FF"),
        "neon_soft": hex_rgba("#3E1E6E"),
    },

    # BRUTE — коренастый громила. Красный неон на окисленной стали,
    # самая широкая стойка: он должен читаться как угроза массой.
    # Ряса поднята по той же причине, что у OVERLORD: при почти чёрном
    # теле корпус и ноги сливались, и масса фигуры не читалась.
    "brute": {
        "robe": mat("#7A4444", "#502C2C", "#2C1818", None, 0.95, 0.0),
        "armor": mat("#9A625C", "#66403C", "#382220", "#E8F0FF", 0.84, 0.45),
        "skin": mat("#C09070", "#8E664C", "#5A402E", "#F0CFAE", 0.60, 0.95),
        "eye": hex_rgba("#FF7A7A"),
        "neon": hex_rgba("#FF3333"),
        "neon_soft": hex_rgba("#7A1414"),
    },
}


# --- Архетипы ----------------------------------------------------------------
# Палитра отвечает только за цвет. Форму задаёт архетип: без него шестеро
# бойцов отличались бы только цветом и читались как перекрашенный клон.
#
#   head    — построение головы (см. функции head_*)
#   torso   — taper (сужается к талии) | box (прямоугольник куртки)
#             | wide (широкие плечевые плиты)
#   hand    — fist (кулак) | hammer (кувалда) | claw (когти)
#   foot    — boot (сапог) | wedge (плоский клин) | block (плита)
#
ARCH = {
    # TETRAX — кибер-монах (GDD §2.1): капюшон, ряса, послушные пропорции.
    # grip=wrap: GDD §2.1 прямо требует «кисти выделены ромбами, символизирующими
    # обмотки», то есть ткань. Стальная кисть давала светлое пятно на шее.
    "tetrax":   dict(head="hood",    SHOULDER=24, WAIST=18, ARM=6, LEG=8,
                     torso="taper", hand="fist",   foot="boot", grip="wrap",
                     ankle=0.34),
    # MATRIX — стрит-рейсер (GDD §2.2): оверсайз-куртка, тактический визор,
    # тонкие стремительные конечности, тяжёлые стопы-трапеции. Перчатка в броне.
    "matrix":   dict(head="visor",   SHOULDER=28, WAIST=26, ARM=5, LEG=6,
                     torso="box",   hand="fist",   foot="wedge", grip="plate",
                     ankle=0.38),
    # OVERLORD — хэви-мек (GDD §2.3): монокуляр вместо лица, широкий торс
    # с реактором, сверхкороткие массивные ноги, руки-кувалды.
    # ankle=0.70: при фиксированном сужении нога сходила в 4 px под
    # корпусом шириной 34 — и тяжёлый боец читался как спички на троне.
    "overlord": dict(head="monocle", SHOULDER=34, WAIST=28, ARM=9, LEG=12,
                     torso="wide",  hand="hammer", foot="block", grip="plate",
                     ankle=0.70),
    # SENTINEL — первый противник кампании: рядовой щитоносец в шлеме.
    # Нейтральный силуэт без узнаваемой детали — «эталон», с которым
    # сравниваются остальные пять.
    "sentinel": dict(head="helm",    SHOULDER=26, WAIST=20, ARM=6, LEG=8,
                     torso="taper", hand="fist",   foot="boot", grip="plate",
                     ankle=0.36),
    # WRAITH — второй противник: худощавый налётчик в маске-капюшоне.
    # Самая узкая талия из шестерых — контраст с OVERLORD и BRUTE.
    # grip=wrap: кисти голые, под ними только тканевые обмотки.
    "wraith":   dict(head="mask",    SHOULDER=22, WAIST=15, ARM=5, LEG=7,
                     torso="taper", hand="claw",   foot="boot", grip="wrap",
                     ankle=0.32),
    # BRUTE — четвёртый противник: широкий громила с шипами на наплечниках.
    # Нога массивная, но сужается сильнее, чем у OVERLORD: он всё-таки
    # ниже ростом, и полные параллельные ноги слили бы его с хэви-меком.
    "brute":    dict(head="spike",   SHOULDER=30, WAIST=26, ARM=8, LEG=11,
                     torso="wide",  hand="fist",   foot="block", grip="plate",
                     ankle=0.58),
}


# --- Поле расстояний и нормаль ----------------------------------------------

def distance_field(mask, maxd=12):
    """Приблизительное расстояние до края маски (chamfer 3-4), обрезано maxd.

    0 — пиксель на границе, 1 — следующий внутрь, и так далее.
    """
    inf = maxd + 1
    d = [[inf] * mask.w for _ in range(mask.h)]
    for y in range(mask.h):
        for x in range(mask.w):
            if mask.has(x, y):
                d[y][x] = 0 if (
                    not mask.has(x - 1, y) or not mask.has(x + 1, y)
                    or not mask.has(x, y - 1) or not mask.has(x, y + 1)
                ) else inf
    # Прямой проход (сверху-слева) и обратный (снизу-справа).
    for y in range(mask.h):
        for x in range(mask.w):
            if d[y][x] == 0:
                continue
            best = d[y][x]
            for dx, dy, w in ((-1, 0, 1), (0, -1, 1), (-1, -1, 2), (1, -1, 2)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < mask.w and 0 <= ny < mask.h:
                    best = min(best, d[ny][nx] + w)
            d[y][x] = min(best, maxd)
    for y in range(mask.h - 1, -1, -1):
        for x in range(mask.w - 1, -1, -1):
            if d[y][x] == 0:
                continue
            best = d[y][x]
            for dx, dy, w in ((1, 0, 1), (0, 1, 1), (1, 1, 2), (-1, 1, 2)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < mask.w and 0 <= ny < mask.h:
                    best = min(best, d[ny][nx] + w)
            d[y][x] = min(best, maxd)
    return d


# Свет сверху-слева и чуть спереди.
LIGHT = (-0.55, -0.83, 0.35)


def _lambert(nx, ny, nz, depth):
    """Освещённость нормали с поправкой на глубину формы.

    Плюс «выпуклость» из поля расстояний: чем глубже пиксель внутри части,
    тем сильнее он уходит в тень — иначе трубы выглядят плоскими.
    """
    length = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    lam = (nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]) / length
    return lam - min(depth, 6) * 0.055


def _shade_band(lam):
    """Индекс полосы тона: 0 — свет, 1 — основной, 2 — тень."""
    if lam > 0.42:
        return 0
    if lam > -0.18:
        return 1
    return 2


def blend(a, b, t):
    """Смешение двух RGBA-цветов."""
    return (round(a[0] + (b[0] - a[0]) * t),
            round(a[1] + (b[1] - a[1]) * t),
            round(a[2] + (b[2] - a[2]) * t),
            round(a[3] + (b[3] - a[3]) * t))


def cel_fill(canvas, mask, material, depth_scale=1.0):
    """Заливает маску сел-шейдингом по полю расстояний.

    Поверх трёх тонов кладётся блик материала (см. mat()): сталь ловит
    свет узким всполохом, кожа — широким мягким пятном, ткань не ловит
    вовсе. Именно это различает материалы, а не тон заливки.

    Блик требует согласия соседей: поле расстояний шумит, `lam` скачет
    пиксель к пикселю, и одиночный порог давал не блик, а светлые брызги
    по сапогам и коленям. Условие «хотя бы два соседа тоже блестят»
    убирает изолированные точки.
    """
    d = distance_field(mask)
    hi, base, dark = mat_ramp(material)
    spec, spec_lam, spec_glow = mat_spec(material)
    tones = (hi, base, dark)

    # Освещённость считаем целиком ДО заливки: блику нужно смотреть на
    # соседей, а не на уже залитый канвас.
    w, h = mask.w, mask.h
    lam = [[-9.0] * w for _ in range(h)]
    thr = [[9.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if not mask.has(x, y):
                continue
            depth = d[y][x]
            # Градиент поля расстояний -> приближённая нормаль трубы.
            gx = d[y][min(x + 1, w - 1)] - d[y][max(x - 1, 0)]
            gy = d[min(y + 1, h - 1)][x] - d[max(y - 1, 0)][x]
            nx, ny = -gx, -gy
            nz = 2.2 + depth * 0.55 * depth_scale
            lam[y][x] = _lambert(nx, ny, nz, depth)
            # Порог падает с глубиной: на изгибе подсветка шире, чем на
            # плоскости, — так блик обтекает форму.
            thr[y][x] = spec_lam - spec_glow * 0.05 * min(depth, 8)

    def glistening(x, y):
        return mask.has(x, y) and lam[y][x] > thr[y][x]

    for y in range(h):
        for x in range(w):
            if not mask.has(x, y):
                continue
            canvas.set(x, y, tones[_shade_band(lam[y][x])])
            if spec is None or not glistening(x, y):
                continue
            if (glistening(x - 1, y) + glistening(x + 1, y)
                    + glistening(x, y - 1) + glistening(x, y + 1)) >= 2:
                # Блик берётся от светлого тона материала, а не от белого:
                # чистый белый на тёмной палитре читается как пыль, не как свет.
                canvas.set(x, y, blend(hi, spec, 0.55))


def edge_of(mask):
    """Пиксели на внешней границе маски."""
    out = Mask(mask.w, mask.h)
    for y in range(mask.h):
        for x in range(mask.w):
            if mask.has(x, y) and not (
                mask.has(x - 1, y) and mask.has(x + 1, y)
                and mask.has(x, y - 1) and mask.has(x, y + 1)
            ):
                out.add(x, y)
    return out


def rim_light(canvas, mask, color, strength=1.0, y_limit=None):
    """Контровой свет по кромке со стороны источника света.

    `y_limit` ограничивает эффект сверху. Без него кромка таза давала
    горизонтальную полосу прямо через грудь — свет должен ловить только
    верх силуэта (голова, плечи), а не любой горизонтальный край детали.
    """
    e = edge_of(mask)
    for y in range(mask.h):
        if y_limit is not None and y > y_limit:
            break
        for x in range(mask.w):
            if not e.has(x, y):
                continue
            # Свет падает сверху-слева -> ловим верхнюю и левую кромку.
            if not mask.has(x, y - 1) or not mask.has(x - 1, y):
                canvas.set(x, y, color)
            elif strength > 0.9:
                canvas.set(x, y, color)


def inner_fold(canvas, mask, x0, y0, x1, y1, color, thick=1):
    """Тёмная складка ткани: тонкая линия внутри части."""
    steps = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
    for i in range(steps + 1):
        t = i / steps
        x = int(round(x0 + (x1 - x0) * t))
        y = int(round(y0 + (y1 - y0) * t))
        for k in range(thick):
            if mask.has(x, y + k):
                canvas.set(x, y + k, color)


# --- Позы (те же покадрово, что в gen_fighters.py, но в новом масштабе) -------

def base_pose():
    """Боевая стойка «боком», лицом вправо.

    Стойка намеренно асимметрична. Симметричный силуэт — это блоб: он
    читается хуже всего, потому что у зеркальной фигуры нет ведущего
    плеча, нет направления взгляда и нет опорной ноги. Здесь:
      * ведущее плечо выдвинуто вперёд и на полпикселя выше дальнего,
        дальнее опущено — линия плеч наклонена, стойка «собирается»;
      * таз скошен: переднее бедро выше заднего;
      * вес на задней ноге — она согнута, передняя выпрямлена и несёт
        направление удара;
      * руки в защите у подбородка, а не висят вдоль тела: опущенная
        рука читалась как лента, а согнутая задаёт силуэт вышки.
    """
    return {
        "head": (41, 15),
        "neck": (40, 28),
        "chest": (40, 40),
        "hip": (40, 64),
        # Дальняя рука / нога: согнута, вес на ней.
        "sh_b": (33, 44), "el_b": (29, 59), "fist_b": (36, 49),
        "hip_b": (37, 66), "knee_b": (31, 93), "foot_b": (24, 123),
        # Ближняя рука / нога: ведущая, выпрямленная, у подбородка.
        "sh_f": (48, 41), "el_f": (54, 56), "fist_f": (49, 45),
        "hip_f": (43, 63), "knee_f": (48, 92), "foot_f": (55, 122),
    }


def shifted(pose, dx=0, dy=0, part=None):
    """Сдвигает суставы позы. part — имя сустава, список имён или None (все)."""
    out = dict(pose)
    if part is None:
        keys = list(pose.keys())
    elif isinstance(part, str):
        keys = [part]
    else:
        keys = list(part)
    for key in keys:
        x, y = out[key]
        out[key] = (x + dx, y + dy)
    return out


def shift_pose(pose, dx, dy=0):
    """Сдвигает все суставы позы разом."""
    out = dict(pose)
    for key, (x, y) in out.items():
        out[key] = (x + dx, y + dy)
    return out


def _ik(sx, sy, tx, ty, l1, l2, bend=1.0):
    """Промежуточный сустав двухзвенной конечности (2-link IK).

    Позы задаются целью кисти или стопы, а не сдвигом каждого сустава
    по отдельности. Цепочка shifted-вызовов не держит длину конечности:
    локоть уезжал, рука в кадре удара то укорачивалась, то вытягивалась
    непропорционально. Здесь длины звеньев берутся из базовой позы и
    сохраняются по построению.
    """
    dx, dy = tx - sx, ty - sy
    d = math.hypot(dx, dy)
    if d < 1e-6:
        return sx + l1, sy
    # Цель дальше суммы звеньев недосяжима — прижимаем к прямой руке.
    d = min(d, (l1 + l2) * 0.999)
    ux, uy = dx / math.hypot(dx, dy), dy / math.hypot(dx, dy)
    cos_a = (d * d + l1 * l1 - l2 * l2) / (2.0 * d * l1)
    a = math.acos(max(-1.0, min(1.0, cos_a)))
    ca, sa = math.cos(a), math.sin(a) * bend
    return sx + l1 * (ux * ca - uy * sa), sy + l1 * (ux * sa + uy * ca)


def _seg_len(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def aim(pose, side, tx, ty, bend=1.0):
    """Наводит руку (`side` = 'f' ближняя, 'b' дальняя) на точку кисти."""
    sh, el, fist = pose["sh_" + side], pose["el_" + side], pose["fist_" + side]
    out = dict(pose)
    out["el_" + side] = _ik(sh[0], sh[1], tx, ty,
                             _seg_len(sh, el), _seg_len(el, fist), bend)
    out["fist_" + side] = (tx, ty)
    return out


def step(pose, side, tx, ty, bend=1.0):
    """Наводит ногу на точку опоры стопы."""
    hip, knee, foot = pose["hip_" + side], pose["knee_" + side], pose["foot_" + side]
    out = dict(pose)
    out["knee_" + side] = _ik(hip[0], hip[1], tx, ty,
                              _seg_len(hip, knee), _seg_len(knee, foot), bend)
    out["foot_" + side] = (tx, ty)
    return out


# --- Опорная стойка и библиотека движений -----------------------------------
# Анимации описаны не функциями, а данными в data/animations.json: кадр — это
# список операций над скелетом (сдвиг сустава, вытянуть кисть или стопу с
# расчётом промежуточного сустава). Смысл решения прямой: новая анимация
# добавляется правкой файла, а не новой функцией Python, и одна анимация
# работает на любом теле — цели задаются смещениями от опорной стойки, а не
# абсолютными пикселями кадра.

RIG_STANCE = {
    "head": (41, 15),
    "neck": (40, 28),
    "chest": (40, 40),
    "hip": (40, 64),
    # Дальняя рука / нога: согнута, вес на ней.
    "sh_b": (33, 44), "el_b": (29, 59), "fist_b": (36, 49),
    "hip_b": (37, 66), "knee_b": (31, 93), "foot_b": (24, 123),
    # Ближняя рука / нога: ведущая, выпрямленная, у подбородка.
    "sh_f": (48, 41), "el_f": (54, 56), "fist_f": (49, 45),
    "hip_f": (43, 63), "knee_f": (48, 92), "foot_f": (55, 122),
}


def load_anim_library(path=None) -> dict:
    """Читает библиотеку движений (data/animations.json)."""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "animations.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


ANIM_LIBRARY = load_anim_library()


def anim_names():
    """Все анимации библиотеки, в порядке появления в файле."""
    return list(ANIM_LIBRARY["anims"].keys())


def anim_info(name):
    entry = ANIM_LIBRARY["anims"].get(name)
    if entry is None:
        raise KeyError("анимация '%s' неизвестна" % name)
    return entry


def apply_ops(stance, ops):
    """Применяет операции кадра к опорной стойке -> поза.

    Операции идут строго по порядку: длина конечности берётся из текущей позы,
    поэтому сдвиг корпуса до постановки кисти меняет, куда дотянется рука. Это
    ровно то поведение, ради которого удар и наклон записывались отдельными
    вызовами.
    """
    pose = dict(stance)
    for op in ops:
        if "shift" in op:
            spec = op["shift"]
            # "part" — один сустав, "parts" — несколько сразу. Второе нужно там,
            # где части тела двигаются вместе, а ноги стоят: приседание опускает
            # корпус, но стопы остаются на полу, и общий сдвиг утащил бы их
            # под кадр.
            part = spec.get("part") or (tuple(spec["parts"]) if "parts" in spec else None)
            pose = shifted(pose, spec.get("dx", 0), spec.get("dy", 0), part)
        elif "aim" in op:
            spec = op["aim"]
            side = spec["side"]
            home = stance["fist_" + side]
            pose = aim(pose, side, home[0] + spec["d"][0], home[1] + spec["d"][1],
                       spec.get("bend", 1.0))
        elif "step" in op:
            spec = op["step"]
            side = spec["side"]
            home = stance["foot_" + side]
            pose = step(pose, side, home[0] + spec["d"][0], home[1] + spec["d"][1],
                        spec.get("bend", 1.0))
        else:
            raise ValueError("неизвестная операция кадра: %r" % (op,))
    return pose


def build_anims(names=None, stance=None):
    """-> [(имя, [поза, ...])]. По умолчанию — шестеро базовых движений."""
    stance = stance or RIG_STANCE
    names = names or ANIM_LIBRARY["default"]
    return [(name, [apply_ops(stance, ops) for ops in anim_info(name)["frames"]])
            for name in names]


# Базовый набор: те же шесть движений и тот же порядок, что и раньше. Всё, что
# обращается к ANIMS, получает привычные 18 кадров; остальное может звать
# build_anims(names) со своим набором из библиотеки.
ANIMS = build_anims()


# --- Построение голов по архетипам ------------------------------------------
# Голова рисуется «как получится» в боксе HEAD_BOX и затем ужимается до
# HEAD_K. Так пропорции не разъезжаются при правке любой из шести голов:
# наивная отрисовка давала 30 px головы при росте 128 = 4.3 головы (чиби),
# а нужно ~5.5.
#
#   HEAD_K       — коэффициент ужатия; 0.71 даёт голову ~22 px в фигуре
#                  123 px, то есть ~5.5 голов
#   HEAD_CHIN_Y  — подбородок, к нему голова прижимается при ужатии
#   HEAD_BOX     — размер временного бокса, в котором строится любая голова
#
HEAD_K = 0.71
HEAD_CHIN_Y = 28
HEAD_BOX = 42
# Каждая функция получает словарь масок (robe/skin/armor/neon), центр головы
# и ширину холста, а возвращает один и тот же кортеж:
#   (brow, lash, iris, glint, neck_shadow, head_over)
# Единая сигнатура нужна рендеру: он не должен знать, какая у бойца голова,
# иначе при добавлении шестёрого пришлось бы править порядок отрисовки.
#
# Плоские маски возвращаются пустыми там, где черты лица не нужны
# (монокуляр, маска), — paint() на пустой маске ничего не пишет.

def _live_face(skin, w, fx, fy, eye_dx=4):
    """Черты живого лица: брови, ресничная линия, радужка, блик, нос, рот.

    Глаз намеренно узкий — широкий прямоугольник читался как гогглы.
    """
    brow, lash, iris, glint = Mask(w, FH), Mask(w, FH), Mask(w, FH), Mask(w, FH)
    for ex in (fx - eye_dx, fx + eye_dx):
        brow.limb((ex - 3, fy - 6), (ex + 3, fy - 6), 0.6)
        lash.limb((ex - 2, fy - 3), (ex + 2, fy - 2), 0.7)
        iris.rect(ex - 1, fy - 2, ex, fy - 1)
        glint.add(ex - 1, fy - 2)
    brow.add(fx, fy + 2)                                      # нос — одна тень
    brow.limb((fx - 2, fy + 5), (fx + 1, fy + 5), 0.5)        # рот
    return brow, lash, iris, glint


def _blank(w):
    return Mask(w, FH)


def head_hood(m, hx, hy, w):
    """TETRAX — капюшон с козырьком, лицо открыто (GDD §2.1)."""
    robe, skin = m["robe"], m["skin"]
    robe.ellipse(hx, hy, 14, 15)
    robe.ellipse(hx - 9, hy + 9, 6, 8)                  # задняя складка
    for ox, oy, ln in ((-11, -1, 3), (-12, 3, 3)):     # пряди из-под капюшона
        robe.limb((hx + ox, hy + oy), (hx + ox - 2, hy + oy + ln), 2)
    fx, fy = hx + 3, hy + 4
    skin.ellipse(fx, fy, 8, 9)
    brow, lash, iris, glint = _live_face(skin, w, fx, fy)
    neck_shadow = _blank(w)
    neck_shadow.ellipse(fx, fy + 9, 6, 2)
    over = _blank(w)
    # Козырёк поднят на 3 px: ниже он срезал лоб, и лицо выглядело лысым.
    over.poly([(hx - 12, hy - 9), (hx + 11, hy - 9), (hx + 10, hy - 2), (hx - 10, hy - 2)])
    over.ellipse(hx - 12, hy - 7, 4, 6)
    return brow, lash, iris, glint, neck_shadow, over


def head_visor(m, hx, hy, w):
    """MATRIX — вытянутый овал и прямоугольный тактический визор (GDD §2.2)."""
    robe, skin, neon = m["robe"], m["skin"], m["neon"]
    robe.ellipse(hx, hy, 12, 16)                       # узкий и высокий
    robe.poly([(hx - 12, hy - 6), (hx - 16, hy + 6), (hx - 11, hy + 11)])  # затылок
    fx, fy = hx + 2, hy + 5
    skin.ellipse(fx, fy, 7, 10)
    brow, lash, iris, glint = _live_face(skin, w, fx, fy, eye_dx=3)
    # Прямоугольный визор поверх глаз — узкая горизонтальная щель.
    neon.rect(hx - 8, fy - 4, hx + 8, fy - 3)
    neon.disc(hx + 6, fy - 4, 1)
    neck_shadow = _blank(w)
    neck_shadow.ellipse(fx, fy + 10, 5, 2)
    over = _blank(w)
    over.ellipse(hx - 12, hy - 4, 3, 5)                 # козырёк-кепка
    return brow, lash, iris, glint, neck_shadow, over


def head_monocle(m, hx, hy, w):
    """OVERLORD — массивный квадрат с горящей точкой вместо лица (GDD §2.3).

    Кожи нет: у Хэви-Мека нет лица, и любая попытка нарисовать глаза
    сбивала бы его с прочитанного образа.
    """
    armor, neon = m["armor"], m["neon"]
    armor.rect(hx - 13, hy - 13, hx + 11, hy + 12)    # голова-коробка
    armor.rect(hx - 15, hy - 6, hx - 11, hy + 6)      # боковые наушники
    armor.rect(hx + 9, hy - 6, hx + 13, hy + 6)
    armor.rect(hx - 10, hy + 12, hx + 8, hy + 17)     # нижняя челюсть-блок
    neon.disc(hx - 1, hy - 1, 2)                      # монокуляр
    neon.limb((hx - 1, hy + 2), (hx - 1, hy + 11), 0.5)  # световая полоса
    return _blank(w), _blank(w), _blank(w), _blank(w), _blank(w), _blank(w)


def head_helm(m, hx, hy, w):
    """SENTINEL — гладкий шлем с горизонтальной щелью визора."""
    robe, skin, armor, neon = m["robe"], m["skin"], m["armor"], m["neon"]
    armor.ellipse(hx, hy, 13, 14)                     # купол
    armor.rect(hx - 12, hy - 1, hx + 12, hy + 2)      # кромка визора
    armor.poly([(hx - 11, hy + 2), (hx + 11, hy + 2),
                (hx + 9, hy + 11), (hx - 9, hy + 11)])  # забрало
    fx, fy = hx + 3, hy + 3
    skin.ellipse(fx, fy, 7, 8)
    brow, lash, iris, glint = _live_face(skin, w, fx, fy, eye_dx=3)
    neon.rect(hx - 8, fy - 3, hx + 8, fy - 3)         # щель визора
    neck_shadow = _blank(w)
    neck_shadow.ellipse(fx, fy + 8, 5, 2)
    over = _blank(w)
    over.rect(hx - 13, hy - 10, hx + 12, hy - 6)      # гребень шлема
    return brow, lash, iris, glint, neck_shadow, over


def head_mask(m, hx, hy, w):
    """WRAITH — капюшон и гладкая маска с двумя щелями глаз."""
    robe, skin, armor, neon = m["robe"], m["skin"], m["armor"], m["neon"]
    robe.ellipse(hx, hy, 14, 15)
    robe.ellipse(hx - 10, hy + 8, 5, 7)                # капюшон сзади
    skin.ellipse(hx + 3, hy + 4, 7, 9)                # лицо под маской
    armor.ellipse(hx + 3, hy + 4, 7, 9)               # сама маска — отдельный материал
    neon.limb((hx, hy + 1), (hx, hy + 4), 0.6)         # вертикальные щели глаз
    neon.limb((hx + 6, hy + 1), (hx + 6, hy + 4), 0.6)
    # Черт лица нет намеренно: у WRAITH за маской ничего не разглядеть.
    brow = lash = iris = glint = neck_shadow = _blank(w)
    over = _blank(w)
    over.poly([(hx - 12, hy - 9), (hx + 11, hy - 9), (hx + 10, hy - 1), (hx - 10, hy - 1)])
    return brow, lash, iris, glint, neck_shadow, over


def head_spike(m, hx, hy, w):
    """BRUTE — тяжёлая голова с шипами и узким визором."""
    robe, skin, armor, neon = m["robe"], m["skin"], m["armor"], m["neon"]
    armor.ellipse(hx, hy, 14, 13)
    for ox in (-11, 11):                              # шипы по бокам шлема
        armor.poly([(hx + ox - 3, hy - 4), (hx + ox + 3, hy - 4),
                    (hx + ox + (3 if ox > 0 else -3), hy + 2)])
    armor.rect(hx - 11, hy + 8, hx + 9, hy + 13)      # челюстная плита
    fx, fy = hx + 3, hy + 3
    skin.ellipse(fx, fy, 8, 8)
    brow, lash, iris, glint = _live_face(skin, w, fx, fy, eye_dx=3)
    neon.rect(hx - 7, fy - 3, hx + 9, fy - 3)
    neck_shadow = _blank(w)
    neck_shadow.ellipse(fx, fy + 8, 5, 2)
    over = _blank(w)
    over.rect(hx - 14, hy - 11, hx + 13, hy - 8)      # надлобье
    return brow, lash, iris, glint, neck_shadow, over


HEAD_BUILDERS = {
    "hood": head_hood, "visor": head_visor, "monocle": head_monocle,
    "helm": head_helm, "mask": head_mask, "spike": head_spike,
}


def _shrink_into(dst, src, c, hx, hy, chin, k):
    """Переносит маску из временного бокса в кадр, ужимая её в k раз.

    Пиксель бокса (x, y) соответствует реальной точке (hx + x - c, hy + y - c).
    Ужатие идёт к подбородку: масштабирование «в ноль» увело бы голову от
    шеи и оставило бы щель между капюшоном и плечами.
    """
    for y in range(src.h):
        for x in range(src.w):
            if not src.has(x, y):
                continue
            rx = hx + (x - c)
            ry = hy + (y - c)
            dst.add(hx + int(round((rx - hx) * k)),
                    chin - int(round((chin - ry) * k)))


def build_head(spec, m, hx, hy, w):
    """Рисует голову во временный бокс и ужимает до HEAD_K.

    Наивная отрисовка давала голову 30 px при росте 128 — это 4.3 головы,
    то есть чиби. Боец должен быть около 5.5 голов (GDD §2.1), поэтому
    голова ужимается целиком. Подбородок остаётся на месте: иначе шея
    оторвётся от корпуса.
    """
    box, c = HEAD_BOX, HEAD_BOX // 2
    tmp = {"robe": Mask(box, FH), "skin": Mask(box, FH),
           "armor": Mask(box, FH), "neon": Mask(box, FH)}
    brow, lash, iris, glint, neck_shadow, over = HEAD_BUILDERS[spec.arch["head"]](
        tmp, c, c, box)
    for key in ("robe", "skin", "armor", "neon"):
        _shrink_into(m[key], tmp[key], c, hx, hy, HEAD_CHIN_Y, HEAD_K)
    out = []
    for src in (brow, lash, iris, glint, neck_shadow, over):
        dst = Mask(w, FH)
        _shrink_into(dst, src, c, hx, hy, HEAD_CHIN_Y, HEAD_K)
        out.append(dst)
    return tuple(out)


def draw_leg(cloth, plate, hip, knee, foot, leg, style="boot", shrink=0.0, ankle=0.34):
    """Нога: бедро сужается к колену, икроножная бугрится и сходит к щиколотке.

    Раньше бедро и голень рисовались limb() — капсулами постоянной ширины, —
    и нога читалась как труба. На пятиголовах фигуре это особенно заметно:
    глаз ждёт сужения к щиколотке, и труба выдаёт себя формой.

    Радиусы — доли от `leg` (радиус бедра):
        бедро        1.00 -> 0.72
        верх голени  0.86 -> 0.64   бугор икроножной мышцы
        низ голени   0.64 -> ankle   сужение к щиколотке

    `ankle` задаёт архетип. Лёгкие бойцы сходят к 0.32-0.38, и нога читается
    как нога. Тяжёлые (OVERLORD 0.70, BRUTE 0.58) почти не сужаются: при
    едином коэффициенте хэви-мек получал 4 px щиколотку под корпусом
    шириной 34 и выглядел как спички на троне.

    `shrink` уменьшает всю ногу — так рисуется дальняя, отодвинутая вглубь.
    """
    k = 1.0 - 0.12 * shrink

    def r(f):
        return max(1.0, leg * f * k)

    hx, hy = hip
    nx, ny = knee
    fx_, fy_ = foot

    # Бедро: от таза к колену.
    cloth.taper((hx, hy), (nx, ny), r(1.00), r(0.72))
    cloth.disc(hx, hy, r(0.88))                        # бедро у таза

    # Голень двумя участками: верхний шире нижнего — это и есть бугор.
    mid = (nx + (fx_ - nx) * 0.32, ny + (fy_ - ny) * 0.32)
    cloth.taper((nx, ny), mid, r(0.86), r(0.64))
    cloth.taper(mid, (fx_, fy_ - 3), r(0.64), r(ankle))
    cloth.disc(nx, ny, r(0.74))                        # колено сглаживает сгиб

    if plate is None:
        return          # дальняя нога в тени, броня ей не нужна

    if style == "wedge":
        # MATRIX: тяжёлый горизонтальный клин (GDD §2.2).
        plate.poly([(fx_ - leg + 2, fy_ - 3), (fx_ + leg + 2, fy_ - 3),
                    (fx_ + leg + 3, fy_ + 2), (fx_ - leg + 1, fy_ + 2)])
    elif style == "block":
        # OVERLORD / BRUTE: короткая массивная нога, опора широкой плитой.
        plate.taper(mid, (fx_, fy_ - 1), r(0.78), r(0.62))
        plate.rect(fx_ - leg, fy_ - 2, fx_ + leg + 1, fy_ + 3)
    else:
        # Сапог. Раньше он начинался ровно от щиколотки и висел отдельным
        # пятном, а выше стоял тонкий тёмный шнур: нога читалась как три
        # разных предмета. Теперь голенище поднимается вверх по икре и
        # смыкается с ней.
        boot_top = fy_ - max(4, int(leg * 0.9))
        plate.taper((fx_, boot_top), (fx_, fy_), r(ankle) + 0.5, r(0.58))
        plate.disc(fx_, fy_, r(0.58))
        plate.limb((fx_ - 2, fy_), (fx_ + 4, fy_), max(1.0, leg * 0.40))
        # Наколенник — маленькая пластина НА суставе. Прежний начинался от
        # колена и уходил на 6 px вниз по икре, отчего нога выглядела так,
        # будто на голень намотали баннер.
        plate.disc(nx, ny, r(0.48))


def build_parts(pose, spec, w=FW):
    """Возвращает маски по слоям и материалам.

    Пропорции, форма торса, кисти, стопы и голова берутся из спецификации:
    без этого шестеро бойцов отличались бы только цветом.

    Ширина `w` передаётся явно: черновой проход рендера рисует на холсте
    с запасом по бокам, чтобы измерить вылет конечности за кадр.

    Дальние конечности вынесены в отдельную маску `far`: раньше они попадали
    в общий силуэт и сливались с корпусом — фигура теряла объём.
    """
    a = spec.arch
    ARM, LEG = a["ARM"], a["LEG"]
    SHOULDER, WAIST = a["SHOULDER"], a["WAIST"]
    far = Mask(w, FH)
    robe = Mask(w, FH)
    armor = Mask(w, FH)
    skin = Mask(w, FH)
    neon = Mask(w, FH)
    arm = Mask(w, FH)
    armor_arm = Mask(w, FH)

    # --- Дальняя нога и рука ---
    # Сапог дальней ноги рисуется в тот же `far`: форма щиколотки должна
    # читаться и в тени, иначе дальняя нога вырождается в тёмную полосу.
    draw_leg(far, far, pose["hip_b"], pose["knee_b"], pose["foot_b"],
             LEG - 1, a["foot"], shrink=0.6, ankle=a["ankle"])
    far.limb(pose["sh_b"], pose["el_b"], ARM - 2)
    far.limb(pose["el_b"], pose["fist_b"], ARM - 2)
    far.disc(pose["fist_b"][0], pose["fist_b"][1], ARM - 1)

    # --- Ближняя нога ---
    # Наколенник и сапог идут в тот же материал, что и кисть (см. grip):
    # у «обмоточных» архетипов нога тканевая, и стальной наколенник на
    # тёмной рясе читался как наклейка, а не как часть ноги. Форма стопы
    # при этом остаётся: она видна силуэтом.
    leg_plate = robe if a["grip"] == "wrap" else armor
    draw_leg(robe, leg_plate, pose["hip_f"], pose["knee_f"], pose["foot_f"],
             LEG, a["foot"], ankle=a["ankle"])

    # --- Торс: форма задана архетипом ---
    cx = pose["chest"][0]
    top = pose["sh_f"][1] - 4
    bot = pose["hip"][1] + 4
    if a["torso"] == "box":
        # Оверсайз-куртка: почти прямоугольный силуэт без талии (GDD §2.2).
        robe.poly([(cx - SHOULDER // 2, top), (cx + SHOULDER // 2, top),
                   (cx + WAIST // 2 + 2, bot - 4), (cx + WAIST // 2 + 2, bot),
                   (cx - WAIST // 2 - 2, bot), (cx - WAIST // 2 - 2, bot - 4)])
    elif a["torso"] == "wide":
        # Хэви-мек: плечевые плиты в обхват торса, талия узкая (GDD §2.3).
        robe.poly([(cx - SHOULDER // 2, top), (cx + SHOULDER // 2, top),
                   (cx + WAIST // 2, bot - 8), (cx + WAIST // 2, bot),
                   (cx - WAIST // 2, bot), (cx - WAIST // 2, bot - 8)])
    else:
        robe.poly([
        (cx - SHOULDER // 2, top), (cx + SHOULDER // 2, top),
        (cx - 1 + SHOULDER // 2, top + 14),
        (cx - 1 + WAIST // 2, bot - 6), (cx - 1 + WAIST // 2 - 1, bot),
        (cx - 1 - WAIST // 2 + 1, bot), (cx - 1 - WAIST // 2, bot - 6),
        (cx - 1 - SHOULDER // 2, top + 14),
    ])
    # Затенение в складках. Без него всё тело — одна ровная заливка, и глаз
    # не находит ни подмышки, ни промежности: там, где части сходятся,
    # свет гаснет. Формы расставлены вручную: общее правило «затемнить всё
    # на стыке масок» заливало бы тенью и намеренные линии вроде воротника.
    ao = Mask(w, FH)
    sf, sb = pose["sh_f"], pose["sh_b"]
    ao.ellipse(sf[0] - 1, sf[1] + 5, 3, 2)            # подмышка ближней руки
    ao.ellipse(sb[0] + 1, sb[1] + 5, 2, 2)            # подмышка дальней
    ao.ellipse(cx, pose["hip"][1] + 3, 2, 3)          # промежность
    ao.ellipse(cx - WAIST // 2 + 1, bot - 10, 2, 2)   # под поясом
    ao.ellipse(cx + WAIST // 2 - 1, bot - 10, 2, 2)

    # Плечевые пластины ловят свет — отдельный материал. Прижаты к силуэту:
    # вынесенные наружу они читались как крылья.
    armor.ellipse(cx - SHOULDER // 2 + 5, top + 4, 6, 5)
    armor.ellipse(cx + SHOULDER // 2 - 5, top + 4, 6, 5)
    # Пояс — тканевая перевязь, не броня. Броневый пояс давал светлую полосу
    # поперёк живота, которая читалась как отдельная деталь поверх рясы.
    robe.rect(cx - WAIST // 2 - 1, bot - 8, cx + WAIST // 2 + 1, bot - 4)
    # Таз: широкий блок в основании торса. Без него ноги визуально «отходят»
    # от корпуса и вся фигура читается как жердь.
    robe.poly([
        (cx - WAIST // 2 - 3, pose["hip"][1] - 8), (cx + WAIST // 2 + 3, pose["hip"][1] - 8),
        (cx + WAIST // 2 + 1, pose["hip"][1] + 6), (cx - WAIST // 2 - 1, pose["hip"][1] + 6),
    ])

    # --- Шея, трапеции, голова по архетипу ---
    hx, hy = pose["head"]
    nx, ny = pose["neck"]
    # Шея шире и короче прежней: тонкая длинная шея отрывала голову от
    # корпуса, и персонаж читался как «голова на палке».
    robe.limb((nx, ny), (nx, ny + 6), ARM + 1)
    # Трапеции: клинья от шеи к плечам — голова «сидит» на теле, а не висит.
    robe.poly([(nx - 3, ny + 1), (nx + 3, ny + 1),
               (cx - SHOULDER // 2 + 2, top + 2), (cx + SHOULDER // 2 - 2, top + 2)])

    brow, lash, iris, glint, neck_shadow, head_over = build_head(
        spec, {"robe": robe, "skin": skin, "armor": armor, "neon": neon}, hx, hy, w)

    # Воротник рясы вместо «застёжки»: V-образный вырез от шеи к поясу.
    collar = Mask(w, FH)
    collar.limb((cx - 5, top + 1), (cx - 1, top + 11), 1.2)
    collar.limb((cx + 5, top + 1), (cx + 1, top + 11), 1.2)

    # --- Ближняя рука (поверх всего) ---
    # Вынесена в отдельные слои arm/armor_arm: пока рука лежала в общей
    # маске рясы вместе с торсом, между ними не могло быть тени — а рука
    # без тени на теле читалась как наклейка, а не как конечность.
    arm.limb(pose["sh_f"], pose["el_f"], ARM)
    arm.limb(pose["el_f"], pose["fist_f"], ARM - 1)
    arm.disc(pose["el_f"][0], pose["el_f"][1], ARM - 1)
    armor_arm.limb((pose["fist_f"][0], pose["fist_f"][1] - 9), pose["fist_f"], ARM)  # наруч
    fx_, fy_ = pose["fist_f"]
    if a["hand"] == "hammer":
        # Рука-кувалда вместо кисти (GDD §2.3): диск заметно крупнее кулака,
        # без пальцев — это молот, а не рука в перчатке.
        armor_arm.disc(fx_, fy_, ARM + 2)
        armor_arm.disc(fx_, fy_, ARM - 1)
    elif a["hand"] == "claw":
        # Когти: тонкие вытянутые когти вместо кулака.
        for i in range(3):
            armor_arm.limb((fx_ - 2 + i * 3, fy_ + 1), (fx_ - 3 + i * 3, fy_ + ARM + 2), 1.0)
    else:
        # Кисть: кулак + намёк на пальцы. Диск радиусом ARM+1 давал кляксу
        # шире запястья — рука читалась как лопата. Теперь кулак уже локтя,
        # и пальцы короче.
        armor_arm.disc(fx_, fy_, ARM - 1)
        for i in range(3):
            armor_arm.rect(fx_ - ARM + 2 + i * 2, fy_ + 2, fx_ - ARM + 2 + i * 2, fy_ + ARM - 1)
        # Хват кисти — тёмная линия поперёк, разделяет пальцы от ладони.
        armor_arm.rect(fx_ - ARM + 1, fy_ + 1, fx_ + ARM - 1, fy_ + 1)

    # --- Неоновая отделка рясы ---
    # Пояс не подсвечиваем: он и так тёмный, а неоновая полоса поверх него
    # читалась как двойная линия через грудь. Остаётся один узел на груди.
    neon.disc(cx, top + 13, 2)

    return (far, robe, skin, armor, neon, head_over, neck_shadow, arm, armor_arm,
            ao, (brow, lash, iris, glint, collar))


def render_frame(pose, spec):
    """Собирает кадр 80x128 с сел-шейдингом и материалами.

    Сначала — черновой проход на холсте с запасом по бокам. Иначе измерить
    вылет нечем: Canvas.set молча отбрасывает пиксели за границей, и
    обрезанная кисть уже не отличима от её отсутствия. Изначально рука в
    кадре удара уходила на x=63 при ширине кадра 64 и терялась.
    Получив реальную ширину, сдвигаем позу внутрь кадра: корпус при ударе
    подаётся назад — это попутно даёт правильную анимацию, а не обрезку.
    """
    probe_w = FW + 2 * PAD
    probe = _draw(shift_pose(pose, PAD), spec, Canvas(probe_w, FH), probe_w)
    lo, hi = _opaque_span(probe)
    dx = 0
    if lo < 1:
        dx = 1 - lo
    elif hi > FW - 2:
        dx = (FW - 2) - hi
    return _draw(shift_pose(pose, dx), spec, Canvas(FW, FH), FW)

# Запас по бокам чернового прохода. 24 px с каждой стороны перекрывает
# любой реальный вылет: самый длинный мах ногой уходил на ~20 px за кадр.
PAD = 24


def _opaque_span(cv):
    """Границы непрозрачных пикселей в координатах исходной позы.

    Сканирование идёт построчно, поэтому брать «первый» и «последний»
    непрозрачный пиксель нельзя: это верх строки, а не крайние точки
    силуэта. Считаем честный минимум и максимум по x.
    """
    xs = [x - PAD for y in range(cv.h) for x in range(cv.w) if cv.px[y][x][3] > 0]
    return (min(xs), max(xs)) if xs else (0, 0)


def darken(color, factor=0.55):
    """Затемняет цвет — обводка берётся из своего материала, а не чёрная."""
    return (round(color[0] * factor), round(color[1] * factor),
            round(color[2] * factor), color[3])


def _draw_outline(cv, layers):
    """Обводит силуэт и внутренние стыки.

    Без контура формы просто обрывались в фон — это и давало «векторный
    блоб», узнаваемый почерк Nuclear Throne.

    Контур рисуется ПОСЛЕ всех заливок и по порядку наложения: у части
    обводится только тот кант, который не прикрыт частью, лежащей сверху.
    Иначе контур руки рисовался бы поверх торса.

    Цвет обводки — затемнённый тон СВОЕГО материала, а не общий чёрный:
    чёрный контур на тёмной палитре проваливался в фон, а цветной держит
    форму и не даёт силуэту слипнуться.

    `layers` — список (маска, рампа из трёх тонов) в порядке наложения.
    """
    masks = [m for m, _ in layers]
    # Что закрывает каждый слой снизу: объединение всех слоёв выше него.
    above = []
    acc = None
    for m in reversed(masks):
        above.append(acc)
        acc = m if acc is None else _union(acc, m)

    for i, (m, ramp) in enumerate(layers):
        if m is None:
            continue
        ring = contour(m, 1)
        cover = above[i]
        if cover is not None:
            for y in range(ring.h):
                for x in range(ring.w):
                    if cover.has(x, y):
                        ring.cells[y][x] = False
        paint(cv, ring, darken(ramp[2]))


def _union(a, b):
    out = Mask(a.w, a.h)
    out.union(a)
    out.union(b)
    return out


def contact_shadow(canvas, caster, receiver, color, ox=1, oy=2):
    """Тень ближней конечности на корпусе.

    Без неё рука выглядит наклейкой: лежит в сантиметре от тела, но
    отбрасывает на него тени, и глаз читает её как отдельный предмет.
    Тень смещена ОТ источника света (свет сверху-слева -> тень вправо-вниз).
    """
    shadow = Mask(caster.w, caster.h)
    for y in range(caster.h):
        for x in range(caster.w):
            if caster.has(x, y) and receiver.has(x - ox, y - oy):
                shadow.add(x, y)
    paint(canvas, shadow, color)


def _draw(pose, spec, cv, w):
    mats = spec.mats
    (far, robe, skin, armor, neon, head_over, neck_shadow, arm, armor_arm,
     ao, face) = build_parts(pose, spec, w)
    brow, lash, iris, glint, collar = face

    # Порядок = порядок наложения:
    #   дальний план -> ткань -> кожа -> козырёк капюшона -> броня -> черты лица.
    # Дальний план заливается приглушённой палитрой, иначе рука и корпус
    # сливаются в одно пятно и объём пропадает. Козырёк идёт ПОСЛЕ кожи,
    # иначе капюшон не перекрывает лоб и лицо выглядит лысым.
    # Дальний план берёт на тон светлее чистой тени: при полностью чёрной
    # заливке рука и корпус сливались в одно пятно на уровне бедра.
    dark = mat_ramp(mats["robe"])[2]
    far_tone = mat_ramp(mats["robe"])[1]
    far_ramp = (far_tone, dark, dark)
    cel_fill(cv, far, far_ramp, depth_scale=0.8)
    cel_fill(cv, robe, mats["robe"], depth_scale=1.0)

    # Тень руки ложится на торс ДО заливки самой руки.
    contact_shadow(cv, arm, robe, darken(dark, 0.8))

    cel_fill(cv, arm, mats["robe"], depth_scale=0.95)
    cel_fill(cv, skin, mats["skin"], depth_scale=0.6)
    cel_fill(cv, head_over, mats["robe"], depth_scale=0.9)
    cel_fill(cv, armor, mats["armor"], depth_scale=0.8)
    # Кисть: у «обмоточных» архетипов (TETRAX, WRAITH) она тканевая и
    # заливается цветом рясы, у остальных — броневая. Стальная кисть в
    # капюшоне читалась светлым пятном и спорила с лицом за внимание.
    grip = mats["robe"] if spec.arch.get("grip") == "wrap" else mats["armor"]
    cel_fill(cv, armor_arm, grip, depth_scale=0.85)

    _draw_outline(cv, [
        (far, far_ramp), (robe, mat_ramp(mats["robe"])), (arm, mat_ramp(mats["robe"])),
        (skin, mat_ramp(mats["skin"])),
        (head_over, mat_ramp(mats["robe"])), (armor, mat_ramp(mats["armor"])),
        (armor_arm, mat_ramp(grip)),
    ])

    # Складки ткани на торсе и бедре.
    fold = mat_ramp(mats["robe"])[2]
    cx = pose["chest"][0]
    top = pose["sh_f"][1] - 4
    inner_fold(cv, robe, cx - 6, top + 12, cx - 4, pose["hip"][1], fold)
    inner_fold(cv, robe, cx + 5, top + 14, cx + 4, pose["hip"][1] - 5, fold)
    inner_fold(cv, robe, pose["hip_f"][0] - 3, pose["hip_f"][1] + 4,
               pose["knee_f"][0] - 2, pose["knee_f"][1] - 3, fold)

    # Черты лица — плоские цвета, без сел-шейдинга.
    paint(cv, neck_shadow, dark)                     # тень под подбородком
    paint(cv, collar, dark)                          # воротник рясы
    paint(cv, ao, darken(dark, 0.8))                 # складки: подмышка, промежность
    paint(cv, iris, mats["eye"])                     # радужка
    paint(cv, glint, hex_rgba("#FFFFFF"))         # блик в зрачке
    paint(cv, lash, hex_rgba("#151A28"))          # ресничная линия
    paint(cv, brow, mat_ramp(mats["robe"])[2])        # брови, нос, рот

    # Контровой свет по верхней кромке силуэта.
    union = Mask(w, FH)
    for m in (far, robe, armor):
        union.union(m)
    rim_light(cv, union, mats["neon_soft"], strength=0.0, y_limit=top + 16)

    # Неон поверх всего: визор, канты, пояс.
    for y in range(FH):
        for x in range(w):
            if neon.has(x, y):
                cv.set(x, y, mats["neon"])

    return cv

def head_extent(spec):
    """Высота головы в пикселях кадра — по её маскам, а не по силуэту.

    По силуэту мерить нельзя: у шлемных бойцов (SENTINEL, OVERLORD, BRUTE)
    под головой есть узкая щель уже шеи, и «самое узкое место ниже головы»
    находилось внутри неё — выходило 7-8 голов вместо 5.
    """
    box, c = HEAD_BOX, HEAD_BOX // 2
    tmp = {k: Mask(box, FH) for k in ("robe", "skin", "armor", "neon")}
    parts = HEAD_BUILDERS[spec.arch["head"]](tmp, c, c, box)
    # Высота считается по координатам ужатого изображения, а не по маске:
    # маска отсекает отрицательные координаты, и после сдвига в ноль
    # половина головы уходила за борт (получалось 12 px вместо 23).
    ys = [int(round((y - c) * HEAD_K))
          for m in list(tmp.values()) + list(parts)
          for y in range(m.h) for x in range(m.w) if m.has(x, y)]
    return (max(ys) - min(ys) + 1) if ys else 0


def measure_heads(spec, frame=0):
    """Реальное число голов: высота фигуры / высота головы.

    Раньше печаталось FH / 23.0 — жёстко зашитая константа. Она врала:
    нарисованная голова занимала 30 px, а не 23, и настоящая пропорция
    была 4.3 головы вместо заявленных 5.5.

    Фигура меряется по силуэту (это надёжно), голова — по маскам головы.
    """
    cv = render_frame(dict(ANIMS)["idle"][frame], spec)
    rows = [sum(1 for x in range(cv.w) if cv.px[y][x][3] > 0) for y in range(cv.h)]
    top = next(y for y, n in enumerate(rows) if n > 0)
    bot = cv.h - 1 - next(i for i, n in enumerate(reversed(rows)) if n > 0)
    figure = bot - top + 1
    head_h = head_extent(spec)
    return figure / float(head_h), head_h, figure


def build_sheet(spec, anims=None):
    """Спрайт-лист персонажа.

    anims задаёт, какие движения и в каком порядке попадут в лист: по умолчанию
    шестеро базовых. Строк столько, сколько движений, а колонок — сколько
    кадров в самом длинном (но не меньше COLS). Поэтому лист подстраивается под
    любой набор: три удара или двадцать три движения из библиотеки.
    """
    anims = anims or ANIMS
    cols = max(COLS, max(len(frames) for _name, frames in anims))
    sheet = Canvas(FW * cols, FH * len(anims))
    for row, (_name, frames) in enumerate(anims):
        for col, pose in enumerate(frames):
            sheet.blit(render_frame(pose, spec), col * FW, row * FH)
    return sheet


# Порядок персонажей в ростер-листе.
HEROES = ["tetrax", "matrix", "overlord", "sentinel", "wraith", "brute"]


# --- Спецификация персонажа --------------------------------------------------
# Персонаж = палитра + архетип. Раньше это были два глобальных словаря,
# а отрисовка ходила в них по id: MATERIALS[hero], ARCH[hero]. Для CLI это
# удобно, для сервера — ловушка: два параллельных запроса с разными
# черновиками перетирают друг другу состояние, и один из них получит чужую
# картинку. Поэтому всё рисование принимает CharSpec — самодостаточный
# снимок персонажа, — а словари остались только реестром и источником
# истины для базовых шестерых.

@dataclass(frozen=True)
class CharSpec:
    """Снимок персонажа: id + палитра (движковый формат) + архетип.

    `frozen` запрещает подменять поля, но не делает внутренние словари
    копиями: они остаются общими с реестром. Это осознанно — сборка листа
    шести раз подряд не должна каждый раз копировать палитры.
    """
    id: str
    mats: dict
    arch: dict

    @staticmethod
    def from_json(hero_id, data, base=None):
        """Собирает спецификацию из формата characters.json.

        Мержит поверх `base`, а если он не задан — поверх персонажа с тем же
        id из реестра. Отсюда документированное поведение: перечислил
        `{"neon": "#B04CFF"}` — получил того же бойца с другим неоном;
        не перечислил ничего — получил его целиком.

        Неизвестный id берёт нейтральный архетип (SENTINEL) за основу:
        палитру угадать нельзя, а пропорции безопасно заполнить значениями
        по умолчанию, иначе render упал бы на KeyError посреди работы.
        """
        if base is None:
            base = char_spec(hero_id) if hero_id in ARCH and hero_id in MATERIALS else None
        mats = dict(base.mats) if base else {}
        arch = dict(base.arch) if base else dict(ARCH_DEFAULTS)

        for key, value in (data.get("materials") or {}).items():
            if isinstance(value, dict):
                mats[key] = mat_from_json(value)
            else:
                mats[key] = hex_rgba(value)
        arch.update({k: v for k, v in (data.get("arch") or {}).items()
                     if k in ARCH_DEFAULTS})
        return CharSpec(hero_id, mats, arch)

    def to_json(self):
        """Обратно в формат characters.json — файл можно скормить CLI."""
        return {
            "materials": {k: (mat_to_json(v) if k in MAT_KEYS else _hex(v))
                          for k, v in self.mats.items() if k in MAT_KEYS + COLOR_KEYS},
            "arch": {k: self.arch[k] for k in ARCH_DEFAULTS if k in self.arch},
        }

    def register(self):
        """Кладёт персонажа в реестр MATERIALS/ARCH/HEROES (нужно CLI)."""
        MATERIALS[self.id] = self.mats
        ARCH[self.id] = self.arch
        if self.id not in HEROES:
            HEROES.append(self.id)
        return self


def char_spec(hero_id) -> CharSpec:
    """Спецификация персонажа из реестра. Единственный мост id -> рисование."""
    if hero_id not in ARCH or hero_id not in MATERIALS:
        raise KeyError("неизвестный персонаж: %s" % hero_id)
    return CharSpec(hero_id, MATERIALS[hero_id], ARCH[hero_id])


def mat_from_json(data: dict) -> dict:
    """Собирает материал из описания вида mat(hi, base, dark, spec, ...)."""
    spec = data.get("spec")
    return mat(
        data["hi"], data["base"], data["dark"],
        spec, float(data.get("spec_lam", 0.86)), float(data.get("spec_glow", 0.4)),
    )


def mat_to_json(m: dict) -> dict:
    """Движковый материал -> {"hi","base","dark","spec","spec_lam","spec_glow"}.

    spec_lam/spec_glow читаются прямо из материала, а не через mat_spec():
    тот возвращает нули, если блика нет, и параметры блика терялись бы при
    обратной сборке — материал без блика, но с его характеристиками приехал
    бы в CLI с другими значениями по умолчанию.
    """
    hi, base, dark = mat_ramp(m)
    return {"hi": _hex(hi), "base": _hex(base), "dark": _hex(dark),
            "spec": _hex(m["spec"]) if m["spec"] else None,
            "spec_lam": m["spec_lam"], "spec_glow": m["spec_glow"]}


def _hex(color) -> str:
    return "#%02X%02X%02X" % (color[0], color[1], color[2])


# Ключи материалов: с рампой (три тона + блик) и просто цвета.
MAT_KEYS = ("robe", "armor", "skin")
COLOR_KEYS = ("eye", "neon", "neon_soft")

# Архетип: перечисления для UI и границы чисел. Это единственное место, где
# описаны варианты и диапазоны: и валидация, и схема для сайта берутся отсюда,
# поэтому «движок умеет mask, а форма предлагает helm» исключено.
ARCH_VARIANTS = {
    "head": ["hood", "visor", "monocle", "helm", "mask", "spike"],
    "torso": ["taper", "box", "wide"],
    "hand": ["fist", "hammer", "claw"],
    "foot": ["boot", "wedge", "block"],
    "grip": ["wrap", "plate"],
}

# Границы подобраны по значениям базовых шестерых (SHOULDER 22..34) с запасом:
# за ними фигура либо выходит за кадр, либо рассыпается на пиксели.
ARCH_RANGES = {
    "SHOULDER": (14, 40), "WAIST": (10, 34),
    "ARM": (3, 14), "LEG": (4, 18), "ankle": (0.10, 0.95),
}

# Характер блика: spec_lam — насколько узкая его площадь, spec_glow — как он
# гаснет в глубине формы.
SPEC_RANGES = {"spec_lam": (0.0, 1.0), "spec_glow": (0.0, 1.0)}

# Нейтральный архетип для нового персонажа: ровно SENTINEL — «эталон»,
# с которым сравниваются остальные.
ARCH_DEFAULTS = dict(head="helm", torso="taper", hand="fist", foot="boot",
                     grip="plate", SHOULDER=26, WAIST=20, ARM=6, LEG=8, ankle=0.36)


def validate_spec(data: dict, hero_id=None) -> list:
    """Проверяет описание персонажа -> список сообщений (пустой = годен).

    Ловит ровно то, обо что спотывается рендер: неизвестный вид головы,
    ключ вне ARCH_DEFAULTS, число вне границ, отсутствующий материал.

    `hero_id` меняет строгость: для существующего персонажа проверяются
    только перечисленные ключи — остальные наследуются, ровно как в
    CharSpec.from_json. Для нового требуются все.
    """
    errors = []
    materials = data.get("materials") or {}
    arch = data.get("arch") or {}
    known = hero_id is not None and hero_id in ARCH and hero_id in MATERIALS

    for key in MAT_KEYS:
        if key not in materials:
            if not known:
                errors.append("нет материала '%s'" % key)
        elif not isinstance(materials[key], dict):
            errors.append("материал '%s': нужен объект с hi/base/dark" % key)
        else:
            for tone in ("hi", "base", "dark"):
                if tone not in materials[key]:
                    errors.append("материал '%s': нет тона '%s'" % (key, tone))
    for key in COLOR_KEYS:
        if key not in materials and not known:
            errors.append("нет цвета '%s'" % key)
    for key, allowed in ARCH_VARIANTS.items():
        value = arch.get(key)
        if value is None:
            if not known:
                errors.append("нет архетипа '%s'" % key)
        elif value not in allowed:
            errors.append("архетип '%s': '%s' неизвестен, можно %s"
                          % (key, value, ", ".join(allowed)))
    for key, (lo, hi) in list(ARCH_RANGES.items()) + list(SPEC_RANGES.items()):
        value = arch.get(key) if key in ARCH_RANGES else _spec_param(materials, key)
        if value is None:
            continue
        if not isinstance(value, (int, float)) or not (lo <= value <= hi):
            errors.append("'%s': нужно %g..%g, получено %r" % (key, lo, hi, value))
    return errors


def _spec_param(materials, key):
    """Ищет spec_lam/spec_glow в материалах, где они и живут."""
    for mat_name in MAT_KEYS:
        value = (materials.get(mat_name) or {}).get(key)
        if isinstance(value, (int, float)):
            return value
    return None


def schema_json() -> dict:
    """Машиночитаемое описание того, чем можно описать персонажа.

    Отдаётся сайтом фронтенку, чтобы контролы собирались из данных движка, а не
    из скопированной вручную копии. Вместе с архетипом сюда попадает вся
    библиотека движений: интерфейсу из неё строятся и вкладки, и список
    галочек «что включить в набор».
    """
    library = ANIM_LIBRARY["anims"]
    groups = []
    for name, entry in library.items():
        group = entry.get("group", "")
        if group and not any(g["name"] == group for g in groups):
            groups.append({"name": group, "anims": []})
        for g in groups:
            if g["name"] == group:
                g["anims"].append(name)
    return {
        "frame": {"w": FW, "h": FH, "cols": COLS, "rows": ROWS},
        "anims": [{"name": name, "frames": len(entry["frames"]),
                   "group": entry.get("group", ""), "fps": entry.get("fps", 8),
                   "loop": bool(entry.get("loop"))}
                  for name, entry in library.items()],
        "animGroups": groups,
        "animDefault": list(ANIM_LIBRARY["default"]),
        "arch": {
            "variants": ARCH_VARIANTS,
            "ranges": {k: list(v) for k, v in ARCH_RANGES.items()},
            "defaults": ARCH_DEFAULTS,
        },
        "materials": {
            "ramp": list(MAT_KEYS),
            "color": list(COLOR_KEYS),
            "spec": {k: list(v) for k, v in SPEC_RANGES.items()},
        },
    }


def _luma(color) -> float:
    r, g, b = color[0] / 255.0, color[1] / 255.0, color[2] / 255.0
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _distance(a, b) -> float:
    return sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5


def spec_warnings(spec) -> list:
    """Подсказки редактору: что в персонаже читается плохо.

    Только дешёвые проверки по данным, без рендера: рост в головах считается
    отдельно (measure_heads рисует кадр). Каждая проверка откалибрована так,
    чтобы шестеро базовых персонажей проходили молча — предупреждение,
    которое горит на эталонном наборе, учат игнорировать.

    Принимает недозаполненный черновик (у нового персонажа палитры может
    ещё не быть): отсутствующие материалы пропускаются, а не роняют
    проверку — редактор зовёт её на каждом движении слайдера.
    """
    out = []
    for key in MAT_KEYS:
        material = spec.mats.get(key)
        if material is None:
            continue
        hi, base, dark = mat_ramp(material)
        if not (_luma(hi) > _luma(base) > _luma(dark)):
            out.append("%s: тона не лежат по светлоте (свет > тон > тень)" % key)

    eye, neon, neon_soft = (spec.mats.get(k) for k in COLOR_KEYS)
    # Близкий к неону глаз — норма для этого стиля (светящийся визор в тон
    # подсветке), поэтому правило срабатывает только на полном совпадении.
    if eye and neon and _distance(eye, neon) < 8:
        out.append("глаз и неон одного цвета — глаз потеряется в подсветке")
    if neon and neon_soft and _distance(neon, neon_soft) < 60:
        out.append("неон и приглушённый неон слишком близки — контур не читается")

    arch = spec.arch
    if arch.get("SHOULDER") is not None and arch["SHOULDER"] <= arch.get("WAIST", 0):
        out.append("плечи не шире талии — силуэт сужается кверху")
    if arch.get("ARM") is not None and arch["ARM"] * 3 > arch.get("SHOULDER", 99):
        out.append("рука толще трети плеча — исчезнет зазор между рукой и корпусом")
    return out


def load_characters(path: str) -> list:
    """Читает characters.json и кладёт персонажей в реестр MATERIALS/ARCH/HEROES.

    Формат файла:
        {"characters": {"<id>": {"materials": {...}, "arch": {...}}}}

    materials.robe/armor/skin описываются как {"hi","base","dark","spec"};
    materials.eye/neon/neon_soft — как строка "#RRGGBB".
    arch повторяет ключи ARCH (head/torso/hand/foot/grip + пропорции).

    Возвращает список добавленных/изменённых идентификаторов.
    """
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    touched = []
    for hero_id, spec in (data.get("characters") or {}).items():
        CharSpec.from_json(hero_id, spec).register()
        touched.append(hero_id)
    return touched


def list_characters() -> None:
    print("Персонажи (%d):" % len(HEROES))
    for hero in HEROES:
        a = ARCH.get(hero, {})
        mats = MATERIALS.get(hero, {})
        neon = mats.get("neon")
        neon_hex = _hex(neon) if neon else "—"
        print("  %-12s голова=%-8s торс=%-6s кисть=%-7s стопа=%-6s неон=%s"
              % (hero, a.get("head", "?"), a.get("torso", "?"),
                 a.get("hand", "?"), a.get("foot", "?"), neon_hex))


def build_roster(specs, scale=2):
    """Один лист со всеми: ряд — боец, внутри — его спрайт-лист.

    Нужен, чтобы судить о бойцах как о наборе: по одному в отдельном окне
    несопоставимы — не видно, кто с кем конфликтует по силуэту и по светлоте.
    """
    roster = Canvas(FW * COLS * len(specs), FH * ROWS)
    for i, spec in enumerate(specs):
        roster.blit(build_sheet(spec), i * FW * COLS, 0)
    return roster


def main():
    ap = argparse.ArgumentParser(
        description="Генератор спрайт-листов персонажей (пиксель-арт, кадр 80x128).")
    ap.add_argument("--hero", default="tetrax",
                    help="персонаж; 'all' — лист со всеми сразу")
    ap.add_argument("--scale", type=int, default=3,
                    help="во сколько раз увеличить превью")
    ap.add_argument("--characters", default=None,
                    help="JSON с новыми/переопределёнными персонажами")
    ap.add_argument("--out", default=None,
                    help="каталог для спрайт-листов (по умолчанию ./out)")
    ap.add_argument("--write", action="store_true",
                    help="записать спрайт-листы на диск (иначе только превью)")
    ap.add_argument("--list", action="store_true",
                    help="показать список персонажей и выйти")
    ap.add_argument("--dump-schema", action="store_true",
                    help="вывести схему описания персонажа (JSON) и выйти")
    args = ap.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    if args.characters:
        added = load_characters(os.path.abspath(args.characters))
        print("из %s загружено: %s" % (args.characters, ", ".join(added)))

    if args.dump_schema:
        print(json.dumps(schema_json(), ensure_ascii=False, indent=2))
        return

    if args.list:
        list_characters()
        return

    prev_dir = os.path.join(root, "preview")
    os.makedirs(prev_dir, exist_ok=True)

    if args.hero == "all":
        roster = build_roster([char_spec(hero) for hero in HEROES])
        prev = os.path.join(prev_dir, "roster_x%d.png" % args.scale)
        roster.scaled(args.scale).save(prev)
        print("превью состава: %s (%dx%d)" % (prev, roster.w * args.scale, roster.h * args.scale))
    else:
        try:
            spec = char_spec(args.hero)
        except KeyError:
            print("неизвестный персонаж: %s (см. --list)" % args.hero)
            return
        sheet = build_sheet(spec)
        prev = os.path.join(prev_dir, "%s_x%d.png" % (args.hero, args.scale))
        sheet.scaled(args.scale).save(prev)
        heads, head_px, figure_px = measure_heads(spec)
        print("превью: %s (%dx%d, кадр %dx%d, %.2f головы: фигура %d px, голова %d px)"
              % (prev, sheet.w * args.scale, sheet.h * args.scale,
                 FW, FH, heads, figure_px, head_px))
        for warning in spec_warnings(spec):
            print("  ! %s" % warning)

    if args.write:
        out_dir = os.path.abspath(args.out or os.path.join(root, "out"))
        os.makedirs(out_dir, exist_ok=True)
        names = HEROES if args.hero == "all" else [args.hero]
        for hero in names:
            # Имя файла = идентификатор персонажа: движок ищет спрайт по нему.
            path = os.path.join(out_dir, "%s.png" % hero)
            build_sheet(char_spec(hero)).save(path)
            print("ассет: %s (%dx%d)" % (path, FW * COLS, FH * ROWS))


if __name__ == "__main__":
    main()
