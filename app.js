'use strict';

const $ = id => document.getElementById(id);
const NDIG = 10;
const WF_GAIN = 2;		// lines arrive as 0..80; the palette tops out at 160
const PAL = (() => {
	const stops = [[0, 0, 0], [0, 0, 140], [0, 160, 220], [240, 220, 0], [255, 40, 0], [255, 255, 255]];
	return Array.from({ length: 256 }, (_, i) => {
		const x = Math.min(i / 160, 1) * (stops.length - 1), k = Math.min(Math.floor(x), stops.length - 2), f = x - k;
		return [...stops[k].map((a, j) => a + (stops[k + 1][j] - a) * f), 255];
	});
})();
const PASS = { USB: [300, 2700], LSB: [-2700, -300], AM: [-4500, 4500], FM: [-7500, 7500], WBFM: [-100000, 100000] };		// Hz rel. to dial
const signed = (v, u) => `${v > 0 ? '+' : ''}${v}${u}`;
const SLIDERS = [		// key = Control command, label, min, max, step, format, double-click value
	['v', 'Volume', 0, 2, 0.01, v => (+v).toFixed(2)],
	['if', 'IF reduction', 20, 59, 1, v => v + ' dB'],
	['lna', 'LNA', 0, 27, 1, v => v],
	['ref', 'Ref', -20, 20, 0.5, v => signed((+v).toFixed(1), ' dB'), 0],
];
const TOGGLES = [['notch', 'Notch'], ['dab', 'DAB'], ['hdr', 'HDR']];

let ws, state = {}, hz = 0, lo = 0, hi = 0, tuneTimer = null, residue = 0;
let pendingHz = null, pendingAt = 0, lastTuneSend = 0;
const timers = {}, moved = {};

//=====================================================================

function connect() {
	ws = new WebSocket(`ws://${location.host}/ws`);
	ws.binaryType = 'arraybuffer';
	ws.onopen = () => status('Connected', 'up');
	ws.onclose = () => { status('Receiver offline, retrying', 'err'); setTimeout(connect, 2000); };
	ws.onmessage = e => {
		if (e.data instanceof ArrayBuffer) return sweep(e.data);
		const m = JSON.parse(e.data);
		if (m.type === 'caps') build(m);
		else if (m.type === 'state') render(m.state);
		else if (m.type === 'notice') status(m.msg, '', 3000);
	};
}

const set = (key, value) => ws && ws.readyState === 1 && ws.send(`${key} ${value}`);		// a Control command line

function sendLater(key, value, ms = 80) {		// debounce per key
	clearTimeout(timers[key]);
	timers[key] = setTimeout(() => set(key, value), ms);
}

let statusTimer;
function status(msg, cls, revert) {
	$('status').textContent = msg;
	$('status').className = cls || '';
	clearTimeout(statusTimer);
	if (revert) statusTimer = setTimeout(() => ws.readyState === 1 ? status('Connected', 'up') : status('Receiver offline', 'err'), revert);
}

//=====================================================================

function build(caps) {
	$('rig').textContent = caps.rig;
	document.title = caps.rig;
	const f = $('freq');
	f.innerHTML = '';
	for (let i = 0; i < NDIG; i++) {
		const d = document.createElement('span');
		d.className = 'd';
		d.tabIndex = 0;
		d.onwheel = e => { e.preventDefault(); tune(i, e.deltaY < 0 ? 1 : -1); };
		d.onkeydown = e => {
			if (e.key === 'ArrowUp') { e.preventDefault(); tune(i, 1); }
			else if (e.key === 'ArrowDown') { e.preventDefault(); tune(i, -1); }
		};
		d.onclick = e => {		// top half up, bottom half down
			const r = d.getBoundingClientRect();
			tune(i, e.clientY < r.top + r.height / 2 ? 1 : -1);
		};
		f.append(d);
		if (i === 0 || i === 3 || i === 6) f.append(Object.assign(document.createElement('span'), { className: 'sep', textContent: '.' }));
	}
	f.append(Object.assign(document.createElement('span'), { className: 'unit', textContent: 'Hz' }));

	$('ctl').innerHTML =
		SLIDERS.map(([k, t, a, b, st]) => `<label>${t}<input type="range" id="k_${k}" min="${a}" max="${b}" step="${st}"><output id="o_${k}"></output></label>`).join('') +
		TOGGLES.map(([k, t]) => `<button id="k_${k}">${t}</button>`).join('');
	for (const [k, , , , , fmt, dbl] of SLIDERS) {
		const el = $('k_' + k);
		el.oninput = () => {
			moved[k] = Date.now();
			$('o_' + k).textContent = fmt(el.value);
			sendLater(k, el.value);
		};
		if (dbl != null) el.ondblclick = () => { el.value = dbl; el.oninput(); };		// back to center
	}
	for (const a of 'abc') $('ant_' + a).onclick = () => set('ant', a);
	$('mode').onclick = () => set('m', state.mode === 'LSB' ? 'usb' : 'lsb');
	for (const z of [0, 1, 2]) $('zoom_' + z).onclick = () => set('zoom', z);
	for (const [k] of TOGGLES) $('k_' + k).onclick = () => set(k, state[k] ? 0 : 1);

	$('wfc').onmousemove = e => {
		if (hi <= lo) return;
		const r = e.target.getBoundingClientRect(), f = lo + (e.clientX - r.left) / r.width * (hi - lo), off = (f - hz) / 1000;
		$('hover').textContent = `${(f / 1e6).toFixed(4)} MHz  (${off >= 0 ? '+' : ''}${off.toFixed(1)} kHz)`;
	};
	$('wfc').onmouseleave = () => $('hover').textContent = '';
	$('wfc').onclick = e => {
		if (hi <= lo) return;
		const r = e.target.getBoundingClientRect();
		const step = hi - lo <= 20000 ? 10 : 100;		// finer clicks when zoomed in
		setHz(Math.round((lo + (e.clientX - r.left) / r.width * (hi - lo)) / step) * step);
	};
}

