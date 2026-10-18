"""Рендер-сервис сайта: спецификация -> PNG, кэш и пул процессов.

Движок рисует медленно: один кадр ~2 с, лист из 18 кадров ~40 с. Для сайта
это значит три вещи, и здесь они и сделаны:

* **кэш по содержимому.** Ключ — хеш описания персонажа плюс анимация, кадр и
  масштаб. Переключение между уже показанными кадрами и возврат к предыдущей
  настройке бесплатны; хеш по содержимому, а не по времени, означает, что
  два одинаковых черновика из разных вкладок делят один результат.
* **пул процессов.** Рендер упирается в пиксельные циклы Python и держит GIL,
  поэтому в одном процессе второй запрос не начнётся раньше конца первого.
  Процессы решают это и заодно ускоряют экспорт листов.
* **счётчики.** Каждый ответ несёт, сколько занял рендер и откуда он взят —
  кэш или процесс. Это видно в devtools и стоит больше, чем лог-файл.

Модуль ничего не знает про HTTP: его можно вызывать из тестов и из CLI.
"""

import hashlib
import io
import json
import os
import struct
import sys
import time
import zipfile
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import char_generator as G   # noqa: E402
from pixelcanvas import Canvas   # noqa: E402


class SpecError(ValueError):
    """Описание персонажа не годится — сообщения годятся показать в форме."""


# --- Кэш ---------------------------------------------------------------------

