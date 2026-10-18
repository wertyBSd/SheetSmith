# SheetSmith

> Fighter sprite sheets forged from code.

![Base roster](docs/roster.png)

**Zero dependencies.** No pip, no npm, no build step. Python standard library and
a browser. If you have Python, you can generate sprites right now.

SheetSmith builds **character sprite sheets from code**. A character is *data*
(palette + archetype + outfit + style), never code: the engine is shared and only
the data differs. Six base fighters ship in the repository; the next one is a JSON
edit.

Built for game developers: the download gives you a sprite sheet, per-frame
timings, hitboxes, hurtboxes, and ready-made bindings for **C, Godot, Unity and
the web**.

[Русская версия](docs/README.ru.md)

---

## Install

Nothing to install. Clone and run:

```bat
py char_generator.py --hero tetrax --scale 3
```

That writes a preview PNG to `preview/`. To get the full pack for your game:

```bat
py server\app.py
```

then open <http://localhost:8000>.

Requirements: Python 3.9+. Tested on 3.9 / 3.11 / 3.14. Nothing else.

## 60 seconds

**Command line** — a sheet, no server:

```bat
py char_generator.py --list                     :: list characters
py char_generator.py --hero tetrax --scale 3    :: one frame, big
py char_generator.py --hero all --scale 1       :: roster of all six
py char_generator.py --hero tetrax --write      :: sprite sheet to out/
py char_generator.py --characters my.json --hero cipher --write
py char_generator.py --dump-schema              :: the description schema
```

