#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, json, logging, re, shutil, socket, sqlite3, struct, subprocess, sys, tempfile, time
from pathlib import Path

import serial
from PyQt6.QtCore import pyqtSignal, QEvent, QPointF, QProcess, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QKeySequence, QPainter, QPalette, QPen, QPolygonF, QShortcut, QTextCursor
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
	QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

import cat, civ, styles
try:		# decoders need numpy; without it shack still runs, the Decoder row says why
	import numpy as np
	import decode
	NODECODE = None
except ImportError as e:
	decode, NODECODE = None, str(e)

HERE = Path(__file__).resolve().parent
log = logging.getLogger('shack')		# set up in main by utils.setup_logging

HELP = '''
<h3>Radio</h3>
<p>Pick IC-705, RSPdx, Xiegu x6200 or FTdx10 to open it, none to close it. Only one radio is open at a time; only its own controls are shown. A greyed-out one doesn't apply in the current mode (Bandwidth outside SSB, for example).</p>

<h3>Frequency</h3>
<p>Like the waterfall page: the mouse wheel over any digit tunes that digit (the dimmed leading zeros too, for big jumps); clicking a digit's top half steps it up, its bottom half down. Arrow keys: left/right picks a digit, up/down tunes it. Or just type a frequency in MHz (7.074) and press Enter; Esc cancels. The radio's own changes don't overwrite what you're typing.</p>

<h3>RSPdx</h3>
<table cellspacing="4">
<tr><td><b>Mode</b></td><td>LSB, USB, AM, WBFM (broadcast FM)</td></tr>
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
<tr><td><b>Save</b></td><td>bookmarks frequency, mode and settings (or updates the bookmark already there)</td></tr>
</table>

<h3>Decoder</h3>
<p>Pick RTTY, CW, VARA, Packet or JS8. With the RSPdx it turns Digital out on and decodes that; with the IC-705 or Xiegu it decodes the radio's USB sound card (the radio's receive audio, read with arecord; use a -D mode or USB with a filter wide enough for the signal). It decodes it (decode.py's decoders; VARA by xara-monitor, packet by direwolf, JS8 by JS8Call), text scrolling below. Use SSB with the Digital bandwidth. It tunes itself to the strongest signal in the passband and follows it; a new station starts a new line. The strip under the row is the audio spectrum the decoder sees (300-2900 Hz): click a signal to lock onto it (RTTY: either tone), the wheel nudges 10 Hz, right-click for Auto tune (back to following the strongest). The dashed line is the squelch; the marker on the signal (two for RTTY) is green while decoding. The status line says auto or locked. The dB box is the squelch: decoding starts when the signal is that far above the noise and stops 3 dB lower. The line next to it shows the tone frequency, signal to noise, open/closed, and for RTTY the alternation (near -1 = real RTTY) or for CW the speed. Right-click the row, the strip or the text for Auto tune, RTTY settings (presets, baud, shift, mark low) and Clear. Scroll up to read back; it stops following until you scroll to the bottom again. VARA: USB, Digital bandwidth, signal centered (VARA sits at 1500 Hz audio; the shaded part of the strip is its bandwidth, right-click > VARA to change it: 500, 2300 or 2750 Hz). Every packet xara-monitor hears is a line: calls, frame types, SN, KISS text. Log (on the Decoder line, remembered) appends everything the text box shows to a file next to shack.py named for the decoder: vara.log, rtty.log, cw.log, packet.log, js8.log, with a dated header each time logging starts or the decoder changes. A station it can't name shows as #hash (a code made from the callsign); calls.txt next to shack.py lists the callsigns it should name, read each time VARA starts. shack adds every callsign xara-monitor decodes (CQ, header and link lines, and 'de CALL' in VarAC text); add your own and your regulars by hand, one per line. It needs xara-monitor on the PATH or in ~/installed/builds/xara/target/release. Packet: AX.25, FX.25 and IL2P, by direwolf (on the PATH or in ~/installed/builds/direwolf/build/src); right-click > Packet: 300 baud (HF: tones 1600/1800 Hz, either sideband, tune so they sit in the shaded part) or 1200 baud (VHF FM, needs an FM radio). Each packet is a line: who to whom, via, text; APRS ones get a second line decoding it. direwolf complains about packets that aren't APRS (BBS and node IDs: 'Unknown APRS Data Type', 'Use of ID is obsolete'); those lines are hidden unless you untick right-click > Packet > Hide complaints. JS8: through JS8Call (started if it isn't running; Off and closing shack leave it running): shack plays Digital out into a sound device of its own, shack-JS8, and shows what JS8Call decodes (UTC, SNR, offset, text), a message's frames joined on one line as in JS8Call's Band Activity. Once, in JS8Call: Settings > Audio, Input = Monitor of shack-JS8; Settings > Reporting, Enable UDP Server API, 127.0.0.1 port 2242. The status line says when JS8Call is connected (it pings every 15 s). JS8 needs the clock right to a second or so. Settings are shared with decode.py (decode.json). Only one program can listen on UDP 7355: close decode.py, or an xara-monitor fed by nc, first.</p>

<h3>Bookmarks (right)</h3>
<p>One list for all radios, kept in bookmarks.db next to shack.py. Double-click: tune there, with the settings this radio had when it was saved. Grey ones are out of this radio's range or modes. Yellow ones are at the current frequency (any mode). Save asks for a name for a new bookmark (blank is fine; Cancel doesn't save); saving one that's already there just updates its settings. Right-click: rename or delete. Most used are at the top. Each radio starts where it was when you last closed it.</p>

<h3>IC-705, Xiegu x6200</h3>
<p>Frequency, VFO A/B, mode, filter, volume, RF gain, AGC, Preamp, ATT, bookmarks (they keep filter, volume, RF gain, AGC, Preamp, ATT, NB and NR and their levels; never TX power), S-meter, transmit power and PTT. The PTT button keys the radio (click again to stop; it unkeys itself after 30 s), and TX/RX next to it shows what the radio reports, whoever keyed it. SWR is the radio's meter, read while it transmits (it keeps the last reading). Power is % of the maximum on the 705 and watts on the Xiegu (1-8 W over CI-V; set below 1 W on the radio it shows ?); drag the slider (it's sent when you let go), or click it and use the arrow keys. Noise blanker and Noise reduction switch the radio's own NB and NR on and off; the % box next to each is its level, usable while it's on. The PTT and Power lines are greyed outside the ham bands, and the PTT port refuses to key there too. Filter picks the radio's FIL1, FIL2 or FIL3 for the current mode: three presets per mode, each with its own width (set in the radio's menu); the width shown is the radio's report for the one in use (none in FM). Changing mode from here lands on FIL1. VFO A and B pick the radio's VFO; CI-V can't read which one the radio is on, so neither is lit until you click one (and switching on the radio itself doesn't show here). TX shows red while transmitting. PTT port 4532 on the status bar is for xara (rigctld PTT); the radio is unkeyed whenever it is closed.</p>
<p>Scope (IC-705): Waterfall shows the radio's own band scope, sent over CI-V about four times a second: spectrum on top, waterfall under it, red line at the dial. Click anywhere on it to tune there; Shift-click a signal to tune so it lands at 1500 Hz audio, the middle of the passband where VARA, ARDOP and the decoders listen (USB and LSB; other modes tune straight there). Pause freezes it and the audio spectrum strip together (either Pause button; both light up) for a closer look at a signal; the radio keeps tuning. Turning it on puts the radio's scope in center mode; the span is how far it reaches either side of the dial, Ref the reference level (lower = weak signals brighter). Changing the span starts the waterfall over. The x6200 doesn't send its scope over CI-V (firmware as of 2026-11), so it has no Scope line.</p>

<h3>FTdx10</h3>
<p>Yaesu CAT on the radio's USB port (the first of its two serial ports, 38400 baud: the radio's CAT-1 RATE). Like the IC-705 without Filter, SWR and Scope: frequency, VFO A/B (read from the radio, so the one it's on is lit; switching here, shack puts back that VFO's Preamp, ATT, Roofing, AGC, NB and NR as they were when you left it, which the radio's own A/B key does by itself but a switch over CAT doesn't; the first switch to a VFO after opening keeps the current settings), mode, volume, RF gain, AGC, Preamp (IPO, AMP1, AMP2), ATT (off, 6, 12, 18 dB), Roofing (the radio's R FIL: 12 kHz, 3 kHz, 500 Hz), NB and NR with their levels, S-meter, power and PTT; bookmarks keep Preamp, ATT and Roofing too. Scope sets the radio's own scope (the picture stays on the radio's screen): display mode (3DSS or waterfall, L/N/S size; Center, Cursor or Fix, the Fix edges set on the radio), span, Ref level (-30 to +30 dB) and speed; they follow the radio when you change them there. Right-click the Scope line (its label or between the boxes) for Peak, Color, RBW, Marker and Hold. Power goes from 5 W up to what the radio's own menu allows (TX GENERAL > HF / 50M MAX POWER, and AM MAX POWER in AM); the slider stops there, and its tooltip says the limit. Raise it in the radio's menu. Decoders read its USB sound card (CODEC, the same name as the 705's: plug in one of the two at a time).</p>

<h3>Spectrum</h3>
<p>The button next to Help keeps the audio spectrum strip running whenever a radio is open, with or without a decoder: voice, music, whatever is in the passband (300-2900 Hz). It's remembered. Pause on the Decoder line freezes the strip, and the IC-705 waterfall with it, until you click it again; decoding carries on. The decoder area's colors are in styles.py (decoder).</p>

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

class Freq(QWidget):		# frequency readout that works like the waterfall page's: wheel or click on any digit tunes that digit
	changed = pyqtSignal(float)		# MHz, on every step and on Enter

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def __init__(self):
		super().__init__()
		self.setFont(QFont('DejaVu Sans Mono', 30, QFont.Weight.DemiBold))
		self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
		self.setMouseTracking(True)		# hover highlight without a button down
		self.setToolTip('Wheel over a digit tunes it; click its top half for up, bottom half for down.\nArrow keys: left/right picks a digit, up/down tunes it.\nOr type a frequency in MHz and press Enter (Esc cancels)')
		self.hz, self.lo, self.hi, self.ndig = 0, 30000, 2000000000, 10
		self.hover = None		# digit under the mouse
		self.cur = None		# digit the arrow keys tune
		self.typed = None		# MHz being typed; None = showing the frequency
		self.moved = 0		# when we last tuned: the radio's older reports mustn't pull the display back
		self.notches = 0		# wheel angle not yet used (touchpads send fractions of a notch)
		return
	# __init__

	'''-------------------------------------------------------------------
		top of the band in MHz: also how many digits there are
	-------------------------------------------------------------------'''

	def set_top(self, mhz):
		self.hi = round(mhz * 1e6)
		self.ndig = len(str(self.hi))
		self.cur, self.moved = None, 0		# a new radio: take its first report
		self.updateGeometry()
		self.update()
		return
	# set_top

	'''-------------------------------------------------------------------
		the radio's frequency; ignored for a second after we tune, so a slow report doesn't rubber-band the display
	-------------------------------------------------------------------'''

	def set_mhz(self, mhz):
		if time.monotonic() - self.moved < 1:
			return
		self.hz = round(mhz * 1e6)
		self.update()
		return
	# set_mhz

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def tune(self, hz):
		self.hz, self.moved = hz, time.monotonic()
		self.update()
		self.changed.emit(hz / 1e6)
		return
	# tune

	'''-------------------------------------------------------------------
		one step of digit i; a step off either end is ignored, as on the waterfall page
	-------------------------------------------------------------------'''

	def step(self, i, up):
		n = self.hz + (1 if up else -1) * 10 ** (self.ndig - 1 - i)
		if self.lo <= n <= self.hi:
			self.tune(n)
		return
	# step

	'''-------------------------------------------------------------------
		(x, width, digit index or None, text) for each character, left to right; dots between groups of three
	-------------------------------------------------------------------'''

	def cells(self):
		cw = self.fontMetrics().horizontalAdvance('0')
		out, x = [], 4
		for i, c in enumerate(str(self.hz).zfill(self.ndig)[-self.ndig:]):
			out.append((x, cw, i, c))
			x += cw
			if i < self.ndig - 1 and (self.ndig - 1 - i) % 3 == 0:
				out.append((x, cw * 2 // 3, None, '.'))
				x += cw * 2 // 3
		return out
	# cells

	def digit_at(self, px):
		return next((i for x, w, i, t in self.cells() if i is not None and x <= px < x + w), None)

	def unit_font(self):
		f = QFont(self.font())
		f.setPointSize(self.font().pointSize() * 2 // 5)
		return f

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def sizeHint(self):
		x, w, i, t = self.cells()[-1]
		return QSize(x + w + QFontMetrics(self.unit_font()).horizontalAdvance('  MHz') + 8, self.fontMetrics().height() + 8)

	def minimumSizeHint(self):
		return self.sizeHint()

	'''-------------------------------------------------------------------
		digits, leading zeros and dots dimmed, the hovered digit shaded, the arrow keys' digit underlined
	-------------------------------------------------------------------'''

	def paintEvent(self, e):
		p = QPainter(self)
		ink = self.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText)		# not the current group: some themes grey the inactive one (window without focus)
		dim = QColor('#a0a0a0')
		shade = QColor(self.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight))
		shade.setAlpha(50)
		h = self.height()
		if self.typed is not None:		# typing: show what's typed, nothing else
			p.setPen(ink)
			p.drawText(QRect(4, 0, self.width() - 4, h), Qt.AlignmentFlag.AlignVCenter, self.typed + '_')
			x = 4 + self.fontMetrics().horizontalAdvance(self.typed + '_')
			unit = 'MHz'
		else:
			first = next((i for i, c in enumerate(str(self.hz).zfill(self.ndig)) if c != '0'), self.ndig)
			for x, w, i, t in self.cells():
				if i is not None and i == self.hover:
					p.fillRect(QRect(x, 2, w, h - 4), shade)
				if i is not None and i == self.cur and self.hasFocus():
					p.fillRect(QRect(x + 2, h - 5, w - 4, 3), ink)
				p.setPen(ink if i is not None and i >= first and self.isEnabled() else dim)
				p.drawText(QRect(x, 0, w, h), Qt.AlignmentFlag.AlignCenter, t)
			x += w
			unit = 'Hz'
		p.setPen(dim)
		p.setFont(self.unit_font())
		p.drawText(QRect(x + 6, 0, self.width() - x, h), Qt.AlignmentFlag.AlignVCenter, unit)
		return
	# paintEvent

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def wheelEvent(self, e):
		i = self.digit_at(e.position().x())
		if i is None:
			return e.ignore()
		self.notches += e.angleDelta().y()
		while abs(self.notches) >= 120:
			self.step(i, self.notches > 0)
			self.notches -= 120 if self.notches > 0 else -120
		e.accept()
		return
	# wheelEvent

	def mousePressEvent(self, e):		# top half up, bottom half down
		i = self.digit_at(e.position().x())
		if i is not None and e.button() == Qt.MouseButton.LeftButton:
			self.cur = i
			self.step(i, e.position().y() < self.height() / 2)
		return

	def mouseMoveEvent(self, e):
		i = self.digit_at(e.position().x())
		self.setCursor(Qt.CursorShape.SizeVerCursor if i is not None else Qt.CursorShape.ArrowCursor)
		if i != self.hover:
			self.hover = i
			self.update()
		return

	def leaveEvent(self, e):
		self.hover = None
		self.update()
		return

	def focusOutEvent(self, e):		# leaving drops a half-typed frequency
		self.typed = None
		self.update()
		return super().focusOutEvent(e)

	'''-------------------------------------------------------------------
		typing a frequency in MHz (digits and a point), or arrows on a digit
	-------------------------------------------------------------------'''

	def keyPressEvent(self, e):
		k, t = e.key(), e.text()
		if self.typed is not None:
			if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
				return self.enter()
			elif k == Qt.Key.Key_Escape:
				self.typed = None
			elif k == Qt.Key.Key_Backspace:
				self.typed = self.typed[:-1] or None
			elif t and t in '0123456789.':
				self.typed += t
			else:
				return super().keyPressEvent(e)
		elif t and t in '0123456789.':
			self.typed = t
		elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right):
			c = self.ndig - 4 if self.cur is None else self.cur		# starts on the kHz digit
			self.cur = max(0, min(self.ndig - 1, c + (1 if k == Qt.Key.Key_Right else -1)))
		elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
			if self.cur is None:
				self.cur = self.ndig - 4
			self.step(self.cur, k == Qt.Key.Key_Up)
		else:
			return super().keyPressEvent(e)
		self.update()
		return
	# keyPressEvent

	def enter(self):		# out of range or not a number: beep and leave it to be fixed
		try:
			n = round(float(self.typed) * 1e6)
		except ValueError:
			n = -1
		if not self.lo <= n <= self.hi:
			return QApplication.beep()
		self.typed = None
		self.tune(n)
		return
	# enter

# class Freq


'''===================================================================================================================================================

