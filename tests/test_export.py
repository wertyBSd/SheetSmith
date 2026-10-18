"""Адаптеры выгрузки: C, Godot, Unity, веб.

Главная проверка здесь не «файл не падает», а «адаптер совпадает с манифестом».
Адаптеров будет много, и каждый следующий имеет смысл добавлять только пока
есть сверка: иначе через пять движков в архиве будет пять правд о таймингах.

Поэтому проверка идёт от манифеста: берём цифры из манифеста и ищем их в
выводе адаптера ровно столько раз, сколько раз они должны там быть.
"""
import json
import re
import sys

sys.path.insert(0, r"C:\Work\character-generator")
sys.path.insert(0, r"C:\Work\character-generator\server")

import char_generator as G
import export as X

FAILED = []


def check(name, condition, detail=""):
    print("  %s %s%s" % ("ок   " if condition else "ПРОВАЛ", name,
                         ("  — %s" % detail) if detail and not condition else ""))
    if not condition:
        FAILED.append(name)


def section(title):
    print("\n%s" % title)


ANIMS = ["idle", "punch", "round_kick"]


def fixture():
    spec = G.char_spec("tetrax")
    built = G.build_anims(ANIMS)
    cols = max(len(f) for _n, f in built)
    return spec, built, X.manifest(spec, built, G.FW, G.FH, cols), cols


def test_manifest():
    section("Манифест")
    spec, built, m, cols = fixture()

    check("схема названа и версионирована", m["schema"] == "sheetsmith/1", m["schema"])
    check("кадр и лист", m["frame"] == {"w": 80, "h": 128}
          and m["sheet"]["width"] == 80 * cols, m["sheet"])
    check("строк столько, сколько движений",
          m["sheet"]["rows"] == len(built) and len(m["anims"]) == len(built),
          m["sheet"]["rows"])
    check("точка постановки на землю",
          m["origin"] == {"x": 40, "y": G.FOOT_Y}, m["origin"])

    for index, (anim, (name, poses)) in enumerate(zip(m["anims"], built)):
        check("строка %s на своём месте" % name, anim["row"] == index, anim["row"])
        check("кадры %s совпадают с позами" % name, anim["frames"] == len(poses),
              anim["frames"])
        check("прямоугольники кадров %s ведут по листу" % name,
              [r["x"] for r in anim["rects"]] == [i * 80 for i in range(len(poses))]
              and all(r["y"] == index * 128 for r in anim["rects"]),
              anim["rects"])
        check("сумма таймингов %s" % name,
              anim["totalMs"] == sum(anim["ms"]), anim["totalMs"])
        check("активные кадры %s совпадают с хитбоксами" % name,
              anim["activeFrames"] == [i for i, b in enumerate(anim["hits"]) if b],
              anim["activeFrames"])

    # Порядок анимаций не должен зависеть от того, как Python перебирает словарь:
    # иначе две машины дают разный порядок строк в листе и diff выгрузок
    # показывает перестановку вместо изменений.
    again = X.manifest(spec, G.build_anims(list(reversed(ANIMS))),
                       G.FW, G.FH, cols)
    check("порядок задаётся списком, а не разбором",
          [a["name"] for a in again["anims"]] == list(reversed(ANIMS)),
          [a["name"] for a in again["anims"]])


def _c_arrays(text):
    """Массивы из C-заголовка -> {имя: [[числа, ...], ...]}."""
    out = {}
    for name, _rows, _cols, body in re.findall(
            r"static const int (\w+)\[(\d+)\]\[(\d+)\] = \{([^;]*)\};", text):
        rows = re.findall(r"\{([^{}]*)\}", body)
        out[name] = [[int(v) for v in row.replace("\n", " ").split(",") if v.strip()]
                     for row in rows]
    return out


