#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, json, math, re, signal, sys, time
from pathlib import Path

import consts

HERE = Path(__file__).resolve().parent

'''===================================================================================================================================================
	pvara.py: makes vara.log (xara-monitor's lines, as shack logs them) readable, one line per transmission
	pvara.py [vara.log]       whole file, then follows it like tail -f
	pvara.py -e               only what comes in from now on
	pvara.py -q               whole file, then quit (pvara.py -q > vara.txt)
	pvara.py -a               also the session bookkeeping (ACK, NACK, REQ, IDLE, ...), blind and dropped lines
==================================================================================================================================================='''

EXAMPLES = """examples:
  pvara.py                     vara.log next to pvara.py: whole file, then follow it (Ctrl-C to stop)
  pvara.py -e                  only new lines (watch while shack's VARA decoder runs)
  pvara.py -q old.log          an old log: start to end, then quit
  pvara.py -q > vara.txt       save the readable version (no colors when not a terminal)
  pvara.py -q | less           page through it (piped: no colors)
  pvara.py -a -q               everything, incl. ACK/NACK/REQ/IDLE and blind guesses
  pvara.py -g FM05ab -q        distances from another grid
"""

GRID = re.compile(r'[A-Ra-r]{2}[0-9]{2}([A-Xa-x]{2})?')

def grid_ll(g):		# 4/6-char Maidenhead -> middle lat, lon
	g = g.strip()
	if not GRID.fullmatch(g):
		return None
	g = g.upper()
	lon = (ord(g[0]) - 65) * 20 - 180 + int(g[2]) * 2
	lat = (ord(g[1]) - 65) * 10 - 90 + int(g[3])
	if len(g) == 6:
		return lat + (ord(g[5]) - 65 + 0.5) / 24, lon + (ord(g[4]) - 65 + 0.5) / 12
	return lat + 0.5, lon + 1

def path(a, b):		# great circle: miles, initial bearing
	(la1, lo1), (la2, lo2) = ((math.radians(x) for x in p) for p in (a, b))
	d = 2 * math.asin(math.sqrt(math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2))
	brg = math.degrees(math.atan2(math.sin(lo2 - lo1) * math.cos(la2), math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1))) % 360
	return d * 3958.8, brg

def home_grid():		# shack's 'My grid' (decode.json), else EM85RP
	try:
		return json.loads((HERE / 'decode.json').read_text()).get('grid', 'EM85RP')
	except (OSError, ValueError):
		return 'EM85RP'

'''===================================================================================================================================================

==================================================================================================================================================='''

