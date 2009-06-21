#! /usr/bin/env python

# Copyright (C) 2008, 2009 James D. Simmons
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
import tempfile
import time
import zipfile
from zipfile import BadZipfile
import pygtk
import gtk
import string
import pygame, pygame.display
from pygame.locals import *
from sugar.activity import activity
from sugar import network
from sugar.datastore import datastore
from sugar.graphics.alert import NotifyAlert
from readtoolbar import ReadToolbar, ViewToolbar,  SlidesToolbar
from gettext import gettext as _
import dbus
import gobject
import telepathy
from decimal import *
import xopower

_TOOLBAR_READ = 1
_TOOLBAR_SLIDES = 3
COLUMN_IMAGE = 0
COLUMN_PATH = 1
COLUMN_OLD_NAME = 1

_logger = logging.getLogger('view-slides')

class ReadHTTPRequestHandler(network.ChunkedGlibHTTPRequestHandler):
    """HTTP Request Handler for transferring document while collaborating.

    RequestHandler class that integrates with Glib mainloop. It writes
    the specified file to the client in chunks, returning control to the
    mainloop between chunks.

    """
    def translate_path(self, path):
        """Return the filepath to the shared document."""
        return self.server.filepath


class ReadHTTPServer(network.GlibTCPServer):
    """HTTP Server for transferring document while collaborating."""
    def __init__(self, server_address, filepath):
        """Set up the GlibTCPServer with the ReadHTTPRequestHandler.

        filepath -- path to shared document to be served.
        """
        self.filepath = filepath
        network.GlibTCPServer.__init__(self, server_address,
                                       ReadHTTPRequestHandler)


class ReadURLDownloader(network.GlibURLDownloader):
    """URLDownloader that provides content-length and content-type."""

    def get_content_length(self):
        """Return the content-length of the download."""
        if self._info is not None:
            return int(self._info.headers.get('Content-Length'))

    def get_content_type(self):
        """Return the content-type of the download."""
        if self._info is not None:
            return self._info.headers.get('Content-type')
        return None


READ_STREAM_SERVICE = 'read-activity-http'

