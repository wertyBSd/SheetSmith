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
    status = "ок  " if condition else "ПРОВАЛ"
    print("  %s %s%s" % (status, name, ("  — " + detail) if detail and not condition else ""))
    if not condition:
        FAILED.append(name)


def section(title):
    print("\n%s" % title)


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
    for key in ("frame", "anims", "arch", "materials"):
        check("есть раздел '%s'" % key, key in schema)
    check("кадр 80x128", (schema["frame"]["w"], schema["frame"]["h"]) == (80, 128))
    check("анимаций шесть", len(schema["anims"]) == 6)
    check("сумма кадров 18", sum(a["frames"] for a in schema["anims"]) == 18)
    check("варианты головы совпадают с HEAD_BUILDERS",
          sorted(schema["arch"]["variants"]["head"]) == sorted(G.HEAD_BUILDERS))
    check("все границы на месте",
          set(schema["arch"]["ranges"]) == set(G.ARCH_RANGES))
    check("границы записываются в JSON",
          isinstance(schema["arch"]["ranges"]["ankle"], list))
    check("схема выживает json.dumps",
          json.dumps(schema, ensure_ascii=False) is not None)


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
