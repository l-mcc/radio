#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, json, math, os, re, shutil, signal, sqlite3, subprocess, sys, time
from email.utils import parsedate_to_datetime
from pathlib import Path

import consts

HERE = Path(__file__).resolve().parent

'''===================================================================================================================================================
	rms.py: Winlink RMS gateway list (all bands, all modes) -> rms.db (sqlite), one row per gateway channel
	rms.py              runs `pat rmslist -d` (fresh download into pat's cache), then builds rms.db next to this file
	rms.py -n           no download: use pat's cached list as it is
	rms.py -f FILE      a gateway status json file (pat's rmslist.json or the API's)
	pat keeps its copy in ~/.local/share/pat/rmslist.json and never refreshes it by itself (only with -d)
==================================================================================================================================================='''

GRID = re.compile(r'[A-Ra-r]{2}[0-9]{2}([A-Xa-x]{2})?')

BANDS = (		# kHz
	('160m', 1800, 2000), ('80m', 3500, 4000), ('60m', 5330, 5410), ('40m', 7000, 7300), ('30m', 10100, 10150), ('20m', 14000, 14350),
	('17m', 18068, 18168), ('15m', 21000, 21450), ('12m', 24890, 24990), ('10m', 28000, 29700), ('6m', 50000, 54000), ('2m', 144000, 148000),
	('1.25m', 219000, 225000), ('70cm', 420000, 450000), ('33cm', 902000, 928000), ('23cm', 1240000, 1300000),
)

SCHEMA = '''
create table channels (
	call text,		-- gateway callsign (with SSID)
	base text,		-- without SSID
	band text,
	khz real,		-- center frequency (what the gateway lists)
	dial_khz real,		-- USB dial: center - 1.5 kHz for VARA HF / ARDOP / Pactor, = center for FM and packet
	kind text,		-- VARA HF, VARA FM, ARDOP, Pactor, Packet, Robust Packet
	bw text,		-- VARA HF 500/2300/2750, VARA FM narrow/wide, ARDOP 500/1000/2000, Pactor levels, packet baud
	modes text,		-- as listed (SupportedModes)
	grid text,
	miles real,
	bearing real,
	hours text,		-- operating hours (UTC), as listed
	last_status text,		-- UTC
	hours_since integer,		-- since the gateway last reported
	antenna text,
	range text,
	service text,
	lat real,
	lon real,
	comments text,
	connect text		-- pat connect URL
);
create index channels_khz on channels (khz);
create index channels_call on channels (call);
create table meta (key text primary key, value text);
'''

'''===================================================================================================================================================

==================================================================================================================================================='''

def grid_ll(g):		# 4/6-char Maidenhead -> middle lat, lon
	g = (g or '').strip()
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

def pat_dir():		# pat's data dir (XDG)
	return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share') / 'pat'

def home_grid():		# pat's locator, else shack's 'My grid' (decode.json), else EM85RP
	for f, key in ((Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'pat/config.json', 'locator'), (HERE / 'decode.json', 'grid')):
		try:
			g = json.loads(f.read_text()).get(key, '')
			if grid_ll(g):
				return g
		except (OSError, ValueError):
			pass
	return 'EM85RP'

def cached():		# pat's newest rmslist*.json (rmslist-<codes>.json with other service codes)
	files = sorted(pat_dir().glob('rmslist*.json'), key=lambda f: f.stat().st_mtime)
	return files[-1] if files else None

def band(khz):
	for name, lo, hi in BANDS:
		if lo <= khz <= hi:
			return name
	return None

'''===================================================================================================================================================

==================================================================================================================================================='''

