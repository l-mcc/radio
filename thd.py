#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import glob, logging, queue, threading, time

import serial

log = logging.getLogger('shack.thd')		# child of shack's logger: lands in shack.log

PORT = '*JVCKENWOOD_TH-D75*'		# by-id pattern: the radio's USB-C port, a plain ttyACM
MODES = {'NBFM': '0', 'DV': '1', 'AM': '2', 'LSB': '3', 'USB': '4', 'CW': '5', 'FM-N': '6', 'DR': '7', 'WBFM': '8', 'CW-R': '9'}		# FO field 5 / MD; names as civ.py and cat.py have them
RMODES = {v: k for k, v in MODES.items()}
STEPS = [5000, 6250, 25000 / 3, 9000, 10000, 12500, 15000, 20000, 25000, 30000, 50000, 100000]		# FO field 3, one hex digit (2 = 8.33 kHz)
FINE = [20, 100, 500, 1000]		# FO field 7, used while field 6 (fine mode) is 1
PICK = (11, 10, 9, 8, 7, 6, 5, 4, 1, 0)		# steps tried, widest first, for a frequency that isn't on the band's own step (never 8.33 or 9 kHz)
TOP = 524		# MHz: band B's top; band A is 136-174, 216-260 and 410-470 only

'''-------------------------------------------------------------------
	the radio's port, None if not plugged in
-------------------------------------------------------------------'''

def find_dev():
	ids = glob.glob('/dev/serial/by-id/' + PORT)
	return ids[0] if ids else None
# find_dev

def on_step(hz, i):		# is hz a multiple of step i
	return hz * 3 % 25000 == 0 if i == 2 else hz % STEPS[i] == 0


'''===================================================================================================================================================

==================================================================================================================================================='''

