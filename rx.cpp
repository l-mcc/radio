#include "rsp.h"
#include "control.h"
#include "audio.h"
#include "waterfall.h"
#include "demod.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <csignal>
#include <ctime>
#include <memory>
#include <thread>
#include <unistd.h>
#include <sys/resource.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <liquid/liquid.h>
#include <vector>

namespace {
class DigiOut {		// decoder feed: 48 kHz s16le mono over UDP to 127.0.0.1:7355, gqrx's format (direwolf reads udp:7355)
	public:
		explicit DigiOut(double rate) : ratio(48000.0 / rate), rs(msresamp_rrrf_create(ratio, 60.0f)) {
			fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
			to.sin_family = AF_INET;
			to.sin_port = htons(7355);
			to.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
		}
		~DigiOut() {
			msresamp_rrrf_destroy(rs);
			if (fd >= 0) close(fd);
		}
		void write(std::vector<float> &x) {
			if (x.empty() || fd < 0) return;
			out.resize(static_cast<size_t>(x.size() * ratio) + 64);
			unsigned int n = 0;
			msresamp_rrrf_execute(rs, x.data(), x.size(), out.data(), &n);
			for (unsigned int i = 0; i < n; ++i) pcm.push_back(static_cast<int16_t>(std::lround(std::clamp(out[i], -1.0f, 1.0f) * 32767)));
			size_t sent = 0;
			for (; pcm.size() - sent >= packet; sent += packet)		// nobody listening is fine: the datagrams just drop
				sendto(fd, pcm.data() + sent, packet * 2, MSG_DONTWAIT, reinterpret_cast<sockaddr *>(&to), sizeof to);
			pcm.erase(pcm.begin(), pcm.begin() + sent);
		}

	private:
		static constexpr size_t packet = 480;		// 10 ms per datagram
		double ratio;
		msresamp_rrrf rs;
		int fd = -1;
		sockaddr_in to{};
		std::vector<float> out;
		std::vector<int16_t> pcm;
};		// class DigiOut

// ---------------------------------------------------------
//
// ---------------------------------------------------------

std::string state_json(const Control::State &s) {		// rx -n stdout line; same fields as the waterfall page gets, plus ovln, nb, digi, bw and wf
	std::string mode = s.mode;
	for (auto &ch : mode) ch = toupper(ch);
	char b[400];
	snprintf(b, sizeof b, "{\"type\":\"state\",\"state\":{\"mhz\":%.6f,\"mode\":\"%s\",\"v\":%.2f,\"if\":%d,\"lna\":%d,\"ant\":\"%c\","
		"\"notch\":%d,\"dab\":%d,\"hdr\":%d,\"ovl\":%d,\"ovln\":%llu,\"ref\":%.1f,\"zoom\":%d,\"nb\":%d,\"digi\":%d,\"bw\":%d,\"wf\":%d}}", s.mhz, mode.c_str(),
		s.volume, s.if_reduction, s.lna, s.antenna, s.rf_notch, s.dab_notch, s.hdr, s.overload, static_cast<unsigned long long>(s.overload_count),
		s.ref, s.zoom, nb_on ? nb_db : 0, int(digi_on), ssb_bw_index, int(wf_on));		// ovln: overloads since start, so short ones aren't missed
	return b;
}

// ---------------------------------------------------------
//
// ---------------------------------------------------------

std::string json_text(const std::string &t) {		// a quoted JSON string; bookmark names are user text
	std::string o = "\"";
	for (unsigned char c : t)
		if (c == '"' || c == '\\') o += '\\', o += c;
		else if (c < 0x20) { char b[8]; snprintf(b, sizeof b, "\\u%04x", c); o += b; }
		else o += c;
	return o + '"';
}

// ---------------------------------------------------------
//
// ---------------------------------------------------------

std::string bookmarks_json(Control &c) {		// rx -n: the whole list, most picked first; an empty name gets its band label
	std::string o = "{\"type\":\"bookmarks\",\"list\":[";
	for (auto &b : c.bookmarks()) {
		char f[96];
		snprintf(f, sizeof f, "{\"hz\":%lld,\"mode\":\"%s\",\"hits\":%d,\"name\":", static_cast<long long>(b.hz), b.mode.c_str(), b.hits);
		o += (o.back() == '[' ? "" : ",") + std::string(f) + json_text(b.name.empty() ? band_name(b.hz / 1e6) : b.name) + "}";
	}
	return o + "]}";
}

// ---------------------------------------------------------
//
// ---------------------------------------------------------

double thread_cpu_seconds() {
	timespec ts;
	clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts);
	return ts.tv_sec + ts.tv_nsec * 1e-9;
}
}

volatile std::sig_atomic_t running = 1;
volatile std::sig_atomic_t stopping = 0;

void signal_handler(int) {
	if (stopping) _exit(130);
	running = 0;
}

