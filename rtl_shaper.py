"""Right-to-left text -> what to draw when the editor cannot shape or reorder.

Sublime Text draws every codepoint as an isolated glyph, left to right: it has
neither the Unicode Bidirectional Algorithm nor Arabic contextual shaping. This
module does both jobs up front so the result *looks* right under such a
renderer:

  1. shaping   - letters -> Arabic Presentation Forms A/B (joined glyphs)
  2. reorder   - simplified UBA, logical order -> visual order

Shaping covers the Arabic script, which Arabic, Persian, Urdu, Sindhi and
Uyghur all use. Hebrew needs only the reordering half, having no contextual
forms of its own.

The output is display-only. Presentation forms are a legacy compatibility
encoding and not how this text is stored, so the result must never be written
back to a file.

No sublime imports here on purpose: this half is plain Python and can be run
and checked outside the editor.
"""

from functools import lru_cache

ZWNJ = '‌'
ZWJ = '‍'
LAM = 'ل'

_ISOLATED, _FINAL, _INITIAL, _MEDIAL = range(4)

# base letter -> (isolated, final, initial, medial); None where the form does
# not exist. A missing initial form is what makes a letter right-joining.
_FORMS = {
    'ء': ('ﺀ', None, None, None),              # ء
    'آ': ('ﺁ', 'ﺂ', None, None),          # آ
    'أ': ('ﺃ', 'ﺄ', None, None),          # أ
    'ؤ': ('ﺅ', 'ﺆ', None, None),          # ؤ
    'إ': ('ﺇ', 'ﺈ', None, None),          # إ
    'ئ': ('ﺉ', 'ﺊ', 'ﺋ', 'ﺌ'),  # ئ
    'ا': ('ﺍ', 'ﺎ', None, None),          # ا
    'ب': ('ﺏ', 'ﺐ', 'ﺑ', 'ﺒ'),  # ب
    'ة': ('ﺓ', 'ﺔ', None, None),          # ة
    'ت': ('ﺕ', 'ﺖ', 'ﺗ', 'ﺘ'),  # ت
    'ث': ('ﺙ', 'ﺚ', 'ﺛ', 'ﺜ'),  # ث
    'ج': ('ﺝ', 'ﺞ', 'ﺟ', 'ﺠ'),  # ج
    'ح': ('ﺡ', 'ﺢ', 'ﺣ', 'ﺤ'),  # ح
    'خ': ('ﺥ', 'ﺦ', 'ﺧ', 'ﺨ'),  # خ
    'د': ('ﺩ', 'ﺪ', None, None),          # د
    'ذ': ('ﺫ', 'ﺬ', None, None),          # ذ
    'ر': ('ﺭ', 'ﺮ', None, None),          # ر
    'ز': ('ﺯ', 'ﺰ', None, None),          # ز
    'س': ('ﺱ', 'ﺲ', 'ﺳ', 'ﺴ'),  # س
    'ش': ('ﺵ', 'ﺶ', 'ﺷ', 'ﺸ'),  # ش
    'ص': ('ﺹ', 'ﺺ', 'ﺻ', 'ﺼ'),  # ص
    'ض': ('ﺽ', 'ﺾ', 'ﺿ', 'ﻀ'),  # ض
    'ط': ('ﻁ', 'ﻂ', 'ﻃ', 'ﻄ'),  # ط
    'ظ': ('ﻅ', 'ﻆ', 'ﻇ', 'ﻈ'),  # ظ
    'ع': ('ﻉ', 'ﻊ', 'ﻋ', 'ﻌ'),  # ع
    'غ': ('ﻍ', 'ﻎ', 'ﻏ', 'ﻐ'),  # غ
    'ـ': ('ـ', 'ـ', 'ـ', 'ـ'),  # ـ tatweel
    'ف': ('ﻑ', 'ﻒ', 'ﻓ', 'ﻔ'),  # ف
    'ق': ('ﻕ', 'ﻖ', 'ﻗ', 'ﻘ'),  # ق
    'ك': ('ﻙ', 'ﻚ', 'ﻛ', 'ﻜ'),  # ك arabic kaf
    'ل': ('ﻝ', 'ﻞ', 'ﻟ', 'ﻠ'),  # ل
    'م': ('ﻡ', 'ﻢ', 'ﻣ', 'ﻤ'),  # م
    'ن': ('ﻥ', 'ﻦ', 'ﻧ', 'ﻨ'),  # ن
    'ه': ('ﻩ', 'ﻪ', 'ﻫ', 'ﻬ'),  # ه
    'و': ('ﻭ', 'ﻮ', None, None),          # و
    'ى': ('ﻯ', 'ﻰ', 'ﯨ', 'ﯩ'),  # ى
    'ي': ('ﻱ', 'ﻲ', 'ﻳ', 'ﻴ'),  # ي arabic yeh
    'پ': ('ﭖ', 'ﭗ', 'ﭘ', 'ﭙ'),  # پ
    'چ': ('ﭺ', 'ﭻ', 'ﭼ', 'ﭽ'),  # چ
    'ژ': ('ﮊ', 'ﮋ', None, None),          # ژ
    'ک': ('ﮎ', 'ﮏ', 'ﮐ', 'ﮑ'),  # ک persian keheh
    'گ': ('ﮒ', 'ﮓ', 'ﮔ', 'ﮕ'),  # گ
    'ھ': ('ﮪ', 'ﮫ', 'ﮬ', 'ﮭ'),  # ھ
    'ۀ': ('ﮤ', 'ﮥ', None, None),          # ۀ
    'ی': ('ﯼ', 'ﯽ', 'ﯾ', 'ﯿ'),  # ی persian yeh
    'ے': ('ﮮ', 'ﮯ', None, None),          # ے
}

