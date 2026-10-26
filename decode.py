#!/usr/bin/env python3
# decode.py - decoders for rx Digital out (UDP 48 kHz s16le mono)
#	decode.py	curses UI: pick decoder and settings, click the spectrum to tune
#	decode.py rtty [-b BAUD] [-w SHIFT] [-c HZ] [-r] [-s DB]	RTTY, text to stdout, status to stderr
#	decode.py cw [-c HZ] [-s DB]	CW, strongest signal unless -c
#	decode.py send [-b BAUD] [-w SHIFT] [--cw WPM] [-c HZ] [-r] [-n SNR] [-l] [text]	test sender
#	decode.py rec [SECONDS]	record Digital out to rec.wav (default 60 s)
#	decoded text also goes to cw.txt / rtty.txt in the current folder, overwritten each run
#	the UI keeps its settings in decode.json next to this file
#	DWD weather: -r -b 50 -w 450
import sys, os, json, socket, time, argparse, select, curses, locale, wave
import numpy as np

FS = 48000
BAUD = 1000 / 22	# 45.45, sender only
SHIFT = 170
DEC = 40	# 48k -> 1200
FS2 = FS // DEC
ER = 400	# CW envelope rate
F0, F1 = 300, 2900	# audio range searched and shown

def setmode(baud, shift):
	global BAUD, SHIFT
	BAUD, SHIFT = baud, shift

# ITA2, US figures; index = 5-bit code, first bit = LSB; 27 FIGS, 31 LTRS, bell dropped
LTRS = '\0E\nA SIU\rDRJNFCKTZLWHYPQOBG\0MXV\0'
FIGS = "\x003\n- \x0087\r$4',!:(5\")2#6019?&\0./;\0"

MORSE = {'.-': 'A', '-...': 'B', '-.-.': 'C', '-..': 'D', '.': 'E', '..-.': 'F', '--.': 'G', '....': 'H', '..': 'I',
	'.---': 'J', '-.-': 'K', '.-..': 'L', '--': 'M', '-.': 'N', '---': 'O', '.--.': 'P', '--.-': 'Q', '.-.': 'R',
	'...': 'S', '-': 'T', '..-': 'U', '...-': 'V', '.--': 'W', '-..-': 'X', '-.--': 'Y', '--..': 'Z',
	'-----': '0', '.----': '1', '..---': '2', '...--': '3', '....-': '4', '.....': '5', '-....': '6', '--...': '7',
	'---..': '8', '----.': '9', '.-.-.-': '.', '--..--': ',', '..--..': '?', '-..-.': '/', '-....-': '-',
	'---...': ':', '.----.': "'", '-.--.-': ')', '.-..-.': '"', '.--.-.': '@', '.-...': '&',
	'-...-': '=', '.-.-.': '+', '-.--.': '<KN>', '...-.-': '<SK>'}	# = BT, + AR

def lowpass(fc, fs, n):
	t = np.arange(n) - (n - 1) / 2
	h = np.sinc(2 * fc / fs * t) * np.blackman(n)
	return h / h.sum()

class Fir:	# streaming FIR with decimation
	def __init__(s, taps, dec=1):
		s.t, s.dec, s.ph = taps, dec, 0
		s.h = np.zeros(len(taps) - 1, complex)
	def __call__(s, x):
		x = np.concatenate((s.h, x))
		s.h = x[len(x) - len(s.h):]
		n = len(x) - len(s.h)	# new samples
		y = np.convolve(x, s.t, 'valid')[s.ph::s.dec]
		s.ph = (s.ph - n) % s.dec
		return y

class Mixer:	# shifts f down to DC, phase continuous
	def __init__(s, f, fs):
		s.fs, s.p = fs, 0.0
		s.set(f)
	def set(s, f):
		s.w = 2 * np.pi * f / s.fs
	def __call__(s, x):
		y = x * np.exp(-1j * (s.p + s.w * np.arange(len(x))))
		s.p = (s.p + s.w * len(x)) % (2 * np.pi)
		return y

