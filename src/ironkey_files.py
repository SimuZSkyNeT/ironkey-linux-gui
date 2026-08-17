#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
#
# Built-in file browser — part of IronKey Locker+ for Linux
# Copyright (C) 2026 Simuz
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 2 as published
# by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
# Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, see <https://www.gnu.org/licenses/>.
"""
Built-in file browser for the IronKey GUI.

Lets you work with the drive's contents without leaving the app: browse
folders, import files in, export them out, create folders and delete
things.

Runs entirely unprivileged — the mount is owned by the user, so no
authentication is ever needed here.
"""

import os
import shutil
import time

import gi

try:
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    GTK4 = True
except (ValueError, ImportError):
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    GTK4 = False



def human_size(n):
    if n < 1024:
        return f"{n} B"
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        n /= 1024
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} PiB"


class FileBrowser(Gtk.Window):
    """A minimal, safe file manager scoped to the drive."""

    def __init__(self, parent, root):
        super().__init__(title="Files on the drive", transient_for=parent)
        self.set_default_size(720, 520)
        self.parent = parent
        self.root = os.path.realpath(root)
        self.cwd = self.root

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for m in ("set_margin_top", "set_margin_bottom",
                  "set_margin_start", "set_margin_end"):
            getattr(box, m)(14)
        if GTK4:
            self.set_child(box)
        else:
            self.add(box)

        # --- path bar ---
        pathrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_up = Gtk.Button(label="↑ Up")
        self.btn_up.connect("clicked", lambda *_: self.go_up())
        self.lbl_path = Gtk.Label()
        self.lbl_path.set_xalign(0)
        self.lbl_path.set_selectable(True)
        self._pack(pathrow, self.btn_up, False)
        self._pack(pathrow, self.lbl_path, True)
        self._pack(box, pathrow, False)

        # --- listing ---
        # columns: name, size text, modified text, is_dir, size bytes
        self.store = Gtk.ListStore(str, str, str, bool, object)
        self.view = Gtk.TreeView(model=self.store)
        self.view.set_headers_visible(True)
        for i, title in enumerate(("Name", "Size", "Modified")):
            col = Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=i)
            col.set_resizable(True)
            if i == 0:
                col.set_expand(True)
            self.view.append_column(col)
        self.view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.view.connect("row-activated", self.on_activate)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        if GTK4:
            scroll.set_child(self.view)
        else:
            scroll.add(self.view)
        self._pack(box, scroll, True)

        # --- status line ---
        self.lbl_status = Gtk.Label()
        self.lbl_status.set_xalign(0)
        self.lbl_status.get_style_context().add_class("dim")
        self._pack(box, self.lbl_status, False)

        # --- actions ---
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_homogeneous(True)
        for label, cb in (("Import files…", self.on_import),
                          ("Export selected…", self.on_export),
                          ("New folder", self.on_mkdir),
                          ("Delete", self.on_delete),
                          ("Refresh", lambda *_: self.reload())):
            b = Gtk.Button(label=label)
            b.connect("clicked", cb)
            if label == "Delete":
                b.get_style_context().add_class("destructive-action")
            self._pack(row, b, True)
        self._pack(box, row, False)

        self.reload()

    # ------------------------------------------------------------------
    @staticmethod
    def _pack(container, widget, expand):
        if GTK4:
            widget.set_vexpand(expand)
            widget.set_hexpand(expand)
            container.append(widget)
        else:
            container.pack_start(widget, expand, expand, 0)

    def _show(self, win):
        if GTK4:
            win.present()
        else:
            win.show_all()

    def error(self, text):
        d = Gtk.MessageDialog(transient_for=self, modal=True,
                              message_type=Gtk.MessageType.ERROR,
                              buttons=Gtk.ButtonsType.OK, text=text)
        d.connect("response", lambda x, *_: x.destroy())
        self._show(d)

    # ------------------------------------------------------------------
    def _inside_root(self, path):
        """Never let navigation escape the drive."""
        real = os.path.realpath(path)
        return real == self.root or real.startswith(self.root + os.sep)

    def reload(self):
        self.store.clear()
        try:
            entries = sorted(
                os.scandir(self.cwd),
                key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError as e:
            self.error(f"Cannot read this folder: {e}")
            return

        files = dirs = 0
        total = 0
        for e in entries:
            if e.name.startswith("."):
                continue                      # hide dotfiles and our temps
            try:
                st = e.stat()
                is_dir = e.is_dir()
                size = st.st_size
                when = time.strftime("%Y-%m-%d %H:%M",
                                     time.localtime(st.st_mtime))
            except OSError:
                continue
            self.store.append([
                ("📁 " if is_dir else "📄 ") + e.name,
                "" if is_dir else human_size(size),
                when, is_dir, e.name])
            if is_dir:
                dirs += 1
            else:
                files += 1
                total += size

        rel = os.path.relpath(self.cwd, self.root)
        self.lbl_path.set_text("/" if rel == "." else "/" + rel)
        self.btn_up.set_sensitive(self.cwd != self.root)
        self.lbl_status.set_text(
            f"{dirs} folders, {files} files, {human_size(total)}"
            + self._free_text())

    def _free_text(self):
        try:
            st = os.statvfs(self.cwd)
            free = st.f_bavail * st.f_frsize
            return f"  ·  {human_size(free)} free"
        except OSError:
            return ""

    def go_up(self):
        parent = os.path.dirname(self.cwd)
        if self._inside_root(parent):
            self.cwd = parent
            self.reload()

    def on_activate(self, view, path, column):
        it = self.store.get_iter(path)
        is_dir = self.store.get_value(it, 3)
        name = self.store.get_value(it, 4)
        target = os.path.join(self.cwd, name)
        if is_dir and self._inside_root(target):
            self.cwd = target
            self.reload()
        elif not is_dir:
            # Hand single files to the desktop's default application.
            try:
                import subprocess
                subprocess.Popen(["xdg-open", target])
            except Exception as e:
                self.error(f"Could not open the file: {e}")

    def selected_names(self):
        model, paths = self.view.get_selection().get_selected_rows()
        return [model.get_value(model.get_iter(p), 4) for p in paths]

    # ------------------------------------------------------------------
    def on_import(self, _btn):
        dlg = Gtk.FileChooserDialog(
            title="Choose files to copy onto the drive",
            transient_for=self, action=Gtk.FileChooserAction.OPEN)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Copy", Gtk.ResponseType.OK)
        dlg.set_select_multiple(True)

        def resp(d, r):
            paths = []
            if r == Gtk.ResponseType.OK:
                try:
                    if GTK4:
                        files = d.get_files()
                        paths = [files.get_item(i).get_path()
                                 for i in range(files.get_n_items())]
                    else:
                        paths = d.get_filenames()
                except Exception:
                    paths = []
            d.destroy()
            if paths:
                self._copy_in(paths)
        dlg.connect("response", resp)
        self._show(dlg)

    def _copy_in(self, paths):
        done, failed = 0, []
        for src in paths:
            try:
                dst = os.path.join(self.cwd, os.path.basename(src))
                if os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
                done += 1
            except Exception as e:
                failed.append(f"{os.path.basename(src)}: {e}")
        os.sync() if hasattr(os, "sync") else None
        self.reload()
        if failed:
            self.error("Some items could not be copied:\n" +
                       "\n".join(failed[:6]))
        else:
            self.lbl_status.set_text(f"Copied {done} item(s) onto the drive.")

    def on_export(self, _btn):
        names = self.selected_names()
        if not names:
            self.error("Select something to export first.")
            return
        dlg = Gtk.FileChooserDialog(
            title="Choose where to copy the selection",
            transient_for=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Copy here", Gtk.ResponseType.OK)

        def resp(d, r):
            dest = None
            if r == Gtk.ResponseType.OK:
                try:
                    dest = d.get_file().get_path() if GTK4 \
                        else d.get_filename()
                except Exception:
                    dest = None
            d.destroy()
            if dest:
                self._copy_out(names, dest)
        dlg.connect("response", resp)
        self._show(dlg)

    def _copy_out(self, names, dest):
        done, failed = 0, []
        for name in names:
            src = os.path.join(self.cwd, name)
            dst = os.path.join(dest, name)
            try:
                if os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
                done += 1
            except Exception as e:
                failed.append(f"{name}: {e}")
        if failed:
            self.error("Some items could not be exported:\n" +
                       "\n".join(failed[:6]))
        else:
            self.lbl_status.set_text(f"Exported {done} item(s) to {dest}")

    def on_mkdir(self, _btn):
        dlg = Gtk.Dialog(title="New folder", transient_for=self, modal=True)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Create", Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)
        c = dlg.get_content_area()
        c.set_spacing(8)
        for m in ("set_margin_top", "set_margin_bottom",
                  "set_margin_start", "set_margin_end"):
            getattr(c, m)(14)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Folder name")
        entry.set_activates_default(True)
        self._pack(c, entry, False)

        def resp(d, r):
            name = entry.get_text().strip()
            d.destroy()
            if r != Gtk.ResponseType.OK or not name:
                return
            if "/" in name or name in (".", ".."):
                self.error("That name is not allowed.")
                return
            try:
                os.makedirs(os.path.join(self.cwd, name), exist_ok=False)
                self.reload()
            except OSError as e:
                self.error(f"Could not create the folder: {e}")
        dlg.connect("response", resp)
        self._show(dlg)

    def on_delete(self, _btn):
        names = self.selected_names()
        if not names:
            self.error("Select what you want to delete first.")
            return
        preview = ", ".join(names[:4]) + ("…" if len(names) > 4 else "")
        dlg = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=f"Delete {len(names)} item(s)?")
        dlg.format_secondary_text(
            f"{preview}\n\nThis does not use a wastebasket: the files are "
            f"removed immediately.")
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        b = dlg.add_button("Delete", Gtk.ResponseType.OK)
        b.get_style_context().add_class("destructive-action")

        def resp(d, r):
            d.destroy()
            if r != Gtk.ResponseType.OK:
                return
            failed = []
            for name in names:
                p = os.path.join(self.cwd, name)
                if not self._inside_root(p):
                    continue
                try:
                    if os.path.isdir(p) and not os.path.islink(p):
                        shutil.rmtree(p)
                    else:
                        os.remove(p)
                except OSError as e:
                    failed.append(f"{name}: {e}")
            self.reload()
            if failed:
                self.error("Some items could not be deleted:\n" +
                           "\n".join(failed[:6]))
        dlg.connect("response", resp)
        self._show(dlg)


def open_browser(parent, mountpoint):
    win = FileBrowser(parent, mountpoint)
    if GTK4:
        win.present()
    else:
        win.show_all()
    return win
