"""HTTP-сервер сайта-генератора. Только стандартная библиотека.

Запуск:
    py server\\app.py                      :: http://localhost:8000
    py server\\app.py --host 0.0.0.0       :: доступ из локальной сети
    py server\\app.py --jobs 7             :: рендер в 7 процессах (0 = в этом же)

Почему не FastAPI: проект держится на «никаких зависимостей», и это не
претензия к фреймворку, а требование к себе — репозиторий должен запускаться
командой `py` без `pip install`. Валидация описания и схема живут в движке
(`char_generator.validate_spec` и `schema_json`), то есть ровно то, ради чего
обычно и берут веб-фреймворк, здесь уже есть. Остаётся раздать JSON и PNG.

Маршруты:
    GET  /                     -> интерфейс (web/dist), если собран; иначе подсказка
    GET  /api/health           -> живо ли сервер, что умеет
    GET  /api/schema           -> варианты, границы, сетка анимаций
    GET  /api/presets          -> шестеро базовых персонажей
    POST /api/preview          -> PNG одного кадра
    POST /api/measure          -> рост в головах + предупреждения
    POST /api/jobs             -> долгий экспорт (лист/ростер), ответ jobId
    GET  /api/jobs/<id>        -> статус задачи
    GET  /api/jobs/<id>/file   -> готовый PNG
    POST /api/save             -> записать PNG в out/ и героя в characters.local.json
"""

import argparse
import json
import os
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import char_generator as G                       # noqa: E402
from render import (RenderService, SpecError,    # noqa: E402
                    anim_frames, build_spec, chosen_anims, presets)

WEB_ROOT = os.path.join(ROOT, "web")
WEB_DIST = os.path.join(WEB_ROOT, "dist")


def web_root():
    """Откуда отдавать интерфейс: сборка, если она есть, иначе исходники.

    Интерфейс написан без сборки (обычные ES-модули), так что `web/` — уже
    готовый сайт. Но если появится `web/dist` — после перехода на сборку, —
    победит она: собранное всегда ближе к пользователю.
    """
    if os.path.isfile(os.path.join(WEB_DIST, "index.html")):
        return WEB_DIST
    return WEB_ROOT

# Какие расширения сервер готов отдать из web/dist.
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
    ".ico": "image/x-icon",
    ".map": "application/json; charset=utf-8",
}

# Долгие задачи живут в памяти: сервер локальный, переживать перезапуск ради
# незаконченного экспорта незачем.
JOBS = {}
JOBS_LOCK = threading.Lock()


def start_job(kind, fn, total=1):
    """Запускает рендер в фоне и отдаёт id.

    `fn` получает колбэк прогресса: клиент спрашивает `/api/jobs/<id>` и видит
    «посчитано 7 из 18 кадров», а не крутится на голом «работаю».
    """
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"id": job_id, "kind": kind, "status": "running",
                        "done": 0, "total": total, "error": None}

    def progress(done, count):
        with JOBS_LOCK:
            JOBS[job_id]["done"] = done
            JOBS[job_id]["total"] = count

    def run():
        try:
            result = fn(progress)
            with JOBS_LOCK:
                # done = total, а не «одна задача»: у листа 18 кадров, и клиент
                # ждёт «18 из 18», а не загадочную единицу.
                JOBS[job_id].update(status="done", done=JOBS[job_id]["total"],
                                    result=result)
        except Exception as exc:                      # noqa: BLE001
            with JOBS_LOCK:
                JOBS[job_id].update(status="error", error="%s: %s"
                                    % (type(exc).__name__, exc))

    threading.Thread(target=run, daemon=True).start()
    return job_id


