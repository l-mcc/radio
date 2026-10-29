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
	'nb': (0x16, 0x22), 'nr': (0x16, 0x40), 'anf': (0x16, 0x41),
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
log = logging.getLogger('civ')
SWR = [(0, 1.0), (48, 1.5), (80, 2.0), (120, 3.0)]		# 15 12 meter -> SWR, Icom's table (the X6200 assumed the same); +1 per 40 above

def swr(v):
	if v >= SWR[-1][0]:
		return SWR[-1][1] + (v - SWR[-1][0]) / 40
	(a, x), (b, y) = next((p, q) for p, q in zip(SWR, SWR[1:]) if v < q[0])
	return x + (v - a) / (b - a) * (y - x)

RIGS = {		# port: by-id pattern of the CI-V port; top: MHz; att: 11 xx value for on; agc: 16 12 values; watts: power at 00 and 255 (None = show %); swr: 15 12 meter works
	'IC-705': dict(port='*IC-705*if00', baud=115200, modes=MODE_LIST, top=470, att=0x20, agc={'Fast': 1, 'Mid': 2, 'Slow': 3}, watts=None, swr=True),
	'Xiegu x6200': dict(port='*1a86_USB_Dual_Serial*if02', baud=19200, modes=['LSB', 'USB', 'LSB-D', 'USB-D', 'AM', 'CW', 'CW-R', 'NBFM'], top=108,		# its if00 is the radio's Linux console, never CI-V
		att=0x01, agc={'Off': 0, 'Fast': 1, 'Slow': 2, 'Auto': 3}, watts=(1, 8), swr=False),		# power below 1 W reads back as 00 FA; 15 12 always 0 (firmware as of 2026-10)
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
		s.att_on = 0x20		# 11 xx for attenuator on: 705 20 (dB), X6200 01 (RIGS att)
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
		s.send(0x04)
		s.send(0x1C, 0x00)
		if b in DATA_OK:
			s.send(0x1A, 0x06)
		if b and b not in NOWIDTH:
			s.send(0x1A, 0x03)

	def read_slow(s):
		for c in [*CTL.values(), (0x11,), (0x27, 0x19, 0x00), *((0x27, sub, 0x00) for sub in SCOPE.values())]:
			s.send(*c)

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
		elif cmd == 0x11 and len(d) == 1:
			s.emit('att', int(d[0] != 0))
		elif cmd == 0x15 and len(d) == 3 and d[0] == 0x02:
			v = int(d[1:].hex())		# 0 = S0, 120 = S9, 241 = S9+60
			s.emit('smeter', round(v / 120 * 54 - 54 if v <= 120 else (v - 120) / 121 * 60))
		elif cmd == 0x15 and len(d) == 3 and d[0] == 0x12 and d[1:].hex().isdigit():
			s.emit('swr', (swr(int(d[1:].hex())), int(d[1:].hex())))		# (SWR, raw meter)
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

	def ptt(s, on):
		s.send(0x1C, 0x00, 0x01 if on else 0x00)
		s.send(0x1C, 0x00)

	def set(s, k, v):
		s.last_set = time.monotonic()
		if k == 'ptt':
			s.ptt(int(v))
		elif k == 'mhz':
			s.send(0x05, *tobcd(round(float(v) * 1e6)))
			s.send(0x03)		# read back so the UI settles immediately
		elif k == 'mode':
			base, data = v.removesuffix('-D'), v.endswith('-D')
			s.send(0x06, MODES[base], 0x01)		# filter byte required by the X6200 (it ignores 06 xx alone); FIL1
			if base in DATA_OK:
				s.send(0x1A, 0x06, *((0x01, 0x01) if data else (0x00, 0x00)))		# data mode, FIL1
			s.read_fast(base)
		elif k == 'fil':
			f = int(v)
			if s.data and s.base in DATA_OK:
				s.send(0x1A, 0x06, 0x01, f)		# in data mode the filter rides on the data command
			else:
				s.send(0x06, MODES[s.base], f)
			s.read_fast()
		elif k == 'ref':
			n = round(abs(float(v)) * 2) * 50		# 0.5 dB steps, hundredths
			s.send(0x27, 0x19, 0x00, *bytes.fromhex(f'{n:04d}'), 0x01 if float(v) < 0 else 0x00)
			s.send(0x27, 0x19, 0x00)
		elif k == 'vfo':		# no read command for the selected VFO, so the engine remembers what it set
			s.send(0x07, 0x00 if v == 'A' else 0x01)
			s.emit('vfo', 'A' if v == 'A' else 'B')
			s.read_fast('')		# the other VFO's mode isn't known yet
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

class PttServer(socketserver.ThreadingTCPServer):
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

def serve_ptt(rig, port=4532, host='127.0.0.1'):		# the bit of rigctld xara uses: T 1, T 0, t
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
					if len(c) == 2 and c[0] == 'T' and c[1] in ('0', '1'):
						keyed = c[1] == '1'
						rig.ptt(keyed)
						r = 'RPRT 0'
					elif c == ['t']:
						r = str(rig.tx)
					elif c in (['q'], ['Q']):
						break
					else:
						r = 'RPRT -4'		# not implemented
					h.wfile.write((r + '\n').encode())
			finally:
				if keyed:
					rig.ptt(False)		# client gone while keyed: never leave the 705 transmitting
				srv.conns.discard(h.connection)
				srv.clients -= 1
				if not srv.closed:		# after stop() the window may already belong to another radio
					rig.emit('ptt_clients', srv.clients)
	srv = PttServer((host, port), H)
	srv.conns = set()
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
