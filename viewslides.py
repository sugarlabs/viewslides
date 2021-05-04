# Copyright (C) 2008, 2013 James D. Simmons <nicestep@gmail.com>
# Copyright (C) 2012 Aneesh Dogra <lionaneesh@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

import os
import logging
import time
import zipfile
from zipfile import BadZipfile

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk
from gi.repository import GdkPixbuf
from gi.repository import Gdk
import pygame
import re

from sugar3.activity import activity
from sugar3 import network
from sugar3.datastore import datastore
from sugar3 import profile
from sugar3.graphics.alert import NotifyAlert
from sugar3.graphics.toolbarbox import ToolbarBox
from sugar3.graphics.toolbarbox import ToolbarButton
from sugar3.activity.widgets import ActivityToolbarButton, StopButton
from readtoolbar import SlidesToolbar
from sugar3.graphics.toolbutton import ToolButton
from sugar3.graphics.menuitem import MenuItem
from sugar3.graphics.toggletoolbutton import ToggleToolButton

from readsidebar import Sidebar
from gettext import gettext as _
import dbus
from gi.repository import GLib
from gi.repository import GObject
from gi.repository import TelepathyGLib
import pickle
from decimal import *
import xopower
from collabwrapper import CollabWrapper

_TOOLBAR_READ = 1
_TOOLBAR_SLIDES = 3
COLUMN_IMAGE = 0
COLUMN_PATH = 1
COLUMN_OLD_NAME = 1

_logger = logging.getLogger('view-slides')


class JobjectWrapper():
    def __init__(self):
        self.__jobject = None
        self.__file_path = None

    def set_jobject(self, jobject):
        self.__jobject = jobject

    def set_file_path(self, file_path):
        self.__file_path = file_path

    def get_file_path(self):
        if self.__jobject is not None:
            return self.__jobject.get_file_path()
        else:
            return self.__file_path


class Annotations():

    def __init__(self, pickle_file_name):
        self.title = ''
        self.notes = {0: ''}
        self.bookmarks = []
        self.pickle_file_name = pickle_file_name

    def get_title(self):
        return self.title

    def set_title(self, title):
        self.title = title

    def get_notes(self):
        return self.notes

    def get_note(self, page):
        try:
            return self.notes[page]
        except KeyError:
            return ''

    def add_note(self, page, text):
        status = False
        if self.get_note(page) != text:
            status = True
        self.notes[page] = text
        if text == '':
            del self.notes[page]
        return status

    def is_bookmarked(self, page):
        bookmark = self.bookmarks.count(page)
        if bookmark > 0:
            return True
        else:
            return False

    def add_bookmark(self, page):
        self.bookmarks.append(page)

    def remove_bookmark(self, page):
        try:
            self.bookmarks.remove(page)
        except ValueError:
            print('page already not bookmarked', page)

    def get_bookmarks(self):
        self.bookmarks.sort()
        return self.bookmarks

    def restore(self):
        if os.path.exists(self.pickle_file_name):
            pickle_input = open(self.pickle_file_name, 'rb')
            self.title = pickle.load(pickle_input)
            self.bookmarks = pickle.load(pickle_input)
            self.notes = pickle.load(pickle_input)
            pickle_input.close()

    def save(self):
        pickle_output = open(self.pickle_file_name, 'wb')
        pickle.dump(self.title, pickle_output)
        pickle.dump(self.bookmarks, pickle_output)
        pickle.dump(self.notes, pickle_output)
        pickle_output.close()