class Tuner:	# averaged spectrum, finds the strongest tone pair (RTTY) or tone (CW, shift 0)
	N = 16384
	def __init__(s, center, sq, shift, jump):
		s.fixed = center is not None
		s.center = center or 0.0
		s.sq, s.snr, s.jump = sq, 0.0, jump
		s.buf, s.new, s.avg, s.sc = np.zeros(0), 0, None, None
		s.win = np.hanning(s.N)
		s.df = FS / s.N
		s.sh = round(shift / s.df)
		s.lo, s.hi = int(F0 / s.df), int(F1 / s.df) - s.sh	# (low) tone in F0..F1-shift
	def __call__(s, x):	# True when the spectrum was updated
		s.buf = np.concatenate((s.buf, x))[-s.N:]
		s.new += len(x)
		if s.new < s.N // 4 or len(s.buf) < s.N:
			return False
		s.new = 0
		p = np.abs(np.fft.rfft(s.buf * s.win)) ** 2
		p = np.convolve(p, np.ones(3), 'same')	# tolerate a bin of drift
		s.avg = p if s.avg is None else s.avg + 0.15 * (p - s.avg)	# ~0.6 s
		sc = s.sc = np.minimum(s.avg[s.lo:s.hi], s.avg[s.lo + s.sh:s.hi + s.sh])	# weaker tone of the pair
		s.nf = np.median(s.avg[s.lo:s.hi + s.sh])
		k = int(np.argmax(sc))
		kc = round(s.center / s.df - s.sh / 2) - s.lo
		cur = sc[kc] if 0 <= kc < len(sc) else 0.0
		if not s.fixed and s.db(sc[k]) > s.sq and (s.db(cur) < s.sq or sc[k] > s.jump * cur or abs(k - kc) <= 10):
			s.center = (s.lo + k + s.sh / 2) * s.df	# follow drift, jump to a stronger one
			cur = sc[k]
		s.snr = s.db(cur)
		return True
	def db(s, v):
		return 10 * np.log10(v / s.nf + 1e-12)
	def snap(s, f, w):	# strongest tone (pair) within w Hz of f, if there is one
		if s.sc is None:
			return f
		k0, r = round(f / s.df - s.sh / 2) - s.lo, round(w / s.df)
		a, b = max(k0 - r, 0), min(k0 + r + 1, len(s.sc))
		if a >= b:
			return f
		k = a + int(np.argmax(s.sc[a:b]))
		return (s.lo + k + s.sh / 2) * s.df if s.db(s.sc[k]) > s.sq - 3 else f

def corr(a, b):	# -1 when the tones alternate (RTTY), ~0 for unrelated carriers or noise
	if len(a) < 1200:
		return 0.0
	a, b = a - a.mean(), b - b.mean()
	return (a @ b) / (np.sqrt((a @ a) * (b @ b)) + 1e-12)

class Uart:	# async 1 start, 5 data, 1.5 stop on soft bits (>0 mark)
	def __init__(s, spb):
		s.spb, s.buf, s.i = spb, np.zeros(0), 0
	def bit(s, c):	# average over the middle half of a bit
		h = s.spb / 4
		return s.buf[int(c - h):int(c + h) + 1].mean()
	def __call__(s, d):
		b = s.buf = np.concatenate((s.buf[s.i:], d))
		i, out, spb = 0, [], s.spb
		while True:
			e = np.flatnonzero((b[i:-1] > 0) & (b[i + 1:] <= 0))	# mark -> space
			if not len(e):
				i = max(len(b) - 1, 0)
				break
			t = i + e[0] + 0.5
			if t + 7 * spb >= len(b):
				i += e[0]	# wait for the rest of the character
				break
			if s.bit(t + 0.5 * spb) > 0:	# not a start bit
				i += e[0] + 1
				continue
			if s.bit(t + 6.5 * spb) <= 0:	# framing error, resync
				i = int(t) + 1
				continue
			out.append(sum((s.bit(t + (k + 1.5) * spb) > 0) << k for k in range(5)))
			i = int(t + 6.5 * spb)
		s.i = i
		return out

