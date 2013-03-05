# Copyright (C) 2008, James Simmons.
# Copyright (C) 2012 Aneesh Dogra <lionaneesh@gmail.com>
# Adapted from code Copyright (C) Red Hat Inc.
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

import logging
from gettext import gettext as _
import re

from gi.repository import Gtk
from gi.repository import GObject

from sugar3.graphics.toolbutton import ToolButton
from sugar3.graphics.menuitem import MenuItem
from sugar3.graphics.toggletoolbutton import ToggleToolButton
from sugar3.activity import activity

class ReadToolbar(Gtk.Toolbar):
    __gtype_name__ = 'ReadToolbar'

    def __init__(self):
        Gtk.Toolbar.__init__(self)
        self.back = ToolButton('go-previous')
        self.back.set_tooltip(_('Back'))
        self.back.props.sensitive = False
        palette = self.back.get_palette()
        self.prev_page = MenuItem(text_label= _("Previous page"))
        palette.menu.append(self.prev_page) 
        self.prev_page.show_all()        
        self.prev_bookmark = MenuItem(text_label= _("Previous bookmark"))
        palette.menu.append(self.prev_bookmark) 
        self.prev_bookmark.show_all()
        self.back.connect('clicked', self.go_back_cb)
        self.prev_page.connect('activate', self.go_back_cb)
        self.prev_bookmark.connect('activate', self.prev_bookmark_activate_cb)
        self.insert(self.back, -1)
        self.back.show()

        self.forward = ToolButton('go-next')
        self.forward.set_tooltip(_('Forward'))
        self.forward.props.sensitive = False
        palette = self.forward.get_palette()
        self.next_page = MenuItem(text_label= _("Next page"))
        palette.menu.append(self.next_page) 
        self.next_page.show_all()        
        self.next_bookmark = MenuItem(text_label= _("Next bookmark"))
        palette.menu.append(self.next_bookmark) 
        self.next_bookmark.show_all()
        self.forward.connect('clicked', self.go_forward_cb)
        self.next_page.connect('activate', self.go_forward_cb)
        self.next_bookmark.connect('activate', self.next_bookmark_activate_cb)
        self.insert(self.forward, -1)
        self.forward.show()

        num_page_item = Gtk.ToolItem()

        self._num_page_entry = Gtk.Entry()
        self._num_page_entry.set_text('0')
        self._num_page_entry.set_alignment(1)
        self._num_page_entry.connect('insert-text',
                                     self._num_page_entry_insert_text_cb)
        self._num_page_entry.connect('activate',
                                     self._num_page_entry_activate_cb)

        self._num_page_entry.set_width_chars(4)

        num_page_item.add(self._num_page_entry)
        self._num_page_entry.show()

        self.insert(num_page_item, -1)
        num_page_item.show()

        total_page_item = Gtk.ToolItem()

        self._total_page_label = Gtk.Label()
        self._total_page_label.set_markup("<span foreground='#FFF' size='14000'></span>")

        self._total_page_label.set_text(' / 0')
        total_page_item.add(self._total_page_label)
        self._total_page_label.show()

        self.insert(total_page_item, -1)
        total_page_item.show()

        spacer = Gtk.SeparatorToolItem()
        self.insert(spacer, -1)
        spacer.show()
  
        bookmarkitem = Gtk.ToolItem()
        self.bookmarker = ToggleToolButton('emblem-favorite')
        self.bookmarker.set_tooltip(_('Toggle Bookmark'))
        self.bookmarker_handler_id = self.bookmarker.connect('clicked',
                                      self.bookmarker_clicked_cb)
  
        bookmarkitem.add(self.bookmarker)

        self.insert(bookmarkitem, -1)
        bookmarkitem.show_all()

    def _num_page_entry_insert_text_cb(self, entry, text, length, position):
        if not re.match('[0-9]', text):
            entry.emit_stop_by_name('insert-text')
            return True
        return False

    def _num_page_entry_activate_cb(self, entry):
        if entry.props.text:
            page = int(entry.props.text) - 1
        else:
            page = 0

        if page >= self.total_pages:
            page = self.total_pages - 1
        elif page < 0:
            page = 0

        self.current_page = page
        self.activity.set_current_page(page)
        self.activity.show_page(page)
        entry.props.text = str(page + 1)
        self._update_nav_buttons()
        
    def go_back_cb(self, button):
        self.activity.previous_page()
    
    def go_forward_cb(self, button):
        self.activity.next_page()
    
    def _update_nav_buttons(self):
        current_page = self.current_page
        self.back.props.sensitive = current_page > 0
        self.forward.props.sensitive = \
            current_page < self.total_pages - 1
        
        self._num_page_entry.props.text = str(current_page + 1)
        self._total_page_label.props.label = \
            ' / ' + str(self.total_pages)

    def set_total_pages(self, pages):
        self.total_pages = pages
        
    def set_current_page(self, page):
        self.current_page = page
        self._update_nav_buttons()
        
    def set_activity(self, activity):
        self.activity = activity

    def prev_bookmark_activate_cb(self, menuitem):
        self.activity.prev_bookmark()
 
    def next_bookmark_activate_cb(self, menuitem):
        self.activity.next_bookmark()
        
    def bookmarker_clicked_cb(self, button):
        self.activity.bookmarker_clicked(button)

    def setToggleButtonState(self,button,b,id):
        button.handler_block(id)
        button.set_active(b)
        button.handler_unblock(id)

    def update_bookmark_button(self,  state):
        self.setToggleButtonState(self.bookmarker,  state,  self.bookmarker_handler_id)

