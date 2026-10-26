#! /home/larry/gvenv/bin/python3
# -*- coding: utf-8 -*-

import argparse, json, logging, sys, time
from pathlib import Path

import serial
from PyQt6.QtCore import pyqtSignal, QProcess, Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QGridLayout, QLabel, QMainWindow, QMessageBox, QProgressBar, QSpinBox, QWidget

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

'''-------------------------------------------------------------------
-------------------------------------------------------------------'''

class Rx:		# rx -n (RSPdx) as a child process: commands on its stdin, JSON lines on its stdout; same face as civ.Rig
	MODES = ['LSB', 'USB', 'AM', 'WBFM']

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def __init__(self, path, emit):
		self.emit, self.ok, self.tx = emit, False, 0		# ok once rx has sent its first state
		self.last = ''		# last stderr line, for the box if rx dies
		self.p = QProcess()
		self.p.setWorkingDirectory(str(Path(path).parent))		# index.html, app.js if the waterfall comes back
		self.p.readyReadStandardOutput.connect(self.out)
		self.p.readyReadStandardError.connect(self.err)
		self.p.finished.connect(self.ended)
		self.p.errorOccurred.connect(lambda e: e == QProcess.ProcessError.FailedToStart and self.emit('gone', f"Couldn't run {path}.\n\n{self.p.errorString()}"))
		self.p.start(str(path), ['-n'])		# no frequency: rx restores the last s/save

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def out(self):
		while self.p.canReadLine():
			m = json.loads(bytes(self.p.readLine()).decode())
			if m['type'] == 'state':
				self.ok = True
				self.emit('mhz', m['state']['mhz'])
				for k in ('mode', 'v', 'if', 'lna', 'ant'):
					self.emit(k, m['state'][k])
			elif m['type'] == 'notice':
				self.emit('error', m['msg'])

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def err(self):		# rx's stderr goes to shack.log
		for line in bytes(self.p.readAllStandardError()).decode(errors='replace').splitlines():
			if line.strip():
				self.last = line.strip()
				log.info(f'rx: {self.last}')

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def ended(self, code, status):
		if self.ok:
			how = 'crashed' if status == QProcess.ExitStatus.CrashExit else f'stopped (exit {code})'
			self.emit('gone', f'rx {how}.\n\nLast from rx: {self.last}\n(all of it is in shack.log)')
		else:
			self.emit('gone', f"Couldn't start the RSPdx.\n\n{self.last or 'rx exited with ' + str(code)}\n\nIs it plugged in? Is another program using it?")

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

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def send(self, line):
		self.p.write((line + '\n').encode())

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def close(self):		# EOF on stdin is rx's cue to shut down cleanly
		self.p.finished.disconnect(self.ended)
		self.p.closeWriteChannel()
		if not self.p.waitForFinished(3000):
			self.p.kill()
			self.p.waitForFinished(1000)
# class Rx


'''-------------------------------------------------------------------
-------------------------------------------------------------------'''

