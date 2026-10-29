#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, json, logging, sys, time
from pathlib import Path

import serial
from PyQt6.QtCore import pyqtSignal, QEvent, QProcess, Qt, QTimer
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
	QProgressBar, QPushButton, QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

import civ, styles

HERE = Path(__file__).resolve().parent
log = logging.getLogger('shack')		# set up in main by utils.setup_logging

HELP = '''
<h3>Radio</h3>
<p>Pick IC-705, RSPdx or Xiegu x6200 to open it, none to close it. Only one radio is open at a time; only its own controls are shown. A greyed-out one doesn't apply in the current mode (Bandwidth outside SSB, for example).</p>

<h3>Frequency</h3>
<p>Mouse wheel: 1 kHz steps, Ctrl+wheel: 10 kHz. Or type a value in MHz and press Enter. The radio's own changes don't overwrite what you're typing.</p>

<h3>RSPdx</h3>
<table cellspacing="4">
<tr><td><b>Mode</b></td><td>LSB, USB, AM, WBFM</td></tr>
<tr><td><b>Volume</b></td><td>0 to 1</td></tr>
<tr><td><b>IF reduction</b></td><td>20-59 dB; more = less gain</td></tr>
<tr><td><b>LNA reduction</b></td><td>0 = most gain; the top end depends on the band</td></tr>
<tr><td><b>Antenna</b></td><td>A, B or C</td></tr>
<tr><td><b>Bandwidth</b></td><td>SSB only. Narrow 300-2100 Hz, Normal 300-3100, Wide 200-3300, Digital 200-3000 (for decoders)</td></tr>
<tr><td><b>Overload</b></td><td>red while the RSPdx reports RF overload; the count is since it started</td></tr>
<tr><td><b>ADC</b></td><td>everything coming into the RSPdx. Red above -6 dBFS: raise IF or LNA reduction</td></tr>
<tr><td><b>Signal</b></td><td>level inside the passband, dBFS</td></tr>
</table>

<h3>Buttons (green = on)</h3>
<table cellspacing="4">
<tr><td><b>RF notch, DAB notch</b></td><td>the RSPdx's MW/FM broadcast and DAB band filters</td></tr>
<tr><td><b>HDR</b></td><td>high dynamic range mode, below 2 MHz only</td></tr>
<tr><td><b>Digital out</b></td><td>48 kHz 16-bit mono audio as UDP to 127.0.0.1:7355, for decode.py, xara-monitor, direwolf</td></tr>
<tr><td><b>Waterfall</b></td><td>web page at http://localhost:8700/ (opens a browser if none connects)</td></tr>
<tr><td><b>Noise blanker</b></td><td>SSB and AM. The dB box is the trigger level: lower catches more, and changing it turns the blanker on. /s is pulses blanked per second: about 1/s for the electric fence; dozens on plain noise means the level is too low</td></tr>
<tr><td><b>Noise reduction</b></td><td>RNNoise, a neural network trained on speech: takes the hiss and noise floor out from under voice. Like SDRconnect's NR it won't dig very weak signals out of heavy noise, and it can chop up music and CW, so turn it off for those (it's off in WBFM). Digital out (the decoders) gets the audio before it</td></tr>
<tr><td><b>Save</b></td><td>stores frequency, mode and settings as a bookmark, and starts here next time</td></tr>
</table>

<h3>Bookmarks (right)</h3>
<p>Double-click: tune there with its settings. Right-click: rename (empty = band label) or delete. Most used are at the top.</p>

<h3>IC-705, Xiegu x6200</h3>
<p>Frequency, mode, volume, AGC, Preamp, ATT, S-meter, transmit power and PTT. The PTT button keys the radio (click again to stop; it unkeys itself after 30 s), and TX/RX next to it shows what the radio reports, whoever keyed it. SWR is the radio's meter, read while it transmits (it keeps the last reading). Power is % of the maximum on the 705 and watts on the Xiegu (1-8 W over CI-V; set below 1 W on the radio it shows ?); drag the slider (it's sent when you let go), or click it and use the arrow keys. Noise blanker and Noise reduction switch the radio's own NB and NR on and off (the dB box is RSPdx only). TX shows red while transmitting. PTT port 4532 on the status bar is for xara (rigctld PTT); the radio is unkeyed whenever it is closed.</p>

<p><i>F1 opens this help.</i></p>
'''

'''-------------------------------------------------------------------
-------------------------------------------------------------------'''

def sunits(db):		# S9-relative dB -> S7 / S9+20
	return f'S{max(0, (db + 54) // 6)}' if db <= 0 else f'S9+{db}'

'''-------------------------------------------------------------------
-------------------------------------------------------------------'''

def lna_max(mhz):		# RSPdx LNA states by frequency, as tui.cpp has them
	for top, n in ((12, 18), (50, 19), (60, 24), (250, 26), (420, 27), (1000, 20)):
		if mhz < top:
			return n
	return 18

