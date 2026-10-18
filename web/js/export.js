/* Выгрузка набора: лист PNG или zip с ассетами для игры.
 *
 * Здесь важно одно: выгрузка идёт задачей с опросом статуса, а не одним
 * запросом. Лист из двадцати анимаций — это больше сотни кадров, десятки
 * секунд рендера, и синхронный запрос упал бы по таймауту. Клиент спрашивает
 * «сколько посчитано», рисует полосу и в конце скачивает файл.
 */

import * as api from "./api.js";

const LABELS = {
  sheet: { button: "Скачать спрайт-лист (PNG)", filename: "sheet" },
  pack: { button: "Скачать набор для игры (ZIP)", filename: "sprites" },
};

export function renderExport(root, getState) {
  const box = document.createElement("div");
  box.className = "group";
  const heading = document.createElement("h2");
  heading.textContent = "Выгрузка";
  box.append(heading);

  const wrap = document.createElement("div");
  wrap.className = "export";

  const bar = document.createElement("div");
  bar.className = "progress";
  const fill = document.createElement("i");
  bar.append(fill);

  const note = document.createElement("p");
  note.className = "hint";

  const buttons = {};
  for (const kind of ["sheet", "pack"]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = LABELS[kind].button;
    if (kind === "pack") button.classList.add("primary");
    button.addEventListener("click", () => generate(kind, { getState, buttons, note, fill }));
    buttons[kind] = button;
    wrap.append(button);
  }

  const save = document.createElement("button");
  save.type = "button";
  save.textContent = "Сохранить в проект (out/ + characters.local.json)";
  save.addEventListener("click", async () => {
    const state = getState();
    if (!state.character) return;
    save.disabled = true;
    note.className = "hint";
    note.textContent = "сохраняю…";
    try {
      const result = await api.save(state.character);
      note.textContent = `ассет: ${result.png}, библиотека: ${result.library}. В CLI: ${result.cli}`;
    } catch (error) {
      note.className = "hint bad";
      note.textContent = `не получилось: ${error.message}`;
    } finally {
      save.disabled = false;
    }
  });
  wrap.append(save, bar, note);

  box.append(wrap);
  root.append(box);
}

function busy(buttons, active) {
  for (const [kind, button] of Object.entries(buttons)) {
    button.disabled = kind === active ? false : active !== null;
  }
}

async function generate(kind, ctx) {
  const { getState, buttons, note, fill } = ctx;
  const state = getState();
  if (!state.character || !state.anims?.length) {
    note.className = "hint warn";
    note.textContent = "Выбери персонажа и хотя бы одно движение.";
    return;
  }

  busy(buttons, kind);
  fill.style.width = "0%";
  note.className = "hint";
  try {
    const { jobId } = await api.startJob({
      kind,
      character: state.character,
      anims: state.anims,
      scale: 1,
    });
    const total = await waitForJob(jobId, note, fill);
    if (!total) return;
    download(api.jobFileUrl(jobId), `${state.character.id}-${LABELS[kind].filename}`);
    note.textContent = total;
  } catch (error) {
    note.className = "hint bad";
    note.textContent = `не получилось: ${error.message}`;
  } finally {
    busy(buttons, null);
  }
}

async function waitForJob(jobId, note, fill) {
  const deadline = Date.now() + 15 * 60 * 1000;
  while (Date.now() < deadline) {
    const job = await api.jobStatus(jobId);
    if (job.status === "error") {
      note.className = "hint bad";
      note.textContent = `ошибка рендера: ${job.error}`;
      return null;
    }
    if (job.status === "done") {
      fill.style.width = "100%";
      return `готово: ${job.done} из ${job.total} кадров`;
    }
    fill.style.width = `${Math.round((job.done / Math.max(1, job.total)) * 100)}%`;
    note.textContent = `считаем ${job.done} из ${job.total} кадров…`;
    await new Promise((resolve) => setTimeout(resolve, 700));
  }
  note.className = "hint bad";
  note.textContent = "рендер не уложился в 15 минут";
  return null;
}

function download(url, filename) {
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
}