class Baudot:
	def __init__(s):
		s.figs = False
	def __call__(s, c):
		if c == 31 or c == 27:
			s.figs = c == 27
			return ''
		ch = (FIGS if s.figs else LTRS)[c]
		if c == 4:
			s.figs = False	# unshift on space
		return '' if ch in '\0\r' else ch

class Rtty:	# decoder(x) -> (text, new station)
	def __init__(s, baud, shift, rev, center, sq):
		s.baud, s.shift, s.rev = baud, shift, rev
		s.tu = Tuner(center, sq, shift, 2)	# jump to a 3 dB stronger pair
		s.mix = Mixer(s.tu.center, FS)
		s.lp1 = Fir(lowpass(shift / 2 + 2 * baud, FS, 331), DEC)	# both tones + keying sidebands, to 1200 S/s
		pol = -1 if rev else 1
		s.mm, s.ms = Mixer(pol * shift / 2, FS2), Mixer(-pol * shift / 2, FS2)
		s.lpm, s.lps = Fir(lowpass(0.75 * baud, FS2, 57)), Fir(lowpass(0.75 * baud, FS2, 57))
		s.uart, s.bd = Uart(FS2 / baud), Baudot()
		s.hm = s.hs = np.zeros(0)	# last 2 s of tone envelopes
		s.op, s.said, s.k = False, None, 0.0
	def tune(s, f):	# None = auto
		s.tu.fixed = f is not None
		if f is not None:
			s.tu.center = f
			s.mix.set(f)
	def info(s):
		return f'alternation {s.k:+.2f}'
	def __call__(s, x):
		new = False
		if s.tu(x):
			s.mix.set(s.tu.center)
			s.k = corr(s.hm, s.hs)
			if s.op:	# hysteresis: closes 3 dB lower and on weaker alternation
				s.op = s.tu.snr > s.tu.sq - 3 and s.k < -0.3
			else:
				s.op = s.tu.snr > s.tu.sq and s.k < -0.45
			if s.op and (s.said is None or abs(s.tu.center - s.said) > 20):	# only when the frequency moves
				s.said, new = s.tu.center, True
				s.bd.figs = False
		bb = s.lp1(s.mix(x))
		m, sp = np.abs(s.lpm(s.mm(bb))), np.abs(s.lps(s.ms(bb)))
		s.hm, s.hs = np.concatenate((s.hm, m))[-2 * FS2:], np.concatenate((s.hs, sp))[-2 * FS2:]
		txt = ''.join(s.bd(c) for c in s.uart((m - sp) / (m + sp + 1e-9)))
		return (txt if s.op else ''), new

class Keyer:	# envelope -> marks and spaces -> text, adapts to speed
	def __init__(s):
		s.hi, s.lo, s.on, s.n, s.prev = 1e-9, 0.0, False, 0, 0
		s.reset()
	def reset(s):	# new station: forget the speed
		s.u = 0.06 * ER	# dot length in samples, starts at 20 wpm
		s.marks, s.sym, s.spaced = [], '', True
	def wpm(s):
		return 1.2 * ER / s.u
	def mark(s, d):	# classify a mark, re-estimate the dot from the last 20
		s.marks = (s.marks + [d])[-20:]
		m = np.log(s.marks)
		if len(m) >= 4 and m.max() - m.min() > np.log(2):	# both dots and dashes seen: 2-means on log length
			c = np.array([m.min(), m.max()])
			for _ in range(5):
				g = np.abs(m - c[0]) > np.abs(m - c[1])
				c = np.array([m[~g].mean(), m[g].mean()])
			if 1.8 < np.exp(c[1] - c[0]) < 6:
				s.u = (np.exp(c[0]) + np.exp(c[1]) / 3) / 2
				m = None
		if m is not None:	# one length only, or clusters don't look like dots and dashes
			s.u += 0.2 * ((d if d < 2 * s.u else d / 3) - s.u)
		s.u = min(max(s.u, 0.012 * ER), 0.2 * ER)	# 6..100 wpm
		s.sym += '.' if d < 2 * s.u else '-'
	def __call__(s, env):
		o = ''
		for v in env:
			s.hi = v if v > s.hi else s.hi * 0.9995	# peak, ~5 s decay
			s.lo = v if v < s.lo else s.lo + 0.001 * (v - s.lo)	# floor, slow rise
			th = s.lo + (s.hi - s.lo) * (0.4 if s.on else 0.6)	# hysteresis
			s.n += 1
			if s.on and v < th:
				if s.n < 0.3 * s.u:	# glitch: undo the mark
					s.n += s.prev
				else:
					s.mark(s.n)
					s.n = 0
				s.on = False
			elif not s.on and v > th:
				s.on, s.prev, s.n, s.spaced = True, s.n, 0, False
			elif not s.on:
				if s.sym and s.n > 2 * s.u:	# character gap
					o += MORSE.get(s.sym, '*')
					s.sym = ''
				elif not s.spaced and s.n > 5 * s.u:	# word gap
					o += ' '
					s.spaced = True
		return o

