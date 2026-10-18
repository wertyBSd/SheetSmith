"""Тесты API-слоя: спецификация, JSON, валидация, схема.

Быстрые: считают секунды, потому что рисуют не листы, а пару одиночных
кадров. Golden-тест (test_parity.py) отвечает на вопрос «не поехала ли
картинка», этот — «правильно ли движок описывает персонажа».

Запуск: py tests\\test_spec_api.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import char_generator as G   # noqa: E402

EXAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "characters.example.json")

FAILED = []


def check(name, condition, detail=""):
    status = "ок   " if condition else "ПРОВАЛ"
    tail = "  — %s" % detail if detail and not condition else ""
    print("  %s %s%s" % (status, name, tail))
    if not condition:
        FAILED.append(name)


def section(title):
    print("\n%s" % title)


def same_pixels(a, b):
    """Canvas не умеет сравниваться через ==, поэтому сравниваем пиксели."""
    return (a.w, a.h) == (b.w, b.h) and all(
        a.px[y][x] == b.px[y][x] for y in range(a.h) for x in range(a.w))


def test_roundtrip():
    section("Спецификация -> JSON -> спецификация (шестеро базовых)")
    for hero in G.HEROES:
        spec = G.char_spec(hero)
        back = G.CharSpec.from_json(hero, spec.to_json())
        check("%s восстанавливается из своего JSON" % hero,
              back.mats == spec.mats and back.arch == spec.arch)


def test_merge():
    section("Слияние поверх существующего (документированное поведение)")
    spec = G.CharSpec.from_json("wraith", {"materials": {"neon": "#B04CFF"}})
    check("неон поменялся", G._hex(spec.mats["neon"]) == "#B04CFF")
    check("остальная палитра осталась от wraith",
          G._hex(spec.mats["robe"]["ramp"][1]) == G._hex(G.char_spec("wraith").mats["robe"]["ramp"][1]))
    check("архетип остался от wraith", spec.arch["head"] == "mask")
    check("материал не мутируется в реестре",
          G._hex(G.char_spec("wraith").mats["neon"]) == "#9966FF")


def test_new_character():
    section("Новый персонаж: нейтральный архетип и ошибки вместо KeyError")
    with open(EXAMPLE, "r", encoding="utf-8") as fh:
        example = json.load(fh)
    payload = example["characters"]["cipher"]

    full = G.CharSpec.from_json("cipher_test", payload)
    check("все ключи архетипа на месте",
          all(k in full.arch for k in G.ARCH_DEFAULTS), sorted(full.arch))

    partial = G.CharSpec.from_json("cipher_partial", {"materials": payload["materials"]})
    check("недостающий архетип добирается до значений по умолчанию",
          partial.arch == G.ARCH_DEFAULTS)

    cv = G.render_frame(dict(G.ANIMS)["idle"][0], partial)
    check("рендерится даже без архетипа", cv.w == G.FW and cv.h == G.FH)

    errors = G.validate_spec({"materials": {}, "arch": {}}, "brand_new")
    check("валидатор требует палитру у нового персонажа",
          any("robe" in e for e in errors), errors)
    check("валидатор требует архетип у нового персонажа",
          any("head" in e for e in errors), errors)

    errors = G.validate_spec({"materials": {"neon": "#123456"}}, "wraith")
    check("частичное переопределение существующего — не ошибка", errors == [], errors)


def test_validation():
    section("Валидация ловит то, обо что спотыкается рендер")
    bad = {
        "materials": {"robe": {"hi": "#111111"}, "armor": {}, "skin": {},
                      "eye": "#FFFFFF", "neon": "#FFFFFF", "neon_soft": "#000000"},
        "arch": {"head": "drone", "torso": "box", "hand": "fist", "foot": "boot",
                 "grip": "plate", "SHOULDER": 99, "WAIST": 20, "ARM": 2, "LEG": 8,
                 "ankle": 5.0},
    }
    errors = G.validate_spec(bad)
    check("неизвестная голова", any("drone" in e for e in errors))
    check("SHOULDER вне границ", any("SHOULDER" in e for e in errors))
    check("ARM вне границ", any("ARM" in e for e in errors))
    check("ankle вне границ", any("ankle" in e for e in errors))
    check("не хватает тонов", any("robe" in e for e in errors))

    for hero in G.HEROES:
        errors = G.validate_spec(G.char_spec(hero).to_json(), hero)
        check("%s: собственный JSON проходит валидацию" % hero, errors == [], errors)


def test_warnings():
    section("Предупреждения: на базовых молчат, на дрянных срабатывают")
    for hero in G.HEROES:
        warnings = G.spec_warnings(G.char_spec(hero))
        check("%s без предупреждений" % hero, warnings == [], warnings)

    flat = G.CharSpec.from_json("flat", {"materials": {
        "robe": {"hi": "#202020", "base": "#404040", "dark": "#606060"},
        "armor": {"hi": "#888888", "base": "#666666", "dark": "#222222"},
        "skin": {"hi": "#CCCCCC", "base": "#AAAAAA", "dark": "#888888"},
        "eye": "#00FFFF", "neon": "#00FFFF", "neon_soft": "#00FFFF"}})
    warnings = G.spec_warnings(flat)
    check("тона не по светлоте", any("светлоте" in w for w in warnings), warnings)
    check("глаз и неон одного цвета", any("одного цвета" in w for w in warnings), warnings)

    narrow = G.CharSpec.from_json("narrow", {"arch": {"SHOULDER": 20, "WAIST": 26,
                                                      "ARM": 12, "LEG": 8}})
    warnings = G.spec_warnings(narrow)
    check("плечи не шире талии", any("талии" in w for w in warnings), warnings)
    check("рука толще трети плеча", any("трети плеча" in w for w in warnings), warnings)

    draft = G.CharSpec.from_json("draft", {"materials": {"neon": "#39FF88"}})
    check("черновик без палитры не роняет проверки",
          isinstance(G.spec_warnings(draft), list))


def test_schema():
    section("Схема — источник истины для сайта")
    schema = G.schema_json()
    for key in ("frame", "anims", "animGroups", "arch", "materials"):
        check("есть раздел '%s'" % key, key in schema)
    check("кадр 80x128", (schema["frame"]["w"], schema["frame"]["h"]) == (80, 128))
    check("в схеме вся библиотека движений",
          len(schema["anims"]) == len(G.anim_names()),
          "%d против %d" % (len(schema["anims"]), len(G.anim_names())))
    check("базовый набор прежний",
          G.ANIM_LIBRARY["default"] == ["idle", "punch", "kick", "super", "stun", "slip"],
          G.ANIM_LIBRARY["default"])
    check("ANIMS собран из этого набора",
          [name for name, _f in G.ANIMS] == G.ANIM_LIBRARY["default"])
    check("animDefault совпадает с ANIMS",
          schema["animDefault"] == [name for name, _f in G.ANIMS],
          schema["animDefault"])
    grouped = [name for g in schema["animGroups"] for name in g["anims"]]
    check("группы покрывают всю библиотеку",
          sorted(grouped) == sorted(G.anim_names()))
    check("у каждой анимации есть кадры и fps",
          all(a["frames"] >= 1 and a["fps"] >= 1 for a in schema["anims"]))
    check("варианты головы совпадают с HEAD_BUILDERS",
          sorted(schema["arch"]["variants"]["head"]) == sorted(G.HEAD_BUILDERS))
    check("все границы на месте",
          set(schema["arch"]["ranges"]) == set(G.ARCH_RANGES))
    check("границы записываются в JSON",
          isinstance(schema["arch"]["ranges"]["ankle"], list))
    check("схема выживает json.dumps",
          json.dumps(schema, ensure_ascii=False) is not None)


def test_anim_library():
    section("Библиотека движений: данные вместо кода")
    names = G.anim_names()
    check("библиотека не пустая", len(names) >= 20, len(names))
    check("все анимации разрешаются", all(G.anim_info(n)["frames"] for n in names))

    # Мусор в JSON должен падать здесь, а не в середине рендера у пользователя.
    known = {"shift", "aim", "step"}
    bad = [(name, index, op) for name in names
           for index, ops in enumerate(G.anim_info(name)["frames"])
           for op in ops if len(set(op) & known) != 1]
    check("все операции известны интерпретатору", not bad, bad[:2])

    poses = G.build_anims(["walk"])
    check("поза содержит все суставы стойки",
          set(poses[0][1][0]) == set(G.RIG_STANCE))

    try:
        G.build_anims(["нет_такой"])
        check("неизвестное движение падает", False)
    except KeyError:
        check("неизвестное движение падает", True)

    check("базовые шесть дают 18 кадров",
          sum(len(f) for _n, f in G.ANIMS) == 18)
    check("лист строится под любой набор движений",
          len(G.build_sheet(G.char_spec("tetrax"),
                            G.build_anims(["idle", "walk"])).px) == 2 * G.FH)


def test_outfits():
    section("Слоты одежды и библиотека комплектов")
    sets = G.outfit_library()
    check("библиотека не пустая", len(sets) >= 8, len(sets))

    for name, entry in sets.items():
        slots = entry.get("outfit") or {}
        unknown = [s for s in slots if s not in G.OUTFIT_REGIONS]
        check("%s: слоты известны движку" % name, not unknown, unknown)
        # Слот ссылается либо на материал комплекта, либо на стандартный
        # материал персонажа (например head -> skin означает «без капюшона»).
        available = set(entry.get("materials", {})) | set(G.MAT_KEYS)
        dangling = sorted({m for m in slots.values() if m not in available})
        check("%s: слоты указывают на существующие материалы" % name,
              not dangling, dangling)
        check("%s: есть палитра" % name, bool(entry.get("materials")))

    base = G.char_spec("tetrax")
    spec = G.apply_outfit(base, "knight")
    check("архетип не тронут", spec.arch == base.arch)
    check("глаз и неон не тронуты",
          G._hex(spec.mats["eye"]) == G._hex(base.mats["eye"])
          and G._hex(spec.mats["neon"]) == G._hex(base.mats["neon"]))
    check("материалы комплекта попали в палитру", "mail" in spec.mats)
    check("слоты на месте", spec.outfit.get("body") == "mail")
    check("слот отдаёт свой материал",
          spec.region_material("body", "robe") is spec.mats["mail"])
    check("незаданный слот берёт запасной",
          base.region_material("body", "robe") is base.mats["robe"])
    check("пустой гардероб не меняет описание", base.outfit in (None, {}))

    errors = G.validate_spec({"materials": {}, "arch": {}, "outfit": {"хвост": "robe"}})
    check("неизвестный слот ловится", any("слот" in e for e in errors), errors)
    errors = G.validate_spec({"materials": {"a": {"hi": "#111111"}},
                              "arch": {}, "outfit": {"body": "нет_такого"}})
    check("слот на несуществующий материал ловится",
          any("материала" in e for e in errors), errors)

    data = spec.to_json()
    back = G.CharSpec.from_json("tetrax", data)
    check("outfit переживает экспорт в JSON", back.outfit == spec.outfit)
    check("палитра переживает экспорт в JSON", back.mats == spec.mats)

    # Одежда обязана влиять на картинку, иначе слоты — мёртвый код.
    pose = dict(G.ANIMS)["idle"][0]
    plain = G.render_frame(pose, base)
    dressed = G.render_frame(pose, spec)
    diff = sum(1 for y in range(plain.h) for x in range(plain.w)
               if plain.px[y][x] != dressed.px[y][x])
    check("комплект меняет картинку", diff > 200, diff)

    try:
        G.apply_outfit(base, "нет_такой")
        check("неизвестный комплект падает", False)
    except KeyError:
        check("неизвестный комплект падает", True)


def test_shapes():
    section("Форма одежды: рукав, штаны, плащ, воротник, пояс, наплечники")
    check("шесть параметров формы", len(G.OUTFIT_SHAPES) == 6, sorted(G.OUTFIT_SHAPES))
    check("у каждого есть значения", all(len(v) >= 2 for v in G.OUTFIT_SHAPES.values()))

    base = G.char_spec("tetrax")
    pose = dict(G.ANIMS)["idle"][0]
    plain = G.render_frame(pose, base)

    for name, values in G.OUTFIT_SHAPES.items():
        for value in values:
            spec = G.CharSpec.from_json("tetrax", {"shapes": {name: value}}, base=base)
            check("%s=%s: форма прочитана" % (name, value), spec.shape(name) == value)
            try:
                G.render_frame(pose, spec)
                drawn = True
            except Exception as exc:                      # noqa: BLE001
                drawn = False
                print("      исключение: %r" % (exc,))
            check("%s=%s: рендер не падает" % (name, value), drawn)

    # Форма обязана менять картинку, иначе это мёртвый параметр.
    for name, value in (("sleeve", "none"), ("pants", "short"), ("cape", "long"),
                        ("collar", "high"), ("belt", "wide"), ("shoulders", "plates")):
        spec = G.CharSpec.from_json("tetrax", {"shapes": {name: value}}, base=base)
        dressed = G.render_frame(pose, spec)
        diff = sum(1 for y in range(plain.h) for x in range(plain.w)
                   if plain.px[y][x] != dressed.px[y][x])
        check("%s=%s меняет картинку" % (name, value), diff > 30, diff)

    errors = G.validate_spec({"materials": {}, "arch": {}, "shapes": {"хвост": "long"}})
    check("неизвестный параметр формы ловится", any("форма" in e for e in errors), errors)
    errors = G.validate_spec({"materials": {}, "arch": {}, "shapes": {"cape": "banana"}})
    check("неизвестное значение формы ловится", any("известно" in e for e in errors), errors)

    spec = G.CharSpec.from_json("tetrax", {"shapes": {"cape": "long"}}, base=base)
    check("форма переживает экспорт в JSON",
          G.CharSpec.from_json("tetrax", spec.to_json()).shapes == spec.shapes)

    for name, entry in G.outfit_library().items():
        shapes = entry.get("shapes") or {}
        bad = [k for k, v in shapes.items()
               if k not in G.OUTFIT_SHAPES or v not in G.OUTFIT_SHAPES[k]]
        check("комплект %s: формы известны движку" % name, not bad, bad)


def test_style():
    section("Стиль: размер головы, полосы света, толщина контура")
    check("три параметра стиля", len(G.STYLE_PARAMS) == 3, sorted(G.STYLE_PARAMS))
    check("значения по умолчанию — прежние",
          G.STYLE_DEFAULTS == {"head": 1.0, "bands": 3, "outline": 1},
          G.STYLE_DEFAULTS)

    base = G.char_spec("tetrax")
    pose = dict(G.ANIMS)["idle"][0]
    plain = G.render_frame(pose, base)
    check("пустой стиль = прежняя картинка",
          same_pixels(G.render_frame(pose, G.CharSpec.from_json("t", {}, base=base)),
                      plain))

    for key, (low, high) in G.STYLE_PARAMS.items():
        for value in (low, high):
            spec = G.CharSpec.from_json("t", {"style": {key: value}}, base=base)
            drawn = G.render_frame(pose, spec)
            diff = sum(1 for y in range(plain.h) for x in range(plain.w)
                       if plain.px[y][x] != drawn.px[y][x])
            check("%s=%s меняет картинку" % (key, value), diff > 20, diff)

    # Голова должна расти вместе с коэффициентом, и метрика роста — честной.
    small = G.CharSpec.from_json("t", {"style": {"head": 1.45}}, base=base)
    heads_small, head_px, _ = G.measure_heads(small)
    heads_base, head_px_base, _ = G.measure_heads(base)
    check("крупная голова выше пикселем", head_px > head_px_base,
          "%d против %d" % (head_px, head_px_base))
    check("и считается честно: меньше голов в фигуре", heads_small < heads_base,
          "%.2f против %.2f" % (heads_small, heads_base))

    errors = G.validate_spec({"materials": {}, "arch": {}, "style": {"хвост": 1}})
    check("неизвестный параметр стиля ловится", any("стиля" in e for e in errors), errors)
    errors = G.validate_spec({"materials": {}, "arch": {}, "style": {"bands": 99}})
    check("значение вне границ ловится", any("нужно" in e for e in errors), errors)
    errors = G.validate_spec({"materials": {}, "arch": {}, "style": {"head": "много"}})
    check("нечисловой параметр ловится", any("число" in e for e in errors), errors)

    spec = G.CharSpec.from_json("t", {"style": {"bands": 5}}, base=base)
    check("стиль переживает экспорт в JSON",
          G.CharSpec.from_json("t", spec.to_json()).style == spec.style)

    for name, entry in G.style_library().items():
        params = entry.get("params") or {}
        unknown = [k for k in params if k not in G.STYLE_PARAMS]
        outside = [k for k, v in params.items() if k in G.STYLE_PARAMS
                   and not (G.STYLE_PARAMS[k][0] <= v <= G.STYLE_PARAMS[k][1])]
        check("образец %s: параметры верные" % name,
              not unknown and not outside, unknown + outside)

    dressed = G.apply_style(base, "chibi")
    check("образец меняет только стиль",
          dressed.arch == base.arch and dressed.mats == base.mats)
    check("образец применён", dressed.style_value("head") == 1.45,
          dressed.style_value("head"))
    try:
        G.apply_style(base, "нет_такого")
        check("неизвестный образец падает", False)
    except KeyError:
        check("неизвестный образец падает", True)


def test_combat():
    section("Боевые данные: тайминги по кадрам и зоны попадания")
    names = G.anim_names()

    # Тайминги: у каждого движения есть длительность каждого кадра.
    for name in names:
        t = G.anim_timing(name)
        entry = G.anim_info(name)
        check("%s: мс на каждый кадр" % name, len(t["ms"]) == t["frames"],
              "%d против %d" % (len(t["ms"]), t["frames"]))
        check("%s: длительности положительные" % name,
              all(ms >= 1 for ms in t["ms"]), t["ms"])
        check("%s: сумма совпадает" % name, t["totalMs"] == sum(t["ms"]), t["totalMs"])

    # Без явного `ms` тайминги считаются от fps — так вела себя библиотека раньше.
    plain = {"frames": [[], [], []], "fps": 10}
    check("без ms длительность берётся из fps",
          G.frame_ms(plain) == [100, 100, 100], G.frame_ms(plain))
    check("короткий список ms продолжается последним значением",
          G.frame_ms({"frames": [[]] * 4, "ms": [50, 70]}) == [50, 70, 70, 70],
          G.frame_ms({"frames": [[]] * 4, "ms": [50, 70]}))
    check("ноль и мусор не ломают тайминг",
          G.frame_ms({"frames": [[]], "ms": [0]}) == [1]
          and all(ms >= 1 for ms in G.frame_ms({"frames": [[]] * 2, "fps": 0})))
    check("пустое движение не падает", G.frame_ms({"frames": []}) == [])

    # Активные окна: удар обязан иметь хитбокс на кадре контакта.
    attacks = [n for n in names if G.anim_info(n).get("hits")]
    check("ударов с хитбоксами: 11", len(attacks) == 11, len(attacks))
    for name in attacks:
        t = G.anim_timing(name)
        active = [i for i, boxes in enumerate(t["hits"]) if boxes]
        check("%s: активные кадры есть" % name, bool(active), active)
        # Пушк всегда раньше отхода: если оба есть, пуск должен идти первым.
        kinds = [b["kind"] for boxes in t["hits"] for b in boxes]
        if "пуск" in kinds and "удар" in kinds:
            check("%s: пуск раньше отхода" % name,
                  kinds.index("пуск") < kinds.index("удар"), kinds)
        for col, boxes in enumerate(t["hits"]):
            for box in boxes:
                # Хитбокс НЕ обрезается по кадру и это не ошибка: кадр — это
                # границы спрайта, а не границы персонажа. Удар в упор должен
                # доставать соседа, стоящего вплотную, иначе он проходил бы
                # сквозь него. Проверяем поэтому не «влезает в кадр», а «есть
                # ли площадь внутри кадра» — вылет за край не делает зону
                # пустой.
                inside = (0 <= box["x"] and 0 <= box["y"]
                          and box["x"] < G.FW and box["y"] < G.FH
                          and box["w"] > 0 and box["h"] > 0)
                check("%s кадр %d: хитбокс попадает в кадр" % (name, col),
                      inside, box)

    # Хитбокс обязан быть на вытянутой конечности, иначе удар ловит вхолостую.
    for name in attacks:
        poses = G.build_anims([name])[0][1]
        for col, boxes in enumerate(G.anim_info(name)["hits"]):
            for box in boxes:
                part = box["part"]
                home = G.RIG_STANCE[part]
                reach = ((poses[col][part][0] - home[0]) ** 2
                         + (poses[col][part][1] - home[1]) ** 2) ** 0.5
                check("%s кадр %d: %s вытянут" % (name, col, part),
                      reach >= 10, "вылет %.1f" % reach)

    # Хёртбоксы: шесть зон на каждом кадре, все в пределах кадра.
    for name in ("idle", "walk", "punch", "round_kick"):
        t = G.anim_timing(name, spec=G.char_spec("tetrax"))
        for col, boxes in enumerate(t["hurt"]):
            check("%s кадр %d: шесть зон" % (name, col), len(boxes) == 6, len(boxes))
            check("%s кадр %d: зоны в кадре" % (name, col),
                  all(0 <= b["x"] and 0 <= b["y"] and b["w"] > 0 and b["h"] > 0
                      and b["x"] + b["w"] <= G.FW and b["y"] + b["h"] <= G.FH
                      for b in boxes),
                  [b for b in boxes if b["x"] + b["w"] > G.FW])

    # Зона головы следует за стилем: у чиби голова крупнее, и зона тоже.
    spec = G.char_spec("tetrax")
    big = G.apply_style(spec, "chibi")
    plain_head = [b for b in G.hurt_boxes(G.RIG_STANCE, spec)
                  if b["kind"] == "голова"][0]
    chibi_head = [b for b in G.hurt_boxes(G.RIG_STANCE, big)
                  if b["kind"] == "голова"][0]
    check("зона головы растёт вместе со стилем",
          chibi_head["w"] > plain_head["w"] and chibi_head["h"] > plain_head["h"],
          "%dx%d против %dx%d" % (chibi_head["w"], chibi_head["h"],
                                  plain_head["w"], plain_head["h"]))

    # Зоны едут за позой: поднятая нога поднимает свою зону. Сравниваются
    # верхние края, а не нижние: зона стоящей ноги упирается в ниж кадра и
    # обрезается, поэтому по низу отличить поднятую ногу от стоящей нельзя.
    poses = G.build_anims(["round_kick"])[0][1]
    rest = [b for b in G.hurt_boxes(poses[0], spec) if b["kind"] == "нога"]
    lifted = [b for b in G.hurt_boxes(poses[1], spec) if b["kind"] == "нога"]
    check("зона поднятой ноги выше, чем у стоящей",
          min(b["y"] for b in lifted) < min(b["y"] for b in rest),
          "%d против %d" % (min(b["y"] for b in lifted), min(b["y"] for b in rest)))
    check("зона поднятой ноги уходит вперёд",
          max(b["x"] + b["w"] for b in lifted) > max(b["x"] + b["w"] for b in rest),
          "%d против %d" % (max(b["x"] + b["w"] for b in lifted),
                            max(b["x"] + b["w"] for b in rest)))


def test_bounds():
    section("Границы архетипа не роняют рендер")
    idle = dict(G.ANIMS)["idle"][0]
    base = G.char_spec("sentinel").to_json()["materials"]
    for name, patch in (("верхние", {"SHOULDER": 40, "WAIST": 34, "ARM": 14,
                                     "LEG": 18, "ankle": 0.95}),
                        ("нижние", {"SHOULDER": 14, "WAIST": 10, "ARM": 3,
                                    "LEG": 4, "ankle": 0.10})):
        spec = G.CharSpec.from_json("bounds", {"materials": base, "arch": patch})
        try:
            cv = G.render_frame(idle, spec)
            ok = cv.w == G.FW and cv.h == G.FH
        except Exception as exc:                      # noqa: BLE001 - тест ловит всё
            ok = False
            print("      исключение: %r" % (exc,))
        check("%s границы рисуются" % name, ok)


def test_registry():
    section("Реестр и мост id -> спецификация")
    check("char_spec отдаёт CharSpec", isinstance(G.char_spec("tetrax"), G.CharSpec))
    try:
        G.char_spec("nobody")
        check("неизвестный персонаж падает", False)
    except KeyError:
        check("неизвестный персонаж падает", True)


def main():
    print("Тесты API-слоя SheetSmith")
    test_roundtrip()
    test_merge()
    test_new_character()
    test_validation()
    test_warnings()
    test_schema()
    test_anim_library()
    test_outfits()
    test_shapes()
    test_style()
    test_combat()
    test_bounds()
    test_registry()
    print()
    if FAILED:
        print("ПРОВАЛ: %d проверок" % len(FAILED))
        for name in FAILED:
            print("  - %s" % name)
        return 1
    print("OK: все проверки прошли.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
