# RTL Assistant

Read and edit right-to-left text in Sublime Text.

Sublime draws every character as a separate glyph, left to right. It implements
neither the Unicode Bidirectional Algorithm nor Arabic contextual shaping, and
exposes no API to change how buffer text is drawn. Arabic-script text therefore
appears as disconnected letters in reverse order, and Hebrew appears backwards.

This package renders the text correctly and lets you edit it there, while your
file keeps its original characters.

```
file on disk    سلام دنیا
without this    ﺱ ﻝ ﺍ ﻡ   ﺩ ﻥ ﯼ ﺍ      disconnected, reversed
with this       ﺎﯿﻧﺩ ﻡﻼﺳ                joined, right to left
```

## Install

Package Control → *Install Package* → **RTL Assistant**.

Or clone into your `Packages` directory, which you can locate with
*Preferences → Browse Packages*:

```sh
git clone https://github.com/jd1378/sublime_rtl_assistant RTLAssistant
```

Use that folder name. The plugin code itself does not care — its imports are
relative — but the *Preferences → Package Settings* entry resolves a path
under `Packages/RTLAssistant`, so a different name leaves that menu item
pointing nowhere. Package Control installs it under the right name for you.

## Use

Command Palette → **RTL: Toggle View on This File**. Also on the **View** menu,
and on right-click for both the tab and the editor.

Two modes:

| mode | what it does |
| --- | --- |
| **Toggle** (`rtl_toggle`) | Renders the file in its own tab. One tab, no split. |
| **Split** (`rtl_split_open`) | Opens a second pane showing the rendering, with the real text beside it. |

Both are editable, and both write ordinary characters to disk. Choose the split
view if you want to see the real text as well, or if you want Sublime's native
undo — see *Trade-offs* below.

Editing works as you would expect. Typing, Backspace, Delete, Ctrl+Backspace,
arrow keys and Ctrl+Arrow all operate on the text rather than on screen
positions, so a word runs in the direction it reads.

## Settings

Preferences → Package Settings → RTL Assistant → Settings.

```json
{
    // render right-to-left files automatically on open
    "rtl_auto_render": false,

    // never render a file larger than this, in characters
    "rtl_max_file_size": 1048576
}
```

`rtl_auto_render` needs two things to be true: the setting is on, *and* the
file actually contains right-to-left letters. Files without any are untouched,
so enabling it costs nothing on a codebase that has none.

## Scripts

| script | shaping | reordering |
| --- | --- | --- |
| Arabic | yes | yes |
| Persian | yes | yes |
| Urdu | yes | yes |
| Sindhi | yes | yes |
| Uyghur | yes | yes |
| Hebrew | not needed | yes |
| Kurdish, Pashto, Jawi | partial | yes |

Shaping tables are generated from Unicode's own decomposition data, and cover
every Arabic-script letter for which Unicode encodes presentation forms.

Some letters — `ټ ځ څ ډ ړ ږ ښ ګ ڼ` in Pashto, `ڕ ڵ ێ` in Kurdish, `ڠ ڬ ڽ` in
Jawi — have **no presentation forms anywhere in Unicode**, so there is nothing
to map them to. Text using them still reads in the right order; only the joined
shapes are unavailable. Supporting those properly needs a real OpenType shaping
engine, which is out of scope here.

## Trade-offs

Worth knowing before you pick a mode.

**Undo.** In toggle mode the real text does not live in a buffer of its own, so
Sublime's history has nothing meaningful to undo and this package keeps its own.
It steps back a word at a time, which is coarser than Sublime's native history.
The split view keeps native undo intact.

**Selections.** A contiguous selection on screen can cover a non-contiguous run
of the underlying text where direction changes. Deleting, copying and cutting
all do the right thing; the highlight is just an imperfect picture of it.
Copying always yields the real characters, never the rendering, so what you
paste elsewhere is ordinary text.

**Multiple cursors** are not supported in the rendered view. An edit with more
than one cursor is refused and the view resynced rather than guessed at.

**Clicking on a direction boundary** is ambiguous by nature: one position on
screen can stand for two positions in the text. The caret binds to whichever
run the surrounding letters put it in. If that is not the one you wanted, click
one position over.

## Your file is never rewritten

The rendering uses Arabic Presentation Forms, a legacy compatibility encoding.
That form is for display only and must never reach disk — a file written that
way would look fine on screen and be wrong everywhere else.

Three things guarantee it does not:

- the real text is kept beside the rendering and is always the authority
- saving swaps the real characters in before the write, whichever view you save
  from, and restores the rendering afterwards
- closing restores the real text, so a restored session never comes back
  holding presentation forms

Any edit that cannot be translated confidently is refused and the view
resynced, so the file never receives a guess.

## How it works

`rtl_shaper.py` is pure Python with no Sublime imports, and does the real work:

1. **Shaping** — each letter becomes the presentation form its neighbours call
   for, including the lam-alef ligatures.
2. **Reordering** — a simplified Unicode Bidirectional Algorithm, including
   rule N0 for bracket pairs, so `foo(x)` inside a right-to-left line stays
   intact.

The inverse is not computed, because it does not exist: many different logical
strings render to the same visual string. Instead the permutation that produced
the rendering is kept, and each edit is carried back through it.

Rendering is cached per line, so re-rendering after a keystroke costs a dictionary
lookup for every line you did not touch.

| file | first render | per keystroke |
| --- | --- | --- |
| 1,000 lines | 74 ms | 0.14 ms |
| 5,000 lines (400 KB) | 374 ms | 0.92 ms |
| 5,000 lines, no RTL | 2 ms | 0.42 ms |

Measured on CPython 3.13; Sublime's plugin host is slower, so expect roughly
1.5–2× for the first render. Typing stays imperceptible either way.

## Tests

The shaping and editing logic runs outside Sublime:

```sh
python3 tests/run.py
```

Covers presentation forms against expected codepoints, bidi reordering, bracket
pairing, mark placement, edit translation, and the in-place editing state
machine driven through a fake view.

## Licence

MIT. See `LICENSE`.