==================================================================================================================================================='''

class Bookmarks:		# bookmarks.db next to shack.py: one list for every radio; a row keeps settings per radio, {radio: {key: value}}

	'''-------------------------------------------------------------------
		opens (or makes) the database; a new one gets rspdx's settings.db rows copied in
	-------------------------------------------------------------------'''

	def __init__(self, path, old):
		new = not Path(path).exists()
		self.db = sqlite3.connect(path)
		self.db.execute("CREATE TABLE IF NOT EXISTS bookmarks (hz INTEGER NOT NULL, mode TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', hits INTEGER NOT NULL DEFAULT 1,"
			" settings TEXT NOT NULL DEFAULT '{}', updated TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (hz, mode))")
		self.db.execute("CREATE TABLE IF NOT EXISTS last (radio TEXT PRIMARY KEY, hz INTEGER NOT NULL, mode TEXT NOT NULL, settings TEXT NOT NULL DEFAULT '{}')")		# where each radio was left
		if new and Path(old).exists():
			self.copy_rspdx(old)
		self.db.commit()
		return
	# __init__

	'''-------------------------------------------------------------------
		one-time copy of rspdx's settings.db (its -1 = never saved, left out)
	-------------------------------------------------------------------'''

	def copy_rspdx(self, old):
		src = sqlite3.connect(old)
		cols = ('v', 'if', 'lna', 'ant', 'notch', 'dab', 'hdr', 'bw', 'nb', 'nr')
		for hz, mode, name, hits, *vals in src.execute('SELECT frequency_hz, mode, name, hits, volume, if_reduction, lna, antenna, rf_notch, dab_notch, hdr, bw, nb, nr FROM settings'):
			s = {k: v for k, v in zip(cols, vals) if not (k in ('bw', 'nb', 'nr') and v < 0)}
			self.db.execute('INSERT OR IGNORE INTO bookmarks (hz, mode, name, hits, settings) VALUES (?, ?, ?, ?, ?)', (hz, mode.upper(), name, hits, json.dumps({'RSPdx': s})))
		row = src.execute('SELECT frequency_hz, mode FROM last_frequency ORDER BY saved_at DESC LIMIT 1').fetchone()
		if row:
			b = self.find(row[0], row[1].upper())
			self.set_last('RSPdx', row[0], row[1].upper(), b['settings'].get('RSPdx', {}) if b else {})
		src.close()
		log.info(f'bookmarks: copied {self.db.execute("SELECT count(*) FROM bookmarks").fetchone()[0]} from {old}')
		return
	# copy_rspdx

	'''-------------------------------------------------------------------
		all bookmarks, most picked first
	-------------------------------------------------------------------'''

	def list(self):
		out = []
		for hz, mode, name, hits, s in self.db.execute('SELECT hz, mode, name, hits, settings FROM bookmarks ORDER BY hits DESC, hz'):
			out.append(dict(hz=hz, mode=mode, name=name, hits=hits, settings=json.loads(s)))
		return out
	# list

	def find(self, hz, mode):
		return next((b for b in self.list() if b['hz'] == hz and b['mode'] == mode), None)

	'''-------------------------------------------------------------------
		Save: adds the bookmark with its name, or updates this radio's settings in it (other radios' settings, name and hits stay)
	-------------------------------------------------------------------'''

	def save(self, radio, hz, mode, settings, name=''):
		b = self.find(hz, mode)
		s = b['settings'] if b else {}
		s[radio] = settings
		self.db.execute('INSERT INTO bookmarks (hz, mode, name, settings) VALUES (?, ?, ?, ?) ON CONFLICT (hz, mode) DO UPDATE SET settings = excluded.settings, updated = CURRENT_TIMESTAMP',
			(hz, mode, name, json.dumps(s)))
		self.db.commit()
		return
	# save

	def picked(self, b):
		self.db.execute('UPDATE bookmarks SET hits = hits + 1 WHERE hz = ? AND mode = ?', (b['hz'], b['mode']))
		self.db.commit()

	def rename(self, b, name):
		self.db.execute('UPDATE bookmarks SET name = ? WHERE hz = ? AND mode = ?', (name, b['hz'], b['mode']))
		self.db.commit()

	def delete(self, b):
		self.db.execute('DELETE FROM bookmarks WHERE hz = ? AND mode = ?', (b['hz'], b['mode']))
		self.db.commit()

	'''-------------------------------------------------------------------
		where a radio was left: (hz, mode, settings) or None
	-------------------------------------------------------------------'''

	def last(self, radio):
		row = self.db.execute('SELECT hz, mode, settings FROM last WHERE radio = ?', (radio,)).fetchone()
		return (row[0], row[1], json.loads(row[2])) if row else None

	def set_last(self, radio, hz, mode, settings):
		self.db.execute('INSERT OR REPLACE INTO last (radio, hz, mode, settings) VALUES (?, ?, ?, ?)', (radio, hz, mode, json.dumps(settings)))
		self.db.commit()
		return
	# set_last

# class Bookmarks


'''===================================================================================================================================================

==================================================================================================================================================='''

class Rspdx:		# rspdx -n (RSPdx) as a child process: commands on its stdin, JSON lines on its stdout; same face as civ.Rig
	MODES = ['LSB', 'USB', 'AM', 'WBFM']
	BANDWIDTHS = ['Narrow voice  1.8 kHz', 'Normal voice  2.8 kHz', 'Wide voice  3.1 kHz', 'Digital  2.8 kHz']		# bw 0-3, SSB only
	SWITCHES = {'notch': 'RF notch', 'dab': 'DAB notch', 'hdr': 'HDR', 'digi': 'Digital out', 'wf': 'Waterfall', 'nb': 'Noise blanker', 'nr': 'Noise reduction'}		# rspdx command: button

	'''-------------------------------------------------------------------
		starts the rspdx program (C++ demodulators) as a child process
	-------------------------------------------------------------------'''

	def __init__(self, path, emit, last=None):		# last: (hz, mode, settings) to start from, as Bookmarks.last gives it
		self.emit, self.ok, self.tx = emit, False, 0		# ok once rspdx has sent its first state
		self.start = last		# not self.last: that's rspdx's last stderr line
		self.state = {}		# rspdx's last state line
		self.last = ''		# last stderr line, for the box if rspdx dies
		self.p = QProcess()
		self.p.setWorkingDirectory(str(Path(path).parent))		# index.html, app.js if the waterfall comes back
		self.p.readyReadStandardOutput.connect(self.out)
		self.p.readyReadStandardError.connect(self.err)
		self.p.finished.connect(self.ended)
		self.p.errorOccurred.connect(lambda e: e == QProcess.ProcessError.FailedToStart and self.emit('gone', f"Couldn't run {path}.\n\n{self.p.errorString()}"))
		self.p.start(str(path), ['-n', *((f'{last[0] / 1e6:.6f}', last[1].lower()) if last else ())])		# no frequency: rspdx starts at its default
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
					if not self.ok and self.start:		# first state: rspdx is up, put back the settings it was left with
						self.apply(self.start[1], self.start[2])
					self.ok, self.state = True, m['state']
					self.show()
				elif m['type'] == 'notice':		# every command gets one; a refused command changes no state, so put the controls back
					self.emit('error', m['msg'])
					self.show()
				elif m['type'] == 'meter':		# every 250 ms
					self.emit('meter', (m['adc'], m['sig'], m['nbr']))
				elif m['type'] == 'bookmarks':		# rspdx's own list, empty under shack (bookmarks.db is shack's)
					pass
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
		what a bookmark keeps for the RSPdx, and putting it back
	-------------------------------------------------------------------'''

	KEEP = ('v', 'if', 'lna', 'ant', 'notch', 'dab', 'hdr', 'bw', 'nb', 'nr', 'ref')		# ref: the waterfall's reference level, dB

	def settings(self):
		return {k: self.state[k] for k in self.KEEP if k in self.state}

	def apply(self, mode, s):
		for k, v in s.items():
			if k == 'nb':		# 0 = off, else its trigger level (which turns it on)
				self.set('nbdb', v) if v else self.set('nb', 0)
			elif k == 'bw':
				if v >= 0 and mode in ('LSB', 'USB'):		# SSB only
					self.set('bw', v)
			elif k in self.KEEP:
				self.set(k, v)
		return
	# apply

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
		elif k == 'ref':
			self.send(f'ref {v:g}')
		elif k in self.SWITCHES:		# on/off
			self.send(f'{k} {int(v)}')
		elif k == 'nbdb':		# blanker level; rspdx turns the blanker on at it
			self.send(f'nb {v}')
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


