#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, subprocess, sys, time

import serial

import cat, civ, consts

'''===================================================================================================================================================
	setup_vara.py: puts a radio's menu settings in place for VARA (xara) over its USB audio, via its own CAT / CI-V port
	setup_vara.py ftdx10          MODE PSK/DATA menu items (DATA MOD SOURCE REAR, REAR SELECT USB, ..., RPTT SELECT DAKY) + DATA-U
	setup_vara.py ic705           DATA MOD = USB, USB SEND off (xara keys over CI-V) + mode by the dial: FM-D from 144 MHz up (VARA FM), USB-D below (VARA HF)
	setup_vara.py ic705 --fm      FM-D wherever the dial is (--hf: USB-D)
	setup_vara.py x6200           mode only (its menu isn't reachable over CI-V)
	-n                            show what's there and what would change, change nothing
	each item: what the radio has now -> what VARA wants, read back after setting
	the port must be free: close shack, openslot.sh / rigctld first
==================================================================================================================================================='''

def pick(*names):		# menu value -> its name
	return lambda n: names[n] if n < len(names) else f'? ({n})'

def num(unit=''):
	return lambda n: f'{n}{unit}'

def cut(lo):		# FTdx10 LCUT/HCUT FREQ: 00 = OFF, then 50 Hz steps from lo
	return lambda n: 'OFF' if n == 0 else f'{lo + (n - 1) * 50} Hz'

FTDX10 = (		# (menu item, EX number, digits, wanted (None = only shown), value -> text); FTdx10 CAT manual, Table 2
	('DATA MOD SOURCE', '010415', 1, 1, pick('MIC', 'REAR')),
	('REAR SELECT', '010416', 1, 1, pick('DATA', 'USB')),
	('RPORT GAIN', '010417', 3, 5, num()),
	('TX BPF SEL', '010414', 1, 0, pick('50-3050', '100-2900', '200-2800', '300-2700', '400-2600')),
	('LCUT FREQ', '010409', 2, 0, cut(100)),
	('HCUT FREQ', '010411', 2, 0, cut(700)),
	('DATA SHIFT (SSB)', '010408', 4, 1500, num(' Hz')),
	('DATA OUT LEVEL', '010413', 3, None, num()),
	('RPTT SELECT', '010418', 1, 0, pick('DAKY', 'RTS', 'DTR')),		# DAKY: only the rear DATA jack keys; RTS/DTR would let anything opening ttyUSB1 key it (we key over CAT)
	('HF MAX POWER', '030401', 3, None, num(' W')),
)

IC705 = (		# (menu item, 1A 05 number, BCD bytes, wanted, value -> text); IC-705 CI-V Reference Guide, SET > Connectors
	('DATA MOD', '0119', 1, 1, pick('MIC', 'USB', 'MIC,USB', 'WLAN')),
	('USB SEND', '0125', 1, 0, pick('OFF', 'USB (A) DTR', 'USB (A) RTS', 'USB (B) DTR', 'USB (B) RTS')),
	('USB MOD Level', '0116', 2, None, lambda n: f'{round(n / 2.55)} %'),
)

RADIOS = {		# kind: which protocol; name: key in cat.RIGS / civ.RIGS
	'ftdx10': dict(kind='cat', name='FTdx10', items=FTDX10,
		note='TX drive: xara --drive-db (openslot.sh DRIVE) and OpenSlot\'s own Tune window level; keep ALC at 0. RF power: FUNC > RF POWER.'),
	'ic705': dict(kind='civ', name='IC-705', items=IC705,
		note='USB MOD Level and xara --drive-db set the TX drive. FM-D: run xara --fm narrow (or wide), USB-D: xara --hf.'),
	'x6200': dict(kind='civ', name='Xiegu x6200', items=(),
		note='Audio input source and levels: the radio\'s own menu (not over CI-V).'),
}

ICOM_MODES = {'USB-D': 0x01, 'FM-D': 0x05}		# 06 mm; the data flag goes separately (1A 06)
FM_FROM = 144		# MHz: VARA FM from 2 m up, VARA HF below
ICOM_NAMES = {0x00: 'LSB', 0x01: 'USB', 0x02: 'AM', 0x03: 'CW', 0x04: 'RTTY', 0x05: 'FM', 0x06: 'WFM', 0x07: 'CW-R', 0x08: 'RTTY-R', 0x17: 'DV'}

'''===================================================================================================================================================
	Yaesu ASCII CAT: EX010417; -> EX010417005;  (a set gets no answer; ?; = refused)
==================================================================================================================================================='''