**Your engine** — get the pack from the web UI, then open the folder for your
tool (see [Export](#export)):

| you use | open |
|---|---|
| C, C++, raylib, SDL, Godot GDExtension | `<id>/<id>.h` |
| Godot 4 | `<id>/godot/<id>_frames.tres` + `<id>/godot/combat.gd` |
| Unity | `<id>/unity/<id>.cs` |
| Phaser, PixiJS, anything on the web | `<id>/web/animations.json` |
| GameMaker, Construct, anything else | `<id>/sheet.png` + `<id>/sheetsmith.json` |

**Anywhere else** — the manifest is the contract. `sheetsmith.json` says where
every frame lives in the sheet, how long each frame lasts, and where the hit and
hurt boxes are. Read it and you are done; the bindings above are conveniences.

## What you get

A frame is **80×128**. The sheet is a grid: one row per animation, one column per
frame.

Default pack — 6 animations, 18 frames, 4×6 grid:

| row | animation | frames |
|---|---|---|
| 0 | idle | 4 |
| 1 | punch | 3 |
| 2 | kick | 3 |
| 3 | super | 4 |
| 4 | stun | 2 |
| 5 | slip | 2 |

Every run prints the character's **height in heads** — the main metric of pixel
art. If a new fighter comes out at 3.8 heads, they are either small or their head
is the wrong size.

## Export

The **"Download pack"** button gives you a zip:

```
<id>/sheet.png             the whole pack: one row per animation, one column per frame
<id>/animations/<name>.png the same, one file per animation
<id>/<id>.json             the character description — the engine reads it back
<id>/sheetsmith.json       the manifest: frame rects, timings, hit/hurt boxes
<id>/combat.json           the same combat data on its own
<id>/<id>.h                C header: timings and boxes as arrays
<id>/godot/<id>_frames.tres   Godot SpriteFrames with per-frame durations
<id>/godot/combat.gd          GDScript: `hit_boxes("punch", 1)`
<id>/unity/<id>.cs            C#: ScriptableObjects and Rect arrays
<id>/web/animations.json      Phaser / PixiJS animation config
<id>/README.txt            plain text: rows, frames, what loops
```

### The manifest

`sheetsmith.json` is the single source of truth. Every binding above is generated
from it and a test compares each one against it, so the four of them cannot drift
apart.

```json
{
  "schema": "sheetsmith/1",
  "frame": { "w": 80, "h": 128 },
  "origin": { "x": 40, "y": 124 },
  "sheet": { "cols": 4, "rows": 6, "width": 320, "height": 768 },
  "anims": [{
    "name": "punch", "row": 1, "frames": 3, "loop": false,
    "ms": [85, 65, 130], "totalMs": 280,
    "rects": [{ "x": 0, "y": 128, "w": 80, "h": 128 }, ...],
    "activeFrames": [1, 2],
    "hits": [[], [{ "x": 67, "y": 39, "w": 18, "h": 16, "kind": "пуск" }], ...],
    "hurt": [[{ "x": 29, "y": 3, "w": 24, "h": 24, "kind": "голова" }, ...], ...]
  }]
}
```

Coordinates are **frame pixels**, origin at the top-left corner. `origin` is
where to put the character's feet on the ground.

### The C header

Cheapest to consume: `#include "tetrax.h"` and you have the timings as arrays.

```c
#include "tetrax.h"

// duration of frame 2 of "punch"
int ms = TETRAX_ANIM_MS[1][2];

// hit box of that frame: [x1, y1, x2, y2], right/bottom inclusive
int start = TETRAX_HIT_INDEX[6][0], count = TETRAX_HIT_INDEX[6][1];
for (int i = 0; i < count; i++) {
    int *b = TETRAX_HIT_BOXES[start + i];
    // b[0], b[1] — top-left; b[2], b[3] — bottom-right, inclusive
}
```

Box arrays are flat, ordered anim by anim then frame by frame; `*_INDEX[frame]`
is `[start, count]`. Hit boxes are **not** clipped to the frame — a strike at
point-blank range must still reach a neighbour standing close. Hurt boxes *are*
clipped, because a box hanging outside the sheet looks like a bug in the data.

## Combat data

An animation knows more than "how many frames" — that is enough to *show* a move
and useless to *play* it. A fighting game needs per-frame durations and needs to
know where the character strikes and where it can be hit.

```json
"punch": {
  "group": "атака", "fps": 12, "loop": false,
  "ms": [85, 65, 130],
  "frames": [ /* skeleton operations */ ],
  "hits": [
    [],
    [{"part": "fist_f", "w": 18, "h": 16, "kind": "пуск"}],
    [{"part": "fist_f", "w": 16, "h": 14, "kind": "удар"}]
  ]
}
```

| field | meaning |
|---|---|
| `ms` | milliseconds to hold each frame. Absent → `1000 / fps`, i.e. unchanged from before |
| `hits` | hit box per frame: `part` is the joint that strikes, `w`/`h` its size, `kind` is the first contact frame (`пуск`) or the follow-through (`удар`) |
| `loop` | whether to loop |

**A hit box hangs off a joint, not off coordinates.** Punching and kicking are
the same mechanism with a different `part`, and the box follows the limb on its
own — so changing the body, the style or the outfit never breaks a move.

**Hurt boxes come from the skeleton**: six zones — head, torso, two arms, two
legs. Deliberately *not* from the picture: the silhouette changes with outfit and
style, while the game needs to know where the torso is at all times. The head
zone takes its radius from the same number the head is drawn with, so a chibi
gets a chibi-sized zone instead of a hole.

In the editor, the **"boxes"** checkbox draws them over the frame, and the player
runs on the real timings instead of a flat loop.

## How a character is described

Two dictionaries. The palette is colour, the archetype is shape — without the
archetype, every fighter differs only in colour and reads as a recoloured clone.

```json
{
  "characters": {
    "cipher": {
      "materials": {
        "robe":  {"hi": "#2F6B5E", "base": "#1D463E", "dark": "#0F2620"},
        "eye": "#7CFFB2", "neon": "#39FF88", "neon_soft": "#14703C"
      },
      "arch": {
        "head": "mask", "torso": "taper", "hand": "claw", "foot": "wedge",
        "grip": "wrap", "SHOULDER": 24, "WAIST": 17,
        "ARM": 5, "LEG": 7, "ankle": 0.36
      }
    }
  }
}
```

`--characters` accepts this file, so anything the editor builds goes straight to
the CLI. `py char_generator.py --dump-schema` prints the whole schema: every
field, every range.

## Style

Proportions and palette say **which** fighter this is; style says **how** it is
drawn.

| parameter | range | default | what it does |
|---|---|---|---|
| `head` | 0.6…1.8 | 1.0 | head size: bigger for chibi, smaller for realistic |
| `bands` | 2…6 | 3 | shading bands: 2 coarse, 3 strict 16-bit, 5–6 soft light |
| `outline` | 0…2 | 1 | outline thickness in pixels, 0 for none |

```json
"style": { "head": 1.45, "bands": 3, "outline": 1 }
```

Three bands is the pixel-art language and gives exactly the tones it always did:
the light boundaries did not move, the range between them is just split into more
steps. So a soft style needs neither new colours nor blurring — the interpolation
runs inside the material's own three tones.

Height in heads is measured with the same coefficient the head is drawn with, so
a chibi honestly reports 3.7 heads instead of 5.

Six presets in [data/styles.json](data/styles.json): strict 16-bit, chibi,
cartoon, soft, gritty, flat. Style layers over any character and any outfit: a
knight drawn in a cartoon style is one line, not a copy of the engine.

## Clothing

Body parts are **slots**, and each slot takes its own material:

| slot | paints | default |
|---|---|---|
| `body` | torso, neck, collar | `robe` |
| `arms` | arm to the wrist | `robe` |
| `hands` | fist and cuff | cloth or armour, by grip |
| `legs` | trousers | `robe` |
| `feet` | boot and knee pad | cloth or armour, by grip |
| `shoulders` | pauldrons and plates | `armor` |
| `head` | hood, helmet, hair | `robe` |

```json
"outfit": {"body": "trench", "legs": "trousers", "feet": "shoes", "head": "skin"}
```

Omitting a slot keeps the old value, and a character without clothing renders
**pixel for pixel as before** — the golden test enforces it.

Ten sets in [data/outfits.json](data/outfits.json): cyber-monk, street racer,
knight, mage, ninja, athlete, builder, trench, mecha, beach. A set is a palette,
a slot mapping **and garment shape**; it never touches the archetype, so it
combines with any body and any colours.

Garment shape is separate from colour — six parameters:

| parameter | values | what changes |
|---|---|---|
| `sleeve` | `long` `short` `none` | sleeve length; bare skin fills the gap |
| `pants` | `long` `short` | full length or mid-thigh |
| `cape` | `none` `short` `long` | cape behind the torso, drawn under it |
| `collar` | `v` `high` `scarf` | neckline, closed collar, scarf with a tail |
| `belt` | `thin` `wide` `sash` `none` | thin wrap, wide belt, over the shoulder |
| `shoulders` | `pads` `plates` `none` | padded pauldrons, two rows of plates, none |

## Animations

Animations are **data** in [data/animations.json](data/animations.json), not
Python functions. A frame is a list of skeleton operations:

```json
{"shift": {"parts": ["head", "neck", "chest"], "dx": 0, "dy": 20}}
{"aim":   {"side": "f", "d": [27, 2], "bend": 1.0}}
{"step":  {"side": "b", "d": [-5, -1], "bend": 1.0}}
```

A new animation is a file edit, and one animation works on any body: targets are
offsets from a reference stance, not absolute frame pixels. Elbow and knee are
two-bone IK, so limb length holds by construction.

**23 animations** in five groups: idle; movement (walk, run, crouch, jump, fall,
land, dash); defence (block); attack (jab, cross, hook, uppercut, low_kick,
round_kick, knee, throw, punch, kick, super); reaction (hit, stun, slip).

Which of them land in the export is up to checkboxes — the sheet is built for the
chosen set: as many rows as you picked, as many columns as the longest animation.

## Web editor

```bat
py server\app.py                      :: http://localhost:8000
py server\app.py --host 0.0.0.0       :: reachable from the LAN
py server\app.py --jobs 7             :: render in 7 processes
```

No build step: plain ES modules served as-is, so `py server\app.py` gives a
working editor immediately.

* archetype variants and slider bounds come from `/api/schema`, not from constants
  duplicated in the frontend — the engine is the source of truth;
* stale preview requests are aborted, so dragging a slider never leaves an old
  frame on screen;
* the default scale fits the window but stays a whole number — otherwise pixel
  art goes blurry;
* height in heads and engine warnings are shown under the frame.

| method | path | what |
|---|---|---|
| GET | `/api/health` | alive, cache stats |
| GET | `/api/schema` | archetype variants, bounds, animation grid |
| GET | `/api/presets` | the six base characters as descriptions |
| POST | `/api/preview` | one frame as PNG (`anim`, `frame`, `scale`) |
| POST | `/api/measure` | height in heads and warnings |
| POST | `/api/combat` | per-frame timings and hit/hurt boxes |
| POST | `/api/jobs` | long export (`kind: sheet\|roster\|pack`), returns `jobId` |
| GET | `/api/jobs/<id>` | progress: `done` of `total` |
| GET | `/api/jobs/<id>/file` | the finished PNG or zip |
| POST | `/api/save` | write assets to `out/` and the hero to `characters.local.json` |

Repeated requests for the same frame come from cache instantly, and `--jobs`
spreads the sheet across processes: on 4 processes it builds about three times
faster and is **byte for byte** identical to the single-threaded engine.

## Programming access

The engine imports as a module. Drawing takes a **character specification**, not
an id, so two drafts can never get swapped — that is exactly the bug any
multi-user renderer hits.

```python
import char_generator as G

spec = G.CharSpec.from_json("cipher", {
    "materials": {"robe": {"hi": "#2F6B5E", "base": "#1D463E", "dark": "#0F2620"},
                  "eye": "#7CFFB2", "neon": "#39FF88", "neon_soft": "#14703C"},
    "arch": {"head": "mask", "torso": "taper", "hand": "claw", "foot": "wedge",
             "grip": "wrap", "SHOULDER": 24, "WAIST": 17,
             "ARM": 5, "LEG": 7, "ankle": 0.36},
})

G.render_frame(dict(G.ANIMS)["punch"][1], spec)   # Canvas 80x128
G.build_sheet(spec)                               # Canvas 320x768
G.anim_timing("punch", spec=spec)                 # ms, hits, hurt
G.measure_heads(spec)                             # (5.22, 23, 120)
G.validate_spec(data, "cipher")                   # what breaks, before rendering
G.schema_json()                                   # bounds and variants for a UI
```

`CharSpec.to_json()` returns the `characters.json` format, so everything the
editor builds reaches the CLI with no conversion.

## Tests

```bat
py tests\test_spec_api.py            :: specification, JSON, validation (seconds)
py tests\test_export.py              :: every binding matches the manifest
py tests\test_server.py              :: routes, cache, jobs (~2 min)
py tests\test_parity.py              :: the picture did not drift (6 heroes, ~60 s)
py tests\test_parity.py --update     :: rewrite the golden images
```

The golden images are committed, so any change shows up in `git diff` as a file
change. `test_server.py` compares the sheet from `/api/jobs/.../file` against the
CLI golden: the site and the command line must produce the same picture.

`test_export.py` is the one to read before adding a binding — it compares each
generated file against the manifest it came from, which is what keeps four
engines from telling four different stories about the same fighter.

## Where this is going

SheetSmith started as a fighting-game sheet generator, but the goal is wider:
characters in **different styles** (chibi, cartoon, 16-bit, closer to realistic)
and **different clothing** (modern to fantasy), for fighting games, beat-'em-ups
and adventures. Side view first, sprites in volume when mature.

So a character is data (body, archetype, materials, skeleton) and the pixel
engine is just one consumer of that description. The full analysis — what already
does not stand in the way, what does, and the order it gets fixed in — is in
[PLAN.md](PLAN.md).

### References

Four games set the target. They are references for the **language** — proportions,
weight of a hit, readability of clothing — not for copying characters, costumes
or silhouettes.

| game | what it sets |
|---|---|
| The Friends of Ringo Ishikawa | height in heads: taller and thinner, small head, silhouette readable at a glance |
| Streets of Rage 3 | you recognise the character by clothing and gait, not by skin colour |
| Mortal Kombat II | per-move timings, its own active window, its own hit box |
| Golden Axe III | weight: broad shoulders, big fists, recovery longer than the wind-up |

Practically, this means: the frame size has to become a style parameter
(Ishikawa does not fit in 80×128), height in heads has to be selectable rather
than a constant (5 for Golden Axe, 6.5 for Ishikawa), and a move's weight comes
from its timings plus a pose, not from timing alone.

### Adding a binding

`sheetsmith.json` is the contract; a binding is a function from the manifest to a
text file. Add it in `server/export.py`, register it in `write_pack`, and extend
`tests/test_export.py` — which parses the generated file back and compares it
against the manifest. Generated code is commented in English: it lands in other
people's repositories.

## Requirements

Python 3.9 or newer. Standard library only. Nothing to install, ever.