#!/usr/bin/env python3
import argparse, json, sys, time

import serial
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QUrl
from PyQt6.QtGui import QFont
from PyQt6.QtWebSockets import QWebSocket
from PyQt6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QGridLayout, QLabel, QMainWindow, QMessageBox, QProgressBar, QWidget

import civ

def sunits(db):		# S9-relative dB -> S7 / S9+20
	return f'S{max(0, (db + 54) // 6)}' if db <= 0 else f'S9+{db}'

class Rx:		# rx (RSPdx) over its waterfall websocket; same face as civ.Rig: set(), close(), tx, emit(kind, value)
	MODES = ['LSB', 'USB', 'AM', 'WBFM']

	def __init__(s, url, emit):
		s.emit, s.ok, s.tx = emit, False, 0		# ok once rx has said it's rx
		s.ws = QWebSocket()
		s.ws.textMessageReceived.connect(s.text)
		s.ws.disconnected.connect(s.lost)
		s.ws.open(QUrl(url))

	def text(s, msg):
		m = json.loads(msg)
		if m['type'] == 'caps':
			s.ok = m.get('rig') == 'RSPdx'
			if not s.ok:
				s.emit('gone', f"Something else is on that port ({m.get('rig')}), not rx.")
		elif m['type'] == 'state' and s.ok:
			s.emit('mhz', m['state']['mhz'])
			s.emit('mode', m['state']['mode'])
		elif m['type'] == 'notice':
			s.emit('error', m['msg'])

	def lost(s):
		s.emit('gone', 'Lost rx.\n\nClosed, or its waterfall turned off?' if s.ok else
			"Can't reach rx.\n\nStart it with the waterfall on: ./rx -w\n(or w in its TUI)")

	def set(s, k, v):
		if k == 'mhz':
			s.ws.sendTextMessage(f'f {v:.6f}')
		elif k == 'mode':
			s.ws.sendTextMessage(f'm {v.lower()}')

	def close(s):
		s.ws.disconnected.disconnect(s.lost)
		s.ws.close()