class ViewToolbar(Gtk.Toolbar):
    __gsignals__ = {
        'go-fullscreen': (GObject.SIGNAL_RUN_FIRST,
                          GObject.TYPE_NONE,
                          ([]))
    }

    def __init__(self):
        Gtk.Toolbar.__init__(self)
        self._zoom_out = ToolButton('zoom-out')
        self._zoom_out.set_tooltip(_('Zoom out'))
        self._zoom_out.connect('clicked', self._zoom_out_cb)
        self.insert(self._zoom_out, -1)
        self._zoom_out.props.sensitive = False
        self._zoom_out.show()

        self._zoom_in = ToolButton('zoom-in')
        self._zoom_in.set_tooltip(_('Zoom in'))
        self._zoom_in.connect('clicked', self._zoom_in_cb)
        self.insert(self._zoom_in, -1)
        self._zoom_in.props.sensitive = True
        self._zoom_in.show()

        spacer = Gtk.SeparatorToolItem()
        spacer.props.draw = False
        self.insert(spacer, -1)
        spacer.show()

        self._fullscreen = ToolButton('view-fullscreen')
        self._fullscreen.set_tooltip(_('Fullscreen'))
        self._fullscreen.connect('clicked', self._fullscreen_cb)
        self.insert(self._fullscreen, -1)
        self._fullscreen.show()

    def _zoom_in_cb(self, button):
        self._zoom_in.props.sensitive = False
        self._zoom_out.props.sensitive = True
        self.activity.zoom_to_width()
    
    def _zoom_out_cb(self, button):
        self._zoom_in.props.sensitive = True
        self._zoom_out.props.sensitive = False
        self.activity.zoom_to_fit()

    def enable_zoom_in(self):
        self._zoom_in.props.sensitive = True
        self._zoom_out.props.sensitive = False

    def enable_zoom_out(self):
        self._zoom_in.props.sensitive = False
        self._zoom_out.props.sensitive = True

    def set_activity(self, activity):
        self.activity = activity

    def _fullscreen_cb(self, button):
        self.emit('go-fullscreen')

class SlidesToolbar(Gtk.Toolbar):
    __gtype_name__ = 'SlidesToolbar'

    def __init__(self):
        Gtk.Toolbar.__init__(self)
        self._show_image_tables = ToolButton('insert-image')
        self._show_image_tables.set_tooltip(_('Show Image Table'))
        self._show_image_tables.connect('clicked', self._show_image_tables_cb)
        self.insert(self._show_image_tables, -1)
        self._show_image_tables.show()

        self._reload_journal_table = ToolButton('reload')
        self._reload_journal_table.set_tooltip(_('Reload Journal Table'))
        self._reload_journal_table.connect('clicked', self._reload_journal_table_cb)
        self.insert(self._reload_journal_table, -1)
        self._reload_journal_table.props.sensitive = False
        self._reload_journal_table.show()

        self._hide_image_tables = ToolButton('dialog-cancel')
        self._hide_image_tables.set_tooltip(_('Hide Image Tables'))
        self._hide_image_tables.connect('clicked', self._hide_image_tables_cb)
        self.insert(self._hide_image_tables, -1)
        self._hide_image_tables.props.sensitive = False
        self._hide_image_tables.show()

        spacer = Gtk.SeparatorToolItem()
        spacer.props.draw = False
        self.insert(spacer, -1)
        spacer.show()

        self._add_image = ToolButton('list-add')
        self._add_image.set_tooltip(_('Add Image'))
        self._add_image.connect('clicked', self._add_image_cb)
        self.insert(self._add_image, -1)
        self._add_image.props.sensitive = False
        self._add_image.show()

        self._remove_image = ToolButton('list-remove')
        self._remove_image.set_tooltip(_('Remove Image'))
        self._remove_image.connect('clicked', self._remove_image_cb)
        self.insert(self._remove_image, -1)
        self._remove_image.props.sensitive = False
        self._remove_image.show()

        self.extract_image = ToolButton('gnome-mime-image')
        self.extract_image.set_tooltip(_('Extract Image'))
        self.extract_image.connect('clicked', self.extract_image_cb)
        self.insert(self.extract_image, -1)
        self.extract_image.props.sensitive = False
        self.extract_image.show()

    def set_activity(self, activity):
        self.activity = activity

    def _reload_journal_table_cb(self, button):
        self.activity.load_journal_table()

    def _add_image_cb(self, button):
        self.activity.add_image()
    
    def _remove_image_cb(self, button):
        self.activity.remove_image()
        
    def extract_image_cb(self, button):
        self.activity.extract_image()
        
    def _show_image_tables_cb(self,  button):
        self._hide_image_tables.props.sensitive = True
        self._reload_journal_table.props.sensitive = True
        self._show_image_tables.props.sensitive = False
        self.activity.show_image_tables(True)

    def _hide_image_tables_cb(self,  button):
        self._hide_image_tables.props.sensitive = False
        self._reload_journal_table.props.sensitive = False
        self._show_image_tables.props.sensitive = True
        self._add_image.props.sensitive = False
        self._remove_image.props.sensitive = False
        self.extract_image.props.sensitive = False
        self.activity.show_image_tables(False)
