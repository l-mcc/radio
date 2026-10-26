#pragma once
#include <atomic>
#include <chrono>
#include <complex>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

struct spgramcf_s;
struct nco_crcf_s;
struct msresamp_crcf_s;
class Control;

class Waterfall {		// IQ -> spectrum lines -> browser page over a websocket; page commands -> Control
public:
	Waterfall(std::vector<double> rates, int port = 8700, std::string dir = ".");		// rates: span per zoom, [0] = the raw IQ rate
	~Waterfall();
	void configure(std::vector<double> rates);		// new mode: new streams; the page stays connected
	void poll(Control &c);		// main thread: runs page commands, picks up retunes and settings
	void push(const std::complex<float> *x, size_t n);		// raw IQ around the LO; drops when the worker is behind
	uint64_t dropped() const { return drops; }
private:
	struct Client { int fd; std::string buf; };
	void run();
	void emit(const float *psd, unsigned g);
	void serve();
	void receive();
	void broadcast(const std::string &frame);

	double fs;
	std::vector<double> rates;
	std::atomic<int> level{0};
	int nfft = 0;		// worker thread only
	std::string dir;
	int lsock = -1;
	std::vector<Client> clients;		// worker thread only
	std::atomic<bool> watching{false};		// any clients; lets push() skip work
	spgramcf_s *q = nullptr;
	nco_crcf_s *nco = nullptr;		// narrow views: view center to DC, then down to the span
	msresamp_crcf_s *rs = nullptr;
	std::vector<std::complex<float>> in, work, narrow;
	std::vector<float> psd;
	std::mutex m;		// guards everything below
	std::condition_variable cv;
	std::thread th;
	bool stop = false;
	double center = 0, lo = 0, ref = 0;		// view center and hardware LO, Hz
	unsigned gen = 0;		// bumped when the view or LO moves so the worker discards stale samples
	std::vector<std::string> cmds;		// page -> main thread
	std::string state, notice;		// main thread -> page
	bool state_new = false;
	uint64_t drops = 0;
	std::chrono::steady_clock::time_point born = std::chrono::steady_clock::now();
	bool looked = false;		// checked once for an open page, opened a tab if none
	std::chrono::steady_clock::time_point last_poll{};
};