def test_c():
    section("Адаптер C")
    spec, built, m, _cols = fixture()
    text = X.c_header(m)
    arrays = _c_arrays(text)

    check("страж от повторного включения", text.count("#ifndef TETRAX_H") == 1
          and "#define TETRAX_H" in text and text.rstrip().endswith("#endif /* TETRAX_H */"))
    check("размеры кадра в макросах",
          "#define TETRAX_FRAME_W 80" in text and "#define TETRAX_FRAME_H 128" in text)
    check("число движений", "#define TETRAX_ANIM_COUNT %d" % len(m["anims"]) in text)
    check("имена движений на месте",
          all('"%s"' % a["name"] in text for a in m["anims"]))

    check("массив таймингов есть", "TETRAX_ANIM_MS" in arrays, list(arrays))
    check("строки таймингов — по числу движений",
          len(arrays["TETRAX_ANIM_MS"]) == len(m["anims"]))
    for index, anim in enumerate(m["anims"]):
        row = arrays["TETRAX_ANIM_MS"][index][:anim["frames"]]
        check("тайминги %s совпали с манифестом" % anim["name"], row == anim["ms"],
              "%s против %s" % (row, anim["ms"]))
        padded = arrays["TETRAX_ANIM_MS"][index][anim["frames"]:]
        check("дырка таймингов %s заполнена, а не нулями" % anim["name"],
              all(v == anim["ms"][-1] for v in padded), padded)

    check("число кадров совпало",
          [r[0] for r in arrays["TETRAX_ANIM_FRAMES"]] == [a["frames"] for a in m["anims"]])
    check("зацикливание совпало",
          [r[0] for r in arrays["TETRAX_ANIM_LOOP"]] ==
          [1 if a["loop"] else 0 for a in m["anims"]])

    # Зоны склеены по кадрам всех движений подряд; индекс говорит, где какой.
    frames = [f for a in m["anims"] for f in a["hits"]]
    hits = arrays["TETRAX_HIT_BOXES"]
    hurts = arrays["TETRAX_HURT_BOXES"]
    check("число хитбоксов совпало",
          len(hits) == sum(len(f) for f in frames), len(hits))
    check("число хёртбоксов совпало",
          len(hurts) == sum(len(f) for a in m["anims"] for f in a["hurt"]), len(hurts))

    index = arrays["TETRAX_HIT_INDEX"]
    check("индекс хитбоксов — по кадрам всех движений",
          len(index) == len(frames), len(index))
    flat = [b for f in frames for b in f]
    for frame_no, (start, count) in enumerate(index):
        check("индекс кадра %d указывает на свои зоны" % frame_no,
              hits[start:start + count] ==
              [[b["x"], b["y"], b["x"] + b["w"] - 1, b["y"] + b["h"] - 1]
               for b in frames[frame_no]],
              (start, count))

    # Правая-нижняя граница включительная — если её не вычесть, зона на кадре
    # в листе сдвинется на пиксель и перестанет ловить удар.
    if flat:
        first = flat[0]
        check("правый-нижний угол включительный",
              hits[0] == [first["x"], first["y"],
                          first["x"] + first["w"] - 1, first["y"] + first["h"] - 1],
              hits[0])
        check("зоны не пустые", hits and all(r[2] >= r[0] and r[3] >= r[1] for r in hits))


def test_godot():
    section("Адаптеры Godot")
    spec, built, m, _cols = fixture()
    tres = X.godot_tres(m)
    gd = X.godot_gd(m)

    check("это SpriteFrames", tres.startswith('[gd_resource type="SpriteFrames"'))
    check("лист объявлен ext_resource в шапке",
          '[ext_resource type="Texture2D" path="res://tetrax/sheet.png"' in tres,
          tres.splitlines()[0:3])
    check("ext_resource стоит до sub_resource и [resource]",
          tres.index("[ext_resource") < tres.index("[sub_resource")
          < tres.index("[resource]"),
          "неверный порядок блоков")
    check("в шапке нет runtime-свойства external_resources",
          "external_resources" not in tres, "свойство вместо объявления")
    check("load_steps посчитан",
          tres.splitlines()[0] == '[gd_resource type="SpriteFrames" load_steps=%d '
          'format=3]' % (1 + sum(a["frames"] for a in m["anims"]) + 1),
          tres.splitlines()[0])
    check("по sub_resource на кадр",
          tres.count("[sub_resource type=\"AtlasTexture\"") ==
          sum(a["frames"] for a in m["anims"]))
    check("все AtlasTexture ссылаются на лист",
          tres.count('atlas = ExtResource("1_sheet")') ==
          sum(a["frames"] for a in m["anims"]))

    for anim in m["anims"]:
        expected = "region = Rect2(%d, %d, 80, 128)" % (0, anim["row"] * 128)
        check("кадры %s ссылаются на строку листа" % anim["name"],
              expected in tres, expected)
        check("анимация %s объявлена" % anim["name"],
              '"name": &"%s"' % anim["name"] in tres)
        check("зацикливание %s перенесено" % anim["name"],
              ('"loop": %s, "name": &"%s"' % ("true" if anim["loop"] else "false",
                                             anim["name"])) in tres)
        # Длительность в .tres измеряется в кадрах при speed=60, поэтому
        # миллисекунды обязаны превратиться в мс/60 — иначе анимация в Godot
        # проиграется в шестьдесят раз быстрее.
        for col, ms in enumerate(anim["ms"]):
            check("кадр %d %s: %d мс -> %.3f кадра" % (col, anim["name"], ms, ms / 60.0),
                  '"duration": %.3f' % (ms / 60.0) in tres)

    check("GDScript без class_name (иначе персонажи столкнутся именами)",
          "class_name" not in gd, "class_name есть")
    check("GDScript расширяет Resource", gd.startswith("extends Resource"))
    check("тайминги в GDScript совпали",
          all("PackedInt32Array([%s])" % ", ".join(str(v) for v in a["ms"]) in gd
              for a in m["anims"]))
    check("активные кадры в GDScript совпали",
          all("PackedInt32Array([%s])" % ", ".join(str(v) for v in a["activeFrames"]) in gd
              for a in m["anims"]))
    check("зоны в GDScript — массивы, а не строки",
          '"hits": [[], [Rect2(' in gd and "': [[" not in gd,
          [line for line in gd.splitlines() if "'" in line][:2])
    check("размер кадра в GDScript",
          "const FRAME_W := 80" in gd and "const FRAME_H := 128" in gd)