'''===================================================================================================================================================

==================================================================================================================================================='''

class Spectrum(QWidget):		# the decoder's averaged audio spectrum, 300-2900 Hz: click a signal to lock onto it
	TOP = 40		# dB above the noise at the top of the strip

	'''-------------------------------------------------------------------
		owner: the Decoders it shows and tunes
	-------------------------------------------------------------------'''

	def __init__(self, owner):
		super().__init__()
		self.owner = owner
		self.setFixedHeight(80)
		self.setCursor(Qt.CursorShape.CrossCursor)
		self.setToolTip('Click a signal to decode it (RTTY: either tone). Wheel: nudge 10 Hz. Right-click: Auto tune, settings.\nDashed line: squelch. Marker green while decoding')
		self.shot = None		# Pause: the picture as it was, drawn instead of the live one
		return
	# __init__

	def pause(self, on):		# grab() paints the live picture before shot is set
		self.shot = self.grab() if on else None
		self.update()
		return

	def hz(self, x):
		return decode.F0 + x / self.width() * (decode.F1 - decode.F0)

	def px(self, hz):
		return round((hz - decode.F0) / (decode.F1 - decode.F0) * self.width())

	'''-------------------------------------------------------------------
		spectrum filled, 500 Hz grid and labels, squelch dashed, the tone(s) being decoded
	-------------------------------------------------------------------'''

	def paintEvent(self, e):
		p = QPainter(self)
		if self.shot:
			p.drawPixmap(self.rect(), self.shot)
			return
		w, h = self.width(), self.height() - 14		# bottom 14 px: Hz labels
		p.fillRect(self.rect(), QColor(styles.decoder['spectrum']))		# colors: styles.py
		if not decode:
			return
		p.setFont(QFont('DejaVu Sans', 7))
		for f in range(500, decode.F1, 500):
			x = self.px(f)
			p.setPen(QColor(styles.decoder['grid']))
			p.drawLine(x, 0, x, h)
			p.setPen(QColor(styles.decoder['labels']))
			p.drawText(QRect(x - 25, h, 50, 14), Qt.AlignmentFlag.AlignCenter, f'{f}')
		d = self.owner.dec
		if not d or d.tu.avg is None:
			return
		tu = d.tu
		y = lambda db: h - max(0.0, min(1.0, db / self.TOP)) * h
		edges = np.linspace(decode.F0, decode.F1, w + 1) / tu.df		# bins under each pixel column
		db = tu.db(tu.avg[:int(edges[-1]) + 1])
		col = np.maximum.reduceat(db, edges[:-1].astype(int))		# loudest bin in each column
		band = getattr(d, 'band', None)		# VARA, packet: the audio they decode, shaded
		if band:
			p.fillRect(QRect(self.px(band[0]), 0, self.px(band[1]) - self.px(band[0]), h), QColor(32, 160, 32, 60))
		pts = [QPointF(0, h)] + [QPointF(i, y(v)) for i, v in enumerate(col)] + [QPointF(w, h)]
		p.setPen(Qt.PenStyle.NoPen)
		p.setBrush(QColor(styles.decoder['fill']))
		p.drawPolygon(QPolygonF(pts))
		if tu.sq <= self.TOP:
			p.setPen(QPen(QColor(styles.decoder['squelch']), 1, Qt.PenStyle.DashLine))
			p.drawLine(0, round(y(tu.sq)), w, round(y(tu.sq)))
		if tu.center:
			sh = getattr(d, 'shift', 0)
			p.setPen(QPen(QColor('#20a020' if d.op else '#e08000'), 2))
			for f in ((tu.center - sh / 2, tu.center + sh / 2) if sh else (tu.center,)):
				p.drawLine(self.px(f), 0, self.px(f), h)
		return
	# paintEvent

	'''-------------------------------------------------------------------
		click: lock onto the strongest tone (RTTY: tone pair) near the click
	-------------------------------------------------------------------'''

	def mousePressEvent(self, e):
		d = self.owner.dec
		if d and e.button() == Qt.MouseButton.LeftButton:
			d.tune(d.tu.snap(self.hz(e.position().x()), getattr(d, 'shift', 0) / 2 + 40))
			self.update()
		return
	# mousePressEvent

	def wheelEvent(self, e):		# nudge 10 Hz, locks
		d = self.owner.dec
		if d and d.tu.center and e.angleDelta().y():
			d.tune(min(max(d.tu.center + (10 if e.angleDelta().y() > 0 else -10), decode.F0), decode.F1))
			self.update()
		e.accept()
		return
	# wheelEvent

# class Spectrum


'''===================================================================================================================================================

==================================================================================================================================================='''