class Rig:		# Kenwood TH-D75 (TH-D74 command set): ASCII lines ending in CR, one at a time, each answered; same face as civ.Rig. Bands A and B are shack's VFO A and B
	'''-------------------------------------------------------------------
		one thread does all the talking: a command, then its reply, then the next (the radio drops commands sent in a burst)
	-------------------------------------------------------------------'''

	def __init__(self, dev, emit):
		self.ser = serial.Serial(dev, 9600, timeout=0.5, exclusive=True)
		self.emit = emit		# emit(kind, value) is called from the worker thread
		self.q = queue.Queue()		# commands (str) and jobs (callables) for the worker
		self.band, self.vfo, self.mhz, self.mode = None, '', 0.0, ''
		self.tx = 0		# receive only from shack, so far
		self.want, self.lock = {}, threading.Lock()		# mhz, mode: asked for from shack, not sent yet
		self.modes = list(MODES)
		self.last_set = 0.0
		self.alive, self.heard = True, time.monotonic()
		threading.Thread(target=self.worker, daemon=True).start()
		self.q.put('BC')		# which band, before the first frequency read
		return
	# __init__

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def worker(self):
		while self.alive:
			try:
				job = self.q.get(timeout=0.5)
			except queue.Empty:
				continue
			try:
				job() if callable(job) else self.ask(job)
			except (serial.SerialException, OSError, TypeError):
				if self.alive:		# not our close(): unplugged or powered off
					self.alive = False
					self.emit('gone', 'Lost the radio.\n\nUnplugged or turned off?')
				return
			except Exception:
				log.exception(f'job {job!r}')
		# while alive
	# worker

	'''-------------------------------------------------------------------
		send one command, return its reply: the command's own line, ? (not understood) or N (refused); None = no answer.
		Anything else that arrives meanwhile is taken too
	-------------------------------------------------------------------'''

	def ask(self, cmd):
		self.ser.write((cmd + '\r').encode())
		end = time.monotonic() + 0.5
		while time.monotonic() < end:
			line = self.ser.read_until(b'\r').decode(errors='replace').strip()
			if not line:
				continue
			self.heard = time.monotonic()
			if line in ('?', 'N'):
				return line
			try:
				self.dispatch(line)
			except Exception:
				log.exception(f'reply {line!r}')
			if line[:2] == cmd[:2]:
				return line
		return None
	# ask

	'''-------------------------------------------------------------------
		one reply -> emits to Shack
	-------------------------------------------------------------------'''

	def dispatch(self, line):
		cmd, f = line[:2], line[3:].split(',')
		if cmd == 'BC' and f[0] in ('0', '1'):		# the band the keys and FO work on
			if f[0] != self.band:
				self.band, self.vfo = f[0], 'AB'[int(f[0])]
				self.q.put(f'FO {self.band}')
			self.emit('vfo', self.vfo)
		elif cmd == 'FO' and len(f) >= 8 and f[0] == self.band and f[1].isdigit():
			self.mhz = int(f[1]) / 1e6
			self.emit('mhz', self.mhz)
			if f[5] in RMODES:
				self.mode = RMODES[f[5]]
				self.emit('mode', self.mode)
		elif cmd == 'SM' and len(f) == 2 and f[0] == self.band and f[1].isdigit():		# 0-5 bars
			self.emit('bars', int(f[1]))
		return
	# dispatch

	'''-------------------------------------------------------------------
		shack's 250 ms timer, n counts its ticks; nothing new while the worker is still busy
	-------------------------------------------------------------------'''

	def poll(self, n):
		if not self.q.empty() or self.band is None:
			return
		self.q.put(f'SM {self.band}')
		if n % 4 == 0:		# every second, so the radio's own knob and keys show up too
			self.q.put('BC')
			self.q.put(f'FO {self.band}')
		return
	# poll

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def close(self):
		self.alive = False
		self.ser.close()
		return
	# close

	'''-------------------------------------------------------------------
		frequency and mode go out together in the band's FO line: read it, change those fields, write it back (there is no FQ on the D75).
		A frequency off the band's step gets the widest step it sits on, or the nearest 5 kHz
	-------------------------------------------------------------------'''

	def retune(self):
		with self.lock:
			want, self.want = self.want, {}
		line = self.ask(f'FO {self.band}')
		if not line or line[:2] != 'FO':
			return
		f = line.split(',')
		if 'mode' in want:
			f[5] = MODES[want['mode']]
		if 'mhz' in want:
			hz = round(want['mhz'] * 1e6)
			if f[6] == '1' and f[7].isdigit() and int(f[7]) < len(FINE):		# fine tuning on (band B: SSB, CW, AM)
				hz = round(hz / FINE[int(f[7])]) * FINE[int(f[7])]
			elif not on_step(hz, int(f[3], 16)):
				i = next((i for i in PICK if on_step(hz, i)), None)
				if i is None:
					i, hz = 0, round(hz / 5000) * 5000
				f[3] = f[4] = f'{i:X}'
			f[1] = f'{hz:010d}'
		r = self.ask(','.join(f))
		if r in ('?', 'N', None):
			log.info(f"refused: {','.join(f)!r} -> {r!r}")
			self.emit('error', f"The radio refused that on band {self.vfo}" + (' (band A: 136-174, 216-260, 410-470 MHz, FM and DV only)' if self.vfo == 'A' else ''))
		self.ask(f'FO {self.band}')		# where it is now, taken or not
		return
	# retune

	'''-------------------------------------------------------------------
		a control changed in shack; what this radio doesn't have (a bookmark from another one) is ignored
	-------------------------------------------------------------------'''

	def set(self, k, v):
		self.last_set = time.monotonic()
		if k in ('mhz', 'mode'):
			if k == 'mode' and v not in MODES:
				return
			with self.lock:
				first = not self.want
				self.want[k] = v
			if first:		# one write carries both
				self.q.put(self.retune)
		elif k == 'vfo' and v in ('A', 'B'):		# FO only writes to this band, so tuning follows it
			self.q.put(f"BC {'AB'.index(v)}")
		return
	# set
# class Rig
