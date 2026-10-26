#include "waterfall.h"
#include "control.h"
#include <liquid/liquid.h>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <arpa/inet.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

namespace {
constexpr int bins = 1024, fps = 25, avg = 8;		// avg: FFTs per line, the rest skipped
constexpr float floor_db = -90, range_db = 60, full = 80;		// app.js doubles (WF_GAIN) and saturates at 160
const char *browser = "flatpak run io.github.ungoogled_software.ungoogled_chromium";

uint32_t rol(uint32_t x, int n) { return x << n | x >> (32 - n); }

std::string sha1(std::string s) {		// only for the websocket handshake
	uint32_t h[5] = {0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0};
	uint64_t bits = s.size() * 8;
	s += '\x80';
	while (s.size() % 64 != 56) s += '\0';
	for (int i = 7; i >= 0; i--) s += char(bits >> i * 8);
	for (size_t c = 0; c < s.size(); c += 64) {
		uint32_t w[80], a = h[0], b = h[1], x = h[2], d = h[3], e = h[4];
		for (int i = 0; i < 16; i++)
			w[i] = uint8_t(s[c + 4 * i]) << 24 | uint8_t(s[c + 4 * i + 1]) << 16 | uint8_t(s[c + 4 * i + 2]) << 8 | uint8_t(s[c + 4 * i + 3]);
		for (int i = 16; i < 80; i++) w[i] = rol(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
		for (int i = 0; i < 80; i++) {
			uint32_t f = i < 20 ? (b & x) | (~b & d) : i < 40 || i >= 60 ? b ^ x ^ d : (b & x) | (b & d) | (x & d);
			uint32_t k = i < 20 ? 0x5A827999 : i < 40 ? 0x6ED9EBA1 : i < 60 ? 0x8F1BBCDC : 0xCA62C1D6;
			uint32_t t = rol(a, 5) + f + e + k + w[i];
			e = d; d = x; x = rol(b, 30); b = a; a = t;
		}
		h[0] += a; h[1] += b; h[2] += x; h[3] += d; h[4] += e;
	}
	std::string out;
	for (uint32_t v : h) for (int i = 3; i >= 0; i--) out += char(v >> i * 8);
	return out;
}

std::string base64(const std::string &s) {
	static const char *t = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
	std::string o;
	for (size_t i = 0; i < s.size(); i += 3) {
		size_t rem = s.size() - i;
		uint32_t v = uint8_t(s[i]) << 16 | (rem > 1 ? uint8_t(s[i + 1]) << 8 : 0) | (rem > 2 ? uint8_t(s[i + 2]) : 0);
		for (size_t j = 0; j < 4; j++) o += j <= rem ? t[v >> (18 - 6 * j) & 63] : '=';
	}
	return o;
}

std::string frame(uint8_t op, const std::string &p) {		// server -> browser, unmasked
	std::string f(1, char(0x80 | op));
	if (p.size() < 126) f += char(p.size());
	else { f += char(126); f += char(p.size() >> 8); f += char(p.size() & 255); }
	return f + p;
}

bool send_all(int fd, const std::string &s) {
	return send(fd, s.data(), s.size(), MSG_NOSIGNAL) == ssize_t(s.size());
}
}

Waterfall::Waterfall(std::vector<double> rates, int port, std::string dir)
		: fs(rates.at(0)), rates(std::move(rates)), dir(std::move(dir)) {
	lsock = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
	int one = 1;
	setsockopt(lsock, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
	sockaddr_in a{};
	a.sin_family = AF_INET;
	a.sin_port = htons(port);
	if (bind(lsock, (sockaddr *)&a, sizeof a) < 0 || listen(lsock, 4) < 0) {
		fprintf(stderr, "Waterfall: port %d: %s\n", port, strerror(errno));
		close(lsock);
		lsock = -1;
	}
	nco = nco_crcf_create(LIQUID_NCO);
	th = std::thread(&Waterfall::run, this);
}

Waterfall::~Waterfall() {
	{ std::lock_guard<std::mutex> l(m); stop = true; }
	cv.notify_one();
	th.join();
	if (q) spgramcf_destroy(q);
	if (rs) msresamp_crcf_destroy(rs);
	nco_crcf_destroy(nco);
	for (auto &c : clients) close(c.fd);
	if (lsock >= 0) close(lsock);
}

void Waterfall::poll(Control &c) {
	auto now = std::chrono::steady_clock::now();
	if (now - last_poll < std::chrono::milliseconds(50)) return;
	last_poll = now;
	std::vector<std::string> todo;
	{ std::lock_guard<std::mutex> l(m); todo.swap(cmds); }
	std::string note;
	for (auto &line : todo) {
		std::string op = line.substr(0, line.find(' '));
		if (op == "f" || op == "m" || op == "ref" || op == "zoom" || op == "v" || op == "if" || op == "lna" || op == "ant" || op == "notch" || op == "dab" || op == "hdr") {		// no q, save, ...
			c.command(line);
			note = c.notice();
		}
	}
	auto s = c.state();
	std::string mode = s.mode;
	for (auto &ch : mode) ch = toupper(ch);		// app.js keys its passband table on USB/LSB/...
	char b[320];
	std::lock_guard<std::mutex> l(m);
	snprintf(b, sizeof b, "{\"type\":\"state\",\"state\":{\"mhz\":%.6f,\"mode\":\"%s\",\"v\":%.2f,\"if\":%d,\"lna\":%d,\"ant\":\"%c\","
		"\"notch\":%d,\"dab\":%d,\"hdr\":%d,\"ovl\":%d,\"ref\":%.1f,\"zoom\":%d}}", s.mhz, mode.c_str(), s.volume, s.if_reduction, s.lna,
		s.antenna, s.rf_notch, s.dab_notch, s.hdr, s.overload, s.ref, s.zoom);
	ref = s.ref;
	int z = std::clamp<int>(s.zoom, 0, rates.size() - 1);
	double rf = s.rf_mhz * 1e6, dial = s.mhz * 1e6;
	double edge = 0.4 * rates[z];		// narrow views stay put while the dial is within 40% of centre, then pan with it
	double cf = !z ? rf : z != level || center <= 0 ? dial : std::clamp(center, dial - edge, dial + edge);
	if (cf != center || z != level || rf != lo) {
		center = cf;
		level = z;
		lo = rf;
		fs = rates[z];
		in.clear();
		gen++;
	}
	if (state != b) { state = b; state_new = true; }
	if (!note.empty()) notice = "{\"type\":\"notice\",\"msg\":\"" + note + "\"}";		// Control's notices are plain text, no quotes
}

void Waterfall::configure(std::vector<double> r) {
	std::lock_guard<std::mutex> l(m);
	rates = std::move(r);
	center = -1;		// poll() recomputes center, level and fs from the next state
}

void Waterfall::push(const std::complex<float> *x, size_t n) {
	bool wake;
	{
		std::lock_guard<std::mutex> l(m);
		if (center <= 0 || !watching) return;		// nobody watching: no FFTs
		if (in.size() + n > rates[0] / 4) { drops++; return; }		// worker more than 250 ms behind
		in.insert(in.end(), x, x + n);
		wake = in.size() >= rates[0] / 50;		// batch ~20 ms per wakeup
	}
	if (wake) cv.notify_one();
}

void Waterfall::run() {
	size_t acc = 0, per_line = 0, delay = 0;
	double ratio = 1;		// narrow rate / raw rate, read under the lock
	unsigned g = ~0u;
	for (;;) {
		std::string st, note;
		{
			std::unique_lock<std::mutex> l(m);
			cv.wait_for(l, std::chrono::milliseconds(50), [&] { return stop || in.size() >= rates[0] / 50; });
			if (stop) return;
			work.swap(in);
			in.clear();
			if (g != gen) {		// retuned or zoomed: start over
				g = gen;
				nfft = level ? bins : 4 * bins;		// the wide view max-pools 4 bins per pixel
				per_line = fs / fps;
				delay = std::min<size_t>(nfft, per_line);		// narrow views overlap FFTs to keep 25 lines/s
				if (q) spgramcf_destroy(q);
				q = spgramcf_create(nfft, LIQUID_WINDOW_HANN, nfft, delay);
				psd.resize(nfft);
				acc = 0;
				if (rs) msresamp_crcf_destroy(rs);
				ratio = fs / rates[0];
				rs = level ? msresamp_crcf_create(ratio, 60) : nullptr;
				nco_crcf_set_phase(nco, 0);
				nco_crcf_set_frequency(nco, 2 * M_PI * (center - lo) / rates[0]);
			}
			if (state_new) { st = state; state_new = false; }
			note.swap(notice);
		}
		serve();
		receive();
		if (!looked && std::chrono::steady_clock::now() - born > std::chrono::seconds(3)) {		// an open tab retries every 2 s
			looked = true;
			if (clients.empty() && std::system((std::string(browser) + " http://localhost:8700/ >/dev/null 2>&1 &").c_str()) != 0)
				fprintf(stderr, "Waterfall: could not start %s\n", browser);
		}
		if (!st.empty()) broadcast(frame(1, st));
		if (!note.empty()) broadcast(frame(1, note));
		if (rs) {		// narrow view: shift its center to DC and decimate the raw IQ
			auto *x = reinterpret_cast<liquid_float_complex *>(work.data());
			nco_crcf_mix_block_down(nco, x, x, work.size());
			narrow.resize(work.size() * ratio + 64);
			unsigned ny = 0;
			msresamp_crcf_execute(rs, x, work.size(), reinterpret_cast<liquid_float_complex *>(narrow.data()), &ny);
			narrow.resize(ny);
			work.swap(narrow);
		}
		for (size_t i = 0; i < work.size(); ) {
			size_t k = std::min(work.size() - i, per_line - acc), keep = avg * delay;
			if (acc < keep)
				spgramcf_write(q, work.data() + i, std::min(k, keep - acc));
			i += k; acc += k;
			if (acc >= per_line) {
				spgramcf_get_psd(q, psd.data());		// dB, DC centered
				spgramcf_clear(q);
				acc = 0;
				emit(psd.data(), g);
			}
		}
	}
}

void Waterfall::emit(const float *p, unsigned g) {
	uint32_t left, right;
	float fl;
	{
		std::lock_guard<std::mutex> l(m);
		if (g != gen) return;		// the view moved while this line was being built
		left = uint32_t(center - fs / 2);
		right = uint32_t(center + fs / 2);
		fl = floor_db + ref - 10 * std::log10(rates[0] / fs);		// narrower stream, less noise per bin: keep the floor put
	}
	std::string pkt(8 + bins, '\0');		// [lo u32][hi u32][0..80 per bin]
	std::memcpy(&pkt[0], &left, 4);
	std::memcpy(&pkt[4], &right, 4);
	int pool = nfft / bins;
	for (int b = 0; b < bins; b++) {
		float v = *std::max_element(p + b * pool, p + (b + 1) * pool);
		pkt[8 + b] = char(std::clamp<float>(std::lround((v - fl) * full / range_db), 0, full));
	}
	broadcast(frame(2, pkt));
}

void Waterfall::broadcast(const std::string &f) {
	clients.erase(std::remove_if(clients.begin(), clients.end(), [&](Client &c) {
		if (send_all(c.fd, f)) return false;
		close(c.fd);
		return true;
	}), clients.end());
	watching = !clients.empty();
}

void Waterfall::serve() {		// new connections: a file from dir, or the /ws upgrade
	if (lsock < 0) return;
	int fd;
	while ((fd = accept4(lsock, nullptr, nullptr, SOCK_CLOEXEC)) >= 0) {
		timeval tv{0, 200000};
		setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
		setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
		char buf[4096];
		ssize_t n = recv(fd, buf, sizeof buf - 1, 0);
		std::string req(buf, std::max<ssize_t>(n, 0)), path;
		std::istringstream(req) >> path >> path;		// "GET /path HTTP/1.1"
		size_t k = req.find("Sec-WebSocket-Key:");
		if (path == "/ws" && k != std::string::npos) {
			k += 18;
			std::string key = req.substr(k, req.find("\r\n", k) - k);
			key.erase(0, key.find_first_not_of(' '));
			send_all(fd, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: "
				+ base64(sha1(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")) + "\r\n\r\n");
			std::string st;
			{ std::lock_guard<std::mutex> l(m); st = state; }
			send_all(fd, frame(1, "{\"type\":\"caps\",\"rig\":\"RSPdx\"}"));
			if (!st.empty()) send_all(fd, frame(1, st));
			clients.push_back({fd, {}});
			watching = true;
			continue;
		}
		std::string name = path == "/" ? "index.html" : path.substr(std::min<size_t>(1, path.size()));
		std::ifstream f;
		if (name.find('/') == std::string::npos && name.find("..") == std::string::npos)		// plain files in dir only
			f.open(dir + "/" + name, std::ios::binary);
		if (!f.is_open()) {
			std::string msg = name + " not found in " + dir + "\n";
			send_all(fd, "HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\nContent-Length: " + std::to_string(msg.size()) + "\r\nConnection: close\r\n\r\n" + msg);
		} else {
			std::string body((std::istreambuf_iterator<char>(f)), {});
			auto ends = [&](const char *x) { size_t n = strlen(x); return name.size() >= n && name.compare(name.size() - n, n, x) == 0; };
			send_all(fd, "HTTP/1.1 200 OK\r\nContent-Type: " + std::string(ends(".js") ? "text/javascript" : ends(".css") ? "text/css" : ends(".html") ? "text/html" : "application/octet-stream")
				+ "\r\nCache-Control: no-cache\r\nContent-Length: " + std::to_string(body.size()) + "\r\nConnection: close\r\n\r\n" + body);
		}
		close(fd);
	}
}

void Waterfall::receive() {		// page -> cmds; browsers always mask and never fragment short text
	clients.erase(std::remove_if(clients.begin(), clients.end(), [&](Client &c) {
		char tmp[1024];
		ssize_t n;
		while ((n = recv(c.fd, tmp, sizeof tmp, MSG_DONTWAIT)) > 0) c.buf.append(tmp, n);
		bool ok = n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) && c.buf.size() < 65536;
		while (ok && c.buf.size() >= 2) {
			const std::string &b = c.buf;
			int op = b[0] & 15;
			size_t len = b[1] & 127, h = 2;
			if (len == 126) {
				if (b.size() < 4) break;
				len = uint8_t(b[2]) << 8 | uint8_t(b[3]);
				h = 4;
			}
			if (len == 127 || !(b[1] & 0x80)) { ok = false; break; }
			if (b.size() < h + 4 + len) break;
			std::string p = b.substr(h + 4, len);
			for (size_t i = 0; i < len; i++) p[i] ^= b[h + i % 4];
			c.buf.erase(0, h + 4 + len);
			if (op == 8) ok = false;
			else if (op == 1) { std::lock_guard<std::mutex> l(m); cmds.push_back(p); }
		}
		if (!ok) close(c.fd);
		return !ok;
	}), clients.end());
	watching = !clients.empty();
}
