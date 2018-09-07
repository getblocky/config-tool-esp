#!/usr/bin/env python

from __future__ import print_function
import wx
import wx.adv
import wx.lib.inspection
import wx.lib.mixins.inspection

import sys
import os
import esptool
import threading
import json
import images as images
from serial import SerialException
from serial.tools import list_ports
from esptool import ESPLoader
from esptool import NotImplementedInROMError
from argparse import Namespace

import platform
import posixpath
import re
import serial.serialutil

import dotenv


import ampy.files as files
import ampy.pyboard as pyboard



__version__ = "1.0"
__flash_help__ = '''
<p>This setting is highly dependent on your device!<p>
<p>
  Details at <a style="color: #004CE5;"
        href="https://www.esp32.com/viewtopic.php?p=5523&sid=08ef44e13610ecf2a2a33bb173b0fd5c#p5523">http://bit.ly/2v5Rd32</a>
  and in the <a style="color: #004CE5;" href="https://github.com/espressif/esptool/#flash-modes">esptool
  documentation</a>
<ul>
  <li>Most ESP32 and ESP8266 ESP-12 use DIO.</li>
  <li>Most ESP8266 ESP-01/07 use QIO.</li>
  <li>ESP8285 requires DOUT.</li>
</ul>
</p>
'''
__supported_baud_rates__ = [9600, 57600, 74880, 115200, 230400, 460800, 921600]

# ---------------------------------------------------------------------------

_board = None


def get_serial_ports():
    ports = [""]
    for port, desc, hwid in sorted(list_ports.comports()):
        ports.append(port)
    return ports

def windows_full_port_name(portname):
    # Helper function to generate proper Windows COM port paths.  Apparently
    # Windows requires COM ports above 9 to have a special path, where ports below
    # 9 are just referred to by COM1, COM2, etc. (wacky!)  See this post for
    # more info and where this code came from:
    # http://eli.thegreenplace.net/2009/07/31/listing-all-serial-ports-on-windows-with-python/
    m = re.match("^COM(\d+)$", portname)
    if m and int(m.group(1)) < 10:
        return portname
    else:
        return "\\\\.\\{0}".format(portname)


# See discussion at http://stackoverflow.com/q/41101897/131929
class RedirectText:
    def __init__(self, text_ctrl):
        self.__out = text_ctrl

    def write(self, string):
        if string.startswith("\r"):
            # carriage return -> remove last line i.e. reset position to start of last line
            current_value = self.__out.GetValue()
            last_newline = current_value.rfind("\n")
            new_value = current_value[:last_newline + 1]  # preserve \n
            new_value += string[1:]  # chop off leading \r
            wx.CallAfter(self.__out.SetValue, new_value)
        else:
            wx.CallAfter(self.__out.AppendText, string)

    # noinspection PyMethodMayBeStatic
    def flush(self):
        # noinspection PyStatementEffect
        None

# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
class FlashingThread(threading.Thread):
    def __init__(self, parent, config):
        threading.Thread.__init__(self)
        self.daemon = True
        self._parent = parent
        self._config = config

    def run(self):
        try:
            initial_baud = min(ESPLoader.ESP_ROM_BAUD, self._config.baud)

            esp = ESPLoader.detect_chip(self._config.port, initial_baud)
            print("Chip is %s" % (esp.get_chip_description()))

            esp = esp.run_stub()

            if self._config.baud > initial_baud:
                try:
                    esp.change_baud(self._config.baud)
                except NotImplementedInROMError:
                    print("WARNING: ROM doesn't support changing baud rate. Keeping initial baud rate %d." %
                          initial_baud)

            args = Namespace()
            args.flash_size = "detect"
            args.flash_mode = self._config.mode
            args.flash_freq = "40m"
            args.no_progress = False
            args.no_stub = False
            args.verify = False  # TRUE is deprecated
            args.compress = True
            args.addr_filename = [[int("0x00000", 0), open(self._config.firmware_path, 'rb')]]

            print("Configuring flash size...")
            esptool.detect_flash_size(esp, args)
            esp.flash_set_parameters(esptool.flash_size_bytes(args.flash_size))

            if self._config.erase_before_flash:
                esptool.erase_flash(esp, args)
            esptool.write_flash(esp, args)
            # The last line printed by esptool is "Leaving..." -> some indication that the process is done is needed
            print("\nDone.")
        except SerialException as e:
            self._parent.report_error(e.strerror)
            raise e

