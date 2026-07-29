"""Right-to-left rendering applied to a file's own tab, with no second view.

While this is on, the buffer holds the visual form and the real text lives in a
shadow beside it. Every edit is translated back into that shadow, and the buffer
is re-rendered from it, so the shadow is always the truth.

Undo is kept here too. The logical text no longer sits in a buffer of its own,
so Sublime's history would only be able to undo the rendering steps; this module
keeps its own stack of logical snapshots instead. It coalesces a run of typing
into one step, but it is coarser than Sublime's native history -- that is the
price of this mode; the split view in rtl_split.py keeps native undo.

The file on disk never sees the display form: on_pre_save swaps the real text in
first, and on_pre_close restores it so a restored session never comes back
holding presentation forms.
"""

import sublime
import sublime_plugin

from .rtl_shaper import (apply_visual_buffer_edit, buffer_logical_caret,
                         buffer_visual_caret, has_rtl, step_caret, to_logical,
                         to_visual_text, visual_span_logical_indices,
                         visual_span_to_logical)

# marks a view that is currently rendered; also lets a reload find it again
INPLACE_FLAG = "rtl_inplace"

# the real text, parked on the view so a plugin reload can recover it
SHADOW_SETTING = "rtl_shadow"

AUTO_SETTING = "rtl_auto_render"
MAX_SIZE_SETTING = "rtl_max_file_size"

STATUS_KEY = "rtl_mode"
STATUS_TEXT = "RTL view"

SETTINGS_FILE = "RTLAssistant.sublime-settings"

DEFAULT_MAX_BUFFER = 1024 * 1024
UNDO_LIMIT = 500

HISTORY_COMMANDS = ("undo", "redo", "redo_or_repeat",
                    "soft_undo", "soft_redo")
WORD_UNITS = ("words", "word_ends", "subwords", "subword_ends")

AUTO_DELAY_MS = 400

_states = {}        # view id -> _State
_auto_pending = {}  # view id -> latest scheduled auto-render check
_auto_declined = set()   # views the user turned off; never re-render those


class _State(object):
    """Everything known about one view being rendered."""

    def __init__(self, logical, caret):
        self.logical = logical
        self.caret = caret
        self.opened_with = logical      # to tell a pure toggle from a real edit
        self.rendered = ""              # the visual text we last wrote
        self.anchor = (0, 0)            # (visual offset, logical offset)
        self.undo = []                  # [(logical, caret)], oldest first
        self.redo = []
        self.coalesce = False           # may the next insert join the last step
        # the text as it stands on disk, or None when that is already unknown
        # because the buffer had unsaved edits before rendering started
        self.saved_logical = None
        self.scratch_before = False


def _all_text(view):
    return view.substr(sublime.Region(0, view.size()))


def consider_auto(view):
    """Render this view if the setting asks for it and the text warrants it."""
    if view.id() in _states or view.id() in _auto_declined:
        return
    if not setting(view, AUTO_SETTING, False):
        return
    if view.size() <= _max_buffer(view) and has_rtl(_all_text(view)):
        turn_on(view)


def _schedule_auto(view):
    """Re-check after a pause. Text becomes right-to-left partway through
    typing it, so checking only when a file opens misses everything written
    rather than loaded."""
    view_id = view.id()
    serial = _auto_pending.get(view_id, 0) + 1
    _auto_pending[view_id] = serial

    def run():
        if _auto_pending.get(view_id) != serial:
            return                      # superseded by a later keystroke
        _auto_pending.pop(view_id, None)
        if view.is_valid():
            consider_auto(view)

    sublime.set_timeout(run, AUTO_DELAY_MS)


def setting(view, key, default=None):
    """A view setting wins, so a project or a syntax can override; otherwise
    the package's own settings file, which is where users configure this."""
    value = view.settings().get(key)
    if value is not None:
        return value
    return sublime.load_settings(SETTINGS_FILE).get(key, default)


def _max_buffer(view):
    return setting(view, MAX_SIZE_SETTING, DEFAULT_MAX_BUFFER)