# The other languages written in this script -- Urdu, Sindhi, Uyghur and the
# rest. Same four forms as above, taken from Unicode's own decomposition data
# rather than transcribed by hand. Adding these cannot disturb Arabic or
# Persian: a letter is looked up by itself, so entries for letters those two
# never use are never consulted.
#
# Some letters cannot be listed at all -- Pashto, Kurdish and Jawi have
# letters that Unicode never gave presentation forms, so there is nothing to
# map them to. Those stay unjoined; see the module docstring.
_FORMS.update({
    'ٱ': ('ﭐ', 'ﭑ', None, None),              # Alef Wasla
    'ٷ': ('ﯝ', None, None, None),             # U With Hamza Above
    'ٹ': ('ﭦ', 'ﭧ', 'ﭨ', 'ﭩ'),                # Tteh
    'ٺ': ('ﭞ', 'ﭟ', 'ﭠ', 'ﭡ'),                # Tteheh
    'ٻ': ('ﭒ', 'ﭓ', 'ﭔ', 'ﭕ'),                # Beeh
    'ٿ': ('ﭢ', 'ﭣ', 'ﭤ', 'ﭥ'),                # Teheh
    'ڀ': ('ﭚ', 'ﭛ', 'ﭜ', 'ﭝ'),                # Beheh
    'ڃ': ('ﭶ', 'ﭷ', 'ﭸ', 'ﭹ'),                # Nyeh
    'ڄ': ('ﭲ', 'ﭳ', 'ﭴ', 'ﭵ'),                # Dyeh
    'ڇ': ('ﭾ', 'ﭿ', 'ﮀ', 'ﮁ'),                # Tcheheh
    'ڈ': ('ﮈ', 'ﮉ', None, None),              # Ddal
    'ڌ': ('ﮄ', 'ﮅ', None, None),              # Dahal
    'ڍ': ('ﮂ', 'ﮃ', None, None),              # Ddahal
    'ڎ': ('ﮆ', 'ﮇ', None, None),              # Dul
    'ڑ': ('ﮌ', 'ﮍ', None, None),              # Rreh
    'ڤ': ('ﭪ', 'ﭫ', 'ﭬ', 'ﭭ'),                # Veh
    'ڦ': ('ﭮ', 'ﭯ', 'ﭰ', 'ﭱ'),                # Peheh
    'ڭ': ('ﯓ', 'ﯔ', 'ﯕ', 'ﯖ'),                # Ng
    'ڱ': ('ﮚ', 'ﮛ', 'ﮜ', 'ﮝ'),                # Ngoeh
    'ڳ': ('ﮖ', 'ﮗ', 'ﮘ', 'ﮙ'),                # Gueh
    'ں': ('ﮞ', 'ﮟ', None, None),              # Noon Ghunna
    'ڻ': ('ﮠ', 'ﮡ', 'ﮢ', 'ﮣ'),                # Rnoon
    'ہ': ('ﮦ', 'ﮧ', 'ﮨ', 'ﮩ'),                # Heh Goal
    'ۅ': ('ﯠ', 'ﯡ', None, None),              # Kirghiz Oe
    'ۆ': ('ﯙ', 'ﯚ', None, None),              # Oe
    'ۇ': ('ﯗ', 'ﯘ', None, None),              # U
    'ۈ': ('ﯛ', 'ﯜ', None, None),              # Yu
    'ۉ': ('ﯢ', 'ﯣ', None, None),              # Kirghiz Yu
    'ۋ': ('ﯞ', 'ﯟ', None, None),              # Ve
    'ې': ('ﯤ', 'ﯥ', 'ﯦ', 'ﯧ'),                # E
    'ۓ': ('ﮰ', 'ﮱ', None, None),              # Yeh Barree With Hamza Above
})