def cwcut(w):	# narrowest filter that keeps the dots: 12 Hz at 20 wpm
	return min(max(0.6 * w, 10), 30)

class Cw:	# decoder(x) -> (text, new station)
	def __init__(s, center, sq):
		s.tu = Tuner(center, sq, 0, 4)	# jump to a 6 dB stronger signal
		s.mix = Mixer(s.tu.center, FS)
		s.lp1 = Fir(lowpass(200, FS, 331), DEC)	# to 1200 S/s
		s.ky = Keyer()
		s.fw = s.ky.wpm()	# speed the filter is set for
		s.lp2 = Fir(lowpass(cwcut(s.fw), FS2, 115), FS2 // ER)	# to 400 S/s
		s.op, s.said = False, None
	def tune(s, f):
		Rtty.tune(s, f)
		s.ky.reset()
	def info(s):
		return f'{s.ky.wpm():.0f} wpm'
	def __call__(s, x):
		new = False
		if s.tu(x):
			s.mix.set(s.tu.center)
			if not s.op and s.tu.snr > s.tu.sq:
				s.ky.reset()	# relearn the speed, forget noise
			s.op = s.tu.snr > s.tu.sq - 3 if s.op else s.tu.snr > s.tu.sq
			if s.op and (s.said is None or abs(s.tu.center - s.said) > 20):	# new station
				s.said, new = s.tu.center, True
				s.ky.reset()
		txt = s.ky(np.abs(s.lp2(s.lp1(s.mix(x)))))
		if abs(s.ky.wpm() / s.fw - 1) > 0.15:	# follow the speed
			s.fw = s.ky.wpm()
			s.lp2.t = lowpass(cwcut(s.fw), FS2, 115)
		return (txt if s.op else ''), new

def listen(port):
	sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	try:
		sk.bind(('127.0.0.1', port))
	except OSError as e:
		sys.exit(f'udp {port}: {e.strerror} (another decoder or direwolf listening?)')
	return sk

LOGS = {}
def log(kind, txt):	# cw.txt / rtty.txt, truncated on first use each run
	if kind not in LOGS:
		LOGS[kind] = open(kind.lower() + '.txt', 'w')
	LOGS[kind].write(txt)
	LOGS[kind].flush()

def plain(dec, a):	# command line: text to stdout, status to stderr
	sk = listen(a.port)
	print(f'listening on udp {a.port},', 'auto tune' if a.center is None else f'{a.center:.0f} Hz', file=sys.stderr)
	while True:
		txt, new = dec(np.frombuffer(sk.recv(4096), '<i2') / 32768.0)
		if new:
			print(f'\n[{dec.tu.center:.0f} Hz  {dec.tu.snr:.0f} dB]', file=sys.stderr)
			log(a.kind, '\n')
		if txt:
			sys.stdout.write(txt)
			sys.stdout.flush()
			log(a.kind, txt)

# curses UI

PRESETS = [('ham', 1000 / 22, 170, False), ('DWD', 50, 450, True)]	# RTTY name, baud, shift, mark lower
BAUDS = [1000 / 22, 50, 75, 100]
SHIFTS = [170, 200, 425, 450, 850]
RAMP = ' .:-=+*#%@'	# spectrum, 3 dB a step
CONF = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'decode.json')
KEYS = 'm decoder  v preset  b baud  w shift  r mark  ←→ tune  a auto  +- squelch  c clear  q quit'

