"""An editable right-to-left view, beside the file it reflects.

Sublime has no BiDi and no Arabic shaping, and no API to change how buffer text
is drawn. So a second view holds the shaped, visually-ordered form of the same
buffer, and edits made there are carried back.

The source buffer stays the single source of truth. The mirror is never parsed
back into logical order -- that direction is ambiguous. Instead each edit is
diffed against the visual text we last generated, translated through the
permutation that produced it, and applied to the source; the mirror is then
regenerated from the source. Anything that cannot be translated confidently is
refused and the mirror resynced, so the source never receives a guess.

Nothing here writes to disk. Edits land in the source buffer and Sublime's
normal dirty flag and save behaviour apply, from either pane.
"""

import os

import sublime
import sublime_plugin

from .rtl_shaper import (apply_visual_edit, base_level, logical_caret,
                         step_caret, to_logical, to_visual_text, visual_caret,
                         visual_span_logical_indices, visual_span_to_logical)

# set on a mirror view; holds the id of the buffer it reflects
SOURCE_ID = "rtl_source_id"

# rendering is linear in buffer size, so refuse the pathological case outright
# rather than hang the editor on open
DEFAULT_MAX_BUFFER = 1024 * 1024
MAX_SIZE_SETTING = "rtl_max_file_size"
SETTINGS_FILE = "RTLAssistant.sublime-settings"

SYNC_DELAY_MS = 120

# the units "move" uses for ctrl+arrow and alt+arrow; all of them mean a
# boundary in the text, so they are resolved against the source
WORD_UNITS = ("words", "word_ends", "subwords", "subword_ends")

# every way undo or redo can be asked for. The edit history belongs to the
# source buffer, so all of them are forwarded there. ctrl+y is redo_or_repeat
# rather than redo, and missing it leaves redo dead on that key alone.
HISTORY_COMMANDS = ("undo", "redo", "redo_or_repeat",
                    "soft_undo", "soft_redo")

_mirrors = {}        # source view id -> mirror view id
_visual_text = {}    # mirror view id -> the visual text we last generated
_pending = {}        # source view id -> latest scheduled sync serial

# mirror view id -> (visual offset, logical offset) of the caret we last placed.
# Where a left-to-right run meets a right-to-left one, a single visual offset
# stands for two logical ones, and screen position alone cannot say which was
# meant. Remembering the logical offset the caret actually holds settles it, so
# a run of typing stays on one side of the boundary instead of alternating.
_anchor = {}


def plugin_loaded():
    for window in sublime.windows():
        for view in window.views():
            source_id = view.settings().get(SOURCE_ID)
            if source_id is None:
                continue
            _mirrors[source_id] = view.id()
            # A reload empties the module state but leaves the mirror on
            # screen holding the visual text we generated before it. Adopt
            # that as the baseline: an edit is understood by diffing against
            # it, so without one the next keystroke has nothing to compare
            # against and would be discarded.
            _visual_text[view.id()] = _all_text(view)
            source = _view_by_id(source_id)
            if source is not None:
                _sync_caret_to_mirror(source, view)


def _view_by_id(view_id):
    if view_id is None:
        return None
    view = sublime.View(view_id)
    return view if view.is_valid() else None


def _mirror_of(source):
    mirror_id = _mirrors.get(source.id())
    if mirror_id is None:
        return None
    mirror = _view_by_id(mirror_id)
    if mirror is None:
        del _mirrors[source.id()]
    return mirror


def _source_of(mirror):
    return _view_by_id(mirror.settings().get(SOURCE_ID))


def _all_text(view):
    return view.substr(sublime.Region(0, view.size()))


def _logical_offset(source, row, col):
    """Logical offset in the source for a caret sitting at mirror (row, col).

    Both ends of an embedded run land on the same two visual offsets, so the
    boundary is settled in favour of the line's own base direction. On an
    otherwise-English line that keeps a click just past `<html>` meaning just
    past `<html>`, rather than skidding to the far end of the Persian inside
    it; it also keeps the position before the closing tag reachable, which
    binding blindly to one side does not.
    """
    line_start = source.text_point(row, 0)
    line = source.substr(source.line(line_start))
    prefer = 'rtl' if base_level(line) else 'ltr'
    return line_start + logical_caret(line, col, prefer=prefer)