class Waterfall(QWidget):		# a CI-V radio's own band scope (27 00 data): spectrum on top, waterfall under it, the dial marked; click tunes there
	ROWS = 200		# sweeps of history (about 50 s at the 705's 4 a second)
	SPEC = 70		# px of spectrum above the waterfall
	TOP = 160		# the 705's scope level at full scale (0xA0)
	STOPS = ((0, '#000000'), (30, '#000050'), (60, '#0000ff'), (85, '#00e0ff'), (110, '#ffff00'), (135, '#ff0000'), (160, '#ffffff'))		# level: color

	'''-------------------------------------------------------------------
		tune(hz, center): what a click does; center = Shift held (Shack.wf_click)
	-------------------------------------------------------------------'''

	def __init__(self, tune):
		super().__init__()
		self.tune = tune
		self.setFixedHeight(self.SPEC + self.ROWS + 14)		# bottom 14 px: kHz labels
		self.setCursor(Qt.CursorShape.CrossCursor)
		self.setToolTip('The radio\'s band scope. Click: tune there.  Shift-click: tune so that signal lands at 1500 Hz audio (VARA, ARDOP, the decoders\' middle).  Red line: the dial')
		self.lo, self.hi, self.dial = 0, 0, 0		# Hz across the width, Hz of the dial
		self.n = 0		# points per sweep
		self.rows = bytearray()		# newest sweep first, ROWS * n levels
		self.paused = False		# Pause: sweeps are dropped, the picture stays
		self.colors = self.table()
		return
	# __init__

	'''-------------------------------------------------------------------
		256 colors for Indexed8: STOPS blended, white above the top
	-------------------------------------------------------------------'''

	def table(self):
		out = []
		for v in range(256):
			(a, ca), (b, cb) = next(((s, t) for s, t in zip(self.STOPS, self.STOPS[1:]) if v <= t[0]), (self.STOPS[-1], self.STOPS[-1]))
			f = (v - a) / (b - a) if b > a else 1
			ca, cb = QColor(ca), QColor(cb)
			out.append(QColor(round(ca.red() + f * (cb.red() - ca.red())), round(ca.green() + f * (cb.green() - ca.green())), round(ca.blue() + f * (cb.blue() - ca.blue()))).rgb())
		return out
	# table

	'''-------------------------------------------------------------------
		one sweep from civ.Rig: <II lo hi Hz, then a level byte per point; a new width (span, points) starts the waterfall over
	-------------------------------------------------------------------'''

	def add(self, v):
		lo, hi = struct.unpack('<II', v[:8])
		d = v[8:]
		if len(d) < 2 or self.paused:
			return
		if len(d) != self.n or hi - lo != self.hi - self.lo:
			self.n, self.rows = len(d), bytearray(len(d) * self.ROWS)
		self.lo, self.hi = lo, hi
		self.rows[len(d):] = self.rows[:-len(d)]
		self.rows[:len(d)] = d
		self.update()
		return
	# add

	def clear(self):
		self.n, self.rows = 0, bytearray()
		self.update()

	def pause(self, on):
		self.paused = on
		self.update()

	def px(self, hz):
		return round((hz - self.lo) / (self.hi - self.lo) * self.width())

	'''-------------------------------------------------------------------
		spectrum (newest sweep), grid and kHz labels, waterfall, dial line
	-------------------------------------------------------------------'''

	def paintEvent(self, e):
		p = QPainter(self)
		w, s, h = self.width(), self.SPEC, self.SPEC + self.ROWS
		p.fillRect(self.rect(), QColor(styles.decoder['spectrum']))
		if not self.n or self.hi <= self.lo:
			p.setPen(QColor(styles.decoder['labels']))
			p.drawText(QRect(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, 'waiting for the radio\'s scope…')
			return
		raw = bytes(self.rows)		# QImage doesn't copy: keep it alive while drawing
		img = QImage(raw, self.n, self.ROWS, self.n, QImage.Format.Format_Indexed8)
		img.setColorTable(self.colors)
		p.drawImage(QRect(0, s, w, self.ROWS), img)
		span = self.hi - self.lo
		step = next((x for x in (500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000) if span / x <= 10), 500000)
		p.setFont(QFont('DejaVu Sans', 7))
		for f in range((self.lo // step + 1) * step, self.hi, step):
			x = self.px(f)
			p.setPen(QColor(styles.decoder['grid']))
			p.drawLine(x, 0, x, s)
			p.setPen(QColor(styles.decoder['labels']))
			if 30 <= x <= w - 30:		# no half labels at the edges
				p.drawText(QRect(x - 30, h, 60, 14), Qt.AlignmentFlag.AlignCenter, f'{f / 1e6:.4f}'.rstrip('0').rstrip('.') if step < 10000 else f'{f / 1e6:.3f}')
		pts = [QPointF(0, s)] + [QPointF(i * w / (self.n - 1), s - min(c, self.TOP) / self.TOP * s) for i, c in enumerate(self.rows[:self.n])] + [QPointF(w, s)]
		p.setPen(Qt.PenStyle.NoPen)
		p.setBrush(QColor(styles.decoder['fill']))
		p.drawPolygon(QPolygonF(pts))
		if self.lo <= self.dial <= self.hi:
			p.setPen(QPen(QColor(255, 60, 60, 200), 1))
			p.drawLine(self.px(self.dial), 0, self.px(self.dial), h)
		return
	# paintEvent

	'''-------------------------------------------------------------------
		click: the frequency under the mouse, to 10 Hz on narrow spans, 100 Hz on wide ones
	-------------------------------------------------------------------'''

	def mousePressEvent(self, e):
		if self.n and e.button() == Qt.MouseButton.LeftButton:
			hz = self.lo + e.position().x() / self.width() * (self.hi - self.lo)
			snap = 10 if self.hi - self.lo <= 20000 else 100
			self.tune(round(hz / snap) * snap, bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
		return
	# mousePressEvent

# class Waterfall


'''===================================================================================================================================================

==================================================================================================================================================='''

class Helper:		# a decoder program run as a child: Digital out goes to its stdin, its lines to the text box (xara-monitor for VARA, direwolf for packet). Same face as decode.Rtty / Cw
	PROGRAMS = {'VARA': ('xara-monitor', '~/installed/builds/xara/target/release/xara-monitor'), 'Packet': ('direwolf', '~/installed/builds/direwolf/build/src/direwolf'),
		'JS8': ('pacat', '/usr/bin/pacat')}		# kind: (name on the PATH, where else to look). JS8: pacat plays the audio into a sound device JS8Call listens to
	CHATTER = ('Dire Wolf', 'Includes optional', 'Reading config', 'Audio input', 'Audio out', 'Channel 0:', 'Note: PTT', 'Disabled', 'Running this as root', 'End of file')		# direwolf's start-up and exit lines: shack.log only

	'''-------------------------------------------------------------------
		args: the program's options; band: (Hz, Hz) of audio it decodes, shaded on the strip
		add(text): lines to the box; gone(why): it stopped by itself
	-------------------------------------------------------------------'''

	def __init__(self, kind, args, band, add, gone):
		path = self.find(kind)
		self.name, self.band, self.add, self.gone = self.PROGRAMS[kind][0], band, add, gone
		self.tu = decode.Tuner(None, 99, 0, 4)		# spectrum only; a 99 dB squelch never picks a signal
		self.op, self.last = False, ''		# last: its last stderr line
		self.hush = kind == 'Packet'		# direwolf talks a lot before its first packet (VOX advice...): shack.log until then
		self.drop = ()		# line starts not to show at all
		self.seen = None		# seen(line): every decoded line, before it's shown (VARA: callsigns for calls.txt)
		self.gain = 1.0		# on the audio it gets
		self.p = QProcess()
		self.p.readyReadStandardOutput.connect(self.out)
		self.p.readyReadStandardError.connect(self.err)
		self.p.finished.connect(self.ended)
		self.p.errorOccurred.connect(lambda e: e == QProcess.ProcessError.FailedToStart and self.gone(f"couldn't run {path}: {self.p.errorString()}"))
		self.p.start(path, args)
		return
	# __init__

	@classmethod
	def find(cls, kind):		# path of the program, None if it isn't there
		name, alt = cls.PROGRAMS[kind]
		return next((p for p in (shutil.which(name), str(Path(alt).expanduser())) if p and Path(p).is_file()), None)

	'''-------------------------------------------------------------------
		audio in: to the spectrum and to the program's stdin as s16le
	-------------------------------------------------------------------'''

	def __call__(self, x):
		self.tu(x)
		if self.p.state() == QProcess.ProcessState.Running:
			self.p.write((np.clip(x * self.gain, -1, 1) * 32767).astype('<i2').tobytes())
		return '', False
	# __call__

	def info(self):
		return ''

	def tune(self, f):		# the program finds the signal itself
		return

	'''-------------------------------------------------------------------
		stdout: decoded lines (start-up and exit chatter to shack.log); stderr: to shack.log
	-------------------------------------------------------------------'''

	def out(self):
		while self.p.canReadLine():
			line = bytes(self.p.readLine()).decode(errors='replace').rstrip()
			if self.hush and 'audio level' in line:		# a packet's first line
				self.hush = False
			if line.startswith(self.drop):
				continue
			if self.hush or line.startswith(self.CHATTER) or not line.strip():
				log.info(f'{self.name}: {line}') if line.strip() else None
			else:
				if self.seen:
					self.seen(line)
				self.add(line + '\n')
		return
	# out

	def err(self):
		for line in bytes(self.p.readAllStandardError()).decode(errors='replace').splitlines():
			if line.strip():
				self.last = line.strip()
				log.info(f'{self.name}: {self.last}')
		return
	# err

	def ended(self, code, status):
		self.gone(f'{self.name} stopped: {self.last or "exit " + str(code)} (see shack.log)')
		return

	'''-------------------------------------------------------------------
		EOF on its stdin is the program's cue to finish
	-------------------------------------------------------------------'''

	def close(self):
		self.p.finished.disconnect(self.ended)
		self.p.closeWriteChannel()
		if not self.p.waitForFinished(2000):
			self.p.kill()
			self.p.waitForFinished(1000)
		return
	# close

# class Helper


'''===================================================================================================================================================

==================================================================================================================================================='''

class Scope:		# Spectrum button with no decoder running: the strip alone. Same face as decode.Rtty / Cw
	def __init__(self):
		self.tu = decode.Tuner(None, 99, 0, 4)		# a 99 dB squelch never picks a signal: no marker, no squelch line
		self.op = False

	def __call__(self, x):
		self.tu(x)
		return '', False

	def info(self):
		return ''

	def tune(self, f):
		return

# class Scope


'''===================================================================================================================================================

==================================================================================================================================================='''

class Decoders:		# decode.py's RTTY and CW, VARA (xara-monitor), packet (direwolf) and JS8 (JS8Call), on Digital out (UDP 7355): a row of controls (self.row), the spectrum (self.spec) and the text they decode (self.text)
	KINDS = ['Off', 'RTTY', 'CW', 'VARA', 'Packet', 'JS8']
	SINK, JS8PORT = 'shack_js8', 2242		# JS8: null sink JS8Call's input listens to (its monitor); JS8Call's UDP API port
	PACKET = {300: (1550, 1850), 1200: (1150, 2250)}		# baud: audio band, mark and space tones inside it
	NAGS = ('ERROR!!!  Unknown APRS Data Type', 'Use of "ID" in the destination', 'Tell the sender')		# direwolf on packets that aren't APRS (BBS and node IDs)
	CALLS = HERE / 'calls.txt'		# callsigns xara-monitor shows by name instead of #hash (--calls); shack adds the ones it hears, you can add your own
	NAMED = re.compile(r'CQFRAME (\S+)|HEADER (\S+) → (\S+):|link (\S+) → (\S+) ended')		# xara-monitor lines that carry callsigns
	DE = re.compile(r'\bde ([A-Z0-9]+(?:-\d{1,2})?)\b')		# VarAC text: '15 de N5IMC <R+08>'
	CALL = re.compile(r'(?=.*\d)(?=.*[A-Z])[A-Z0-9]{3,7}(-\d{1,2})?')		# a callsign, not a #hash

	'''-------------------------------------------------------------------
		digi(): turns the radio's Digital out on
	-------------------------------------------------------------------'''

	def __init__(self, digi, port=7355):
		self.digi, self.port = digi, port
		self.alsa = None		# the open radio's USB sound card (ALSA name); None = the RSPdx's Digital out on UDP
		self.rec, self.carry, self.last = None, b'', ''		# arecord reading it; half a sample left over; arecord's last stderr line
		self.sk, self.dec, self.heard = None, None, 0
		self.kind, self.baud, self.shift, self.rev = 'RTTY', 1000 / 22, 170, False
		self.sq = {'RTTY': 6, 'CW': 10}		# squelch dB per decoder
		self.hf = 2300		# VARA bandwidth
		self.pk = 300		# packet baud
		self.hide = True		# hide direwolf's complaints about non-APRS packets
		self.api, self.js8 = None, None		# JS8: socket JS8Call's API sends to; (when it last pinged, its version)
		self.scope, self.ready = False, False		# Spectrum button: strip on with no decoder; a radio is open (shack sets it)
		self.lines = {}		# JS8: offset Hz: (cursor at the start of that message's line, when its last frame came, the line's start); a message's frames join one line
		self.logging, self.logged = False, None		# Log button; logged: the decoder the file's last header was for
		self.load()
		self.pick = QComboBox(toolTip='Decode Digital out: RTTY, CW, VARA, packet or JS8 (through JS8Call). Right-click for settings')
		self.pick.addItems(self.KINDS)
		self.pick.activated.connect(lambda i: self.start(self.pick.itemText(i)))
		self.sql = QSpinBox(minimum=0, maximum=30, suffix=' dB', keyboardTracking=False, enabled=False, toolTip='Squelch: decodes when the signal is this far above the noise, stops 3 dB lower')
		self.sql.valueChanged.connect(self.set_sq)
		self.status = QLabel(NODECODE and f"can't decode: {NODECODE}" or '')
		self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)		# changing text mustn't resize the window
		self.row = QWidget(toolTip='Right-click: Auto tune, RTTY, VARA and packet settings, Clear', objectName='decoderRow')
		self.row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)		# a plain QWidget paints a style sheet background only with this
		self.row.setStyleSheet(f"QWidget#decoderRow {{ background-color: {styles.decoder['row']}; border-radius: 4px; }}")		# colors: styles.py
		h = QHBoxLayout(self.row)
		h.setContentsMargins(4, 2, 4, 2)
		h.addWidget(self.pick)
		h.addWidget(self.sql)
		self.pauseb = QPushButton('Pause', checkable=True, toolTip='Freeze the spectrum strip and the waterfall (for a closer look); click again to go on. Decoding carries on')		# Shack.freeze
		h.addWidget(self.pauseb)
		self.logb = QPushButton('Log', checkable=True, checked=self.logging, toolTip='Keep what the decoder shows in a file next to shack.py: vara.log, rtty.log, cw.log, packet.log, js8.log')
		self.logb.clicked.connect(self.set_log)
		h.addWidget(self.logb)
		h.addWidget(self.status, 1)
		self.text = QPlainTextEdit(readOnly=True, toolTip='Decoded text. Scroll up to read back; right-click for Clear and settings')
		self.text.setMaximumBlockCount(500)
		self.text.setFont(QFont('DejaVu Sans Mono', 11))
		self.text.setStyleSheet(f"QPlainTextEdit {{ background-color: {styles.decoder['text']}; }}")
		self.scopeb = QPushButton('Spectrum', checkable=True, checked=self.scope, toolTip='The audio spectrum strip, also with no decoder running (voice, music...)')
		self.scopeb.clicked.connect(self.set_scope)
		self.spec = Spectrum(self)
		for w in (self.row, self.spec, self.text):
			w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
			w.customContextMenuRequested.connect(lambda pos, w=w: self.menu(w, pos))
		self.timer = QTimer(timeout=self.poll, interval=100)
		return
	# __init__

	'''-------------------------------------------------------------------
		Off, RTTY, CW, VARA, Packet or JS8 from the combo; RSPdx: listens on the port and turns Digital out on; CI-V radio: records its USB sound card
	-------------------------------------------------------------------'''

	def start(self, kind):
		self.stop()
		if kind == 'Off':
			if not (self.scope and self.ready):
				return
			kind = 'Scope'		# no decoder, Spectrum on: the strip alone
		if not decode:
			quiet(self.pick, self.pick.setCurrentIndex, 0)
			return self.status.setText(f"can't decode: {NODECODE}")
		if kind in Helper.PROGRAMS and not Helper.find(kind):
			quiet(self.pick, self.pick.setCurrentIndex, 0)
			name, alt = Helper.PROGRAMS[kind]
			return self.status.setText(f"can't find {name} (on the PATH or at {alt})")
		why = self.record() if self.alsa else self.bind()
		if why:
			self.off()
			return self.status.setText(why)
		if kind == 'JS8':
			why = self.js8open()
			if why:
				self.off()		# closes what's open
				return self.status.setText(why)
		self.kind, self.heard = kind, time.monotonic()
		self.make()
		self.sql.setEnabled(kind not in Helper.PROGRAMS and kind != 'Scope')		# the programs have no squelch here
		self.digi()
		self.timer.start()
		if kind != 'Scope':
			self.save()
		return
	# start

	'''-------------------------------------------------------------------
		the audio: the RSPdx's Digital out on UDP, or arecord on a CI-V radio's USB sound card. return why not, or None
	-------------------------------------------------------------------'''

	def bind(self):
		self.sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		try:
			self.sk.bind(('127.0.0.1', self.port))
		except OSError as e:
			return f'UDP {self.port}: {e.strerror}. Is decode.py, xara-monitor or nc listening?'
		self.sk.setblocking(False)
		self.sk.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)		# room for a few seconds of audio if the window stalls (the kernel may cap it)
		return None
	# bind

	def record(self):
		self.rec, self.carry, self.last = QProcess(), b'', ''
		self.rec.readyReadStandardError.connect(self.recerr)
		self.rec.finished.connect(self.recended)
		self.rec.start('arecord', ['-q', '-D', self.alsa, '-f', 'S16_LE', '-r', '48000', '-c', '1', '-t', 'raw'])
		if not self.rec.waitForStarted(2000):
			return f"couldn't run arecord: {self.rec.errorString()}"
		return None
	# record

	def recerr(self):
		for line in bytes(self.rec.readAllStandardError()).decode(errors='replace').splitlines():
			if line.strip():
				self.last = line.strip()
				log.info(f'arecord: {self.last}')
		return

	def recended(self, code, status):		# by itself: the card is busy or gone
		self.recerr()
		self.rec = None		# already gone
		self.off()		# and whatever decoder program was running
		self.status.setText(f"arecord stopped: {self.last or 'exit ' + str(code)}. Radio's USB audio {self.alsa} plugged in? Another program using it?")
		return

	'''-------------------------------------------------------------------
		calls.txt: made the first time with a comment saying what it is; read again at each xara-monitor start, so hand edits count
	-------------------------------------------------------------------'''

	def calls(self):
		if not self.CALLS.exists():
			self.CALLS.write_text('# callsigns xara-monitor shows by name instead of #hash; one per line, # starts a comment.\n# shack adds the ones it hears (CQ, header, link lines); add yours and the stations you work.\n')
		self.known = {l.split('#')[0].split(',')[0].strip().upper() for l in self.CALLS.read_text().splitlines()} - {''}
		return self.CALLS
	# calls

	def named(self, line):		# a callsign xara-monitor decoded and calls.txt doesn't have yet: add it
		m = self.NAMED.search(line)
		for c in (*(m.groups() if m else ()), *self.DE.findall(line)):
			if c and self.CALL.fullmatch(c) and c not in self.known:
				self.known.add(c)
				with open(self.CALLS, 'a') as f:
					f.write(c + '\n')
				log.info(f'calls.txt: added {c}')
		return
	# heard

	'''-------------------------------------------------------------------
		a new decoder with the current settings
	-------------------------------------------------------------------'''

	def make(self, center=None):		# center: stay locked there; None = tune itself
		self.logged = None		# a new header in the log (VARA: its bandwidth may have changed)
		if self.kind == 'Scope':
			self.dec = Scope()
			return
		if self.kind in Helper.PROGRAMS:
			if self.kind == 'VARA':
				h = Helper('VARA', ['--stdin', '48000', '--hf', str(self.hf), '--calls', str(self.calls())], (1500 - self.hf / 2, 1500 + self.hf / 2), self.add, self.died)		# VARA sits at 1500 Hz
				h.seen = self.named
			elif self.kind == 'JS8':
				h = Helper('JS8', ['--playback', '--raw', '--format=s16le', '--rate=48000', '--channels=1', f'--device={self.SINK}', '--latency-msec=200'], (500, 2500), self.add, self.died)		# JS8 signals sit in 500-2500 Hz
			else:
				h = Helper('Packet', ['-c', self.dwconf(), '-t', '0'], self.PACKET[self.pk], self.add, self.died)
				h.drop = self.NAGS if self.hide else ()
				h.gain = 0.5		# Digital out arrives at direwolf's 'audio level' 70-100; it wants about 50 (above that it warns of distortion)
			if self.sk or self.rec:		# still on: a failed start has already turned it off
				self.dec = h
			return self.newline()
		if self.kind == 'RTTY':
			self.dec = decode.Rtty(self.baud, self.shift, self.rev, center, self.sq['RTTY'])
		else:
			self.dec = decode.Cw(center, self.sq['CW'])
		quiet(self.sql, self.sql.setValue, round(self.sq[self.kind]))
		self.newline()
		return
	# make

	'''-------------------------------------------------------------------
		stop() keeps the combo as it is (start uses it); off() also sets it to Off, for closing the radio
	-------------------------------------------------------------------'''

	def stop(self):
		self.timer.stop()
		if self.rec:
			self.rec.finished.disconnect(self.recended)
			self.rec.kill()
			self.rec.waitForFinished(1000)
			self.rec = None
		if isinstance(self.dec, Helper):
			self.dec.close()
		if self.sk:
			self.sk.close()
		if self.api:
			self.api.close()
		self.sk, self.dec, self.api, self.js8, self.lines = None, None, None, None, {}
		self.sql.setEnabled(False)
		self.status.setText('')
		self.spec.update()
		return
	# stop

	def off(self):
		self.stop()
		quiet(self.pick, self.pick.setCurrentIndex, 0)
		return

	'''-------------------------------------------------------------------
		Spectrum button: on with no decoder starts the strip alone; off stops it (a decoder keeps its strip)
	-------------------------------------------------------------------'''

	def set_scope(self, on):
		self.scope = on
		if on and self.ready and not self.dec:
			self.start('Off')
		elif not on and self.kind == 'Scope' and self.dec:
			self.off()
		self.save()
		return
	# set_scope

	def opened(self, alsa):		# a radio was opened: where its audio comes from (None = RSPdx Digital out); the strip starts if Spectrum is on
		self.alsa, self.ready = alsa, True
		if self.scope:
			self.start('Off')
		return

	def closed(self):		# the radio is being closed
		self.ready = False
		self.off()
		return

	def died(self, why):		# the decoder program stopped by itself
		self.dec = None		# already gone, nothing to close
		self.off()
		self.status.setText(why)
		return

	'''-------------------------------------------------------------------
		every 100 ms: decode whatever audio has arrived, then the status line
	-------------------------------------------------------------------'''

	def poll(self):
		if self.rec:		# arecord's output, whole samples
			b = self.carry + bytes(self.rec.readAllStandardOutput())
			n = len(b) & ~1
			raw, self.carry = b[:n], b[n:]
		else:
			data = []
			while True:
				try:
					data.append(self.sk.recv(4096))
				except BlockingIOError:
					break
			raw = b''.join(data)
		if raw:
			self.heard = time.monotonic()
			txt, new = self.dec(np.frombuffer(raw, '<i2') / 32768.0)
			if new:
				self.newline()
			if txt:
				self.add(txt)
		if self.api:
			self.js8in()
		tu = self.dec.tu
		if time.monotonic() - self.heard > 1:
			t = f'no audio from {self.alsa}' if self.alsa else 'no audio: is Digital out on?'
		elif self.kind == 'Scope':
			t = 'spectrum only'
		elif self.kind == 'VARA':
			t = f'{self.hf} Hz   xara-monitor listening'
		elif self.kind == 'Packet':
			t = f'{self.pk} baud   direwolf listening'
		elif self.kind == 'JS8':
			ok = self.js8 and time.monotonic() - self.js8[0] < 40		# it pings every 15 s
			t = f'JS8Call {self.js8[1]} connected' if ok else f'waiting for JS8Call: running? Settings > Reporting > UDP Server API on, port {self.JS8PORT}? Input: Monitor of shack-JS8'
		else:
			t = ('locked  ' if tu.fixed else 'auto  ') + (f'{tu.center:.0f} Hz  {tu.snr:.0f} dB' if tu.center else '-- Hz') + ('  open  ' if self.dec.op else '  closed  ') + self.dec.info()
		if self.kind == 'RTTY':
			t = f'{self.bd(self.baud)} Bd  {self.shift} Hz{"  mark low" if self.rev else ""}    ' + t
		self.status.setText(t)
		self.spec.update()
		return
	# poll

	def bd(self, b):
		return '45.45' if b == 1000 / 22 else f'{b:g}'

	'''-------------------------------------------------------------------
		text at the end; follows it only if already at the bottom, so reading back isn't yanked away
	-------------------------------------------------------------------'''

	def add(self, txt):
		if self.logging and self.dec and self.kind in self.KINDS[1:]:
			self.write_log(txt)
		sb = self.text.verticalScrollBar()
		end = sb.value() >= sb.maximum() - 2
		c = QTextCursor(self.text.document())
		c.movePosition(QTextCursor.MoveOperation.End)
		c.insertText(txt)
		if end:
			sb.setValue(sb.maximum())
		return
	# add

	'''-------------------------------------------------------------------
		Log: decoded text appended to <decoder>.log, a dated header whenever logging starts or the decoder changes (xara's lines carry only the UTC time)
	-------------------------------------------------------------------'''

	def set_log(self, on):
		self.logging, self.logged = on, None
		self.save()
		return

	def write_log(self, txt):
		try:
			with open(HERE / f'{self.kind.lower()}.log', 'a') as f:
				if self.logged != self.kind:
					f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC  {self.kind}{f' {self.hf} Hz' if self.kind == 'VARA' else ''} ---\n")
					self.logged = self.kind
				f.write(txt)
		except OSError as e:
			self.logging = False
			self.logb.setChecked(False)
			self.status.setText(f'log: {e.strerror}')
		return
	# write_log

	def newline(self):
		if self.text.document().lastBlock().text():
			self.add('\n')
		return

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def set_sq(self, v):
		self.sq[self.kind] = v
		if self.dec:
			self.dec.tu.sq = v
		self.save()
		return
	# set_sq

	'''-------------------------------------------------------------------
		VARA bandwidth changed: a new xara-monitor if VARA is running
	-------------------------------------------------------------------'''

	def vara(self, hf):
		self.hf = hf
		if self.kind == 'VARA' and isinstance(self.dec, Helper):
			self.dec.close()
			self.make()
		self.save()
		return
	# vara

	'''-------------------------------------------------------------------
		packet speed changed: a new direwolf if packet is running
	-------------------------------------------------------------------'''

	def packet(self, baud):
		self.pk = baud
		if self.kind == 'Packet' and isinstance(self.dec, Helper):
			self.dec.close()
			self.make()
		self.save()
		return
	# packet

	'''-------------------------------------------------------------------
		direwolf's "Unknown APRS Data Type" and "Use of ID is obsolete" lines on BBS and node IDs: hidden or shown, from now on
	-------------------------------------------------------------------'''

	def nags(self, hide):
		self.hide = hide
		if self.kind == 'Packet' and isinstance(self.dec, Helper):
			self.dec.drop = self.NAGS if hide else ()
		self.save()
		return
	# nags

	'''-------------------------------------------------------------------
		JS8: the null sink pacat plays into (made once, left in place so JS8Call keeps its input), JS8Call itself if it isn't running,
		and the port JS8Call's API sends to. returns why it can't, or None
	-------------------------------------------------------------------'''

	def js8open(self):
		try:
			if self.SINK not in subprocess.run(['pactl', 'list', 'short', 'sinks'], capture_output=True, text=True, timeout=5).stdout:
				subprocess.run(['pactl', 'load-module', 'module-null-sink', f'sink_name={self.SINK}', 'sink_properties=device.description=shack-JS8', 'rate=48000', 'channels=1'],
					check=True, capture_output=True, timeout=5)
				log.info(f'made the {self.SINK} sink for JS8Call')
		except (OSError, subprocess.SubprocessError) as e:
			log.error(f'JS8 sink: {e!r}')
			return f"couldn't make the shack-JS8 sound device (pactl: {e})"
		if subprocess.run(['pgrep', '-x', 'js8call'], capture_output=True).returncode != 0:		# not running: start it, on its own (Off or closing shack leaves it running)
			if not shutil.which('js8call'):
				return "can't find js8call on the PATH"
			ok, pid = QProcess.startDetached('js8call', [])
			log.info(f'started js8call, pid {pid}' if ok else "couldn't start js8call")
			if not ok:
				return "couldn't start js8call (see shack.log)"
		self.api = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		try:
			self.api.bind(('127.0.0.1', self.JS8PORT))
		except OSError as e:
			self.api.close()
			self.api = None
			return f'UDP {self.JS8PORT}: {e.strerror}. Another program listening to JS8Call?'
		self.api.setblocking(False)
		return None
	# js8open

	'''-------------------------------------------------------------------
		JS8Call's API messages: PING every 15 s, CLOSE when it quits, RX.ACTIVITY for each decoded frame (shown)
	-------------------------------------------------------------------'''

	def js8in(self):
		while True:
			try:
				m = json.loads(self.api.recv(65536))
			except BlockingIOError:
				break
			except ValueError:
				continue
			kind, p = m.get('type'), m.get('params', {})
			if kind == 'PING':
				self.js8 = (time.monotonic(), p.get('VERSION', ''))
			elif kind == 'CLOSE':
				self.js8 = None
			elif kind == 'RX.ACTIVITY':
				self.frame(p.get('OFFSET', 0), p.get('SNR', 0), p.get('UTC', 0), m.get('value', '').strip())
		return
	# js8in

	'''-------------------------------------------------------------------
		one JS8 frame: joins the open line at its offset (within 10 Hz, its last frame less than a minute ago) like JS8Call's
		Band Activity, or starts a line 'UTC  SNR  offset  text'. The end-of-message mark closes the line
	-------------------------------------------------------------------'''

	def frame(self, off, snr, utc, text):
		now = time.monotonic()
		self.lines = {o: v for o, v in self.lines.items() if now - v[1] < 60}
		hit = next((o for o in self.lines if abs(o - off) <= 10), None)
		c, head = self.lines.pop(hit)[0::2] if hit is not None else (None, None)		# c: start of the line; text added at the end of the box doesn't move it
		if c and c.block().text().startswith(head):		# still that line (not dropped off the top of the box)
			e = QTextCursor(c)
			e.movePosition(QTextCursor.MoveOperation.EndOfBlock)
			e.insertText(' ' + text)
		else:
			self.newline()
			head = f"{time.strftime('%H:%M:%S', time.gmtime(utc / 1000))}  {snr:+3d} dB  {off:4d} Hz  "
			self.add(head + text)
			c = QTextCursor(self.text.document())
			c.movePosition(QTextCursor.MoveOperation.End)
			c.movePosition(QTextCursor.MoveOperation.StartOfBlock)
		if not text.endswith('\u2662'):		# ♢: the message is complete
			self.lines[off] = (c, now, head)
		return
	# frame

	'''-------------------------------------------------------------------
		direwolf's config, written fresh each start: audio from stdin, nothing out, no network ports or announcing (nothing to clash with)
	-------------------------------------------------------------------'''

	def dwconf(self):
		path = Path(tempfile.gettempdir()) / 'shack-direwolf.conf'
		path.write_text(f'ADEVICE stdin null\nARATE 48000\nACHANNELS 1\nCHANNEL 0\nMODEM {self.pk}\nAGWPORT 0\nKISSPORT 0\nDNSSD 0\n')
		return str(path)
	# dwconf

	'''-------------------------------------------------------------------
		RTTY settings changed: a new decoder if RTTY is running
	-------------------------------------------------------------------'''

	def rtty(self, baud, shift, rev):
		self.baud, self.shift, self.rev = baud, shift, rev
		if self.dec and self.kind == 'RTTY':
			self.make(self.dec.tu.center if self.dec.tu.fixed else None)
		self.save()
		return
	# rtty

	'''-------------------------------------------------------------------
		right-click on the row, the strip or the text: Auto tune, RTTY, VARA and packet settings, Clear (the text also gets Copy, Select all)
	-------------------------------------------------------------------'''

	def menu(self, w, pos):
		m = self.text.createStandardContextMenu() if w is self.text else QMenu(w)
		if w is self.text:
			m.addSeparator()
		d = self.dec
		if d and not isinstance(d, (Helper, Scope)):		# ticked = following the strongest signal; unticking locks where it is
			self.check(m, 'Auto tune', not d.tu.fixed, lambda on: d.tune(None if on else d.tu.center))
		r = m.addMenu('RTTY')
		if decode:
			for name, b, s, rev in decode.PRESETS:
				self.check(r, f'{name}: {self.bd(b)} Bd, {s} Hz' + (', mark low' if rev else ''), (b, s, rev) == (self.baud, self.shift, self.rev), lambda on, p=(b, s, rev): self.rtty(*p))
			r.addSeparator()
			bm, sm = r.addMenu('Baud'), r.addMenu('Shift')
			for b in decode.BAUDS:
				self.check(bm, self.bd(b), b == self.baud, lambda on, b=b: self.rtty(b, self.shift, self.rev))
			for s in decode.SHIFTS:
				self.check(sm, f'{s} Hz', s == self.shift, lambda on, s=s: self.rtty(self.baud, s, self.rev))
			self.check(r, 'Mark low (reverse)', self.rev, lambda on: self.rtty(self.baud, self.shift, on))
		v = m.addMenu('VARA')
		for hf in (500, 2300, 2750):
			self.check(v, f'{hf} Hz', hf == self.hf, lambda on, hf=hf: self.vara(hf))
		k = m.addMenu('Packet')
		for b, what in ((300, '300 baud (HF)'), (1200, '1200 baud (VHF FM)')):
			self.check(k, what, b == self.pk, lambda on, b=b: self.packet(b))
		k.addSeparator()
		self.check(k, 'Hide complaints about non-APRS packets', self.hide, self.nags)
		a = m.addAction('Clear')
		a.triggered.connect(self.text.clear)
		m.exec(w.mapToGlobal(pos))
		return
	# menu

	def check(self, menu, text, on, f):		# a ticked or unticked menu item
		a = menu.addAction(text)
		a.setCheckable(True)
		a.setChecked(on)
		a.triggered.connect(f)
		return

	'''-------------------------------------------------------------------
		settings shared with decode.py's own UI (decode.json)
	-------------------------------------------------------------------'''

	def load(self):
		if not decode:
			return
		try:
			c = json.load(open(decode.CONF))
			self.kind = c['kind'] if c['kind'] in self.sq else self.kind
			self.baud, self.shift, self.rev = float(c['baud']), int(c['shift']), bool(c['rev'])
			self.sq.update({k: float(v) for k, v in c['sq'].items() if k in self.sq})
			self.hf = int(c.get('hf', self.hf))
			self.pk = int(c.get('pk', self.pk))
			self.hide = bool(c.get('hide', self.hide))
			self.scope = bool(c.get('scope', self.scope))
			self.logging = bool(c.get('log', self.logging))
		except (OSError, ValueError, KeyError, TypeError, AttributeError):
			pass
		return
	# load

	def save(self):
		if not decode:
			return
		try:
			with open(decode.CONF, 'w') as f:
				json.dump({'kind': self.kind if self.kind in self.sq else 'RTTY', 'scope': self.scope, 'baud': self.baud, 'shift': self.shift, 'rev': self.rev, 'sq': self.sq, 'hf': self.hf, 'pk': self.pk, 'hide': self.hide, 'log': self.logging}, f)
		except OSError as e:
			log.error(f'decoder settings: {e}')
		return
	# save

# class Decoders


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
		self.bm = Bookmarks(HERE / 'bookmarks.db', Path(a.rspdx).parent / 'settings.db')
		self.got.connect(self.on_rig, Qt.ConnectionType.QueuedConnection)		# always via the event loop, never inside a rig callback

		self.radio = QComboBox()
		self.radio.addItems(['none', 'IC-705', 'RSPdx', 'Xiegu x6200', 'FTdx10'])
		self.radio.activated.connect(lambda i: self.pick(self.radio.itemText(i)))
		self.freq = Freq()
		self.freq.changed.connect(lambda v: self.rig and self.rig.set('mhz', v))
		self.mode = QComboBox()
		self.mode.activated.connect(lambda i: self.rig and self.rig.set('mode', self.mode.itemText(i)))
		self.filrow = QWidget(toolTip='The radio\'s three filter presets for this mode, widest to narrowest as the radio ships; the width is what the radio reports for the one in use')
		self.fil = QComboBox()
		self.fil.addItems(['FIL1', 'FIL2', 'FIL3'])
		self.fil.activated.connect(lambda i: self.rig and self.rig.set('fil', i + 1))
		self.filw = QLabel()
		h = QHBoxLayout(self.filrow)
		h.setContentsMargins(0, 0, 0, 0)
		h.addWidget(self.fil)
		h.addWidget(self.filw)
		h.addStretch()
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
		self.pa = QComboBox(toolTip='Preamp: IPO = none (strong signals, low bands), AMP1, AMP2 = most gain')
		self.pa.addItems(cat.PREAMPS)
		self.pa.activated.connect(lambda i: self.rig and self.rig.set('pa', i))
		self.ra = QComboBox(toolTip='Attenuator')
		self.ra.addItems(cat.ATTS)
		self.ra.activated.connect(lambda i: self.rig and self.rig.set('ra', i))
		self.roof = QComboBox(toolTip='Roofing filter (R FIL): the first, widest filter. Narrower keeps strong nearby signals out; it should be at least as wide as the mode needs')
		self.roof.addItems(list(cat.ROOFS))
		self.roof.activated.connect(lambda i: self.rig and self.rig.set('roof', self.roof.itemText(i)))
		h = QHBoxLayout(self.power)
		h.setContentsMargins(0, 0, 0, 0)
		h.addWidget(self.pwr)
		h.addWidget(self.pwrv)
		self.vol = QDoubleSpinBox(decimals=2, minimum=0, maximum=1, singleStep=0.02, keyboardTracking=False)
		self.vol.valueChanged.connect(lambda v: self.rig and self.rig.set('v', v))
		self.rfg = QSpinBox(minimum=0, maximum=100, singleStep=5, suffix=' %', keyboardTracking=False, toolTip='The radio\'s RF gain; 100 % = full gain')
		self.rfg.valueChanged.connect(lambda v: self.rig and self.rig.set('rfg', round(v * 2.55)))
		self.vfo = {}		# A, B: which one the radio listens on; neither lit until one is picked (CI-V can't ask the radio)
		self.vfomem, self.vfo_on, self.vfo_t = {}, None, 0.0		# FTdx10: each VFO's settings as last seen, the VFO it reports, when that last changed
		self.vforow = QWidget(toolTip='The radio\'s VFO A or B. CI-V can\'t read which one the radio is on, so neither is lit until you pick one here')
		h = QHBoxLayout(self.vforow)
		h.setContentsMargins(0, 0, 0, 0)
		for k in ('A', 'B'):
			b = self.vfo[k] = QPushButton(f'VFO {k}', checkable=True)
			b.clicked.connect(lambda on, k=k: self.pick_vfo(k))
			h.addWidget(b)
		h.addStretch()
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
		self.lvl = {}		# CI-V NB and NR levels, %; usable while their switch is on
		for k, what in (('nb', 'noise blanker'), ('nr', 'noise reduction')):
			w = self.lvl[k] = QSpinBox(minimum=0, maximum=100, singleStep=5, suffix=' %', keyboardTracking=False, toolTip=f'The radio\'s {what} level; turn the {what} on to change it')
			w.valueChanged.connect(lambda v, k=k: self.rig and self.rig.set(k + 'l', round(v * 2.55)))
		self.nbr.setFixedWidth(self.nbr.fontMetrics().horizontalAdvance('9999.9/s'))		# changing text mustn't resize the panel
		for k, name in Rspdx.SWITCHES.items():
			b = self.sw[k] = QPushButton(name, checkable=True)
			b.clicked.connect(lambda on, k=k: self.rig and self.rig.set(k, on))		# clicked, not toggled: the radio's own updates don't echo back
			switches.addWidget(b)
			if k == 'nb':		# its level and rate sit next to it
				switches.addWidget(self.nbdb)
				switches.addWidget(self.nbr)
			if k in self.lvl:		# CI-V: the radio's level
				switches.addWidget(self.lvl[k])
		self.sw['pre'] = QPushButton('Preamp', checkable=True)		# CI-V radios
		self.sw['att'] = QPushButton('ATT', checkable=True)
		for k in ('pre', 'att'):
			self.sw[k].clicked.connect(lambda on, k=k: self.rig and self.rig.set(k, on))
			switches.insertWidget(0 if k == 'pre' else 1, self.sw[k])
		self.sw['nr'].setToolTip('RNNoise: removes the noise floor under voice (SSB, AM). Turn off for very weak signals, music and CW; Digital out is unaffected')
		self.save = QPushButton('Save', toolTip='Bookmark this frequency and mode with the radio\'s settings (updates it if it\'s already there)')
		self.save.clicked.connect(self.bm_save)
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
		self.bms.cellDoubleClicked.connect(lambda r, c: self.bm_pick(r))
		self.bms.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
		self.bms.customContextMenuRequested.connect(self.bm_menu)
		self.bmlist = []
		self.dec = Decoders(lambda: isinstance(self.rig, Rspdx) and self.rig.set('digi', True))		# Digital out: the RSPdx only
		self.rsp = (self.dec.row, self.dec.spec, self.dec.text, self.dec.scopeb, self.ifr, self.lna, self.ant, self.ovl, self.bw, *(self.sw[k] for k in Rspdx.SWITCHES), self.nbdb, self.nbr, self.save, self.adc, self.bms)		# RSPdx controls
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
		self.wf = Waterfall(self.wf_click)		# CI-V band scope, shown while wfb is on
		self.scoperow = QWidget()		# Waterfall button, span, ref level
		self.wfb = QPushButton('Waterfall', checkable=True, toolTip='The radio\'s band scope, sent over CI-V')
		self.wfb.clicked.connect(self.set_wf)
		self.span = QComboBox(toolTip='Scope width: this much either side of the dial')
		self.span.addItems([f'± {x / 1000:g} kHz' for x in civ.SPANS])
		self.span.activated.connect(lambda i: self.rig and self.rig.set('span', civ.SPANS[i]))
		self.ref = QDoubleSpinBox(minimum=-20, maximum=20, singleStep=0.5, decimals=1, suffix=' dB', keyboardTracking=False, toolTip='Scope reference level: lower shows weak signals brighter')
		self.ref.valueChanged.connect(lambda v: self.rig and self.rig.set('ref', v))
		self.ft = {}		# FTdx10: its own scope's settings (shown on the radio), SS item: combo
		for k, items, tip in (('mode', cat.SCOPEMODES, 'The radio\'s scope display: 3DSS or waterfall (L, N, S = size); Center, Cursor or Fix (the Fix edges are set on the radio)'),
				('span', cat.SPANS, 'The radio\'s scope width'), ('speed', cat.SPEEDS, 'The radio\'s scope sweep speed')):
			c = self.ft[k] = QComboBox(toolTip=tip)
			c.addItems(items)
			c.activated.connect(lambda i, k=k: self.rig and self.rig.set('ss.' + k, i))
		self.ss = {}		# FTdx10 scope items in the right-click menu: last value the radio reported
		h = QHBoxLayout(self.scoperow)
		h.setContentsMargins(0, 0, 0, 0)
		h.addWidget(self.wfb)
		h.addWidget(self.span)
		h.addWidget(self.ft['mode'])
		h.addWidget(self.ft['span'])
		h.addWidget(QLabel('Ref'))
		h.addWidget(self.ref)
		h.addWidget(self.ft['speed'])
		self.wfp = QPushButton('Pause', checkable=True, toolTip='Freeze the waterfall and the spectrum strip (for a closer look); click again to go on')
		h.addWidget(self.wfp)
		h.addStretch()
		for b in (self.wfp, self.dec.pauseb):
			b.clicked.connect(self.freeze)

		self.labels = {}		# control: its row label
		g = QGridLayout()
		g.setColumnStretch(2, 1)		# empty column 2 takes the spare width: adjusters stay narrow in column 1, meters span 1-2
		for row, (name, w) in enumerate([('Radio', self.radio), ('Frequency', self.freq), ('VFO', self.vforow), ('Mode', self.mode), ('Filter', self.filrow), ('Volume', self.vol), ('RF gain', self.rfg), ('AGC', self.agc), ('Preamp', self.pa), ('ATT', self.ra), ('Roofing', self.roof),
				('IF reduction', self.ifr), ('LNA reduction', self.lna), ('Antenna', self.ant), ('Bandwidth', self.bw), ('Overload', self.ovl), ('ADC', self.adc), ('Signal', self.smeter), ('SWR', self.swr), ('Power', self.power), ('PTT', self.pttrow)]):
			self.labels[w] = QLabel(name)
			w.installEventFilter(self)		# the label greys out with its control (eventFilter)
			g.addWidget(self.labels[w], row, 0)
			g.addWidget(w, row, 1, 1, 2 if w in (self.freq, self.vforow, self.filrow, self.ovl, self.adc, self.smeter, self.power, self.pttrow) else 1)
		row += 1
		g.addLayout(switches, row, 0, 1, 3)
		self.helpw = None
		more = QHBoxLayout()		# second button row
		hb = QPushButton('Help', toolTip='What everything does (F1)')
		hb.clicked.connect(self.help)
		QShortcut(QKeySequence.StandardKey.HelpContents, self, self.help)
		more.addWidget(hb)
		more.addWidget(self.dec.scopeb)
		more.addStretch()
		row += 1
		g.addLayout(more, row, 0, 1, 3)
		row += 1
		self.labels[self.scoperow] = QLabel('Scope')		# under the buttons: the waterfall is tall
		for w in (self.scoperow, self.labels[self.scoperow]):		# FTdx10: more scope settings
			w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
			w.customContextMenuRequested.connect(lambda pos, w=w: self.scope_menu(w.mapToGlobal(pos)))
		g.addWidget(self.labels[self.scoperow], row, 0)
		g.addWidget(self.scoperow, row, 1, 1, 2)
		row += 1
		g.addWidget(self.wf, row, 0, 1, 3)
		row += 1
		self.labels[self.dec.row] = QLabel('Decoder')
		g.addWidget(self.labels[self.dec.row], row, 0)
		g.addWidget(self.dec.row, row, 1, 1, 2)
		row += 1
		g.addWidget(self.dec.spec, row, 0, 1, 3)
		row += 1
		g.addWidget(self.dec.text, row, 0, 1, 3)
		g.setRowStretch(row, 1)		# spare height goes to the decoded text (or below the rows when it's hidden)
		box = QWidget()
		box.setLayout(g)
		main = QHBoxLayout()
		main.addWidget(box, 3)
		main.addWidget(self.bms, 2)
		top = QWidget()
		top.setLayout(main)
		self.setCentralWidget(top)

		self.parts = (self.freq, self.vforow, self.mode, self.filrow, self.vol, self.rfg, self.agc, self.pa, self.ra, self.roof, self.smeter, self.swr, self.power, self.pttrow, self.scoperow, self.wf, self.sw['pre'], self.sw['att'], *self.lvl.values(), *self.rsp)		# everything that comes and goes with the radio
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
		elif name in cat.RIGS:
			self.open_cat(name)
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
		self.scope_for(False)
		self.rig.read_fast()
		self.smeter.setRange(-54, 60)
		self.show_radio(name, r['modes'], r['top'], (self.freq, self.vforow, self.mode, self.filrow, self.vol, self.rfg, self.agc, self.sw['pre'], self.sw['att'], self.sw['nb'], self.sw['nr'], *self.lvl.values(), self.smeter, *([self.swr] if r['swr'] else []), self.power, self.pttrow, *([self.scoperow] if r['scope'] else []), self.save, self.bms,
			self.dec.row, self.dec.spec, self.dec.text, self.dec.scopeb))		# nb, nr: the radio's own, on/off
		self.dec.opened(r['audio'])		# decoders read the radio's USB sound card
		self.levels()
		self.tx_band(0)		# no PTT until the radio says where it is
		self.show_bms()
		self.on_rig('ptt_clients', 0)
		return
	# open_civ

	'''-------------------------------------------------------------------
		open a Yaesu CAT radio: the FTdx10 (no filter presets, SWR or scope over CAT yet)
	-------------------------------------------------------------------'''

	def open_cat(self, name):
		dev = cat.find_dev(name)
		r = cat.RIGS[name]
		if not dev:
			return self.oops(f"Can't find the {name}.\n\nIs it plugged in and turned on?")
		try:
			self.rig = cat.Rig(dev, self.a.baud or r['baud'], self.got.emit)
		except serial.SerialException as e:
			return self.oops(f"Found the {name} but couldn't open it.\n\n{e}\n\nIs another program (flrig, rigctld, WSJT-X) using it?")
		try:
			self.srv = civ.serve_ptt(self.rig, self.a.ptt) if self.a.ptt else None
		except OSError as e:
			self.statusBar().showMessage(f'PTT port {self.a.ptt}: {e.strerror}', 10000)
		self.has_swr = False
		self.agcs = r['agc']
		quiet(self.agc, self.agc.clear)
		quiet(self.agc, self.agc.addItems, list(self.agcs))
		self.pscale = r['watts']
		quiet(self.pwr, self.pwr.setRange, self.pscale[0] * 10, self.pscale[1] * 10)
		self.scope_for(True)
		self.rig.poll(0)
		self.smeter.setRange(-54, 60)
		self.show_radio(name, r['modes'], r['top'], (self.freq, self.vforow, self.mode, self.vol, self.rfg, self.agc, self.pa, self.ra, self.roof, self.sw['nb'], self.sw['nr'], *self.lvl.values(), self.smeter, self.power, self.pttrow, self.scoperow, self.save, self.bms,
			self.dec.row, self.dec.spec, self.dec.text, self.dec.scopeb))
		self.dec.opened(r['audio'])
		self.levels()
		self.tx_band(0)
		self.show_bms()
		self.on_rig('ptt_clients', 0)
		return
	# open_cat

	'''-------------------------------------------------------------------
		the Scope line: the 705's waterfall controls, or the FTdx10's settings for its own scope
	-------------------------------------------------------------------'''

	def scope_for(self, ft):
		for w in (self.wfb, self.span, self.wfp):
			w.setVisible(not ft)
		for w in self.ft.values():
			w.setVisible(ft)
		quiet(self.ref, self.ref.setRange, *((-30, 30) if ft else (-20, 20)))
		self.ref.setToolTip('The radio\'s scope level: lower shows weak signals brighter' if ft else 'Scope reference level: lower shows weak signals brighter')
		self.ss = {}
		return
	# scope_for

	'''-------------------------------------------------------------------
		right-click the Scope line (FTdx10): peak, color, RBW, marker, hold
	-------------------------------------------------------------------'''

	def scope_menu(self, at):
		if not isinstance(self.rig, cat.Rig):
			return
		menu = QMenu(self)
		acts = {}
		for k, name, items in (('peak', 'Peak', cat.PEAKS), ('color', 'Color', cat.COLORS), ('rbw', 'RBW', cat.RBWS)):
			sub = menu.addMenu(name)
			for i, t in enumerate(items):
				a = sub.addAction(t)
				a.setCheckable(True)
				a.setChecked(self.ss.get(k) == i)
				acts[a] = (k, i)
		for k, name in (('marker', 'Marker'), ('hold', 'Hold')):
			a = menu.addAction(name)
			a.setCheckable(True)
			a.setChecked(bool(self.ss.get(k)))
			acts[a] = (k, int(not self.ss.get(k)))
		act = menu.exec(at)
		if act in acts:
			k, v = acts[act]
			self.rig.set('ss.' + k, v)
		return
	# scope_menu

	'''-------------------------------------------------------------------
		start the rspdx program, which runs the RSPdx through the sdrplay api
	-------------------------------------------------------------------'''

	def open_rx(self):
		if not Path(self.a.rspdx).is_file():
			return self.oops(f"Can't find rspdx at {self.a.rspdx}.\n\nIs it built? (./build.sh)")
		self.rig = Rspdx(self.a.rspdx, self.got.emit, self.bm.last('RSPdx'))
		self.smeter.setRange(-100, -40)		# the TUI's signal scale, dBFS in the passband
		self.show_radio('RSPdx', Rspdx.MODES, 2000, (self.freq, self.mode, self.vol, self.smeter, *self.rsp))		# no PTT on a receiver
		self.dec.opened(None)		# decoders read Digital out
		self.show_bms()
		return
	# open_rx

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def show_radio(self, name, modes, top, live):
		self.radio.setCurrentText(name)
		self.mode.clear()
		self.mode.addItems(modes)
		self.freq.set_top(top)
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
		self.dec.closed()
		self.vfomem, self.vfo_on = {}, None
		if self.rig:
			if isinstance(self.rig, Rspdx) and self.rig.ok:		# start here next time
				self.bm.set_last('RSPdx', round(self.rig.state['mhz'] * 1e6), self.rig.state['mode'].upper(), self.rig.settings())
			if isinstance(self.rig, (civ.Rig, cat.Rig)) and (self.rig.tx or self.pttb.isChecked()):		# the button may be ahead of the radio's report
				self.rig.ptt(False)
			if isinstance(self.rig, civ.Rig) and self.wfb.isChecked():
				self.rig.send(0x27, 0x11, 0x00)		# scope data off
			self.rig.close()
			self.rig = None
		if self.srv:
			self.srv.stop()		# also hangs up on connected clients (xara), so none keeps talking to a closed radio
			self.srv = None
		self.radio.setCurrentText('none')
		self.clients.setText('')
		self.on_rig('ovl', (0, 0))
		self.bms.setRowCount(0)
		self.adc.setValue(-60)
		self.smeter.setValue(self.smeter.minimum())
		self.smeter.setFormat('')
		self.nbr.setText('')
		self.pwrv.setText('')
		self.swr.setText('')
		self.swr.setStyleSheet('')
		self.filw.setText('')
		quiet(self.fil, self.fil.setCurrentIndex, -1)
		for b in (*self.sw.values(), *self.vfo.values(), self.wfb):
			b.setChecked(False)
		self.freeze(False)
		self.wf.clear()
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
		if not isinstance(self.rig, (civ.Rig, cat.Rig)):		# rspdx pushes its state; only CI-V and CAT need asking
			return
		if time.monotonic() - self.rig.heard > 3:		# polled 4x a second, so 3 s of silence = gone
			return self.oops('The radio stopped answering.\n\nUnplugged or turned off?')
		if isinstance(self.rig, cat.Rig):
			if time.monotonic() - self.rig.last_set > 0.5:
				self.rig.poll(self.n)
				self.n += 1
			if self.vfo_on and time.monotonic() - self.vfo_t > 2:		# the controls have caught up with this VFO: remember them
				self.vfomem[self.vfo_on] = self.vfo_settings()
			return
		if time.monotonic() - self.rig.last_set > 0.5:		# stay out of the way of user commands
			self.rig.send(0x15, 0x02)
			if self.has_swr:
				self.rig.send(0x15, 0x12)
			self.rig.send(0x1C, 0x00)		# TX/RX, so a short transmission (a tuner cycle) shows
			if self.n % 4 == 0:
				self.rig.read_fast()
				for k in ('vol', 'rfg', 'nb', 'nr', 'nbl', 'nrl', 'pwr', 'pre', 'agc'):		# so the radio's own knob and buttons show up here too
					self.rig.send(*civ.CTL[k])
				self.rig.send(0x11)		# attenuator
			self.n += 1

	'''-------------------------------------------------------------------
		VFO A/B buttons. FTdx10: a CAT switch doesn't load the VFO's own settings as the radio's A/B key does,
		so shack keeps each VFO's settings and puts them back (known after the first visit to that VFO)
	-------------------------------------------------------------------'''

	VFOKEEP = ('pa', 'ra', 'roof', 'agc', 'nb', 'nr', 'nbl', 'nrl')

	def pick_vfo(self, k):
		if not self.rig:
			return
		ft = isinstance(self.rig, cat.Rig)
		if ft and self.vfo_on and k != self.vfo_on and time.monotonic() - self.vfo_t > 2:
			self.vfomem[self.vfo_on] = self.vfo_settings()
		self.rig.set('vfo', k)
		if ft and k != self.vfo_on and k in self.vfomem:
			self.apply('', self.vfomem[k])
		self.vfo_t = time.monotonic()
		return
	# pick_vfo

	def vfo_settings(self):
		return {k: v for k, v in self.settings().items() if k in self.VFOKEEP}

	'''-------------------------------------------------------------------
		PTT button: key or unkey; keying starts the 30 s limit
	-------------------------------------------------------------------'''

	def key(self, on):
		if not isinstance(self.rig, (civ.Rig, cat.Rig)):
			return
		if self.rig.ptt(on) is False:		# refused: outside the ham bands
			self.pttb.setChecked(False)
			return self.statusBar().showMessage('No transmitting outside the ham bands', 5000)
		self.rig.last_set = time.monotonic()
		if on:
			self.ptt_limit.start()
		else:
			self.ptt_limit.stop()
			self.pttb.setChecked(False)
		return
	# key

	'''-------------------------------------------------------------------
		PTT and power only inside the ham bands; tuning out while keyed unkeys
	-------------------------------------------------------------------'''

	def tx_band(self, mhz):
		ok = civ.ham(mhz)
		self.pttrow.setEnabled(ok)		# the whole line, its label too
		self.power.setEnabled(ok)
		self.pttb.setToolTip('Transmit: click to key, click again to stop. Drops back to receive by itself after 30 s' if ok else 'Outside the ham bands: no transmitting')
		if not ok and (self.pttb.isChecked() or self.rig.tx):
			self.key(False)
		return
	# tx_band

	'''-------------------------------------------------------------------
		waterfall click: the dial there; Shift: the dial 1500 Hz below it in USB (above in LSB), so the signal sits mid-passband
	-------------------------------------------------------------------'''

	AUDIO = 1500		# Hz: where VARA and ARDOP sit, middle of the decoders' range

	def wf_click(self, hz, center):
		if not self.rig:
			return
		m = self.mode.currentText()
		if center and m.startswith('USB'):
			hz -= self.AUDIO
		elif center and m.startswith('LSB'):
			hz += self.AUDIO
		self.rig.set('mhz', hz / 1e6)
		return
	# wf_click

	'''-------------------------------------------------------------------
		either Pause button: waterfall and spectrum strip freeze together, both buttons show it
	-------------------------------------------------------------------'''

	def freeze(self, on):
		for b in (self.wfp, self.dec.pauseb):
			b.setChecked(on)
		self.wf.pause(on)
		self.dec.spec.pause(on)
		return
	# freeze

	'''-------------------------------------------------------------------
		Waterfall button: the radio's scope on, centered on the dial, its data over CI-V; off stops the data
	-------------------------------------------------------------------'''

	def set_wf(self, on):
		if not isinstance(self.rig, civ.Rig):
			return
		if on:
			self.rig.send(0x27, 0x10, 0x01)		# scope on
			self.rig.send(0x27, 0x14, 0x00, 0x00)		# center mode: the span picks the width
			self.rig.send(0x27, 0x11, 0x01)		# its data out on CI-V
			self.rig.send(0x27, 0x19, 0x00)		# ref level
		else:
			self.rig.send(0x27, 0x11, 0x00)
			self.wf.clear()
			self.freeze(False)
		self.wf.setVisible(on)
		return
	# set_wf

	'''-------------------------------------------------------------------
		an NB or NR level is usable only while its switch is on (and usable)
	-------------------------------------------------------------------'''

	def levels(self):
		for k, w in self.lvl.items():
			w.setEnabled(self.sw[k].isEnabled() and self.sw[k].isChecked())
		return
	# levels

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
		if k == 'mhz':		# Freq keeps what's being typed, and ignores reports for a second after a step
			self.freq.set_mhz(v)
			if self.wf.dial != round(v * 1e6):
				self.wf.dial = round(v * 1e6)
				self.mark()
			if isinstance(self.rig, Rspdx):
				quiet(self.lna, self.lna.setMaximum, lna_max(v))		# may clamp the value; that's rspdx's call, not a command
			else:
				self.tx_band(v)
		elif k == 'mode' and self.mode.findText(v) >= 0:
			self.mode.setCurrentText(v)
			if v.removesuffix('-D') in civ.NOWIDTH:		# FM: the radio doesn't report a width
				self.filw.setText('')
			self.sw['nr'].setEnabled(v != 'WBFM')		# speech NR chops up broadcast music; rspdx skips it there too
			if isinstance(self.rig, Rspdx):		# as in the TUI: bandwidth presets are SSB only, the blanker isn't in WBFM
				self.bw.setEnabled(v in ('LSB', 'USB'))
				self.sw['nb'].setEnabled(v != 'WBFM')
				self.nbdb.setEnabled(v != 'WBFM')
			self.levels()
		elif k == 'bw':
			self.bw.setCurrentIndex(v)
		elif k == 'fil' and 1 <= v <= 3:		# CI-V filter preset, from the 04 reply
			quiet(self.fil, self.fil.setCurrentIndex, v - 1)
		elif k == 'width':		# CI-V passband of the filter in use, Hz; 0 = the radio sent something unreadable
			self.filw.setText('?' if not v else f'{v} Hz' if v < 1000 else f'{v / 1000:g} kHz')
		elif k in self.sw:
			self.sw[k].setChecked(bool(v))
			self.levels()
			if k == 'nb' and v and isinstance(self.rig, Rspdx):		# rspdx's nb carries the level when on, 0 when off
				quiet(self.nbdb, self.nbdb.setValue, v)
		elif k == 'v':
			quiet(self.vol, self.vol.setValue, v)
		elif k == 'vol':		# CI-V AF level 0-255
			quiet(self.vol, self.vol.setValue, v / 255)
		elif k in ('nbl', 'nrl'):		# CI-V NB / NR level 0-255
			quiet(self.lvl[k[:2]], self.lvl[k[:2]].setValue, round(v / 2.55))
		elif k == 'rfg':		# CI-V RF gain 0-255
			quiet(self.rfg, self.rfg.setValue, round(v / 2.55))
		elif k == 'vfo':		# CI-V: what civ last set (the radio can't be asked); FTdx10: what the radio reports
			for x, b in self.vfo.items():
				b.setChecked(x == v)
			if v != self.vfo_on:		# the controls still show the other VFO for a moment
				self.vfo_on, self.vfo_t = v, time.monotonic()
		elif k == 'pwr' and not self.pwr.isSliderDown():		# CI-V RF power 0-255, None when the radio sends junk
			if v is None:
				return self.pwrv.setText('?')
			quiet(self.pwr, self.pwr.setValue, round(v / 2.55) if not self.pscale else round((self.pscale[0] + v / 255 * (self.pscale[1] - self.pscale[0])) * 10))
			self.pwrv.setText(self.pwr_text(self.pwr.value()))
		elif k in ('pa', 'ra'):		# FTdx10: index into cat.PREAMPS / ATTS
			w = self.pa if k == 'pa' else self.ra
			if 0 <= v < w.count():
				quiet(w, w.setCurrentIndex, v)
		elif k == 'roof':
			quiet(self.roof, self.roof.setCurrentText, v)
		elif k == 'pmax':		# FTdx10: its MAX POWER menu caps what PC can set
			quiet(self.pwr, self.pwr.setMaximum, v * 10)
			self.pwrv.setText(self.pwr_text(self.pwr.value()))
			self.power.setToolTip(f"Transmit power. The radio's MAX POWER menu (TX GENERAL) allows {v} W here. Changing it doesn't transmit")
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
		elif k == 'scope' and self.wfb.isChecked():
			self.wf.add(v)
		elif k == 'span' and v in civ.SPANS and self.span.currentIndex() != civ.SPANS.index(v):
			quiet(self.span, self.span.setCurrentIndex, civ.SPANS.index(v))
		elif k == 'ref' and isinstance(self.rig, (civ.Rig, cat.Rig)):
			quiet(self.ref, self.ref.setValue, v)
		elif k.startswith('ss.'):		# FTdx10 scope settings
			self.ss[k[3:]] = v
			c = self.ft.get(k[3:])
			if c and 0 <= v < c.count():
				quiet(c, c.setCurrentIndex, v)
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
			name, ok = QInputDialog.getText(self, 'Rename bookmark', 'Name:', text=b['name'])
			if ok:
				self.bm.rename(b, name.strip())
		elif act == dele:
			if QMessageBox.question(self, 'Delete bookmark', f"Delete {b['name']}  {b['hz'] / 1e6:.6f} {b['mode']}?") == QMessageBox.StandardButton.Yes:
				self.bm.delete(b)
		self.show_bms()
		return
	# bm_menu

	'''-------------------------------------------------------------------
		the bookmark table; ones the open radio can't tune (mode or range) are greyed and won't pick
	-------------------------------------------------------------------'''

	def show_bms(self):
		self.bmlist = self.bm.list()
		self.bms.setRowCount(len(self.bmlist))
		for r, b in enumerate(self.bmlist):
			for c, t in enumerate((b['name'], f"{b['hz'] / 1e6:.6f}", b['mode'], str(b['hits']))):
				it = QTableWidgetItem(t)
				if not self.can(b):
					it.setForeground(QColor('#b0b0b0'))
					it.setToolTip(f"The {self.radio.currentText()} can't tune this")
				self.bms.setItem(r, c, it)
		self.mark()
		return
	# show_bms

	def mark(self):		# rows at the dial's frequency, any mode, shaded
		for r, b in enumerate(self.bmlist):
			for c in range(self.bms.columnCount()):
				it = self.bms.item(r, c)
				if it:
					it.setBackground(QColor('#ffe680') if b['hz'] == self.freq.hz else QColor(0, 0, 0, 0))
		return
	# mark

	def can(self, b):
		return self.mode.findText(b['mode']) >= 0 and self.freq.lo <= b['hz'] <= self.freq.hi

	'''-------------------------------------------------------------------
		double-click: tune there; this radio's settings from the bookmark, if it has any
	-------------------------------------------------------------------'''

	def bm_pick(self, r):
		b = self.bmlist[r]
		if not self.rig or not self.can(b):
			return self.statusBar().showMessage(f"The {self.radio.currentText()} can't tune {b['hz'] / 1e6:.6f} {b['mode']}", 5000)
		self.bm.picked(b)
		self.rig.set('mode', b['mode'])
		self.rig.set('mhz', b['hz'] / 1e6)
		s = b['settings'].get(self.radio.currentText())
		if s:
			self.apply(b['mode'], s)
		self.show_bms()
		return
	# bm_pick

	'''-------------------------------------------------------------------
		Save: bookmark the open radio's frequency, mode and settings
	-------------------------------------------------------------------'''

	def bm_save(self):
		if not self.rig:
			return
		radio, hz, mode, s = self.radio.currentText(), self.freq.hz, self.mode.currentText(), self.settings()
		name = ''
		if not self.bm.find(hz, mode):		# a new one: ask for its name; an existing one keeps its own
			name, ok = QInputDialog.getText(self, 'Save bookmark', f'Name for {hz / 1e6:.6f} {mode}:')
			if not ok:
				return
		self.bm.save(radio, hz, mode, s, name.strip())
		if isinstance(self.rig, Rspdx):		# a CI-V radio remembers where it was by itself
			self.bm.set_last(radio, hz, mode, s)
		self.show_bms()
		self.statusBar().showMessage(f'Saved {hz / 1e6:.6f} {mode}', 5000)
		return
	# bm_save

	'''-------------------------------------------------------------------
		what a bookmark keeps for the open radio, and putting it back. CI-V: read off the controls (they follow the radio);
		never TX power, so a bookmark can't change what you transmit with
	-------------------------------------------------------------------'''

	def settings(self):
		if isinstance(self.rig, Rspdx):
			return self.rig.settings()
		s = {'v': self.vol.value(), 'rfg': self.rfg.value(), 'nbl': self.lvl['nb'].value(), 'nrl': self.lvl['nr'].value(), **{k: int(self.sw[k].isChecked()) for k in ('nb', 'nr')}}
		if isinstance(self.rig, cat.Rig):
			s.update(pa=self.pa.currentIndex(), ra=self.ra.currentIndex(), roof=self.roof.currentText())
		else:
			s.update(pre=int(self.sw['pre'].isChecked()), att=int(self.sw['att'].isChecked()))
		if self.fil.currentIndex() >= 0:
			s['fil'] = self.fil.currentIndex() + 1
		if self.agc.currentText() in self.agcs:
			s['agc'] = self.agcs[self.agc.currentText()]
		return s
	# settings

	def apply(self, mode, s):
		if isinstance(self.rig, Rspdx):
			return self.rig.apply(mode, s)
		for k, v in s.items():
			if k in ('rfg', 'nbl', 'nrl'):		# % -> 0-255
				self.rig.set(k, round(v * 2.55))
			elif k in ('v', 'fil', 'pre', 'att', 'nb', 'nr', 'agc', 'pa', 'ra', 'roof'):
				self.rig.set(k, v)
		return
	# apply

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