//=====================================================================

function tune(i, dir) {
	const n = hz + dir * 10 ** (NDIG - 1 - i);
	if (state.mhz != null && n > 0 && n <= 2e9) setHz(n);		// like the TUI: a step off either end is ignored, and nothing before the first state
}

function sendTune() {
	tuneTimer = null;
	lastTuneSend = Date.now();
	set('f', (hz / 1e6).toFixed(6));
}

function setHz(n) {
	hz = Math.round(Math.max(0, Math.min(n, 2000000000)));
	pendingHz = hz;
	pendingAt = Date.now();
	drawFreq();
	if (!tuneTimer) {
		const delay = Math.max(0, 80 - (Date.now() - lastTuneSend));
		if (delay) tuneTimer = setTimeout(sendTune, delay);
		else sendTune();
	}
}

function drawFreq() {
	const s = String(hz).padStart(NDIG, '0'), first = s.search(/[1-9]/);
	document.querySelectorAll('#freq .d').forEach((d, i) => {
		d.textContent = s[i];
		d.classList.toggle('lead', first === -1 || i < first);
	});
	cursor();
}

function render(s) {
	state = { ...state, ...s };
	const remoteHz = Math.round(state.mhz * 1e6);
	if (pendingHz !== null && ((!tuneTimer && remoteHz === pendingHz) || Date.now() - pendingAt > 2000))
		pendingHz = null;
	if (pendingHz === null) { hz = remoteHz; drawFreq(); }
	$('mode').textContent = state.mode;
	$('ovl').hidden = !state.ovl;
	for (const a of 'abc') $('ant_' + a).classList.toggle('on', state.ant === a);
	for (const z of [0, 1, 2]) $('zoom_' + z).classList.toggle('on', state.zoom === z);
	for (const [k, , , , , fmt] of SLIDERS) {
		if (state[k] == null || Date.now() - (moved[k] || 0) < 1500) continue;		// don't fight the slider
		$('k_' + k).value = state[k];
		$('o_' + k).textContent = fmt(state[k]);
	}
	for (const [k] of TOGGLES) $('k_' + k).classList.toggle('on', !!state[k]);
	cursor();
}

//=====================================================================

function sweep(buf) {		// [lo u32][hi u32][points u8 ...]
	const v = new DataView(buf), pts = new Uint8Array(buf, 8);
	const c = $('wfc'), g = c.getContext('2d');
	setView(v.getUint32(0, true), v.getUint32(4, true));
	if (!pts.length) return;
	if (c.width !== pts.length) c.width = pts.length;
	g.drawImage(c, 0, 0, c.width, c.height - 1, 0, 1, c.width, c.height - 1);		// scroll down one row
	const row = g.createImageData(pts.length, 1);
	pts.forEach((p, i) => row.data.set(PAL[Math.min(p * WF_GAIN, 255)], i * 4));
	g.putImageData(row, 0, 0);
}

new ResizeObserver(() => {		// one canvas row per screen pixel, so a line is a 1 px step
	const c = $('wfc'), h = Math.max(2, c.clientHeight);
	if (c.height !== h) c.height = h;
}).observe($('wfc'));

function setView(nlo, nhi) {		// new edges; slide the history along if only the center moved
	if (nlo === lo && nhi === hi) return;
	const c = $('wfc'), g = c.getContext('2d');
	if (hi > lo && nhi - nlo === hi - lo) {
		residue += (lo - nlo) / (hi - lo) * c.width;		// canvas px, carried so small steps add up
		const dx = Math.trunc(residue);
		residue -= dx;
		if (dx) {
			g.globalCompositeOperation = 'copy';
			g.drawImage(c, dx, 0);
			g.globalCompositeOperation = 'source-over';
		}
	} else {		// new span: old lines are at the wrong scale
		residue = 0;
		g.clearRect(0, 0, c.width, c.height);
	}
	lo = nlo;
	hi = nhi;
	$('lo').textContent = (lo / 1e6).toFixed(3);
	$('hi').textContent = (hi / 1e6).toFixed(3);
	cursor();
}

function cursor() {
	const ok = hi > lo, x = (hz - lo) / (hi - lo), el = $('cursor');
	el.hidden = !(ok && x >= 0 && x <= 1);
	el.style.left = x * 100 + '%';
	const p = $('pass'), shade = $('shade'), b = PASS[state.mode];
	p.hidden = shade.hidden = !(ok && b);
	if (p.hidden) return;
	const a = Math.max(0, (hz + b[0] - lo) / (hi - lo)), z = Math.min(1, (hz + b[1] - lo) / (hi - lo));
	p.style.left = a * 100 + '%';
	p.style.width = Math.max(0, z - a) * 100 + '%';
	shade.style.setProperty('--pass-left', a * 100 + '%');
	shade.style.setProperty('--pass-right', Math.max(a, z) * 100 + '%');
}

connect();
