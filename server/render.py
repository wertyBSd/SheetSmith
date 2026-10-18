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
import json
import os
import struct
import sys
import time
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor

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
    anims = dict(G.ANIMS)
    if name not in anims:
        raise SpecError(["анимация '%s' неизвестна, можно: %s"
                         % (name, ", ".join(anims))])
    frames = anims[name]
    if not isinstance(frame, int) or not (0 <= frame < len(frames)):
        raise SpecError(["кадр %r вне диапазона 0..%d" % (frame, len(frames) - 1)])
    return frames[frame]


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

    def sheet(self, spec, scale=1, progress=None):
        """Спрайт-лист одного персонажа -> (png, meta)."""
        started = time.time()
        payload = spec.to_json()
        tasks, slots = _layout([spec])
        sheet = Canvas(G.FW * G.COLS * 1, G.FH * G.ROWS)
        for canvas, (ox, oy) in zip(self.render_many(tasks, progress), slots):
            sheet.blit(canvas, ox, oy)
        return _finish(sheet, scale, started)

    def roster(self, specs, scale=1, progress=None):
        """Несколько персонажей рядом -> (png, meta)."""
        started = time.time()
        tasks, slots = _layout(specs)
        roster = Canvas(G.FW * G.COLS * len(specs), G.FH * G.ROWS)
        for canvas, (ox, oy) in zip(self.render_many(tasks, progress), slots):
            roster.blit(canvas, ox, oy)
        return _finish(roster, scale, started)

    def measure(self, spec):
        return self.submit(measure_task, (spec.id, spec.to_json()))

    def shutdown(self):
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None


def _layout(specs):
    """Задачи и места для кадров: по строке анимация, по колонке кадр.

    Возвращает параллельные списки: i-я задача кладётся в i-е место. Порядок
    задач совпадает с ANIMS, поэтому лист собирается ровно как build_sheet(),
    а не «как получится по мере готовности».
    """
    tasks, slots = [], []
    for index, spec in enumerate(specs):
        payload = spec.to_json()
        for row, (name, poses) in enumerate(G.ANIMS):
            for col in range(len(poses)):
                tasks.append((spec.id, payload, name, col))
                slots.append((index * G.FW * G.COLS + col * G.FW, row * G.FH))
    return tasks, slots


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