class Parser:		# xara-monitor lines in, readable lines out (self.out)
	STAMP = re.compile(r'^\s*(\d\d:\d\d:\d\d)\.\d (.*)$|^\s*(\d+\.\d{3}) (.*)$')
	UNPROTO = re.compile(r'UNPROTO\s+c(\d+)\s+(.+?)\s+f(\d) (\d+) SN ([+-][\d.]+)')
	CTRL = re.compile(r'(CALLER|ANSWERER)\s+c(\d+)\s+(.+?)\s+f(\d) (\d+)/(\d+)(?: SN ([+-][\d.]+))? df ([+-][\d.]+)( \[blind\])?\s+(.*)')
	DATA = re.compile(r'DATA\s+L(\d+)\s+x(\d+) \[([+-]*)\](?: SN ([+-][\d.]+))? afc ([+-][\d.]+)\s+(.*)')
	HEADER = re.compile(r'HEADER (\S+) → (\S+): flags \S+ \((\w+), compression ([^,]+), encryption (\d+)\)')
	PAYLOAD = re.compile(r'ctl \w+ r\d h\d( z)?( last n\d+)?( full)?: (.*)')
	KISS = re.compile(r'KISS cmd (\w+) (\d+) B "(.*)"$')
	CQ = re.compile(r'CQFRAME (\S+)(?: (\d+))?')
	LINKEND = re.compile(r'link (\S+) → (\S+) ended \((.*?)\) after ([\d.]+) s: (\d+) B caller→answerer, (\d+) B answerer→caller')
	BEACON = re.compile(r'(\d{1,3})([A-Z])([A-R]{2}\d{2}[A-X]{2})([A-Z0-9/]+)\s*$')		# VarAC: band, letter (?), 6-char grid, call
	BUSY = ('ACK', 'NACK', 'REQ', 'IDLE', 'GO AHEAD', 'NOTHING TO SEND', 'START LEVEL', 'code ')		# session bookkeeping, hidden unless -a
	GAP = 30		# s of silence that ends a conversation (blank line)
	COLORS = {'BEACON': consts.KGRN, 'CQ': consts.KYEL, 'CONNECT': consts.KCYN, 'REPLY': consts.KCYN, 'LINK': consts.KCYN, 'END': consts.KCYN, 'QRT': consts.KCYN, 'LOST': consts.KRED}

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def __init__(self, out, everything=False, color=False, home='EM85RP'):
		self.out = out
		self.everything = everything
		self.color = color
		self.home = grid_ll(home)
		self.group = []		# a top-level line and its indented lines: [(stamp, text), ...]
		self.unproto = None		# an UNPROTO header waiting for its data frame: (stamp, code, name, sn)
		self.link = None		# (caller, answerer) from the last HEADER
		self.last = None		# (seconds of day, solo) of the last line printed; None = nothing to separate from
		self.cut = False		# next line starts a new conversation
		return
	# __init__

	'''-------------------------------------------------------------------
		one line from the log
	-------------------------------------------------------------------'''

	def feed(self, line):
		line = line.rstrip('\n')
		if not line.strip():
			return
		if line.startswith('---'):		# shack's dated header
			self.flush()
			self.end_unproto()
			self.out('\n' + line)
			self.last = None
			return
		m = self.STAMP.match(line)
		if not m:
			self.flush()
			self.out(line)
			self.cut = True
			return
		t, text = (m[1], m[2]) if m[1] else (m[3], m[4])
		if text.startswith('  ') and self.group:
			self.group.append((t, text.strip()))
		else:
			self.flush()
			self.group = [(t, text.strip())]
		return
	# feed

	'''-------------------------------------------------------------------
		nothing new for a while (following the file): print what's waiting
	-------------------------------------------------------------------'''

	def idle(self, secs):
		if secs > 1:
			self.flush()
		if secs > 15:
			self.end_unproto()
		return
	# idle

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def flush(self):
		if not self.group:
			return
		g, self.group = self.group, []
		t, top = g[0]
		rest = [x for _, x in g[1:]]

		m = self.UNPROTO.match(top)
		if m:
			self.end_unproto()
			self.unproto = (t, int(m[1]), m[2], m[5])
			return
		m = self.DATA.match(top)
		if m:
			if m[6] == 'sender unknown' and self.unproto:
				self.broadcast(m, rest)
			else:
				self.end_unproto()
				self.session(t, m, rest)
			return
		self.end_unproto()
		m = self.CTRL.match(top)
		if m:
			self.ctrl(t, m)
			return
		m = self.LINKEND.match(top)
		if m:
			self.say(t, 'END', f'{m[1]} → {m[2]}', f'{m[3]}, {float(m[4]):.0f} s, {m[5]}/{m[6]} B')
			self.link = None
			self.cut = True
			return
		if top.startswith('learned '):
			self.say(t, 'learned', top[8:])
			return
		if top.startswith(('blind ', 'dropped ', 'summary:')) and not self.everything:
			return
		self.say(t, '', top)		# anything else as it came
		for x in rest:
			self.say(t, '', '  ' + x)
		return
	# flush

	'''-------------------------------------------------------------------
		an UNPROTO header that got no data frame
	-------------------------------------------------------------------'''

	def end_unproto(self):
		if not self.unproto:
			return
		t, code, name, sn = self.unproto
		self.unproto = None
		kind = 'BEACON' if name == 'KISS' else name
		self.say(t, kind, '(data frame missed)', sn=sn, solo=True)
		return
	# end_unproto

	'''-------------------------------------------------------------------
		header + data frame of an unconnected transmission: beacon or CQ
	-------------------------------------------------------------------'''

	def broadcast(self, m, rest):
		t, code, name, sn = self.unproto
		self.unproto = None
		sn = f'{sn} / {m[4]}' if m[4] else sn		# header's / data frame's
		bad = '+' not in m[3]
		for x in rest:
			k = self.KISS.match(x)
			if k:
				b = self.BEACON.match(k[3])
				if b:
					call, grid = b[4], b[3][:4] + b[3][4:].lower()
					self.say(t, 'BEACON', f'{call:<10} {grid}  {b[1]}m  {b[2]}', self.far(grid), sn, True)
				else:
					self.say(t, 'KISS', f'"{k[3].rstrip()}"', sn=sn, solo=True)
				return
			c = self.CQ.match(x)
			if c:
				self.say(t, 'CQ', f'{c[1]:<10} {c[2] + " Hz" if c[2] else ""}', sn=sn, solo=True)
				return
		kind = 'BEACON' if name == 'KISS' else name
		self.say(t, kind, '(data frame failed CRC)' if bad else '(no text)', sn=sn, solo=True)
		return
	# broadcast

	'''-------------------------------------------------------------------
		data frame inside a session: header (calls) and/or payload
	-------------------------------------------------------------------'''

	def session(self, t, m, rest):
		who = m[6]
		texts, extra = [], []
		for x in rest:
			h = self.HEADER.match(x)
			if h:
				if self.link and self.link != (h[1], h[2]):
					self.cut = True		# other stations: another conversation
				self.link = (h[1], h[2])
				self.say(t, 'LINK', f'{h[1]} → {h[2]}', f'{h[3]}, compression {h[4]}' + (', encrypted' if h[5] != '0' else ''))
				continue
			p = self.PAYLOAD.match(x)
			if p:
				body = p[4]
				if 'repeat, not delivered' in body and not self.everything:
					continue
				d = re.match(r'(\d+) B "(.*)"$', body)
				texts.append(d[2] if d else body)
				if p[1]:
					extra.append('z')
				continue
			extra.append(x)
		if self.link:
			who = who.replace('caller', self.link[0]).replace('answerer', self.link[1])
		if not texts and not self.everything and '+' in m[3]:
			return		# a data frame with nothing new in it
		txt = ' '.join(f'"{s}"' for s in texts) if texts else '(nothing decoded)' if '+' not in m[3] else ''
		self.say(t, 'DATA', who, f'L{m[1]} [{m[3]}] {txt}' + (f'  {" ".join(extra)}' if extra else ''), m[4])
		return
	# session

	'''-------------------------------------------------------------------
		a control frame inside a session (connect, reply, ack, ...)
	-------------------------------------------------------------------'''

	def ctrl(self, t, m):
		name = m[3]
		if name.startswith(self.BUSY) and not self.everything:
			return
		if m[9] and not self.everything:		# [blind]: unconfirmed guess
			return
		kind = 'CONNECT' if name == 'CONNECT' else 'REPLY' if name.startswith('CONNECT REPLY') else 'QRT' if name.startswith('QRT') else name
		who = m[10] if '→' in m[10] else f'{m[10]} ({m[1].lower()})'		# 'answerer → CALL', or the sender's own name
		if self.link:
			who = who.replace('caller →', self.link[0] + ' →').replace('answerer →', self.link[1] + ' →')
		more = name[len('CONNECT REPLY '):] if kind == 'REPLY' else name if kind != name else ''
		self.say(t, kind, who, more, m[7])
		return
	# ctrl

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def far(self, grid):
		there = grid_ll(grid)
		if not (there and self.home):
			return ''
		mi, brg = path(self.home, there)
		return f'{mi:6,.0f} mi {brg:3.0f}°'
	# far

	'''-------------------------------------------------------------------
		'HH:MM:SS' (or plain seconds) -> seconds
	-------------------------------------------------------------------'''

	def secs(self, t):
		if ':' not in t:
			return float(t)
		h, m, s = t.split(':')
		return int(h) * 3600 + int(m) * 60 + int(s)
	# secs

	'''-------------------------------------------------------------------
		blank line before a line that isn't part of the last one's conversation:
		beacons and CQs stand alone; END, other calls or GAP s of silence end a session
	-------------------------------------------------------------------'''

	def space(self, t, solo):
		now = self.secs(t)
		if self.last:
			was, alone = self.last
			d = (now - was) % 86400
			if self.cut or solo or alone or min(d, 86400 - d) > self.GAP:
				self.out('')
		self.last = (now, solo)
		self.cut = False
		return
	# space

	'''-------------------------------------------------------------------
		one output line: time, kind, who/what, more, S/N
	-------------------------------------------------------------------'''

	def say(self, t, kind, what, more='', sn=None, solo=False):
		self.space(t, solo)
		s = f'{t}  {kind:<8} {what:<26} {more}'.rstrip()
		if sn:
			s = f'{s:<72} SN {sn}'
		if self.color and kind in self.COLORS:
			s = self.COLORS[kind] + s + consts.COLOR_RESET
		self.out(s)
		return
	# say

