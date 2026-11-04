#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import glob, logging, threading, time

import serial

import civ		# ham(): the same transmit bands

log = logging.getLogger('shack.cat')		# child of shack's logger: lands in shack.log

MODES = {'LSB': '1', 'USB': '2', 'CW': '3', 'NBFM': '4', 'AM': '5', 'RTTY': '6', 'CW-R': '7', 'LSB-D': '8', 'RTTY-R': '9', 'NBFM-D': 'A', 'FM-N': 'B', 'USB-D': 'C', 'AM-N': 'D', 'PSK': 'E', 'FM-N-D': 'F'}		# MD0x; names as civ.py has them (CW = upper side, RTTY = Yaesu RTTY-L)
RMODES = {v: k for k, v in MODES.items()}
SMETER = [(0, -54), (12, -48), (27, -42), (40, -36), (55, -30), (65, -24), (80, -18), (95, -12), (112, -6), (130, 0), (150, 10), (172, 20), (190, 30), (220, 40), (240, 50), (255, 60)]		# SM0 0-255 -> dB re S9, Hamlib's Yaesu table: check against the radio's meter

RIGS = {		# port: by-id pattern of the CAT port (the CP2105's Enhanced port; if01 is the Standard port, RTS/DTR keying: never opened); audio: ALSA card; top: MHz; att: RA0 value for on; agc: GT0 values; watts: PC range
	'FTdx10': dict(port='*CP2105_Dual_USB_to_UART*if00-port0', audio='plughw:CARD=CODEC', baud=38400, modes=['LSB', 'USB', 'LSB-D', 'USB-D', 'AM', 'AM-N', 'CW', 'CW-R', 'RTTY', 'RTTY-R', 'NBFM', 'NBFM-D', 'FM-N', 'PSK'], top=75,
		att='3', agc={'Off': 0, 'Fast': 1, 'Mid': 2, 'Slow': 3, 'Auto': 4}, watts=(5, 100)),		# att 3 = 18 dB (1 6 dB, 2 12 dB)
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
	SLOW = 'VS;MD0;AG0;RG0;PC;PA0;RA0;GT0;NB0;NR0;NL0;RL0;'		# every second, so the radio's own knobs show up too

	'''-------------------------------------------------------------------
		RTS held high: the Enhanced port uses it as flow control, the radio holds its replies while it's low
	-------------------------------------------------------------------'''

	def __init__(self, dev, baud, emit, att='3'):
		self.ser = serial.Serial(None, baud, timeout=0.2, exclusive=True)
		self.ser.port, self.ser.dtr, self.ser.rts = dev, False, True
		self.ser.open()
		self.emit, self.att_on = emit, att		# emit(kind, value) is called from the reader thread
		self.wlock = threading.Lock()
		self.mhz, self.vfo, self.tx = 0.0, 'A', 0
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
				self.mhz = int(d) / 1e6
				self.emit('mhz', self.mhz)
		elif cmd == 'MD' and len(d) == 2:
			if d[1] in RMODES:
				self.emit('mode', RMODES[d[1]])
		elif cmd == 'SM' and len(d) == 4 and d.isdigit():
			self.emit('smeter', smeter(int(d[1:])))
		elif cmd == 'TX' and d.isdigit():
			self.tx = int(d != '0')		# 1 = keyed by CAT, 2 = by the mic or the rear jack
			self.emit('ptt', self.tx)
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
		elif cmd in ('PA', 'RA', 'NB', 'NR') and len(d) == 2 and d.isdigit():		# PA: 0 IPO, 1 AMP1, 2 AMP2; RA: 0 off, 1-3 = 6-18 dB
			self.emit({'PA': 'pre', 'RA': 'att', 'NB': 'nb', 'NR': 'nr'}[cmd], int(d[1] != '0'))
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
			self.send(f"VS{0 if v == 'A' else 1};VS;")
		elif k == 'v':		# shack's volume 0-1
			self.set('vol', round(float(v) * 255))
		elif k in ('vol', 'rfg'):
			c = 'AG' if k == 'vol' else 'RG'
			self.send(f'{c}0{int(v):03d};{c}0;')
		elif k == 'pwr':
			lo, hi = RIGS['FTdx10']['watts']
			self.send(f'PC{round(lo + int(v) / 255 * (hi - lo)):03d};PC;')
		elif k == 'pre':
			self.send(f"PA0{1 if int(v) else 0};PA0;")		# on = AMP1
		elif k == 'att':
			self.send(f"RA0{self.att_on if int(v) else 0};RA0;")
		elif k == 'agc':
			self.send(f'GT0{int(v)};GT0;')
		elif k in ('nb', 'nr'):
			c = k.upper()
			self.send(f'{c}0{1 if int(v) else 0};{c}0;')
		elif k == 'nbl':
			self.send(f'NL0{round(int(v) / 255 * 10):03d};NL0;')
		elif k == 'nrl':
			self.send(f'RL0{1 + round(int(v) / 255 * 14):02d};RL0;')
		elif k == 'fil':		# a bookmark from a CI-V radio; the FTdx10's widths aren't presets
			pass
		else:
			raise KeyError(k)
		return
	# set
# class Rig
