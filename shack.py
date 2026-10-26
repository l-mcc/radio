#!/usr/bin/env python3
import argparse, sys, time

from PyQt5.QtCore import pyqtSignal, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QGridLayout, QLabel, QMainWindow, QProgressBar, QWidget

import civ

def sunits(db):		# S9-relative dB -> S7 / S9+20
	return f'S{max(0, (db + 54) // 6)}' if db <= 0 else f'S9+{db}'

class Shack(QMainWindow):
	got = pyqtSignal(str, object)		# rig reader thread -> GUI thread

	def __init__(s, a):
		super().__init__()
		s.setWindowTitle('shack')
		s.got.connect(s.on_rig)
		s.rig = civ.Rig(a.dev, a.baud, a.addr, s.got.emit)
		s.srv = civ.serve_ptt(s.rig, a.ptt) if a.ptt else None

		s.radio = QComboBox()
		s.radio.addItem('IC-705')		# RSPdx and Xiegu join later
		s.freq = QDoubleSpinBox(decimals=6, minimum=0.03, maximum=470, singleStep=0.001, suffix=' MHz', keyboardTracking=False)
		s.freq.setFont(QFont('monospace', 20))
		s.freq.valueChanged.connect(lambda v: s.rig.set('mhz', v))		# Enter, wheel (1 kHz, Ctrl 10 kHz), arrows
		s.freq.editingFinished.connect(lambda: s.freq.lineEdit().setModified(False))
		s.mode = QComboBox()
		s.mode.addItems(civ.MODE_LIST)
		s.mode.activated.connect(lambda i: s.rig.set('mode', s.mode.itemText(i)))
		s.smeter = QProgressBar(minimum=-54, maximum=60, value=-54, format='S0')
		s.tx = QLabel('RX')
		s.tx.setFont(QFont('monospace', 16, QFont.Bold))
		s.clients = QLabel()
		s.statusBar().addPermanentWidget(s.clients)
		s.on_rig('ptt_clients', 0)

		g = QGridLayout()
		for row, (name, w) in enumerate([('Radio', s.radio), ('Frequency', s.freq), ('Mode', s.mode), ('Signal', s.smeter), ('PTT', s.tx)]):
			g.addWidget(QLabel(name), row, 0)
			g.addWidget(w, row, 1)
		box = QWidget()
		box.setLayout(g)
		s.setCentralWidget(box)

		s.rig.send(0x27, 0x11, 0x00)		# no scope data until the waterfall step
		s.rig.read_fast()
		s.n = 0
		s.timer = QTimer(timeout=s.poll, interval=250)
		s.timer.start()

	def poll(s):
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
		elif k == 'error':
			s.statusBar().showMessage(v, 5000)

	def closeEvent(s, e):
		if s.rig.tx:
			s.rig.ptt(False)
		if s.srv:
			s.srv.shutdown()
		e.accept()

if __name__ == '__main__':
	ap = argparse.ArgumentParser()
	ap.add_argument('--dev', default=civ.find_dev())
	ap.add_argument('--baud', type=int, default=115200)
	ap.add_argument('--addr', type=lambda x: int(x, 0), default=0xA4)
	ap.add_argument('--ptt', type=int, default=4532, help='rigctld-style PTT port for xara, 0 = off')
	a = ap.parse_args()
	app = QApplication(sys.argv)
	w = Shack(a)
	w.show()
	sys.exit(app.exec_())