# ل + alef collapses into a single ligature glyph: (isolated, final)
_LAM_ALEF = {
    'آ': ('ﻵ', 'ﻶ'),
    'أ': ('ﻷ', 'ﻸ'),
    'إ': ('ﻹ', 'ﻺ'),
    'ا': ('ﻻ', 'ﻼ'),
}

# combining marks: they hang off the previous letter and never break a join
_MARK_RANGES = (
    (0x0610, 0x061A), (0x064B, 0x065F), (0x0670, 0x0670),
    (0x06D6, 0x06DC), (0x06DF, 0x06E4), (0x06E7, 0x06E8),
    (0x06EA, 0x06ED), (0x08D3, 0x08E1), (0x08E3, 0x08FF),
    (0xFE00, 0xFE0F), (0xFE20, 0xFE2F),
)


def _in_ranges(cp, ranges):
    for low, high in ranges:
        if low <= cp <= high:
            return True
    return False


# A line draws on a handful of distinct characters, so classifying a codepoint
# is worth doing once per run rather than once per occurrence. Both caches are
# bounded by the alphabet in play, not by input size.
_MARK_CACHE = {}
_CLASS_CACHE = {}


def _is_mark(ch):
    cached = _MARK_CACHE.get(ch)
    if cached is None:
        cached = _MARK_CACHE[ch] = _in_ranges(ord(ch), _MARK_RANGES)
    return cached


# --------------------------------------------------------------------------
# 1. contextual shaping
# --------------------------------------------------------------------------

def _next_letter_index(text, i):
    """First index at or after i that is not a combining mark."""
    n = len(text)
    while i < n and _is_mark(text[i]):
        i += 1
    return i


def _joins_backward(ch):
    """Can ch connect to the letter before it?"""
    if ch == ZWJ:
        return True
    forms = _FORMS.get(ch)
    return forms is not None and forms[_FINAL] is not None


def shape_with_spans(text):
    """-> (shaped, spans). spans[k] is the (start, end) slice of `text` that
    shaped character k stands for. A lam-alef ligature is one character
    covering two, which is why callers need a span and not an index."""
    out = []
    spans = []
    prev_forward = False   # can the previous letter connect to this one?
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if _is_mark(ch):
            out.append(ch)
            spans.append((i, i + 1))
            i += 1
            continue

        if ch == ZWNJ or ch == ZWJ:
            # zero width by definition: it only steers joining, never draws
            prev_forward = (ch == ZWJ)
            i += 1
            continue

        forms = _FORMS.get(ch)
        if forms is None:
            out.append(ch)
            spans.append((i, i + 1))
            prev_forward = False
            i += 1
            continue

        j = _next_letter_index(text, i + 1)
        nxt = text[j] if j < n else ''

        if ch == LAM and nxt in _LAM_ALEF:
            isolated, final = _LAM_ALEF[nxt]
            out.append(final if prev_forward else isolated)
            spans.append((i, j + 1))
            for k in range(i + 1, j):   # marks that sat between ل and the alef
                out.append(text[k])
                spans.append((k, k + 1))
            prev_forward = False        # an alef never connects forward
            i = j + 1
            continue

        nxt_backward = _joins_backward(nxt) if nxt else False
        if prev_forward and nxt_backward and forms[_MEDIAL]:
            out.append(forms[_MEDIAL])
        elif prev_forward and forms[_FINAL]:
            out.append(forms[_FINAL])
        elif nxt_backward and forms[_INITIAL]:
            out.append(forms[_INITIAL])
        else:
            out.append(forms[_ISOLATED])

        spans.append((i, i + 1))
        prev_forward = forms[_INITIAL] is not None
        i += 1

    return ''.join(out), spans


def shape(text):
    """Replace letters with the presentation form their context calls for."""
    return shape_with_spans(text)[0]


