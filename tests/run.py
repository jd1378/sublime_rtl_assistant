"""Tests for RTL Assistant.

    python3 tests/run.py

Runs outside Sublime. The shaping module is plain Python; the editor-facing
modules are imported against a stub of the Sublime API, and the in-place
editing mode is driven through a fake view.
"""

import importlib
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.dirname(HERE)
PKG_NAME = os.path.basename(PKG_DIR)
sys.path.insert(0, os.path.dirname(PKG_DIR))


# --------------------------------------------------------------------------
# enough of the Sublime API to import the editor-facing modules
# --------------------------------------------------------------------------

class Region(object):
    def __init__(self, a, b=None):
        self.a = a
        self.b = a if b is None else b

    def empty(self):
        return self.a == self.b


_sublime = types.ModuleType("sublime")
_sublime.Region = Region
_sublime.set_timeout = lambda fn, ms=0: fn()
_sublime.windows = lambda: []
_sublime.load_settings = lambda name: {}
_clipboard = [""]
_sublime.set_clipboard = lambda text: _clipboard.__setitem__(0, text)
_sublime.get_clipboard = lambda: _clipboard[0]
_sublime.CLASS_WORD_START = 1
_sublime.CLASS_WORD_END = 2
_sublime.CLASS_PUNCTUATION_START = 4
_sublime.CLASS_PUNCTUATION_END = 8
_sublime.CLASS_LINE_START = 64
_sublime.CLASS_LINE_END = 128
sys.modules["sublime"] = _sublime

_plugin = types.ModuleType("sublime_plugin")


class _Base(object):
    def __init__(self, view=None):
        self.view = view

    def is_visible(self):
        return True


_plugin.TextCommand = _Base
_plugin.WindowCommand = _Base
_plugin.EventListener = _Base
sys.modules["sublime_plugin"] = _plugin

shaper = importlib.import_module(PKG_NAME + ".rtl_shaper")
inplace = importlib.import_module(PKG_NAME + ".rtl_inplace")


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------

_failures = []
_count = [0]


def check(label, got, want):
    _count[0] += 1
    if got != want:
        _failures.append((label, got, want))
        print("   FAIL %-46s %r  want %r" % (label, got, want))
    else:
        print("   ok   %-46s %r" % (label, got))


def section(title):
    print("\n=== %s ===" % title)


def cps(text):
    return " ".join("%04X" % ord(ch) for ch in text)


def diff(old, new):
    limit = min(len(old), len(new))
    prefix = 0
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    return prefix, old[prefix:len(old) - suffix], new[prefix:len(new) - suffix]


class Editor(object):
    """The buffer as the plugin models it: real text plus a caret."""

    def __init__(self, text=""):
        self.text = text
        self.caret = len(text)

    def visual(self):
        return shaper.to_visual_text(self.text)

    def anchor(self):
        return shaper.buffer_visual_caret(self.text, self.caret), self.caret

    def type(self, typed):
        at_visual, at_logical = self.anchor()
        old = self.visual()
        new = old[:at_visual] + typed + old[at_visual:]
        grew = len(new) - len(old)
        exact = (grew > 0
                 and new[:at_visual] == old[:at_visual]
                 and new[at_visual + grew:] == old[at_visual:])
        if exact:                       # plain typing at the caret
            logical = shaper.to_logical(new[at_visual:at_visual + grew])
            self.text = (self.text[:at_logical] + logical
                         + self.text[at_logical:])
            self.caret = at_logical + len(logical)
            return
        prefix, removed, inserted = diff(old, new)
        text, caret = shaper.apply_visual_buffer_edit(
            self.text, old, prefix, removed, inserted)
        self.text, self.caret = text, caret

    def backspace(self):
        if self.caret > 0:
            self.text = self.text[:self.caret - 1] + self.text[self.caret:]
            self.caret -= 1


# --------------------------------------------------------------------------
# 1. shaping
# --------------------------------------------------------------------------