# class Parser

'''===================================================================================================================================================

==================================================================================================================================================='''

def follow(fname, p, end=False, quit=False):		# read the file, then keep reading what's added (starts over if it's truncated)
	f = open(fname, 'rb')
	if end:
		f.seek(0, 2)
	last = time.monotonic()
	part = b''		# half a line, still being written
	while True:
		line = part + f.readline()
		if line.endswith(b'\n'):
			part = b''
			p.feed(line.decode(errors='replace'))
			last = time.monotonic()
			continue
		part = line
		if quit:
			break
		p.idle(time.monotonic() - last)
		sys.stdout.flush()
		time.sleep(0.2)
		try:
			if Path(fname).stat().st_size < f.tell():
				f.close()
				f, part = open(fname, 'rb'), b''
		except FileNotFoundError:
			pass
	p.flush()
	p.end_unproto()
	f.close()
	return

'''===================================================================================================================================================

==================================================================================================================================================='''

if __name__ == '__main__':
	import logging
	import subprocess

	import utils

	try:
		app = 'pvara'
		logg = utils.setup_logging(name=app, logfile=f'{app}.log', level=logging.INFO, console=False)
		hn, cron, cwd, vscode, machine = utils.setup()

		if vscode:
			print(consts.COLOR_RESET)
			subprocess.run('clear')
			utils.set_vscode_terminal_font_size(12)
			utils.set_vscode_editor_font_size(15)
			# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
			# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
			ap = argparse.ArgumentParser(description='vara.log, one line per transmission', epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
			ap.add_argument('log', nargs='?', default=str(HERE / 'vara.log'), help='default: vara.log next to pvara.py')
			ap.add_argument('-e', '--end', action='store_true', help='start at the end: only new lines')
			ap.add_argument('-q', '--quit', action='store_true', help="don't follow: quit at the end of the file")
			ap.add_argument('-a', '--all', action='store_true', help='also ACK/NACK/REQ/IDLE, blind, dropped, summary lines')
			ap.add_argument('-g', '--grid', default=home_grid(), help="your grid for distances (default: shack's, decode.json)")
			ap.add_argument('--plain', action='store_true', help='no colors')

			tst = 2
			match tst:
				case 1:
					a = ap.parse_args([])		# pvara.py: whole vara.log, then follow it
				case 2:
					a = ap.parse_args(['-q'])		# pvara.py -q: whole vara.log, then quit
				case 3:
					a = ap.parse_args(['-q', 'old.log'])		# pvara.py -q old.log: another log, then quit
				case 4:
					a = ap.parse_args(['-a', '-q'])		# pvara.py -a -q: also the bookkeeping
				case 5:
					a = ap.parse_args(['-e'])		# pvara.py -e: only new lines
				case 6:
					a = ap.parse_args(['-g', 'FM05ab', '-q'])		# pvara.py -g FM05ab -q: distances from another grid
				case 7:
					a = ap.parse_args(['-h'])		# pvara.py -h: help, then exits
				case _:
					a = ap.parse_args()		# whatever launch.json / the command line gives
			# match tst
		else:
			ap = argparse.ArgumentParser(description='vara.log, one line per transmission', epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
			ap.add_argument('log', nargs='?', default=str(HERE / 'vara.log'), help='default: vara.log next to pvara.py')
			ap.add_argument('-e', '--end', action='store_true', help='start at the end: only new lines')
			ap.add_argument('-q', '--quit', action='store_true', help="don't follow: quit at the end of the file")
			ap.add_argument('-a', '--all', action='store_true', help='also ACK/NACK/REQ/IDLE, blind, dropped, summary lines')
			ap.add_argument('-g', '--grid', default=home_grid(), help="your grid for distances (default: shack's, decode.json)")
			ap.add_argument('--plain', action='store_true', help='no colors')
			a = ap.parse_args()
		# if vscode

		signal.signal(signal.SIGPIPE, signal.SIG_DFL)		# quiet when piped into head or less
		p = Parser(print, a.all, sys.stdout.isatty() and not a.plain, a.grid)
		follow(a.log, p, a.end, a.quit)

		pass
	except KeyboardInterrupt:
		print()
	except Exception as e:
		s = utils.mkexs(e)
		print('\n%s%s%s' % (consts.KRED, s + repr(e), consts.COLOR_RESET))
	finally:
		logging.shutdown()
		pass