def _logical_caret_of(source, mirror):
    """The caret's logical offset in the source.

    Exact when the caret is still where we placed it; otherwise read back from
    the screen column, which is all there is to go on after a click.
    """
    caret = mirror.sel()[0].b
    anchor = _anchor.get(mirror.id())
    if anchor is not None and anchor[0] == caret:
        return anchor[1]
    row, col = mirror.rowcol(caret)
    return _logical_offset(source, row, col)


def _mirror_name(source):
    """Name the mirror after the file it reflects, carrying the source's
    unsaved marker so the tab reads like the real one."""
    path = source.file_name()
    label = os.path.basename(path) if path else (source.name() or "untitled")
    return "RTL: " + label + (" *" if source.is_dirty() else "")


def _retarget(source, mirror):
    """Point the mirror's save path at the file it reflects.

    Sublime already tries to save this view; it just has no path, which is why
    it asks for one. Giving it the source's path removes the dialog and sends
    the write to the right file. Safe only because on_pre_save swaps the real
    characters in first, so the display form is never what gets written.
    """
    path = source.file_name()
    if path and mirror.file_name() != path:
        mirror.retarget(path)


def _refresh_name(source, mirror):
    name = _mirror_name(source)
    if mirror.name() != name:
        mirror.set_name(name)


def _note(view, message):
    view.set_status("rtl_status", "RTL: " + message)
    sublime.set_timeout(lambda: view.erase_status("rtl_status"), 4000)


# --------------------------------------------------------------------------
# source -> mirror
# --------------------------------------------------------------------------

def _push_to_mirror(source, mirror):
    visual = to_visual_text(_all_text(source))
    if _all_text(mirror) != visual:
        mirror.run_command("rtl_split_replace", {"text": visual})
    _visual_text[mirror.id()] = visual
    _retarget(source, mirror)
    _refresh_name(source, mirror)


def _sync_caret_to_mirror(source, mirror):
    if not source.is_valid() or not mirror.is_valid() or not source.sel():
        return
    point = source.sel()[0].b
    row, col = source.rowcol(point)
    line = source.substr(source.line(point))
    target = mirror.text_point(row, visual_caret(line, col))
    mirror.sel().clear()
    mirror.sel().add(sublime.Region(target))
    mirror.show(target)
    _anchor[mirror.id()] = (target, point)


def _schedule_push(source):
    source_id = source.id()
    serial = _pending.get(source_id, 0) + 1
    _pending[source_id] = serial

    def run():
        if _pending.get(source_id) != serial:
            return                      # superseded by a later keystroke
        _pending.pop(source_id, None)
        current = _view_by_id(source_id)
        if current is None:
            return
        mirror = _mirror_of(current)
        if mirror is not None:
            _push_to_mirror(current, mirror)
            _sync_caret_to_mirror(current, mirror)

    sublime.set_timeout(run, SYNC_DELAY_MS)


# --------------------------------------------------------------------------
# mirror -> source
# --------------------------------------------------------------------------

def _diff(old, new):
    """-> (prefix_len, removed, inserted) for one contiguous change."""
    limit = min(len(old), len(new))
    prefix = 0
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    return prefix, old[prefix:len(old) - suffix], new[prefix:len(new) - suffix]


def _rowcol(text, offset):
    row = text.count("\n", 0, offset)
    line_start = text.rfind("\n", 0, offset) + 1
    return row, offset - line_start


