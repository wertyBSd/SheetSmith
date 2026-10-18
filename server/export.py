"""Выгрузка набора в чужие движки.

Главное решение модуля: есть ОДИН манифест (`sheetsmith.json`) — плоская
раскладка набора: где в листе лежит каждый кадр, сколько он живёт, где зоны.
Все адаптеры собираются из него же и ничего не знают про движок. Иначе через
три адаптера каждая мелочь начинает разъезжаться: Godot считает кадры иначе,
Unity иначе, веб иначе, и «просто поправить» превращается в проверку всего
списка каждый раз.

Поэтому адаптер умеет ровно одно: взять манифест и вернуть текст файла.
Проверяется это одним тестом, который сверяет каждый адаптер с манифестом —
иначе «работает» означало бы «работает, пока никто не менял манифест».

Чужие форматы пишутся руками, без шаблонного движка по json: в Godot это
`.tres` с таймингами и `SpriteFrames`, в Unity — C# с массивами, в вебе —
конфиг анимаций. Универсальной грамматики у них нет, и общий код всё равно
оказался бы вложенными if с экранированием.

Координаты везде в пикселях КАДРА (не листа), начало — левый верхний угол.
Позиция на земле — `origin`: движок рисует спрайт, а игре нужно знать, куда
поставить ноги персонажа, чтобы он не «висел» в воздухе.
"""
import json

import char_generator as G

SCHEMA = "sheetsmith/1"
MANIFEST = "sheetsmith.json"
GENERATOR = "SheetSmith"


# --- манифест ----------------------------------------------------------------