# ---------------------------------------------------------------------------

class SaveConfigThread(threading.Thread):
    def __init__(self, parent, config):
        threading.Thread.__init__(self)
        self.daemon = True
        self._parent = parent
        self._config = config

    def run(self):
        global _board
        try:
            initial_baud = min(ESPLoader.ESP_ROM_BAUD, self._config['baud'])

            #esp = ESPLoader.detect_chip(self._config['port'], initial_baud)
            #print("Chip is %s" % (esp.get_chip_description()))

            #esp = esp.run_stub()

            if self._config['baud'] > initial_baud:
                try:
                    esp.change_baud(self._config['baud'])
                except NotImplementedInROMError:
                    print("WARNING: ROM doesn't support changing baud rate. Keeping initial baud rate %d." %
                          initial_baud)

            # On Windows fix the COM port path name for ports above 9 (see comment in
            # windows_full_port_name function).
            port = self._config['port']
            if platform.system() == "Windows":
                port = windows_full_port_name(port)
            try:
                # create config file
                print('Generating config file')
                local_file = './config.json'
                config_json = {
                    'known_networks': [
                        {'ssid': self._config['wifi_name'], 'password': self._config['wifi_pass']},
                    ], 
                    'device_name': 'blocky_111', 
                    'auth_key': self._config['device_key']}

                with open(local_file, 'w') as outfile:
                    json.dump(config_json, outfile)

                print('Done')
                print('Sending to board...')
                
                _board = pyboard.Pyboard(port, baudrate=initial_baud, rawdelay=0)
            
                # Use the local filename
                remote = 'config.json'

                # File copy, open the file and copy its contents to the board.
                # Put the file on the board.
                with open(local_file, "rb") as infile:
                    board_files = files.Files(_board)
                    board_files.put(remote, infile.read())
            except pyboard.PyboardError as err:
                print('Error detected')
                print(err)
            # The last line printed by esptool is "Leaving..." -> some indication that the process is done is needed
            print("\nDone.")
        except Exception as e:
            print('Error detected')
            print(e)
            #self._parent.report_error(e.strerror)
            #raise e
        _board.close()
        _board = None

# ---------------------------------------------------------------------------
# DTO between GUI and flashing thread
class FlashConfig:
    def __init__(self):
        self.baud = 115200
        self.erase_before_flash = False
        self.mode = "dio"
        self.firmware_path = None
        self.port = None

    @classmethod
    def load(cls, file_path):
        conf = cls()
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                data = json.load(f)
            conf.port = data['port']
            conf.baud = data['baud']
            conf.mode = data['mode']
            conf.erase_before_flash = data['erase']
        return conf

    def safe(self, file_path):
        data = {
            'port': self.port,
            'baud': self.baud,
            'mode': self.mode,
            'erase': self.erase_before_flash,
        }
        with open(file_path, 'w') as f:
            json.dump(data, f)

    def is_complete(self):
        return self.firmware_path is not None and self.port is not None