section("contextual shaping")
for text, want in [
    ("سلام", "FEE1 FEFC FEB3"),              # lam-alef ligature
    ("ایران", "FEE5 FE8D FEAE FBFE FE8D"),
    ("می‌رود", "FEA9 FEED FEAD FBFD FEE3"),  # ZWNJ breaks the join
    ("کتاب", "FE8F FE8E FE98 FB90"),
    ("گلبرگ", "FB92 FEAE FE92 FEE0 FB94"),
    ("الله", "FEEA FEE0 FEDF FE8D"),
    ("مُحَمَّد", "FEAA FEE4 064E 0651 FEA4 064E FEE3 064F"),  # marks stay put
    ("", ""),
]:
    check(repr(text), cps(shaper.to_visual(text)), want)

section("the tables agree with Unicode")
try:
    import unicodedata
    tags = {"<isolated>": 0, "<final>": 1, "<initial>": 2, "<medial>": 3}
    derived = {}
    for low, high in ((0xFB50, 0xFDFF), (0xFE70, 0xFEFF)):
        for cp in range(low, high + 1):
            parts = unicodedata.decomposition(chr(cp)).split()
            if parts and parts[0] in tags and len(parts) == 2:
                base = chr(int(parts[1], 16))
                derived.setdefault(base, [None] * 4)[tags[parts[0]]] = chr(cp)
    wrong = [b for b, f in shaper._FORMS.items()
             if b != "ـ" and list(f) != derived.get(b)]
    check("every letter matches Unicode decomposition", wrong, [])
except ImportError:                      # pragma: no cover
    print("   skipped, no unicodedata")

# --------------------------------------------------------------------------
# 2. reordering
# --------------------------------------------------------------------------

section("bidirectional reordering")
check("brackets stay whole (rule N0)",
      shaper.to_visual("if (شرط) { کار }"), "if (ﻁﺮﺷ) { ﺭﺎﮐ }")
check("a function call inside RTL",
      shaper.to_visual("تابع foo(x) را صدا بزن"),
      "ﻥﺰﺑ ﺍﺪﺻ ﺍﺭ foo(x) ﻊﺑﺎﺗ")
check("digits keep their order",
      shaper.to_visual("سال ۱۴۰۳ بود"), "ﺩﻮﺑ ۱۴۰۳ ﻝﺎﺳ")
check("a pure LTR line is untouched",
      shaper.to_visual("def f(): return 1"), "def f(): return 1")
check("Hebrew reorders without shaping",
      shaper.to_visual("שלום עולם"), "םלוע םולש")

section("script coverage")
check("Arabic", shaper.to_visual("السلام عليكم"), "ﻢﻜﻴﻠﻋ ﻡﻼﺴﻟﺍ")
check("Urdu", shaper.to_visual("ٹماٹر"), "ﺮﭨﺎﻤﭨ")
check("Sindhi", shaper.to_visual("ڀولو"), "ﻮﻟﻮﭜ")
check("Uyghur", shaper.to_visual("ئۇيغۇر"), "ﺭﯘﻐﻳﯘﺋ")
check("no RTL detected in code", shaper.has_rtl("def f(): pass"), False)
check("RTL detected in a comment", shaper.has_rtl("# مقدار"), True)

# --------------------------------------------------------------------------
# 3. carets
# --------------------------------------------------------------------------

section("caret mapping")
line = "<html>این یک تست است</html>"
check("just past the opening tag stays there",
      shaper.logical_caret(line, 6, prefer="ltr"), 6)
check("before the closing tag is reachable",
      shaper.logical_caret(line, 20, prefer="ltr"), 20)
check("rendered just past the tag", shaper.visual_caret(line, 6), 6)
check("one step enters the run at its own start",
      shaper.step_caret(line, 6, True), 7)
check("a ligature has no interior position",
      shaper.step_caret("سلام", 1, True), 3)
check("direction after an English letter",
      shaper.caret_direction("سلام world", 10), "ltr")
check("a space does not drag it back",
      shaper.caret_direction("world سلام", 6), "ltr")

walk = [shaper.visual_caret(line, p) for p in range(len(line) + 1)]
check("every step moves somewhere new",
      [i for i, (a, b) in enumerate(zip(walk, walk[1:])) if a == b], [])