# presentation form -> the logical character(s) it stands for
_FROM_FORM = {}
for _base, _forms in _FORMS.items():
    for _form in _forms:
        if _form:
            _FROM_FORM[_form] = _base
for _alef, (_iso, _fin) in _LAM_ALEF.items():
    _FROM_FORM[_iso] = LAM + _alef
    _FROM_FORM[_fin] = LAM + _alef


def to_logical(text):
    """Presentation forms -> base letters. Anything else passes through, so
    freshly typed characters and Latin text survive untouched."""
    return ''.join(_FROM_FORM.get(ch, ch) for ch in text)


# --------------------------------------------------------------------------
# 2. bidirectional reordering (simplified UBA: one embedding level, no
#    explicit directional formatting codes, no bracket pairing)
# --------------------------------------------------------------------------

_L, _R, _AL = 'L', 'R', 'AL'
_EN, _AN = 'EN', 'AN'
_ES, _ET, _CS = 'ES', 'ET', 'CS'
_NSM, _BN, _B, _S, _WS, _ON = 'NSM', 'BN', 'B', 'S', 'WS', 'ON'

_ARABIC_RANGES = (
    (0x0620, 0x064A), (0x066E, 0x066F), (0x0671, 0x06D5),
    (0x06E5, 0x06E6), (0x06EE, 0x06EF), (0x06FA, 0x06FF),
    (0x0750, 0x077F), (0x08A0, 0x08BF),
    (0xFB50, 0xFDFF), (0xFE70, 0xFEFE),
)
_HEBREW_RANGES = ((0x0590, 0x05FF), (0xFB1D, 0xFB4F))

_EXPLICIT_CLASS = {
    0x060B: _AL, 0x060D: _AL, 0x061B: _AL, 0x061E: _AL, 0x061F: _AL,
    0x066D: _AL, 0x06D4: _AL,
    0x060C: _CS,
    0x066A: _ET,
    0x066B: _AN, 0x066C: _AN, 0x06DD: _AN,
    0x061C: _BN, 0x200C: _BN, 0x200D: _BN, 0xFEFF: _BN,
    0x200E: _L, 0x200F: _R,
}


def _bidi_class(ch):
    cached = _CLASS_CACHE.get(ch)
    if cached is None:
        cached = _CLASS_CACHE[ch] = _classify(ch)
    return cached


def _classify(ch):
    cp = ord(ch)
    explicit = _EXPLICIT_CLASS.get(cp)
    if explicit is not None:
        return explicit
    if 0x30 <= cp <= 0x39 or 0x06F0 <= cp <= 0x06F9:
        return _EN
    if 0x0660 <= cp <= 0x0669 or 0x0600 <= cp <= 0x0605:
        return _AN
    if _in_ranges(cp, _MARK_RANGES):
        return _NSM
    if _in_ranges(cp, _ARABIC_RANGES):
        return _AL
    if _in_ranges(cp, _HEBREW_RANGES):
        return _R
    if ch in '\n\r ':
        return _B
    if ch == '\t':
        return _S
    if ch.isspace():
        return _WS
    if cp in (0x2B, 0x2D, 0x2212):
        return _ES
    if ch in '#$%¢£¤¥°‰':
        return _ET
    if ch in ',.:/ ':
        return _CS
    if ch.isalpha():
        return _L
    return _ON


_BRACKET_PAIRS = {'(': ')', '[': ']', '{': '}'}
_CLOSERS = dict((close, open_) for open_, close in _BRACKET_PAIRS.items())

_MAX_PAIR_DEPTH = 63    # BD16 abandons pairing past this


def _strong_side(cls):
    if cls in (_R, _EN, _AN):
        return _R
    if cls == _L:
        return _L
    return None


def _bracket_pairs(text, cls):
    """BD16: matched (open, close) index pairs, in opening order."""
    stack = []
    pairs = []
    for i, ch in enumerate(text):
        if cls[i] != _ON:
            continue
        if ch in _BRACKET_PAIRS:
            if len(stack) == _MAX_PAIR_DEPTH:
                break
            stack.append((_BRACKET_PAIRS[ch], i))
        elif ch in _CLOSERS:
            for depth in range(len(stack) - 1, -1, -1):
                if stack[depth][0] == ch:
                    pairs.append((stack[depth][1], i))
                    del stack[depth:]
                    break
    pairs.sort()
    return pairs


