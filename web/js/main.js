/* Точка входа: связывает состояние, сервер и экран.
 *
 * Порядок загрузки важен: без схемы нельзя построить вкладки анимаций (там
 * число кадров у каждой своё), а без персонажа нечего рисовать. Поэтому сначала
 * схема и пресеты, потом первый кадр.
 */

import * as api from "./api.js";
import { store, cloneCharacter, animFrames } from "./store.js";
import { renderEditor } from "./editor.js";
import { renderExport } from "./export.js";

const el = {
  status: document.getElementById("status"),
  panel: document.getElementById("panel"),
  presets: document.getElementById("presets"),
  controls: document.getElementById("controls"),
  tabs: document.getElementById("tabs"),
  previewBox: document.getElementById("preview"),
  previewImg: document.getElementById("preview-img"),
  frameInput: document.getElementById("frame-input"),
  scaleInput: document.getElementById("scale-input"),
  play: document.getElementById("play"),
  hint: document.getElementById("hint"),
  metrics: document.getElementById("metrics"),
};

let previewUrl = null;
let timer = null;
let player = null;

function setStatus(text, kind = "") {
  el.status.textContent = text;
  el.status.className = `status ${kind}`;
}

/** Единственное место, где превью уезжает на сервер. */
async function requestPreview() {
  const state = store.state;
  if (!state.character) return;

  // Поле масштаба приводим к тому, что реально используется: подгонка под окно
  // меняет масштаб молча, и без этого в поле осталось бы старое число.
  el.scaleInput.value = String(state.scale);

  const result = await api.previewFrame(
    state.character,
    { anim: state.anim, frame: state.frame, scale: state.scale },
    (status) => {
      el.previewBox.dataset.state = status;
      if (status === "loading") setStatus("считаем…", "busy");
    },
  );
  if (!result) return;   // ответ устарел, придёт следующий

  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = result.url;
  el.previewImg.src = previewUrl;
  el.previewBox.dataset.state = "ready";

  const ms = result.meta.cached ? "из кэша" : `рендер ${result.meta.renderMs} мс`;
  setStatus(ms, result.meta.cached ? "" : "busy");
  store.set({ lastRender: result.meta });
}

/** Правка ползунка шлёт десятки запросов в секунду — жмём их в один. */
function refreshPreview(delay = 200) {
  clearTimeout(timer);
  timer = setTimeout(requestPreview, delay);
}

// --- панель: пресеты и контролы --------------------------------------------

function renderPresets() {
  const group = document.createElement("div");
  group.className = "group";
  group.innerHTML = "<h2>Базовые персонажи</h2>";

  const grid = document.createElement("div");
  grid.className = "preset-grid";
  for (const preset of store.state.presets) {
    const button = document.createElement("button");
    button.className = "preset";
    button.type = "button";
    button.title = preset.id;

    // Полоска из трёх тонов рясы: узнать бойца можно по цвету, не открывая.
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    for (const tone of ["hi", "base", "dark"]) {
      const part = document.createElement("i");
      part.style.background = preset.materials.robe[tone];
      swatch.append(part);
    }
    const neon = document.createElement("i");
    neon.style.background = preset.neon;

    const label = document.createElement("span");
    label.textContent = preset.id;

    button.append(swatch, label);
    button.addEventListener("click", () => selectCharacter(cloneCharacter(preset)));
    grid.append(button);
  }
  group.append(grid);
  // replaceChildren, а не append: панель пересобирается при каждой смене
  // персонажа, и append размножил бы кнопки.
  el.presets.replaceChildren(group);
}

/** Общая точка для кнопок пресетов, undo и загрузки из библиотеки. */
function loadCharacter(character) {
  store.set({ character: cloneCharacter(character), metrics: null, outfitSet: null });
  markSelectedPreset();
  renderPresets();
  refreshControls();
  refreshPreview(0);
  scheduleMeasure();
}

/** Галочки движений: пересчитываем лист, но предпросмотр не трогаем. */
function animChange(anims, groupNode) {
  store.set({ anims });
  const chosen = new Set(anims);
  const frames = store.state.schema.anims.reduce(
    (sum, a) => sum + (chosen.has(a.name) ? a.frames : 0), 0);
  const hint = groupNode.querySelector(".hint");
  if (hint) hint.textContent = `выбрано ${chosen.size} движений, ${frames} кадров`;
}

/**
 * Комплект одежды целиком. Сервер не хранит гардероб — комплект применяется
 * на его стороне к текущему описанию, поэтому ответ приходит уже готовым.
 */
async function applyOutfitSet(name) {
  if (!name) {
    store.set({ outfitSet: null });
    refreshControls();
    return;
  }
  try {
    const character = await api.applyOutfit(store.state.character, name);
    store.set({ character, outfitSet: name, metrics: null });
    refreshControls();
    refreshPreview(0);
    scheduleMeasure();
  } catch (error) {
    setStatus(`комплект не применился: ${error.message}`, "error");
  }
}

/** Точечная правка слота: переставить один материал на другую часть тела. */
function applyOutfitSlot(slot, material) {
  const character = cloneCharacter(store.state.character);
  const outfit = { ...(character.outfit || {}) };
  if (material) outfit[slot] = material; else delete outfit[slot];
  character.outfit = Object.keys(outfit).length ? outfit : null;
  store.set({ character, outfitSet: null, metrics: null });
  refreshControls();
  refreshPreview(0);
  scheduleMeasure();
}

/** Форма одежды: длина рукава, плащ, воротник — вид, а не цвет. */
function applyShape(key, value) {
  const character = cloneCharacter(store.state.character);
  character.shapes = { ...(character.shapes || {}), [key]: value };
  store.set({ character, outfitSet: null, metrics: null });
  refreshControls();
  refreshPreview(0);
}

