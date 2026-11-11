#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import glob, logging, threading, time

import serial

import civ		# ham(): the same transmit bands

log = logging.getLogger('shack.cat')		# child of shack's logger: lands in shack.log

MODES = {'LSB': '1', 'USB': '2', 'CW': '3', 'NBFM': '4', 'AM': '5', 'RTTY': '6', 'CW-R': '7', 'LSB-D': '8', 'RTTY-R': '9', 'NBFM-D': 'A', 'FM-N': 'B', 'USB-D': 'C', 'AM-N': 'D', 'PSK': 'E', 'FM-N-D': 'F'}		# MD0x; names as civ.py has them (CW = upper side, RTTY = Yaesu RTTY-L)
RMODES = {v: k for k, v in MODES.items()}
SMETER = [(0, -54), (12, -48), (27, -42), (40, -36), (55, -30), (65, -24), (80, -18), (95, -12), (112, -6), (130, 0), (150, 10), (172, 20), (190, 30), (220, 40), (240, 50), (255, 60)]		# SM0 0-255 -> dB re S9, Hamlib's Yaesu table: check against the radio's meter

PREAMPS = ['IPO', 'AMP1', 'AMP2']		# PA0 0-2
ATTS = ['Off', '6 dB', '12 dB', '18 dB']		# RA0 0-3
ROOFS = {'12 kHz': ('1', '6'), '3 kHz': ('2', '7'), '500 Hz': ('4', '9')}		# RF0: (set value, answer value); 300 Hz is an option filter
SPANS = ['1 kHz', '2 kHz', '5 kHz', '10 kHz', '20 kHz', '50 kHz', '100 kHz', '200 kHz', '500 kHz', '1 MHz']		# the radio's scope, SS: index = P3
SPEEDS = ['Slow 1', 'Slow 2', 'Fast 1', 'Fast 2', 'Fast 3']
SCOPEMODES = ['3DSS Center', '3DSS Cursor', '3DSS Fix', *(f'Waterfall {m} {z}' for m in ('Center', 'Cursor', 'Fix') for z in ('L', 'N', 'S'))]		# 0-B
PEAKS = [f'LV{i}' for i in range(1, 6)]
COLORS = [f'Color {i}' for i in range(1, 12)]		# 0-A
RBWS = ['High', 'Mid', 'Low']		# EX040201: DISPLAY SETTING > SCOPE > RBW
SCOPE = {'speed': '0', 'peak': '1', 'marker': '2', 'color': '3', 'span': '5', 'mode': '6', 'hold': '8'}		# SS P2 (4 = level: 'ref')
RSCOPE = {v: k for k, v in SCOPE.items()}
MAXW = {'030401': 'HF', '030402': '50M', '030404': 'AM'}		# EX menu items: TX GENERAL > HF / 50M / AM MAX POWER, W

RIGS = {		# port: by-id pattern of the CAT port (the CP2105's Enhanced port; if01 is the Standard port, RTS/DTR keying: never opened); audio: ALSA card; top: MHz; agc: GT0 values; watts: PC range
	'FTdx10': dict(port='*CP2105_Dual_USB_to_UART*if00-port0', audio='plughw:CARD=CODEC', baud=38400, modes=['LSB', 'USB', 'LSB-D', 'USB-D', 'AM', 'AM-N', 'CW', 'CW-R', 'RTTY', 'RTTY-R', 'NBFM', 'NBFM-D', 'FM-N', 'PSK'], top=75,
		agc={'Off': 0, 'Fast': 1, 'Mid': 2, 'Slow': 3, 'Auto': 4}, watts=(5, 100)),
}

'''-------------------------------------------------------------------
	the radio's CAT port, None if not plugged in
-------------------------------------------------------------------'''

def find_dev(name='FTdx10'):
	ids = glob.glob('/dev/serial/by-id/' + RIGS[name]['port'])
	return ids[0] if ids else None