def _resolve_bracket_pairs(text, cls, is_mark, base):
    """N0: a bracket pair takes one direction, so `foo(x)` cannot be torn in
    half when it sits inside a right-to-left line."""
    n = len(cls)
    embedding = _R if base % 2 else _L
    opposite = _L if base % 2 else _R

    for open_i, close_i in _bracket_pairs(text, cls):
        inside_opposite = False
        resolved = None
        for k in range(open_i + 1, close_i):
            side = _strong_side(cls[k])
            if side == embedding:
                resolved = embedding
                break
            if side == opposite:
                inside_opposite = True
        if resolved is None:
            if not inside_opposite:
                continue            # nothing strong inside: leave it to N1/N2
            before = None
            for k in range(open_i - 1, -1, -1):
                before = _strong_side(cls[k])
                if before is not None:
                    break
            resolved = opposite if before == opposite else embedding

        for i in (open_i, close_i):
            cls[i] = resolved
            k = i + 1
            while k < n and is_mark[k]:     # marks follow the bracket they hang on
                cls[k] = resolved
                k += 1


def _resolve_levels(text, classes, base):
    n = len(classes)
    cls = [_ON if c == _BN else c for c in classes]
    outer = _R if base % 2 else _L

    # W1: a combining mark inherits the class of what it sits on.
    prev = outer
    for i in range(n):
        if cls[i] == _NSM:
            cls[i] = prev
        else:
            prev = cls[i]

    # W2: european digits become arabic digits after arabic script.
    strong = outer
    for i in range(n):
        if cls[i] in (_L, _R, _AL):
            strong = cls[i]
        elif cls[i] == _EN and strong == _AL:
            cls[i] = _AN

    # W3
    for i in range(n):
        if cls[i] == _AL:
            cls[i] = _R

    # W4: a lone separator between two like numbers joins them.
    for i in range(1, n - 1):
        if cls[i] == _ES and cls[i - 1] == _EN and cls[i + 1] == _EN:
            cls[i] = _EN
        elif cls[i] == _CS and cls[i - 1] == cls[i + 1] and cls[i - 1] in (_EN, _AN):
            cls[i] = cls[i - 1]

    # W5: a run of terminators touching european digits joins them.
    i = 0
    while i < n:
        if cls[i] == _ET:
            j = i
            while j < n and cls[j] == _ET:
                j += 1
            before = cls[i - 1] if i > 0 else None
            after = cls[j] if j < n else None
            if before == _EN or after == _EN:
                for k in range(i, j):
                    cls[k] = _EN
            i = j
        else:
            i += 1

    # W6
    for i in range(n):
        if cls[i] in (_ET, _ES, _CS):
            cls[i] = _ON

    # W7
    strong = outer
    for i in range(n):
        if cls[i] in (_L, _R):
            strong = cls[i]
        elif cls[i] == _EN and strong == _L:
            cls[i] = _L

    # N0: bracket pairs first, so a pair resolves as a unit.
    _resolve_bracket_pairs(text, cls, [c == _NSM for c in classes], base)

    # N1/N2: remaining neutrals follow their neighbours when both agree, else
    # the paragraph direction.
    i = 0
    while i < n:
        if cls[i] in (_ON, _WS, _S, _B):
            j = i
            while j < n and cls[j] in (_ON, _WS, _S, _B):
                j += 1
            before = _strong_side(cls[i - 1]) if i > 0 else outer
            after = _strong_side(cls[j]) if j < n else outer
            fill = before if (before is not None and before == after) else outer
            for k in range(i, j):
                cls[k] = fill
            i = j
        else:
            i += 1

    # I1/I2: turn resolved classes into embedding levels.
    levels = []
    for c in cls:
        if base % 2 == 0:
            if c == _R:
                levels.append(base + 1)
            elif c in (_EN, _AN):
                levels.append(base + 2)
            else:
                levels.append(base)
        else:
            if c in (_L, _EN, _AN):
                levels.append(base + 1)
            else:
                levels.append(base)

    # L1: separators, and any whitespace trailing them or the line, snap back
    # to the paragraph level so a line never ends with stranded spaces.
    for i in range(n):
        if classes[i] in (_B, _S):
            levels[i] = base
            j = i - 1
            while j >= 0 and classes[j] in (_WS, _BN):
                levels[j] = base
                j -= 1
    for i in range(n - 1, -1, -1):
        if classes[i] in (_WS, _S, _B, _BN):
            levels[i] = base
        else:
            break

    return levels