def _note(view, message):
    view.set_status("rtl_inplace", "RTL: " + message)
    sublime.set_timeout(lambda: view.erase_status("rtl_inplace"), 4000)


def state_of(view):
    return _states.get(view.id())


def plugin_loaded():
    # A reload drops the state but leaves rendered buffers on screen. The
    # shadow was parked on the view for exactly this case.
    for window in sublime.windows():
        for view in window.views():
            if not view.settings().get(INPLACE_FLAG):
                continue
            shadow = view.settings().get(SHADOW_SETTING)
            # Adopt only a view that really is showing the rendering of that
            # shadow. A stale flag -- left behind by a close, or brought back
            # with a restored session -- would otherwise re-enable the mode
            # silently, and the first edit would then rewrite the buffer from
            # text that no longer matches what is in it.
            if shadow is None or _all_text(view) != to_visual_text(shadow):
                view.settings().erase(INPLACE_FLAG)
                view.settings().erase(SHADOW_SETTING)
                continue
            state = _State(shadow, 0)
            state.rendered = _all_text(view)
            state.saved_logical = None if view.is_dirty() else shadow
            _states[view.id()] = state
            # say so straight away: the mode was previously invisible until
            # the first edit happened to render and set this
            view.set_status(STATUS_KEY, STATUS_TEXT)
            _place_caret(view, state)


# --------------------------------------------------------------------------
# turning it on and off
# --------------------------------------------------------------------------

def turn_on(view):
    if view.id() in _states or view.settings().get("rtl_source_id"):
        return False                    # already on, or this is a split mirror
    if view.size() > _max_buffer(view):
        _note(view, "file too large to render")
        return False
    caret = view.sel()[0].b if len(view.sel()) else 0
    state = _State(_all_text(view), caret)
    state.scratch_before = view.is_scratch()
    # Rendering rewrites the buffer, which Sublime counts as an edit. Whether
    # the *file* has unsaved changes is a question about the shadow, so track
    # that here and report it ourselves.
    state.saved_logical = None if view.is_dirty() else state.logical
    _states[view.id()] = state
    view.settings().set(INPLACE_FLAG, True)
    view.settings().set(SHADOW_SETTING, state.logical)
    _render(view, state)
    return True


def turn_off(view):
    state = _states.pop(view.id(), None)
    if state is None:
        return False
    # A deliberate toggle off outranks the auto setting; without this, the
    # next keystroke would turn it straight back on.
    _auto_declined.add(view.id())
    view.settings().erase(INPLACE_FLAG)
    view.settings().erase(SHADOW_SETTING)
    view.erase_status(STATUS_KEY)
    view.set_scratch(state.scratch_before)

    untouched = (state.logical == state.opened_with and not state.undo)
    if untouched:
        # Rendering was the only change this view has seen, so undoing it
        # restores the buffer *and* Sublime's saved/modified state. Replacing
        # the text instead would leave the file looking edited when nothing
        # about it changed.
        view.run_command("undo")
    if _all_text(view) != state.logical:
        # The undo above did nothing -- a view restored with a session has no
        # history to undo -- so put the real text back explicitly. Skipping
        # this leaves the buffer holding the rendering and the real text lost.
        view.run_command("rtl_inplace_replace", {"text": state.logical})
    view.sel().clear()
    view.sel().add(sublime.Region(min(state.caret, view.size())))
    view.show(view.sel()[0].b)
    return True


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def _render(view, state):
    visual = to_visual_text(state.logical)
    if _all_text(view) != visual:
        view.run_command("rtl_inplace_replace", {"text": visual})
    state.rendered = visual
    view.settings().set(SHADOW_SETTING, state.logical)
    view.set_status(STATUS_KEY, STATUS_TEXT)
    _update_dirty(view, state)
    _place_caret(view, state)


def _update_dirty(view, state):
    """Show the file as modified only when the real text differs from disk.

    Scratch suppresses Sublime's own flag, which here would only be reporting
    that the buffer holds a rendering. It is switched back off the moment there
    are genuine unsaved changes, so closing still prompts for them.
    """
    saved = state.saved_logical
    view.set_scratch(saved is not None and state.logical == saved)