# --------------------------------------------------------------------------
# 4. editing
# --------------------------------------------------------------------------

section("typing")
for start, keys, want in [
    ("", "سلام دنیا", "سلام دنیا"),
    ("", "سلام world", "سلام world"),
    ("hello ", "دنیا", "hello دنیا"),
    ("", "salam دنیا ok", "salam دنیا ok"),
    ("# ", "کد foo() است", "# کد foo() است"),
    ("", "if (شرط) x=1", "if (شرط) x=1"),
    ("کتاب", "", "کتاب"),
]:
    editor = Editor(start)
    for key in keys:
        editor.type(key)
    check("type %r" % (keys or "nothing"), editor.text, want)

section("deleting")
editor = Editor()
for key in "سلام":
    editor.type(key)
editor.type("w")
editor.backspace()
check("backspace takes the English letter", editor.text, "سلام")
editor.backspace()
check("then the Arabic-script one", editor.text, "سلا")

section("edits expressed in visual order")
for text, label, mutate, want in [
    ("سلام دنیا", "delete the leftmost word", lambda v: v[4:], "سلام "),
    ("خط اول\nخط دوم", "edit the second line",
     lambda v: v.replace("ﻡﻭﺩ", "ﻡﻭﺩX"), "خط اول\nخط Xدوم"),
    ("x = 1  # مقدار", "edit the code half",
     lambda v: v.replace("x = 1", "x = 10"), "x = 10  # مقدار"),
    ("متن (test) ادامه", "no change round-trips", lambda v: v,
     "متن (test) ادامه"),
]:
    old = shaper.to_visual_text(text)
    prefix, removed, inserted = diff(old, mutate(old))
    got, _ = shaper.apply_visual_buffer_edit(text, old, prefix, removed,
                                             inserted)
    check(label, got, want)

# --------------------------------------------------------------------------
# 5. the in-place mode, driven through a fake view
# --------------------------------------------------------------------------

class Selection(list):
    def clear(self):
        del self[:]

    def add(self, region):
        self.append(region)


class FakeView(object):
    _next_id = [1]

    def __init__(self, text=""):
        self.text = text
        self._id = FakeView._next_id[0]
        FakeView._next_id[0] += 1
        self._sel = Selection([Region(0, 0)])
        self._settings = {}
        self.scratch = False
        self.dirty = False
        self.history = []

    def id(self):
        return self._id

    def size(self):
        return len(self.text)

    def substr(self, region):
        return self.text[region.a:region.b]

    def sel(self):
        return self._sel

    def show(self, point):
        pass

    def set_status(self, key, value):
        pass

    def erase_status(self, key):
        pass

    def is_scratch(self):
        return self.scratch

    def set_scratch(self, value):
        self.scratch = value

    def is_dirty(self):
        return self.dirty and not self.scratch

    def settings(self):
        owner = self

        class Settings(object):
            def get(self, key, default=None):
                return owner._settings.get(key, default)

            def set(self, key, value):
                owner._settings[key] = value

            def erase(self, key):
                owner._settings.pop(key, None)

        return Settings()

    def run_command(self, name, args=None):
        if name == "rtl_inplace_replace":
            self.history.append((self.text, self.dirty))
            self.text = args["text"]
            self.dirty = True
        elif name == "undo" and self.history:
            # toggling off an untouched file undoes the render, which is what
            # restores Sublime's own saved state as well as the text
            self.text, self.dirty = self.history.pop()

    def type_at_caret(self, typed):
        self.dirty = True
        point = self._sel[0].b
        self.text = self.text[:point] + typed + self.text[point:]
        self._sel.clear()
        self._sel.add(Region(point + len(typed)))
        inplace.handle_edit(self)

    def press_arrow(self, listener, forward, by="characters"):
        point = max(0, min(self._sel[0].b + (1 if forward else -1),
                           len(self.text)))
        self._sel.clear()
        self._sel.add(Region(point))
        listener.on_post_text_command(self, "move",
                                      {"by": by, "forward": forward})