class FrameCache:
    """LRU с ограничением по числу кадров и по суммарному объёму.

    Кадр 80x128 в PNG — единицы килобайт, так что лимит считается и в штуках,
    и в байтах: иначе сотня масштабированных превью незаметно съест память.
    """

    def __init__(self, max_entries=512, max_bytes=256 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._data = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(spec_json, anim, frame, scale):
        payload = json.dumps([spec_json, anim, frame, scale], sort_keys=True,
                             ensure_ascii=False)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def get(self, key):
        """-> (png, meta) или None."""
        entry = self._data.get(key)
        if entry is None:
            self.misses += 1
            return None
        self._data.move_to_end(key)
        self.hits += 1
        return entry

    def put(self, key, png, meta):
        """Кладёт кадр вместе с метаданными.

        Хранить надо и то, и другое: ответ клиенту собирается из meta, и
        попадание в кэш обязано выглядеть снаружи ровно так же, как
        холодный рендер, — иначе второй запрос «теряет» размеры кадра.
        """
        if key in self._data:
            self._bytes -= len(self._data[key][0])
        self._data[key] = (png, meta)
        self._bytes += len(png)
        while self._data and (len(self._data) > self.max_entries
                              or self._bytes > self.max_bytes):
            _key, (old_png, _old_meta) = self._data.popitem(last=False)
            self._bytes -= len(old_png)

    def stats(self):
        return {"entries": len(self._data), "bytes": self._bytes,
                "hits": self.hits, "misses": self.misses}


# --- Сборка спецификации ------------------------------------------------------

def build_spec(payload):
    """Из тела запроса -> CharSpec или SpecError со списком причин.

    Порядок именно такой: сначала проверка, потом сборка. Собирать и потом
    валидировать нельзя — part-спека без палитры упала бы на KeyError уже
    внутри рендера, и пользователь получил бы «500» вместо списка ошибок.
    """
    hero_id = (payload.get("id") or "draft").strip() or "draft"
    errors = G.validate_spec(payload, hero_id)
    if errors:
        raise SpecError(errors)
    try:
        return G.CharSpec.from_json(hero_id, payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise SpecError(["описание персонажа разобрать не удалось: %s" % exc])


def _anim(name, frame):
    poses = pose_of(name)
    if not isinstance(frame, int) or not (0 <= frame < len(poses)):
        raise SpecError(["кадр %r вне диапазона 0..%d" % (frame, len(poses) - 1)])
    return poses[frame]


@lru_cache(maxsize=512)
def pose_of(name):
    """Позы анимации из библиотеки движений.

    Кэшируются по имени: позы не зависят от персонажа (заданы смещениями от
    опорной стойки), а строятся каждый вызов заново из операций.
    """
    try:
        return G.build_anims([name])[0][1]
    except KeyError:
        raise SpecError(["анимация '%s' неизвестна, можно: %s"
                         % (name, ", ".join(G.anim_names()))])


def anim_frames(name):
    return len(pose_of(name))


def chosen_anims(names):
    """Проверяет и нормализует список движений для листа."""
    if not names:
        names = G.ANIM_LIBRARY["default"]
    if len(names) > 32:
        raise SpecError(["больше 32 движений в один лист не собираем"])
    for name in names:
        pose_of(name)          # заодно отвечает на «анимация неизвестна»
    return list(names)


def spec_json(spec):
    """Канонический JSON спецификации — он же ключ кэша."""
    return json.dumps(spec.to_json(), sort_keys=True, ensure_ascii=False)


# --- Задачи для пула ----------------------------------------------------------

# --- Передача холстов между процессами ---------------------------------------
# Холст — это список списков кортежей, и pickle такого небережлив: на каждый
# пиксель уходит ссылка на объект. Наружу отдаём плоский массив RGBA по 4 байта
# на пиксель — 40 КБ на кадр вместо мегабайтов, распаковка обратно копирует
# пиксели по строкам через struct.unpack_from.

def pack_canvas(canvas):
    raw = bytearray()
    for row in canvas.px:
        for px in row:
            raw.extend(px[:4])
    return canvas.w, canvas.h, bytes(raw)


def unpack_canvas(width, height, packed):
    canvas = Canvas(width, height)
    for y in range(height):
        values = struct.unpack_from("<%dB" % (width * 4), packed, y * width * 4)
        canvas.px[y] = list(zip(*[iter(values)] * 4))
    return canvas


# --- Задачи для пула ----------------------------------------------------------

def render_task(task):
    """Один кадр -> (width, height, packed). Выполняется и в пуле, и в потоке."""
    hero_id, payload, anim, frame = task[0], task[1], task[2], task[3]
    spec = G.CharSpec.from_json(hero_id, payload)
    return pack_canvas(G.render_frame(_anim(anim, frame), spec))


def measure_task(task):
    """Рост в головах + предупреждения — рисует один кадр."""
    hero_id, payload = task
    started = time.time()
    spec = G.CharSpec.from_json(hero_id, payload)
    heads, head_px, figure_px = G.measure_heads(spec)
    return {"heads": round(heads, 2), "head_px": head_px, "figure_px": figure_px,
            "warnings": G.spec_warnings(spec),
            "render_ms": int((time.time() - started) * 1000)}


# --- Сервис -------------------------------------------------------------------

class RenderService:
    """Единая точка входа для всего рендера сайта.

    jobs=0 включает рендер прямо в потоке: медленно, зато без пула — так
    удобно ловить ошибки и измерять однопоточный рендер в одиночку.
    """

    def __init__(self, jobs=0, cache=None):
        self.jobs = jobs
        self.cache = cache if cache is not None else FrameCache()
        self._pool = None

    def _executor(self):
        if self.jobs <= 0:
            return None
        if self._pool is None:
            self._pool = ProcessPoolExecutor(max_workers=self.jobs)
        return self._pool

    def submit(self, func, task):
        """Выполняет задачу в пуле или прямо здесь -> результат как есть.

        Решение «из кэша или нет» принимает вызывающий: у кадра это осмысленно,
        у листа и ростера результат всегда новый.
        """
        pool = self._executor()
        if pool is None:
            return func(task)
        return pool.submit(func, task).result()

    def render_many(self, tasks, progress=None):
        """Рисует список задач по одной на кадр -> [Canvas].

        Лист и ростер собираются именно так: если отдать целый лист одному
        работнику, пул не поможет вовсе — внутри него кадры идут по очереди,
        и 18 кадров снова превращаются в 26 секунд. По одному кадру на задачу
        пул, наконец, задействован, а заодно появляется честный прогресс.
        """
        pool = self._executor()
        if pool is None:
            out = []
            for i, task in enumerate(tasks):
                out.append(unpack_canvas(*render_task(task)))
                if progress:
                    progress(i + 1, len(tasks))
            return out
        out = []
        for i, packed in enumerate(pool.map(render_task, tasks)):
            out.append(unpack_canvas(*packed))
            if progress:
                progress(i + 1, len(tasks))
        return out

    def frame(self, spec, anim="idle", frame=0, scale=1):
        """Один кадр. -> (png, meta); в meta есть cached и render_ms."""
        key = FrameCache.key(spec_json(spec), anim, frame, scale)
        entry = self.cache.get(key)
        if entry is not None:
            png, meta = entry
            return png, dict(meta, cached=True, render_ms=0)
        started = time.time()
        canvas = unpack_canvas(*self.submit(render_task,
                                            (spec.id, spec.to_json(), anim, frame)))
        if scale != 1:
            canvas = canvas.scaled(scale)
        png = canvas.to_png_bytes()
        meta = {"render_ms": int((time.time() - started) * 1000),
                "width": canvas.w, "height": canvas.h, "cached": False}
        self.cache.put(key, png, meta)
        return png, meta

    def sheet(self, spec, scale=1, progress=None, anims=None):
        """Спрайт-лист одного персонажа -> (png, meta)."""
        started = time.time()
        anims = G.build_anims(chosen_anims(anims))
        tasks, slots = _layout([spec], anims)
        sheet = Canvas(G.FW * _cols(anims), G.FH * len(anims))
        for canvas, (ox, oy) in zip(self.render_many(tasks, progress), slots):
            sheet.blit(canvas, ox, oy)
        return _finish(sheet, scale, started)

    def roster(self, specs, scale=1, progress=None, anims=None):
        """Несколько персонажей рядом -> (png, meta)."""
        started = time.time()
        anims = G.build_anims(chosen_anims(anims))
        tasks, slots = _layout(specs, anims)
        roster = Canvas(G.FW * _cols(anims) * len(specs), G.FH * len(anims))
        for canvas, (ox, oy) in zip(self.render_many(tasks, progress), slots):
            roster.blit(canvas, ox, oy)
        return _finish(roster, scale, started)

    def pack(self, spec, scale=1, progress=None, anims=None):
        """Готовый набор для игры -> zip-байты.

        Внутри: общий спрайт-лист, отдельный PNG на каждое движение, описание
        персонажа и текст с раскладкой. Играющему обычно нужен либо целый лист
        (GameMaker, Construct), либо отдельные полоски под каждую анимацию —
        поэтому в архиве и то, и другое.
        """
        started = time.time()
        names = chosen_anims(anims)
        built = G.build_anims(names)
        total_frames = sum(len(frames) for _n, frames in built)
        tasks, slots = _layout([spec], built)
        frames = self.render_many(tasks, progress)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            sheet = Canvas(G.FW * _cols(built), G.FH * len(built))
            for canvas, (ox, oy) in zip(frames, slots):
                sheet.blit(canvas, ox, oy)
            archive.writestr("%s/sheet.png" % spec.id, sheet.to_png_bytes())
            archive.writestr("%s/%s.json" % (spec.id, spec.id),
                             json.dumps(spec.to_json(), ensure_ascii=False, indent=2))
            archive.writestr("%s/combat.json" % spec.id,
                             json.dumps(_combat(spec, built),
                                        ensure_ascii=False, indent=2))
            archive.writestr("%s/README.txt" % spec.id,
                             _pack_readme(spec, built, G.FW, G.FH))
            for index, (name, poses) in enumerate(built):
                strip = Canvas(G.FW * len(poses), G.FH)
                offset = sum(len(f) for _n, f in built[:index])
                for col, canvas in enumerate(frames[offset:offset + len(poses)]):
                    strip.blit(canvas, col * G.FW, 0)
                archive.writestr("%s/animations/%s.png" % (spec.id, name),
                                 strip.to_png_bytes())
        meta = {"render_ms": int((time.time() - started) * 1000),
                "width": 0, "height": 0, "cached": False,
                "anims": len(built), "frames": total_frames,
                "bytes": len(buffer.getvalue())}
        return buffer.getvalue(), meta

    def measure(self, spec):
        return self.submit(measure_task, (spec.id, spec.to_json()))

    def shutdown(self):
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None


def _cols(anims):
    """Число колонок листа: сколько кадров в самом длинном движении."""
    return max(G.COLS, max(len(frames) for _name, frames in anims))


def _layout(specs, anims=None):
    """Задачи и места для кадров: по строке анимация, по колонке кадр.

    Возвращает параллельные списки: i-я задача кладётся в i-е место. Порядок
    задач совпадает с порядком anims, поэтому лист собирается ровно так же,
    как build_sheet(), а не «как получится по мере готовности».
    """
    anims = anims or G.ANIMS
    cols = _cols(anims)
    tasks, slots = [], []
    for index, spec in enumerate(specs):
        payload = spec.to_json()
        for row, (name, poses) in enumerate(anims):
            for col in range(len(poses)):
                tasks.append((spec.id, payload, name, col))
                slots.append((index * G.FW * cols + col * G.FW, row * G.FH))
    return tasks, slots


def _combat(spec, built):
    """Боевые данные набора: тайминги по кадрам и зоны попадания.

    Отдельный файл, а не поле в описании персонажа: описание отвечает за «кто
    это», а combat.json — за «как его бьют». Движок склеивает одно с другим
    сам, игре до этого дела нет.
    """
    out = {"character": spec.id, "frame": {"w": G.FW, "h": G.FH},
           "origin": {"x": G.FW // 2, "y": G.FOOT_Y},
           "note": "координаты зон — в пикселях кадра, левый верхний угол; "
                   "hit — куда бьёт персонаж, hurt — куда попасть можно",
           "anims": {}}
    for name, poses in built:
        out["anims"][name] = G.anim_timing(name, poses=poses, spec=spec)
    return out


def _pack_readme(spec, anims, fw, fh):
    """Текст внутри архива: что где лежит и как нарезать лист."""
    lines = [
        "SheetSmith — набор спрайтов «%s»" % spec.id,
        "",
        "sheet.png — весь набор: по строке движение, по колонке кадр.",
        "         Кадр %dx%d, сетка %d x %d, слева направо — кадры 0..n-1."
        % (fw, fh, _cols(anims), len(anims)),
        "animations/ — то же, но отдельным файлом на каждое движение,",
        "         одна строка, кадры слева направо.",
        "%s.json — описание персонажа (палитра и архетип), принимается" % spec.id,
        "         движком: py char_generator.py --characters <файл> --hero %s" % spec.id,
        "combat.json — тайминги по кадрам и зоны попадания для движка игры:",
        "         ms[i] — сколько миллисекунд держать кадр i, hit — куда бьёт",
        "         персонаж, hurt — куда по нему можно попасть. Координаты в",
        "         пикселях кадра; начало координат — левый верхний угол, а",
        "         «поставка» персонажа на землю — %d, %d (по центру кадра)."
        % (G.FW // 2, G.FOOT_Y),
        "",
        "Движения в порядке строк:",
    ]
    for index, (name, poses) in enumerate(anims):
        info = G.anim_info(name)
        ms = G.frame_ms(info)
        active = [i for i, boxes in enumerate(info.get("hits") or [])
                  if boxes] if info.get("hits") else []
        lines.append("  %2d. %-12s кадров: %d   %d мс   %s   %s"
                     % (index, name, len(poses), sum(ms),
                        info.get("group", ""),
                        "зацикливать" if info.get("loop") else "однократно"))
        if ms and (ms[0] != ms[-1] or len(set(ms)) > 1):
            lines.append("      мс по кадрам: %s"
                         % ", ".join(str(v) for v in ms))
        if active:
            lines.append("      активные кадры (удар): %s"
                         % ", ".join(str(i) for i in active))
    lines.append("")
    return "\n".join(lines)


def _finish(canvas, scale, started):
    if scale and scale != 1:
        canvas = canvas.scaled(scale)
    return canvas.to_png_bytes(), {
        "render_ms": int((time.time() - started) * 1000),
        "width": canvas.w, "height": canvas.h, "cached": False,
    }


def presets():
    """Базовые персонажи как готовые описания — для списка в редакторе."""
    out = []
    for hero in G.HEROES:
        spec = G.char_spec(hero)
        out.append({
            "id": spec.id,
            "materials": spec.to_json()["materials"],
            "arch": spec.to_json()["arch"],
            "neon": "#%02X%02X%02X" % tuple(spec.mats["neon"][:3]),
        })
    return out