_MIRRORED = {
    '(': ')', ')': '(', '[': ']', ']': '[', '{': '}', '}': '{',
    '<': '>', '>': '<', '«': '»', '»': '«',
    '‹': '›', '›': '‹',
}


def _visual_order(text, levels, classes):
    """L2 -> the permutation mapping visual position to index in `text`."""
    n = len(text)
    if n == 0:
        return []
    order = list(range(n))
    run_levels = list(levels)
    highest = max(run_levels)
    odd = [lvl for lvl in run_levels if lvl % 2]
    lowest_odd = min(odd) if odd else highest + 1

    # L2: reverse each contiguous run at every level, deepest first.
    for level in range(highest, lowest_odd - 1, -1):
        i = 0
        while i < n:
            if run_levels[i] >= level:
                j = i
                while j < n and run_levels[j] >= level:
                    j += 1
                order[i:j] = order[i:j][::-1]
                run_levels[i:j] = run_levels[i:j][::-1]
                i = j
            else:
                i += 1

    # Reversing put each combining mark in front of its letter, where a naive
    # renderer would stack it onto the neighbour instead. Flip every
    # mark-group back so a mark still trails the letter it belongs to.
    i = 0
    while i < n:
        if levels[order[i]] % 2 and classes[order[i]] == _NSM:
            j = i
            while j < n and classes[order[j]] == _NSM and levels[order[j]] % 2:
                j += 1
            if j < n and levels[order[j]] % 2:
                j += 1                              # take in the base letter
                order[i:j] = order[i:j][::-1]
            i = j
        else:
            i += 1

    return order


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def base_level(text, default=0):
    """0 for a left-to-right line, 1 for a right-to-left one."""
    for ch in text:
        cls = _bidi_class(ch)
        if cls == _L:
            return 0
        if cls in (_R, _AL):
            return 1
    return default


def _is_plain(line, direction):
    """True when nothing in the line can shape or reorder, so it already is
    its own visual form."""
    return direction != 'rtl' and max(line) < '֐'


def _compose(line, direction):
    """-> (visual, spans, levels), every list indexed by visual position."""
    shaped, shaped_spans = shape_with_spans(line)
    if direction == 'rtl':
        base = 1
    elif direction == 'ltr':
        base = 0
    else:
        base = base_level(shaped)
    classes = [_bidi_class(ch) for ch in shaped]
    levels = _resolve_levels(shaped, classes, base)
    order = _visual_order(shaped, levels, classes)
    visual = ''.join(
        _MIRRORED.get(shaped[k], shaped[k]) if levels[k] % 2 else shaped[k]
        for k in order
    )
    return visual, [shaped_spans[k] for k in order], [levels[k] for k in order]


@lru_cache(maxsize=32768)
def to_visual(line, direction='auto'):
    """One logical line -> what a shaping-less renderer must draw for it.

    Cached per line: an edit touches one line, so re-rendering a buffer costs
    a dict lookup for every line the user did not just type in.
    """
    if not line or _is_plain(line, direction):
        return line
    return _compose(line, direction)[0]


@lru_cache(maxsize=256)
def render(line, direction='auto'):
    """As to_visual, but also returns, per visual position, the logical span it
    came from and its embedding level. That is everything needed to carry an
    edit made in visual order back to logical order.

    Cached far smaller than to_visual: only lines being edited need it.
    """
    if not line or _is_plain(line, direction):
        return line, [(i, i + 1) for i in range(len(line))], [0] * len(line)
    return _compose(line, direction)


def _insert_point(spans, levels, visual_len, vis_pos):
    """Which logical offset text typed at visual position vis_pos belongs at.

    In a right-to-left run the two sides swap: arriving at the left edge of a
    glyph on screen means arriving after it in logical order.
    """
    if visual_len == 0:
        return 0
    if vis_pos >= visual_len:
        last = visual_len - 1
        return spans[last][0] if levels[last] % 2 else spans[last][1]
    return spans[vis_pos][1] if levels[vis_pos] % 2 else spans[vis_pos][0]