def _translate(source, old_visual, prefix, removed, inserted):
    """Carry one visual-order change into the source buffer.

    Returns the logical caret offset, or None when the change cannot be
    translated and the caller should resync instead.
    """
    source_lines = _all_text(source).split("\n")
    visual_lines = old_visual.split("\n")
    if len(source_lines) != len(visual_lines):
        return None                     # the two views drifted apart

    first_row, first_col = _rowcol(old_visual, prefix)
    last_row, last_col = _rowcol(old_visual, prefix + len(removed))
    if last_row >= len(source_lines):
        return None

    logical = to_logical(inserted)

    if first_row == last_row:
        block, caret = apply_visual_edit(
            source_lines[first_row], first_col, last_col - first_col, logical)
    else:
        # the change spans a line break: each end contributes the part of its
        # own line that survived, and the lines between vanish entirely
        head, at = apply_visual_edit(
            source_lines[first_row], first_col,
            len(visual_lines[first_row]) - first_col, "")
        tail, _ = apply_visual_edit(source_lines[last_row], 0, last_col, "")
        block = head[:at] + logical + head[at:] + tail
        caret = at + len(logical)

    start = source.text_point(first_row, 0)
    end = source.text_point(last_row, 0) + len(source_lines[last_row])
    source.run_command("rtl_source_replace",
                       {"a": start, "b": end, "text": block})
    return start + caret


def _insert_at_anchor(source, mirror, old_visual, new_visual):
    """Typing at the caret we placed: insert where that caret logically is.

    Deriving the point from the visual offset instead would be ambiguous at a
    direction boundary and would scatter a typed word across it.

    The test is exact rather than a diff offset: a diff reports the longest
    common prefix, which runs past the caret whenever the typed character
    already sits there. Returns None when this is not plain typing at that
    caret, leaving the change to _translate.
    """
    anchor = _anchor.get(mirror.id())
    if anchor is None:
        return None
    at_visual, at_logical = anchor
    grew = len(new_visual) - len(old_visual)
    if grew <= 0 or at_logical > source.size():
        return None
    if (new_visual[:at_visual] != old_visual[:at_visual]
            or new_visual[at_visual + grew:] != old_visual[at_visual:]):
        return None                     # not a plain insertion at that caret

    logical = to_logical(new_visual[at_visual:at_visual + grew])
    source.run_command("rtl_source_replace",
                       {"a": at_logical, "b": at_logical, "text": logical})
    return at_logical + len(logical)


def _resync(source, mirror, message):
    _push_to_mirror(source, mirror)
    _sync_caret_to_mirror(source, mirror)
    _note(mirror, message)


def _handle_mirror_edit(mirror):
    if not mirror.is_valid():
        return
    source = _source_of(mirror)
    if source is None:
        return
    new_visual = _all_text(mirror)
    old_visual = _visual_text.get(mirror.id())
    if old_visual is None:
        # No baseline to diff against. Rebuild one from the source rather than
        # resyncing the mirror, which would throw away the edit that is sitting
        # in it right now.
        old_visual = to_visual_text(_all_text(source))
        _visual_text[mirror.id()] = old_visual
    if new_visual == old_visual:
        return                          # our own write, or nothing to do

    if len(mirror.sel()) > 1:
        _resync(source, mirror, "multiple cursors are not supported here")
        return

    try:
        caret = _insert_at_anchor(source, mirror, old_visual, new_visual)
        if caret is None:
            prefix, removed, inserted = _diff(old_visual, new_visual)
            caret = _translate(source, old_visual, prefix, removed, inserted)
    except Exception:
        # the source is untouched unless the replace itself ran, so falling
        # back to a resync is always safe
        caret = None

    if caret is None:
        _resync(source, mirror, "could not translate that edit -- resynced")
        return

    source.sel().clear()
    source.sel().add(sublime.Region(caret))
    _push_to_mirror(source, mirror)
    _sync_caret_to_mirror(source, mirror)


def _sync_caret_to_source(mirror):
    source = _source_of(mirror)
    if source is None or not mirror.is_valid() or not mirror.sel():
        return
    if _visual_text.get(mirror.id()) != _all_text(mirror):
        return          # an edit is still pending; its own caret wins
    point = mirror.sel()[0].b
    anchor = _anchor.get(mirror.id())
    if anchor is not None and anchor[0] == point:
        # The caret is exactly where we placed it, so its logical offset is
        # already known. Re-deriving it would round-trip through a boundary
        # that does not survive the trip, and would drag the caret away from
        # the position we just set.
        return
    row, col = mirror.rowcol(point)
    target = _logical_offset(source, row, col)
    source.sel().clear()
    source.sel().add(sublime.Region(target))
    source.show(target)
    # a deliberate click resolves the boundary once; typing then follows it
    _anchor[mirror.id()] = (point, target)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

