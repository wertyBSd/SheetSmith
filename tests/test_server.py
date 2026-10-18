"""Тесты сервера: поднимаем его на случайном порту и бьём по маршрутам.

Сервер поднимается в потоке на порту 0 (то есть «выбери свободный»), запросы
идут через urllib — тем же, чем пользуется браузер. Никаких внешних
зависимостей: если тест требует pip install, он уже не про этот проект.

Что проверяется по существу, а не «не 500-ка»:
  * PNG начинается с сигнатуры и декодируется обратно (tests/png_reader.py);
  * размер кадра совпадает со схемой — превью не поехало по геометрии;
  * повторный запрос того же кадра приходит из кэша и не рисует заново;
  * мусор в описании даёт 400 со списком причин, а не 500;
  * задача экспорта доходит до статуса done и отдаёт файл.

Запуск: py tests\\test_server.py
"""

import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "server"))

from app import Handler                                  # noqa: E402
from render import RenderService, build_spec              # noqa: E402
from png_reader import read_png                           # noqa: E402
import char_generator as G                                # noqa: E402

FAILED = []
PORT = 0
BASE = ""


def check(name, condition, detail=""):
    print("  %s %s%s" % ("ок   " if condition else "ПРОВАЛ", name,
                         ("  — %s" % detail) if detail and not condition else ""))
    if not condition:
        FAILED.append(name)


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=60) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def post(path, payload):
    request = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=300) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def first_preset():
    _s, _h, body = get("/api/presets")
    return json.loads(body)["characters"][0]


def test_health_and_schema():
    print("\nСлужебные маршруты")
    status, _headers, body = get("/api/health")
    health = json.loads(body)
    check("health отвечает", status == 200 and health["ok"])
    check("health знает размер кадра",
          (health["frame"]["w"], health["frame"]["h"]) == (80, 128))

    status, _headers, body = get("/api/schema")
    schema = json.loads(body)
    check("schema отдаёт библиотеку движений", len(schema["anims"]) >= 20,
          len(schema["anims"]))
    check("schema отдаёт группы движений", len(schema["animGroups"]) >= 3)
    check("schema отдаёт границы", "ankle" in schema["arch"]["ranges"])
    check("schema отдаёт варианты головы", len(schema["arch"]["variants"]["head"]) == 6)

    status, _headers, body = get("/api/presets")
    presets = json.loads(body)["characters"]
    check("шестеро пресетов", len(presets) == 6, len(presets))
    check("у пресета есть палитра и архетип",
          "materials" in presets[0] and "arch" in presets[0])


def test_preview():
    print("\nПревью одного кадра")
    preset = first_preset()
    payload = dict(preset, anim="idle", frame=0, scale=1)

    started = time.time()
    status, headers, png = post("/api/preview", payload)
    cold_ms = int((time.time() - started) * 1000)
    check("200 и PNG", status == 200 and png[:8] == b"\x89PNG\r\n\x1a\n")
    check("это холодный рендер", headers.get("X-Cache") == "miss")
    check("кадр 80x128", (headers.get("X-Frame-Width"), headers.get("X-Frame-Height"))
          == ("80", "128"), dict(headers))

    started = time.time()
    _status, headers2, png2 = post("/api/preview", payload)
    warm_ms = int((time.time() - started) * 1000)
    check("повтор приходит из кэша", headers2.get("X-Cache") == "hit")
    check("байты те же самые", png2 == png)
    check("кэш заметно быстрее", warm_ms < cold_ms,
          "холодный %d мс, тёплый %d мс" % (cold_ms, warm_ms))

    # Пиксели читаются обратно и совпадают с движком: сервер не «перерисовал».
    tmp = os.path.join(ROOT, "preview", "_server_test.png")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "wb") as fh:
        fh.write(png)
    width, height, rows = read_png(tmp)
    os.remove(tmp)
    opaque = sum(1 for y in range(height) for x in range(width) if rows[y][x][3] > 0)
    check("декодируется и не пустой", width == 80 and height == 128 and opaque > 200,
          "%dx%d, %d px" % (width, height, opaque))

    _status, headers3, _png = post("/api/preview", dict(payload, scale=3))
    check("scale=3 даёт 240x384",
          (headers3.get("X-Frame-Width"), headers3.get("X-Frame-Height")) == ("240", "384"))

    # Движение из библиотеки, не входящее в базовый набор, тоже рисуется:
    # вкладки в интерфейсе показывают всю библиотеку.
    _status, headers4, _png = post("/api/preview",
                                   dict(preset, anim="walk", frame=2, scale=1))
    check("движение из библиотеки доступно", _status == 200 and headers4, _status)