class Yaesu:
	'''-------------------------------------------------------------------
		RTS high: the radio holds its replies while it's low (cat.Rig does the same)
	-------------------------------------------------------------------'''

	def __init__(self, dev, baud):
		self.ser = serial.Serial(None, baud, timeout=0.1, exclusive=True)
		self.ser.port, self.ser.dtr, self.ser.rts = dev, False, True
		self.ser.open()
		time.sleep(0.3)
		self.ser.reset_input_buffer()		# leftovers from shack's last poll
		self.buf = b''
		return
	# __init__

	'''-------------------------------------------------------------------
		the text after head in the answer to cmd, '?' if refused, None if nothing came
	-------------------------------------------------------------------'''

	def ask(self, cmd, head):
		self.ser.write(f'{cmd};'.encode())
		end = time.monotonic() + 1
		while time.monotonic() < end:
			self.buf += self.ser.read(self.ser.in_waiting or 1)
			while b';' in self.buf:
				m, self.buf = self.buf.split(b';', 1)
				m = m.decode(errors='replace').strip()
				if m == '?':
					return '?'
				if m.startswith(head):
					return m[len(head):]
		return None
	# ask

	def get(self, item):		# value as int, None if unanswered
		v = self.ask(f'EX{item[1]}', f'EX{item[1]}')
		return int(v) if v and v.isdigit() else None

	def put(self, item, v):
		self.ser.write(f'EX{item[1]}{v:0{item[2]}d};'.encode())
		time.sleep(0.1)
		return

	'''-------------------------------------------------------------------
		(was, now) as mode names
	-------------------------------------------------------------------'''

	def mode(self, want, dry):
		was = cat.RMODES.get(self.ask('MD0', 'MD0'), '?')
		if dry or was == want:
			return was, was
		self.ser.write(f'MD0{cat.MODES[want]};'.encode())
		time.sleep(0.2)
		return was, cat.RMODES.get(self.ask('MD0', 'MD0'), '?')
	# mode

	def close(self):
		self.ser.close()
		return
# class Yaesu


'''===================================================================================================================================================
	Icom CI-V (the X6200 too): FE FE A4 E0 1A 05 01 19 FD -> FE FE E0 A4 1A 05 01 19 01 FD; a set -> FB (FA = NG)
==================================================================================================================================================='''

class Icom:
	'''-------------------------------------------------------------------
		DTR and RTS low: some radios key TX on RTS (civ.Rig does the same)
	-------------------------------------------------------------------'''

	def __init__(self, dev, baud, addr=0xA4):
		self.ser = serial.Serial(None, baud, timeout=0.1, exclusive=True)
		self.ser.port, self.ser.dtr, self.ser.rts = dev, False, False
		self.ser.open()
		time.sleep(0.2)
		self.ser.reset_input_buffer()
		self.addr, self.buf = addr, b''
		return
	# __init__

	'''-------------------------------------------------------------------
		sends body; the reply after head, b'' for FB, None for NG or nothing
		frames not from the radio to us (our own echo, transceive broadcasts to 00) are skipped
	-------------------------------------------------------------------'''

	def ask(self, body, head=b''):
		self.ser.write(bytes([0xFE, 0xFE, self.addr, civ.ME]) + body + b'\xfd')
		end = time.monotonic() + 1
		while time.monotonic() < end:
			self.buf += self.ser.read(self.ser.in_waiting or 1)
			while b'\xfd' in self.buf:
				f, self.buf = self.buf.split(b'\xfd', 1)
				i = f.rfind(b'\xfe\xfe')
				if i < 0 or len(f) < i + 5 or f[i + 2] != civ.ME or f[i + 3] != self.addr:
					continue
				d = f[i + 4:]
				if d == b'\xfb':
					return b''
				if d == b'\xfa':
					return None
				if head and d.startswith(head):
					return d[len(head):]
		return None
	# ask

	def get(self, item):
		head = b'\x1a\x05' + bytes.fromhex(item[1])
		v = self.ask(head, head)
		return int(v.hex()) if v and len(v) == item[2] and v.hex().isdigit() else None

	def put(self, item, v):
		self.ask(b'\x1a\x05' + bytes.fromhex(item[1]) + bytes.fromhex(f'{v:0{item[2] * 2}d}'))
		return

	'''-------------------------------------------------------------------
		(was, now): 04 = mode + filter, 1A 06 = data flag + filter; set with filter 1 (the X6200 ignores 06 mm alone)
	-------------------------------------------------------------------'''

	def name(self):
		m, d = self.ask(b'\x04', b'\x04'), self.ask(b'\x1a\x06', b'\x1a\x06')
		if not m:
			return '?'
		return ICOM_NAMES.get(m[0], f'{m[0]:02x}') + ('-D' if d and d[0] else '')
	# name

	def mhz(self):		# 03: operating frequency, 5 bytes little-endian BCD Hz; None if no answer
		f = self.ask(b'\x03', b'\x03')
		return civ.bcd(f) / 1e6 if f and len(f) == 5 else None

	def mode(self, want, dry):
		was = self.name()
		if dry or was == want:
			return was, was
		self.ask(bytes([0x06, ICOM_MODES[want], 0x01]))
		self.ask(b'\x1a\x06\x01\x01')
		time.sleep(0.2)
		return was, self.name()
	# mode

	def close(self):
		self.ser.close()
		return
# class Icom