'''-------------------------------------------------------------------
-------------------------------------------------------------------'''

def quiet(w, f, *a):		# change a widget from the radio's side without echoing it back as a command
	w.blockSignals(True)
	f(*a)
	w.blockSignals(False)

'''===================================================================================================================================================

==================================================================================================================================================='''

class Rspdx:		# rspdx -n (RSPdx) as a child process: commands on its stdin, JSON lines on its stdout; same face as civ.Rig
	MODES = ['LSB', 'USB', 'AM', 'WBFM']
	BANDWIDTHS = ['Narrow voice  1.8 kHz', 'Normal voice  2.8 kHz', 'Wide voice  3.1 kHz', 'Digital  2.8 kHz']		# bw 0-3, SSB only
	SWITCHES = {'notch': 'RF notch', 'dab': 'DAB notch', 'hdr': 'HDR', 'digi': 'Digital out', 'wf': 'Waterfall', 'nb': 'Noise blanker', 'nr': 'Noise reduction'}		# rspdx command: button

	'''-------------------------------------------------------------------
		starts the rspdx program (C++ demodulators) as a child process
	-------------------------------------------------------------------'''

	def __init__(self, path, emit):
		self.emit, self.ok, self.tx = emit, False, 0		# ok once rspdx has sent its first state
		self.state = {}		# rspdx's last state line
		self.last = ''		# last stderr line, for the box if rspdx dies
		self.p = QProcess()
		self.p.setWorkingDirectory(str(Path(path).parent))		# index.html, app.js if the waterfall comes back
		self.p.readyReadStandardOutput.connect(self.out)
		self.p.readyReadStandardError.connect(self.err)
		self.p.finished.connect(self.ended)
		self.p.errorOccurred.connect(lambda e: e == QProcess.ProcessError.FailedToStart and self.emit('gone', f"Couldn't run {path}.\n\n{self.p.errorString()}"))
		self.p.start(str(path), ['-n'])		# no frequency: rspdx restores the last s/save
		return
	# __init__

	'''-------------------------------------------------------------------
		lines coming in on rspdx's stdout: state and notices every 50 ms, meter every 250 ms
		emits signals with new rspdx values to class Shack()
	-------------------------------------------------------------------'''

	def out(self):
		while self.p.canReadLine():
			line = bytes(self.p.readLine()).decode(errors='replace')
			try:
				m = json.loads(line)
				if m['type'] == 'state':
					if not self.ok:
						self.send('bm')		# first state: rspdx is up, ask for the bookmarks
					self.ok, self.state = True, m['state']
					self.show()
				elif m['type'] == 'notice':		# every command gets one; a refused command changes no state, so put the controls back
					self.emit('error', m['msg'])
					self.show()
				elif m['type'] == 'meter':		# every 250 ms
					self.emit('meter', (m['adc'], m['sig'], m['nbr']))
				elif m['type'] == 'bookmarks':		# at start and after save, pick, rename, delete
					self.emit('bookmarks', m['list'])
			except (ValueError, KeyError, TypeError) as e:		# not JSON, or an rspdx older than this file (pull without ./build.sh): log it, keep going
				log.error(f'rspdx: bad line ({e!r}): {line.strip()}')
				self.emit('error', 'rspdx sent something unexpected (rebuild it after the last pull?), see shack.log')
		# while data coming in from rspdx.cpp
	# out

	'''-------------------------------------------------------------------
		emits signals to Shack
		mhz and other real time values, including overload
	-------------------------------------------------------------------'''

	def show(self):		# rspdx's last state -> the window
		st = self.state
		if not st:
			return
		self.emit('mhz', st['mhz'])
		for k in ('mode', 'v', 'if', 'lna', 'ant', 'bw', *self.SWITCHES):
			self.emit(k, st[k])
		self.emit('ovl', (st['ovl'], st['ovln']))
	# show

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def err(self):		# rspdx's stderr goes to shack.log
		for line in bytes(self.p.readAllStandardError()).decode(errors='replace').splitlines():
			if line.strip():
				self.last = line.strip()
				log.info(f'rspdx: {self.last}')
	# err

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def ended(self, code, status):
		if self.ok:
			how = 'crashed' if status == QProcess.ExitStatus.CrashExit else f'stopped (exit {code})'
			self.emit('gone', f'rspdx {how}.\n\nLast from rspdx: {self.last}\n(all of it is in shack.log)')
		else:
			self.emit('gone', f"Couldn't start the RSPdx.\n\n{self.last or 'rspdx exited with ' + str(code)}\n\nIs it plugged in? Is another program using it?")
	# ended

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def set(self, k, v):
		if k == 'mhz':
			self.send(f'f {v:.6f}')
		elif k == 'mode':
			self.send(f'm {v.lower()}')
		elif k == 'v':
			self.send(f'v {v:.3f}')
		elif k in ('if', 'lna'):
			self.send(f'{k} {v}')
		elif k == 'ant':
			self.send(f'ant {v.lower()}')
		elif k == 'bw':
			self.send(f'bw {v}')
		elif k in self.SWITCHES:		# on/off
			self.send(f'{k} {int(v)}')
		elif k == 'nbdb':		# blanker level; rspdx turns the blanker on at it
			self.send(f'nb {v}')
		elif k == 'save':
			self.send('s')
		elif k in ('bmpick', 'bmdel'):		# v = bookmark dict from the list
			self.send(f"{k} {v['hz']} {v['mode']}")
		elif k == 'bmname':		# v = (bookmark, new name); '' goes back to the band label
			self.send(f"bmname {v[0]['hz']} {v[0]['mode']} {v[1]}")
	# set

	'''-------------------------------------------------------------------
		writes a command line to rspdx's stdin; rspdx reads it in Control::poll(), every 50 ms
	-------------------------------------------------------------------'''

	def send(self, line):
		self.p.write((line + '\n').encode())
	# send

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def close(self):		# EOF on stdin is rspdx's cue to shut down cleanly
		self.p.finished.disconnect(self.ended)
		self.p.closeWriteChannel()
		if not self.p.waitForFinished(3000):
			self.p.kill()
			self.p.waitForFinished(1000)
	# close