def get_job(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return None
    # Байты наружу не отдаём: статус должен оставаться лёгким JSON.
    return {k: v for k, v in job.items() if k != "result"}


class Handler(BaseHTTPRequestHandler):
    server_version = "SheetSmith"
    protocol_version = "HTTP/1.1"

    # --- инфраструктура ответов ---

    def _send(self, status, body, content_type="application/json; charset=utf-8",
              headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status=200, headers=None):
        self._send(status, json.dumps(data, ensure_ascii=False),
                   headers=headers)

    def _png(self, png, headers=None):
        self._send(200, png, "image/png", headers)

    def _error(self, status, message):
        self._json({"error": message}, status=status)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SpecError(["тело запроса не разобрано: %s" % exc])

    def log_message(self, fmt, *args):
        # Лог в stdout, но без даты: сервер запускают из окна, где важнее текст.
        sys.stdout.write("  %s\n" % (fmt % args))

    def handle_one_request(self):
        """Проглатывает разрывы соединений вместо трассировки.

        Браузер закрывает соединение сам — например, когда отменяет уже ненужный
        запрос превью. Каждый такой разрыв печатал бы пятнадцать строк трейсбека
        и засорял окно, из которого сервер запущен.
        """
        try:
            BaseHTTPRequestHandler.handle_one_request(self)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    # --- маршруты ---

    def do_GET(self):                                  # noqa: N802 (имя из BaseHTTPRequestHandler)
        path = urlparse(self.path).path
        service = self.server.service

        if path == "/api/health":
            return self._json({
                "ok": True,
                "python": sys.version.split()[0],
                "frame": {"w": G.FW, "h": G.FH},
                "jobs": service.jobs,
                "cache": service.cache.stats(),
            })
        if path == "/api/schema":
            return self._json(G.schema_json())
        if path == "/api/presets":
            return self._json({"characters": presets()})
        if path.startswith("/api/jobs/"):
            tail = path[len("/api/jobs/"):]
            if tail.endswith("/file"):
                return self._job_file(tail[:-len("/file")])
            return self._job_status(tail)
        return self._static(path)

    def do_POST(self):                                 # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._body()
        except SpecError as exc:
            return self._error(400, "; ".join(exc.args[0]))

        try:
            if path == "/api/preview":
                return self._preview(payload)
            if path == "/api/measure":
                return self._measure(payload)
            if path == "/api/jobs":
                return self._create_job(payload)
            if path == "/api/save":
                return self._save(payload)
        except SpecError as exc:
            return self._error(400, "; ".join(exc.args[0]))
        except KeyError as exc:
            return self._error(400, "нет поля %s" % exc)
        except Exception as exc:                          # noqa: BLE001
            # Клиенту — 500 с текстом, но соединение не рвём: оборванный запрос
            # в браузере выглядит как «сервер мёртв» и совращает диагностику.
            traceback.print_exc()
            return self._error(500, "%s: %s" % (type(exc).__name__, exc))
        return self._error(404, "неизвестный путь: %s" % path)

    # --- обработчики ---

    def _preview(self, payload):
        spec = build_spec(payload)
        anim = payload.get("anim", "idle")
        frame = int(payload.get("frame", 0))
        scale = int(payload.get("scale", 1))
        if scale < 1 or scale > 6:
            raise SpecError(["масштаб %d: можно 1..6" % scale])
        png, meta = self.server.service.frame(spec, anim, frame, scale)
        self._png(png, {
            "X-Render-Ms": meta["render_ms"],
            "X-Cache": "hit" if meta["cached"] else "miss",
            "X-Frame-Width": meta["width"],
            "X-Frame-Height": meta["height"],
        })

    def _measure(self, payload):
        self._json(self.server.service.measure(build_spec(payload)))

    def _create_job(self, payload):
        kind = payload.get("kind", "sheet")
        scale = int(payload.get("scale", 1))
        anims = chosen_anims(payload.get("anims"))
        frames_total = sum(anim_frames(name) for name in anims)
        if kind == "sheet":
            spec = build_spec(payload.get("character") or payload)
            # В задачу уходит только результат: метаданные рендера нужны в
            # логах сервера, а статус задачи должен оставаться лёгким JSON.
            job_id = start_job(
                kind,
                lambda done: self.server.service.sheet(spec, scale, done, anims)[0],
                frames_total)
        elif kind == "roster":
            entries = payload.get("characters") or []
            if not entries:
                raise SpecError(["ростер пуст: нужен хотя бы один персонаж"])
            if len(entries) > 12:
                raise SpecError(["ростер длиннее 12 персонажей не рисуем"])
            specs = [build_spec(item) for item in entries]
            job_id = start_job(
                kind,
                lambda done: self.server.service.roster(specs, scale, done, anims)[0],
                frames_total * len(specs))
        elif kind == "pack":
            spec = build_spec(payload.get("character") or payload)
            job_id = start_job(
                kind,
                lambda done: self.server.service.pack(spec, 1, done, anims)[0],
                frames_total)
        else:
            raise SpecError(["вид задачи '%s' неизвестен: sheet, roster или pack"
                             % kind])
        self._json({"jobId": job_id, "kind": kind, "anims": anims,
                    "frames": frames_total}, status=202)

    def _job_status(self, job_id):
        job = get_job(job_id)
        if not job:
            return self._error(404, "нет такой задачи: %s" % job_id)
        self._json(job)

    def _job_file(self, job_id):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            result = job.get("result") if job else None
            kind = job.get("kind") if job else ""
        if not result:
            return self._error(404, "задача не готова или не найдена")
        types = {"sheet": "image/png", "roster": "image/png", "pack": "application/zip"}
        names = {"sheet": "%s.png", "roster": "roster.png", "pack": "%s-sprites.zip"}
        name = names.get(kind, "result.bin")
        self._send(200, result, types.get(kind, "application/octet-stream"),
                   {"Content-Disposition":
                    'attachment; filename="%s"' % (name % job_id if "%s" in name else name)})

    def _save(self, payload):
        """Кладёт ассет в out/ и героя в characters.local.json — мост в CLI."""
        spec = build_spec(payload.get("character") or payload)
        out_dir = os.path.join(ROOT, "out")
        os.makedirs(out_dir, exist_ok=True)
        png = self.server.service.sheet(spec, 1)[0]
        png_path = os.path.join(out_dir, "%s.png" % spec.id)
        with open(png_path, "wb") as fh:
            fh.write(png)

        library = os.path.join(ROOT, "characters.local.json")
        data = {}
        if os.path.exists(library):
            try:
                with open(library, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except ValueError:
                data = {}
        data.setdefault("characters", {})[spec.id] = spec.to_json()
        with open(library, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

        self._json({
            "saved": True,
            "png": os.path.relpath(png_path, ROOT).replace("\\", "/"),
            "library": os.path.relpath(library, ROOT).replace("\\", "/"),
            "cli": "py char_generator.py --characters characters.local.json --list",
        })

    # --- статика ---

    def _static(self, path):
        base = web_root()
        target = os.path.normpath(os.path.join(base, path.lstrip("/")))
        if not target.startswith(base):
            return self._error(403, "вне каталога сайта")
        if path in ("/", "") and not os.path.isfile(os.path.join(target, "index.html")):
            return self._send(200, PLACEHOLDER, "text/html; charset=utf-8")
        if not os.path.isfile(target):
            # Интерфейс на одной странице: неизвестный путь без расширения
            # ведёт на index.html, а не в 404.
            fallback = os.path.join(base, "index.html")
            if os.path.isfile(fallback):
                target = fallback
            else:
                return self._error(404, "не найдено: %s" % path)
        ext = os.path.splitext(target)[1].lower()
        with open(target, "rb") as fh:
            body = fh.read()
        self._send(200, body, STATIC_TYPES.get(ext, "application/octet-stream"))


PLACEHOLDER = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>SheetSmith</title>
<style>
 body{background:#14161d;color:#d8dee9;font:16px/1.6 system-ui,sans-serif;
      display:grid;place-items:center;min-height:100vh;margin:0}
 code{background:#1e212b;padding:2px 6px;border-radius:4px;color:#7ee787}
 .card{max-width:34rem;padding:2rem}
</style></head>
<body><div class="card">
<h1>SheetSmith</h1>
<p>Сервер работает, но интерфейс ещё не собран.</p>
<p>Проверить движок можно прямо отсюда:</p>
<p><code>GET /api/schema</code> · <code>GET /api/presets</code> · <code>GET /api/health</code></p>
<p>Собрать интерфейс: <code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code></p>
</div></body></html>
"""


def serve(host="127.0.0.1", port=8000, jobs=0):
    service = RenderService(jobs=jobs)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.service = service
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    if host == "0.0.0.0":
        shown = "localhost (и по адресу этой машины в сети)"
    print("SheetSmith на http://%s:%d" % (shown, port))
    print("рендер: %s" % ("%d процессов" % jobs if jobs else "в этом же процессе"))
    print("остановить: Ctrl+C\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлено")
    finally:
        service.shutdown()
        httpd.server_close()


def main():
    ap = argparse.ArgumentParser(description="Сервер сайта-генератора SheetSmith.")
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 — открыть доступ из локальной сети")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--jobs", type=int, default=0,
                    help="процессов для рендера (0 = в этом же, по одному кадру)")
    args = ap.parse_args()
    serve(args.host, args.port, args.jobs)


if __name__ == "__main__":
    main()