class ViewSlidesActivity(activity.Activity):
    def __init__(self, handle):
        "The entry point to the Activity"
        activity.Activity.__init__(self, handle)

        self._fileserver = None
        self._object_id = handle.object_id
        self.zoom_image_to_fit = True

        self.connect("expose_event", self.area_expose_cb)
        self.connect("delete_event", self.delete_cb)
        toolbox = activity.ActivityToolbox(self)
        activity_toolbar = toolbox.get_activity_toolbar()
        activity_toolbar.remove(activity_toolbar.keep)
        activity_toolbar.keep = None
        
        self._read_toolbar = ReadToolbar()
        toolbox.add_toolbar(_('Read'), self._read_toolbar)
        self._read_toolbar.show()

        self._view_toolbar = ViewToolbar()
        toolbox.add_toolbar(_('View'), self._view_toolbar)
        self._view_toolbar.set_activity(self)
        self._view_toolbar.connect('go-fullscreen',
                self.__view_toolbar_go_fullscreen_cb)
        self._view_toolbar.show()

        self._slides_toolbar = SlidesToolbar()
        toolbox.add_toolbar(_('Slides'), self._slides_toolbar)
        self._slides_toolbar.set_activity(self)
        self._slides_toolbar.show()

        self.set_toolbox(toolbox)
        toolbox.show()
        self.scrolled = gtk.ScrolledWindow()
        self.scrolled.set_policy(gtk.POLICY_NEVER, gtk.POLICY_AUTOMATIC)
        self.scrolled.props.shadow_type = gtk.SHADOW_NONE
        self.image = gtk.Image()
        self.eventbox = gtk.EventBox()
        self.eventbox.add(self.image)
        self.image.show()
        self.eventbox.show()
        self.scrolled.add_with_viewport(self.eventbox)
        self.eventbox.set_events(gtk.gdk.KEY_PRESS_MASK | gtk.gdk.BUTTON_PRESS_MASK)
        self.eventbox.set_flags(gtk.CAN_FOCUS)
        self.eventbox.connect("key_press_event", self.keypress_cb)
        self.eventbox.connect("button_press_event", self.buttonpress_cb)
 
        self.ls_left = gtk.ListStore(gobject.TYPE_STRING,  gobject.TYPE_STRING)
        tv_left = gtk.TreeView(self.ls_left)
        tv_left.set_rules_hint(True)
        tv_left.set_search_column(COLUMN_IMAGE)
        selection_left = tv_left.get_selection()
        selection_left.set_mode(gtk.SELECTION_SINGLE)
        selection_left.connect("changed", self.selection_left_cb)
        renderer = gtk.CellRendererText()
        col_left = gtk.TreeViewColumn('Slideshow Image', renderer, text=COLUMN_IMAGE)
        col_left.set_sort_column_id(COLUMN_IMAGE)
        renderer.set_property('editable',  True)
        renderer.connect('edited',  self.col_left_edited_cb,  self.ls_left)
        tv_left.append_column(col_left)

        self.list_scroller_left = gtk.ScrolledWindow(hadjustment=None, vadjustment=None)
        self.list_scroller_left.set_policy(gtk.POLICY_NEVER,  gtk.POLICY_AUTOMATIC)
        self.list_scroller_left.add(tv_left)

        self.ls_right = gtk.ListStore(gobject.TYPE_STRING,  gobject.TYPE_PYOBJECT)
        tv_right = gtk.TreeView(self.ls_right)
        tv_right.set_rules_hint(True)
        tv_right.set_search_column(COLUMN_IMAGE)
        selection_right = tv_right.get_selection()
        selection_right.set_mode(gtk.SELECTION_SINGLE)
        selection_right.connect("changed", self.selection_right_cb)
        renderer = gtk.CellRendererText()
        col_right = gtk.TreeViewColumn('Journal Image', renderer, text=COLUMN_IMAGE)
        col_right.set_sort_column_id(COLUMN_IMAGE)
        tv_right.append_column(col_right)
        
        self.list_scroller_right = gtk.ScrolledWindow(hadjustment=None, vadjustment=None)
        self.list_scroller_right.set_policy(gtk.POLICY_NEVER, gtk.POLICY_AUTOMATIC)
        self.list_scroller_right.add(tv_right)

        self.hpane = gtk.HPaned()
        self.hpane.add1(self.list_scroller_left)
        self.hpane.add2(self.list_scroller_right)
        
        vbox = gtk.VBox()
        vbox.add(self.scrolled)
        vbox.add(self.hpane)

        self.set_canvas(vbox)
        self.scrolled.show()
        tv_left.show()
        self.list_scroller_left.show()
        tv_right.show()
        self.list_scroller_right.show()
        self.hpane.show()
        vbox.show()
        self.hpane.hide()
        
        self.is_dirty = False

        self.load_journal_table()

        self.show_image("ViewSlides.jpg")
        self._read_toolbar.set_activity(self)
        self.page = 0
        self.temp_filename = ''
        self.saved_screen_width = 0
        self.eventbox.grab_focus()
        
        pixmap = gtk.gdk.Pixmap(None, 1, 1, 1)
        color = gtk.gdk.Color()
        self.hidden_cursor = gtk.gdk.Cursor(pixmap, pixmap, color, color, 0, 0)
        self.cursor_visible = True

        xopower.setup_idle_timeout()
        if xopower.service_activated:
            self.scrolled.props.vadjustment.connect("value-changed", self._user_action_cb)
            self.scrolled.props.hadjustment.connect("value-changed", self._user_action_cb)
            self.connect("focus-in-event", self._focus_in_event_cb)
            self.connect("focus-out-event", self._focus_out_event_cb)
            self.connect("notify::active", self._now_active_cb)

        # start on the read toolbar
        self.toolbox.set_current_toolbar(_TOOLBAR_READ)
        self.unused_download_tubes = set()
        self._want_document = True
        self._download_content_length = 0
        self._download_content_type = None
       # Status of temp file used for write_file:
        self._tempfile = None
        self._close_requested = False
        self.connect("shared", self._shared_cb)
        h = hash(self._activity_id)
        self.port = 1024 + (h % 64511)

        self.is_received_document = False
        self.selected_journal_entry = None
        self.selection_left = None
        
        if self._shared_activity and handle.object_id == None:
            # We're joining, and we don't already have the document.
            if self.get_shared():
                # Already joined for some reason, just get the document
                self._joined_cb(self)
            else:
                # Wait for a successful join before trying to get the document
                self.connect("joined", self._joined_cb)
        else:
            # Assign a file path to create if one doesn't exist yet
            if handle.object_id == None:
                self._tempfile = os.path.join(self.get_activity_root(), 'instance',
                                    'tmp%i' % time.time())
                self.toolbox.set_current_toolbar(_TOOLBAR_SLIDES)
                self.show_image_tables(True)

    def load_journal_table(self):
        ds_objects, num_objects = datastore.find({'mime_type':['image/jpeg',  'image/gif', 'image/tiff',  \
            'image/png']},  'title')
        for i in xrange (0, num_objects, 1):
            iter = self.ls_right.append()
            self.ls_right.set(iter, COLUMN_IMAGE, ds_objects[i].metadata['title'])
            self.ls_right.set(iter,  COLUMN_PATH,  ds_objects[i])

    def col_left_edited_cb(self, cell,  path,  new_text,  user_data):
        liststore = user_data
        if self.check_for_duplicates(new_text)  == True:
            self._alert("Duplicate Filename",  'File ' + str(new_text) + ' already exists in slideshow!')
            return
        liststore[path][COLUMN_IMAGE] = new_text
        self.is_dirty = True
        return
    
    def  show_image_tables(self,  state):
        if state == True:
            self.hpane.show()
        else:
            self.hpane.hide()
            self.rewrite_zip()
            self.set_current_page(0)
            self._load_document(self._tempfile)

    def selection_left_cb(self, selection):
        tv = selection.get_tree_view()
        model = tv.get_model()
        self.selection_left = selection.get_selected()
        if self.selection_left:
            model, iter = self.selection_left
            selected_file = model.get_value(iter, COLUMN_OLD_NAME)
            zf = zipfile.ZipFile(self._tempfile, 'r')
            if self.save_extracted_file(zf, selected_file) == True:
                fname = "/tmp/" + self.make_new_filename(selected_file)
                self.show_image(fname)
                os.remove(fname)
            self._slides_toolbar._remove_image.props.sensitive = True

    def selection_right_cb(self, selection):
        tv = selection.get_tree_view()
        model = tv.get_model()
        sel = selection.get_selected()
        if sel:
            model, iter = sel
            jobject = model.get_value(iter,COLUMN_PATH)
            fname = jobject.get_file_path()
            self.show_image(fname)
            self._slides_toolbar._add_image.props.sensitive = True
            self.selected_journal_entry = jobject

    def add_image(self):
        if self.selected_journal_entry == None:
            return
        selected_file = self.selected_journal_entry.get_file_path()
        arcname = os.path.basename(selected_file)
        if self.check_for_duplicates(arcname)  == True:
            self._alert("Duplicate Filename",  'File ' + str(arcname) + ' already exists in slideshow!')
            return
        try:
            if os.path.exists(self._tempfile):
                zf = zipfile.ZipFile(self._tempfile, 'a')
            else:
                zf = zipfile.ZipFile(self._tempfile, 'w')
            zf.write(selected_file.encode( "utf-8" ),  arcname.encode( "utf-8" ))
            zf.close()
            iter = self.ls_left.append()
            self.ls_left.set(iter, COLUMN_IMAGE, arcname,  COLUMN_OLD_NAME,  arcname)
            self._slides_toolbar._add_image.props.sensitive = False
        except BadZipfile, err:
            print 'Error opening the zip file: %s' % (err)
            self._alert('Error', 'Error opening the zip file')
    
    def remove_image(self):
        if self.selection_left:
            model, iter = self.selection_left
            self.ls_left.remove(iter)
            self._slides_toolbar._remove_image.props.sensitive = True
            self.is_dirty = True

    def check_for_duplicates(self,  filename):
        for row in self.ls_left:
            if row [COLUMN_OLD_NAME] == filename:
                return True
            if row [COLUMN_IMAGE] == filename:
                return True
        return False
 
    def rewrite_zip(self):
        if self.is_dirty == False:
            return
        new_zipfile = os.path.join(self.get_activity_root(), 'instance',
                'rewrite%i' % time.time())
        print self._tempfile,  new_zipfile
        zf_new = zipfile.ZipFile(new_zipfile, 'w')
        zf_old = zipfile.ZipFile(self._tempfile, 'r')
        for row in self.ls_left:
            copied_file = row [COLUMN_OLD_NAME]
            new_file = row[COLUMN_IMAGE]
            if self.save_extracted_file(zf_old, copied_file) == True:
                outfn = self.make_new_filename(copied_file)
                fname = "/tmp/" + outfn
                zf_new.write(fname.encode( "utf-8" ),  new_file.encode( "utf-8" ))
                print 'rewriting',  new_file
                os.remove(fname)
        zf_old.close()
        zf_new.close()
        os.remove(self._tempfile)
        self._tempfile = new_zipfile
        self.is_dirty = False

    def buttonpress_cb(self, widget, event):
        widget.grab_focus()

    def __view_toolbar_go_fullscreen_cb(self, view_toolbar):
        self.fullscreen()

    def zoom_to_width(self):
        self.zoom_image_to_fit = False
        self.show_page(self.page)

    def zoom_to_fit(self):
        self.zoom_image_to_fit = True
        self.show_page(self.page)

    def keypress_cb(self, widget, event):
        "Respond when the user presses Escape or one of the arrow keys"
        if xopower.service_activated:
            xopower.reset_sleep_timer()
        keyname = gtk.gdk.keyval_name(event.keyval)
        if keyname == 'Page_Up':
            self.previous_page()
            return True
        if keyname == 'Page_Down' :
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
        if keyname == 'KP_Home':
            if self.cursor_visible:
                self.window.set_cursor(self.hidden_cursor)
                self.cursor_visible = False
            else:
                self.window.set_cursor(None)
                self.cursor_visible = True
            return True
        if keyname == 'plus':
            self._view_toolbar.enable_zoom_out()
            self.zoom_to_width()
            return True
        if keyname == 'minus':
            self._view_toolbar.enable_zoom_in()
            self.zoom_to_fit()
            return True
        return False

    def scroll_down(self):
        v_adjustment = self.scrolled.get_vadjustment()
        if v_adjustment.value == v_adjustment.upper - v_adjustment.page_size:
            self.next_page()
            return
        if v_adjustment.value < v_adjustment.upper - v_adjustment.page_size:
            new_value = v_adjustment.value + v_adjustment.step_increment
            if new_value > v_adjustment.upper - v_adjustment.page_size:
                new_value = v_adjustment.upper - v_adjustment.page_size
            v_adjustment.value = new_value

    def scroll_up(self):
        v_adjustment = self.scrolled.get_vadjustment()
        if v_adjustment.value == v_adjustment.lower:
            self.previous_page()
            return
        if v_adjustment.value > v_adjustment.lower:
            new_value = v_adjustment.value - v_adjustment.step_increment
            if new_value < v_adjustment.lower:
                new_value = v_adjustment.lower
            v_adjustment.value = new_value

    def previous_page(self):
        page = self.page
        page=page-1
        if page < 0: page=0
        if self.save_extracted_file(self.zf, self.image_files[page]) == True:
            fname = "/tmp/" + self.make_new_filename(self.image_files[page])
            self.show_image(fname)
            os.remove(fname)
        v_adjustment = self.scrolled.get_vadjustment()
        v_adjustment.value = v_adjustment.upper - v_adjustment.page_size
        self._read_toolbar.set_current_page(page)
        self.page = page

    def set_current_page(self, page):
        self.page = page

    def next_page(self):
        page = self.page
        page = page + 1
        if page >= len(self.image_files): page=len(self.image_files) - 1
        if self.save_extracted_file(self.zf, self.image_files[page]) == True:
            fname = "/tmp/" + self.make_new_filename(self.image_files[page])
            self.show_image(fname)
            os.remove(fname)
        v_adjustment = self.scrolled.get_vadjustment()
        v_adjustment.value = v_adjustment.lower
        self._read_toolbar.set_current_page(page)
        self.page = page

    def area_expose_cb(self, area, event):
        screen_width = gtk.gdk.screen_width()
        screen_height = gtk.gdk.screen_height()
        if self.saved_screen_width != screen_width and self.saved_screen_width != 0:
            self.show_page(self.page)
        self.saved_screen_width = screen_width
        return False

    def show_page(self, page):
        if self.save_extracted_file(self.zf, self.image_files[page]) == True:
            fname = "/tmp/" + self.make_new_filename(self.image_files[page])
            self.show_image(fname)
            os.remove(fname)
        
    def show_image(self, filename):
        "display a resized image in a full screen window"
        TOOLBOX_HEIGHT = 100
        # get the size of the fullscreen display
        screen_width = gtk.gdk.screen_width()
        screen_height = gtk.gdk.screen_height()
        screen_height = screen_height - TOOLBOX_HEIGHT
        # get the size of the image.
        im = pygame.image.load(filename)
        image_width, image_height = im.get_size()
        getcontext().prec = 7
        s_a_ratio = Decimal(screen_height) / Decimal(screen_width)
        i_a_ratio = Decimal(image_height) / Decimal(image_width)
        new_width = image_width
        new_height = image_height
        if self.zoom_image_to_fit == True:
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
        
        pixbuf = gtk.gdk.pixbuf_new_from_file(filename)
        scaled_buf = pixbuf.scale_simple(new_width, new_height, gtk.gdk.INTERP_BILINEAR)
        self.image.set_from_pixbuf(scaled_buf)
        self.image.show()
 
    def save_extracted_file(self, zipfile, filename):
        "Extract the file to a temp directory for viewing"
        try:
            filebytes = zipfile.read(filename)
        except BadZipfile, err:
            print 'Error opening the zip file: %s' % (err)
            # self._alert('Error', 'Error opening the zip file')
            return False
        except KeyError,  err:
            self._alert('Key Error', 'Zipfile key not found: '  + str(filename))
            return
        outfn = self.make_new_filename(filename)
        if (outfn == ''):
            return False
        f = open("/tmp/" + outfn, 'w')
        try:
            f.write(filebytes)
        finally:
            f.close
        return True

    def read_file(self, file_path):
        """Load a file from the datastore on activity start"""
        tempfile = os.path.join(self.get_activity_root(),  'instance', 'tmp%i' % time.time())
        os.link(file_path,  tempfile)
        self._tempfile = tempfile
        self.get_saved_page_number()
        self._load_document(self._tempfile)

    def delete_cb(self, widget, event):
        os.remove(self.temp_filename)
        print 'deleted file', self.temp_filename
        return False

    def make_new_filename(self, filename):
        partition_tuple = filename.rpartition('/')
        return partition_tuple[2]
    
    def get_saved_page_number(self):
        title = self.metadata.get('title', '')
        if not title[len(title)- 1].isdigit():
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
        if not title[len(title)- 1].isdigit():
            title = title + ' P' +  str(self.page + 1)
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
            i = 0
            valid_endings = ('.jpg', '.JPG', '.gif', '.GIF', '.tiff', '.TIFF', '.png', '.PNG')
            self.ls_left.clear()
            while i < len(self.image_files):
                newfn = self.make_new_filename(self.image_files[i])
                if newfn.endswith(valid_endings):
                    iter = self.ls_left.append()
                    self.ls_left.set(iter, COLUMN_IMAGE, self.image_files[i],  COLUMN_OLD_NAME,  self.image_files[i])
                    i = i + 1
                else:   
                    del self.image_files[i]
            self.save_extracted_file(self.zf, self.image_files[self.page])
            currentFileName = "/tmp/" + self.make_new_filename(self.image_files[self.page])
            self.show_image(currentFileName)
            os.remove(currentFileName)
            self._read_toolbar.set_total_pages(len(self.image_files))
            self._read_toolbar.set_current_page(self.page)
            # We've got the document, so if we're a shared activity, offer it
            if self.get_shared():
                self.watch_for_tubes()
                self._share_document()
        else:
            print 'Not a zipfile',  file_path
            # self._alert('Invalid', 'Not a zipfile: '  + file_path)

    def write_file(self, file_path):
        "Save meta data for the file."
        if not os.path.exists(self._tempfile):
            zf = zipfile.ZipFile(self._tempfile, 'w')
            zf.writestr("filler.txt", "filler")
            zf.close()

        self.save_page_number()
        self.metadata['activity'] = self.get_bundle_id()
        self.rewrite_zip()
        os.link(self._tempfile,  file_path)
 
        if self._close_requested:
            _logger.debug("Removing temp file %s because we will close", self._tempfile)
            os.unlink(self._tempfile)
            self._tempfile = None

    def can_close(self):
        self._close_requested = True
        return True
        
    # The code from here on down is for sharing.
    def _download_result_cb(self, getter, tempfile, suggested_name, tube_id):
        if self._download_content_type == 'text/html':
            # got an error page instead
            self._download_error_cb(getter, 'HTTP Error', tube_id)
            return

        del self.unused_download_tubes

        self._tempfile = tempfile
        file_path = os.path.join(self.get_activity_root(), 'instance',
                                    '%i' % time.time())
        _logger.debug("Saving file %s to datastore...", file_path)
        os.link(tempfile, file_path)
        self._jobject.file_path = file_path
        datastore.write(self._jobject, transfer_ownership=True)

        _logger.debug("Got document %s (%s) from tube %u",
                      tempfile, suggested_name, tube_id)
        self._load_document(tempfile)
        self.save()

    def _download_progress_cb(self, getter, bytes_downloaded, tube_id):
        if self._download_content_length > 0:
            _logger.debug("Downloaded %u of %u bytes from tube %u...",
                          bytes_downloaded, self._download_content_length, 
                          tube_id)
        else:
            _logger.debug("Downloaded %u bytes from tube %u...",
                          bytes_downloaded, tube_id)
        # total = getter._info.headers["Content-Length"]
        total = self._download_content_length
        self._read_toolbar.set_downloaded_bytes(bytes_downloaded,  total)
        while gtk.events_pending():
            gtk.main_iteration()

    def _download_error_cb(self, getter, err, tube_id):
        _logger.debug("Error getting document from tube %u: %s",
                      tube_id, err)
        self._alert('Failure', 'Error getting document from tube')
        self._want_document = True
        self._download_content_length = 0
        self._download_content_type = None
        gobject.idle_add(self._get_document)

    def _download_document(self, tube_id, path):
        # FIXME: should ideally have the CM listen on a Unix socket
        # instead of IPv4 (might be more compatible with Rainbow)
        chan = self._shared_activity.telepathy_tubes_chan
        iface = chan[telepathy.CHANNEL_TYPE_TUBES]
        addr = iface.AcceptStreamTube(tube_id,
                telepathy.SOCKET_ADDRESS_TYPE_IPV4,
                telepathy.SOCKET_ACCESS_CONTROL_LOCALHOST, 0,
                utf8_strings=True)
        _logger.debug('Accepted stream tube: listening address is %r', addr)
        # SOCKET_ADDRESS_TYPE_IPV4 is defined to have addresses of type '(sq)'
        assert isinstance(addr, dbus.Struct)
        assert len(addr) == 2
        assert isinstance(addr[0], str)
        assert isinstance(addr[1], (int, long))
        assert addr[1] > 0 and addr[1] < 65536
        port = int(addr[1])

        getter = ReadURLDownloader("http://%s:%d/document"
                                           % (addr[0], port))
        getter.connect("finished", self._download_result_cb, tube_id)
        getter.connect("progress", self._download_progress_cb, tube_id)
        getter.connect("error", self._download_error_cb, tube_id)
        _logger.debug("Starting download to %s...", path)
        getter.start(path)
        self._download_content_length = getter.get_content_length()
        self._download_content_type = getter.get_content_type()
        return False

    def _get_document(self):
        if not self._want_document:
            return False

        # Assign a file path to download if one doesn't exist yet
        if not self._jobject.file_path:
            path = os.path.join(self.get_activity_root(), 'instance',
                                'tmp%i' % time.time())
        else:
            path = self._jobject.file_path

        # Pick an arbitrary tube we can try to download the document from
        try:
            tube_id = self.unused_download_tubes.pop()
        except (ValueError, KeyError), e:
            _logger.debug('No tubes to get the document from right now: %s',
                          e)
            return False

        # Avoid trying to download the document multiple times at once
        self._want_document = False
        gobject.idle_add(self._download_document, tube_id, path)
        return False

    def _joined_cb(self, also_self):
        """Callback for when a shared activity is joined.

        Get the shared document from another participant.
        """
        self.watch_for_tubes()
        gobject.idle_add(self._get_document)

    def _share_document(self):
        """Share the document."""
        # FIXME: should ideally have the fileserver listen on a Unix socket
        # instead of IPv4 (might be more compatible with Rainbow)

        _logger.debug('Starting HTTP server on port %d', self.port)
        self._fileserver = ReadHTTPServer(("", self.port),
            self._tempfile)

        # Make a tube for it
        chan = self._shared_activity.telepathy_tubes_chan
        iface = chan[telepathy.CHANNEL_TYPE_TUBES]
        self._fileserver_tube_id = iface.OfferStreamTube(READ_STREAM_SERVICE,
                {},
                telepathy.SOCKET_ADDRESS_TYPE_IPV4,
                ('127.0.0.1', dbus.UInt16(self.port)),
                telepathy.SOCKET_ACCESS_CONTROL_LOCALHOST, 0)
 
    def watch_for_tubes(self):
        """Watch for new tubes."""
        tubes_chan = self._shared_activity.telepathy_tubes_chan

        tubes_chan[telepathy.CHANNEL_TYPE_TUBES].connect_to_signal('NewTube',
            self._new_tube_cb)
        tubes_chan[telepathy.CHANNEL_TYPE_TUBES].ListTubes(
            reply_handler=self._list_tubes_reply_cb,
            error_handler=self._list_tubes_error_cb)

    def _new_tube_cb(self, tube_id, initiator, tube_type, service, params,
                     state):
        """Callback when a new tube becomes available."""
        _logger.debug('New tube: ID=%d initator=%d type=%d service=%s '
                      'params=%r state=%d', tube_id, initiator, tube_type,
                      service, params, state)
        if service == READ_STREAM_SERVICE:
            _logger.debug('I could download from that tube')
            self.unused_download_tubes.add(tube_id)
            # if no download is in progress, let's fetch the document
            if self._want_document:
                gobject.idle_add(self._get_document)

    def _list_tubes_reply_cb(self, tubes):
        """Callback when new tubes are available."""
        for tube_info in tubes:
            self._new_tube_cb(*tube_info)

    def _list_tubes_error_cb(self, e):
        """Handle ListTubes error by logging."""
        _logger.error('ListTubes() failed: %s', e)

    def _shared_cb(self, activityid):
        """Callback when activity shared.

        Set up to share the document.

        """
        # We initiated this activity and have now shared it, so by
        # definition we have the file.
        _logger.debug('Activity became shared')
        self.watch_for_tubes()
        self._share_document()

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
        