class Ui:
	def __init__(s, scr, sk, port):
		s.scr, s.sk = scr, sk
		s.sk.setblocking(False)
		s.port = port
		s.kind, s.baud, s.shift, s.rev = 'RTTY', *PRESETS[0][1:]
		s.sq = {'RTTY': 6, 'CW': 10}
		try:	# last run's settings
			c = json.load(open(CONF))
			s.kind = c['kind'] if c['kind'] in s.sq else s.kind
			s.baud, s.shift, s.rev = float(c['baud']), int(c['shift']), bool(c['rev'])
			s.sq.update({k: float(v) for k, v in c['sq'].items() if k in s.sq})
		except (OSError, ValueError, KeyError, TypeError, AttributeError):
			pass
		s.lines, s.fields = [''], []
		s.h, s.w = scr.getmaxyx()
		s.make(None)
	def make(s, center):	# new decoder with the current settings
		if s.kind == 'RTTY':
			s.dec = Rtty(s.baud, s.shift, s.rev, center, s.sq['RTTY'])
		else:
			s.dec = Cw(center, s.sq['CW'])
		s.newline()
	def newline(s):
		if s.lines[-1]:
			s.lines.append('')
			log(s.kind, '\n')
	def add(s, txt):
		if txt:
			log(s.kind, txt)
		for ch in txt:
			if ch == '\n':
				s.lines.append('')
			else:
				s.lines[-1] += ch
		del s.lines[:-500]
	def preset(s):
		return next((p[0] for p in PRESETS if p[1:] == (s.baud, s.shift, s.rev)), 'custom')
	def act(s, k, d=1):	# keys, clicks (k = key) and wheel (d = -1/+1)
		tu = s.dec.tu
		fixed = tu.center if tu.fixed else None
		if k == 'm':
			s.kind = 'CW' if s.kind == 'RTTY' else 'RTTY'
			s.make(None)
		elif k in 'vbwr' and s.kind == 'RTTY':
			if k == 'v':
				i = next((i for i, p in enumerate(PRESETS) if p[0] == s.preset()), -1)
				s.baud, s.shift, s.rev = PRESETS[(i + d) % len(PRESETS)][1:]
			elif k == 'b':
				s.baud = BAUDS[(BAUDS.index(s.baud) + d) % len(BAUDS) if s.baud in BAUDS else 0]
			elif k == 'w':
				s.shift = SHIFTS[(SHIFTS.index(s.shift) + d) % len(SHIFTS) if s.shift in SHIFTS else 0]
			else:
				s.rev = not s.rev
			s.make(fixed)
		elif k == 'a':
			s.dec.tune(None if tu.fixed else tu.center)
		elif k == '+':
			s.sq[s.kind] = tu.sq = tu.sq + d
		elif k == '-':
			s.sq[s.kind] = tu.sq = tu.sq - d
		elif k == 'c':
			s.lines = ['']
		elif k == 't':	# tune by d Hz
			s.dec.tune(min(max(tu.center + d, F0), F1))
		if k in 'mvbwr+-':
			s.save()
	def save(s):
		try:
			with open(CONF, 'w') as f:
				json.dump({'kind': s.kind, 'baud': s.baud, 'shift': s.shift, 'rev': s.rev, 'sq': s.sq}, f)
		except OSError:
			pass
	def click(s, x, y, b):
		up = b & curses.BUTTON4_PRESSED
		down = b & getattr(curses, 'BUTTON5_PRESSED', 0x200000)
		if not (up or down or b & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED)):
			return	# ignore releases
		if y in (3, 4, 5):	# spectrum: click snaps to the strongest signal near it, wheel nudges 10 Hz
			if up or down:
				s.act('t', 10 if up else -10)
			else:
				f = F0 + (x + 0.5) * (F1 - F0) / s.w
				s.dec.tune(s.dec.tu.snap(f, 2.5 * (F1 - F0) / s.w))
			return
		for fy, x0, x1, k in s.fields:
			if fy == y and x0 <= x < x1:
				s.act(k, -1 if down else 1)
	def put(s, y, x, txt, attr=0, key=None):	# clipped addstr, remembers clickable fields
		if y >= s.h or x >= s.w:
			return x
		txt = txt[:s.w - x - (y == s.h - 1)]
		s.scr.addstr(y, x, txt, attr)
		if key:
			s.fields.append((y, x, x + len(txt), key))
		return x + len(txt)
	def field(s, y, x, label, val, key, hint=None):
		x = s.put(y, x, label + ' ', curses.A_DIM, key)
		x = s.put(y, x, val, curses.color_pair(2) | curses.A_BOLD, key)
		return s.put(y, x, f'({hint or key})', curses.A_DIM, key) + 2
	def draw(s):
		s.h, s.w = s.scr.getmaxyx()
		s.scr.erase()
		s.fields = []
		if s.h < 12 or s.w < 60:
			s.put(0, 0, 'terminal too small (60x12)')
			s.scr.refresh()
			return
		dec, tu = s.dec, s.dec.tu
		s.put(0, 0, f' decode  udp {s.port}'.ljust(s.w - 34) + 'spectrum: click tune, wheel ±10 Hz '.rjust(34)[-max(s.w - 25, 0):], curses.A_REVERSE)
		x = s.field(1, 0, 'Decoder', s.kind, 'm')
		if s.kind == 'RTTY':
			x = s.field(1, x, 'Preset', s.preset(), 'v')
			x = s.field(1, x, 'Baud', f'{s.baud:g}' if s.baud != BAUDS[0] else '45.45', 'b')
			x = s.field(1, x, 'Shift', f'{s.shift}', 'w')
			x = s.field(1, x, 'Mark', 'low' if s.rev else 'high', 'r')
		x = s.field(2, 0, 'Tune', 'fixed' if tu.fixed else 'auto', 'a')
		x = s.put(2, x, f'{tu.center:.0f} Hz  {tu.snr:.0f} dB  ' if tu.center else '-- Hz        ', curses.A_BOLD)
		x = s.field(2, x, 'Squelch', f'{tu.sq:g} dB', '+', '+-')
		x = s.put(2, x, ' open ' if dec.op else ' closed ', curses.color_pair(1 if dec.op else 3) | curses.A_REVERSE)
		s.put(2, x + 2, dec.info(), curses.A_DIM)
		if tu.avg is not None:	# spectrum bar, tone markers, scale
			bins = np.linspace(F0, F1, s.w + 1) / tu.df
			row = ''.join(RAMP[min(max(int(tu.db(tu.avg[int(a):max(int(b), int(a) + 1)].max()) / 3), 0), 9)] for a, b in zip(bins, bins[1:]))
			s.put(3, 0, row, curses.color_pair(4))
		col = lambda f: int((f - F0) / (F1 - F0) * s.w)
		if s.kind == 'RTTY':
			for f, c in ((tu.center - s.shift / 2, 'S' if not s.rev else 'M'), (tu.center + s.shift / 2, 'M' if not s.rev else 'S')):
				if 0 <= col(f) < s.w:
					s.put(4, col(f), c, curses.color_pair(2) | curses.A_BOLD)
		elif 0 <= col(tu.center) < s.w:
			s.put(4, col(tu.center), '^', curses.color_pair(2) | curses.A_BOLD)
		for f in range(500, F1, 500):
			s.put(5, col(f), f'{f}', curses.A_DIM)
		s.put(6, 0, '─' * s.w, curses.A_DIM)
		rows, out = s.h - 8, []	# text: wrap the last lines to fit
		for ln in reversed(s.lines):
			out[:0] = [ln[i:i + s.w] for i in range(0, max(len(ln), 1), s.w)]
			if len(out) >= rows:
				break
		for i, ln in enumerate(out[-rows:]):
			s.put(7 + i, 0, ln, curses.color_pair(1))
		s.put(s.h - 1, 0, KEYS.ljust(s.w), curses.A_REVERSE)
		s.scr.refresh()
	def run(s):
		last = 0
		while True:
			select.select([s.sk, sys.stdin], [], [], 0.1)
			data = []
			while True:	# everything that has arrived
				try:
					data.append(s.sk.recv(4096))
				except BlockingIOError:
					break
			if data:
				txt, new = s.dec(np.frombuffer(b''.join(data), '<i2') / 32768.0)
				if new:
					s.newline()
				s.add(txt)
			while (k := s.scr.getch()) != -1:
				if k == ord('q'):
					return
				if k == curses.KEY_MOUSE:
					try:
						_, x, y, _, b = curses.getmouse()
						s.click(x, y, b)
					except curses.error:
						pass
				elif k in (curses.KEY_LEFT, curses.KEY_RIGHT):
					s.act('t', 10 if k == curses.KEY_RIGHT else -10)
				elif k in (curses.KEY_SLEFT, curses.KEY_SRIGHT):
					s.act('t', 1 if k == curses.KEY_SRIGHT else -1)
				elif k == ord('='):
					s.act('+')
				elif 0 < k < 128 and chr(k) in 'mvbwra+-c':
					s.act(chr(k))
			if time.monotonic() - last > 0.1:
				s.draw()
				last = time.monotonic()