class ViewSlidesActivity(activity.Activity):
    __gsignals__ = {
        'go-fullscreen': (GObject.SIGNAL_RUN_FIRST,
                          GObject.TYPE_NONE,
                          ([]))
    }

    def __init__(self, handle):
        "The entry point to the Activity"
        activity.Activity.__init__(self, handle)

        self._fileserver = None
        self._object_id = handle.object_id
        self.zoom_image_to_fit = True
        self.total_pages = 0
        self.buddies = {}

        self.connect("draw", self.__draw_cb)
        self.connect("delete-event", self.__delete_event_cb)
        self.object_id = handle.object_id
        self.create_new_toolbar()
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(
            Gtk.PolicyType.NEVER,
            Gtk.PolicyType.AUTOMATIC)
        self.image = Gtk.Image()
        self.eventbox = Gtk.EventBox()
        self.eventbox.add(self.image)
        self.image.show()
        self.eventbox.show()
        self.scrolled.add_with_viewport(self.eventbox)
        self.eventbox.set_events(
            Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.BUTTON_PRESS_MASK)
        self.eventbox.set_can_focus(True)
        self.eventbox.connect("key-press-event", self.__key_press_event_cb)
        self.eventbox.connect("button-press-event",
                                  self.__button_press_event_cb)

        self.annotation_textview = Gtk.TextView()
        self.annotation_textview.set_left_margin(50)
        self.annotation_textview.set_right_margin(50)
        self.annotation_textview.set_wrap_mode(Gtk.WrapMode.WORD)
        self.annotation_textview.show()
        self.sidebar = Sidebar()
        self.sidebar.show()

        self.ls_left = Gtk.ListStore(
            GObject.TYPE_STRING, GObject.TYPE_STRING)
        tv_left = Gtk.TreeView(self.ls_left)
        tv_left.set_rules_hint(True)
        tv_left.set_search_column(COLUMN_IMAGE)
        selection_left = tv_left.get_selection()
        selection_left.set_mode(Gtk.SelectionMode.SINGLE)
        selection_left.connect("changed", self.selection_left_cb)
        renderer = Gtk.CellRendererText()
        col_left = Gtk.TreeViewColumn(
            _('Slideshow Image'), renderer, text=COLUMN_IMAGE)
        col_left.set_sort_column_id(COLUMN_IMAGE)
        renderer.set_property('editable', True)
        renderer.connect('edited', self.col_left_edited_cb, self.ls_left)
        tv_left.append_column(col_left)

        self.list_scroller_left = Gtk.ScrolledWindow()
        self.list_scroller_left.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list_scroller_left.add(tv_left)

        self.ls_right = Gtk.ListStore(
            GObject.TYPE_STRING, GObject.TYPE_PYOBJECT)
        tv_right = Gtk.TreeView(self.ls_right)
        tv_right.set_rules_hint(True)
        tv_right.set_search_column(COLUMN_IMAGE)
        selection_right = tv_right.get_selection()
        selection_right.set_mode(Gtk.SelectionMode.SINGLE)
        selection_right.connect("changed", self.selection_right_cb)
        renderer = Gtk.CellRendererText()
        self.col_right = Gtk.TreeViewColumn(
            _('Available Images'), renderer, text=COLUMN_IMAGE)
        self.col_right.set_sort_column_id(COLUMN_IMAGE)
        tv_right.append_column(self.col_right)

        self.list_scroller_right = Gtk.ScrolledWindow(
            hadjustment=None, vadjustment=None)
        self.list_scroller_right.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list_scroller_right.add(tv_right)

        self.hpane = Gtk.HPaned()
        self.hpane.add1(self.list_scroller_left)
        self.hpane.add2(self.list_scroller_right)
        self.progressbar = Gtk.ProgressBar()
        self.progressbar.set_fraction(0.0)

        vbox = Gtk.VBox()
        vbox.pack_start(self.progressbar, False, False, 10)
        vbox.pack_start(self.scrolled, True, True, 0)
        vbox.pack_end(self.hpane, True, True, 0)
        vbox.pack_end(self.annotation_textview, False, False, 10)

        sidebar_hbox = Gtk.HBox()
        sidebar_hbox.pack_start(self.sidebar, False, False, 0)
        sidebar_hbox.pack_start(vbox, True, True, 0)
        self.set_canvas(sidebar_hbox)
        sidebar_hbox.show()

        self.scrolled.show()
        tv_left.show()
        self.list_scroller_left.show()
        tv_right.show()
        self.list_scroller_right.show()
        self.hpane.show()
        vbox.show()
        self.hpane.hide()

        self.is_dirty = False
        self.annotations_dirty = False

        self.activity_zip = os.path.join(
            self.get_activity_root(),
            'instance',
            'viewslides-files')
        self.load_journal_table()

        self.page = 0
        self.temp_filename = ''
        self.saved_screen_width = 0
        self.eventbox.grab_focus()
        self.cursor_visible = True

        self.pickle_file_temp = os.path.join(
            self.get_activity_root(),
            'instance',
            'pkl{}'.format(time.time()))
        self.annotations = Annotations(self.pickle_file_temp)

        xopower.setup_idle_timeout()
        if xopower.service_activated:
            self.scrolled.props.vadjustment.connect(
                "value-changed", self._user_action_cb)
            self.scrolled.props.hadjustment.connect(
                "value-changed", self._user_action_cb)
            self.connect("focus-in-event", self._focus_in_event_cb)
            self.connect("focus-out-event", self._focus_out_event_cb)
            self.connect("notify::active", self._now_active_cb)

        self._want_document = False
        # Status of temp file used for write_file:
        self._close_requested = False
        self.connect("shared", self._shared_cb)

        self.is_received_document = False
        self.selected_journal_entry = None
        self.selected_title = None
        self.selection_left = None

        if not self.get_shared():
            if handle.object_id is None:
                self.show_image_tables(True)

        self.collab = CollabWrapper(self)
        self.collab.joined.connect(self._joined_cb)
        self.collab.buddy_joined.connect(self._buddy_joined_cb)
        self.collab.buddy_left.connect(self._buddy_left_cb)
        self.collab.incoming_file.connect(self._incoming_file_cb)
        self.collab.setup()

    def create_new_toolbar(self):
        toolbar_box = ToolbarBox()

        activity_button = ActivityToolbarButton(self)
        toolbar_box.toolbar.insert(activity_button, 0)
        activity_button.show()

        self._slides_toolbar = SlidesToolbar()
        self._slides_toolbar.set_activity(self)
        self._slides_toolbar.show()
        slides_toolbar_button = ToolbarButton(
            page=self._slides_toolbar, icon_name='slides')
        toolbar_box.toolbar.insert(slides_toolbar_button, -1)
        slides_toolbar_button.show()

        self.connect('go-fullscreen',
                     self.__view_toolbar_go_fullscreen_cb)

        self.back = ToolButton('go-previous')
        self.back.set_tooltip(_('Back'))
        self.back.props.sensitive = False
        palette = self.back.get_palette()
        self.menu_prev_page = MenuItem(text_label=_("Previous page"))
        palette.menu.append(self.menu_prev_page)
        self.menu_prev_page.show_all()
        self.menu_prev_bookmark = MenuItem(text_label=_("Previous bookmark"))
        palette.menu.append(self.menu_prev_bookmark)
        self.menu_prev_bookmark.show_all()
        self.back.connect('clicked', self.go_back_cb)
        self.menu_prev_page.connect('activate', self.go_back_cb)
        self.menu_prev_bookmark.connect(
            'activate', self.prev_bookmark_activate_cb)
        toolbar_box.toolbar.insert(self.back, -1)
        self.back.show()

        self.forward = ToolButton('go-next')
        self.forward.set_tooltip(_('Forward'))
        self.forward.props.sensitive = False
        palette = self.forward.get_palette()
        self.menu_next_page = MenuItem(text_label=_("Next page"))
        palette.menu.append(self.menu_next_page)
        self.menu_next_page.show_all()
        self.menu_next_bookmark = MenuItem(text_label=_("Next bookmark"))
        palette.menu.append(self.menu_next_bookmark)
        self.menu_next_bookmark.show_all()
        self.forward.connect('clicked', self.go_forward_cb)
        self.menu_next_page.connect('activate', self.go_forward_cb)
        self.menu_next_bookmark.connect(
            'activate', self.next_bookmark_activate_cb)
        toolbar_box.toolbar.insert(self.forward, -1)
        self.forward.show()

        num_page_item = Gtk.ToolItem()
        self.num_page_entry = Gtk.Entry()
        self.num_page_entry.set_text('0')
        self.num_page_entry.set_alignment(1)
        self.num_page_entry.connect('insert-text',
                                    self.__new_num_page_entry_insert_text_cb)
        self.num_page_entry.connect('activate',
                                    self.__new_num_page_entry_activate_cb)
        self.num_page_entry.set_width_chars(4)
        num_page_item.add(self.num_page_entry)
        self.num_page_entry.show()
        toolbar_box.toolbar.insert(num_page_item, -1)
        num_page_item.show()

        total_page_item = Gtk.ToolItem()
        self.total_page_label = Gtk.Label()

        self.total_page_label.set_markup(
            "<span foreground='#FFF' size='14000'></span>")
        self.total_page_label.set_text(' / 0')
        total_page_item.add(self.total_page_label)
        self.total_page_label.show()
        toolbar_box.toolbar.insert(total_page_item, -1)
        total_page_item.show()

        spacer = Gtk.SeparatorToolItem()
        toolbar_box.toolbar.insert(spacer, -1)
        spacer.show()

        bookmarkitem = Gtk.ToolItem()
        self.bookmarker = ToggleToolButton('emblem-favorite')
        self.bookmarker.set_tooltip(_('Toggle Bookmark'))
        self.bookmarker_handler_id = self.bookmarker.connect(
            'clicked', self.bookmarker_clicked_cb)

        bookmarkitem.add(self.bookmarker)

        toolbar_box.toolbar.insert(bookmarkitem, -1)
        bookmarkitem.show_all()

        spacer2 = Gtk.SeparatorToolItem()
        toolbar_box.toolbar.insert(spacer2, -1)
        spacer2.show()

        self._zoom_out = ToolButton('zoom-out')
        self._zoom_out.set_tooltip(_('Zoom out'))
        self._zoom_out.connect('clicked', self._zoom_out_cb)
        toolbar_box.toolbar.insert(self._zoom_out, -1)
        self._zoom_out.props.sensitive = False
        self._zoom_out.show()

        self._zoom_in = ToolButton('zoom-in')
        self._zoom_in.set_tooltip(_('Zoom in'))
        self._zoom_in.connect('clicked', self._zoom_in_cb)
        toolbar_box.toolbar.insert(self._zoom_in, -1)
        self._zoom_in.props.sensitive = True
        self._zoom_in.show()

        self._fullscreen = ToolButton('view-fullscreen')
        self._fullscreen.set_tooltip(_('Fullscreen'))
        self._fullscreen.connect('clicked', self._fullscreen_cb)
        toolbar_box.toolbar.insert(self._fullscreen, -1)
        self._fullscreen.show()

        separator = Gtk.SeparatorToolItem()
        separator.props.draw = False
        separator.set_expand(True)
        toolbar_box.toolbar.insert(separator, -1)
        separator.show()

        stop_button = StopButton(self)
        toolbar_box.toolbar.insert(stop_button, -1)
        stop_button.show()

        self.set_toolbar_box(toolbar_box)
        toolbar_box.show()
        if self.object_id is None:
            # Not joining, not resuming
            slides_toolbar_button.set_expanded(True)

    def get_data(self):
        return None

    def set_data(self, data):
        pass

    def _zoom_in_cb(self, button):
        self._zoom_in.props.sensitive = False
        self._zoom_out.props.sensitive = True
        self.zoom_to_width()

    def _zoom_out_cb(self, button):
        self._zoom_in.props.sensitive = True
        self._zoom_out.props.sensitive = False
        self.zoom_to_fit()

    def enable_zoom_in(self):
        self._zoom_in.props.sensitive = True
        self._zoom_out.props.sensitive = False

    def enable_zoom_out(self):
        self._zoom_in.props.sensitive = False
        self._zoom_out.props.sensitive = True

    def _fullscreen_cb(self, button):
        self.emit('go-fullscreen')

    def __new_num_page_entry_insert_text_cb(
            self, entry, text, length, position):
        if not re.match('[0-9]', text):
            entry.emit_stop_by_name('insert-text')
            return True
        return False

    def __new_num_page_entry_activate_cb(self, entry):
        if entry.props.text:
            page = int(entry.props.text) - 1
        else:
            page = 0

        if page >= self.total_pages:
            page = self.total_pages - 1
        elif page < 0:
            page = 0

        self.set_current_page(page)
        self.show_page(page)
        entry.props.text = str(page + 1)
        self.update_nav_buttons()

    def go_back_cb(self, button):
        self.previous_page()

    def go_forward_cb(self, button):
        self.next_page()

    def update_nav_buttons(self):
        current_page = self.page
        self.back.props.sensitive = current_page > 0
        self.forward.props.sensitive = \
            current_page < self.total_pages - 1

        self.num_page_entry.handler_block_by_func(
            self.__new_num_page_entry_insert_text_cb)
        self.num_page_entry.handler_block_by_func(
            self.__new_num_page_entry_activate_cb)
        self.num_page_entry.props.text = str(current_page + 1)
        self.num_page_entry.handler_unblock_by_func(
            self.__new_num_page_entry_insert_text_cb)
        self.num_page_entry.handler_unblock_by_func(
            self.__new_num_page_entry_activate_cb)

        self.total_page_label.props.label = \
            ' / ' + str(self.total_pages)

    def set_total_pages(self, pages):
        self.total_pages = pages

    def prev_bookmark_activate_cb(self, menuitem):
        self.prev_bookmark()

    def next_bookmark_activate_cb(self, menuitem):
        self.next_bookmark()

    def bookmarker_clicked_cb(self, button):
        self.bookmarker_clicked(button)

    def setToggleButtonState(self, button, b, id):
        button.handler_block(id)
        button.set_active(b)
        button.handler_unblock(id)

    def update_bookmark_button(self, state):
        self.setToggleButtonState(
            self.bookmarker, state, self.bookmarker_handler_id)

    def load_journal_table(self):
        ds_objects, num_objects = datastore.find({'mime_type': ['image/jpeg', 'image/gif',
            'image/tiff', 'image/png']}, properties=['uid', 'title', 'mime_type'])
        self.ls_right.clear()
        self.activity_zip = zipfile.ZipFile(self.activity_zip, 'w')
        for i in range(0, num_objects):
            selected_iter = self.ls_right.append()
            title = ds_objects[i].metadata['title']
            mime_type = ds_objects[i].metadata['mime_type']
            if mime_type == 'image/jpeg' and not title.endswith('.jpg') and not title.endswith(
                    '.jpeg') and not title.endswith('.JPG') and not title.endswith('.JPEG'):
                title = title + '.jpg'
            if mime_type == 'image/png' and not title.endswith(
                    '.png') and not title.endswith('.PNG'):
                title = title + '.png'
            if mime_type == 'image/gif' and not title.endswith(
                    '.gif') and not title.endswith('.GIF'):
                title = title + '.gif'
            if mime_type == 'image/tiff' and not title.endswith(
                    '.tiff') and not title.endswith('.TIFF'):
                title = title + '.tiff'
            self.ls_right.set(selected_iter, COLUMN_IMAGE, title)
            jobject_wrapper = JobjectWrapper()
            jobject_wrapper.set_jobject(ds_objects[i])
            self.ls_right.set(selected_iter, COLUMN_PATH, jobject_wrapper)

            if title not in self.activity_zip.namelist():
                self.activity_zip.write(ds_objects[i].get_file_path(), title)

        self.activity_zip.close()
        self.ls_right.set_sort_column_id(COLUMN_IMAGE, Gtk.SortType.ASCENDING)

    def reload_journal_table(self):
        if os.path.exists(self.activity_zip.filename) and zipfile.is_zipfile(self.activity_zip):
            zf = zipfile.ZipFile(self.activity_zip, 'r')
            for filename in zf.namelist():
                selected_iter = self.ls_right.append()
                jobject_wrapper = JobjectWrapper()
                jobject_wrapper.set_file_path(
                    os.path.abspath(self.activity_zip.filename))
                self.ls_right.set(selected_iter, COLUMN_IMAGE, filename)
                self.ls_right.set(selected_iter, COLUMN_PATH, jobject_wrapper)
        else:
            self.load_journal_table()

    def col_left_edited_cb(self, cell, path, new_text, user_data):
        liststore = user_data
        if self.check_for_duplicates(new_text):
            self._alert(
                "Duplicate Filename",
                'File ' +
                str(new_text) +
                ' already exists in slideshow!')
            return
        liststore[path][COLUMN_IMAGE] = new_text
        self.is_dirty = True
        return

    def show_image_tables(self, state):
        if state:
            self.hpane.show()
            self.annotation_textview.hide()
            self.sidebar.hide()
            self._slides_toolbar._hide_image_tables.props.sensitive = True
            self._slides_toolbar._reload_journal_table.props.sensitive = True
            self._slides_toolbar._show_image_tables.props.sensitive = False

            self.show_image("ViewSlides.jpg")
        else:
            self.hpane.hide()
            self.annotation_textview.show()
            self.sidebar.show()
            self.set_current_page(0)
            self._load_document(os.path.abspath(self.activity_zip))

    def selection_left_cb(self, selection):
        tv = selection.get_tree_view()
        model = tv.get_model()
        self.selection_left = selection.get_selected()
        if self.selection_left:
            model, selected_iter = self.selection_left
            self._slides_toolbar._remove_image.props.sensitive = True

    def selection_right_cb(self, selection):
        tv = selection.get_tree_view()
        model = tv.get_model()
        sel = selection.get_selected()
        if sel:
            model, selected_iter = sel
            jobject = model.get_value(selected_iter, COLUMN_PATH)
            fname = jobject.get_file_path()
            self.show_image(fname)
            self._slides_toolbar._add_image.props.sensitive = True
            self.selected_journal_entry = jobject
            self.selected_title = model.get_value(selected_iter, COLUMN_IMAGE)

    def add_image(self):
        if self.selected_journal_entry is None:
            return
        selected_file = self.selected_journal_entry.get_file_path()
        arcname = self.selected_title
        if self.check_for_duplicates(arcname):
            self._alert(
                "Duplicate Filename",
                'File ' +
                str(arcname) +
                ' already exists in slideshow!')
            return

        selected_iter = self.ls_left.append()
        self.ls_left.set(
            selected_iter,
            COLUMN_IMAGE,
            arcname,
            COLUMN_OLD_NAME,
            arcname)

        self._slides_toolbar._add_image.props.sensitive = False

    def remove_image(self):
        if self.selection_left:
            model, selected_iter = self.selection_left
            self.ls_left.remove(selected_iter)
            self._slides_toolbar._remove_image.props.sensitive = True
            self.is_dirty = True

    def create_journal_entry(self, tempfile, title):
        journal_entry = datastore.create()
        journal_entry.metadata['title'] = title
        journal_entry.metadata['title_set_by_user'] = '1'
        journal_entry.metadata['keep'] = '0'
        mime_type = 'image/jpeg'
        if title.endswith('.tiff') or title.endswith('.TIFF'):
            mime_type = 'image/tiff'
        elif title.endswith('.gif') or title.endswith('.GIF'):
            mime_type = 'image/gif'
        elif title.endswith('.png') or title.endswith('.PNG'):
            mime_type = 'image/png'
        journal_entry.metadata['mime_type'] = mime_type
        journal_entry.metadata['buddies'] = ''
        journal_entry.metadata['preview'] = ''
        journal_entry.metadata['icon-color'] = profile.get_color().to_string()
        journal_entry.file_path = tempfile
        datastore.write(journal_entry)
        self.load_journal_table()
        self._alert(_('Success'), title + _(' added to Journal.'))

    def check_for_duplicates(self, filename):
        for row in self.ls_left:
            if row[COLUMN_OLD_NAME] == filename:
                return True
            if row[COLUMN_IMAGE] == filename:
                return True
        return False

    def final_rewrite_zip(self):
        if not self.annotations_dirty:
            return

        new_zipfile = os.path.join(self.get_activity_root(), 'instance',
                                   'rewrite{}'.format(time.time()))
        print(self.activity_zip, new_zipfile)
        zf_new = zipfile.ZipFile(new_zipfile, 'w')
        zf_old = zipfile.ZipFile(self.activity_zip, 'r')
        image_files = zf_old.namelist()
        i = 0
        while (i < len(image_files)):
            if (image_files[i] != 'annotations.pkl'):
                self.save_extracted_file(zf_old, image_files[i])
                outfn = self.make_new_filename(image_files[i])
                fname = os.path.join(
                    self.get_activity_root(), 'instance', outfn)
                zf_new.write(fname.encode("utf-8"), outfn.encode("utf-8"))
                os.remove(fname)
            i = i + 1
        zf_new.write(self.pickle_file_temp, 'annotations.pkl')

        zf_old.close()
        zf_new.close()
        os.remove(self.activity_zip)
        self.activity_zip = new_zipfile

    def __button_press_event_cb(self, widget, event):
        widget.grab_focus()

    def __view_toolbar_go_fullscreen_cb(self, view_toolbar):
        self.fullscreen()

    def zoom_to_width(self):
        self.zoom_image_to_fit = False
        self.show_page(self.page)

    def zoom_to_fit(self):
        self.zoom_image_to_fit = True
        self.show_page(self.page)

    def __key_press_event_cb(self, widget, event):
        "Respond when the user presses Escape or one of the arrow keys"
        if xopower.service_activated:
            xopower.reset_sleep_timer()
        keyname = Gdk.keyval_name(event.keyval)
        if keyname == 'Page_Up':
            self.previous_page()
            return True
        if keyname == 'Page_Down':
            self.next_page()
            return True
        if keyname == 'KP_Right':
            self.scroll_down()
            return True
        if keyname == 'Down' or keyname == 'KP_Down':
            self.scroll_down()
            return True
        if keyname == 'Up' or keyname == 'KP_Up':
            self.scroll_up()
            return True
        if keyname == 'KP_Left':
            self.scroll_up()
            return True
        if keyname == 'plus':
            self.view_toolbar.enable_zoom_out()
            self.zoom_to_width()
            return True
        if keyname == 'minus':
            self.view_toolbar.enable_zoom_in()
            self.zoom_to_fit()
            return True
        return False

    def bookmarker_clicked(self, button):
        if button.get_active():
            self.annotations.add_bookmark(self.page)
        else:
            self.annotations.remove_bookmark(self.page)
        self.show_bookmark_state(self.page)
        self.annotations_dirty = True

    def show_bookmark_state(self, page):
        bookmark = self.annotations.is_bookmarked(page)
        if bookmark:
            self.sidebar.show_bookmark_icon(True)
            self.update_bookmark_button(True)
        else:
            self.sidebar.show_bookmark_icon(False)
            self.update_bookmark_button(False)

    def prev_bookmark(self):
        textbuffer = self.annotation_textview.get_buffer()
        if self.annotations.add_note(
            self.page,
            textbuffer.get_text(
                textbuffer.get_start_iter(),
                textbuffer.get_end_iter(),
                include_hidden_chars=True)):
            self.annotations_dirty = True
        bookmarks = self.annotations.get_bookmarks()
        count = len(bookmarks) - 1
        while count >= 0:
            if bookmarks[count] < self.page:
                self.page = bookmarks[count]
                self.show_page(self.page)
                self.set_current_page(self.page)
                return
            count = count - 1
        # if we're before the first bookmark wrap to the last.
        if len(bookmarks) > 0:
            self.page = bookmarks[len(bookmarks) - 1]
            self.show_page(self.page)
            self.set_current_page(self.page)

    def next_bookmark(self):
        textbuffer = self.annotation_textview.get_buffer()
        if self.annotations.add_note(
            self.page,
            textbuffer.get_text(
                textbuffer.get_start_iter(),
                textbuffer.get_end_iter(),
                include_hidden_chars=True)):
            self.annotations_dirty = True
        bookmarks = self.annotations.get_bookmarks()
        count = 0
        while count < len(bookmarks):
            if bookmarks[count] > self.page:
                self.page = bookmarks[count]
                self.show_page(self.page)
                self.set_current_page(self.page)
                return
            count = count + 1
        # if we're after the last bookmark wrap to the first.
        if len(bookmarks) > 0:
            self.page = bookmarks[0]
            self.show_page(self.page)
            self.set_current_page(self.page)

    def scroll_down(self):
        v_adjustment = self.scrolled.get_vadjustment()
        if v_adjustment.get_value() == v_adjustment.get_upper() - \
                v_adjustment.get_page_size():
            self.next_page()
            return
        if v_adjustment.get_value() < v_adjustment.get_upper() - \
                v_adjustment.get_page_size():
            new_value = v_adjustment.get_value() + v_adjustment.get_step_increment()
            if new_value > v_adjustment.get_upper() - v_adjustment.get_page_size():
                new_value = v_adjustment.get_upper() - v_adjustment.get_page_size()
            v_adjustment.set_value(new_value)

    def scroll_up(self):
        v_adjustment = self.scrolled.get_vadjustment()
        if v_adjustment.get_value() == v_adjustment.get_lower():
            self.previous_page()
            return
        if v_adjustment.get_value() > v_adjustment.get_lower():
            new_value = v_adjustment.get_value() - v_adjustment.get_step_increment()
            if new_value < v_adjustment.get_lower():
                new_value = v_adjustment.get_lower()
            v_adjustment.set_value(new_value)

    def previous_page(self):
        textbuffer = self.annotation_textview.get_buffer()
        if self.annotations.add_note(
            self.page,
            textbuffer.get_text(
                textbuffer.get_start_iter(),
                textbuffer.get_end_iter(),
                include_hidden_chars=True)):
            self.annotations_dirty = True
        page = self.page
        page = page - 1
        if page < 0:
            page = 0
        if self.save_extracted_file(self.zf, self.image_files[page]):
            fname = os.path.join(
                self.get_activity_root(),
                'instance',
                self.make_new_filename(
                    self.image_files[page]))
            self.show_image(fname)
            os.remove(fname)
            self.show_bookmark_state(page)
        v_adjustment = self.scrolled.get_vadjustment()
        v_adjustment.set_value(
            v_adjustment.get_upper() -
            v_adjustment.get_page_size())
        self.set_current_page(page)
        self.page = page
        annotation_textbuffer = self.annotation_textview.get_buffer()
        annotation_textbuffer.set_text(self.annotations.get_note(page))

    def set_current_page(self, page):
        self.page = page
        self.update_nav_buttons()

    def next_page(self):
        textbuffer = self.annotation_textview.get_buffer()
        if self.annotations.add_note(
            self.page,
            textbuffer.get_text(
                textbuffer.get_start_iter(),
                textbuffer.get_end_iter(),
                include_hidden_chars=False)):
            self.annotations_dirty = True
        page = self.page
        page = page + 1
        if page >= len(self.image_files):
            page = len(self.image_files) - 1
        if self.save_extracted_file(self.zf, self.image_files[page]):
            fname = os.path.join(
                self.get_activity_root(),
                'instance',
                self.make_new_filename(
                    self.image_files[page]))
            self.show_image(fname)
            os.remove(fname)
            self.show_bookmark_state(page)
        v_adjustment = self.scrolled.get_vadjustment()
        v_adjustment.set_value(v_adjustment.get_lower())
        self.set_current_page(page)
        self.page = page
        annotation_textbuffer = self.annotation_textview.get_buffer()
        annotation_textbuffer.set_text(self.annotations.get_note(page))

    def __draw_cb(self, widget, cr):
        screen_width = Gdk.Screen.width()
        if self.saved_screen_width != screen_width and self.saved_screen_width != 0:
            self.show_page(self.page)
        self.saved_screen_width = screen_width
        return False

    def show_page(self, page):
        self.show_bookmark_state(page)
        if page not in self.image_files:
            return
        if self.save_extracted_file(self.zf, self.image_files[page]):
            fname = os.path.join(
                self.get_activity_root(),
                'instance',
                self.make_new_filename(
                    self.image_files[page]))
            self.show_image(fname)
            os.remove(fname)
            annotation_textbuffer = self.annotation_textview.get_buffer()
            annotation_textbuffer.set_text(self.annotations.get_note(page))

    def show_image(self, filename):
        "display a resized image in a full screen window"
        TOOLBOX_HEIGHT = 60
        BORDER_WIDTH = 30
        # get the size of the fullscreen display
        screen_width = Gdk.Screen.width()
        screen_width = screen_width - BORDER_WIDTH
        screen_height = Gdk.Screen.height()
        screen_height = screen_height - TOOLBOX_HEIGHT
        # get the size of the image.
        im = pygame.image.load(filename)
        image_width, image_height = im.get_size()
        getcontext().prec = 7
        s_a_ratio = Decimal(screen_height) / Decimal(screen_width)
        i_a_ratio = Decimal(image_height) / Decimal(image_width)
        new_width = image_width
        new_height = image_height
        if self.zoom_image_to_fit:
            if s_a_ratio >= i_a_ratio:
                new_width = screen_width
                new_height = image_height * screen_width
                if image_width > 1:
                    new_height /= image_width

                if new_height > screen_width:
                    new_height *= screen_width
                    if new_width > 1:
                        new_height /= new_width
                    new_width = screen_width
            else:
                new_height = screen_height
                new_width = image_width * screen_height
                if image_height > 1:
                    new_width /= image_height
                if new_width > screen_height:
                    new_width *= screen_height
                    if new_height > 1:
                        new_width /= new_height
                    new_height = screen_height
        else:
            new_width = screen_width
            new_height = image_height * screen_width
            if image_width > 1:
                new_height /= image_width

            if new_height > screen_width:
                new_height *= screen_width
                if new_width > 1:
                    new_height /= new_width
                new_width = screen_width

        pixbuf = GdkPixbuf.Pixbuf.new_from_file(filename)
        scaled_buf = pixbuf.scale_simple(
            new_width, new_height, GdkPixbuf.InterpType.BILINEAR)
        self.image.set_from_pixbuf(scaled_buf)
        self.image.show()

    def save_extracted_file(self, zipfile, filename):
        "Extract the file to a temp directory for viewing"
        try:
            filebytes = zipfile.read(filename)
        except BadZipfile as err:
            print('Error opening the zip file: {}'.format(err))
            return False
        except KeyError as err:
            self._alert('Key Error', 'Zipfile key not found: ' + str(filename))
            return
        outfn = self.make_new_filename(filename)
        if (outfn == ''):
            return False
        fname = os.path.join(self.get_activity_root(), 'instance', outfn)
        with open(fname, 'wb') as f:
            f.write(filebytes)

        return True

    def extract_pickle_file(self):
        "Extract the pickle file to an instance directory for viewing"
        try:
            self.zf.getinfo('annotations.pkl')
            filebytes = self.zf.read('annotations.pkl')
            f = open(self.pickle_file_temp, 'wb')
            try:
                f.write(filebytes)
            finally:
                f.close()
            return True
        except KeyError:
            return False

    def read_file(self, file_path):
        """Load a file from the datastore on activity start"""
        self.get_saved_page_number()
        self._load_document(os.path.abspath(self.activity_zip.filename))

    def __delete_event_cb(self, widget, event):
        os.remove(self.temp_filename)
        return False

    def make_new_filename(self, filename):
        partition_tuple = filename.rpartition('/')
        return partition_tuple[2]

    def get_saved_page_number(self):
        title = self.metadata.get('title', '')
        if not title[len(title) - 1].isdigit():
            self.page = 0
        else:
            i = len(title) - 1
            page = ''
            while (title[i].isdigit() and i > 0):
                page = title[i] + page
                i = i - 1
            if title[i] == 'P':
                self.page = int(page) - 1
            else:
                # not a page number; maybe a volume number.
                self.page = 0

    def save_page_number(self):
        title = self.metadata.get('title', '')
        if not title[len(title) - 1].isdigit():
            title = title + ' P' + str(self.page + 1)
        else:
            i = len(title) - 1
            while (title[i].isdigit() and i > 0):
                i = i - 1
            if title[i] == 'P':
                title = title[0:i] + 'P' + str(self.page + 1)
            else:
                title = title + ' P' + str(self.page + 1)
        self.metadata['title'] = title

    def _load_document(self, file_path):
        "Read the Zip file containing the images"
        if zipfile.is_zipfile(file_path):
            self.zf = zipfile.ZipFile(file_path, 'r')
            self.image_files = self.zf.namelist()
            self.image_files.sort()
            self.ls_left.clear()
            i = 0
            while i < len(self.image_files):
                newfn = self.make_new_filename(self.image_files[i])
                selected_iter = self.ls_left.append()
                self.ls_left.set(
                    selected_iter,
                    COLUMN_IMAGE,
                    self.image_files[i],
                    COLUMN_OLD_NAME,
                    self.image_files[i])
                i += 1

            self.extract_pickle_file()
            self.annotations.restore()
            self.show_page(self.page)
            self.set_total_pages(len(self.image_files))
            self.set_current_page(self.page)
            if self.is_received_document:
                self.metadata['title'] = self.annotations.get_title()
                self.metadata['title_set_by_user'] = '1'
        else:
            print('Not a zipfile', file_path)
            self.activity_zip = None

    def write_file(self, file_path):
        "Save meta data for the file."
        if not os.path.exists(self.activity_zip.filename):
            zf = zipfile.ZipFile(self.activity_zip, 'w')
            zf.writestr("filler.txt", "filler")
            zf.close()

        self.save_page_number()
        self.metadata['activity'] = self.get_bundle_id()
        self.metadata['mime_type'] = 'application/x-cbz'

        if self._close_requested:
            textbuffer = self.annotation_textview.get_buffer()
            if self.annotations.add_note(
                self.page,
                textbuffer.get_text(
                    textbuffer.get_start_iter(),
                    textbuffer.get_end_iter(),
                    include_hidden_chars=True)):
                self.annotations_dirty = True
            title = self.metadata.get('title', '')
            self.annotations.set_title(str(title))
            self.annotations.save()
            self.final_rewrite_zip()
            os.link(
                os.path.abspath(self.activity_zip.filename), file_path)
            os.unlink(os.path.abspath(self.activity_zip.filename))
            os.remove(self.pickle_file_temp)
            self.activity_zip = None
            self.pickle_file_temp = None

    def can_close(self):
        self._close_requested = True
        return True

    # The code from here on down is for sharing.
    def set_downloaded_bytes(self, bytes, total):
        _logger.debug("Downloaded {} of {} bytes...".format(
                      bytes_downloaded,
                      fsize,
                      ))
        fraction = float(bytes) / float(total)
        self.progressbar.set_fraction(fraction)

    def clear_downloaded_bytes(self):
        self.progressbar.set_fraction(0.0)

    def _download_complete_cb(self, fpath):
        _logger.debug(
            "Saving file {} to datastore...".format(
            os.path.basename(fpath)))
        self._jobject.file_path = fpath
        datastore.write(self._jobject, transfer_ownership=True)

        self._load_document(fpath)
        self.save()
        self.progressbar.hide()
        self.clear_downloaded_bytes()

    def _buddy_joined_cb(self, collab, buddy):
        self._want_document = True
        if buddy.nick in self.buddies.keys():
            return
        self.buddies[buddy.nick] = buddy

        GLib.idle_add(self._share_document, buddy)

    def _buddy_left_cb(self, collab, buddy):
        if buddy.nick not in self.buddies.keys():
            return
        del self.buddies[buddy.nick]

    def _message_cb(self, collab, buddy, message):
        action = message.get('action')

        if action == 'add-image':
            self.add_image()
        if action == 'remove-image':
            self.remove_image()
        if action == 'reload':
            self.reload_journal_table()

    def _incoming_file_cb(self, collab, incoming_file, description):
        _logger.debug("Accepting incoming file")
        if not self._want_document:
            return False

        # Assign a file path to write incoming file to
        fpath = os.path.join(self.get_activity_root(), 'instance',
                             'activity-files')

        # Make sure the receiver has enough space on their
        # computer to receive the sent file
        def diskfree(path):
            return os.statvfs(path).f_bsize * os.statvfs(path).f_bavail

        a_space = diskfree(os.path.dirname(fpath)) // (1024**2)

        if a_space > incoming_file.file_size:
            incoming_file.accept_to_file(fpath)
            incoming_file.ready.connect(ready_cb)
            transfer_complete = False

            while not transfer_complete:
                self.progressbar.show()
                self.set_downloaded_bytes(
                    incoming_file.props.transferred_bytes,
                    incoming_file.props.file_size)
        else:
            self._alert("No space on disk",
                        "Delete some files to create space")
            self._want_document = True
            incoming_file.cancel()

        def ready_cb(fpath):
            transfer_complete = True
            self._download_complete_cb(fpath)

        # Avoid trying to download the document multiple times at once
        self._want_document = False

    def _joined_cb(self, also_self):
        """Callback for when a shared activity is joined.

        Get the shared document from another participant.
        """
        _logger.debug('Activity has been joined')

    def _share_document(self, buddy):
        """Share the document."""
        if self._want_document:
            path = os.path.abspath(self.activity_zip.filename)
            self.collab.send_file_file(
                buddy,
                path,
                "Share zip file to joined buddies")

    def _shared_cb(self, activityid):
        """Callback when activity shared."""
        # We initiated this activity and have now shared it, so by
        # definition we have the file.
        _logger.debug('Activity became shared')

    def _alert(self, title, text=None):
        alert = NotifyAlert(timeout=15)
        alert.props.title = title
        alert.props.msg = text
        self.add_alert(alert)
        alert.connect('response', self._alert_cancel_cb)
        alert.show()

    def _alert_cancel_cb(self, alert, response_id):
        self.remove_alert(alert)

    # From here down is power management stuff.

    def _now_active_cb(self, widget, pspec):
        if self.props.active:
            # Now active, start initial suspend timeout
            xopower.reset_sleep_timer()
            xopower.sleep_inhibit = False
        else:
            # Now inactive
            xopower.sleep_inhibit = True

    def _focus_in_event_cb(self, widget, event):
        xopower.turn_on_sleep_timer()

    def _focus_out_event_cb(self, widget, event):
        xopower.turn_off_sleep_timer()

    def _user_action_cb(self, widget):
        xopower.reset_sleep_timer()

    def _suspend_cb(self):
        xopower.suspend()
        return False
