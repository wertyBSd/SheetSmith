/* Обращения к серверу.
 *
 * Ключевое здесь — устаревшие ответы. Правя ползунок, браузер шлёт запрос на
 * каждый шаг, а рендер занимает секунды: ответ на ранний запрос может прийти
 * позже ответа на поздний и показать старую картинку. Поэтому у каждого
 * запроса есть номер, а предыдущий отменяется через AbortController.
 */

let lastToken = 0;
let current = null;

async function getJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(await describe(response));
  return response.json();
}

async function postJson(path, body, signal) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw new Error(await describe(response));
  return response.json();
}

async function describe(response) {
  try {
    const data = await response.json();
    return data.error || `${response.status} ${response.statusText}`;
  } catch {
    return `${response.status} ${response.statusText}`;
  }
}

export function schema() {
  return getJson("/api/schema");
}

export function presets() {
  return getJson("/api/presets").then((data) => data.characters);
}

export function measure(character, signal) {
  return postJson("/api/measure", character, signal);
}

export async function startJob(body) {
  const response = await fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await describe(response));
  return response.json();
}

export function jobStatus(id) {
  return getJson(`/api/jobs/${id}`);
}

export function jobFileUrl(id) {
  return `/api/jobs/${id}/file`;
}

export function save(character) {
  return postJson("/api/save", { character });
}

/**
 * Применяет комплект одежды на сервере и возвращает готовое описание.
 *
 * Так сделано потому, что комплект — это связка «палитра + слоты», и собирать
 * её в браузере пришлось бы дублировать правила движка. Сервер отдаёт ровно то
 * описание, которое потом уедет в движок при рендере.
 */
export function applyOutfit(character, name) {
  return postJson("/api/outfit", { character, outfit: name });
}

/**
 * Боевые данные: тайминги кадров и зоны попадания по всем движениям набора.
 *
 * Считается на сервере из того же движка, что и картинка, поэтому зона и пиксель
 * не могут разойтись. Отдельный запрос, а не поле в превью: превью
 * перерисовывается на каждый чих слайдера, а боевые данные меняются только
 * вместе с описанием персонажа.
 */
export async function combat(character, anims) {
  const response = await fetch("/api/combat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...character, anims }),
  });
  if (!response.ok) throw new Error(await describe(response));
  return response.json();
}

/**
 * Кадр превью -> { url, meta }. Старые запросы отменяются, ответы с устаревшим
 * номером выбрасываются. onState("loading" | "ready" | "error") — чтобы
 * интерфейс мог показать прошлый кадр, пока считается новый.
 */
export async function previewFrame(character, options, onState) {
  const token = ++lastToken;
  if (current) current.abort();
  const controller = new AbortController();
  current = controller;

  onState?.("loading");
  try {
    const response = await fetch("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...character, ...options }),
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(await describe(response));
    if (token !== lastToken) return null;   // ответ запоздал, его уже не ждут

    const blob = await response.blob();
    if (token !== lastToken) return null;
    return {
      url: URL.createObjectURL(blob),
      meta: {
        cached: response.headers.get("X-Cache") === "hit",
        renderMs: Number(response.headers.get("X-Render-Ms") || 0),
      },
    };
  } catch (error) {
    if (error.name === "AbortError") return null;
    onState?.("error");
    throw error;
  } finally {
    if (current === controller) current = null;
  }
}
