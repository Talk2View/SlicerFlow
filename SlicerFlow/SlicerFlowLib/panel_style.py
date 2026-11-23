"""The module panel's shared looks: muted secondary text, the colour that marks a state
(running, busy, a problem), and the primary button in Talk2View's Teal. Colours come from
the widget's palette, or are picked for a light or a dark palette, so the panel reads the
same in Slicer's light and dark styles. They are read when a widget is styled, so a style
switched while Slicer runs takes effect the next time the module is opened.

Runs on Slicer's main thread.
"""

import os

import qt

#: Talk2View's brand colours (the brand book's core palette and accent ramps). Smoke on
#: Teal is about 8.4:1, safe for a button's label; white on Teal (about 2.1:1) is not.
TEAL = "#26C8B8"
SMOKE = "#021C25"
STONE = "#9AA4A8"
AMBER = "#F0A74A"
RUST = "#BB524E"
#: Text in a state's colour, (on a light palette, on a dark palette): the accent, or the
#: ramp step that keeps it readable (Amber +40 on light, Rust -40 on dark). "idle" text is
#: the muted text colour.
STATE_COLORS = {
    "ok": (TEAL, TEAL),
    "busy": ("#90642C", AMBER),
    "problem": (RUST, "#D69795"),
}
#: A state's dot: the accent itself, on either palette (a dot is not text).
DOT_COLORS = {"ok": TEAL, "busy": AMBER, "problem": RUST, "idle": STONE}
#: The brand typeface, bundled with the module (``Resources/Fonts``, SIL OFL 1.1).
BRAND_FAMILY = "Space Grotesk"


def is_dark(widget):
    return widget.palette.color(qt.QPalette.Window).lightness() < 128


def muted_color(widget):
    """The text colour mixed 45% toward the background. Qt's own disabled-text colour is
    plain black in some of Slicer's palettes, which mutes nothing."""
    palette = widget.palette
    text = palette.color(qt.QPalette.WindowText)
    back = palette.color(qt.QPalette.Window)

    def mix(a, b):
        return int(round(a * 0.55 + b * 0.45))

    return qt.QColor(mix(text.red(), back.red()), mix(text.green(), back.green()),
                     mix(text.blue(), back.blue()))


def state_color(widget, state):
    """The ``qt.QColor`` for ``state`` ("ok", "busy", "problem", or "idle")."""
    pair = STATE_COLORS.get(state)
    if pair is None:
        return muted_color(widget)
    return qt.QColor(pair[1] if is_dark(widget) else pair[0])


def dot_color(widget, state):
    """The ``qt.QColor`` of the status dot for ``state``."""
    return qt.QColor(DOT_COLORS.get(state, STONE))


def load_brand_fonts(folder):
    """Add the bundled Space Grotesk files to Qt's font database (once a process) and
    return the family name, or None when Qt refused them: the panel then keeps Slicer's
    font."""
    database = qt.QFontDatabase()
    if BRAND_FAMILY in database.families():
        return BRAND_FAMILY
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if name.endswith(".ttf"):
            qt.QFontDatabase.addApplicationFont(os.path.join(folder, name))
    return BRAND_FAMILY if BRAND_FAMILY in qt.QFontDatabase().families() else None


def brand_font(widget, family, point_delta=0, weight=None):
    """``widget``'s font in ``family`` (unchanged when it is None), ``point_delta`` points
    larger, at ``weight`` (a ``qt.QFont`` weight) when given."""
    font = qt.QFont(widget.font)
    if family:
        font.setFamily(family)
    if point_delta:
        font.setPointSize(font.pointSize() + point_delta)
    if weight is not None:
        font.setWeight(weight)
    return font


def set_text_color(label, color):
    label.setStyleSheet("color: {};".format(color.name()))


class FittedBody(qt.QWidget):
    """The module panel's content: as tall as its wrapped lines at its width, told to Slicer
    as a plain minimum height instead of a height-for-width.

    Slicer's module panel takes a module out of its scroll area with ``takeAt`` and never
    deletes that layout item (qSlicerModulePanel::removeModule). The leaked item stays the
    one Qt updates when the panel changes, so the item a module gets on its next visit is
    never told: its minimum and preferred size are measured afresh each time, but the
    height-for-width it caches stays the first one for good. A panel whose wrapped lines
    report a height-for-width is then given its old height after switching modules away
    and back, and the Tools section's lists are drawn over the lines under them."""

    def hasHeightForWidth(self):
        return False

    # A widget with a layout takes its sizes from the layout, as QWidget's own do.
    def minimumSizeHint(self):
        layout = self.layout()
        return self._fitted(layout, layout.totalMinimumSize()) if layout else qt.QSize(0, 0)

    def sizeHint(self):
        layout = self.layout()
        return self._fitted(layout, layout.totalSizeHint()) if layout else qt.QSize(0, 0)

    def resizeEvent(self, event):
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()  # a new width wraps the lines anew: a new height

    def _fitted(self, layout, size):
        if layout.hasHeightForWidth() and self.width > 0:
            size.setHeight(max(size.height(), layout.totalHeightForWidth(self.width)))
        return size


def muted_label(text=""):
    """A word-wrapped label in the muted text colour, for hints under a control."""
    label = qt.QLabel(text)
    label.wordWrap = True
    set_text_color(label, muted_color(label))
    return label


def primary_button_style(widget, family=None):
    """A stylesheet that makes a push button the panel's one main action, as the brand's
    buttons: Smoke on Teal, square corners, a little taller than its neighbours."""
    fill = qt.QColor(TEAL)
    font = "font-family: '{}'; ".format(family) if family else ""
    return (
        "QPushButton {{ background-color: {fill}; color: {text}; border: none; "
        "border-radius: 0px; padding: 8px 14px; {font}font-weight: 600; }}"
        "QPushButton:hover {{ background-color: {hover}; }}"
        "QPushButton:pressed {{ background-color: {pressed}; }}"
        "QPushButton:disabled {{ background-color: {off}; color: {text}; }}"
    ).format(
        fill=fill.name(), text=SMOKE, hover=fill.lighter(110).name(),
        pressed=fill.darker(110).name(), off=STONE, font=font,
    )