def _place_caret(view, state):
    target = buffer_visual_caret(state.logical, state.caret)
    target = min(target, view.size())
    view.sel().clear()
    view.sel().add(sublime.Region(target))
    view.show(target)
    state.anchor = (target, state.caret)


def _caret_now(view, state):
    """The caret's logical offset: exact when it is where we put it."""
    if not len(view.sel()):
        return state.caret
    point = view.sel()[0].b
    if state.anchor[0] == point:
        return state.anchor[1]
    return buffer_logical_caret(state.logical, point)


# --------------------------------------------------------------------------
# history
# --------------------------------------------------------------------------

def _remember(state, coalescing=False):
    """Snapshot the text before it changes."""
    if coalescing and state.coalesce and state.undo:
        return                          # fold into the step already recorded
    state.undo.append((state.logical, state.caret))
    if len(state.undo) > UNDO_LIMIT:
        del state.undo[0]
    state.redo = []
    state.coalesce = coalescing


def _apply(view, state, logical, caret):
    state.logical = logical
    state.caret = max(0, min(caret, len(logical)))
    _render(view, state)


def _undo(view, state):
    if not state.undo:
        _note(view, "nothing to undo")
        return
    state.redo.append((state.logical, state.caret))
    logical, caret = state.undo.pop()
    state.coalesce = False
    _apply(view, state, logical, caret)


def _redo(view, state):
    if not state.redo:
        _note(view, "nothing to redo")
        return
    state.undo.append((state.logical, state.caret))
    logical, caret = state.redo.pop()
    state.coalesce = False
    _apply(view, state, logical, caret)


# --------------------------------------------------------------------------
# edits made in the rendered buffer
# --------------------------------------------------------------------------

def _diff(old, new):
    limit = min(len(old), len(new))
    prefix = 0
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    return prefix, old[prefix:len(old) - suffix], new[prefix:len(new) - suffix]


def handle_edit(view):
    state = _states.get(view.id())
    if state is None:
        return
    new_visual = _all_text(view)
    if new_visual == state.rendered:
        return                          # our own write

    if len(view.sel()) > 1:
        _note(view, "multiple cursors are not supported here")
        _render(view, state)
        return

    old_visual = state.rendered
    typed = _typed_at_caret(state, old_visual, new_visual)
    if typed is not None:
        logical, caret, inserted = typed
        # A run of ordinary typing is one undo step, but whitespace ends the
        # run, so undo steps back a word at a time rather than unwinding
        # everything typed since the view was opened.
        run_on = not any(ch.isspace() for ch in inserted)
    else:
        prefix, removed, inserted = _diff(old_visual, new_visual)
        logical, caret = apply_visual_buffer_edit(
            state.logical, old_visual, prefix, removed, inserted)
        run_on = False

    if logical is None:
        _note(view, "could not translate that edit")
        _render(view, state)
        return

    _remember(state, coalescing=run_on)
    _apply(view, state, logical, caret)


def _typed_at_caret(state, old_visual, new_visual):
    """Plain typing at the caret we placed -> (logical, caret, typed), else None.

    Where a left-to-right run meets a right-to-left one, one visual offset
    stands for two logical ones. The caret's own logical offset settles it, so
    a typed word does not scatter across the boundary.
    """
    at_visual, at_logical = state.anchor
    grew = len(new_visual) - len(old_visual)
    if grew <= 0 or at_logical > len(state.logical):
        return None
    if (new_visual[:at_visual] != old_visual[:at_visual]
            or new_visual[at_visual + grew:] != old_visual[at_visual:]):
        return None
    logical = to_logical(new_visual[at_visual:at_visual + grew])
    return (state.logical[:at_logical] + logical + state.logical[at_logical:],
            at_logical + len(logical), logical)


# --------------------------------------------------------------------------
# caret-relative operations, done on the text rather than the screen
# --------------------------------------------------------------------------