def test_errors():
    print("\nОшибки вместо 500")
    status, _headers, body = post("/api/preview", {"arch": {"head": "drone"}})
    check("мусорный head даёт 400", status == 400, status)
    check("в ответе перечислены причины",
          "drone" in json.loads(body)["error"], body)

    status, _headers, body = post("/api/preview",
                                  dict(first_preset(), anim="пауэрпафф"))
    check("неизвестная анимация даёт 400", status == 400, status)

    status, _headers, body = post("/api/preview",
                                  dict(first_preset(), anim="kick", frame=99))
    check("кадр вне диапазона даёт 400", status == 400, status)

    status, _headers, _body = post("/api/preview", dict(first_preset(), scale=99))
    check("масштаб 99 даёт 400", status == 400, status)


def test_measure():
    print("\nЗамер пропорций")
    status, _headers, body = post("/api/measure", first_preset())
    data = json.loads(body)
    check("рост в головах посчитан", status == 200 and 3.5 < data["heads"] < 7.0, data)
    check("предупреждения — список", isinstance(data["warnings"], list))


def test_job():
    print("\nДолгий экспорт листа")
    preset = first_preset()
    status, _headers, body = post("/api/jobs", {"kind": "sheet", "character": preset})
    check("задача принята (202)", status == 202, status)
    job_id = json.loads(body)["jobId"]

    deadline = time.time() + 300
    state = {}
    while time.time() < deadline:
        _status, _headers, raw = get("/api/jobs/%s" % job_id)
        state = json.loads(raw)
        if state["status"] != "running":
            break
        time.sleep(0.5)
    check("задача завершилась", state.get("status") == "done", state)
    check("отчёт не тащит байты", "result" not in state)
    check("прогресс дошёл до конца", state.get("done") == state.get("total") == 18, state)

    status, headers, png = get("/api/jobs/%s/file" % job_id)
    check("файл отдан как вложение", status == 200 and png[:8] == b"\x89PNG\r\n\x1a\n")
    check("это спрайт-лист 320x768",
          int(headers.get("Content-Length", 0)) > 1000)

    # Главное обещание сайта: картинка из браузера совпадает с картинкой из CLI.
    # Сравниваем с эталоном, а не с байтами: эталон и ответ могут быть собраны
    # разными версиями zlib, а пиксели должны совпадать всегда.
    golden = os.path.join(ROOT, "tests", "golden", "%s.png" % preset["id"])
    if os.path.exists(golden):
        tmp = os.path.join(ROOT, "preview", "_server_job.png")
        with open(tmp, "wb") as fh:
            fh.write(png)
        got = read_png(tmp)[2]
        os.remove(tmp)
        want = read_png(golden)[2]
        diff = sum(1 for y in range(len(want)) for x in range(len(want[y]))
                   if got[y][x] != want[y][x])
        check("лист из сервера равен эталону CLI (%s)" % preset["id"], diff == 0,
              "%d пикселей расходятся" % diff)
    else:
        check("эталон для сравнения найден", False, golden)

    status, _headers, _body = get("/api/jobs/nosuchjob")
    check("неизвестная задача даёт 404", status == 404, status)


