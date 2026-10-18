/* Панель управления: палитра, архетип, пропорции.
 *
 * Границы вариантов и диапазоны берутся из /api/schema, а не из констант
 * здесь: движок — источник истины, и подпись слайдера «плечи 24 px» должна
 * соответствовать тому, что сервер на самом деле принимает.
 *
 * Правки не ждут отклика: ползунок дергает рендер через debounce, а сервер
 * всё равно отменяет устаревшие запросы. Замер роста — отдельный запрос с
 * более длинной паузой, потому что он рисует кадр и меняется медленнее.
 */

import { store } from "./store.js";

const TONES = [["hi", "свет"], ["base", "тон"], ["dark", "тень"]];

// Подписи материалов по-человечески: движок оперирует ключами robe/armor/skin,
// но тот, кто правит палитру, знает их как рясу, броню и кожу.
const MATERIAL_NAMES = {
  robe: "ряса", armor: "броня", skin: "кожа",
  eye: "глаз", neon: "неон", neon_soft: "неон т.",
};
const RAMP_KEYS = ["robe", "armor", "skin"];
const COLOR_KEYS = ["eye", "neon", "neon_soft"];

function group(title) {
  const box = document.createElement("div");
  box.className = "group";
  const heading = document.createElement("h2");
  heading.textContent = title;
  box.append(heading);
  return box;
}

function colorField(label, value, onInput) {
  const cell = document.createElement("div");
  cell.className = "color-cell";
  const caption = document.createElement("label");
  caption.textContent = label;
  const input = document.createElement("input");
  input.type = "color";
  input.value = value || "#000000";
  input.addEventListener("input", () => onInput(input.value));
  cell.append(caption, input);
  return cell;
}

function slider(label, { min, max, step }, value, format, onInput, onCommit) {
  const field = document.createElement("div");
  field.className = "field";
  const caption = document.createElement("label");
  caption.textContent = label;
  const readout = document.createElement("span");
  readout.className = "value";
  readout.textContent = format(value);

  const input = document.createElement("input");
  input.type = "range";
  input.min = String(min);
  input.max = String(max);
  input.step = String(step);
  input.value = String(value);
  // input — во время перетаскивания, change — когда отпустили: так картинка
  // не дёргается на каждом пикселе движения мыши.
  input.addEventListener("input", () => {
    const next = Number(input.value);
    readout.textContent = format(next);
    onInput(next);
  });
  input.addEventListener("change", () => onCommit(Number(input.value)));

  field.append(caption, readout, input);
  return field;
}

function selectField(label, options, value, onChange) {
  const field = document.createElement("div");
  field.className = "field";
  const caption = document.createElement("label");
  caption.textContent = label;
  const select = document.createElement("select");
  for (const option of options) {
    const item = document.createElement("option");
    item.value = option;
    item.textContent = option;
    if (option === value) item.selected = true;
    select.append(item);
  }
  select.addEventListener("change", () => onChange(select.value));
  field.append(caption, select);
  return field;
}

/**
 * Собирает панель под текущего персонажа. Пересобирается целиком при смене
 * персонажа: контролы должны показать именно его палитру, а не ту, что была
 * до переключения.
 */
export function renderEditor(root, hooks) {
  const { schema, character } = store.state;
  if (!schema || !character) return;

  const parts = [];

  // --- палитра ---
  const palette = group("Палитра");
  for (const key of RAMP_KEYS) {
    const row = document.createElement("div");
    row.className = "colors";
    const material = character.materials[key] || {};
    for (const [field, title] of TONES) {
      row.append(colorField(`${MATERIAL_NAMES[key]} · ${title}`, material[field],
        (value) => hooks.edit(() => store.setMaterial(key, field, value))));
    }
    palette.append(row);
  }

  const accents = document.createElement("div");
  accents.className = "colors";
  for (const key of COLOR_KEYS) {
    accents.append(colorField(MATERIAL_NAMES[key], character.materials[key],
      (value) => hooks.edit(() => store.setMaterial(key, "base", value))));
  }
  palette.append(accents);

  // Блик: у брони и кожи он есть, у ткани почти никогда.
  for (const key of ["armor", "skin"]) {
    const material = character.materials[key] || {};
    const row = document.createElement("div");
    row.className = "colors";
    row.append(colorField(`${MATERIAL_NAMES[key]} · блик`, material.spec || "#ffffff",
      (value) => hooks.edit(() => store.setMaterial(key, "spec", value))));
    palette.append(row);
    palette.append(slider(`${MATERIAL_NAMES[key]}: блик узкий`,
      { min: 0, max: 1, step: 0.02 }, material.spec_lam ?? 0.86, (v) => v.toFixed(2),
      (v) => hooks.edit(() => store.setMaterial(key, "spec_lam", v), false)));
    palette.append(slider(`${MATERIAL_NAMES[key]}: блик гаснет`,
      { min: 0, max: 1, step: 0.02 }, material.spec_glow ?? 0.4, (v) => v.toFixed(2),
      (v) => hooks.edit(() => store.setMaterial(key, "spec_glow", v), false)));
  }
  parts.push(palette);

  // --- архетип ---
  const arch = group("Архетип");
  const variants = schema.arch.variants;
  const labels = { head: "голова", torso: "торс", hand: "кисть", foot: "стопа", grip: "хват" };
  for (const [key, options] of Object.entries(variants)) {
    arch.append(selectField(labels[key] || key, options, character.arch[key],
      (value) => hooks.edit(() => store.setArch(key, value))));
  }
  parts.push(arch);

  // --- пропорции ---
  const sizes = group("Пропорции");
  const ranges = schema.arch.ranges;
  const names = { SHOULDER: "плечи", WAIST: "талия", ARM: "толщина руки", LEG: "толщина ноги" };
  for (const [key, [min, max]] of Object.entries(ranges)) {
    if (key === "ankle") continue;
    sizes.append(slider(names[key] || key, { min, max, step: 1 }, character.arch[key],
      (v) => `${v} px`, (v) => hooks.edit(() => store.setArch(key, v))));
  }
  const [amin, amax] = ranges.ankle;
  sizes.append(slider("сужение к щиколотке", { min: amin, max: amax, step: 0.01 },
    character.arch.ankle, (v) => v.toFixed(2),
    (v) => hooks.edit(() => store.setArch("ankle", v))));
  parts.push(sizes);

  root.replaceChildren(...parts);
}