def test_unity():
    section("Адаптер Unity")
    spec, built, m, _cols = fixture()
    cs = X.unity_cs(m)

    check("пространство имён", "namespace SheetSmith.Generated" in cs)
    check("не требует C# 9", "= new()" not in cs and "var " not in cs,
          "найден целевой new/var")
    check("размеры кадра", "FrameW = 80" in cs and "FrameH = 128" in cs)
    for anim in m["anims"]:
        check("анимация %s объявлена" % anim["name"],
              'name = "%s", row = %d, frames = %d' % (anim["name"], anim["row"],
                                                      anim["frames"]) in cs)
        check("тайминги %s совпали" % anim["name"],
              "ms = new int[] { %s }," % ", ".join(str(v) for v in anim["ms"]) in cs)
        flat = [b for f in anim["hits"] for b in f]
        check("хитбоксы %s совпали" % anim["name"],
              all("new Rect(%d, %d, %d, %d)" % (b["x"], b["y"], b["w"], b["h"]) in cs
                  for b in flat), len(flat))
        # Границы кадров обязаны идти в том же порядке, что и сами зоны.
        offsets, total = [], 0
        for frame in anim["hits"]:
            offsets.append("new Vector2Int(%d, %d)" % (total, len(frame)))
            total += len(frame)
        check("границы хитбоксов %s в том же порядке" % anim["name"],
              "hitBoxRanges = new Vector2Int[] { %s }," % ", ".join(offsets) in cs,
              offsets[:2])


def test_web():
    section("Адаптер веба")
    spec, built, m, _cols = fixture()
    data = json.loads(X.web_json(m))

    check("схема на месте", data["schema"] == "sheetsmith/1")
    check("путь к листу", data["sheet"] == "tetrax/sheet.png", data["sheet"])
    check("анимаций столько же", len(data["animations"]) == len(m["anims"]))
    check("имена совпали",
          [a["key"] for a in data["animations"]] == [a["name"] for a in m["anims"]])

    for got, want in zip(data["animations"], m["anims"]):
        check("повтор %s перенесён" % want["name"],
              got["repeat"] == (-1 if want["loop"] else 0), got["repeat"])
        check("кадров %s столько же" % want["name"],
              got["frameTotal"] == want["frames"] and
              len(got["frames"]) == want["frames"], got["frameTotal"])
        for col, frame in enumerate(got["frames"]):
            check("длительность %s кадр %d" % (want["name"], col),
                  frame["duration"] == want["ms"][col], frame["duration"])
            check("рамка кадра %s кадр %d" % (want["name"], col),
                  frame["frame"] == want["rects"][col], frame["frame"])
            check("зоны %s кадр %d" % (want["name"], col),
                  frame["hitboxes"] == want["hits"][col] and
                  frame["hurtboxes"] == want["hurt"][col])
            check("активность %s кадр %d" % (want["name"], col),
                  frame["active"] == (col in want["activeFrames"]))


def test_consistency():
    section("Все адаптеры одинаковы между собой")
    spec, built, m, _cols = fixture()
    web = json.loads(X.web_json(m))
    gd = X.godot_gd(m)
    cs = X.unity_cs(m)
    hdr = X.c_header(m)
    arrays = _c_arrays(hdr)

    for index, (anim, got) in enumerate(zip(m["anims"], web["animations"])):
        ms_c = arrays["TETRAX_ANIM_MS"][index][:anim["frames"]]
        ms_cs = [int(v) for v in
                 re.search(r'name = "%s".*?ms = new int\[\] \{ ([^}]*) \}'
                           % re.escape(anim["name"]), cs, re.S).group(1).split(",")]
        ms_gd = [int(v) for v in re.search(
            r'"%s": \{[^}]*?"ms": PackedInt32Array\(\[([0-9, ]*)\]\)'
            % re.escape(anim["name"]), gd).group(1).split(",")]
        check("тайминги %s совпадают в C, Unity, вебе и Godot" % anim["name"],
              ms_c == ms_cs == ms_gd == anim["ms"] ==
              [f["duration"] for f in got["frames"]],
              "C=%s Unity=%s Godot=%s веб=%s" % (ms_c, ms_cs, ms_gd,
                                                  [f["duration"] for f in got["frames"]]))
        check("число кадров %s совпадает везде" % anim["name"],
              arrays["TETRAX_ANIM_FRAMES"][index][0] == anim["frames"] ==
              got["frameTotal"], arrays["TETRAX_ANIM_FRAMES"][index])

    check("число анимаций совпадает везде",
          int(re.search(r"#define TETRAX_ANIM_COUNT (\d+)", hdr).group(1)) ==
          len(web["animations"]) == gd.count("\t\t\"ms\""), len(web["animations"]))


def main():
    test_manifest()
    test_c()
    test_godot()
    test_unity()
    test_web()
    test_consistency()
    print()
    if FAILED:
        print("ПРОВАЛ: %d проверок" % len(FAILED))
        for name in FAILED:
            print("  - %s" % name)
        return 1
    print("OK: адаптеры совпадают с манифестом.")
    return 0


if __name__ == "__main__":
    sys.exit(main())