def _kind(ch, separators):
    if ch.isspace():
        return 'space'
    if ch in separators:
        return 'punct'
    return 'word'


def _word_edge(text, pos, forward, separators):
    """The word boundary Sublime would land on, computed on logical text."""
    n = len(text)
    i = pos
    if forward:
        while i < n and _kind(text[i], separators) == 'space':
            i += 1
        if i < n:
            kind = _kind(text[i], separators)
            while i < n and _kind(text[i], separators) == kind:
                i += 1
    else:
        while i > 0 and _kind(text[i - 1], separators) == 'space':
            i -= 1
        if i > 0:
            kind = _kind(text[i - 1], separators)
            while i > 0 and _kind(text[i - 1], separators) == kind:
                i -= 1
    return i


def _separators(view):
    return view.settings().get("word_separators") or ""


def delete(view, state, forward, by_word):
    point = _caret_now(view, state)
    if by_word:
        edge = _word_edge(state.logical, point, forward, _separators(view))
    else:
        edge = point + (1 if forward else -1)
    start, end = (point, edge) if forward else (edge, point)
    start = max(0, start)
    end = min(len(state.logical), end)
    if start >= end:
        return
    _remember(state)
    _apply(view, state, state.logical[:start] + state.logical[end:], start)


def move(view, state, forward, by_word, origin):
    point = origin
    if by_word:
        target = _word_edge(state.logical, point, forward, _separators(view))
    else:
        row = state.logical.count('\n', 0, point)
        line_start = state.logical.rfind('\n', 0, point) + 1
        line = state.logical.split('\n')[row]
        col = point - line_start
        stepped = step_caret(line, col, forward)
        target = (line_start + stepped if stepped != col
                  else point + (1 if forward else -1))
    state.caret = max(0, min(target, len(state.logical)))
    _place_caret(view, state)


def cut_span(view, state, vis_start, vis_end):
    """Remove what a visual selection covers, in logical terms."""
    doomed = set(visual_span_logical_indices(state.logical, vis_start, vis_end))
    if not doomed:
        return
    _remember(state)
    kept = "".join(ch for i, ch in enumerate(state.logical) if i not in doomed)
    _apply(view, state, kept, min(doomed))


def move_to(view, state, to, origin):
    point = origin
    if to == "bol" or to == "hardbol":
        target = state.logical.rfind('\n', 0, point) + 1
    elif to == "eol" or to == "hardeol":
        nxt = state.logical.find('\n', point)
        target = len(state.logical) if nxt < 0 else nxt
    elif to == "bof":
        target = 0
    elif to == "eof":
        target = len(state.logical)
    else:
        return False
    state.caret = target
    _place_caret(view, state)
    return True


def _follow_caret(view):
    state = _states.get(view.id())
    if state is None or not len(view.sel()):
        return
    if _all_text(view) != state.rendered:
        return                          # an edit is pending; its caret wins
    point = view.sel()[0].b
    if point == state.anchor[0]:
        return                          # exactly where we put it
    state.caret = buffer_logical_caret(state.logical, point)
    state.anchor = (point, state.caret)
    state.coalesce = False              # a caret jump ends the typing run


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

class RtlInplaceReplaceCommand(sublime_plugin.TextCommand):
    """Internal: the only writer this module has."""

    def run(self, edit, text):
        self.view.replace(edit, sublime.Region(0, self.view.size()), text)


class RtlToggleCommand(sublime_plugin.TextCommand):
    """Render this file in place, or put it back."""

    def run(self, edit):
        if not turn_off(self.view):
            turn_on(self.view)

    def is_checked(self):
        return self.view.id() in _states

    def is_enabled(self):
        return self.view.settings().get("rtl_source_id") is None