def ui(scr, sk, port):
	curses.curs_set(0)
	curses.use_default_colors()
	for i, c in enumerate((curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_CYAN), 1):
		curses.init_pair(i, c, -1)
	curses.mousemask(curses.ALL_MOUSE_EVENTS)
	curses.mouseinterval(0)
	scr.nodelay(True)
	scr.keypad(True)
	Ui(scr, sk, port).run()

# test sender

def encode(text):
	lt = {c: i for i, c in enumerate(LTRS) if c != '\0'}
	fg = {c: i for i, c in enumerate(FIGS) if c != '\0'}
	out, figs = [], False
	for c in text.upper().replace('\n', '\r\n'):
		if c in ' \r\n':
			out.append(lt[c])
			if c == ' ':
				figs = False	# receiver unshifts on space
		elif c in lt:
			if figs:
				out.append(31)
				figs = False
			out.append(lt[c])
		elif c in fg:
			if not figs:
				out.append(27)
				figs = True
			out.append(fg[c])
	return out

def afsk(a):
	lv, du = [1], [BAUD / 2]	# 0.5 s idle mark
	for c in [31] * 6 + encode(a.text) + [31] * 3:
		lv += [0] + [(c >> k) & 1 for k in range(5)] + [1]
		du += [1] * 6 + [1.5]
	lv.append(1)
	du.append(BAUD / 2)
	edges = np.round(np.cumsum([0] + du) * FS / BAUD).astype(int)
	sgn = np.repeat(np.array(lv) * 2 - 1, np.diff(edges)) * (-1 if a.rev else 1)
	y = 0.3 * np.sin(np.cumsum(2 * np.pi * (a.center + sgn * SHIFT / 2) / FS))
	return noisy(y, a.snr)