# class Rspdx


'''-------------------------------------------------------------------
-------------------------------------------------------------------'''

class Shack(QMainWindow):
	got = pyqtSignal(str, object)		# rig reader thread -> GUI thread, receives signals from civ.Rig and Rspdx to self.on_rig

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def __init__(self, a):
		super().__init__()
		self.setWindowTitle('shack')
		self.a, self.rig, self.srv = a, None, None
		self.got.connect(self.on_rig, Qt.ConnectionType.QueuedConnection)		# always via the event loop, never inside a rig callback

		self.radio = QComboBox()
		self.radio.addItems(['none', 'IC-705', 'RSPdx', 'Xiegu x6200'])
		self.radio.activated.connect(lambda i: self.pick(self.radio.itemText(i)))
		self.freq = QDoubleSpinBox(decimals=6, minimum=0.03, maximum=470, singleStep=0.001, suffix=' MHz', keyboardTracking=False)
		self.freq.setFont(QFont('monospace', 20))
		self.freq.valueChanged.connect(lambda v: self.rig and self.rig.set('mhz', v))		# Enter, wheel (1 kHz, Ctrl 10 kHz), arrows
		self.freq.editingFinished.connect(lambda: self.freq.lineEdit().setModified(False))
		self.mode = QComboBox()
		self.mode.activated.connect(lambda i: self.rig and self.rig.set('mode', self.mode.itemText(i)))
		self.power = QWidget(toolTip='Transmit power, % of the radio\'s maximum. Changing it doesn\'t transmit')		# slider + its % readout
		self.pwr = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=100, singleStep=1, pageStep=10, tracking=False)		# sends on release, not on every pixel of a drag
		self.pwr.valueChanged.connect(lambda v: self.rig and self.rig.set('pwr', self.pwr_raw(v)))
		self.pscale = None		# (W at 0, W at 255) for radios that show watts, None = %
		self.pwrv = QLabel()
		self.pwrv.setFixedWidth(self.pwrv.fontMetrics().horizontalAdvance('10.0 W  '))
		self.pwr.sliderMoved.connect(lambda v: self.pwrv.setText(self.pwr_text(v)))		# readout follows the drag
		self.pwr.valueChanged.connect(lambda v: self.pwrv.setText(self.pwr_text(v)))
		self.agc = QComboBox(toolTip='The radio\'s AGC')
		self.agc.activated.connect(lambda i: self.rig and self.rig.set('agc', self.agcs[self.agc.itemText(i)]))
		self.agcs = {}		# label: 16 12 value, per radio (civ.RIGS)
		h = QHBoxLayout(self.power)
		h.setContentsMargins(0, 0, 0, 0)
		h.addWidget(self.pwr)
		h.addWidget(self.pwrv)
		self.vol = QDoubleSpinBox(decimals=2, minimum=0, maximum=1, singleStep=0.02, keyboardTracking=False)
		self.vol.valueChanged.connect(lambda v: self.rig and self.rig.set('v', v))
		self.ifr = QSpinBox(minimum=20, maximum=59, suffix=' dB', keyboardTracking=False)		# IF gain reduction
		self.ifr.valueChanged.connect(lambda v: self.rig and self.rig.set('if', v))
		self.lna = QSpinBox(minimum=0, maximum=18, keyboardTracking=False)		# LNA state; top end follows the band
		self.lna.valueChanged.connect(lambda v: self.rig and self.rig.set('lna', v))
		self.ant = QComboBox()
		self.ant.addItems(['A', 'B', 'C'])
		self.ant.activated.connect(lambda i: self.rig and self.rig.set('ant', self.ant.itemText(i)))
		self.ovl = QLabel()		# RF overload: blank until the first one
		self.bw = QComboBox()
		self.bw.addItems(Rspdx.BANDWIDTHS)
		self.bw.activated.connect(lambda i: self.rig and self.rig.set('bw', i))
		self.sw = {}		# rspdx command: toggle button
		switches = QHBoxLayout()
		self.nbdb = QSpinBox(minimum=8, maximum=20, value=12, suffix=' dB', keyboardTracking=False, toolTip='Blanker trigger level; changing it turns the blanker on')
		self.nbdb.valueChanged.connect(lambda v: self.rig and self.rig.set('nbdb', v))
		self.nbr = QLabel(toolTip='Blanker pulses per second')
		self.nbr.setFixedWidth(self.nbr.fontMetrics().horizontalAdvance('9999.9/s'))		# changing text mustn't resize the panel
		for k, name in Rspdx.SWITCHES.items():
			b = self.sw[k] = QPushButton(name, checkable=True)
			b.clicked.connect(lambda on, k=k: self.rig and self.rig.set(k, on))		# clicked, not toggled: the radio's own updates don't echo back
			switches.addWidget(b)
			if k == 'nb':		# its level and rate sit next to it
				switches.addWidget(self.nbdb)
				switches.addWidget(self.nbr)
		self.sw['pre'] = QPushButton('Preamp', checkable=True)		# CI-V radios
		self.sw['att'] = QPushButton('ATT', checkable=True)
		for k in ('pre', 'att'):
			self.sw[k].clicked.connect(lambda on, k=k: self.rig and self.rig.set(k, on))
			switches.insertWidget(0 if k == 'pre' else 1, self.sw[k])
		self.sw['nr'].setToolTip('RNNoise: removes the noise floor under voice (SSB, AM). Turn off for very weak signals, music and CW; Digital out is unaffected')
		self.save = QPushButton('Save', toolTip='Store these settings for this frequency and mode, and start here next time (like s in the TUI)')
		self.save.clicked.connect(lambda: self.rig and self.rig.set('save', 1))
		switches.addWidget(self.save)
		switches.addStretch()		# buttons keep their size when others are hidden
		self.adc = QProgressBar(minimum=-60, maximum=0, value=-60, format='', toolTip='Broadband level into the RSPdx ADC; red above -6 dBFS: back off the gain')
		self.bms = QTableWidget(0, 4, toolTip='Double-click: go there.  Right-click: rename or delete.  Save adds the current station.')
		self.bms.setHorizontalHeaderLabels(['Name', 'MHz', 'Mode', 'Hits'])
		self.bms.verticalHeader().hide()
		self.bms.setMinimumWidth(320)		# the button row is wide; don't let it squeeze the table away
		hh = self.bms.horizontalHeader()
		hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
		hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)		# Name takes the spare width
		self.bms.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
		self.bms.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
		self.bms.cellDoubleClicked.connect(lambda r, c: self.rig and self.rig.set('bmpick', self.bmlist[r]))
		self.bms.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
		self.bms.customContextMenuRequested.connect(self.bm_menu)
		self.bmlist = []
		self.rsp = (self.ifr, self.lna, self.ant, self.ovl, self.bw, *(self.sw[k] for k in Rspdx.SWITCHES), self.nbdb, self.nbr, self.save, self.adc, self.bms)		# RSPdx controls
		self.smeter = QProgressBar(minimum=-54, maximum=60, value=-54, format='S0')		# 705: S-units; RSPdx: dBFS (open_rx)
		self.swr = QLabel(toolTip='The radio\'s SWR meter while transmitting; the last reading stays until the next transmission')
		self.tx = QLabel('RX')
		self.tx.setFont(QFont('monospace', 16, QFont.Weight.Bold))
		self.pttb = QPushButton('PTT', checkable=True, toolTip='Transmit: click to key, click again to stop. Drops back to receive by itself after 30 s')
		self.pttb.setFont(QFont('monospace', 14, QFont.Weight.Bold))
		self.pttb.setStyleSheet('QPushButton:checked { background-color: #c00000; }')		# transmitting: red, not the usual green
		self.pttb.clicked.connect(self.key)
		self.ptt_limit = QTimer(singleShot=True, interval=30000, timeout=lambda: self.key(False))		# safety net
		self.pttrow = QWidget()		# PTT button + TX/RX indicator
		h = QHBoxLayout(self.pttrow)
		h.setContentsMargins(0, 0, 0, 0)
		h.addWidget(self.pttb)
		h.addWidget(self.tx)
		h.addStretch()
		self.clients = QLabel()
		self.statusBar().addPermanentWidget(self.clients)

		self.labels = {}		# control: its row label
		g = QGridLayout()
		g.setColumnStretch(2, 1)		# empty column 2 takes the spare width: adjusters stay narrow in column 1, meters span 1-2
		for row, (name, w) in enumerate([('Radio', self.radio), ('Frequency', self.freq), ('Mode', self.mode), ('Volume', self.vol), ('AGC', self.agc),
				('IF reduction', self.ifr), ('LNA reduction', self.lna), ('Antenna', self.ant), ('Bandwidth', self.bw), ('Overload', self.ovl), ('ADC', self.adc), ('Signal', self.smeter), ('SWR', self.swr), ('Power', self.power), ('PTT', self.pttrow)]):
			self.labels[w] = QLabel(name)
			w.installEventFilter(self)		# the label greys out with its control (eventFilter)
			g.addWidget(self.labels[w], row, 0)
			g.addWidget(w, row, 1, 1, 2 if w in (self.ovl, self.adc, self.smeter, self.power, self.pttrow) else 1)
		row += 1
		g.addLayout(switches, row, 0, 1, 3)
		self.helpw = None
		more = QHBoxLayout()		# second button row
		hb = QPushButton('Help', toolTip='What everything does (F1)')
		hb.clicked.connect(self.help)
		QShortcut(QKeySequence.StandardKey.HelpContents, self, self.help)
		more.addWidget(hb)
		more.addStretch()
		row += 1
		g.addLayout(more, row, 0, 1, 3)
		g.setRowStretch(row + 1, 1)		# spare height goes below the rows, not into them
		box = QWidget()
		box.setLayout(g)
		main = QHBoxLayout()
		main.addWidget(box, 3)
		main.addWidget(self.bms, 2)
		top = QWidget()
		top.setLayout(main)
		self.setCentralWidget(top)

		self.parts = (self.freq, self.mode, self.vol, self.agc, self.smeter, self.swr, self.power, self.pttrow, self.sw['pre'], self.sw['att'], *self.rsp)		# everything that comes and goes with the radio
		self.n = 0
		self.timer = QTimer(timeout=self.poll, interval=250)
		self.timer.start()
		self.shut()

	'''-------------------------------------------------------------------
		pick the radio
	-------------------------------------------------------------------'''

	def pick(self, name):
		self.shut()
		if name in civ.RIGS:
			self.open_civ(name)
		elif name == 'RSPdx':
			self.open_rx()
		return
	# pick

	'''-------------------------------------------------------------------
		open a CI-V radio: the IC-705 or the Xiegu x6200 (which speaks the 705's CI-V)
	-------------------------------------------------------------------'''

	def open_civ(self, name):
		dev = civ.find_dev(name)
		r = civ.RIGS[name]
		if not dev:
			return self.oops(f"Can't find the {name}.\n\nIs it plugged in and turned on?")
		try:
			self.rig = civ.Rig(dev, self.a.baud or r['baud'], self.a.addr, self.got.emit)
		except serial.SerialException as e:
			return self.oops(f"Found the {name} but couldn't open it.\n\n{e}\n\nIs another program (civ.py, engine.py) using it?")
		try:
			self.srv = civ.serve_ptt(self.rig, self.a.ptt) if self.a.ptt else None
		except OSError as e:
			self.statusBar().showMessage(f'PTT port {self.a.ptt}: {e.strerror}', 10000)
		self.rig.att_on = r['att']
		self.has_swr = r['swr']		# the X6200 always reads 0
		self.agcs = r['agc']
		quiet(self.agc, self.agc.clear)
		quiet(self.agc, self.agc.addItems, list(self.agcs))
		self.pscale = r['watts']
		lo, hi = (0, 100) if not self.pscale else (self.pscale[0] * 10, self.pscale[1] * 10)		# % or tenths of a watt
		quiet(self.pwr, self.pwr.setRange, lo, hi)
		self.rig.send(0x27, 0x11, 0x00)		# no scope data until the waterfall step
		self.rig.read_fast()
		self.smeter.setRange(-54, 60)
		self.show_radio(name, r['modes'], r['top'], (self.freq, self.mode, self.vol, self.agc, self.sw['pre'], self.sw['att'], self.sw['nb'], self.sw['nr'], self.smeter, *([self.swr] if r['swr'] else []), self.power, self.pttrow))		# nb, nr: the radio's own, on/off
		self.on_rig('ptt_clients', 0)
		return
	# open_civ

	'''-------------------------------------------------------------------
		start the rspdx program, which runs the RSPdx through the sdrplay api
	-------------------------------------------------------------------'''

	def open_rx(self):
		if not Path(self.a.rspdx).is_file():
			return self.oops(f"Can't find rspdx at {self.a.rspdx}.\n\nIs it built? (./build.sh)")
		self.rig = Rspdx(self.a.rspdx, self.got.emit)
		self.smeter.setRange(-100, -40)		# the TUI's signal scale, dBFS in the passband
		self.show_radio('RSPdx', Rspdx.MODES, 2000, (self.freq, self.mode, self.vol, self.smeter, *self.rsp))		# no PTT on a receiver
		return
	# open_rx

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def show_radio(self, name, modes, top, live):
		self.radio.setCurrentText(name)
		self.mode.clear()
		self.mode.addItems(modes)
		self.freq.setMaximum(top)
		for w in live:
			w.setEnabled(True)		# a mode may grey some out again (bandwidth outside SSB...)
		self.only(live)

	'''-------------------------------------------------------------------
		show just the controls this radio has, with their row labels
	-------------------------------------------------------------------'''

	def only(self, live):
		for w in self.parts:
			w.setVisible(w in live)
			if w in self.labels:
				self.labels[w].setVisible(w in live)
		return
	# only

	'''-------------------------------------------------------------------
		close the radio, unkeyed, and hide its controls
	-------------------------------------------------------------------'''

	def shut(self):
		self.ptt_limit.stop()
		if self.rig:
			if isinstance(self.rig, civ.Rig) and (self.rig.tx or self.pttb.isChecked()):		# the button may be ahead of the radio's report
				self.rig.ptt(False)
			self.rig.close()
			self.rig = None
		if self.srv:
			self.srv.stop()		# also hangs up on connected clients (xara), so none keeps talking to a closed radio
			self.srv = None
		self.radio.setCurrentText('none')
		self.clients.setText('')
		self.on_rig('ovl', (0, 0))
		self.on_rig('bookmarks', [])
		self.adc.setValue(-60)
		self.smeter.setValue(self.smeter.minimum())
		self.smeter.setFormat('')
		self.nbr.setText('')
		self.pwrv.setText('')
		self.swr.setText('')
		self.swr.setStyleSheet('')
		for b in self.sw.values():
			b.setChecked(False)
		self.on_rig('ptt', 0)
		self.only(())
		return
	# shut

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def help(self):		# non-modal: the radio stays usable while it's open
		if not self.helpw:
			self.helpw = QDialog(self, windowTitle='shack help')
			t = QTextBrowser(openExternalLinks=True)
			t.setHtml(HELP)
			b = QPushButton('Close')
			b.clicked.connect(self.helpw.close)
			v = QVBoxLayout(self.helpw)
			v.addWidget(t)
			v.addWidget(b, alignment=Qt.AlignmentFlag.AlignRight)
			self.helpw.resize(640, 640)
		self.helpw.show()
		self.helpw.raise_()
		self.helpw.activateWindow()
		return
	# help

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def oops(self, msg):
		self.shut()
		QMessageBox.warning(self, 'shack', msg)

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def poll(self):
		if not isinstance(self.rig, civ.Rig):		# rspdx pushes its state; only CI-V needs asking
			return
		if time.monotonic() - self.rig.heard > 3:		# polled 4x a second, so 3 s of silence = gone
			return self.oops('The radio stopped answering.\n\nUnplugged or turned off?')
		if time.monotonic() - self.rig.last_set > 0.5:		# stay out of the way of user commands
			self.rig.send(0x15, 0x02)
			if self.has_swr:
				self.rig.send(0x15, 0x12)
			self.rig.send(0x1C, 0x00)		# TX/RX, so a short transmission (a tuner cycle) shows
			if self.n % 4 == 0:
				self.rig.read_fast()
				for k in ('vol', 'nb', 'nr', 'pwr', 'pre', 'agc'):		# so the radio's own knob and buttons show up here too
					self.rig.send(*civ.CTL[k])
				self.rig.send(0x11)		# attenuator
			self.n += 1

	'''-------------------------------------------------------------------
		PTT button: key or unkey; keying starts the 30 s limit
	-------------------------------------------------------------------'''

	def key(self, on):
		if not isinstance(self.rig, civ.Rig):
			return
		self.rig.set('ptt', on)
		if on:
			self.ptt_limit.start()
		else:
			self.ptt_limit.stop()
			self.pttb.setChecked(False)
		return
	# key

	'''-------------------------------------------------------------------
		power slider value (% or tenths of a watt) <-> CI-V level 0-255 and its readout
	-------------------------------------------------------------------'''

	def pwr_raw(self, v):
		if not self.pscale:
			return round(v * 2.55)
		lo, hi = self.pscale
		return max(0, min(255, round((v / 10 - lo) / (hi - lo) * 255)))

	def pwr_text(self, v):
		return f'{v / 10:.1f} W' if self.pscale else f'{v} %'

	'''-------------------------------------------------------------------
		a row label follows its control's enabled state
	-------------------------------------------------------------------'''

	def eventFilter(self, w, e):
		if e.type() == QEvent.Type.EnabledChange and w in self.labels:
			self.labels[w].setEnabled(w.isEnabled())
		return super().eventFilter(w, e)

	'''-------------------------------------------------------------------
		slot that receives signals from civ.Rig and Rspdx
	-------------------------------------------------------------------'''

	def on_rig(self, k, v):
		if v is None and k != 'pwr':		# a level the radio sent as junk
			return
		if k == 'mhz' and not self.freq.lineEdit().isModified():		# don't overwrite typing
			self.freq.blockSignals(True)
			self.freq.setValue(v)
			self.freq.blockSignals(False)
			if isinstance(self.rig, Rspdx):
				quiet(self.lna, self.lna.setMaximum, lna_max(v))		# may clamp the value; that's rspdx's call, not a command
		elif k == 'mode' and self.mode.findText(v) >= 0:
			self.mode.setCurrentText(v)
			self.sw['nr'].setEnabled(v != 'WBFM')		# speech NR chops up broadcast music; rspdx skips it there too
			if isinstance(self.rig, Rspdx):		# as in the TUI: bandwidth presets are SSB only, the blanker isn't in WBFM
				self.bw.setEnabled(v in ('LSB', 'USB'))
				self.sw['nb'].setEnabled(v != 'WBFM')
				self.nbdb.setEnabled(v != 'WBFM')
		elif k == 'bw':
			self.bw.setCurrentIndex(v)
		elif k in self.sw:
			self.sw[k].setChecked(bool(v))
			if k == 'nb' and v and isinstance(self.rig, Rspdx):		# rspdx's nb carries the level when on, 0 when off
				quiet(self.nbdb, self.nbdb.setValue, v)
		elif k == 'v':
			quiet(self.vol, self.vol.setValue, v)
		elif k == 'vol':		# CI-V AF level 0-255
			quiet(self.vol, self.vol.setValue, v / 255)
		elif k == 'pwr' and not self.pwr.isSliderDown():		# CI-V RF power 0-255, None when the radio sends junk
			if v is None:
				return self.pwrv.setText('?')
			quiet(self.pwr, self.pwr.setValue, round(v / 2.55) if not self.pscale else round((self.pscale[0] + v / 255 * (self.pscale[1] - self.pscale[0])) * 10))
			self.pwrv.setText(self.pwr_text(self.pwr.value()))
		elif k == 'agc':
			name = next((n for n, x in self.agcs.items() if x == v), None)
			if name:
				quiet(self.agc, self.agc.setCurrentText, name)
		elif k in ('if', 'lna'):
			w = self.ifr if k == 'if' else self.lna
			quiet(w, w.setValue, v)
		elif k == 'ant':
			self.ant.setCurrentText(v.upper())
		elif k == 'ovl':		# (now, count since rspdx started); red while overloaded
			now, n = v
			self.ovl.setText(f"{'OVERLOAD' if now else 'clear'} ({n})" if now or n else '')
			self.ovl.setStyleSheet('color: white; background: red; font-weight: bold' if now else '')
		elif k == 'smeter':
			self.smeter.setValue(max(-54, min(60, v)))
			self.smeter.setFormat(sunits(v))
		elif k == 'meter':		# RSPdx: ADC dBFS, signal dBFS, blanker pulses/s
			adc, sig, nbr = v
			self.adc.setValue(round(max(-60, min(0, adc))))
			self.adc.setFormat(f'{adc:.1f} dBFS')
			self.adc.setStyleSheet('QProgressBar::chunk { background: red }' if adc > -6 else '')
			self.smeter.setValue(round(max(-100, min(-40, sig))))
			self.smeter.setFormat(f'{sig:.1f} dBFS')
			self.nbr.setText(f'{nbr:.1f}/s' if self.sw['nb'].isChecked() else '')
		elif k == 'bookmarks':
			self.bmlist = v
			self.bms.setRowCount(len(v))
			for r, b in enumerate(v):
				for c, t in enumerate((b['name'], f"{b['hz'] / 1e6:.6f}", b['mode'].upper(), str(b['hits']))):
					self.bms.setItem(r, c, QTableWidgetItem(t))
		elif k == 'swr':
			r, raw = v
			if raw or (self.rig and self.rig.tx):		# receiving reads 0: keep the last transmission's value
				self.swr.setText(f'{r:.1f}' + (' +' if raw >= 255 else ''))
				self.swr.setToolTip(f'SWR meter {raw}/255')
				self.swr.setStyleSheet('color: white; background: red' if r > 3 else 'color: black; background: orange' if r > 2 else '')
		elif k == 'ptt':
			self.tx.setText('TX' if v else 'RX')
			self.tx.setStyleSheet('color: white; background: red' if v else '')
			self.pttb.setChecked(bool(v))		# also when xara or the radio's tuner keys it
		elif k == 'ptt_clients':
			self.clients.setText(f'PTT port {self.srv.server_address[1]}: {v} connected' if self.srv else 'PTT port off')
		elif k == 'gone' and self.rig:
			self.oops(v)
		elif k == 'error':
			self.statusBar().showMessage(v, 5000)
		return
	# on_rig

	'''-------------------------------------------------------------------
		right-click on a bookmark: rename or delete
	-------------------------------------------------------------------'''

	def bm_menu(self, pos):
		r = self.bms.rowAt(pos.y())
		if r < 0 or not self.rig:
			return
		b = self.bmlist[r]
		menu = QMenu(self)
		ren = menu.addAction('Rename…')
		dele = menu.addAction('Delete')
		act = menu.exec(self.bms.viewport().mapToGlobal(pos))
		if act == ren:
			name, ok = QInputDialog.getText(self, 'Rename bookmark', 'Name (empty = band label):', text=b['name'])
			if ok:
				self.rig.set('bmname', (b, name.strip()))
		elif act == dele:
			if QMessageBox.question(self, 'Delete bookmark', f"Delete {b['name']}  {b['hz'] / 1e6:.6f} {b['mode'].upper()}?") == QMessageBox.StandardButton.Yes:
				self.rig.set('bmdel', b)
		return
	# bm_menu

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def closeEvent(self, e):
		self.shut()
		e.accept()
		return
	# closeEvent