class RtlSplitReplaceCommand(sublime_plugin.TextCommand):
    """Internal: rewrites the mirror from the source."""

    def run(self, edit, text):
        self.view.replace(edit, sublime.Region(0, self.view.size()), text)


class RtlSourceReplaceCommand(sublime_plugin.TextCommand):
    """Internal: the only writer the source buffer ever has."""

    def run(self, edit, a, b, text):
        self.view.replace(edit, sublime.Region(a, b), text)


class RtlNoopCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        pass


class RtlSplitOpenCommand(sublime_plugin.TextCommand):
    """Open (or refresh) the editable right-to-left view for this buffer."""

    def run(self, edit):
        window = self.view.window()
        if window is None:
            return
        if self.view.settings().get("rtl_inplace"):
            # rendering an already-rendered buffer would shape it twice
            self.view.run_command("rtl_toggle")
        limit = (self.view.settings().get(MAX_SIZE_SETTING)
                 or sublime.load_settings(SETTINGS_FILE).get(
                     MAX_SIZE_SETTING, DEFAULT_MAX_BUFFER))
        if self.view.size() > limit:
            _note(self.view, "buffer too large to render")
            return

        mirror = _mirror_of(self.view)
        if mirror is None:
            if window.num_groups() < 2:
                window.set_layout({
                    "cols": [0.0, 0.5, 1.0],
                    "rows": [0.0, 1.0],
                    "cells": [[0, 0, 1, 1], [1, 0, 2, 1]],
                })
            source_group = window.active_group()
            window.focus_group(1 if source_group == 0 else 0)
            mirror = window.new_file()
            window.focus_group(source_group)

            mirror.set_scratch(True)     # the source carries the dirty flag
            _refresh_name(self.view, mirror)
            mirror.settings().set(SOURCE_ID, self.view.id())
            mirror.settings().set("word_wrap", False)   # wrapping breaks order
            mirror.settings().set("spell_check", False)
            mirror.settings().set("draw_white_space", "none")
            _mirrors[self.view.id()] = mirror.id()

        _push_to_mirror(self.view, mirror)
        _sync_caret_to_mirror(self.view, mirror)

    def is_enabled(self):
        # Only a mirror is excluded. A view already rendered in place is fine:
        # run() turns that off first. Refusing here instead would hide this
        # from the command palette, which filters out disabled commands, and
        # the command would look as though it did not exist.
        return self.view.settings().get(SOURCE_ID) is None


class RtlSplitCloseCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        source = self.view
        if source.settings().get(SOURCE_ID) is not None:
            source = _source_of(self.view) or self.view
        mirror = _mirror_of(source)
        if mirror is not None:
            del _mirrors[source.id()]
            _visual_text.pop(mirror.id(), None)
            _anchor.pop(mirror.id(), None)
            sublime.set_timeout(mirror.close, 0)

    def is_enabled(self):
        return (self.view.settings().get(SOURCE_ID) is not None
                or _mirror_of(self.view) is not None)


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------