def keyed(a):	# CW: dot 1.2/wpm s, 5 ms raised cosine edges
	rev = {v: k for k, v in MORSE.items() if len(v) == 1}
	lv = [0] * 7
	for c in a.text.upper():
		if c in rev:
			for e in rev[c]:
				lv += [1] * (1 if e == '.' else 3) + [0]
			lv += [0, 0]
		elif c in ' \n':
			lv += [0] * 4
	lv += [0] * 7
	k = np.repeat(lv, round(1.2 / a.cw * FS)).astype(float)
	r = int(0.005 * FS)
	k = np.convolve(k, np.hanning(2 * r) / r, 'same')
	return noisy(0.3 * k * np.sin(2 * np.pi * a.center * np.arange(len(k)) / FS), a.snr)

def noisy(y, snr):	# snr in 3 kHz, against a 0.3 peak tone
	if snr is not None:
		y = y + np.random.normal(0, np.sqrt(0.045 / 10 ** (snr / 10) * (FS / 2) / 3000), len(y))
	return (np.clip(y, -0.9, 0.9) * 32767).astype('<i2')

def send(a):
	sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	t0, n = time.monotonic(), 0
	while True:
		y = keyed(a) if a.cw else afsk(a)
		for i in range(0, len(y) - 479, 480):
			sk.sendto(y[i:i + 480].tobytes(), ('127.0.0.1', a.port))
			n += 1
			time.sleep(max(0, t0 + n * 0.01 - time.monotonic()))	# real time
		if not a.loop:
			break

