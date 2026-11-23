"""The module panel's Tools section: every pack the assistant's tools come in, in one list,
each with an on/off box and its status: SlicerFlow's own packs (``tools.BUILTIN_PACKS``)
first, then the user's. They all live in the user's tools folder, SlicerFlow's as
``slicerflow_<pack>.py``, written and kept up to date by ``user_tools.sync_builtins`` on
every refresh, except one the user edited: that is marked Edited and is the user's from then
on, until Restore original puts SlicerFlow's version back.

Below the list, the selected pack: its tools (each one's name, the whole of what it does,
whether it asks first), read from the source by ``user_tools.read_pack``, a line for
an edited pack or a newer SlicerFlow version of one, and the buttons to open the file in a text editor (never run it), show it in its folder, and restore
SlicerFlow's version. Then New, Import and Open folder, an Install button for a selected pack
that needs a package, and a restart bar when the running chat has other packs on than the
ones it loaded.

Switching one of SlicerFlow's own packs asks nothing: it is SlicerFlow's code, unchanged.
Turning on an edited one, or a pack of the user's, or importing one, asks first: a pack is
Python that runs with full access to Slicer and the user's files every time the chat
starts. Nothing here runs a pack's code. An "on" pack's status comes from the running chat
(``Runtime.packs``), and "Needs …" comes from its source (``user_tools.check_requires``).

Runs on Slicer's main thread.
"""

import importlib
import os
import subprocess
from dataclasses import dataclass, field
from typing import Optional

import ctk
import qt
import slicer

from . import lifecycle, panel_style, paths, user_tools

TRUST_TEXT = (
    "{name} is Python code. Once on, it runs inside 3D Slicer every time the chat starts, "
    "with full access to your scene and your files. Turn it on only if you wrote it or "
    "you trust whoever did."
)
EDITED_TEXT = (
    "{file} is not the version SlicerFlow wrote, so it counts as your own code. " + TRUST_TEXT
)
RESTORE_TEXT = (
    "Replace {file} with SlicerFlow's version? Your edited file is kept beside it as "
    "{file}.bak, replacing any earlier {file}.bak."
)
NEWER_TEXT = "SlicerFlow has a newer version of this pack: Restore original puts it in."
#: What the Pack column says after a pack's title, by kind.
KIND_LABELS = {"builtin": "SlicerFlow", "edited": "Edited", "user": "Yours"}
EDITED_DETAIL = "Edited: your version of SlicerFlow's pack, which SlicerFlow leaves alone."
#: Each list grows with its rows up to this many, then scrolls.
MAX_ROWS = 10
#: ``user_tools.pack_status`` kinds and the colour each one's status text gets.
KIND_STATES = {"error": "problem", "needs": "busy", "off": "idle", "pending": "idle"}
NO_TOOLS_TEXT = "No tools are on: the assistant can only answer in text."


class _WrappingTree(qt.QTreeWidget):
    """A tree that calls ``onWidth`` after its width changes: its wrapped rows need new
    heights then. (A stretched last column says nothing when it widens or narrows.)"""

    onWidth = None

    def resizeEvent(self, event):
        qt.QTreeWidget.resizeEvent(self, event)
        if self.onWidth is not None and event.size().width() != event.oldSize().width():
            qt.QTimer.singleShot(0, self.onWidth)  # once the header has its new widths


class _TopDelegate(qt.QStyledItemDelegate):
    """Draws an item's icon at the top of its row, by its name, not halfway down a row a
    long description made tall."""

    def initStyleOption(self, option, index):
        qt.QStyledItemDelegate.initStyleOption(self, option, index)
        option.decorationAlignment = qt.Qt.AlignLeft | qt.Qt.AlignTop