class Rms:		# gateway status json -> rows -> rms.db

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def __init__(self, home='EM85RP'):
		self.grid = home
		self.home = grid_ll(home)
		self.rows = []
		self.gateways = 0
		return
	# __init__

	'''-------------------------------------------------------------------
		modes text -> kind, bandwidth, dial offset (Hz), pat transport
	-------------------------------------------------------------------'''

	def kind(self, modes):
		m = modes.split()
		low = modes.lower()
		if low.startswith('vara fm'):
			return 'VARA FM', 'wide' if 'wide' in low else 'narrow', 0, 'varafm'
		if low.startswith('vara'):
			return 'VARA HF', m[1] if len(m) > 1 else '2300', 1500, 'varahf'
		if low.startswith('ardop'):
			return 'ARDOP', m[1] if len(m) > 1 else '', 1500, 'ardop'
		if low.startswith('pactor'):
			return 'Pactor', ' '.join(m[1:]), 1500, 'pactor'
		if low.startswith('robust'):
			return 'Robust Packet', '', 0, 'ax25'
		if low.startswith('packet'):
			return 'Packet', m[1] if len(m) > 1 else '', 0, 'ax25'
		return modes, '', 0, ''
	# kind

	'''-------------------------------------------------------------------
		the json: {"Gateways": [{..., "GatewayChannels": [...]}, ...]}
	-------------------------------------------------------------------'''

	def load(self, fname):
		d = json.loads(Path(fname).read_text())
		for gw in d.get('Gateways') or []:
			self.gateways += 1
			last = gw.get('LastStatus') or ''
			try:
				last = parsedate_to_datetime(last).strftime('%Y-%m-%d %H:%M')
			except (TypeError, ValueError):
				pass
			for ch in gw.get('GatewayChannels') or []:
				modes = (ch.get('SupportedModes') or '').strip()
				hz = ch.get('Frequency') or 0
				kind, bw, off, transport = self.kind(modes)
				dial = hz - off
				grid = ch.get('Gridsquare') or ''
				there = grid_ll(grid) or ((gw['Latitude'], gw['Longitude']) if gw.get('Latitude') or gw.get('Longitude') else None)
				miles, brg = path(self.home, there) if there and self.home else (None, None)
				url = f'{transport}:///{gw["Callsign"]}?freq={dial / 1000:g}' if transport else ''
				if transport == 'varahf' or (transport == 'ardop' and bw):
					url += f'&bw={bw}' + ('MAX' if transport == 'ardop' else '')
				self.rows.append((
					gw.get('Callsign'), gw.get('BaseCallsign'), band(hz / 1000), hz / 1000, dial / 1000, kind, bw, modes, grid,
					round(miles) if miles is not None else None, round(brg) if brg is not None else None,
					ch.get('OperatingHours'), last, gw.get('HoursSinceStatus'), ch.get('Antenna'), ch.get('RadioRange'), ch.get('ServiceCode'),
					gw.get('Latitude'), gw.get('Longitude'), (gw.get('Comments') or '').strip(), url,
				))
		return
	# load

	'''-------------------------------------------------------------------
		new file next to the old one, then rename (an open DB Browser keeps the old one until you reopen)
	-------------------------------------------------------------------'''

	def save(self, fname, source):
		fname = Path(fname)
		tmp = fname.with_suffix('.tmp')
		tmp.unlink(missing_ok=True)
		db = sqlite3.connect(tmp)
		db.executescript(SCHEMA)
		db.executemany(f'insert into channels values ({",".join("?" * 21)})', sorted(self.rows, key=lambda r: (r[3], r[0])))
		db.executemany('insert into meta values (?, ?)', (
			('source', str(source)),
			('source_time', time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(Path(source).stat().st_mtime))),
			('built', time.strftime('%Y-%m-%d %H:%M:%S')),
			('home_grid', self.grid),
			('gateways', str(self.gateways)),
			('channels', str(len(self.rows))),
		))
		db.commit()
		db.close()
		os.replace(tmp, fname)
		return
	# save

	'''-------------------------------------------------------------------
		counts per kind and band
	-------------------------------------------------------------------'''

	def summary(self):
		kinds = sorted({r[5] for r in self.rows})
		bands = [b for b, _, _ in BANDS if any(r[2] == b for r in self.rows)] + ([None] if any(r[2] is None for r in self.rows) else [])
		print(f'{"":<14}' + ''.join(f'{b or "other":>7}' for b in bands))
		for k in kinds:
			print(f'{k:<14}' + ''.join(f'{sum(1 for r in self.rows if r[5] == k and r[2] == b) or "":>7}' for b in bands))
		return
	# summary

# class Rms

'''===================================================================================================================================================

==================================================================================================================================================='''

if __name__ == '__main__':
	import logging

	import utils

	try:
		app = 'rms'
		logg = utils.setup_logging(name=app, logfile=f'{app}.log', level=logging.INFO, console=False)
		hn, cron, cwd, vscode, machine = utils.setup()

		if vscode:
			print(consts.COLOR_RESET)
			subprocess.run('clear')
			utils.set_vscode_terminal_font_size(12)
			utils.set_vscode_editor_font_size(15)
			# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
			# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
			ap = argparse.ArgumentParser(description='Winlink RMS list -> rms.db')
			ap.add_argument('-o', '--out', default=str(HERE / 'rms.db'))
			ap.add_argument('-n', '--no-download', action='store_true', help="use pat's cached list, don't download")
			ap.add_argument('-f', '--file', help="gateway status json (default: pat's rmslist.json)")
			ap.add_argument('-g', '--grid', default=home_grid(), help="your grid for distances (default: pat's locator)")
			a = ap.parse_args()
		else:
			ap = argparse.ArgumentParser(description='Winlink RMS list -> rms.db')
			ap.add_argument('-o', '--out', default=str(HERE / 'rms.db'))
			ap.add_argument('-n', '--no-download', action='store_true', help="use pat's cached list, don't download")
			ap.add_argument('-f', '--file', help="gateway status json (default: pat's rmslist.json)")
			ap.add_argument('-g', '--grid', default=home_grid(), help="your grid for distances (default: pat's locator)")
			a = ap.parse_args()
		# if vscode

		signal.signal(signal.SIGPIPE, signal.SIG_DFL)		# quiet when piped into head or less
		src = a.file
		if not src:
			if not a.no_download:
				pat = shutil.which('pat')
				if not pat:
					sys.exit('pat not found on the PATH (or use -n / -f FILE)')
				print('pat rmslist -d ...')
				r = subprocess.run([pat, 'rmslist', '-d'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
				if r.returncode:
					sys.exit(f'pat rmslist -d failed:\n{r.stderr.strip()}')
			src = cached()
			if not src:
				sys.exit(f'no rmslist*.json in {pat_dir()}: run without -n')
		rms = Rms(a.grid)
		rms.load(src)
		rms.save(a.out, src)
		print(f'{rms.gateways} gateways, {len(rms.rows)} channels from {src}\n-> {a.out} (distances from {a.grid})\n')
		rms.summary()

		pass
	except KeyboardInterrupt:
		print()
	except Exception as e:
		s = utils.mkexs(e)
		print('\n%s%s%s' % (consts.KRED, s + repr(e), consts.COLOR_RESET))
	finally:
		logging.shutdown()
		pass