class RtlSplitListener(sublime_plugin.EventListener):

    def on_modified(self, view):
        if view.settings().get(SOURCE_ID) is not None:
            # translate promptly: a debounce here would let the mirror show
            # raw logical characters in visual positions
            sublime.set_timeout(lambda: _handle_mirror_edit(view), 0)
        elif _mirror_of(view) is not None:
            _schedule_push(view)

    def on_selection_modified(self, view):
        if view.settings().get(SOURCE_ID) is not None:
            sublime.set_timeout(lambda: _sync_caret_to_source(view), 0)
            return
        mirror = _mirror_of(view)
        if mirror is None:
            return
        window = view.window()
        active = window.active_view() if window is not None else None
        if active is not None and active.id() == mirror.id():
            return                      # the mirror is driving; do not fight it
        sublime.set_timeout(lambda: _sync_caret_to_mirror(view, mirror), 0)

    def on_text_command(self, view, name, args):
        if view.settings().get(SOURCE_ID) is None:
            return None

        # undo history belongs to the source buffer, not to the generated view
        if name in HISTORY_COMMANDS:
            source = _source_of(view)
            if source is not None:
                sublime.set_timeout(lambda: self._undo(source, view, name), 0)
                return ("rtl_noop", {})

        # Backspace and Delete are defined as "leftward" and "rightward", but
        # in a right-to-left run the previous character sits to the right. Do
        # them in logical terms instead, or backspace would delete the wrong
        # character -- and at the growth edge of an RTL line, nothing at all.
        # the clipboard must receive the real characters, not the rendering
        if name in ("copy", "cut"):
            source = _source_of(view)
            picked = [r for r in view.sel() if not r.empty()]
            if source is not None and len(picked) == 1:
                start, end = sorted((picked[0].a, picked[0].b))
                sublime.set_clipboard(
                    visual_span_to_logical(_all_text(source), start, end))
                if name == "cut":
                    sublime.set_timeout(
                        lambda: self._cut(view, source, start, end), 0)
                return ("rtl_noop", {})

        if name in ("left_delete", "right_delete"):
            if len(view.sel()) == 1 and view.sel()[0].empty():
                sublime.set_timeout(lambda: self._delete(view, name), 0)
                return ("rtl_noop", {})

        # Ctrl+Backspace and Ctrl+Delete, for the same reason: a word runs
        # right to left here, so the word before the caret is not the one to
        # the left on screen. Left alone they disagree with the single-step
        # deletes, which already work on the text.
        if name == "delete_word":
            if len(view.sel()) == 1 and view.sel()[0].empty():
                forward = bool((args or {}).get("forward"))
                sublime.set_timeout(
                    lambda: self._delete_word(view, forward), 0)
                return ("rtl_noop", {})
        return None

    def _delete_word(self, mirror, forward):
        """One word along the text.

        Sublime's own delete_word is run on the source, which holds the text in
        logical order, so its word rules apply unchanged and the direction
        follows the text rather than the screen. Whether a built-in command
        acts on a view that does not have focus is the thing being tested here;
        if nothing is deleted, say so rather than fail quietly.
        """
        source = _source_of(mirror)
        if source is None or not mirror.is_valid() or not mirror.sel():
            return
        point = _logical_caret_of(source, mirror)
        source.sel().clear()
        source.sel().add(sublime.Region(point))

        before = source.size()
        source.run_command("delete_word", {"forward": forward})

        at_edge = point >= source.size() if forward else point <= 0
        if source.size() == before and not at_edge:
            _note(mirror, "delete_word did not run on the unfocused source")

        _push_to_mirror(source, mirror)
        _sync_caret_to_mirror(source, mirror)

    def _cut(self, mirror, source, vis_start, vis_end):
        text = _all_text(source)
        doomed = set(visual_span_logical_indices(text, vis_start, vis_end))
        if not doomed:
            return
        low, high = min(doomed), max(doomed) + 1
        kept = "".join(ch for i, ch in enumerate(text[low:high], low)
                       if i not in doomed)
        source.run_command("rtl_source_replace",
                           {"a": low, "b": high, "text": kept})
        source.sel().clear()
        source.sel().add(sublime.Region(low))
        _push_to_mirror(source, mirror)
        _sync_caret_to_mirror(source, mirror)

    def _delete(self, mirror, name):
        source = _source_of(mirror)
        if source is None or not mirror.is_valid() or not mirror.sel():
            return
        # The caret's own logical offset, not one read back off the screen:
        # that round trip does not survive a direction boundary, and typing
        # Persian then one English letter then Backspace would eat the Persian.
        point = _logical_caret_of(source, mirror)

        if name == "left_delete":
            if point <= 0:
                return
            start, end, caret = point - 1, point, point - 1
        else:
            if point >= source.size():
                return
            start, end, caret = point, point + 1, point

        source.run_command("rtl_source_replace",
                           {"a": start, "b": end, "text": ""})
        source.sel().clear()
        source.sel().add(sublime.Region(caret))
        _push_to_mirror(source, mirror)
        _sync_caret_to_mirror(source, mirror)

    def on_post_text_command(self, view, name, args):
        """Turn a Left/Right keypress into a step along the text.

        Deliberately done after the fact rather than by replacing the command.
        Sublime has already moved the caret by one glyph on screen by the time
        this runs, so every early return below still leaves a caret that moved
        -- the key can never end up doing nothing.
        """
        if view.settings().get(SOURCE_ID) is None:
            return
        args = args or {}
        if name != "move" or args.get("extend"):
            return                      # extending moves stay visual

        by = args.get("by")
        anchor = _anchor.get(view.id())
        source = _source_of(view)
        if anchor is None or source is None or not view.is_valid():
            return

        if by == "characters":
            point = self._char_target(source, anchor[1],
                                      bool(args.get("forward")))
            if point is None:
                return
            source.sel().clear()
            source.sel().add(sublime.Region(point))
        elif by in WORD_UNITS:
            # Sublime's own word logic, run on the source where the text sits
            # in logical order, so a word boundary means what it does in the
            # file rather than what happens to be adjacent on screen.
            source.sel().clear()
            source.sel().add(sublime.Region(anchor[1]))
            source.run_command("move", args)
        else:
            return                      # line and page moves stay as-is
        _sync_caret_to_mirror(source, view)

    def _char_target(self, source, origin, forward):
        """One character along the text, or None when there is nowhere to go."""
        row, col = source.rowcol(origin)
        line = source.substr(source.line(origin))
        stepped = step_caret(line, col, forward)
        if stepped != col:
            point = source.text_point(row, 0) + stepped
        else:
            point = origin + (1 if forward else -1)     # over the line break
        return None if point < 0 or point > source.size() else point

    def _undo(self, source, mirror, name):
        source.run_command(name)
        _push_to_mirror(source, mirror)
        _sync_caret_to_mirror(source, mirror)

    def on_pre_save(self, view):
        """Last line of defence.

        If a save ever reaches the mirror itself -- by a route the routing
        above does not cover -- what lands on disk must still be the real text.
        Swap the display form out for the source's own characters first, so a
        file written from this view holds ordinary characters rather than
        presentation forms.
        """
        if view.settings().get(SOURCE_ID) is None:
            return
        source = _source_of(view)
        if source is None:
            return
        logical = _all_text(source)
        view.run_command("rtl_split_replace", {"text": logical})
        # Move the baseline with it. An edit is understood by diffing against
        # that baseline, and without this the swap would read as the user
        # replacing the whole buffer and be translated back into the source.
        _visual_text[view.id()] = logical

    def on_post_save(self, view):
        if view.settings().get(SOURCE_ID) is not None:
            source = _source_of(view)          # put the display form back
            if source is not None:
                _push_to_mirror(source, view)
                _sync_caret_to_mirror(source, view)
                # the file now holds exactly the source's characters, so clear
                # its unsaved marker rather than leave the two tabs disagreeing
                if source.is_dirty():
                    source.run_command("save")
            return
        # saving from the source pane clears its unsaved marker; carry that
        # across so both tabs agree
        mirror = _mirror_of(view)
        if mirror is not None:
            _refresh_name(view, mirror)

    def on_close(self, view):
        mirror_id = _mirrors.pop(view.id(), None)
        if mirror_id is not None:               # source closed: drop its mirror
            _visual_text.pop(mirror_id, None)
            _anchor.pop(mirror_id, None)
            mirror = _view_by_id(mirror_id)
            if mirror is not None:
                sublime.set_timeout(mirror.close, 0)
            return

        source_id = view.settings().get(SOURCE_ID)
        if source_id is not None and _mirrors.get(source_id) == view.id():
            del _mirrors[source_id]
        _visual_text.pop(view.id(), None)
        _anchor.pop(view.id(), None)
