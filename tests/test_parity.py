"""Golden-тест: картинка не должна меняться, пока её не просят.

Что делает
----------
Перерисовывает все спрайт-листы базовых персонажей и сравнивает их с эталоном
в tests/golden/ ПОПИКСЕЛЬНО. Любая правка движка — оптимизация циклов, смена
порядка наложения слоёв, перенос позы — сначала проходит этот тест, и только
потом имеет право попасть в main.

Почему это важно именно здесь: генератор пишет пиксель-арт, и «на глаз почти
то же самое» — не аргумент. Полсотни разъехавшихся пикселей в контуре кисти
не видно в превью, но видно в игре.

Запуск
------
    py tests\\test_parity.py             # проверить (код 0 = совпало)
    py tests\\test_parity.py --update    # перезаписать эталон
    py tests\\test_parity.py --hero wraith --jobs 4
    py tests\\test_parity.py --anim punch # быстрый цикл по одной анимации

Эталон в репозитории намеренно: изменение картинки должно быть видна в
`git diff` как изменение файла, а не «что-то сломалось на проде».
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from png_reader import read_png                    # noqa: E402
import char_generator as G                          # noqa: E402
from pixelcanvas import Canvas                      # noqa: E402


HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN_DIR = os.path.join(HERE, "golden")
MEASURE_FILE = os.path.join(GOLDEN_DIR, "measurements.json")


# --- Рендер (выполняется в пуле процессов) -----------------------------------

def render_sheet(hero):
    """Полный спрайт-лист персонажа + замер пропорций.

    Собирается здесь, а не через G.build_sheet(), ради замеров: тесту нужен
    ещё и рост в головах, а build_sheet его не возвращает.
    """
    sheet = Canvas(G.FW * G.COLS, G.FH * G.ROWS)
    for row, (_name, poses) in enumerate(G.ANIMS):
        for col, pose in enumerate(poses):
            sheet.blit(G.render_frame(pose, hero), col * G.FW, row * G.FH)
    heads, head_px, figure_px = G.measure_heads(hero)
    return hero, sheet, (round(heads, 4), head_px, figure_px)


# --- Сравнение ----------------------------------------------------------------

def cell_of(x, y):
    """Пиксель листа -> (имя строки, колонка, позы этой строки)."""
    row, col = y // G.FH, x // G.FW
    name, poses = G.ANIMS[row]
    return name, col, poses


EMPTY_CELL = "(пустая ячейка)"


def diff_sheet(canvas, golden_rows):
    """Сравнивает лист с эталоном -> (отчёт по кадрам, отчёт по пустым ячейкам).

    Различаются два вида расхождений, и они разные по смыслу:

    * заполненный кадр отличается от эталона — сломалась отрисовка;
    * непустая ячейка в строке, которой по сетке положено быть прозрачной, —
      сломался ход времени (рендерер нарисовал кадр, которого там быть не
      должно), и это видно только сравнением с эталоном, а не просмотром.
    """
    cells = {}
    stray = {}
    for y in range(canvas.h):
        for x in range(canvas.w):
            got = canvas.px[y][x]
            want = golden_rows[y][x]
            if got == want:
                continue
            name, col, poses = cell_of(x, y)
            bucket = cells if col < len(poses) else stray
            entry = bucket.setdefault((name, col), {"delta": 0, "at": (0, 0), "n": 0})
            entry["delta"] = max(entry["delta"], max(abs(got[i] - want[i]) for i in range(4)))
            entry["at"] = (x % G.FW, y % G.FH)
            entry["n"] += 1

    def render_lines(bucket, suffix=""):
        return ["    %-14s кадр %d: %5d px, макс. расхождение %3d, в (%d, %d)%s"
                % (name, col, e["n"], e["delta"], e["at"][0], e["at"][1], suffix)
                for (name, col), e in sorted(bucket.items(), key=lambda kv: -kv[1]["n"])]

    return render_lines(cells), render_lines(stray, "  <- лишний кадр")


# --- main ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Golden-тест спрайт-листов SheetSmith.")
    ap.add_argument("--update", action="store_true", help="перезаписать эталон")
    ap.add_argument("--hero", action="append", default=None,
                    help="ограничить проверку одним персонажем (можно повторять)")
    ap.add_argument("--anim", default=None,
                    help="вывести только одну анимацию из листа (для быстрой проверки)")
    ap.add_argument("--jobs", type=int, default=0, help="процессов (0 = все ядра)")
    args = ap.parse_args()

    heroes = args.hero or list(G.HEROES)
    unknown = [h for h in heroes if h not in G.ARCH or h not in G.MATERIALS]
    if unknown:
        print("неизвестные персонажи: %s" % ", ".join(unknown))
        return 2

    jobs = args.jobs or os.cpu_count() or 1
    print("рендер %d персонажей, процессов: %d%s"
          % (len(heroes), jobs, (", анимация " + args.anim) if args.anim else ""))

    started = time.time()
    if jobs > 1 and len(heroes) > 1:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(render_sheet, heroes))
    else:
        results = [render_sheet(hero) for hero in heroes]
    render_s = time.time() - started

    frames_total = sum(len(poses) for _n, poses in G.ANIMS)
    print("готово за %.1f с (%.2f с на кадр)"
          % (render_s, render_s / max(1, frames_total * len(heroes))))

    if args.update:
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        for hero, sheet, (heads, head_px, figure_px) in results:
            path = os.path.join(GOLDEN_DIR, "%s.png" % hero)
            sheet.save(path)
            print("  эталон: %s (%dx%d, %.2f головы)"
                  % (os.path.basename(path), sheet.w, sheet.h, heads))
        _save_measurements(results, heroes)
        return 0

    if not os.path.isdir(GOLDEN_DIR):
        print("эталона нет — сначала: py tests\\test_parity.py --update")
        return 2

    failed = 0
    checked = 0
    for hero, sheet, (heads, head_px, figure_px) in results:
        path = os.path.join(GOLDEN_DIR, "%s.png" % hero)
        if not os.path.exists(path):
            print("  %-10s НЕТ ЭТАЛОНА (%s)" % (hero, os.path.basename(path)))
            failed += 1
            continue
        width, height, rows = read_png(path)

        report = []
        if (sheet.w, sheet.h) != (width, height):
            report.append("    размер: получено %dx%d, эталон %dx%d"
                          % (sheet.w, sheet.h, width, height))
        elif len(rows) == sheet.h and len(rows[0]) == sheet.w:
            cells, stray = diff_sheet(sheet, rows)
            if args.anim:
                # --anim отсекает остальные строки листа, но жалобы на лишние
                # кадры показываются всегда: они про другое свойство.
                cells = [line for line in cells if (" %s " % args.anim) in line]
            report = cells + stray
        else:
            report.append("    эталон %dx%d нечитаем" % (width, height))

        if report:
            print("  %-10s РАСХОЖДЕНИЕ" % hero)
            for line in report:
                print(line)
            failed += 1
        else:
            checked += 1
            print("  %-10s ок  (%.2f головы: фигура %d px, голова %d px)"
                  % (hero, heads, figure_px, head_px))

    measure_bad = _check_measurements(results)
    if measure_bad:
        print("  замеры     РАСХОЖДЕНИЕ")
        for line in measure_bad:
            print(line)
        failed += 1

    print()
    if failed:
        print("ПРОВАЛ: %d из %d персонажей разошлись с эталоном." % (failed, len(heroes)))
        print("Если изменение задумано — перезапиши эталон: py tests\\test_parity.py --update")
        return 1
    print("OK: %d персонажей совпали с эталоном попиксельно." % checked)
    return 0


def _measure_path(hero):
    return os.path.join(GOLDEN_DIR, "%s.measure.json" % hero)


def _save_measurements(results, heroes):
    """Замеры хранятся отдельно от картинки: рост в головах — тоже инвариант."""
    for hero, _sheet, m in results:
        with open(_measure_path(hero), "w", encoding="utf-8") as fh:
            fh.write('{\n  "heads": %r,\n  "head_px": %d,\n  "figure_px": %d\n}\n' % m)


def _check_measurements(results):
    bad = []
    for hero, _sheet, m in results:
        path = _measure_path(hero)
        if not os.path.exists(path):
            bad.append("    %s: нет файла замеров" % hero)
            continue
        with open(path, "r", encoding="utf-8") as fh:
            want = json.load(fh)
        got = {"heads": m[0], "head_px": m[1], "figure_px": m[2]}
        for key in ("heads", "head_px", "figure_px"):
            if abs(float(want[key]) - float(got[key])) > 1e-4:
                bad.append("    %s: %s — получено %s, эталон %s"
                           % (hero, key, got[key], want[key]))
    return bad


if __name__ == "__main__":
    sys.exit(main())
