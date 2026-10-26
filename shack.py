#! /home/larry/gvenv/bin/python3
# -*- coding: utf-8 -*-

import argparse, json, sys, time

import serial
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QUrl
from PyQt6.QtGui import QFont
from PyQt6.QtWebSockets import QWebSocket
from PyQt6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QGridLayout, QLabel, QMainWindow, QMessageBox, QProgressBar, QWidget

import civ

def sunits(db):		# S9-relative dB -> S7 / S9+20
	return f'S{max(0, (db + 54) // 6)}' if db <= 0 else f'S9+{db}'

'''===================================================================================================================================================

==================================================================================================================================================='''

'''-------------------------------------------------------------------
-------------------------------------------------------------------'''

class Rx:		# rx (RSPdx) over its waterfall websocket; same face as civ.Rig: set(), close(), tx, emit(kind, value)
	MODES = ['LSB', 'USB', 'AM', 'WBFM']

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def __init__(self, url, emit):
		self.emit, self.ok, self.tx = emit, False, 0		# ok once rx has said it's rx
		self.ws = QWebSocket()
		self.ws.textMessageReceived.connect(self.text)
		self.ws.disconnected.connect(self.lost)
		self.ws.open(QUrl(url))

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def text(self, msg):
		m = json.loads(msg)
		if m['type'] == 'caps':
			self.ok = m.get('rig') == 'RSPdx'
			if not self.ok:
				self.emit('gone', f"Something else is on that port ({m.get('rig')}), not rx.")
		elif m['type'] == 'state' and self.ok:
			self.emit('mhz', m['state']['mhz'])
			self.emit('mode', m['state']['mode'])
		elif m['type'] == 'notice':
			self.emit('error', m['msg'])

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def lost(self):
		self.emit('gone', 'Lost rx.\n\nClosed, or its waterfall turned off?' if self.ok else
			"Can't reach rx.\n\nStart it with the waterfall on: ./rx -w\n(or w in its TUI)")

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def set(self, k, v):
		if k == 'mhz':
			self.ws.sendTextMessage(f'f {v:.6f}')
		elif k == 'mode':
			self.ws.sendTextMessage(f'm {v.lower()}')

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def close(self):
		self.ws.disconnected.disconnect(self.lost)
		self.ws.close()
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
		self.smeter = QProgressBar(minimum=-54, maximum=60, value=-54, format='S0')
		self.tx = QLabel('RX')
		self.tx.setFont(QFont('monospace', 16, QFont.Weight.Bold))
		self.clients = QLabel()
		self.statusBar().addPermanentWidget(self.clients)

		g = QGridLayout()
		for row, (name, w) in enumerate([('Radio', self.radio), ('Frequency', self.freq), ('Mode', self.mode), ('Signal', self.smeter), ('PTT', self.tx)]):
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
		self.rig = Rx(self.a.rx, self.got.emit)
		self.show_radio('RSPdx', Rx.MODES, 2000, (self.freq, self.mode))		# no signal meter or PTT from rx yet

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
		for w in (self.freq, self.mode, self.smeter, self.tx):
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
		elif k == 'mode' and self.mode.findText(v) >= 0:
			self.mode.setCurrentText(v)
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
			ap.add_argument('--rx', default='ws://localhost:8700/ws', help="rx's waterfall websocket")
			a = ap.parse_args()
		else:
			ap = argparse.ArgumentParser()
			ap.add_argument('--baud', type=int, default=115200)
			ap.add_argument('--addr', type=lambda x: int(x, 0), default=0xA4)
			ap.add_argument('--ptt', type=int, default=4532, help='rigctld-style PTT port for xara, 0 = off')
			ap.add_argument('--rx', default='ws://localhost:8700/ws', help="rx's waterfall websocket")
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