def manifest(spec, built, fw, fh, cols):
    """Каноническое описание набора. Единственный источник для адаптеров.

    Плоское и сортированное: порядок анимаций задаётся списком, а не тем, как
    они легли в словарь при разборе JSON. Иначе на двух машинах архив
    раскладывался бы по-разному, и diff двух выгрузок показывал бы не
    содержательные изменения, а перестановку строк.
    """
    anims = []
    for row, (name, poses) in enumerate(built):
        info = G.anim_info(name)
        timing = G.anim_timing(name, poses=poses, spec=spec)
        anims.append({
            "name": name,
            "group": info.get("group", ""),
            "row": row,
            "frames": len(poses),
            "loop": bool(info.get("loop")),
            "ms": timing["ms"],
            "totalMs": timing["totalMs"],
            # Прямоугольники кадра в листе: движку не нужно гадать, где что
            # лежит, — ему остаётся только нарисовать.
            "rects": [{"x": col * fw, "y": row * fh, "w": fw, "h": fh}
                      for col in range(len(poses))],
            "hits": timing["hits"],
            "hurt": timing["hurt"],
            "activeFrames": [i for i, boxes in enumerate(timing["hits"]) if boxes],
        })
    return {
        "schema": SCHEMA,
        "generator": GENERATOR,
        "character": spec.id,
        "frame": {"w": fw, "h": fh},
        "sheet": {"cols": cols, "rows": len(built),
                  "width": fw * cols, "height": fh * len(built)},
        "origin": {"x": fw // 2, "y": G.FOOT_Y},
        "anims": anims,
    }


def _c_array(name, rows, per_row, fmt="%d", pad=None):
    """Многомерный массив в виде `{...};` — так его понимает C и C++.

    Прямоугольный массив в C обязан быть прямоугольным, а движения разной
    длины. Дырки добиваются последним значением строки, а не нулём: читатель,
    доверившийся ширине массива вместо ANIM_FRAMES, должен получить осмысленное
    число (0 мс означал бы «мгновенный кадр» — это тихая поломка плеера).
    """
    if not rows:
        return "static const int %s[1][%d] = {{0}};\n" % (name, per_row)
    body = []
    for row in rows:
        values = list(row)
        if pad is not None:
            while len(values) < per_row:
                values.append(values[-1] if values else pad)
        body.append("    {" + ", ".join(fmt % v for v in values) + "}")
    return "static const int %s[%d][%d] = {\n%s\n};\n" % (name, len(rows),
                                                          per_row,
                                                          ",\n".join(body))


def _c_boxes(frames):
    """Прямоугольники всех кадров в один плоский массив `[n][4]`.

    Принимает список списков — список кадров, — и склеивает их, потому что
    рядом всегда идёт `_c_index`: тот же порядок, те же границы, без
    повторного обхода данных.
    """
    out = []
    for frame in frames:
        for box in frame:
            # Правый-нижний угол, а не левый-верхний с шириной: в C и в любом
            # движке с прямоугольниками это разные соглашения, а расхождение в
            # один пиксель — это хитбокс, который не ловит удар.
            out.append([box["x"], box["y"], box["x"] + box["w"] - 1,
                        box["y"] + box["h"] - 1])
    return out


def _c_index(frames):
    """Границы кадров в склеенном массиве: `[n][2]` — начало и длина.

    Без этого индекса нельзя взять зоны кадра: склеенный массив не знает,
    где кончается кадр 0 и начинается кадр 1.
    """
    out, total = [], 0
    for frame in frames:
        out.append([total, len(frame)])
        total += len(frame)
    return out


def _guard(name):
    """Имя массива в верхнем регистре — так их видно в чужом коде."""
    return name.upper().replace("-", "_").replace(".", "_")


# --- C: заголовок для raylib, SDL, Godot C++, любого движка с C API ----------

def c_header(m):
    """Готовый C-заголовок: подключил — получил тайминги и зоны.

    Это самый дешёвый для потребителя вариант: `char_generator` становится
    `sheetsmith.h` с массивами, которые компилируются куда угодно. Именно его
    переваривает и Godot через GDExtension, и любой C++-движок.
    """
    hero = _guard(m["character"])
    out = ["/* Generated by %s — do not edit." % GENERATOR,
           " * Character: %s   schema: %s" % (m["character"], m["schema"]),
           " * Frame %dx%d, sheet %dx%d, origin (%d, %d)."
           % (m["frame"]["w"], m["frame"]["h"], m["sheet"]["width"],
              m["sheet"]["height"], m["origin"]["x"], m["origin"]["y"]),
           " * Coordinates are frame pixels; box right/bottom are inclusive.",
           " * Flat box arrays are ordered anim by anim, frame by frame; the",
           " * first frame of an anim starts after ANIM_FRAMES of the ones before.",
           " */",
           "#ifndef %s_H" % hero,
           "#define %s_H" % hero,
           "",
           "#define %s_FRAME_W %d" % (hero, m["frame"]["w"]),
           "#define %s_FRAME_H %d" % (hero, m["frame"]["h"]),
           "#define %s_ORIGIN_X %d" % (hero, m["origin"]["x"]),
           "#define %s_ORIGIN_Y %d" % (hero, m["origin"]["y"]),
           "#define %s_ANIM_COUNT %d" % (hero, len(m["anims"])),
           ""]

    out.append("static const char *const %s_ANIM_NAMES[%s_ANIM_COUNT] = {"
               % (hero, hero))
    out.append("    " + ", ".join('"%s"' % a["name"] for a in m["anims"])
               + ", NULL,\n};")

    out.append(_c_array("%s_ANIM_MS" % hero, [a["ms"] for a in m["anims"]],
                        max(len(a["ms"]) for a in m["anims"]),
                        pad=100))
    out.append(_c_array("%s_ANIM_FRAMES" % hero,
                        [[a["frames"]] for a in m["anims"]], 1))
    out.append(_c_array("%s_ANIM_LOOP" % hero,
                        [[1 if a["loop"] else 0] for a in m["anims"]], 1))
    # Зоны склеены в один плоский массив в порядке «анимация за анимацией, внутри
    # кадр за кадром», а границы лежат рядом в *_INDEX. Начало кадра анимации
    # читается суммой ANIM_FRAMES предыдущих — это записано комментарием в
    # шапке, потому что иначе половина читателей угадывает порядок.
    hits = [f for a in m["anims"] for f in a["hits"]]
    hurts = [f for a in m["anims"] for f in a["hurt"]]
    out.append(_c_array("%s_HIT_BOXES" % hero, _c_boxes(hits), 4))
    out.append(_c_array("%s_HURT_BOXES" % hero, _c_boxes(hurts), 4))
    out.append(_c_array("%s_HIT_INDEX" % hero, _c_index(hits), 2))
    out.append(_c_array("%s_HURT_INDEX" % hero, _c_index(hurts), 2))

    out.append("")
    out.append("#endif /* %s_H */" % hero)
    return "\n".join(out) + "\n"


# --- Godot: .tres со SpriteFrames и ресурс с таймингами -----------------------

def godot_tres(m):
    """Ресурс Godot 4: SpriteFrames с таймингами кадров.

    Godot хранит кадры как `AtlasTexture` со ссылкой на область в атласе,
    поэтому на каждый кадр нужен свой sub_resource, а лист подключается
    ext_resource в шапке файла. Порядок блоков в .tres обязателен:
    ext_resource -> sub_resource -> [resource]. Свойство `external_resources`
    здесь не годится — это данные времени выполнения, а не объявление ресурса,
    и Godot такой файл не прочитает.

    Длительность в .tres задана в КАДРАХ при speed=60, то есть миллисекунды
    делятся на 60. Сами миллисекунды остаются в combat.gd: движок, играющий на
    своей скорости, читает их оттуда и не пересчитывает.
    """
    fw, fh = m["frame"]["w"], m["frame"]["h"]
    frames_total = sum(a["frames"] for a in m["anims"])
    lines = ['[gd_resource type="SpriteFrames" load_steps=%d format=3]'
             # load_steps = внешние + внутренние + сам ресурс.
             % (1 + frames_total + 1),
             '[ext_resource type="Texture2D" path="res://%s/sheet.png" id="1_sheet"]'
             % m["character"]]
    for anim in m["anims"]:
        for col in range(anim["frames"]):
            lines.append(
                '[sub_resource type="AtlasTexture" id="AtlasTexture_%s_%d"]'
                % (anim["name"], col))
            lines.append('atlas = ExtResource("1_sheet")')
            lines.append("region = Rect2(%d, %d, %d, %d)"
                         % (col * fw, anim["row"] * fh, fw, fh))
    lines.append('[resource]')
    for anim in m["anims"]:
        frames = ", ".join(
            '{"duration": %.3f, "texture": SubResource("AtlasTexture_%s_%d")}'
            % (ms / 60.0, anim["name"], col)
            for col, ms in enumerate(anim["ms"]))
        lines.append('animations = [{ "frames": [%s], "loop": %s, '
                     '"name": &"%s", "speed": 60.0 }]'
                     % (frames, "true" if anim["loop"] else "false", anim["name"]))
    return "\n".join(lines) + "\n"


def godot_gd(m):
    """GDScript-ресурс с зонами и таймингами: движок спрашивает, а не читает .tres.

    Зоны в .tres не помещаются (там только кадры), поэтому они едут в обычном
    словаре. Скрипт отдаёт данные движку, а не рисует ничего: SheetSmith не
    должен становиться ещё и игровым движком.

    `class_name` здесь намеренно не задаётся: он уникален в пределах проекта,
    и десяток выгруженных бойцов столкнулись бы одним именем. Скрипт берут
    через `preload("res://<герой>/godot/combat.gd")` — это работает и для
    любого числа персонажей.
    """
    hero = m["character"]
    out = ["extends Resource",
           "## Generated by %s — do not edit. Character: %s" % (GENERATOR, hero),
           "## Comments are English on purpose: the file lands in other projects.",
           "##",
           "## Usage:",
           "##   const C = preload(\"res://%s/godot/combat.gd\")" % hero,
           "##   C.anims[\"punch\"][\"ms\"]   # timings",
           "##   C.hit_boxes(\"punch\", 1)   # boxes of frame 1",
           "",
           "const FRAME_W := %d" % m["frame"]["w"],
           "const FRAME_H := %d" % m["frame"]["h"],
           "const ORIGIN := Vector2(%d, %d)" % (m["origin"]["x"], m["origin"]["y"]),
           "",
           "# anim -> { ms: PackedInt32Array, loop: bool, active: PackedInt32Array,",
           "#           hits: [[Rect2, ...]], hurt: [[Rect2, ...]] }",
           "# Coordinates are frame pixels, top-left origin.",
           "var anims := {"]
    for anim in m["anims"]:
        out.append('\t"%s": {' % anim["name"])
        out.append('\t\t"ms": PackedInt32Array([%s]),'
                   % ", ".join(str(v) for v in anim["ms"]))
        out.append('\t\t"loop": %s,' % ("true" if anim["loop"] else "false"))
        out.append('\t\t"active": PackedInt32Array([%s]),'
                   % ", ".join(str(v) for v in anim["activeFrames"]))
        out.append('\t\t"hits": %s,' % _rect_rows(anim["hits"]))
        out.append('\t\t"hurt": %s,' % _rect_rows(anim["hurt"]))
        out.append("\t},")
    out.append("}")
    out.append("")
    out.append("""func frame_ms(anim: String, frame: int) -> int:
	var a: Dictionary = anims.get(anim, {})
	if a.is_empty():
		return 0
	var ms: PackedInt32Array = a["ms"]
	return ms[frame] if frame < ms.size() else ms[ms.size() - 1]


func is_active(anim: String, frame: int) -> bool:
	var a: Dictionary = anims.get(anim, {})
	if a.is_empty():
		return false
	return (a["active"] as PackedInt32Array).has(frame)


func hit_boxes(anim: String, frame: int) -> Array:
	return _boxes(anims, anim, "hits", frame)


func hurt_boxes(anim: String, frame: int) -> Array:
	return _boxes(anims, anim, "hurt", frame)


func _boxes(all: Dictionary, anim: String, key: String, frame: int) -> Array:
	if not all.has(anim):
		return []
	var frames: Array = all[anim][key]
	return frames[frame] if frame < frames.size() else []
""")
    return "\n".join(out) + "\n"


def _rect_rows(frames):
    """Массив кадров -> [[Rect2(...), ...], ...] в синтаксисе GDScript."""
    rows = []
    for frame in frames:
        rows.append("[%s]" % ", ".join(
            "Rect2(%d, %d, %d, %d)" % (b["x"], b["y"], b["w"], b["h"])
            for b in frame))
    return "[%s]" % ", ".join(rows) if rows else "[]"


# --- Unity: C# с таймингами и зонами ----------------------------------------

def unity_cs(m):
    """C# для Unity: массивы таймингов и зон, хранение в ScriptableObject.

    Unity не читает JSON в ассетах без плагинов, поэтому данные приходят
    готовым C#-классом, а сериализуются инспектором. Rect — в пикселях
    спрайта, как и везде; пересчёт в миры делает уже движок игры.
    """
    hero = m["character"].capitalize()
    anims = m["anims"]

    out = ["// Generated by %s — do not edit. Character: %s"
           % (GENERATOR, m["character"]),
           "// Generated code is commented in English on purpose: it lands in other",
           "// people's codebases, where a Russian comment reads as a foreign artefact.",
           "// Rect values are frame pixels, top-left origin, as everywhere else.",
           "using System.Collections.Generic;",
           "using UnityEngine;",
           "",
           "namespace SheetSmith.Generated",
           "{",
           "\tpublic class %sAnim : ScriptableObject" % hero,
           "\t{",
           "\t\tpublic string name;",
           "\t\tpublic int row;",
           "\t\tpublic int frames;",
           "\t\tpublic bool loop;",
           "\t\tpublic int[] ms;",
           "\t\tpublic int[] activeFrames;",
           "\t\tpublic Rect[] hitBoxes;   // per frame, see hitBoxRanges",
           "\t\tpublic Rect[] hurtBoxes;",
           "\t\tpublic Vector2Int[] hitBoxRanges;  // [start, count] per frame",
           "\t\tpublic Vector2Int[] hurtBoxRanges;",
           "\t}",
           "",
           "\tpublic class %sCombat : ScriptableObject" % hero,
           "\t{",
           "\t\tpublic const int FrameW = %d;" % m["frame"]["w"],
           "\t\tpublic const int FrameH = %d;" % m["frame"]["h"],
           "\t\tpublic static readonly Vector2 Origin = new Vector2(%d, %d);"
           % (m["origin"]["x"], m["origin"]["y"]),
           "",
           "\t\tpublic List<%sAnim> anims;" % hero,
           "",
           "\t\t/// Frame duration in ms; out of range clamps to the last one.",
           "\t\tpublic int FrameMs(%sAnim a, int frame)" % hero,
           "\t\t{",
           "\t\t\tif (a == null || a.ms == null || a.ms.Length == 0) return 0;",
           "\t\t\treturn a.ms[Mathf.Clamp(frame, 0, a.ms.Length - 1)];",
           "\t\t}",
           "",
           "\t\t/// Frame rectangle inside the sheet (0-based).",
           "\t\tpublic Rect SheetRect(%sAnim a, int frame)" % hero,
           "\t\t{",
           "\t\t\treturn new Rect(frame * FrameW, a.row * FrameH, FrameW, FrameH);",
           "\t\t}",
           "",
           "\t\t/// Boxes of one frame; empty when there is no active window.",
           "\t\tpublic Rect[] HitBoxes(%sAnim a, int frame)" % hero,
           "\t\t{",
           "\t\t\treturn Slice(a.hitBoxes, a.hitBoxRanges, frame);",
           "\t\t}",
           "",
           "\t\tpublic Rect[] HurtBoxes(%sAnim a, int frame)" % hero,
           "\t\t{",
           "\t\t\treturn Slice(a.hurtBoxes, a.hurtBoxRanges, frame);",
           "\t\t}",
           "",
           "\t\tstatic Rect[] Slice(Rect[] all, Vector2Int[] ranges, int frame)",
           "\t\t{",
           "\t\t\tif (all == null || ranges == null || frame < 0",
           "\t\t\t\t|| frame >= ranges.Length || ranges[frame].y <= 0)",
           "\t\t\t\treturn System.Array.Empty<Rect>();",
           "\t\t\tVector2Int r = ranges[frame];",
           "\t\t\tRect[] outp = new Rect[r.y];",
           "\t\t\tSystem.Array.Copy(all, r.x, outp, 0, r.y);",
           "\t\t\treturn outp;",
           "\t\t}",
           "\t}",
           "",
           "\tpublic static class %sData" % hero,
           "\t{",
           # Явный new List<...>, а не new(): целевое «new» требует C# 9, а
           # версия Unity у пользователя неизвестна — ломать ему компиляцию
           # из-за удобства генератора нельзя.
           "\t\tpublic static readonly List<%sAnim> Anims = "
           "new List<%sAnim>() {" % (hero, hero)]

    for anim in anims:
        out.append("\t\t\tnew %sAnim {" % hero)
        out.append("\t\t\t\tname = \"%s\", row = %d, frames = %d, loop = %s,"
                   % (anim["name"], anim["row"], anim["frames"],
                      "true" if anim["loop"] else "false"))
        out.append("\t\t\t\tms = new int[] { %s },"
                   % ", ".join(str(v) for v in anim["ms"]))
        out.append("\t\t\t\tactiveFrames = new int[] { %s },"
                   % (", ".join(str(v) for v in anim["activeFrames"]) or "0"))
        out.append("\t\t\t\thitBoxes = new Rect[] { %s },"
                   % _cs_rects(anim["hits"]))
        out.append("\t\t\t\thurtBoxes = new Rect[] { %s },"
                   % _cs_rects(anim["hurt"]))
        out.append("\t\t\t\thitBoxRanges = new Vector2Int[] { %s },"
                   % _cs_ranges(anim["hits"]))
        out.append("\t\t\t\thurtBoxRanges = new Vector2Int[] { %s },"
                   % _cs_ranges(anim["hurt"]))
        out.append("\t\t\t},")
    out.append("\t\t};")
    out.append("\t}")
    out.append("}")
    return "\n".join(out) + "\n"


def _cs_rects(frames):
    rows = ["new Rect(%d, %d, %d, %d)" % (b["x"], b["y"], b["w"], b["h"])
            for frame in frames for b in frame]
    return ", ".join(rows) if rows else "new Rect(0,0,0,0)"


def _cs_ranges(frames):
    rows, total = [], 0
    for frame in frames:
        rows.append("new Vector2Int(%d, %d)" % (total, len(frame)))
        total += len(frame)
    return ", ".join(rows) if rows else "new Vector2Int(0, 0)"


# --- веб: конфиг анимаций, готовый для Phaser / PixiJS ----------------------

def web_json(m):
    """Конфиг для браузерных движков: Phaser и PixiJS читают как есть.

    Формат — то, что Phaser называет animation config: имя, покадровая
    длительность в миллисекундах, повтор. Кадр задаётся рамкой в листе, а не
    индексом, потому что лист один на всё и движок умеет брать по рамке.
    """
    return json.dumps({
        "schema": SCHEMA,
        "character": m["character"],
        "frame": m["frame"],
        "sheet": "%s/sheet.png" % m["character"],
        "origin": m["origin"],
        "animations": [{
            "key": a["name"],
            "group": a["group"],
            "frameTotal": a["frames"],
            "frameRate": (round(1000.0 / a["totalMs"], 4) if a["totalMs"] else 0),
            "skipMissedFrames": True,
            "delay": 0,
            "repeat": -1 if a["loop"] else 0,
            "frames": [{
                "filename": m["character"] + "/sheet.png",
                "frame": {"x": col * m["frame"]["w"], "y": a["row"] * m["frame"]["h"],
                          "w": m["frame"]["w"], "h": m["frame"]["h"]},
                "duration": ms,
                "hitboxes": a["hits"][col],
                "hurtboxes": a["hurt"][col],
                "active": col in a["activeFrames"],
            } for col, ms in enumerate(a["ms"])],
        } for a in m["anims"]],
    }, ensure_ascii=False, indent=2)


# --- сборка архива -----------------------------------------------------------

def write_pack(archive, spec, built, fw, fh, cols):
    """Кладёт в архив манифест и все адаптеры, собранные из него.

    Возвращает (манифест, {путь: текст}) — тесты сверяют адаптеры именно с этим
    манифестом, поэтому собирать их второй раз нельзя: проверка сравнивала бы
    адаптер с самим собой и всегда проходила.
    """
    m = manifest(spec, built, fw, fh, cols)
    archive.writestr(MANIFEST, json.dumps(m, ensure_ascii=False, indent=2))

    hero = spec.id
    files = {
        "%s/%s.h" % (hero, hero): c_header(m),
        "%s/godot/%s_frames.tres" % (hero, hero): godot_tres(m),
        "%s/godot/combat.gd" % hero: godot_gd(m),
        "%s/unity/%s.cs" % (hero, hero): unity_cs(m),
        "%s/web/animations.json" % hero: web_json(m),
    }
    for name, text in files.items():
        archive.writestr(name, text)
    return m, files