@dataclass
class Row:
    """One row of the pack list. ``kind`` is "builtin" (SlicerFlow's pack, as written),
    "edited" (SlicerFlow's pack, changed by the user) or "user" (the user's own). ``key`` is
    what ``tools.json`` holds for it: the pack's name in ``builtin_off`` for "builtin", the
    file's stem in ``enabled`` otherwise. ``name`` is the built-in pack's module name ("" for
    the user's own)."""

    kind: str
    key: str
    stem: str
    title: str
    path: str
    on: bool
    info: user_tools.PackInfo
    name: str = ""
    missing: dict = field(default_factory=dict)
    problem: Optional[str] = None
    newer: bool = False


class ToolsSection:
    def __init__(self, layout, restart):
        self._restart = restart
        self._rows = []
        self._refreshing = False
        # True while a handler waits on a modal dialog. The dialog runs Qt's event loop, so
        # the chat panel's status changes call refresh() in the middle of the handler;
        # clearing the list then would delete the item whose itemChanged is on the stack.
        self._handling = False
        # The status refresh() last wrote itself ("No tools are on", too many tools): cleared
        # once its cause is gone, while an action's own message (New, Import) is kept.
        self._refreshStatus = None

        self.box = ctk.ctkCollapsibleButton()
        self.box.text = "Tools"
        self.box.collapsed = True
        layout.addWidget(self.box)
        column = qt.QVBoxLayout(self.box)
        column.setSpacing(8)

        self.packTree = qt.QTreeWidget()
        self.packTree.setColumnCount(2)
        self.packTree.setHeaderLabels(["Pack", "Status"])
        self.packTree.rootIsDecorated = False
        self.packTree.uniformRowHeights = True
        self.packTree.header().setStretchLastSection(True)
        self.packTree.header().setSectionResizeMode(0, qt.QHeaderView.ResizeToContents)
        column.addWidget(self.packTree)

        self.detailLabel = panel_style.muted_label()
        column.addWidget(self.detailLabel)

        self.toolTree = _WrappingTree()
        self.toolTree.setColumnCount(2)
        self.toolTree.setHeaderLabels(["Tool", "What it does"])
        self.toolTree.rootIsDecorated = False
        # A description is read whole: wrapped at the column's width, its row as tall as
        # it, the list as tall as its rows. Nothing is cut short and nothing scrolls.
        self.toolTree.uniformRowHeights = False
        self.toolTree.wordWrap = True
        self.toolTree.textElideMode = qt.Qt.ElideNone
        self.toolTree.header().setStretchLastSection(True)
        self.toolTree.header().setSectionResizeMode(0, qt.QHeaderView.ResizeToContents)
        self.toolTree.setHorizontalScrollBarPolicy(qt.Qt.ScrollBarAlwaysOff)
        self.toolTree.setVerticalScrollBarPolicy(qt.Qt.ScrollBarAlwaysOff)
        # A new width wraps the descriptions anew: measure the rows again then.
        self.toolTree.onWidth = self._fitTools
        self._toolDelegate = _TopDelegate(self.toolTree)  # kept: the tree does not own it for Python
        self.toolTree.setItemDelegate(self._toolDelegate)
        self.toolTree.header().connect("sectionResized(int,int,int)", self._fitTools)
        column.addWidget(self.toolTree)

        fileButtons = qt.QHBoxLayout()
        self.editButton = qt.QPushButton("Open in editor")
        self.editButton.toolTip = "Open the pack's file in a text editor"
        self.showButton = qt.QPushButton("Show in folder")
        self.showButton.toolTip = "Show the pack's file in your tools folder"
        self.restoreButton = qt.QPushButton("Restore original")
        self.restoreButton.toolTip = (
            "Put SlicerFlow's version of this pack back; your edited file is kept as .bak")
        for button in (self.editButton, self.showButton, self.restoreButton):
            fileButtons.addWidget(button)
        fileButtons.addStretch(1)
        column.addLayout(fileButtons)

        buttons = qt.QHBoxLayout()
        self.newButton = qt.QPushButton("New…")
        self.newButton.toolTip = "Write a commented example pack to start from"
        self.importButton = qt.QPushButton("Import…")
        self.importButton.toolTip = "Copy a .py tool pack into your tools folder"
        self.folderButton = qt.QPushButton("Open folder")
        self.folderButton.toolTip = "Show your tools folder"
        for button in (self.newButton, self.importButton, self.folderButton):
            buttons.addWidget(button)
        buttons.addStretch(1)
        column.addLayout(buttons)

        self.installButton = qt.QPushButton()
        self.installButton.toolTip = "Install the packages the selected pack needs"
        self.installButton.hide()
        column.addWidget(self.installButton)

        self.restartBar = qt.QFrame()
        self.restartBar.frameShape = qt.QFrame.StyledPanel
        bar = qt.QHBoxLayout(self.restartBar)
        bar.setContentsMargins(8, 4, 4, 4)
        self.restartLabel = qt.QLabel("Changes apply after a restart.")
        bar.addWidget(self.restartLabel, 1)
        self.restartButton = qt.QPushButton("Restart chat")
        bar.addWidget(self.restartButton)
        self.restartBar.hide()
        column.addWidget(self.restartBar)

        self.statusLabel = panel_style.muted_label()
        column.addWidget(self.statusLabel)

        self.packTree.connect("itemChanged(QTreeWidgetItem*,int)", self._onItemChanged)
        self.packTree.connect("itemSelectionChanged()", self._showSelected)
        self.editButton.connect("clicked(bool)", self.onEdit)
        self.showButton.connect("clicked(bool)", self.onShow)
        self.restoreButton.connect("clicked(bool)", self.onRestore)
        self.newButton.connect("clicked(bool)", self.onNew)
        self.importButton.connect("clicked(bool)", self.onImport)
        self.folderButton.connect("clicked(bool)", self.onOpenFolder)
        self.installButton.connect("clicked(bool)", self.onInstall)
        self.restartButton.connect("clicked(bool)", self.onRestart)
        self.refresh()

    # ── Showing ──

    def refresh(self):
        if self._handling:
            qt.QTimer.singleShot(0, self.refresh)
            return
        folder = user_tools.ensure_user_dir(paths.user_tools_dir())
        state = user_tools.read_state(paths.user_tools_file())
        files, changed = user_tools.sync_builtins(
            folder, state, user_tools.builtin_sources(user_tools.BUILTIN_DIR))
        if changed:
            user_tools.save_state(paths.user_tools_file(), state)
        runtime = lifecycle.current()
        packs = getattr(runtime, "packs", [])
        loaded = {pack.name: pack for pack in packs if not pack.builtin}
        rows = []
        for item in files:
            if item.edited:
                rows.append(self._userRow("edited", item.stem, item.path,
                                          user_tools.read_pack(item.path), state,
                                          name=item.name, newer=item.newer))
            else:
                # Read from the extension, as the chat loads it: the same text as the
                # folder's copy, and still there when the folder refused the write.
                info = user_tools.read_pack(os.path.join(user_tools.BUILTIN_DIR, item.name + ".py"))
                rows.append(Row("builtin", item.name, item.stem, info.title or item.stem,
                                item.path, item.name not in state.builtin_off, info, name=item.name))
        stems = {item.stem for item in files}
        names = [name for name in user_tools.scan(folder) if name not in stems]
        for name in names:
            path = user_tools.pack_path(folder, name)
            rows.append(self._userRow("user", name, path, user_tools.read_pack(path), state))
        selected = self._selected()
        self._refreshing = True  # filling the items fires itemChanged
        try:
            self.packTree.clear()
            self._rows = rows
            for row in rows:
                kind, text = self._rowStatus(row, loaded)
                item = self._item("{} ({})".format(row.title, KIND_LABELS[row.kind]), row.on, kind, text)
                item.setToolTip(0, row.path)
                self.packTree.addTopLevelItem(item)
        finally:
            self._refreshing = False
        self._fitTree(self.packTree, len(rows))
        on = {(self._group(row.kind), row.key) for row in rows if row.on}
        self.box.text = "Tools · {} on".format(len(on)) if on else "Tools"
        # Changes wait for a restart: shown only while a chat runs that loaded other packs.
        started = {("builtin" if pack.builtin else "user", pack.name) for pack in packs}
        self.restartBar.setVisible(runtime is not None and on != started)
        count = len(getattr(runtime, "tools", []))
        status = None
        if not on:
            status = NO_TOOLS_TEXT
        elif count > user_tools.MANY_TOOLS:
            status = (
                "This chat has {} tools. Above {}, the assistant chooses between them less "
                "reliably: turn off packs you are not using.".format(count, user_tools.MANY_TOOLS)
            )
        if status is not None:
            self._status(status)
        elif self._refreshStatus is not None and self.statusLabel.text == self._refreshStatus:
            self._status("")
        self._refreshStatus = status
        if selected is not None:
            self._reselect(selected.stem)
        self._showSelected()

    @staticmethod
    def _group(kind):
        """How ``Runtime.packs`` tells a pack's kind: SlicerFlow's unedited packs are
        ``builtin``, everything loaded from a file is not."""
        return "builtin" if kind == "builtin" else "user"

    def _userRow(self, kind, stem, path, info, state, name="", newer=False):
        """A row for a pack loaded from its file: the user's own, or an edited built-in."""
        missing, problem = user_tools.check_requires(path)
        return Row(kind, stem, stem, info.title or stem, path, stem in state.enabled, info,
                   name=name, missing=missing, problem=problem, newer=newer)

    @staticmethod
    def _rowStatus(row, loaded):
        if row.kind == "builtin":
            return user_tools.builtin_status(row.on, row.info)
        return user_tools.pack_status(row.on, row.missing, loaded.get(row.stem), row.problem)

    def _item(self, name, on, kind, text):
        item = qt.QTreeWidgetItem()
        item.setFlags(item.flags() | qt.Qt.ItemIsUserCheckable)
        item.setText(0, name)
        item.setCheckState(0, qt.Qt.Checked if on else qt.Qt.Unchecked)
        item.setText(1, text)
        item.setToolTip(1, text)  # an error can be longer than the column is wide
        state = KIND_STATES.get(kind)
        if state is not None:
            item.setForeground(1, qt.QBrush(panel_style.state_color(self.packTree, state)))
        if kind in ("error", "needs"):
            item.setIcon(1, self.packTree.style().standardIcon(qt.QStyle.SP_MessageBoxWarning))
        return item

    @staticmethod
    def _fitTree(tree, rows):
        """As tall as its rows (up to ``MAX_ROWS``, then it scrolls): no empty white box
        under two packs."""
        row = tree.sizeHintForRow(0) if rows else 0
        header = tree.header().sizeHint.height()
        frame = 2 * tree.frameWidth
        tree.setFixedHeight(header + frame + row * min(max(rows, 1), MAX_ROWS) + 2)

    def _fitTools(self, *_resized):
        """Each row as tall as its description wrapped at the column's width now, and the
        list as tall as its rows. A tree measures a wrapped row as one line, so each row is
        given its height here, again whenever the column's width changes."""
        tree = self.toolTree
        metrics = qt.QFontMetrics(tree.font)
        # The item's text sits inside a margin on each side (QCommonStyle's view items).
        margin = 2 * (tree.style().pixelMetric(qt.QStyle.PM_FocusFrameHMargin) + 1)
        width = max(tree.header().sectionSize(1) - margin, 1)
        for i in range(tree.topLevelItemCount):
            item = tree.topLevelItem(i)
            text = metrics.boundingRect(qt.QRect(0, 0, width, 100000), qt.Qt.TextWordWrap, item.text(1))
            item.setSizeHint(1, qt.QSize(0, text.height() + 10))  # a gap under each
        tree.doItemsLayout()
        rows = sum(tree.visualItemRect(tree.topLevelItem(i)).height()
                   for i in range(tree.topLevelItemCount))
        header = tree.header().sizeHint.height()
        tree.setFixedHeight(header + 2 * tree.frameWidth + rows + 2)

    def _status(self, text):
        # repaint(), never processEvents(): see SlicerFlowWidget._status.
        self.statusLabel.text = text
        self.statusLabel.repaint()

    def _selected(self):
        """The selected ``Row``, or None."""
        item = self.packTree.currentItem()
        if item is None:
            return None
        index = self.packTree.indexOfTopLevelItem(item)
        return self._rows[index] if 0 <= index < len(self._rows) else None

    def _reselect(self, stem):
        for index, row in enumerate(self._rows):
            if row.stem == stem:
                self.packTree.setCurrentItem(self.packTree.topLevelItem(index))
                return

    def _showSelected(self):
        """The tool list, the detail line and the file buttons for the selected pack."""
        row = self._selected()
        shown = row is not None
        for widget in (self.toolTree, self.editButton, self.showButton):
            widget.setVisible(shown)
        self.detailLabel.setVisible(shown and bool(self._detail(row)))
        self.restoreButton.setVisible(shown and row.kind == "edited")
        missing = row.missing if shown else {}
        self.installButton.text = "Install " + ", ".join(sorted(missing.values()))
        self.installButton.setVisible(bool(missing))
        if not shown:
            return
        self.detailLabel.text = self._detail(row)
        self.toolTree.clear()
        for tool in row.info.tools:
            # An unedited built-in tool asks only when it says so; any other pack's tool asks
            # unless it says not to (user_tools._check).
            asks = tool.permission is True or (tool.permission is None and row.kind != "builtin")
            item = qt.QTreeWidgetItem()
            item.setText(0, tool.name + (" (asks first)" if asks else ""))
            item.setText(1, tool.summary)
            # A name beside a long description sits by its first line.
            for column in (0, 1):
                item.setTextAlignment(column, qt.Qt.AlignLeft | qt.Qt.AlignTop)
            spec = row.info.presentation.get(tool.name)
            warning = spec.get("warning") if isinstance(spec, dict) and spec.get("destructive") else None
            if warning:
                item.setIcon(0, self.toolTree.style().standardIcon(qt.QStyle.SP_MessageBoxWarning))
                item.setToolTip(0, warning)
            self.toolTree.addTopLevelItem(item)
        self._fitTools()

    @staticmethod
    def _detail(row):
        """What the list does not already say about the selected pack, or "": the row gives
        its title, kind, status and tool count (a package it needs too), its tooltip the
        file."""
        lines = []
        if row is not None and row.kind == "edited":
            lines.append(EDITED_DETAIL)
        if row is not None and row.newer:
            lines.append(NEWER_TEXT)
        return "\n".join(lines)

    def _onItemChanged(self, item, column):
        if self._refreshing or column != 0:
            return
        row = self._rows[self.packTree.indexOfTopLevelItem(item)]
        on = item.checkState(0) == qt.Qt.Checked
        state = user_tools.read_state(paths.user_tools_file())
        self._handling = True
        try:
            if row.kind == "builtin":  # SlicerFlow's own code, as written: no question
                if on:
                    state.builtin_off.discard(row.key)
                else:
                    state.builtin_off.add(row.key)
                user_tools.write_state(paths.user_tools_file(), state)
            # Turning off, or a pack already on, needs no question; a declined one stays off,
            # and the refresh below clears its box again.
            elif not on or row.key in state.enabled or self._confirm(self._trustText(row)):
                self._setEnabled(row.key, on)
        finally:
            self._handling = False
        # Later, not now: refresh() clears the list, and this item's own signal is running.
        qt.QTimer.singleShot(0, self.refresh)

    @staticmethod
    def _trustText(row):
        if row.kind == "edited":
            return EDITED_TEXT.format(file=row.stem + ".py", name=row.title)
        return TRUST_TEXT.format(name=row.title)

    def _setEnabled(self, name, on):
        state = user_tools.read_state(paths.user_tools_file())
        if on:
            state.enabled.add(name)
        else:
            state.enabled.discard(name)
        user_tools.write_state(paths.user_tools_file(), state)  # keeps the rest as it was

    # ── Buttons ──

    def onEdit(self):
        row = self._selected()
        if row is not None:
            self._openInEditor(row.path)

    def onShow(self):
        row = self._selected()
        if row is not None:
            self._showInFolder(row.path)

    def onRestore(self):
        row = self._selected()
        if row is None or row.kind != "edited":
            return
        file = row.stem + ".py"
        if not self._confirm(RESTORE_TEXT.format(file=file)):
            return
        state = user_tools.read_state(paths.user_tools_file())
        source = user_tools.builtin_sources(user_tools.BUILTIN_DIR)[row.name]
        try:
            user_tools.restore_builtin(paths.user_tools_dir(), state, row.name, source)
            user_tools.write_state(paths.user_tools_file(), state)
        except OSError as exc:
            self._status("SlicerFlow's {} could not be written: {}".format(file, exc))
            return
        self.refresh()
        self._status("Restored SlicerFlow's {}; your version is {}.bak. Restart the chat to "
                     "load it.".format(file, file))

    def onNew(self):
        name = self._askName()
        if not name:
            return
        try:
            path = user_tools.new_pack(paths.user_tools_dir(), name.strip())
        except (ValueError, FileExistsError) as exc:
            self._status(str(exc))
            return
        self._setEnabled(name.strip(), True)  # the user is writing it: no trust prompt
        self.refresh()
        self._openInEditor(path)
        self._status("Created {} in your tools folder. Edit it, then restart the chat.".format(
            os.path.basename(path)))

    def onImport(self):
        source = self._pickFile()
        if not source:
            return
        name = os.path.splitext(os.path.basename(source))[0]
        if not self._confirm(TRUST_TEXT.format(name=name)):
            return
        try:
            try:
                name = user_tools.import_pack(paths.user_tools_dir(), source)
            except FileExistsError:
                if not self._confirm("You already have a tool pack named {}. Replace it?".format(name)):
                    return
                name = user_tools.import_pack(paths.user_tools_dir(), source, replace=True)
        except (OSError, ValueError) as exc:
            self._status(str(exc))
            return
        self._setEnabled(name, True)
        self.refresh()
        self._status("Imported {}. Restart the chat to load it.".format(name))

    def onOpenFolder(self):
        self._open(user_tools.ensure_user_dir(paths.user_tools_dir()))

    def onInstall(self):
        row = self._selected()
        if row is None or not row.missing:
            return
        packages = sorted(row.missing.values())
        if not self._confirm(
            "Install {} into 3D Slicer's Python for {}? Slicer stops responding until pip "
            "finishes.".format(", ".join(packages), row.title)
        ):
            return
        self._status("Installing {}…".format(", ".join(packages)))
        try:
            slicer.util.pip_install(packages)
        except subprocess.CalledProcessError as exc:
            self._status("pip could not install {}: {}".format(", ".join(packages), exc))
            return
        importlib.invalidate_caches()  # so check_requires finds what was just installed
        self.refresh()
        self._status("Installed {}. Restart the chat to load {}.".format(", ".join(packages), row.title))

    def onRestart(self):
        self._restart()
        self.refresh()

    # ── Dialogs and the desktop (the tests replace these) ──

    def _confirm(self, text):
        return slicer.util.confirmYesNoDisplay(text)

    def _askName(self):
        return qt.QInputDialog.getText(
            None, "New tool pack", "Name (lowercase letters, digits and _, e.g. lung_tools):")

    def _pickFile(self):
        return qt.QFileDialog.getOpenFileName(None, "Import a tool pack", "", "Python (*.py)")

    def _open(self, path):
        qt.QDesktopServices.openUrl(qt.QUrl.fromLocalFile(path))

    def _openInEditor(self, path):
        try:
            user_tools.open_in_editor(path)
        except OSError as exc:
            self._status("Could not open an editor for {}: {}".format(os.path.basename(path), exc))

    def _showInFolder(self, path):
        try:
            user_tools.show_in_folder(path)
        except OSError as exc:
            self._status("Could not show {}: {}".format(os.path.basename(path), exc))
