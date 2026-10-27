#! /home/larry/gvenv/bin/python3
# -*- coding: utf-8 -*-

import argparse, json, logging, sys, time
from pathlib import Path

import serial
from PyQt6.QtCore import pyqtSignal, QProcess, Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
	QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QWidget)

import civ

HERE = Path(__file__).resolve().parent
log = logging.getLogger('shack')		# set up in main by utils.setup_logging

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
	SWITCHES = {'notch': 'RF notch', 'dab': 'DAB notch', 'hdr': 'HDR', 'digi': 'Digital out', 'wf': 'Waterfall', 'nb': 'Noise blanker'}		# rspdx command: button

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
			m = json.loads(bytes(self.p.readLine()).decode())
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
		self.radio.addItems(['none', 'IC-705', 'RSPdx', 'Xiegu x6200'])		# Xiegu to be done soon (placeholder now)
		self.radio.activated.connect(lambda i: self.pick(self.radio.itemText(i)))
		self.freq = QDoubleSpinBox(decimals=6, minimum=0.03, maximum=470, singleStep=0.001, suffix=' MHz', keyboardTracking=False)
		self.freq.setFont(QFont('monospace', 20))
		self.freq.valueChanged.connect(lambda v: self.rig and self.rig.set('mhz', v))		# Enter, wheel (1 kHz, Ctrl 10 kHz), arrows
		self.freq.editingFinished.connect(lambda: self.freq.lineEdit().setModified(False))
		self.mode = QComboBox()
		self.mode.activated.connect(lambda i: self.rig and self.rig.set('mode', self.mode.itemText(i)))
		self.vol = QDoubleSpinBox(decimals=3, minimum=0, maximum=2, singleStep=0.005, keyboardTracking=False)
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
		for k, name in Rspdx.SWITCHES.items():
			b = self.sw[k] = QPushButton(name, checkable=True)
			b.setStyleSheet('QPushButton:checked { background: #2a7d2a; color: white; font-weight: bold }')		# on = green
			b.clicked.connect(lambda on, k=k: self.rig and self.rig.set(k, on))		# clicked, not toggled: the radio's own updates don't echo back
			switches.addWidget(b)
		self.nbdb = QSpinBox(minimum=8, maximum=20, value=12, suffix=' dB', keyboardTracking=False, toolTip='Blanker trigger level; changing it turns the blanker on')
		self.nbdb.valueChanged.connect(lambda v: self.rig and self.rig.set('nbdb', v))
		switches.addWidget(self.nbdb)
		self.nbr = QLabel(toolTip='Blanker pulses per second')
		switches.addWidget(self.nbr)
		self.save = QPushButton('Save', toolTip='Store these settings for this frequency and mode, and start here next time (like s in the TUI)')
		self.save.clicked.connect(lambda: self.rig and self.rig.set('save', 1))
		switches.addWidget(self.save)
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
		self.rsp = (self.vol, self.ifr, self.lna, self.ant, self.ovl, self.bw, *self.sw.values(), self.nbdb, self.save, self.adc, self.bms)		# RSPdx-only controls
		self.smeter = QProgressBar(minimum=-54, maximum=60, value=-54, format='S0')		# 705: S-units; RSPdx: dBFS (open_rx)
		self.tx = QLabel('RX')
		self.tx.setFont(QFont('monospace', 16, QFont.Weight.Bold))
		self.clients = QLabel()
		self.statusBar().addPermanentWidget(self.clients)

		g = QGridLayout()
		for row, (name, w) in enumerate([('Radio', self.radio), ('Frequency', self.freq), ('Mode', self.mode), ('Volume', self.vol),
				('IF reduction', self.ifr), ('LNA reduction', self.lna), ('Antenna', self.ant), ('Bandwidth', self.bw), ('Overload', self.ovl), ('ADC', self.adc), ('Signal', self.smeter), ('PTT', self.tx)]):
			g.addWidget(QLabel(name), row, 0)
			g.addWidget(w, row, 1)
		row += 1
		g.addLayout(switches, row, 0, 1, 2)
		g.setRowStretch(row + 1, 1)		# spare height goes below the rows, not into them
		box = QWidget()
		box.setLayout(g)
		main = QHBoxLayout()
		main.addWidget(box, 3)
		main.addWidget(self.bms, 2)
		top = QWidget()
		top.setLayout(main)
		self.setCentralWidget(top)

		self.n = 0
		self.timer = QTimer(timeout=self.poll, interval=250)
		self.timer.start()
		self.shut()

	'''-------------------------------------------------------------------
		pick the radio
	-------------------------------------------------------------------'''

	def pick(self, name):
		self.shut()
		if name == 'IC-705':
			self.open_705()
		elif name == 'RSPdx':
			self.open_rx()
		return
	# pick

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def open_705(self):
		dev = civ.find_dev()
		if not dev:
			return self.oops("Can't find the IC-705.\n\nIs it plugged in and turned on?")
		try:
			self.rig = civ.Rig(dev, self.a.baud, self.a.addr, self.got.emit)
		except serial.SerialException as e:
			return self.oops(f"Found the IC-705 but couldn't open it.\n\n{e}\n\nIs another program (civ.py, engine.py) using it?")
		try:
			self.srv = civ.serve_ptt(self.rig, self.a.ptt) if self.a.ptt else None
		except OSError as e:
			self.statusBar().showMessage(f'PTT port {self.a.ptt}: {e.strerror}', 10000)
		self.rig.send(0x27, 0x11, 0x00)		# no scope data until the waterfall step
		self.rig.read_fast()
		self.smeter.setRange(-54, 60)
		self.show_radio('IC-705', civ.MODE_LIST, 470, (self.freq, self.mode, self.smeter, self.tx))
		self.on_rig('ptt_clients', 0)

	'''-------------------------------------------------------------------
		start the rspdx program, which runs the RSPdx through the sdrplay api
	-------------------------------------------------------------------'''

	def open_rx(self):
		if not Path(self.a.rspdx).is_file():
			return self.oops(f"Can't find rspdx at {self.a.rspdx}.\n\nIs it built? (./build.sh)")
		self.rig = Rspdx(self.a.rspdx, self.got.emit)
		self.smeter.setRange(-100, -40)		# the TUI's signal scale, dBFS in the passband
		self.show_radio('RSPdx', Rspdx.MODES, 2000, (self.freq, self.mode, self.smeter, *self.rsp))		# no PTT on a receiver
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
			w.setEnabled(True)

	'''-------------------------------------------------------------------
		close the radio, unkeyed, and grey out its controls
	-------------------------------------------------------------------'''

	def shut(self):
		if self.rig:
			if self.rig.tx:
				self.rig.ptt(False)
			self.rig.close()
			self.rig = None
		if self.srv:
			self.srv.shutdown()
			self.srv.server_close()
			self.srv = None
		self.radio.setCurrentText('none')
		self.clients.setText('')
		self.on_rig('ovl', (0, 0))
		self.on_rig('bookmarks', [])
		self.adc.setValue(-60)
		self.smeter.setValue(self.smeter.minimum())
		self.smeter.setFormat('')
		self.nbr.setText('')
		for b in self.sw.values():
			b.setChecked(False)
		self.on_rig('ptt', 0)
		for w in (self.freq, self.mode, self.smeter, self.tx, *self.rsp):
			w.setEnabled(False)
		return
	# shut

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
			if self.n % 4 == 0:
				self.rig.read_fast()
			self.n += 1

	'''-------------------------------------------------------------------
		slot that receives signals from civ.Rig and Rspdx
	-------------------------------------------------------------------'''

	def on_rig(self, k, v):
		if k == 'mhz' and not self.freq.lineEdit().isModified():		# don't overwrite typing
			self.freq.blockSignals(True)
			self.freq.setValue(v)
			self.freq.blockSignals(False)
			if isinstance(self.rig, Rspdx):
				quiet(self.lna, self.lna.setMaximum, lna_max(v))		# may clamp the value; that's rspdx's call, not a command
		elif k == 'mode' and self.mode.findText(v) >= 0:
			self.mode.setCurrentText(v)
			if isinstance(self.rig, Rspdx):		# as in the TUI: bandwidth presets are SSB only, the blanker isn't in WBFM
				self.bw.setEnabled(v in ('LSB', 'USB'))
				self.sw['nb'].setEnabled(v != 'WBFM')
				self.nbdb.setEnabled(v != 'WBFM')
		elif k == 'bw':
			self.bw.setCurrentIndex(v)
		elif k in self.sw:
			self.sw[k].setChecked(bool(v))
			if k == 'nb' and v:		# nb carries the level when on, 0 when off
				quiet(self.nbdb, self.nbdb.setValue, v)
		elif k == 'v':
			quiet(self.vol, self.vol.setValue, v)
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
		elif k == 'ptt':
			self.tx.setText('TX' if v else 'RX')
			self.tx.setStyleSheet('color: white; background: red' if v else '')
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
			ap.add_argument('--baud', type=int, default=115200)
			ap.add_argument('--addr', type=lambda x: int(x, 0), default=0xA4)
			ap.add_argument('--ptt', type=int, default=4532, help='rigctld-style PTT port for xara, 0 = off')
			ap.add_argument('--rspdx', default=str(HERE / 'rspdx'), help='the rspdx program (run as rspdx -n)')
			a = ap.parse_args()
		else:
			ap = argparse.ArgumentParser()
			ap.add_argument('--baud', type=int, default=115200)
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
		w = Shack(a)
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