def rec(port, secs):	# raw Digital out to rec.wav, for testing decoders on real signals
	sk = listen(port)
	with wave.open('rec.wav', 'wb') as w:
		w.setnchannels(1)
		w.setsampwidth(2)
		w.setframerate(FS)
		n = 0
		print(f'recording {secs:g} s from udp {port} to rec.wav', file=sys.stderr)
		while n < secs * FS:
			b = sk.recv(4096)
			w.writeframes(b)
			n += len(b) // 2
	print('done', file=sys.stderr)

def main():
	cmd = sys.argv[1] if sys.argv[1:2] and sys.argv[1] in ('rtty', 'cw', 'send', 'rec') else 'ui'
	argv = sys.argv[2:] if cmd in sys.argv[1:2] else sys.argv[1:]
	p = argparse.ArgumentParser(prog='decode.py' + ('' if cmd == 'ui' else ' ' + cmd))
	p.add_argument('-p', '--port', type=int, default=7355)
	if cmd in ('rtty', 'send'):
		p.add_argument('-r', '--rev', action='store_true', help='mark is the lower tone')
		p.add_argument('-b', '--baud', type=float, default=BAUD, help='default 45.45')
		p.add_argument('-w', '--shift', type=float, default=SHIFT, help='Hz, default 170')
	if cmd == 'rec':
		p.add_argument('secs', nargs='?', type=float, default=60)
		a = p.parse_args(argv)
		rec(a.port, a.secs)
	elif cmd == 'ui':
		a = p.parse_args(argv)
		locale.setlocale(locale.LC_ALL, '')
		curses.wrapper(ui, listen(a.port), a.port)
	elif cmd == 'send':
		p.add_argument('--cw', type=float, metavar='WPM', help='send CW instead of RTTY')
		p.add_argument('-c', '--center', type=float, help='center Hz (default 2210, CW 700)')
		p.add_argument('-n', '--snr', type=float, help='add noise, dB in 3 kHz')
		p.add_argument('-l', '--loop', action='store_true')
		p.add_argument('text', nargs='?')
		a = p.parse_args(argv)
		a.center = a.center or (700 if a.cw else 2210)
		a.text = a.text or ('CQ CQ CQ DE N0CALL N0CALL K\nUR RST 599 5NN = NAME LARRY QTH ASHEVILLE NC + 73 TU\n' if a.cw else
			'RYRYRY CQ CQ CQ DE TEST TEST K\nTHE QUICK BROWN FOX 0123456789 -?:(),./\n')
		setmode(a.baud, a.shift)
		send(a)
	else:
		p.add_argument('-c', '--center', type=float, help='Hz (default: auto)')
		sq = 10 if cmd == 'cw' else 6
		p.add_argument('-s', '--squelch', type=float, default=sq, help=f'dB, default {sq} (closes 3 dB lower)')
		a = p.parse_args(argv)
		a.kind = cmd.upper()
		plain(Cw(a.center, a.squelch) if cmd == 'cw' else Rtty(a.baud, a.shift, a.rev, a.center, a.squelch), a)

if __name__ == '__main__':
	try:
		main()
	except KeyboardInterrupt:
		print(file=sys.stderr)