'''-------------------------------------------------------------------
	one line per item: now -> wanted, set unless dry, read back
-------------------------------------------------------------------'''

def run(rig, items, want_mode, dry, color):
	g, y, r, n = (consts.KGRN, consts.KYEL, consts.KRED, consts.COLOR_RESET) if color else ('', '', '', '')
	bad = 0
	for item in items:
		name, _, _, want, text = item
		v = rig.get(item)
		line = f'  {name:18} {text(v) if v is not None else "?":14}'
		if v is None:
			print(f'{line}{r}no answer{n}')
			bad += 1
		elif want is None:
			print(line.rstrip())
		elif v == want:
			print(f'{line}{g}ok{n}')
		elif dry:
			print(f'{line}{y}-> {text(want)}{n}')
		else:
			rig.put(item, want)
			v = rig.get(item)
			ok = v == want
			bad += not ok
			print(f'{line}{g if ok else r}-> {text(v) if v is not None else "?"}{"" if ok else "  FAILED"}{n}')
	# for item
	was, now = rig.mode(want_mode, dry)
	line = f'  {"mode":18} {was:14}'
	if was == want_mode:
		print(f'{line}{g}ok{n}')
	elif dry:
		print(f'{line}{y}-> {want_mode}{n}')
	else:
		bad += now != want_mode
		print(f'{line}{g if now == want_mode else r}-> {now}{"" if now == want_mode else "  FAILED"}{n}')
	return bad
# run


if __name__ == '__main__':
	import logging

	import utils

	try:
		app = 'setup_vara'
		logg = utils.setup_logging(name=app, logfile=f'{app}.log', level=logging.INFO, console=False)
		hn, cron, cwd, vscode, machine = utils.setup()

		if vscode:
			print(consts.COLOR_RESET)
			subprocess.run('clear')
			utils.set_vscode_terminal_font_size(12)
			utils.set_vscode_editor_font_size(15)
			# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
			# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
			ap = argparse.ArgumentParser(description='set a radio up for VARA (xara)')
			ap.add_argument('radio', choices=RADIOS)
			m = ap.add_mutually_exclusive_group()
			m.add_argument('--fm', action='store_true', help='FM-D (VARA FM) wherever the dial is (default: by the dial, FM-D from 144 MHz up)')
			m.add_argument('--hf', action='store_true', help='USB-D (VARA HF) wherever the dial is')
			ap.add_argument('-n', '--dry-run', action='store_true', help='show only, change nothing')
			if len(sys.argv) == 1:		# VS Code's Run passes nothing: pretend these were typed
				sys.argv += ['ftdx10', '-n']
			a = ap.parse_args()
		else:
			ap = argparse.ArgumentParser(description='set a radio up for VARA (xara)')
			ap.add_argument('radio', choices=RADIOS)
			m = ap.add_mutually_exclusive_group()
			m.add_argument('--fm', action='store_true', help='FM-D (VARA FM) wherever the dial is (default: by the dial, FM-D from 144 MHz up)')
			m.add_argument('--hf', action='store_true', help='USB-D (VARA HF) wherever the dial is')
			ap.add_argument('-n', '--dry-run', action='store_true', help='show only, change nothing')
			a = ap.parse_args()
		# if vscode

		print()
		print(f'sys.argv: {sys.argv[1:]}')
		print(f'a:        {a}')
		print()

		rd = RADIOS[a.radio]
		if a.fm and a.radio != 'ic705':
			sys.exit('--fm is for the IC-705 only (the others have no 2 m)')
		mod = cat if rd['kind'] == 'cat' else civ
		dev = mod.find_dev(rd['name'])
		if not dev:
			sys.exit(f"{rd['name']} not found (/dev/serial/by-id/{mod.RIGS[rd['name']]['port']}): plugged in and on?")
		baud = mod.RIGS[rd['name']]['baud']
		try:
			rig = Yaesu(dev, baud) if rd['kind'] == 'cat' else Icom(dev, baud)
		except serial.SerialException as e:
			sys.exit(f'{dev}: {e}\nclose shack, openslot.sh / rigctld first')
		want, where = 'USB-D', ''
		if rd['kind'] == 'civ':
			mhz = rig.mhz()
			where = f'  {mhz:.4f} MHz' if mhz else '  (frequency not answered)'
			if a.fm or (not a.hf and mhz and mhz >= FM_FROM):
				want = 'FM-D'
		print(f"{rd['name']}  {dev}{where}{'  (dry run)' if a.dry_run else ''}")
		bad = run(rig, rd['items'], want, a.dry_run, sys.stdout.isatty())
		rig.close()
		print(f"\n{rd['note']}")
		if bad:
			sys.exit(f'{bad} item(s) not answered or not taken')

		pass
	except KeyboardInterrupt:
		print()
	except Exception as e:
		s = utils.mkexs(e)
		print('\n%s%s%s' % (consts.KRED, s + repr(e), consts.COLOR_RESET))
	finally:
		logging.shutdown()
		pass