class RtlInplaceListener(sublime_plugin.EventListener):

    def on_load(self, view):
        consider_auto(view)

    def on_modified(self, view):
        if view.id() in _states:
            sublime.set_timeout(lambda: handle_edit(view), 0)
        else:
            # the text may have only just become right-to-left
            _schedule_auto(view)

    def on_text_command(self, view, name, args):
        state = _states.get(view.id())
        if state is None:
            return None
        args = args or {}

        if name in HISTORY_COMMANDS:
            forward = name in ("redo", "redo_or_repeat", "soft_redo")
            sublime.set_timeout(
                lambda: (_redo if forward else _undo)(view, state), 0)
            return ("rtl_inplace_noop", {})

        # Copy and cut must put the real characters on the clipboard. Left
        # alone they would copy the rendering, which is a display-only
        # encoding -- it would look right when pasted back here and be wrong
        # everywhere else.
        if name in ("copy", "cut"):
            picked = [r for r in view.sel() if not r.empty()]
            if len(picked) == 1:
                start, end = sorted((picked[0].a, picked[0].b))
                sublime.set_clipboard(
                    visual_span_to_logical(state.logical, start, end))
                if name == "cut":
                    sublime.set_timeout(
                        lambda: cut_span(view, state, start, end), 0)
                return ("rtl_inplace_noop", {})

        if name in ("left_delete", "right_delete") and _single_caret(view):
            forward = name == "right_delete"
            sublime.set_timeout(
                lambda: delete(view, state, forward, False), 0)
            return ("rtl_inplace_noop", {})

        if name == "delete_word" and _single_caret(view):
            forward = bool(args.get("forward"))
            sublime.set_timeout(lambda: delete(view, state, forward, True), 0)
            return ("rtl_inplace_noop", {})

        return None

    def on_post_text_command(self, view, name, args):
        """Caret movement is corrected after the fact, never replaced.

        Replacing the move command stops Sublime moving the caret, and if the
        replacement then fails to run the key does nothing at all. Letting the
        move happen and adjusting it afterwards means the caret has always
        moved, whatever else goes wrong.
        """
        state = _states.get(view.id())
        if state is None:
            return
        args = args or {}
        origin = state.anchor[1]        # where the caret was before the move
        if name == "move" and not args.get("extend"):
            by = args.get("by")
            if by == "characters" or by in WORD_UNITS:
                move(view, state, bool(args.get("forward")),
                     by in WORD_UNITS, origin)
        elif name == "move_to" and not args.get("extend"):
            if args.get("to") in ("bol", "eol", "hardbol", "hardeol",
                                  "bof", "eof"):
                move_to(view, state, args.get("to"), origin)

    def on_selection_modified(self, view):
        """Follow the caret when it is moved by a click or anything else.

        Deferred so it runs after on_post_text_command, which has already put
        the caret where it belongs and refreshed the anchor; this then sees a
        matching anchor and does nothing.
        """
        if view.id() in _states:
            sublime.set_timeout(lambda: _follow_caret(view), 0)

    def on_pre_save(self, view):
        state = _states.get(view.id())
        if state is None:
            return
        # what reaches disk is the real text, never the display form
        view.run_command("rtl_inplace_replace", {"text": state.logical})
        state.rendered = state.logical

    def on_post_save(self, view):
        state = _states.get(view.id())
        if state is not None:
            state.opened_with = state.logical
            state.saved_logical = state.logical      # now level with disk
            _render(view, state)

    def on_pre_close(self, view):
        # a restored session must not come back holding presentation forms
        # Unconditionally: a rendered view is marked scratch whenever its text
        # matches disk, so testing that here would skip the restore on exactly
        # the clean files this is meant to protect.
        state = _states.get(view.id())
        if state is not None:
            view.run_command("rtl_inplace_replace", {"text": state.logical})
        # clear the marks too, or a restored session comes back with the mode
        # turned on for a file the user never toggled
        view.settings().erase(INPLACE_FLAG)
        view.settings().erase(SHADOW_SETTING)
        _states.pop(view.id(), None)
        _auto_pending.pop(view.id(), None)
        _auto_declined.discard(view.id())


class RtlInplaceNoopCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        pass


def _single_caret(view):
    return len(view.sel()) == 1 and view.sel()[0].empty()