section("in-place mode")
view = FakeView("سلام دنیا")
inplace.turn_on(view)
state = inplace.state_of(view)
check("the tab shows the rendering", view.text,
      shaper.to_visual_text("سلام دنیا"))
check("the real text is kept beside it", state.logical, "سلام دنیا")

view = FakeView("")
inplace.turn_on(view)
state = inplace.state_of(view)
for key in "سلام world":
    view.type_at_caret(key)
check("typing lands in the real text", state.logical, "سلام world")

check("undo steps back a word", (inplace._undo(view, state),
                                 state.logical)[1], "سلام ")
check("and again", (inplace._undo(view, state), state.logical)[1], "سلام")
check("redo returns it", (inplace._redo(view, state), state.logical)[1],
      "سلام ")

view = FakeView("سلام دنیا")
inplace.turn_on(view)
state = inplace.state_of(view)
state.caret = len(state.logical)
inplace._place_caret(view, state)
inplace.delete(view, state, False, True)
check("ctrl+backspace takes a whole word", state.logical, "سلام ")

listener = inplace.RtlInplaceListener()
view = FakeView("سلام دنیا")
inplace.turn_on(view)
state = inplace.state_of(view)
seen = [state.caret]
for _ in range(5):
    view.press_arrow(listener, True)
    seen.append(state.caret)
check("arrows walk the text", seen, [0, 1, 3, 4, 5, 6])

view = FakeView("سلام دنیا")
inplace.turn_on(view)
state = inplace.state_of(view)
inplace.turn_off(view)
check("toggling off restores the real text", view.text, "سلام دنیا")
check("no state is left behind", inplace.state_of(view), None)

section("the unsaved marker tracks the file, not the rendering")
view = FakeView("سلام دنیا")
inplace.turn_on(view)
state = inplace.state_of(view)
check("a clean file stays clean once rendered", view.is_dirty(), False)
view.type_at_caret("!")
check("a real edit shows as unsaved", view.is_dirty(), True)
inplace._undo(view, state)
check("undoing back to the saved text clears it", view.is_dirty(), False)

view = FakeView("سلام")
view.dirty = True
inplace.turn_on(view)
check("a file that was already modified stays so", view.is_dirty(), True)

section("auto-render reacts to text typed, not only text opened")
_sublime.load_settings = lambda name: {"rtl_auto_render": True}
inplace._states.clear()
inplace._auto_declined.clear()

plain = FakeView("def handler(): pass")
inplace.consider_auto(plain)
check("a file with no RTL is left alone", inplace.state_of(plain), None)

# the same view once right-to-left text is typed into it
plain.text = "def handler(): pass  # مقدار"
inplace.consider_auto(plain)
check("and rendered once such text appears",
      inplace.state_of(plain) is not None, True)

# a deliberate toggle off must outrank the setting
off = FakeView("سلام")
inplace.consider_auto(off)
check("auto-rendered on open", inplace.state_of(off) is not None, True)
inplace.turn_off(off)
inplace.consider_auto(off)
check("stays off after being turned off by hand",
      inplace.state_of(off), None)

_sublime.load_settings = lambda name: {}
inplace._states.clear()
inplace._auto_declined.clear()

section("the right-click entry earns its place; the palette entry is always there")


def context_visible(view):
    return inplace.RtlToggleContextCommand(view).is_visible()


def palette_visible(view):
    return inplace.RtlToggleCommand(view).is_visible()


code = FakeView("def handler(): pass")
check("hidden on a file with no RTL", context_visible(code), False)
check("but the palette still offers it", palette_visible(code), True)

rtl = FakeView("سلام دنیا")
check("shown on a file with RTL", context_visible(rtl), True)

inplace.turn_on(rtl)
check("and still shown once rendered, to turn it off",
      context_visible(rtl), True)
inplace.turn_off(rtl)

# has_rtl reads only the head of a buffer, so this is the case the always-on
# palette entry exists for: the feature is needed and cannot be detected
deep = FakeView("x" * shaper.RTL_SCAN_LIMIT + "سلام")
check("hidden when the RTL text is past the scanned head",
      context_visible(deep), False)