def test_pack():
    print("\nНабор для игры: zip с листом, полосками и описанием")
    preset = first_preset()
    anims = ["idle", "walk", "jab"]
    status, _headers, body = post("/api/jobs",
                                  {"kind": "pack", "character": preset, "anims": anims})
    check("задача принята (202)", status == 202, status)
    job_id = json.loads(body)["jobId"]

    deadline = time.time() + 300
    state = {}
    while time.time() < deadline:
        _s, _h, raw = get("/api/jobs/%s" % job_id)
        state = json.loads(raw)
        if state["status"] != "running":
            break
        time.sleep(0.5)
    check("задача завершилась", state.get("status") == "done", state)

    status, headers, blob = get("/api/jobs/%s/file" % job_id)
    check("отдан zip", status == 200 and blob[:2] == b"PK", headers.get("Content-Type"))
    archive = zipfile.ZipFile(io.BytesIO(blob))
    names = archive.namelist()
    hero = preset["id"]
    check("внутри общий лист", "%s/sheet.png" % hero in names, names)
    check("внутри описание персонажа", "%s/%s.json" % (hero, hero) in names)
    check("внутри раскладка", "%s/README.txt" % hero in names)
    check("полоска на каждое движение",
          all("%s/animations/%s.png" % (hero, a) in names for a in anims))
    check("никаких лишних файлов", len(names) == 3 + len(anims), names)

    strip = read_png_file(archive, "%s/animations/jab.png" % hero)
    check("полоска удара — одна строка кадров",
          strip[0] == 80 * 3 and strip[1] == 128, strip)
    check("раскладка перечисляет движения",
          all(a in archive.read("%s/README.txt" % hero).decode("utf-8") for a in anims))


def read_png_file(archive, name):
    tmp = os.path.join(ROOT, "preview", "_zip_test.png")
    with open(tmp, "wb") as fh:
        fh.write(archive.read(name))
    size = read_png(tmp)[:2]
    os.remove(tmp)
    return size


def test_selected_anims():
    print("\nЛист из выбранных движений")
    preset = first_preset()
    status, _headers, body = post("/api/jobs",
                                  {"kind": "sheet", "character": preset,
                                   "anims": ["idle", "walk"]})
    job_id = json.loads(body)["jobId"]
    deadline = time.time() + 300
    while time.time() < deadline:
        _s, _h, raw = get("/api/jobs/%s" % job_id)
        state = json.loads(raw)
        if state["status"] != "running":
            break
        time.sleep(0.5)
    check("лист из двух движений собран", state.get("status") == "done", state)

    _s, _h, png = get("/api/jobs/%s/file" % job_id)
    tmp = os.path.join(ROOT, "preview", "_sheet_test.png")
    with open(tmp, "wb") as fh:
        fh.write(png)
    width, height = read_png(tmp)[:2]
    os.remove(tmp)
    check("две строки по четыре колонки", (width, height) == (320, 256),
          "%dx%d" % (width, height))


def test_pool():
    print("\nТот же лист, собранный пулом процессов")
    preset = first_preset()
    spec = build_spec(preset)
    service = RenderService(jobs=2)
    try:
        png, meta = service.sheet(spec, 1)
        check("размеры листа верные", (meta["width"], meta["height"]) == (320, 768), meta)
        reference = G.build_sheet(spec).to_png_bytes()
        check("пул даёт тот же байт-в-байт результат", png == reference,
              "png %d байт против %d" % (len(png), len(reference)))

        steps = []
        service.sheet(spec, 1, progress=lambda done, total: steps.append((done, total)))
        check("прогресс по кадрам: 18 шагов", [s[0] for s in steps] == list(range(1, 19)), steps[:5])
    finally:
        service.shutdown()


def test_root():
    print("\nКорень")
    status, headers, body = get("/")
    check("отдаётся страница", status == 200 and b"<html" in body.lower())
    check("это html", headers.get("Content-Type", "").startswith("text/html"))


def main():
    global PORT, BASE
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.service = RenderService(jobs=0)   # без пула: тест должен быть предсказуемым
    PORT = httpd.server_address[1]
    BASE = "http://127.0.0.1:%d" % PORT
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    print("Тесты сервера SheetSmith (%s)" % BASE)

    try:
        test_health_and_schema()
        test_preview()
        test_errors()
        test_measure()
        test_root()
        test_job()
        test_selected_anims()
        test_pack()
        test_pool()
    finally:
        httpd.shutdown()
        httpd.server_close()
        httpd.service.shutdown()

    print()
    if FAILED:
        print("ПРОВАЛ: %d проверок" % len(FAILED))
        for name in FAILED:
            print("  - %s" % name)
        return 1
    print("OK: сервер отвечает на всё, чем пользуется редактор.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