def apply_visual_edit(line, vis_pos, vis_delete, inserted, direction='auto'):
    """An edit expressed in visual order -> (new logical line, logical caret).

    Never inverts the bidi algorithm: the permutation that produced the visual
    form is still known, so the edit is carried back through it. A contiguous
    visual selection may cover a scattered set of logical characters, which is
    why deletion works on a set rather than a slice.
    """
    visual, spans, levels = render(line, direction)

    doomed = set()
    for v in range(max(vis_pos, 0), min(vis_pos + vis_delete, len(visual))):
        start, end = spans[v]
        doomed.update(range(start, end))

    if doomed:
        at = min(doomed)                    # replace where the deletion began
    else:
        at = _insert_point(spans, levels, len(visual), vis_pos)

    kept = ''.join(ch for i, ch in enumerate(line) if i not in doomed)
    at -= sum(1 for i in doomed if i < at)
    return kept[:at] + inserted + kept[at:], at + len(inserted)


def caret_direction(line, logical_pos, direction='auto'):
    """Which run the caret sits in: 'ltr' or 'rtl'.

    Taken from the nearest strong character behind the caret, then ahead of it.
    Spaces and punctuation carry no direction of their own and are skipped, so
    a space between English and Persian does not drag the caret back into the
    Persian run. Typing therefore switches direction on the first real letter
    of the other script, and stays there.
    """
    for i in range(min(logical_pos, len(line)) - 1, -1, -1):
        cls = _bidi_class(line[i])
        if cls == _L:
            return 'ltr'
        if cls in (_R, _AL):
            return 'rtl'
    for i in range(max(logical_pos, 0), len(line)):
        cls = _bidi_class(line[i])
        if cls == _L:
            return 'ltr'
        if cls in (_R, _AL):
            return 'rtl'
    if direction in ('ltr', 'rtl'):
        return direction
    return 'rtl' if base_level(line) else 'ltr'


def logical_caret(line, vis_pos, direction='auto', prefer=None):
    """Visual caret offset -> the logical offset it stands for.

    A caret on a run boundary touches two glyphs of opposing direction and so
    stands for two logical offsets; `prefer` says which run it belongs to.
    """
    visual, spans, levels = render(line, direction)
    if not visual:
        return 0

    options = []                                    # (is_rtl, logical offset)
    if 0 <= vis_pos < len(visual):                  # the glyph to the right
        start, end = spans[vis_pos]
        rtl = levels[vis_pos] % 2 == 1
        options.append((rtl, end if rtl else start))
    if 0 < vis_pos <= len(visual):                  # the glyph to the left
        left = vis_pos - 1
        start, end = spans[left]
        rtl = levels[left] % 2 == 1
        options.append((rtl, start if rtl else end))

    if prefer is not None:
        for is_rtl, offset in options:
            if is_rtl == (prefer == 'rtl'):
                return offset
    return options[0][1] if options else 0


def visual_caret(line, logical_pos, direction='auto', prefer=None):
    """Logical caret offset -> the visual offset that renders it.

    The mirror of logical_caret: on a run boundary two visual offsets render
    the same logical one, and `prefer` picks between them, defaulting to the
    run the surrounding strong characters put the caret in.
    """
    visual, spans, levels = render(line, direction)
    if not visual:
        return 0
    if prefer is None:
        prefer = caret_direction(line, logical_pos, direction)
    want_rtl = (prefer == 'rtl')

    options = []                                    # (is_rtl, visual offset)
    for v, (start, end) in enumerate(spans):
        rtl = levels[v] % 2 == 1
        if start <= logical_pos < end:              # caret sits before it
            options.append((rtl, v + 1 if rtl else v))
        if end == logical_pos:                      # caret sits after it
            options.append((rtl, v if rtl else v + 1))

    for is_rtl, offset in options:
        if is_rtl == want_rtl:
            return offset
    if options:
        return options[0][1]
    last = max(range(len(spans)), key=lambda v: spans[v][1])
    return last if levels[last] % 2 else last + 1


def step_caret(line, logical_pos, forward, direction='auto'):
    """The next logical offset whose caret lands somewhere new on screen.

    A ligature such as لا is a single glyph standing for two characters, so the
    offset between them has nowhere to be drawn: stepping onto it would look
    like the key did nothing. Skip on to an offset that does move.
    """
    here = visual_caret(line, logical_pos, direction)
    pos = logical_pos
    step = 1 if forward else -1
    while 0 <= pos + step <= len(line):
        pos += step
        if visual_caret(line, pos, direction) != here:
            break
    return pos


RTL_SCAN_LIMIT = 65536


def has_rtl(text, limit=RTL_SCAN_LIMIT):
    """Whether the text contains any right-to-left letter.

    Only the head of a large buffer is examined; this decides whether a file is
    worth rendering at all, and a file with such text in it has some near the
    top in every realistic case.
    """
    for ch in text[:limit]:
        if _bidi_class(ch) in (_R, _AL):
            return True
    return False