# ---------------------------------------------------------------------------
class BlockConfigFrame(wx.Frame):

    def __init__(self, parent, title):
        wx.Frame.__init__(self, parent, -1, title, size=(700, 650),
                          style=wx.DEFAULT_FRAME_STYLE | wx.NO_FULL_REPAINT_ON_RESIZE)
        
        self._build_status_bar()
        self._set_icons()
        self._build_menu_bar()
        

        # Create a panel and notebook (tabs holder)
        p = wx.Panel(self)
        self.nb = wx.Notebook(p)
 
        # Create the tab windows
        saveConfigTab = TabSaveConfig(self.nb)
        flashFirmwareTab = TabFlashFirmware(self.nb)
 
        # Add the windows to tabs and name them.
        self.nb.AddPage(saveConfigTab, "Generate Config")
        self.nb.AddPage(flashFirmwareTab, "Flash Firmware")

        def on_tab_changed(event):
            tab = self.nb.GetPage(event.GetSelection())
            sys.stdout = RedirectText(tab.console_ctrl)
        
        self.nb.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, on_tab_changed) 
 
        # Set noteboook in a sizer to create the layout
        sizer = wx.BoxSizer()
        sizer.Add(self.nb, 1, wx.EXPAND)
        p.SetSizer(sizer)

        self.SetMinSize((400, 480))
        self.Centre(wx.BOTH)
        self.Show(True)

        sys.stdout = RedirectText(saveConfigTab.console_ctrl)

    def _set_icons(self):
        self.SetIcon(images.Icon.GetIcon())

    def _build_status_bar(self):
        self.statusBar = self.CreateStatusBar(2, wx.STB_SIZEGRIP)
        self.statusBar.SetStatusWidths([-2, -1])
        status_text = "Blocky Config Tool %s" % __version__
        self.statusBar.SetStatusText(status_text, 0)

    def _build_menu_bar(self):
        self.menuBar = wx.MenuBar()

        # File menu
        file_menu = wx.Menu()
        wx.App.SetMacExitMenuItemId(wx.ID_EXIT)
        exit_item = file_menu.Append(wx.ID_EXIT, "E&xit\tCtrl-Q", "Exit Blocky Config Tool")
        exit_item.SetBitmap(images.Exit.GetBitmap())
        self.Bind(wx.EVT_MENU, self._on_exit_app, exit_item)
        self.menuBar.Append(file_menu, "&File")

        # Help menu
        help_menu = wx.Menu()
        help_item = help_menu.Append(wx.ID_ABOUT, '&About', 'About')
        self.Bind(wx.EVT_MENU, self._on_help_about, help_item)
        self.menuBar.Append(help_menu, '&Help')

        self.SetMenuBar(self.menuBar)

    # Menu methods
    def _on_exit_app(self, event):
        self.Close(True)

    def _on_help_about(self, event):
        from About import AboutDlg
        about = AboutDlg(self)
        about.ShowModal()
        about.Destroy()