class Shack(QMainWindow):
	got = pyqtSignal(str, object)		# rig reader thread -> GUI thread

	def __init__(s, a):
		super().__init__()
		s.setWindowTitle('shack')
		s.a, s.rig, s.srv = a, None, None
		s.got.connect(s.on_rig, Qt.ConnectionType.QueuedConnection)		# always via the event loop, never inside a rig callback

		s.radio = QComboBox()
		s.radio.addItems(['none', 'IC-705', 'RSPdx'])		# Xiegu joins once it's been plugged in
		s.radio.activated.connect(lambda i: s.pick(s.radio.itemText(i)))
		s.freq = QDoubleSpinBox(decimals=6, minimum=0.03, maximum=470, singleStep=0.001, suffix=' MHz', keyboardTracking=False)
		s.freq.setFont(QFont('monospace', 20))
		s.freq.valueChanged.connect(lambda v: s.rig and s.rig.set('mhz', v))		# Enter, wheel (1 kHz, Ctrl 10 kHz), arrows
		s.freq.editingFinished.connect(lambda: s.freq.lineEdit().setModified(False))
		s.mode = QComboBox()
		s.mode.activated.connect(lambda i: s.rig and s.rig.set('mode', s.mode.itemText(i)))
		s.smeter = QProgressBar(minimum=-54, maximum=60, value=-54, format='S0')
		s.tx = QLabel('RX')
		s.tx.setFont(QFont('monospace', 16, QFont.Weight.Bold))
		s.clients = QLabel()
		s.statusBar().addPermanentWidget(s.clients)

		g = QGridLayout()
		for row, (name, w) in enumerate([('Radio', s.radio), ('Frequency', s.freq), ('Mode', s.mode), ('Signal', s.smeter), ('PTT', s.tx)]):
			g.addWidget(QLabel(name), row, 0)
			g.addWidget(w, row, 1)
		box = QWidget()
		box.setLayout(g)
		s.setCentralWidget(box)

		s.n = 0
		s.timer = QTimer(timeout=s.poll, interval=250)
		s.timer.start()
		s.shut()

	def pick(s, name):
		s.shut()
		if name == 'IC-705':
			s.open_705()
		elif name == 'RSPdx':
			s.open_rx()

	def open_705(s):
		dev = civ.find_dev()
		if not dev:
			return s.oops("Can't find the IC-705.\n\nIs it plugged in and turned on?")
		try:
			s.rig = civ.Rig(dev, s.a.baud, s.a.addr, s.got.emit)
		except serial.SerialException as e:
			return s.oops(f"Found the IC-705 but couldn't open it.\n\n{e}\n\nIs another program (civ.py, engine.py) using it?")
		try:
			s.srv = civ.serve_ptt(s.rig, s.a.ptt) if s.a.ptt else None
		except OSError as e:
			s.statusBar().showMessage(f'PTT port {s.a.ptt}: {e.strerror}', 10000)
		s.rig.send(0x27, 0x11, 0x00)		# no scope data until the waterfall step
		s.rig.read_fast()
		s.show_radio('IC-705', civ.MODE_LIST, 470, (s.freq, s.mode, s.smeter, s.tx))
		s.on_rig('ptt_clients', 0)

	def open_rx(s):
		s.rig = Rx(s.a.rx, s.got.emit)
		s.show_radio('RSPdx', Rx.MODES, 2000, (s.freq, s.mode))		# no signal meter or PTT from rx yet

	def show_radio(s, name, modes, top, live):
		s.radio.setCurrentText(name)
		s.mode.clear()
		s.mode.addItems(modes)
		s.freq.setMaximum(top)
		for w in live:
			w.setEnabled(True)

	def shut(s):		# close the radio, unkeyed, and grey out its controls
		if s.rig:
			if s.rig.tx:
				s.rig.ptt(False)
			s.rig.close()
			s.rig = None
		if s.srv:
			s.srv.shutdown()
			s.srv.server_close()
			s.srv = None
		s.radio.setCurrentText('none')
		s.clients.setText('')
		s.on_rig('ptt', 0)
		for w in (s.freq, s.mode, s.smeter, s.tx):
			w.setEnabled(False)

	def oops(s, msg):
		s.shut()
		QMessageBox.warning(s, 'shack', msg)

	def poll(s):
		if not isinstance(s.rig, civ.Rig):		# rx pushes its state; only CI-V needs asking
			return
		if time.monotonic() - s.rig.heard > 3:		# polled 4x a second, so 3 s of silence = gone
			return s.oops('The radio stopped answering.\n\nUnplugged or turned off?')
		if time.monotonic() - s.rig.last_set > 0.5:		# stay out of the way of user commands
			s.rig.send(0x15, 0x02)
			if s.n % 4 == 0:
				s.rig.read_fast()
			s.n += 1

	def on_rig(s, k, v):
		if k == 'mhz' and not s.freq.lineEdit().isModified():		# don't overwrite typing
			s.freq.blockSignals(True)
			s.freq.setValue(v)
			s.freq.blockSignals(False)
		elif k == 'mode' and s.mode.findText(v) >= 0:
			s.mode.setCurrentText(v)
		elif k == 'smeter':
			s.smeter.setValue(max(-54, min(60, v)))
			s.smeter.setFormat(sunits(v))
		elif k == 'ptt':
			s.tx.setText('TX' if v else 'RX')
			s.tx.setStyleSheet('color: white; background: red' if v else '')
		elif k == 'ptt_clients':
			s.clients.setText(f'PTT port {s.srv.server_address[1]}: {v} connected' if s.srv else 'PTT port off')
		elif k == 'gone' and s.rig:
			s.oops(v)
		elif k == 'error':
			s.statusBar().showMessage(v, 5000)

	def closeEvent(s, e):
		s.shut()
		e.accept()

if __name__ == '__main__':
	ap = argparse.ArgumentParser()
	ap.add_argument('--baud', type=int, default=115200)
	ap.add_argument('--addr', type=lambda x: int(x, 0), default=0xA4)
	ap.add_argument('--ptt', type=int, default=4532, help='rigctld-style PTT port for xara, 0 = off')
	ap.add_argument('--rx', default='ws://localhost:8700/ws', help="rx's waterfall websocket")
	a = ap.parse_args()
	app = QApplication(sys.argv)
	w = Shack(a)
	w.show()
	sys.exit(app.exec())