def _text_rowcol(text, offset):
    row = text.count('\n', 0, offset)
    return row, offset - (text.rfind('\n', 0, offset) + 1)


def _row_offset(lines, row):
    return sum(len(line) + 1 for line in lines[:row])


def apply_visual_buffer_edit(text, old_visual, prefix, removed, inserted,
                             direction='auto'):
    """Whole-buffer counterpart of apply_visual_edit.

    Takes one contiguous change expressed against `old_visual` -- the visual
    text the edit was made on -- and returns the logical text it means, plus
    where the caret ends up. Returns (None, None) when the change cannot be
    translated, so the caller can fall back rather than guess.
    """
    lines = text.split('\n')
    visual_lines = old_visual.split('\n')
    if len(lines) != len(visual_lines):
        return None, None               # the two have drifted apart

    first_row, first_col = _text_rowcol(old_visual, prefix)
    last_row, last_col = _text_rowcol(old_visual, prefix + len(removed))
    if last_row >= len(lines):
        return None, None

    logical = to_logical(inserted)

    if first_row == last_row:
        block, caret = apply_visual_edit(
            lines[first_row], first_col, last_col - first_col, logical,
            direction)
    else:
        # the change spans a line break: each end keeps the part of its own
        # line that survived, and the lines between vanish entirely
        head, at = apply_visual_edit(
            lines[first_row], first_col,
            len(visual_lines[first_row]) - first_col, "", direction)
        tail, _ = apply_visual_edit(lines[last_row], 0, last_col, "",
                                    direction)
        block = head[:at] + logical + head[at:] + tail
        caret = at + len(logical)

    start = _row_offset(lines, first_row)
    end = _row_offset(lines, last_row) + len(lines[last_row])
    return text[:start] + block + text[end:], start + caret


def visual_span_logical_indices(text, vis_start, vis_end, direction='auto'):
    """Indices into `text` that a visual range covers, in reading order.

    A range that looks contiguous on screen can cover a scattered set of the
    underlying characters wherever direction changes, so this returns a sorted
    set of indices rather than a slice. Copying or cutting a selection has to
    go through here, or the clipboard would receive presentation forms.
    """
    lines = text.split('\n')
    picked = set()
    vis_pos = 0
    log_pos = 0
    for index, line in enumerate(lines):
        visual, spans, _ = render(line, direction)
        for v in range(len(visual)):
            if vis_start <= vis_pos + v < vis_end:
                start, end = spans[v]
                picked.update(range(log_pos + start, log_pos + end))
        vis_pos += len(visual)
        log_pos += len(line)
        if index < len(lines) - 1:              # the line break between them
            if vis_start <= vis_pos < vis_end:
                picked.add(log_pos)
            vis_pos += 1
            log_pos += 1
    return sorted(picked)


def visual_span_to_logical(text, vis_start, vis_end, direction='auto'):
    """The real characters a visual selection covers, in reading order."""
    return ''.join(text[i] for i in
                   visual_span_logical_indices(text, vis_start, vis_end,
                                               direction))


def buffer_visual_caret(text, logical_pos, direction='auto'):
    """Logical offset in a whole buffer -> the visual offset that renders it."""
    row, col = _text_rowcol(text, logical_pos)
    lines = text.split('\n')
    if row >= len(lines):
        return len(to_visual_text(text, direction))
    visual_lines = [to_visual(line, direction) for line in lines]
    return (_row_offset(visual_lines, row)
            + visual_caret(lines[row], col, direction))


def buffer_logical_caret(text, vis_pos, direction='auto'):
    """Visual offset in a whole rendered buffer -> the logical offset it means."""
    lines = text.split('\n')
    visual_lines = [to_visual(line, direction) for line in lines]
    row, col = _text_rowcol('\n'.join(visual_lines), vis_pos)
    if row >= len(lines):
        return len(text)
    line = lines[row]
    prefer = 'rtl' if base_level(line) else 'ltr'
    return _row_offset(lines, row) + logical_caret(line, col, direction, prefer)


def to_visual_text(text, direction='auto'):
    """Whole buffer. Each line is its own paragraph, so line count is kept
    one-to-one with the source and line N still means line N."""
    return '\n'.join(to_visual(line, direction) for line in text.split('\n'))
