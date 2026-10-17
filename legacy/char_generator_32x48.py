"""Генератор спрайт-листов бойцов BlockBrawler.

Рисует 3 героев (TETRAX / MATRIX / OVERLORD) в пиксель-арте 32x48, вид сбоку,
неоновый контур + тёмная заливка — тот же визуальный язык, что давал прежний
процедурный primitive_fighter.gd, но теперь это настоящие спрайты.

Каждый боец = один спрайт-лист 4 колонки x 6 строк (ячейка 32x48):

    строка 0  idle    (4 кадра)
    строка 1  punch   (3 кадра)
    строка 2  kick    (3 кадра)
    строка 3  super   (4 кадра)
    строка 4  stun    (2 кадра)
    строка 5  slip    (2 кадра)

Неиспользуемые ячейки в строках из 2-3 кадров остаются прозрачными.

Запуск:  py tools/gen_fighters.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pixelcanvas import Canvas, Mask, outline, paint, hex_rgba, scale_rgb

# --- Геометрия кадра -------------------------------------------------------

FW, FH = 32, 48          # размер одного кадра
COLS, ROWS = 4, 6        # сетка спрайт-листа
FOOT_Y = 46              # линия ступней внутри кадра

# --- Палитра (совпадает с primitive_fighter.gd / Style.MD) ------------------

FILL = hex_rgba("#4A5265")        # металл корпуса
FILL_DARK = hex_rgba("#2E3546")   # дальние конечности
FILL_HI = hex_rgba("#6E7891")     # блик на верхней кромке

COLORS = {
    "tetrax": hex_rgba("#00FFFF"),
    "matrix": hex_rgba("#FF00FF"),
    "overlord": hex_rgba("#FFAA00"),
    # Промежуточные враги кампании (Roadmap §3.3 / §5, этап B).
    "sentinel": hex_rgba("#33FF66"),   # зелёный дозор
    "wraith": hex_rgba("#9966FF"),     # фиолетовый призрак
    "brute": hex_rgba("#FF3333"),      # красный громила
}

# ===========================================================================
# Позы: координаты суставов. Боец нарисован лицом вправо (flip в движке).
# ===========================================================================


def base_pose():
    """Нейтральная боевая стойка. Ноги расставлены, руки наготове."""
    return {
        "head": (16, 9),
        "neck": (16, 15),
        "chest": (16, 21),
        "hip": (15, 27),
        # дальняя рука / нога (рисуется приглушённо)
        "sh_b": (13, 17), "el_b": (10, 22), "fist_b": (11, 27),
        "hip_b": (13, 27), "knee_b": (11, 35), "foot_b": (8, 45),
        # ближняя рука / нога
        "sh_f": (18, 17), "el_f": (22, 23), "fist_f": (21, 28),
        "hip_f": (16, 27), "knee_f": (19, 35), "foot_f": (22, 45),
    }


def shifted(pose, dx=0, dy=0, part=None):
    """Сдвигает позу. part=None — всё тело, иначе только указанный сустав."""
    out = dict(pose)
    for key in (part,) if part else list(pose.keys()):
        x, y = out[key]
        out[key] = (x + dx, y + dy)
    return out


def idle_frames():
    """Покачивание в стойке: тело слегка проседает и возвращается."""
    out = []
    # (сдвиг корпуса, подъём стоп, размах рук)
    for dy, lift in ((0, 0), (-1, 0), (-1, 1), (0, 0)):
        p = shifted(base_pose(), dy=dy)
        # стопы слегка разводятся при подъёме — читается как шаг на месте
        p = shifted(p, part="foot_b", dx=-lift)
        p = shifted(p, part="foot_f", dx=lift)
        p = shifted(p, part="fist_b", dy=lift)
        p = shifted(p, part="fist_f", dy=-lift)
        out.append(p)
    return out


def punch_frames():
    """Удар рукой: замах -> выпад с полным выносом -> возврат."""
    f0 = shifted(base_pose())
    f0 = shifted(f0, part="fist_f", dx=-6, dy=-2)   # кулак уходит назад
    f0 = shifted(f0, part="el_f", dx=-3, dy=-1)

    # Выпад: корпус подаётся вперёд, рука выпрямлена в упор.
    f1 = shifted(base_pose(), dx=2)
    f1 = shifted(f1, part="sh_f", dx=1)
    f1 = shifted(f1, part="el_f", dx=4, dy=-3)
    f1 = shifted(f1, part="fist_f", dx=8, dy=-7)
    f1 = shifted(f1, part="foot_b", dx=-2)
    f1 = shifted(f1, part="foot_f", dx=3)

    f2 = shifted(base_pose(), dx=1)
    f2 = shifted(f2, part="el_f", dx=2, dy=-2)
    f2 = shifted(f2, part="fist_f", dx=4, dy=-4)

    return [f0, f1, f2]


def kick_frames():
    """Удар ногой: колено вверх -> хлёсткий вынос на уровень пояса -> возврат."""
    f0 = shifted(base_pose())
    f0 = shifted(f0, part="knee_f", dx=2, dy=-7)    # колено поднято к поясу
    f0 = shifted(f0, part="foot_f", dx=4, dy=-14)
    f0 = shifted(f0, part="foot_b", dx=-2)

    f1 = shifted(base_pose(), dx=-2)                 # корпус уходит назад
    f1 = shifted(f1, part="hip_f", dx=3)
    f1 = shifted(f1, part="knee_f", dx=8, dy=-6)     # голень почти горизонтально
    f1 = shifted(f1, part="foot_f", dx=14, dy=-8)    # стопа у противника
    f1 = shifted(f1, part="sh_b", dx=-2)
    f1 = shifted(f1, part="sh_f", dx=-1)
    f1 = shifted(f1, part="fist_f", dx=-2, dy=-3)    # руки уравновешивают

    f2 = shifted(base_pose())
    f2 = shifted(f2, part="knee_f", dx=2, dy=-4)
    f2 = shifted(f2, part="foot_f", dx=4, dy=-7)

    return [f0, f1, f2]


def super_frames():
    """Супер-удар (тетрис): замах обеими руками -> вынос -> пик свечения."""
    f0 = shifted(base_pose(), dx=-1)
    f0 = shifted(f0, part="fist_f", dx=-5, dy=-3)
    f0 = shifted(f0, part="fist_b", dx=-4, dy=-2)
    f0 = shifted(f0, part="head", dy=1)

    f1 = shifted(base_pose(), dx=2)
    f1 = shifted(f1, part="sh_f", dx=1)
    f1 = shifted(f1, part="el_f", dx=4, dy=-3)
    f1 = shifted(f1, part="fist_f", dx=7, dy=-6)
    f1 = shifted(f1, part="foot_f", dx=3)

    f2 = shifted(base_pose(), dx=3)                 # пик: максимальный вынос
    f2 = shifted(f2, part="sh_f", dx=2)
    f2 = shifted(f2, part="el_f", dx=5, dy=-4)
    f2 = shifted(f2, part="fist_f", dx=9, dy=-8)
    f2 = shifted(f2, part="sh_b", dx=1)
    f2 = shifted(f2, part="el_b", dx=-2, dy=-2)
    f2 = shifted(f2, part="foot_b", dx=-3)
    f2 = shifted(f2, part="foot_f", dx=4)

    f3 = shifted(base_pose(), dx=1)
    f3 = shifted(f3, part="el_f", dx=2, dy=-2)
    f3 = shifted(f3, part="fist_f", dx=4, dy=-4)

    return [f0, f1, f2, f3]


def stun_frames():
    """Стан: падение на одно колено, корпус проседает и заваливается."""
    f0 = shifted(base_pose(), dy=7)
    f0 = shifted(f0, part="knee_f", dy=2, dx=-2)     # передняя нога на колене
    f0 = shifted(f0, part="foot_f", dy=4, dx=-4)
    f0 = shifted(f0, part="knee_b", dy=-2)
    f0 = shifted(f0, part="head", dy=3, dx=-1)
    f0 = shifted(f0, part="sh_f", dy=2)
    f0 = shifted(f0, part="el_f", dx=1, dy=3)
    f0 = shifted(f0, part="fist_f", dx=2, dy=5)
    f0 = shifted(f0, part="fist_b", dx=1, dy=4)

    f1 = shifted(f0, dy=1)
    f1 = shifted(f1, part="head", dx=-1)
    return [f0, f1]


def slip_frames():
    """Поскальзывание: корпус уходит назад, руки хаотично вверх."""
    f0 = shifted(base_pose(), dx=-3, dy=2)
    f0 = shifted(f0, part="head", dx=-2)
    f0 = shifted(f0, part="fist_f", dy=-8, dx=-3)   # руки разлетаются
    f0 = shifted(f0, part="el_f", dx=-3, dy=-4)
    f0 = shifted(f0, part="fist_b", dy=-6, dx=-2)
    f0 = shifted(f0, part="el_b", dx=-3, dy=-3)

    f1 = shifted(f0, dx=-2, dy=3)
    f1 = shifted(f1, part="fist_f", dy=-4, dx=2)
    f1 = shifted(f1, part="fist_b", dy=2, dx=2)
    return [f0, f1]


ANIMS = [
    ("idle", idle_frames()),
    ("punch", punch_frames()),
    ("kick", kick_frames()),
    ("super", super_frames()),
    ("stun", stun_frames()),
    ("slip", slip_frames()),
]

# ===========================================================================
# Растеризация: скелет -> маски -> неоновый контур + заливка
# ===========================================================================

# Толщина конечностей и ширина силуэта под каждого героя (Heroes.MD §2).
BUILD = {
    "tetrax": {"arm": 2, "leg": 3, "shoulder": 8, "waist": 6, "head": "round"},
    "matrix": {"arm": 2, "leg": 3, "shoulder": 12, "waist": 11, "head": "visor"},
    "overlord": {"arm": 3, "leg": 4, "shoulder": 15, "waist": 10, "head": "block"},
    # Промежуточные враги кампании (Roadmap §3.3). Силуэты отличаются от героев:
    #   sentinel — стройный стражник (узкие плечи, антенна-шпиль).
    #   wraith   — тощий призрак (очень узкая талия, рваный плащ).
    #   brute    — громила (широченные плечи, короткие ноги).
    "sentinel": {"arm": 2, "leg": 3, "shoulder": 9, "waist": 6, "head": "helm"},
    "wraith": {"arm": 2, "leg": 2, "shoulder": 10, "waist": 4, "head": "hood"},
    "brute": {"arm": 4, "leg": 5, "shoulder": 18, "waist": 12, "head": "spike"},
}


def build_masks(pose, hero):
    """Возвращает (back, front, accent) — маски дальней/ближней части и неона."""
    cfg = BUILD[hero]
    aw, lw = cfg["arm"], cfg["leg"]
    sw = cfg["shoulder"]
    hx, hy = pose["head"]

    back = Mask(FW, FH)
    front = Mask(FW, FH)

    # --- Дальняя нога и рука (приглушённый слой) ---
    back.limb(pose["hip_b"], pose["knee_b"], lw * 0.8)
    back.limb(pose["knee_b"], pose["foot_b"], lw * 0.7)
    back.limb(pose["sh_b"], pose["el_b"], aw)
    back.limb(pose["el_b"], pose["fist_b"], aw * 0.9)

    # --- Ближняя нога ---
    front.limb(pose["hip_f"], pose["knee_f"], lw)
    front.limb(pose["knee_f"], pose["foot_f"], lw * 0.9)

    # --- Торс: широкие плечи -> узкая талия ---
    cx = pose["chest"][0]
    top = pose["sh_f"][1] - 1
    bot = pose["hip"][1] + 1
    front.poly([
        (cx - sw // 2, top), (cx + sw // 2, top),
        (cx + cfg["waist"] // 2 + 1, bot), (cx - cfg["waist"] // 2 - 1, bot),
    ])

    # --- Шея ---
    front.limb(pose["neck"], (pose["neck"][0], pose["neck"][1] + 3), aw + 1)

    # --- Голова + убор/детали корпуса (по архетипу героя) ---
    accent = Mask(FW, FH)
    _draw_head(front, accent, pose, hero, cfg["head"])
    _draw_gear(front, accent, pose, hero, cfg)

    # --- Ближняя рука (поверх всего — она впереди) ---
    front.limb(pose["sh_f"], pose["el_f"], aw)
    front.limb(pose["el_f"], pose["fist_f"], aw * 0.9)
    front.disc(pose["fist_f"][0], pose["fist_f"][1], aw + 1)   # кисть/кулак

    return back, front, accent


def _draw_head(front, accent, pose, hero, shape):
    """Голова + акцентный визор. Форма: round / visor / block."""
    hx, hy = pose["head"]

    if shape == "round":
        # TETRAX: круглая голова-монах + капюшон сзади.
        front.ellipse(hx, hy, 5, 5)
        front.poly([(hx - 5, hy + 4), (hx - 8, hy + 8), (hx - 2, hy + 6)])
        accent.rect(hx, hy - 1, hx + 4, hy + 1)              # визор-щель
        accent.disc(hx + 2, hy, 1)
    elif shape == "visor":
        # MATRIX: прямоугольная маска-визор, гранёный затылок.
        front.rect(hx - 4, hy - 5, hx + 4, hy + 4)
        front.poly([(hx - 4, hy - 5), (hx + 4, hy - 5), (hx + 2, hy - 7), (hx - 2, hy - 7)])
        accent.rect(hx - 3, hy - 2, hx + 4, hy + 1)          # полоса визора
    elif shape == "helm":
        # SENTINEL: шлем-цилиндр с антенной-шпилем и одиночным глазом.
        front.rect(hx - 4, hy - 5, hx + 4, hy + 4)
        front.rect(hx - 2, hy - 9, hx + 2, hy - 6)           # гребень-насадка
        accent.rect(hx - 3, hy - 2, hx + 3, hy)              # сканирующий глаз
        accent.disc(hx + 1, hy - 7, 1)                       # огонёк антенны
    elif shape == "hood":
        # WRAITH: глубокий капюшон, лица почти не видно — пара светящихся глаз.
        front.ellipse(hx, hy, 5, 6)
        front.poly([(hx - 6, hy + 5), (hx - 3, hy - 6), (hx + 3, hy - 6), (hx + 6, hy + 5)])
        accent.rect(hx - 3, hy - 1, hx - 1, hy + 1)          # левый глаз
        accent.rect(hx + 1, hy - 1, hx + 3, hy + 1)          # правый глаз
    elif shape == "spike":
        # BRUTE: приземистая голова-череп с тремя шипами наружу.
        front.rect(hx - 5, hy - 5, hx + 5, hy + 4)
        front.poly([(hx - 5, hy - 5), (hx - 9, hy - 10), (hx - 3, hy - 6)])
        front.poly([(hx, hy - 5), (hx, hy - 12), (hx + 3, hy - 5)])
        front.poly([(hx + 5, hy - 5), (hx + 9, hy - 10), (hx + 3, hy - 6)])
        accent.rect(hx - 4, hy - 2, hx + 4, hy + 1)          # массивный визор
    else:
        # OVERLORD: тяжёлая гранёная голова + массивные рога.
        front.rect(hx - 5, hy - 6, hx + 4, hy + 4)
        # Рога — широкие треугольники от боков черепа вверх-наружу.
        front.poly([(hx - 5, hy - 4), (hx - 5, hy + 2), (hx - 11, hy - 8)])
        front.poly([(hx + 4, hy - 4), (hx + 4, hy + 2), (hx + 10, hy - 7)])
        accent.disc(hx + 1, hy - 1, 2)                        # глаз-искра


def _draw_gear(front, accent, pose, hero, cfg):
    """Детали одежды/брони поверх торса (Этап B роадмапа)."""
    cx = pose["chest"][0]
    top = pose["sh_f"][1] - 1
    mid = (top + pose["hip"][1]) // 2

    if hero == "tetrax":
        # Плащ-капюшон: наплечники и диагональный сгиб плаща на груди.
        front.poly([(cx - 9, top + 1), (cx - 3, top), (cx - 4, top + 5)])
        front.poly([(cx + 9, top + 1), (cx + 3, top), (cx + 4, top + 5)])
        accent.rect(cx - 2, mid - 1, cx + 2, mid + 2)        # сгиб/застёжка
    elif hero == "matrix":
        # Оверсайз-куртка + три LED-линии на груди.
        for i in range(3):
            y = mid - 2 + i * 3
            accent.rect(cx - 5, y, cx + 5, y)
    elif hero == "sentinel":
        # SENTINEL: кираса с центральной сканирующей полосой и наплечниками.
        front.rect(cx - 4, top, cx - 1, top + 3)
        front.rect(cx + 1, top, cx + 4, top + 3)
        accent.rect(cx - 4, mid, cx + 4, mid + 1)            # сканер-полоса
    elif hero == "wraith":
        # WRAITH: рваный плащ — неровные зубцы по низу торса.
        front.poly([(cx - 4, top), (cx - 6, mid + 4), (cx - 2, mid + 2)])
        front.poly([(cx + 4, top), (cx + 6, mid + 4), (cx + 2, mid + 2)])
        accent.rect(cx - 1, top + 2, cx + 1, mid - 1)        # энерго-жила
    elif hero == "brute":
        # BRUTE: массивная броня-плиты + ядро-реактор.
        front.rect(cx - 11, top, cx - 5, top + 5)
        front.rect(cx + 5, top, cx + 11, top + 5)
        accent.disc(cx, mid, 4)                              # ядро
    else:
        # Плечевые плиты + реактор в центре груди.
        front.rect(cx - 9, top, cx - 4, top + 4)
        front.rect(cx + 4, top, cx + 9, top + 4)
        accent.disc(cx, mid, 3)                              # реактор


def render_frame(pose, hero, accent_color):
    """Собирает кадр 32x48: тёмный корпус, неоновый контур, акценты."""
    back, front, accent = build_masks(pose, hero)
    union = Mask(FW, FH).union(back).union(front)

    cv = Canvas(FW, FH)
    paint(cv, union, FILL_DARK)

    # Ближняя часть светлее дальней — боец читается как объёмная фигура.
    paint(cv, front, FILL)

    # Блик по верхней кромке корпуса и головы.
    hi = outline(front)
    for y in range(1, FH):
        for x in range(FW):
            if hi.has(x, y) and not hi.has(x, y - 1) and front.has(x, y):
                cv.set(x, y, FILL_HI)

    # Неоновый контур силуэта: сперва весь яркий, поверх — приглушённый
    # на дальних конечностях, чтобы получить глубину.
    paint(cv, outline(union), accent_color)
    paint(cv, _intersect(outline(back), outline(union)), scale_rgb(accent_color, 0.5))

    # Акцентные детали (визор, реактор, LED) поверх заливки.
    paint(cv, accent, accent_color)

    return cv


def _intersect(a, b):
    out = Mask(a.w, a.h)
    for y in range(a.h):
        for x in range(a.w):
            if a.has(x, y) and b.has(x, y):
                out.add(x, y)
    return out


def build_sheet(hero):
    """Собирает спрайт-лист 4x6 из кадров всех анимаций."""
    sheet = Canvas(FW * COLS, FH * ROWS)
    color = COLORS[hero]
    for row, (_name, frames) in enumerate(ANIMS):
        for col, pose in enumerate(frames):
            sheet.blit(render_frame(pose, hero, color), col * FW, row * FH)
    return sheet


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "tetris-fighter", "assets", "sprites", "fighters")
    # Превью — только для просмотра, в ассеты игры не попадает.
    prev_dir = os.path.join(root, "tools", "preview")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(prev_dir, exist_ok=True)

    for hero in COLORS:
        sheet = build_sheet(hero)
        path = os.path.join(out_dir, "%s.png" % hero)
        sheet.save(path)
        # Превью в 3x — удобно смотреть глазами, движок его не грузит.
        sheet.scaled(3).save(os.path.join(prev_dir, "%s_x3.png" % hero))
        print("%-9s -> %s (%dx%d)" % (hero, path, sheet.w, sheet.h))


if __name__ == "__main__":
    main()