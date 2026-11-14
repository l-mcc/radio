#!/usr/bin/env python3
import argparse, asyncio, glob, json, logging, socket, socketserver, struct, threading, time
from pathlib import Path

import serial
from aiohttp import web, WSMsgType, WSCloseCode

HERE = Path(__file__).resolve().parent
ME = 0xE0
MODES = {'LSB': 0, 'USB': 1, 'AM': 2, 'CW': 3, 'RTTY': 4, 'NBFM': 5, 'WBFM': 6, 'CW-R': 7, 'RTTY-R': 8, 'DV': 0x17}
RMODES = {v: k for k, v in MODES.items()}
MODE_LIST = ['LSB', 'USB', 'LSB-D', 'USB-D', 'AM', 'CW', 'CW-R', 'RTTY', 'RTTY-R', 'NBFM', 'NBFM-D', 'WBFM', 'DV']
DATA_OK = ('LSB', 'USB', 'NBFM', 'AM')		# modes that take the data flag
NOWIDTH = ('NBFM', 'WBFM', 'DV')		# fixed filters, 1A 03 gets NG
SPANS = [2500, 5000, 10000, 25000, 50000, 100000, 250000, 500000]		# ± Hz, center mode
CTL = {		# key: (cmd, sub); 14 xx is a 0..255 BCD level, 16 xx a single byte
	'vol': (0x14, 0x01), 'pwr': (0x14, 0x0A), 'rfg': (0x14, 0x02), 'sql': (0x14, 0x03), 'pbt1': (0x14, 0x07), 'pbt2': (0x14, 0x08), 'pre': (0x16, 0x02), 'agc': (0x16, 0x12),
	'nb': (0x16, 0x22), 'nr': (0x16, 0x40), 'anf': (0x16, 0x41), 'nbl': (0x14, 0x12), 'nrl': (0x14, 0x06),		# nbl, nrl: NB and NR levels 0-255
}
RCTL = {v: k for k, v in CTL.items()}
SCOPE = {'fix': 0x14, 'edge': 0x16, 'hold': 0x17}		# 27 xx 00 nn: center/fixed, fixed edge 1..3, hold
RSCOPE = {v: k for k, v in SCOPE.items()}
BANDS = [(1800, 2000), (3500, 4000), (5330, 5410), (7000, 7300), (10100, 10150), (14000, 14350), (18068, 18168),
	(21000, 21450), (24890, 24990), (28000, 29700), (50000, 54000), (144000, 148000), (420000, 450000)]		# kHz, US ham bands
RANGES = [(30, 1600), (1600, 2000), (2000, 6000), (6000, 8000), (8000, 11000), (11000, 15000), (15000, 20000), (20000, 22000),
	(22000, 26000), (26000, 30000), (30000, 45000), (45000, 60000), (60000, 74800), (74800, 108000), (108000, 137000),
	(137000, 200000), (400000, 470000)]		# kHz, the 705's fixed-edge ranges, codes 01..17
MAXW = 1000		# kHz, widest fixed view (assumed, same as the widest center span)

def ham(mhz):		# inside a (US) ham band: the only places PTT may key
	return any(lo <= mhz * 1000 <= hi for lo, hi in BANDS)