// ---------------------------------------------------------
//
// ---------------------------------------------------------

int main(int argc, char** argv) {
	auto wall_start = std::chrono::steady_clock::now();
	bool want_wf = false, headless = false;
	for (int i = 1; i < argc; )		// -w: start with the waterfall on; -n: driven by shack.py (stdin commands, stdout JSON)
		if (std::strcmp(argv[i], "-w") == 0 || std::strcmp(argv[i], "-n") == 0) {
			(argv[i][1] == 'w' ? want_wf : headless) = true;
			std::copy(argv + i + 1, argv + argc, argv + i);
			argv[--argc] = nullptr;
		} else ++i;
	bool raw = argc > 2 && std::strcmp(argv[2], "raw") == 0;
	double mhz = argc > 1 ? std::atof(argv[1]) : 7.175;
	std::string mode = argc > 2 && !raw ? argv[2] : "";		// empty: Control picks (last save, saved at mhz, band plan)
	float vol = 0.10f;
	if (argc > 3 || (headless && raw) || (!mode.empty() && !make_demod(mode, vol))) {
		fprintf(stderr, "Usage: rx [-w] [-n] [frequency_MHz] [lsb|usb|am|wbfm|raw]   (-n and raw both want stdout)\n");
		return 1;
	}

	std::signal(SIGINT, signal_handler);
	std::signal(SIGTERM, signal_handler);		// shack.py stopping rx: same clean shutdown as Ctrl-C
	if (headless) std::signal(SIGPIPE, SIG_IGN);		// shack.py gone: writes fail, stdin hits EOF, rx quits

	RSP rsp(mhz * 1e6 + 10000);
	if (!rsp.open()) return 1;
	rsp.set_stdout(raw);
	IQQueue queue;
	rsp.set_iq_callback(iq_enqueue, &queue);
	rsp.set_antenna('a');
	rsp.set_gain(50, 5);

	Control control(rsp, vol, mode, mhz * 1e6, 10000);
	if (!control.open("", argc < 2)) return 1;		// no arguments: restore the last s/save, mode included
	if (want_wf) wf_on = true;
	mode = control.state().mode;
	auto demod = make_demod(mode, vol);
	if (!demod) {
		fprintf(stderr, "No demodulator for mode %s\n", mode.c_str());
		return 1;
	}
	control.set_offset(demod->offset);
	demod->tune((control.state().rf_mhz - control.state().mhz) * 1e6);
	rsp.set_decimation(demod->decimation);
	if (!raw) fprintf(stderr, "Streaming %s %.6f MHz at %.0f kS/s\n", mode.c_str(), control.state().mhz, demod->rate() / 1e3);

	auto speaker = std::make_unique<Audio>(static_cast<unsigned int>(demod->audio_rate), demod->name);
	if (!speaker->open()) return 1;
	std::unique_ptr<Waterfall> wf;		// created and dropped to follow wf_on (-w, wf command, saved stations); serves index.html, app.js from the current directory

	if (!rsp.start()) return 1;

	if (headless) control.set_ui_active(true);		// shack.py gets notices on stdout, no per-command chatter on stderr
	std::string last_state;
	auto last_out = std::chrono::steady_clock::now();
	auto last_meter = std::chrono::steady_clock::now();
	auto last_report = last_meter;
	IQ iq;
	double last_if_hz = (control.state().rf_mhz - control.state().mhz) * 1e6;
	std::vector<float> audio;
	uint64_t consumed = 0;
	double signal_seconds = 0, dsp_seconds = 0, meter_power = 0;
	uint64_t meter_count = 0;

	std::unique_ptr<DigiOut> digi;		// follows digi_on (d key, :digi); not saved
	auto process = [&](const IQ &block) {
		double t0 = thread_cpu_seconds();
		for (const auto &sample : block) meter_power += 0.5 * std::norm(sample);
		meter_count += block.size();
		demod->process(block, audio);
		dsp_seconds += thread_cpu_seconds() - t0;
		if (digi) digi->write(demod->clean);
		consumed += block.size();
		signal_seconds += block.size() / demod->rate();
		speaker->write(audio);
	};

	while (running) {
		if (!control.poll()) break;
		if (headless && control.stdin_closed()) break;		// shack.py closed the pipe or died
		auto s = control.state();
		if (s.mode != mode) {
			if (!demod->set_mode(s.mode)) {		// lsb <-> usb happens in place; anything else gets a new chain
				auto next = make_demod(s.mode, vol);
				if (next->decimation != demod->decimation) rsp.set_decimation(next->decimation);
				demod = std::move(next);
				control.set_offset(demod->offset);
				last_if_hz = 1e99;
				speaker = std::make_unique<Audio>(static_cast<unsigned int>(demod->audio_rate), demod->name);
				speaker->open();
				while (queue.pop(iq)) {}		// old rate, old LO
				if (wf) wf->configure(demod->zooms);
				if (digi) digi = std::make_unique<DigiOut>(demod->audio_rate);		// new audio rate
			}
			mode = s.mode;
			s = control.state();
		}
		double if_hz = (s.rf_mhz - s.mhz) * 1e6;
		if (std::abs(if_hz - last_if_hz) >= 0.5) {
			demod->tune(if_hz);
			last_if_hz = if_hz;
		}
		if (queue.pop(iq)) {
			process(iq);
			if (wf) wf->push(iq.data(), iq.size());		// outside the DSP timing so the figure stays comparable
		} else {
			std::this_thread::sleep_for(std::chrono::milliseconds(1));
		}

		auto now = std::chrono::steady_clock::now();
		if (digi_on != bool(digi)) digi = digi_on ? std::make_unique<DigiOut>(demod->audio_rate) : nullptr;
		demod->tap = bool(digi);
		demod->nb = nb_on;
		demod->nb_db = nb_db;

		// ----------------------------------------
		// sends rspdx data to stdout every 50 ms
		// data consummed by python Rx() class
		// ----------------------------------------

		if (headless && now - last_out >= std::chrono::milliseconds(50)) {
			auto st = state_json(control.state()), note = control.take_notice();
			if (st != last_state) printf("%s\n", (last_state = st).c_str());
			if (!note.empty()) printf("{\"type\":\"notice\",\"msg\":%s}\n", json_text(note).c_str());
			if (control.take_list_request()) printf("%s\n", bookmarks_json(control).c_str());
			fflush(stdout);
			last_out = now;
		}
		if (wf_on != bool(wf)) wf = wf_on ? std::make_unique<Waterfall>(demod->zooms) : nullptr;
		if (wf) wf->poll(control);		// page commands, retunes
		if (now - last_meter >= std::chrono::milliseconds(250)) {
			if (headless && meter_count) {		// ADC (broadband) and signal (passband) in dBFS, blanker pulses/s
				printf("{\"type\":\"meter\",\"adc\":%.1f,\"sig\":%.1f,\"nbr\":%.1f}\n", 10.0 * std::log10(std::max(1e-12, meter_power / meter_count)),
					demod->channel_count ? 10.0 * std::log10(std::max(1e-15, demod->channel_power / demod->channel_count)) : -150.0,
					demod->nb_pulses / std::chrono::duration<double>(now - last_meter).count());
				fflush(stdout);
			}
			demod->nb_pulses = 0;
			meter_power = demod->channel_power = 0;
			meter_count = demod->channel_count = 0;
			last_meter = now;
		}
		if (now - last_report >= std::chrono::seconds(5)) {
			auto qs = queue.stats(true);
			if (!headless)
				fprintf(stderr, "Queue: depth=%zu peak=%zu dropped=%llu\n", qs.depth, qs.peak, (unsigned long long)qs.dropped);
			last_report = now;
		}
	}		// while(running)



	double audio_latency = speaker->latency_ms();
	stopping = 1;
	rsp.stop();
	while (queue.pop(iq)) process(iq);
	speaker->flush();
	speaker->close();

	auto qs = queue.stats();
	fprintf(stderr, "Queue: pushed=%llu popped=%llu dropped=%llu peak=%zu depth=%zu\n",
		(unsigned long long)qs.pushed, (unsigned long long)qs.popped, (unsigned long long)qs.dropped, qs.peak, qs.depth);
	fprintf(stderr, "Consumed samples: %llu\n", static_cast<unsigned long long>(consumed));
	if (wf) fprintf(stderr, "Waterfall dropped pushes: %llu\n", static_cast<unsigned long long>(wf->dropped()));
	double wall = std::chrono::duration<double>(std::chrono::steady_clock::now() - wall_start).count();
	rusage ru;
	getrusage(RUSAGE_SELF, &ru);
	double proc_cpu = ru.ru_utime.tv_sec + ru.ru_utime.tv_usec * 1e-6 + ru.ru_stime.tv_sec + ru.ru_stime.tv_usec * 1e-6;
	fprintf(stderr, "Audio latency: %.0f ms\n", audio_latency);
	fprintf(stderr, "Wall time: %.1f s\n", wall);
	fprintf(stderr, "Process CPU: %.1f%%\n", 100.0 * proc_cpu / wall);		// all threads, incl. RSP driver
	fprintf(stderr, "Main thread CPU: %.1f%%\n", 100.0 * thread_cpu_seconds() / wall);
	if (consumed)
		fprintf(stderr, "DSP: %.2f s CPU, %.2f%% of real time, %.1f ns/sample\n",
			dsp_seconds, 100.0 * dsp_seconds / signal_seconds, 1e9 * dsp_seconds / consumed);
	return 0;
}