class Shack(QMainWindow):
	got = pyqtSignal(str, object)		# rig reader thread -> GUI thread

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def __init__(self, a):
		super().__init__()
		self.setWindowTitle('shack')
		self.a, self.rig, self.srv = a, None, None
		self.got.connect(self.on_rig, Qt.ConnectionType.QueuedConnection)		# always via the event loop, never inside a rig callback

		self.radio = QComboBox()
		self.radio.addItems(['none', 'IC-705', 'RSPdx'])		# Xiegu joins once it's been plugged in
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
		self.rsp = (self.vol, self.ifr, self.lna, self.ant)		# RSPdx-only rows
		self.smeter = QProgressBar(minimum=-54, maximum=60, value=-54, format='S0')
		self.tx = QLabel('RX')
		self.tx.setFont(QFont('monospace', 16, QFont.Weight.Bold))
		self.clients = QLabel()
		self.statusBar().addPermanentWidget(self.clients)

		g = QGridLayout()
		for row, (name, w) in enumerate([('Radio', self.radio), ('Frequency', self.freq), ('Mode', self.mode), ('Volume', self.vol),
				('IF reduction', self.ifr), ('LNA reduction', self.lna), ('Antenna', self.ant), ('Signal', self.smeter), ('PTT', self.tx)]):
			g.addWidget(QLabel(name), row, 0)
			g.addWidget(w, row, 1)
		box = QWidget()
		box.setLayout(g)
		self.setCentralWidget(box)

		self.n = 0
		self.timer = QTimer(timeout=self.poll, interval=250)
		self.timer.start()
		self.shut()

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def pick(self, name):
		self.shut()
		if name == 'IC-705':
			self.open_705()
		elif name == 'RSPdx':
			self.open_rx()

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
		self.show_radio('IC-705', civ.MODE_LIST, 470, (self.freq, self.mode, self.smeter, self.tx))
		self.on_rig('ptt_clients', 0)

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def open_rx(self):
		if not Path(self.a.rx).is_file():
			return self.oops(f"Can't find rx at {self.a.rx}.\n\nIs it built? (./build.sh)")
		self.rig = Rx(self.a.rx, self.got.emit)
		self.show_radio('RSPdx', Rx.MODES, 2000, (self.freq, self.mode, *self.rsp))		# no signal meter or PTT from rx yet

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
	-------------------------------------------------------------------'''

	def shut(self):		# close the radio, unkeyed, and grey out its controls
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
		self.on_rig('ptt', 0)
		for w in (self.freq, self.mode, self.smeter, self.tx, *self.rsp):
			w.setEnabled(False)

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def oops(self, msg):
		self.shut()
		QMessageBox.warning(self, 'shack', msg)

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def poll(self):
		if not isinstance(self.rig, civ.Rig):		# rx pushes its state; only CI-V needs asking
			return
		if time.monotonic() - self.rig.heard > 3:		# polled 4x a second, so 3 s of silence = gone
			return self.oops('The radio stopped answering.\n\nUnplugged or turned off?')
		if time.monotonic() - self.rig.last_set > 0.5:		# stay out of the way of user commands
			self.rig.send(0x15, 0x02)
			if self.n % 4 == 0:
				self.rig.read_fast()
			self.n += 1

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def on_rig(self, k, v):
		if k == 'mhz' and not self.freq.lineEdit().isModified():		# don't overwrite typing
			self.freq.blockSignals(True)
			self.freq.setValue(v)
			self.freq.blockSignals(False)
			if isinstance(self.rig, Rx):
				quiet(self.lna, self.lna.setMaximum, lna_max(v))		# may clamp the value; that's rx's call, not a command
		elif k == 'mode' and self.mode.findText(v) >= 0:
			self.mode.setCurrentText(v)
		elif k == 'v':
			quiet(self.vol, self.vol.setValue, v)
		elif k in ('if', 'lna'):
			w = self.ifr if k == 'if' else self.lna
			quiet(w, w.setValue, v)
		elif k == 'ant':
			self.ant.setCurrentText(v.upper())
		elif k == 'smeter':
			self.smeter.setValue(max(-54, min(60, v)))
			self.smeter.setFormat(sunits(v))
		elif k == 'ptt':
			self.tx.setText('TX' if v else 'RX')
			self.tx.setStyleSheet('color: white; background: red' if v else '')
		elif k == 'ptt_clients':
			self.clients.setText(f'PTT port {self.srv.server_address[1]}: {v} connected' if self.srv else 'PTT port off')
		elif k == 'gone' and self.rig:
			self.oops(v)
		elif k == 'error':
			self.statusBar().showMessage(v, 5000)

	def closeEvent(self, e):
		self.shut()
		e.accept()
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
			ap.add_argument('--rx', default=str(HERE / 'rx'), help='the rx program (run as rx -n)')
			a = ap.parse_args()
		else:
			ap = argparse.ArgumentParser()
			ap.add_argument('--baud', type=int, default=115200)
			ap.add_argument('--addr', type=lambda x: int(x, 0), default=0xA4)
			ap.add_argument('--ptt', type=int, default=4532, help='rigctld-style PTT port for xara, 0 = off')
			ap.add_argument('--rx', default=str(HERE / 'rx'), help='the rx program (run as rx -n)')
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

