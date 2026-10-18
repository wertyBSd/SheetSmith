/* Состояние редактора: персонаж, вид и подписки.
 *
 * Сделано без библиотеки по той же причине, по которой сервер без FastAPI:
 * здесь нужно одно состояние, одна подписка и предсказуемое поведение, а не
 * экосистема. Смена решения на zustand/react — это файлы api/store/components,
 * а не переписывание логики.
 */

export const store = {
  state: {
    schema: null,
    presets: [],
    character: null,   // { id, materials, arch } — ровно формат characters.json
    anim: "idle",
    frame: 0,
    scale: 3,
    playing: false,
    busy: false,
    metrics: null,     // { heads, head_px, figure_px, warnings }
    lastRender: null,  // { cached, renderMs }
    error: null,
  },

  subscribers: new Set(),

  subscribe(fn) {
    this.subscribers.add(fn);
    return () => this.subscribers.delete(fn);
  },

  set(patch) {
    Object.assign(this.state, patch);
    for (const fn of this.subscribers) fn(this.state);
  },

  /** Ключ архетипа: setArch("SHOULDER", 26). */
  setArch(key, value) {
    this.state.character.arch[key] = value;
    this.set({});
  },

  /**
   * Цвет или тон материала: setMaterial("robe", "hi", "#2F6B5E") и
   * setMaterial("neon", "base", "#39FF88") — простые цвета тоже живут в
   * словаре, отдельного поля у них нет.
   */
  setMaterial(name, field, value) {
    const material = this.state.character.materials[name];
    if (!material) return;
    material[field] = value;
    this.set({});
  },
};

/** Глубокая копия описания: состояние не должно делиться с исходным пресетом. */
export function cloneCharacter(character) {
  return JSON.parse(JSON.stringify(character));
}

export function animFrames(schema, anim) {
  const found = schema?.anims?.find((a) => a.name === anim);
  return found ? found.frames : 1;
}