# class Shack



import sys
import consts

'''-------------------------------------------------------------------
-------------------------------------------------------------------'''

if __name__ == '__main__':
	import os
	import sys
	import re
	import logging
	import subprocess

	import utils

	try:
		app = 'shack'
		logg = utils.setup_logging(name=app, logfile=f'{app}.log', level=logging.DEBUG, console=True)
		hn, cron, cwd, vscode, machine = utils.setup()
		if sys.stdin.isatty():
			# subprocess.run("stty erase '^?'", shell=True)
			pass
		home = os.environ.get('HOME')

		if vscode:
			def exclude(fname):				# exclude files/directories from file explorer in vscode
				import json

				with open(fname, 'r') as f:
					d = json.load(f)

				with open('.vscode/settings.json', 'r') as f:
					pass

				pass

			if os.path.exists('excludes.json'):
				exclude('excludes.json')

			caller = os.environ.get('CALLER')
			print(consts.COLOR_RESET)
			subprocess.run('clear')
			utils.set_vscode_terminal_font_size(12)
			utils.set_vscode_editor_font_size(15)
			# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
			# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
			# sys.argv.append('88.1')
			ap = argparse.ArgumentParser()
			ap.add_argument('--baud', type=int, help="override the radio's usual CI-V baud")
			ap.add_argument('--addr', type=lambda x: int(x, 0), default=0xA4)
			ap.add_argument('--ptt', type=int, default=4532, help='rigctld-style PTT port for xara, 0 = off')
			ap.add_argument('--rspdx', default=str(HERE / 'rspdx'), help='the rspdx program (run as rspdx -n)')
			a = ap.parse_args()
		else:
			ap = argparse.ArgumentParser()
			ap.add_argument('--baud', type=int, help="override the radio's usual CI-V baud")
			ap.add_argument('--addr', type=lambda x: int(x, 0), default=0xA4)
			ap.add_argument('--ptt', type=int, default=4532, help='rigctld-style PTT port for xara, 0 = off')
			ap.add_argument('--rspdx', default=str(HERE / 'rspdx'), help='the rspdx program (run as rspdx -n)')
			a = ap.parse_args()
		# if vscode


		utils.blank(logg)
		logg.info('starting...')

		ll = len(sys.argv)

		if ll > 1:
			cmd = sys.argv[1]
			if cmd == 'wp':
				pass
			pass

		# ---------------------------------------------------------------------------------------------------

		app = QApplication(sys.argv)
		app.setStyleSheet(styles.style)
		w = Shack(a)
		sys.excepthook = lambda t, v, tb: (logg.error('uncaught exception', exc_info=(t, v, tb)), w.statusBar().showMessage(f'Error: {v!r}  (details in shack.log)', 10000))		# PyQt6 aborts on an exception in a slot unless excepthook is ours
		w.show()
		sys.exit(app.exec())


		# ---------------------------------------------------------------------------------------------------

		pass
	except Exception as e:
		s = utils.mkexs(e)
		if isinstance(e.args[0], str):
			s += e.args[0]
			print('\n%s%s%s' % (consts.KRED, s, consts.COLOR_RESET))
		elif isinstance(e.args[0], list):
			t = e.args[0]		# a tuple
			s = ''
			for i in t:
				s += i + '\n'
			print('\n%s%s%s' % (consts.KRED, s, consts.COLOR_RESET))
		else:
			s = repr(e)
			s = s.replace('\\n', '\n')
			s = re.sub(r'\\', '', s)
			print('\n%s%s%s' % (consts.KRED, s, consts.COLOR_RESET))
	finally:
		logg.info('finally')
		logging.shutdown()
		pass