check("and the palette is how you reach it", palette_visible(deep), True)

inplace._states.clear()
inplace._auto_declined.clear()

section("copying yields the real characters")
for text, span, want in [
    ("سلام دنیا", (0, 4), "دنیا"),
    ("سلام دنیا", None, "سلام دنیا"),
    ("سلام world دنیا", (0, 4), "دنیا"),
    ("خط اول\nخط دوم", None, "خط اول\nخط دوم"),
]:
    visual = shaper.to_visual_text(text)
    start, end = span or (0, len(visual))
    got = shaper.visual_span_to_logical(text, start, end)
    check("select %r of %r" % (span or "all", text), got, want)
    check("  and no display forms in it",
          [c for c in got if 0xFB50 <= ord(c) <= 0xFEFF], [])

view = FakeView("سلام دنیا")
inplace.turn_on(view)
state = inplace.state_of(view)
listener_cb = inplace.RtlInplaceListener()
view._sel.clear()
view._sel.add(Region(0, 4))                  # the leftmost word on screen
ret = listener_cb.on_text_command(view, "copy", {})
check("copy is taken over", ret, ("rtl_inplace_noop", {}))
check("the clipboard holds the real text", _sublime.get_clipboard(), "دنیا")

view._sel.clear()
view._sel.add(Region(0, 4))
listener_cb.on_text_command(view, "cut", {})
check("cut removes it from the real text", state.logical, "سلام ")
check("and copies the real text", _sublime.get_clipboard(), "دنیا")

section("toggling off always restores the real text")
restored = FakeView(shaper.to_visual_text("سلام دنیا"))
restored._settings[inplace.INPLACE_FLAG] = True
restored._settings[inplace.SHADOW_SETTING] = "سلام دنیا"
restored.history = []                        # a restored session has no history
_sublime.windows = lambda: [types.SimpleNamespace(views=lambda: [restored])]
inplace._states.clear()
inplace.plugin_loaded()
inplace.turn_off(restored)
check("even with no undo history to fall back on",
      restored.text, "سلام دنیا")
_sublime.windows = lambda: []

section("a stale mark must not re-enable the mode")
_sublime.windows = lambda: [types.SimpleNamespace(views=lambda: [stale, genuine])]

# a view that was closed and restored: the flag lingers, the text is ordinary
stale = FakeView("سلام دنیا")
stale._settings[inplace.INPLACE_FLAG] = True
stale._settings[inplace.SHADOW_SETTING] = "something else entirely"

# a view that really was rendered when the plugin reloaded
genuine = FakeView(shaper.to_visual_text("سلام دنیا"))
genuine._settings[inplace.INPLACE_FLAG] = True
genuine._settings[inplace.SHADOW_SETTING] = "سلام دنیا"

inplace._states.clear()
inplace.plugin_loaded()
check("a stale mark is not adopted", inplace.state_of(stale), None)
check("and the mark is cleared",
      stale._settings.get(inplace.INPLACE_FLAG), None)
check("the buffer is left as it was", stale.text, "سلام دنیا")
check("a genuine rendering is adopted",
      inplace.state_of(genuine).logical, "سلام دنیا")

# closing must not leave the mark behind for a restored session
view = FakeView("سلام دنیا")
inplace.turn_on(view)
listener_close = inplace.RtlInplaceListener()
listener_close.on_pre_close(view)
check("closing restores the real text", view.text, "سلام دنیا")
check("closing clears the mark",
      view._settings.get(inplace.INPLACE_FLAG), None)

section("nothing display-only can reach disk")
view = FakeView("سلام world")
inplace.turn_on(view)
state = inplace.state_of(view)
listener.on_pre_save(view)
check("what is written is the real text", view.text, "سلام world")
check("with no presentation forms",
      [c for c in view.text if 0xFB50 <= ord(c) <= 0xFEFF], [])

# --------------------------------------------------------------------------

print("\n%d checks, %d failed" % (_count[0], len(_failures)))
for label, got, want in _failures:
    print("  FAILED %s: %r want %r" % (label, got, want))
sys.exit(1 if _failures else 0)