# ---------------------------------------------------------------------------
class TabSaveConfig(wx.Panel):
    def __init__(self, parent):

        self._config = {}

        wx.Panel.__init__(self, parent)
        
        hbox = wx.BoxSizer(wx.HORIZONTAL)

        fgs = wx.FlexGridSizer(6, 2, 10, 10)

        port_label = wx.StaticText(self, label="Serial port")

        self.ports = wx.Choice(self, choices=get_serial_ports())
        bmp = images.Reload.GetBitmap()
        reload_button = wx.BitmapButton(self, id=wx.ID_ANY, bitmap=bmp,
                                        size=(bmp.GetWidth() + 7, bmp.GetHeight() + 7))
        reload_button.Bind(wx.EVT_BUTTON, self.on_reload)
        reload_button.SetToolTip("Reload serial device list")

        serial_boxsizer = wx.BoxSizer(wx.HORIZONTAL)
        serial_boxsizer.Add(self.ports, 1,  wx.EXPAND)
        serial_boxsizer.AddStretchSpacer(0)
        serial_boxsizer.Add(reload_button, 0, wx.ALIGN_RIGHT, 20)


        wifi_name_label = wx.StaticText(self, label="WiFi Name")
        wifi_pass_label = wx.StaticText(self, label="WiFi Password")
        device_key_label = wx.StaticText(self, label="Device Key")

        self.wifi_name_text = wx.TextCtrl(self)
        self.wifi_pass_text = wx.TextCtrl(self)
        self.device_key_text = wx.TextCtrl(self)


        button = wx.Button(self, -1, "Save Config")
        button.Bind(wx.EVT_BUTTON, self.on_clicked)

        console_label = wx.StaticText(self, label="Console")
        self.console_ctrl = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        self.console_ctrl.SetFont(wx.Font(13, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self.console_ctrl.SetBackgroundColour(wx.BLACK)
        self.console_ctrl.SetForegroundColour(wx.RED)
        self.console_ctrl.SetDefaultStyle(wx.TextAttr(wx.RED))

        fgs.AddMany([
            port_label, (serial_boxsizer, 1, wx.EXPAND),
            wifi_name_label, (self.wifi_name_text, 1, wx.EXPAND),
            wifi_pass_label, (self.wifi_pass_text, 1, wx.EXPAND),
            device_key_label, (self.device_key_text, 1, wx.EXPAND),
            (wx.StaticText(self, label="")), (button, 1, wx.EXPAND),
            (console_label, 1, wx.EXPAND), (self.console_ctrl, 1, wx.EXPAND)])
        
        fgs.AddGrowableRow(5, 1)
        fgs.AddGrowableCol(1, 1)
        
        hbox.Add(fgs, proportion=2, flag=wx.ALL | wx.EXPAND, border=15)
        
        self.SetSizer(hbox)

    def on_reload(self, event):
        self.ports.SetItems(get_serial_ports())

    def report_error(self, message):
        self.console_ctrl.SetValue(message)

    def log_message(self, message):
        self.console_ctrl.AppendText(message)

    def on_clicked(self, event):
        self.console_ctrl.SetValue("")
        self.config = {
          'port': self.ports.GetString(self.ports.GetSelection()),
          'baud': 115200,
          'wifi_name': self.wifi_name_text.GetValue(),
          'wifi_pass': self.wifi_pass_text.GetValue(),
          'device_key': self.device_key_text.GetValue()
        }

        worker = SaveConfigThread(self, self.config)
        worker.start()

# ---------------------------------------------------------------------------
class TabFlashFirmware(wx.Panel):
    def __init__(self, parent):
        wx.Panel.__init__(self, parent)
        
        self._config = FlashConfig.load('./config.cnf')

        self._init_ui()

        sys.stdout = RedirectText(self.console_ctrl)

    def _init_ui(self):
        def on_reload(event):
            self.choice.SetItems(self._get_serial_ports())

        def on_baud_changed(event):
            radio_button = event.GetEventObject()

            if radio_button.GetValue():
                self._config.baud = radio_button.rate

        def on_mode_changed(event):
            radio_button = event.GetEventObject()

            if radio_button.GetValue():
                self._config.mode = radio_button.mode

        def on_erase_changed(event):
            radio_button = event.GetEventObject()

            if radio_button.GetValue():
                self._config.erase_before_flash = radio_button.erase

        def on_clicked(event):
            self.console_ctrl.SetValue("")
            worker = FlashingThread(self, self._config)
            worker.start()

        def on_select_port(event):
            choice = event.GetEventObject()
            self._config.port = choice.GetString(choice.GetSelection())

        def on_pick_file(event):
            self._config.firmware_path = event.GetPath().replace("'", "")

        hbox = wx.BoxSizer(wx.HORIZONTAL)

        fgs = wx.FlexGridSizer(7, 2, 10, 10)

        self.choice = wx.Choice(self, choices=get_serial_ports())
        self.choice.Bind(wx.EVT_CHOICE, on_select_port)
        bmp = images.Reload.GetBitmap()
        reload_button = wx.BitmapButton(self, id=wx.ID_ANY, bitmap=bmp,
                                        size=(bmp.GetWidth() + 7, bmp.GetHeight() + 7))
        reload_button.Bind(wx.EVT_BUTTON, on_reload)
        reload_button.SetToolTip("Reload serial device list")

        file_picker = wx.FilePickerCtrl(self, style=wx.FLP_USE_TEXTCTRL)
        file_picker.Bind(wx.EVT_FILEPICKER_CHANGED, on_pick_file)

        serial_boxsizer = wx.BoxSizer(wx.HORIZONTAL)
        serial_boxsizer.Add(self.choice, 1,  wx.EXPAND)
        serial_boxsizer.AddStretchSpacer(0)
        serial_boxsizer.Add(reload_button, 0, wx.ALIGN_RIGHT, 20)

        baud_boxsizer = wx.BoxSizer(wx.HORIZONTAL)

        def add_baud_radio_button(sizer, index, baud_rate):
            style = wx.RB_GROUP if index == 0 else 0
            radio_button = wx.RadioButton(self, name="baud-%d" % baud_rate, label="%d" % baud_rate, style=style)
            radio_button.rate = baud_rate
            # sets default value
            radio_button.SetValue(baud_rate == self._config.baud)
            radio_button.Bind(wx.EVT_RADIOBUTTON, on_baud_changed)
            sizer.Add(radio_button)
            sizer.AddSpacer(10)

        for idx, rate in enumerate(__supported_baud_rates__):
            add_baud_radio_button(baud_boxsizer, idx, rate)

        flashmode_boxsizer = wx.BoxSizer(wx.HORIZONTAL)

        def add_flash_mode_radio_button(sizer, index, mode, label):
            style = wx.RB_GROUP if index == 0 else 0
            radio_button = wx.RadioButton(self, name="mode-%s" % mode, label="%s" % label, style=style)
            radio_button.Bind(wx.EVT_RADIOBUTTON, on_mode_changed)
            radio_button.mode = mode
            radio_button.SetValue(mode == self._config.mode)
            sizer.Add(radio_button)
            sizer.AddSpacer(10)

        add_flash_mode_radio_button(flashmode_boxsizer, 0, "qio", "Quad I/O (QIO)")
        add_flash_mode_radio_button(flashmode_boxsizer, 1, "dio", "Dual I/O (DIO)")
        add_flash_mode_radio_button(flashmode_boxsizer, 2, "dout", "Dual Output (DOUT)")

        erase_boxsizer = wx.BoxSizer(wx.HORIZONTAL)

        def add_erase_radio_button(sizer, index, erase_before_flash, label, value):
            style = wx.RB_GROUP if index == 0 else 0
            radio_button = wx.RadioButton(self, name="erase-%s" % erase_before_flash, label="%s" % label, style=style)
            radio_button.Bind(wx.EVT_RADIOBUTTON, on_erase_changed)
            radio_button.erase = erase_before_flash
            radio_button.SetValue(value)
            sizer.Add(radio_button)
            sizer.AddSpacer(10)

        erase = self._config.erase_before_flash
        add_erase_radio_button(erase_boxsizer, 0, False, "no", erase is False)
        add_erase_radio_button(erase_boxsizer, 1, True, "yes, wipes all data", erase is True)

        button = wx.Button(self, -1, "Flash NodeMCU")
        button.Bind(wx.EVT_BUTTON, on_clicked)

        self.console_ctrl = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        self.console_ctrl.SetFont(wx.Font(13, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self.console_ctrl.SetBackgroundColour(wx.BLACK)
        self.console_ctrl.SetForegroundColour(wx.RED)
        self.console_ctrl.SetDefaultStyle(wx.TextAttr(wx.RED))

        port_label = wx.StaticText(self, label="Serial port")
        file_label = wx.StaticText(self, label="Firmware File")
        baud_label = wx.StaticText(self, label="Baud rate")
        flashmode_label = wx.StaticText(self, label="Flash mode")

        def on_info_hover(event):
            from HtmlPopupTransientWindow import HtmlPopupTransientWindow
            win = HtmlPopupTransientWindow(self, wx.SIMPLE_BORDER, __flash_help__, "#FFB6C1", (410, 140))

            image = event.GetEventObject()
            image_position = image.ClientToScreen((0, 0))
            image_size = image.GetSize()
            win.Position(image_position, (0, image_size[1]))

            win.Popup()

        icon = wx.StaticBitmap(self, wx.ID_ANY, images.Info.GetBitmap())
        icon.Bind(wx.EVT_MOTION, on_info_hover)

        flashmode_label_boxsizer = wx.BoxSizer(wx.HORIZONTAL)
        flashmode_label_boxsizer.Add(flashmode_label, 1, wx.EXPAND)
        flashmode_label_boxsizer.AddStretchSpacer(0)
        flashmode_label_boxsizer.Add(icon, 0, wx.ALIGN_RIGHT, 20)

        erase_label = wx.StaticText(self, label="Erase flash")
        console_label = wx.StaticText(self, label="Console")

        fgs.AddMany([
                    port_label, (serial_boxsizer, 1, wx.EXPAND),
                    file_label, (file_picker, 1, wx.EXPAND),
                    baud_label, baud_boxsizer,
                    flashmode_label_boxsizer, flashmode_boxsizer,
                    erase_label, erase_boxsizer,
                    (wx.StaticText(self, label="")), (button, 1, wx.EXPAND),
                    (console_label, 1, wx.EXPAND), (self.console_ctrl, 1, wx.EXPAND)])
        fgs.AddGrowableRow(6, 1)
        fgs.AddGrowableCol(1, 1)
        hbox.Add(fgs, proportion=2, flag=wx.ALL | wx.EXPAND, border=15)
        self.SetSizer(hbox)

# ----------------------------------------------------------------------------
class App(wx.App, wx.lib.mixins.inspection.InspectionMixin):
    def OnInit(self):
        wx.SystemOptions.SetOption("mac.window-plain-transition", 1)
        self.SetAppName("Blocky Config Tool")

        frame = BlockConfigFrame(None, "Blocky Config Tool")
        frame.Show()

        return True


# ---------------------------------------------------------------------------
def main():
    app = App(False)
    app.MainLoop()
# ---------------------------------------------------------------------------


if __name__ == '__main__':
    __name__ = 'Main'
    main()