function refreshControls() {
  renderEditor(el.controls, {
    edit,
    animChange,
    outfitSet: applyOutfitSet,
    outfitSlot: applyOutfitSlot,
    shape: applyShape,
  });
  renderExport(el.controls, () => store.state);
}

function markSelectedPreset() {
  const current = store.state.character?.id;
  for (const button of el.presets.querySelectorAll(".preset")) {
    button.setAttribute("aria-pressed", String(button.title === current));
  }
}

/**
 * Правка из редактора. `quick` отличает движение ползунка (много запросов,
 * нужен debounce) от отпускания (показать сразу).
 */
function edit(apply, quick = true) {
  apply();
  refreshPreview(quick ? 220 : 0);
  scheduleMeasure();
}

let measureTimer = null;

/** Рост в головах считается отдельно и реже: он меняется медленно вида. */
function scheduleMeasure() {
  clearTimeout(measureTimer);
  measureTimer = setTimeout(async () => {
    try {
      const metrics = await api.measure(cloneCharacter(store.state.character));
      store.set({ metrics });
    } catch {
      /* замер не критичен: картинка уже на экране */
    }
  }, 700);
}

// --- вкладки анимаций ------------------------------------------------------

function renderTabs() {
  el.tabs.replaceChildren();
  // Вкладки — вся библиотека: посмотреть можно любое движение, даже не
  // включённое в выгрузку. Что попадёт в набор, решают галочки в панели.
  for (const anim of store.state.schema.anims) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = anim.name;
    button.title = `${anim.group}: ${anim.frames} кадр(ов), ${anim.fps} fps` +
      (anim.loop ? ", зацикливать" : "");
    button.setAttribute("aria-pressed", String(anim.name === store.state.anim));
    button.addEventListener("click", () => {
      stop();
      store.set({ anim: anim.name, frame: 0 });
      renderTabs();
      syncFrameInput();
      requestPreview();
    });
    el.tabs.append(button);
  }
}

function syncFrameInput() {
  const max = animFrames(store.state.schema, store.state.anim) - 1;
  el.frameInput.max = String(Math.max(0, max));
  el.frameInput.value = String(store.state.frame);
}

// --- плеер -----------------------------------------------------------------

function start() {
  stop();
  const frames = animFrames(store.state.schema, store.state.anim);
  if (frames < 2) return;
  store.set({ playing: true });
  el.play.textContent = "■ стоп";
  el.play.dataset.on = "1";
  player = setInterval(() => {
    const total = animFrames(store.state.schema, store.state.anim);
    const next = (store.state.frame + 1) % total;
    store.set({ frame: next });
    syncFrameInput();
    requestPreview();
  }, 140);
}

function stop() {
  if (player) clearInterval(player);
  player = null;
  store.set({ playing: false });
  el.play.textContent = "▶ пуск";
  el.play.dataset.on = "";
}

// --- метрики и замер --------------------------------------------------------

function renderMetrics() {
  const { metrics, lastRender } = store.state;
  if (!metrics) {
    el.metrics.textContent = "";
    return;
  }
  el.metrics.innerHTML =
    `рост <b>${metrics.heads}</b> голов · фигура ${metrics.figure_px} px · ` +
    `голова ${metrics.head_px} px`;
  if (metrics.warnings?.length) {
    el.hint.className = "hint warn";
    el.hint.textContent = metrics.warnings.join("; ");
  } else {
    el.hint.className = "hint";
    el.hint.textContent = lastRender
      ? (lastRender.cached ? "показан из кэша" : `посчитано за ${lastRender.renderMs} мс`)
      : "";
  }
}

store.subscribe((state) => {
  if (state.metrics || state.lastRender) renderMetrics();
});

// --- запуск ----------------------------------------------------------------

/**
 * Масштаб по умолчанию — наибольший целый, который влезает в окно. Брать
 * фиксированные 3 нельзя: на ноутбуке превью уезжает под нижний край, и
 * половину кадра не видно.
 */
function fitScale() {
  const wrap = document.querySelector(".canvas-wrap");
  const room = wrap.clientHeight - 24;
  const options = [1, 2, 3, 4, 5];
  let best = options[0];
  for (const scale of options) {
    if (scale * 128 <= room) best = scale;
  }
  if (best !== store.state.scale) {
    store.set({ scale: best });
    el.scaleInput.value = String(best);
  }
}

async function boot() {
  try {
    const [schema, presets] = await Promise.all([api.schema(), api.presets()]);
    store.set({ schema, presets, anims: [...schema.animDefault] });
    renderTabs();               // вкладки занимают место до подгонки масштаба
    fitScale();
    renderPresets();
    setStatus(`готов: ${presets.length} пресетов, ${schema.anims.length} движений`);
    if (presets.length) loadCharacter(presets[0]);
    window.addEventListener("resize", () => {
      if (!el.scaleInput.dataset.touched) fitScale();
    });
  } catch (error) {
    setStatus(`сервер недоступен: ${error.message}`, "error");
  }
}

el.frameInput.addEventListener("change", () => {
  stop();
  const max = animFrames(store.state.schema, store.state.anim) - 1;
  const value = Math.max(0, Math.min(max, Number(el.frameInput.value) || 0));
  store.set({ frame: value });
  requestPreview();
});

el.scaleInput.addEventListener("change", () => {
  el.scaleInput.dataset.touched = "1";   // выбор человека важнее подгонки
  store.set({ scale: Number(el.scaleInput.value) });
  requestPreview();
});

el.play.addEventListener("click", () => (player ? stop() : start()));

document.addEventListener("keydown", (event) => {
  if (event.target.matches("input, select")) return;
  if (event.code === "Space") {
    event.preventDefault();
    player ? stop() : start();
  }
});

boot();