def band_window(khz):		# (range code, lo, hi) for the ham band around khz, or a 1 MHz window outside the bands
	rng = next(((i + 1, r) for i, r in enumerate(RANGES) if r[0] <= khz < r[1]), None)
	if not rng:
		return None
	lo, hi = next(((a, b) for a, b in BANDS if a <= khz <= b), (khz - MAXW // 2, khz + MAXW // 2))
	if hi - lo > MAXW:
		lo = max(lo, min(khz - MAXW // 2, hi - MAXW))
		hi = lo + MAXW
	code, (a, b) = rng
	return code, max(lo, a), min(hi, b)
log = logging.getLogger('shack.civ')		# child of shack's logger: lands in shack.log; on its own, goes to the root logger
SWR = [(0, 1.0), (48, 1.5), (80, 2.0), (120, 3.0)]		# 15 12 meter -> SWR, Icom's table (the X6200 assumed the same); +1 per 40 above

def swr(v):
	if v >= SWR[-1][0]:
		return SWR[-1][1] + (v - SWR[-1][0]) / 40
	(a, x), (b, y) = next((p, q) for p, q in zip(SWR, SWR[1:]) if v < q[0])
	return x + (v - a) / (b - a) * (y - x)

def cal(v, table):		# a raw meter reading -> its value, straight lines between the table's (raw, value) points; flat beyond the ends
	(a, x), (b, y) = next(((p, q) for p, q in zip(table, table[1:]) if v <= q[0]), (table[-1], table[-1]))
	return y if a == b else x + (max(v, a) - a) / (b - a) * (y - x)

PO_705 = [(0, 0), (21, 0.5), (43, 1), (65, 1.5), (83, 2), (95, 2.5), (105, 3), (114, 3.5), (124, 4), (143, 5), (183, 7.5), (213, 10), (255, 12)]		# 15 11 -> watts, Hamlib's IC-705 table

RIGS = {		# port: by-id pattern of the CI-V port; audio: ALSA name of its USB sound card (receive audio for shack's decoders); top: MHz; att: 11 xx value for on; agc: 16 12 values; watts: power at 00 and 255 (None = show %); swr: 15 12 meter works; scope: sends its band scope over CI-V (27 00)
	'IC-705': dict(port='*IC-705*if00', audio='plughw:CARD=CODEC', baud=115200, modes=MODE_LIST, top=470, att=0x20, agc={'Fast': 1, 'Mid': 2, 'Slow': 3}, watts=None, swr=True, scope=True, po=PO_705,
		vara=dict(drive=-15, pcm=103, pwr=255)),		# VARA transmit (shack's Modem): xara's drive dB, the card's PCM mixer step (0-128, 1 dB each), RF power 0-255. 100 % power: 9.8 W, ALC 0.08 (105: ALC 0.16; 115: 0.5-0.7; 127: 0.98; 2026-11-10)
	'Xiegu x6200': dict(port='*1a86_USB_Dual_Serial*if02', audio='plughw:CARD=Device', baud=19200, modes=['LSB', 'USB', 'LSB-D', 'USB-D', 'AM', 'CW', 'CW-R', 'NBFM', 'WBFM'], top=108, m26=True,		# its if00 is the radio's Linux console, never CI-V
		att=0x01, agc={'Off': 0, 'Fast': 1, 'Slow': 2, 'Auto': 3}, watts=(1, 8), swr=False, scope=False),		# power below 1 W reads back as 00 FA; 15 12 always 0 (firmware as of 2026-10). m26: firmware 1.0.7 answers 04 with LSB while in broadcast FM; 26 00 tells the truth (06)
}

def find_dev(name='IC-705'):		# the radio's CI-V port, None if not plugged in
	ids = glob.glob('/dev/serial/by-id/' + RIGS[name]['port'])
	return ids[0] if ids else None

def bcd(b):		# little-endian BCD
	return int(bytes(reversed(b)).hex())

def tobcd(n, nbytes=5):
	return bytes.fromhex(f'{n:0{nbytes * 2}d}')[::-1]

class Rig:
	def __init__(s, dev, baud, addr, emit):
		s.ser = serial.Serial(None, baud, timeout=0.2, exclusive=True)		# a second program opening the port gets 'busy' instead of stealing replies
		s.ser.port, s.ser.dtr, s.ser.rts = dev, False, False		# held low: some radios key TX on RTS
		s.ser.open()
		s.addr, s.emit = addr, emit		# emit(kind, value) is called from the reader thread
		s.wlock = threading.Lock()
		s.hdr, s.sweep = None, []
		s.base, s.data, s.fil = '', 0, 0		# mode byte (with its filter 1-3) and data flag arrive separately
		s.oddw = None		# last non-BCD 1A 03 reply, logged once
		s.last_set = 0.0		# NG only reported right after a user command; polls may NG quietly
		s.mhz, s.band, s.win = 0.0, False, None		# band view: fixed scope edges that follow the dial
		s.tx = 0		# as last reported by 1C 00
		s.vfo, s.split = '', 0		# vfo: what was last set from here ('' = never; the radio can't be asked); split: as last reported by 0F
		s.att_on = 0x20		# 11 xx for attenuator on: 705 20 (dB), X6200 01 (RIGS att)
		s.m26 = False		# read the mode with 26 00 (selected VFO) instead of 04 (RIGS m26)
		s.po = None		# (raw, watts) table for the Po meter (RIGS po); None: no power reading
		s.alive, s.heard = True, time.monotonic()		# heard: last frame from the radio
		threading.Thread(target=s.reader, daemon=True).start()

	def send(s, *body):
		if not s.alive:
			return
		with s.wlock:
			s.ser.write(bytes([0xFE, 0xFE, s.addr, ME, *body, 0xFD]))

	def read_fast(s, base=None):		# base '' = mode unknown, skip reads that may NG
		b = s.base if base is None else base
		s.send(0x03)
		s.send(*((0x26, 0x00) if s.m26 else (0x04,)))
		s.send(0x1C, 0x00)
		s.send(0x0F)
		if b in DATA_OK:
			s.send(0x1A, 0x06)
		if b and b not in NOWIDTH:
			s.send(0x1A, 0x03)

	def read_slow(s):
		for c in [*CTL.values(), (0x11,), (0x27, 0x19, 0x00), *((0x27, sub, 0x00) for sub in SCOPE.values())]:
			s.send(*c)

	@property
	def mode(s):		# 'USB-D': as emit_mode sends it, '' until the radio has said
		return s.base + ('-D' if s.data and s.base in DATA_OK else '') if s.base in MODES else ''

	def close(s):
		s.alive = False
		s.ser.close()

	def reader(s):
		buf = bytearray()
		while s.alive:
			try:
				buf += s.ser.read(s.ser.in_waiting or 1)
			except (serial.SerialException, OSError, TypeError):
				if s.alive:		# not our close(): unplugged or powered off
					s.alive = False
					s.emit('gone', 'Lost the radio.\n\nUnplugged or turned off?')
				return
			while (end := buf.find(0xFD)) >= 0:
				f, buf = bytes(buf[:end]), buf[end + 1:]
				i = f.rfind(b'\xfe\xfe')
				if i < 0 or len(f) < i + 5 or f[i + 3] == ME:		# junk or our own echo
					continue
				s.heard = time.monotonic()
				try:
					s.dispatch(f[i + 4], f[i + 5:])
				except Exception:
					log.exception(f'frame {f.hex(" ")}')

	def dispatch(s, cmd, d):
		if cmd in (0x00, 0x03) and len(d) == 5:
			s.mhz = bcd(d) / 1e6
			s.emit('mhz', s.mhz)
			if s.band and s.win:
				s.follow()
		elif cmd in (0x01, 0x04) and d:
			s.base = RMODES.get(d[0], f'{d[0]:02X}')
			s.emit_mode()
			if len(d) > 1:
				s.fil = d[1]
				s.emit('fil', d[1])
		elif cmd == 0x26 and len(d) >= 2 and d[0] == 0x00:		# selected VFO: mode, data, filter (data is left to 1A 06)
			s.base = RMODES.get(d[1], f'{d[1]:02X}')
			s.emit_mode()
			if len(d) > 3:
				s.fil = d[3]
				s.emit('fil', d[3])
		elif cmd == 0x1A and len(d) >= 2 and d[0] == 0x06:
			s.data = d[1]
			s.emit_mode()
		elif cmd == 0x1A and len(d) == 2 and d[0] == 0x03:
			h = d[1:].hex()
			if not h.isdigit():		# the X6200 sometimes answers with a non-BCD byte (2c seen): log it once per value, show the width as unknown
				if h != s.oddw:
					log.info(f'1A 03 width not BCD: {h} in {s.base} FIL{s.fil}')
					s.oddw = h
				return s.emit('width', 0)
			i = int(h)
			s.emit('width', (i + 1) * 200 if s.base == 'AM' else (i + 1) * 50 if i < 10 else (i - 4) * 100)
		elif cmd in (0x14, 0x16) and len(d) >= 2 and (cmd, d[0]) in RCTL:
			h = d[1:3].hex()
			s.emit(RCTL[cmd, d[0]], d[1] if cmd == 0x16 else int(h) if h.isdigit() else None)		# None: not BCD (the X6200's power below 1 W)
		elif cmd == 0x1C and len(d) == 2 and d[0] == 0x00:
			s.tx = d[1]
			s.emit('ptt', d[1])
		elif cmd == 0x0F and len(d) == 1 and d[0] in (0, 1):		# 10-13 are duplex settings
			if d[0] != s.split:
				s.split = d[0]
				s.emit('split', d[0])
		elif cmd == 0x11 and len(d) == 1:
			s.emit('att', int(d[0] != 0))
		elif cmd == 0x15 and len(d) == 3 and d[0] == 0x02:
			v = int(d[1:].hex())		# 0 = S0, 120 = S9, 241 = S9+60
			s.emit('smeter', round(v / 120 * 54 - 54 if v <= 120 else (v - 120) / 121 * 60))
		elif cmd == 0x15 and len(d) == 3 and d[0] == 0x12 and d[1:].hex().isdigit():
			s.emit('swr', (swr(int(d[1:].hex())), int(d[1:].hex())))		# (SWR, raw meter)
		elif cmd == 0x15 and len(d) == 3 and d[0] == 0x11 and d[1:].hex().isdigit() and s.po:		# Po meter
			s.emit('watts', cal(int(d[1:].hex()), s.po))
		elif cmd == 0x15 and len(d) == 3 and d[0] == 0x13 and d[1:].hex().isdigit():		# ALC meter: 120 = the top of its zone
			s.emit('alc', min(int(d[1:].hex()) / 120, 1.0))
		elif cmd == 0x27 and len(d) == 5 and d[0] == 0x19:		# ref level: 00, dB BCD (xx.xx), sign
			s.emit('ref', int(d[2:4].hex()) / 100 * (-1 if d[4] else 1))
		elif cmd == 0x27 and len(d) == 3 and d[0] in RSCOPE:
			s.emit(RSCOPE[d[0]], int(d[2:].hex()))
		elif cmd == 0x27 and len(d) >= 4 and d[0] == 0x00:
			s.scope(d)
		elif cmd == 0xFA and time.monotonic() - s.last_set < 0.5:
			s.emit('error', 'Radio rejected the last command')

	def apply_band(s):
		w = band_window(round(s.mhz * 1000))
		if not w:
			s.emit('error', 'No scope range covers this frequency')
			return
		s.set_win(*w)

	def follow(s):		# pan the view just past the dial when it leaves; rebuild it after a big jump
		khz, (lo, hi) = round(s.mhz * 1000), s.win
		w = hi - lo
		if lo <= khz <= hi:
			return
		if khz < lo - w or khz > hi + w:
			return s.apply_band()
		m = max(1, w // 50)		# land a little inside the edge
		lo += khz + m - hi if khz > hi else khz - m - lo
		code, (a, b) = next(((i + 1, r) for i, r in enumerate(RANGES) if r[0] <= khz < r[1]), (None, (0, 0)))
		if not code:
			return s.apply_band()
		lo = max(a, min(lo, b - w))		# stay inside the 705's edge range
		s.set_win(code, lo, min(lo + w, b))

	def set_win(s, code, lo, hi):
		s.win = (lo, hi)
		s.send(0x27, 0x1E, int(str(code), 16), 0x01, *tobcd(lo * 1000), *tobcd(hi * 1000))		# overwrite Edge 1
		s.send(0x27, 0x16, 0x00, 0x01)
		s.send(0x27, 0x14, 0x00, 0x01)
		s.send(0x27, 0x14, 0x00)

	def emit_mode(s):
		if s.base:
			s.emit('mode', s.base + ('-D' if s.data and s.base in DATA_OK else ''))

	def scope(s, d):
		seq, smax = bcd(d[2:3]), bcd(d[3:4])
		if seq == 1 and len(d) >= 15:
			mode, f1, f2 = d[4], bcd(d[5:10]), bcd(d[10:15])
			s.hdr = (f1 - f2, f1 + f2, f2) if mode == 0 else (f1, f2, None)		# center ± span, or fixed edges
			s.sweep = []
		elif s.hdr and seq == len(s.sweep) + 2:
			s.sweep.append(d[4:])
			if seq == smax:
				lo, hi, span = s.hdr
				s.emit('scope', struct.pack('<II', lo, hi) + b''.join(s.sweep))
				if span:
					s.emit('span', span)
				s.hdr = None
		else:
			s.hdr = None		# dropped a frame; wait for the next header

	def ptt(s, on):		# False: refused, outside the ham bands (unkeying always goes through)
		if on and not ham(s.mhz):
			log.info(f'PTT refused: {s.mhz:.6f} MHz is outside the ham bands')
			return False
		s.send(0x1C, 0x00, 0x01 if on else 0x00)
		s.send(0x1C, 0x00)
		return True

	def send_mode(s, base, fil):		# filter byte required by the X6200 (it ignores 06 xx alone)
		if s.m26 and base == 'WBFM':		# X6200: 06 06 lands on NBFM, 26 00 06 gets broadcast FM (firmware 1.0.7)
			s.send(0x26, 0x00, MODES[base], 0x00, fil)
		else:
			s.send(0x06, MODES[base], fil)

	def set(s, k, v):
		s.last_set = time.monotonic()
		if k == 'ptt':
			s.ptt(int(v))
		elif k == 'mhz':
			s.send(0x05, *tobcd(round(float(v) * 1e6)))
			s.send(0x03)		# read back so the UI settles immediately
		elif k == 'mode':
			base, data = v.removesuffix('-D'), v.endswith('-D')
			s.base, s.data, s.fil = base, int(data), 1		# ahead of the radio's report, so a fil right after this goes with the new mode
			s.send_mode(base, 0x01)		# FIL1
			if base in DATA_OK:
				s.send(0x1A, 0x06, *((0x01, 0x01) if data else (0x00, 0x00)))		# data mode, FIL1
			s.read_fast(base)
		elif k == 'fil':
			f = int(v)
			if s.data and s.base in DATA_OK:
				s.send(0x1A, 0x06, 0x01, f)		# in data mode the filter rides on the data command
			else:
				s.send_mode(s.base, f)
			s.read_fast()
		elif k == 'ref':
			n = round(abs(float(v)) * 2) * 50		# 0.5 dB steps, hundredths
			s.send(0x27, 0x19, 0x00, *bytes.fromhex(f'{n:04d}'), 0x01 if float(v) < 0 else 0x00)
			s.send(0x27, 0x19, 0x00)
		elif k == 'vfo':		# no read command for the selected VFO, so the engine remembers what it set
			s.vfo = 'A' if v == 'A' else 'B'
			s.send(0x07, 0x00 if v == 'A' else 0x01)
			s.emit('vfo', s.vfo)
			s.read_fast('')		# the other VFO's mode isn't known yet
		elif k == 'split':
			s.send(0x0F, 0x01 if int(v) else 0x00)
			s.send(0x0F)
		elif k == 'band':
			s.band = bool(int(v))
			s.emit('band', int(s.band))
			if s.band:
				s.apply_band()
			else:
				s.win = None
				s.send(0x27, 0x14, 0x00, 0x00)
				s.send(0x27, 0x14, 0x00)
		elif k in SCOPE:
			s.send(0x27, SCOPE[k], 0x00, int(v))
			s.send(0x27, SCOPE[k], 0x00)
		elif k == 'span':
			s.send(0x27, 0x15, 0x00, *tobcd(int(v)))
		elif k == 'att':
			s.send(0x11, s.att_on if int(v) else 0x00)
			s.send(0x11)
		elif k == 'v':		# shack's volume 0-1 -> AF level 0-255
			s.set('vol', round(float(v) * 255))
		elif k in CTL:
			cmd, sub = CTL[k]
			v = int(v)
			s.send(cmd, sub, *(bytes.fromhex(f'{v:04d}') if cmd == 0x14 else [v]))
			s.send(cmd, sub)
		else:
			raise KeyError(k)

HAMLIB = {'USB-D': 'PKTUSB', 'LSB-D': 'PKTLSB', 'NBFM': 'FM', 'NBFM-D': 'PKTFM', 'WBFM': 'WFM', 'CW-R': 'CWR', 'RTTY-R': 'RTTYR', 'AM-N': 'AMN', 'FM-N': 'FMN'}		# our mode names -> Hamlib's; the rest are the same
RHAMLIB = {v: k for k, v in HAMLIB.items()}
LONG = {'get_freq': 'f', 'set_freq': 'F', 'get_mode': 'm', 'set_mode': 'M', 'get_ptt': 't', 'set_ptt': 'T', 'get_vfo': 'v', 'set_vfo': 'V', 'get_split_vfo': 's', 'set_split_vfo': 'S'}		# \get_freq = f
DUMP = """0
2
2
100000.000000 470000000.000000 0x1ff -1 -1 0x3 0x0
0 0 0 0 0 0 0
1800000.000000 450000000.000000 0x1ff 100 100000 0x3 0x0
0 0 0 0 0 0 0
0x1ff 1
0 0
0 0
0
0
0
0


0x0
0x0
0x0
0x0
0x0
0x0"""		# \dump_state, protocol 0: what a Hamlib client (rigctl -m 2, WSJT-X, JS8Call, fldigi) asks first. RX range, TX range, tuning step, no filters, no RIT/XIT/IF shift, no preamp/att lists, no functions/levels/parms

class RigServer(socketserver.ThreadingTCPServer):
	allow_reuse_address = daemon_threads = True
	clients = 0
	closed = False

	def stop(s):		# listener and every connected client: a client must never outlive the rig it keys
		s.closed = True
		s.shutdown()
		for c in list(s.conns):
			try:
				c.shutdown(socket.SHUT_RDWR)		# the client sees the connection close
			except OSError:
				pass
		s.server_close()

def serve_rigctld(rig, port=4532, host='127.0.0.1'):		# the part of rigctld that xara (T), Pat (f F), openslot.sh and Hamlib clients use, answered by the radio shack has open. rig: civ.Rig or cat.Rig
	def settled(ok):		# a set is answered once the radio has reported it (0.5 s at most): a client reading straight back gets the new value
		t = time.monotonic() + 0.5
		while not ok() and time.monotonic() < t and rig.alive:
			time.sleep(0.02)

	def answer(op, a):		# one command -> its reply, None = not one of ours
		if op == 'f':
			return str(round(rig.mhz * 1e6))
		if op == 'F' and a:
			hz = round(float(a[-1]))		# a[-1]: 'F VFOA 7102300' from a client in VFO mode
			rig.set('mhz', hz / 1e6)
			settled(lambda: round(rig.mhz * 1e6) == hz)
			return 'RPRT 0'
		if op == 'm':
			return f'{HAMLIB.get(rig.mode, rig.mode)}\n0' if rig.mode else 'RPRT -1'		# mode, passband (0 = the radio's own)
		if op == 'M' and a:
			name = next((x for x in a if not x.startswith('VFO') and not x.lstrip('-').isdigit()), '')
			mode = RHAMLIB.get(name, name)
			if mode not in rig.modes:
				return 'RPRT -1'
			if mode != rig.mode:		# already there: untouched (a set would put a CI-V radio back on FIL1)
				rig.set('mode', mode)
				settled(lambda: rig.mode == mode)
			return 'RPRT 0'
		if op == 't':
			return str(int(bool(rig.tx)))
		if op == 'v':
			return 'VFO' + (rig.vfo or 'A')
		if op == 'V' and a and a[-1] in ('VFOA', 'VFOB'):
			if a[-1][3] != rig.vfo:
				rig.set('vfo', a[-1][3])
				settled(lambda: rig.vfo == a[-1][3])
			return 'RPRT 0'
		if op == 's':
			return f"{rig.split}\nVFO{'B' if rig.split else rig.vfo or 'A'}"		# split, TX VFO
		if op == 'S' and a and a[0] in ('0', '1'):
			rig.set('split', int(a[0]))
			settled(lambda: rig.split == int(a[0]))
			return 'RPRT 0' if rig.split == int(a[0]) else 'RPRT -1'
		if op == 'chk_vfo':
			return '0'		# commands come without a VFO argument
		if op == 'dump_state':
			return DUMP
		if op in ('dump_caps', '1'):		# Pat's "is rigctld there?" (it reads one line)
			return 'Caps dump for model: 2'
		if op == 'get_powerstat':
			return '1'
		if op == 'get_lock_mode':		# Hamlib 4.5 asks before every set_mode
			return '0'
		return None

	class H(socketserver.StreamRequestHandler):
		def handle(h):
			keyed = False
			srv.conns.add(h.connection)
			srv.clients += 1
			rig.emit('ptt_clients', srv.clients)
			try:
				for line in h.rfile:
					if not rig.alive or srv.closed:		# radio gone: hang up rather than answer RPRT 0 for a PTT that does nothing
						break
					c = line.decode(errors='replace').split()
					if not c:
						continue
					op = c[0][1:] if c[0].startswith('\\') else c[0]		# \set_freq 7102300 = F 7102300
					op = LONG.get(op, op)
					if op == 'T' and len(c) > 1 and c[-1] in ('0', '1', '2', '3'):		# Hamlib: 1 = on, 2 = on (mic), 3 = on (data)
						ok = rig.ptt(c[-1] != '0')
						keyed = c[-1] != '0' and ok
						r = 'RPRT 0' if ok else 'RPRT -1'		# -1: refused, outside the ham bands
					elif op in ('q', 'Q'):
						break
					else:
						try:
							r = answer(op, c[1:])
						except (ValueError, KeyError) as e:
							log.info(f'rigctld port: {" ".join(c)!r}: {e!r}')
							r = 'RPRT -1'
						if r is None:
							if c[0] not in srv.odd:		# once per command: what a new client wants that isn't here yet
								srv.odd.add(c[0])
								log.info(f'rigctld port: no answer for {" ".join(c)!r}')
							r = 'RPRT -4'		# not implemented
					h.wfile.write((r + '\n').encode())
			except OSError:		# the client dropped the connection mid-reply
				pass
			finally:
				if keyed:
					rig.ptt(False)		# client gone while keyed: never leave the radio transmitting
				srv.conns.discard(h.connection)
				srv.clients -= 1
				if not srv.closed:		# after stop() the window may already belong to another radio
					rig.emit('ptt_clients', srv.clients)
	srv = RigServer((host, port), H)
	srv.conns, srv.odd = set(), set()
	threading.Thread(target=srv.serve_forever, daemon=True).start()
	return srv

#=====================================================================

rig = queue = None
state = {'rig': 'IC-705', 'mhz': 0.0, 'mode': '', 'span': 0}
clients = set()

async def broadcast(m):
	for ws in list(clients):
		try:
			await (ws.send_bytes(m) if isinstance(m, bytes) else ws.send_json(m))
		except ConnectionError:
			clients.discard(ws)

async def pump():
	while True:
		kind, v = await queue.get()
		if kind == 'scope':
			await broadcast(v)
		elif kind == 'smeter':
			await broadcast({'type': 'smeter', 'db': v})
		elif kind == 'error':
			await broadcast({'type': 'error', 'msg': v})
		elif state.get(kind) != v:
			state[kind] = v
			await broadcast({'type': 'state', 'state': state})

async def poll():
	n = 0
	while True:
		if clients and time.monotonic() - rig.last_set > 0.5:		# stay out of the way of user commands
			rig.send(0x15, 0x02)
			if n % 4 == 0:
				rig.read_fast()
			elif n % 4 == 2:
				rig.read_slow()
			n += 1
		await asyncio.sleep(0.25)

async def ws_handler(request):
	ws = web.WebSocketResponse(heartbeat=20)
	await ws.prepare(request)
	clients.add(ws)
	await ws.send_json({'type': 'caps', 'rig': state['rig'], 'modes': MODE_LIST, 'spans': SPANS})
	await ws.send_json({'type': 'state', 'state': state})
	try:
		async for msg in ws:
			if msg.type != WSMsgType.TEXT:
				continue
			try:
				m = json.loads(msg.data)
				if m.get('cmd') == 'set':
					rig.set(m['key'], m['value'])
				else:
					raise ValueError(f'unknown command {m.get("cmd")}')
			except Exception as e:
				await ws.send_json({'type': 'error', 'msg': repr(e)})
	finally:
		clients.discard(ws)
	return ws

def page(name):
	async def h(request):
		return web.FileResponse(HERE / name, headers={'Cache-Control': 'no-cache'})
	return h

async def close_clients(app):		# open websockets would otherwise hold shutdown for a minute
	for ws in list(clients):
		await ws.close(code=WSCloseCode.GOING_AWAY, message=b'engine shutdown')

async def lifecycle(app):
	global rig, queue
	a, loop = app['args'], asyncio.get_running_loop()
	queue = asyncio.Queue()
	rig = Rig(a.dev, a.baud, a.addr, lambda k, v: loop.call_soon_threadsafe(queue.put_nowait, (k, v)))
	rig.send(0x27, 0x10, 0x01)		# scope on
	rig.send(0x27, 0x11, 0x01)		# scope data out on CI-V
	rig.read_fast()
	rig.read_slow()
	tasks = [asyncio.create_task(pump()), asyncio.create_task(poll())]
	yield
	for t in tasks:
		t.cancel()
	rig.send(0x27, 0x11, 0x00)

#=====================================================================

if __name__ == '__main__':
	ap = argparse.ArgumentParser()
	ap.add_argument('--dev', default=find_dev() or '/dev/ttyACM0')
	ap.add_argument('--baud', type=int, default=115200)
	ap.add_argument('--addr', type=lambda x: int(x, 0), default=0xA4)
	ap.add_argument('--host', default='0.0.0.0')
	ap.add_argument('--port', type=int, default=8700)
	a = ap.parse_args()

	logging.basicConfig(level=logging.INFO, format='%(levelname)-6s%(message)s')
	log.info(f'rig on {a.dev}')
	app = web.Application()
	app['args'] = a
	app.router.add_get('/', page('radcon.html'))
	app.router.add_get('/radcon.js', page('radcon.js'))
	app.router.add_get('/ws', ws_handler)
	app.cleanup_ctx.append(lifecycle)
	app.on_shutdown.append(close_clients)
	web.run_app(app, host=a.host, port=a.port, access_log=None)