# find_dev

'''-------------------------------------------------------------------
-------------------------------------------------------------------'''

def smeter(v):
	(a, x), (b, y) = next(((p, q) for p, q in zip(SMETER, SMETER[1:]) if v <= q[0]), (SMETER[-2], SMETER[-1]))
	return round(x + (v - a) / (b - a) * (y - x))
# smeter


'''===================================================================================================================================================

==================================================================================================================================================='''

class Rig:		# Yaesu ASCII CAT (FA014074000;) over the radio's USB serial; same face as civ.Rig
	POLL = 'SM0;TX;'		# every 250 ms
	SLOW = 'VS;MD0;AG0;RG0;PC;PA0;RA0;RF0;GT0;NB0;NR0;NL0;RL0;' + ''.join(f'EX{k};' for k in MAXW) + ''.join(f'SS0{p};' for p in '01234568') + 'EX040201;DA;ST;'		# every second, so the radio's own knobs show up too

	'''-------------------------------------------------------------------
		RTS held high: the Enhanced port uses it as flow control, the radio holds its replies while it's low
	-------------------------------------------------------------------'''

	def __init__(self, dev, baud, emit):
		self.ser = serial.Serial(None, baud, timeout=0.2, exclusive=True)
		self.ser.port, self.ser.dtr, self.ser.rts = dev, False, True
		self.ser.open()
		self.emit = emit		# emit(kind, value) is called from the reader thread
		self.wlock = threading.Lock()
		self.mhz, self.vfo, self.tx, self.mode = 0.0, 'A', 0, ''
		self.split = 0		# ST: the radio transmits on the other VFO
		self.modes = RIGS['FTdx10']['modes']
		self.maxw, self.pmax = {}, 0		# the radio's MAX POWER menu limits; the one that applies now
		self.oddref = None		# an SS04 reply that isn't a number, logged once
		self.kick = False		# after a VS from here: the radio shows the new VFO but keeps receiving the old one until its frequency changes
		self.last_set = 0.0		# a ?; is only reported right after a user command
		self.alive, self.heard = True, time.monotonic()
		threading.Thread(target=self.reader, daemon=True).start()
		self.send('VS;')		# which VFO, before the first frequency read
		return
	# __init__

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def send(self, text):
		if not self.alive:
			return
		with self.wlock:
			self.ser.write(text.encode())
		return
	# send

	'''-------------------------------------------------------------------
		shack's 250 ms timer, n counts its ticks
	-------------------------------------------------------------------'''

	def poll(self, n):
		self.send(self.POLL + (self.read_fast() + self.SLOW if n % 4 == 0 else ''))
		return
	# poll

	def read_fast(self):		# the operating VFO's frequency
		return 'FB;' if self.vfo == 'B' else 'FA;'

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def close(self):
		self.alive = False
		self.ser.close()
		return
	# close

	'''-------------------------------------------------------------------
		replies end in ;  (?; = the radio didn't take the command)
	-------------------------------------------------------------------'''

	def reader(self):
		buf = b''
		while self.alive:
			try:
				buf += self.ser.read(self.ser.in_waiting or 1)
			except (serial.SerialException, OSError, TypeError):
				if self.alive:		# not our close(): unplugged or powered off
					self.alive = False
					self.emit('gone', 'Lost the radio.\n\nUnplugged or turned off?')
				return
			while b';' in buf:
				m, buf = buf.split(b';', 1)
				m = m.decode(errors='replace').strip()
				if not m:
					continue
				self.heard = time.monotonic()
				try:
					self.dispatch(m[:2], m[2:])
				except Exception:
					log.exception(f'reply {m!r}')
		# while alive
	# reader

	'''-------------------------------------------------------------------
		one reply -> emits to Shack
	-------------------------------------------------------------------'''

	def dispatch(self, cmd, d):
		if cmd in ('FA', 'FB') and d.isdigit():
			if cmd[1] == self.vfo:		# the other VFO's frequency isn't the dial
				if self.kick:		# 10 Hz up and back: retunes the receiver, ends where it was
					self.kick = False
					self.send(f'{cmd}{int(d) + 10:09d};{cmd}{d};')
				self.mhz = int(d) / 1e6
				self.emit('mhz', self.mhz)
				self.limit()
		elif cmd == 'MD' and len(d) == 2:
			if d[1] in RMODES:
				self.mode = RMODES[d[1]]
				self.emit('mode', self.mode)
				self.limit()
		elif cmd == 'SM' and len(d) == 4 and d.isdigit():
			self.emit('smeter', smeter(int(d[1:])))
		elif cmd == 'TX' and d.isdigit():
			self.tx = int(d != '0')		# 1 = keyed by CAT, 2 = by the mic or the rear jack
			self.emit('ptt', self.tx)
		elif cmd == 'ST' and d.isdigit():		# 1 = split, 2 = split +5 kHz
			self.split = int(d != '0')
		elif cmd == 'VS' and d in ('0', '1'):
			v = 'AB'[int(d)]
			if v != self.vfo:
				self.vfo = v
				self.send(self.read_fast())
			self.emit('vfo', v)
		elif cmd in ('AG', 'RG') and len(d) == 4 and d.isdigit():		# 0-255, as CI-V's
			self.emit('vol' if cmd == 'AG' else 'rfg', int(d[1:]))
		elif cmd == 'PC' and d.isdigit():		# watts -> shack's 0-255 power level
			lo, hi = RIGS['FTdx10']['watts']
			self.emit('pwr', (int(d) - lo) / (hi - lo) * 255)		# unrounded, so 10 W reads back as 10.0
		elif cmd in ('PA', 'RA') and len(d) == 2 and d.isdigit():		# index into PREAMPS / ATTS
			self.emit(cmd.lower(), int(d[1]))
		elif cmd == 'RF' and len(d) == 2:
			name = next((n for n, (_, a) in ROOFS.items() if a == d[1]), None)
			if name:
				self.emit('roof', name)
		elif cmd in ('NB', 'NR') and len(d) == 2 and d.isdigit():
			self.emit(cmd.lower(), int(d[1] != '0'))
		elif cmd == 'EX' and d[:6] in MAXW and d[6:].isdigit():
			self.maxw[MAXW[d[:6]]] = int(d[6:])
			self.limit()
		elif cmd == 'EX' and d[:6] == '040201' and d[6:].isdigit():
			self.emit('ss.rbw', int(d[6:]))
		elif cmd == 'DA' and len(d) == 8 and d.isdigit():		# 00, TFT contrast, TFT brightness, LED brightness, 0-20 each
			self.emit('da', (int(d[2:4]), int(d[4:6]), int(d[6:8])))
		elif cmd == 'SS' and len(d) >= 3 and d[0] == '0':
			if d[1] == '4':		# level: signed dB, 0.5 dB steps
				try:
					self.emit('ref', float(d[2:]))
				except ValueError:
					if d != self.oddref:
						log.info(f'SS04 level not a number: {d!r}')
						self.oddref = d
			elif d[1] in RSCOPE:
				self.emit('ss.' + RSCOPE[d[1]], int(d[2], 16))
		elif cmd == 'GT' and len(d) == 2 and d.isdigit():
			self.emit('agc', min(int(d[1]), 4))		# 4-6 read back = AUTO (fast, mid, slow)
		elif cmd == 'NL' and len(d) == 4 and d.isdigit():		# NB level 0-10
			self.emit('nbl', round(int(d[1:]) / 10 * 255))
		elif cmd == 'RL' and len(d) == 3 and d.isdigit():		# NR level 1-15
			self.emit('nrl', round((int(d[1:]) - 1) / 14 * 255))
		elif cmd == '?' and time.monotonic() - self.last_set < 0.5:
			self.emit('error', 'Radio rejected the last command')
		return
	# dispatch

	'''-------------------------------------------------------------------
		the power the radio allows here (its MAX POWER menu: HF, 50 MHz, AM on top of either), emitted when it changes
	-------------------------------------------------------------------'''

	def limit(self):
		w = self.maxw.get('50M' if self.mhz >= 50 else 'HF')
		if w and self.mode.startswith('AM') and 'AM' in self.maxw:
			w = min(w, self.maxw['AM'])
		if w and w != self.pmax:
			self.pmax = w
			self.emit('pmax', w)
		return
	# limit

	'''-------------------------------------------------------------------
		False: refused, outside the ham bands (unkeying always goes through)
	-------------------------------------------------------------------'''

	def ptt(self, on):
		if on and not civ.ham(self.mhz):
			log.info(f'PTT refused: {self.mhz:.6f} MHz is outside the ham bands')
			return False
		self.send(f'TX{1 if on else 0};TX;')
		return True
	# ptt

	'''-------------------------------------------------------------------
		a control changed in shack; values as civ.Rig takes them (levels 0-255), each read back
	-------------------------------------------------------------------'''

	def set(self, k, v):
		self.last_set = time.monotonic()
		if k == 'ptt':
			self.ptt(int(v))
		elif k == 'mhz':
			self.send(f'F{self.vfo}{round(float(v) * 1e6):09d};{self.read_fast()}')
		elif k == 'mode':
			self.send(f'MD0{MODES[v]};MD0;')
		elif k == 'vfo':
			self.kick = True
			self.send(f"VS{0 if v == 'A' else 1};VS;")
		elif k == 'split':
			self.send(f'ST{1 if int(v) else 0};ST;')
		elif k == 'v':		# shack's volume 0-1
			self.set('vol', round(float(v) * 255))
		elif k in ('vol', 'rfg'):
			c = 'AG' if k == 'vol' else 'RG'
			self.send(f'{c}0{int(v):03d};{c}0;')
		elif k == 'pwr':
			lo, hi = RIGS['FTdx10']['watts']
			self.send(f'PC{round(lo + int(v) / 255 * (hi - lo)):03d};PC;')
		elif k in ('pa', 'ra'):		# index into PREAMPS / ATTS
			c = k.upper()
			self.send(f'{c}0{int(v)};{c}0;')
		elif k == 'roof':
			self.send(f'RF0{ROOFS[v][0]};RF0;')
		elif k == 'agc':
			self.send(f'GT0{int(v)};GT0;')
		elif k in ('nb', 'nr'):
			c = k.upper()
			self.send(f'{c}0{1 if int(v) else 0};{c}0;')
		elif k == 'nbl':
			self.send(f'NL0{round(int(v) / 255 * 10):03d};NL0;')
		elif k == 'nrl':
			self.send(f'RL0{1 + round(int(v) / 255 * 14):02d};RL0;')
		elif k == 'ref':		# scope level, dB
			v = max(-30, min(30, round(float(v) * 2) / 2))
			self.send(f'SS04{v:+05.1f};SS04;')
		elif k == 'da':		# (contrast, brightness, LED), 0-20
			self.send('DA00' + ''.join(f'{max(0, min(20, int(x))):02d}' for x in v) + ';DA;')
		elif k == 'ss.rbw':
			self.send(f'EX040201{int(v)};EX040201;')
		elif k.startswith('ss.') and k[3:] in SCOPE:		# index into SPANS, SPEEDS, SCOPEMODES...; on/off for marker and hold
			p = SCOPE[k[3:]]
			self.send(f'SS0{p}{int(v):X}0000;SS0{p};')
		elif k == 'fil':		# a bookmark from a CI-V radio; the FTdx10's widths aren't presets
			pass
		else:
			raise KeyError(k)
		return
	# set
# class Rig
