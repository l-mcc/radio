#pragma once
#include <algorithm>
#include <cmath>
#include <complex>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

using IQ = std::vector<std::complex<float>>;
class Denoise;		// demod.cpp: RNNoise

class Limiter {		// the decoder feed's ceiling: turns the gain down ahead of a peak (2 ms look-ahead) instead of clipping it, so loud peaks don't splatter; back up over 50 ms
public:
	void run(const std::vector<float> &in, std::vector<float> &out, double rate) {
		size_t n = std::max<size_t>(1, static_cast<size_t>(std::lround(rate * 0.002)));
		if (x.size() != n) x.assign(n, 0.0f), need.assign(n, 1.0f), pos = 0, attack = 1.0f - std::exp(-5.0f / n), release = 1.0f - std::exp(-1.0f / static_cast<float>(rate * 0.05));
		out.resize(in.size());
		for (size_t i = 0; i < in.size(); ++i) {
			float v = in[i], a = std::fabs(v);
			float late = x[pos];		// n samples old: the gain has had n samples to get down for it
			x[pos] = v;
			need[pos] = a > ceiling ? ceiling / a : 1.0f;
			pos = (pos + 1) % n;
			float want = *std::min_element(need.begin(), need.end());
			gain += (want < gain ? attack : release) * (want - gain);
			out[i] = std::clamp(late * gain, -ceiling, ceiling);		// the clamp only catches the last fraction of a percent
		}
	}
private:
	static constexpr float ceiling = 0.9f;
	std::vector<float> x, need;		// the delay line, and the gain each sample in it needs
	size_t pos = 0;
	float gain = 1.0f, attack = 1.0f, release = 1.0f;
};		// class Limiter

class Demod {		// one receive mode: everything after the API's IQ stream
public:
	virtual ~Demod();
	virtual void process(const IQ &iq, std::vector<float> &audio) = 0;
	virtual bool set_mode(const std::string &) { return false; }		// switch in place (lsb <-> usb)
	virtual void tune(double if_hz) { (void)if_hz; }		// hardware LO minus dial, Hz
	double rate() const { return 2e6 / decimation; }

	int decimation = 4;		// API decimation from 2 MS/s
	double offset = 10000, audio_rate = 10000;		// offset: LO above the dial
	std::vector<double> zooms;		// waterfall spans; [0] = rate()
	const char *name = "Radio";		// PulseAudio stream name
	double channel_power = 0;		// passband power for the signal meter (every mode); the caller reads and resets
	uint64_t channel_count = 0;
	bool tap = false;		// fill clean: the audio before volume, for decoders
	std::vector<float> clean;
	bool nb = false;		// noise blanker on the 100 kS/s IQ (SSB, AM); rspdx follows nb_on, nb_db
	float nb_db = 12;		// trigger: this far over the running power
	uint64_t nb_pulses = 0;		// pulses blanked; the caller reads and resets
	bool nr = false;		// RNNoise on the audio after the decoder tap; rspdx follows nr_on
protected:
	void finish(std::vector<float> &audio, float vol, bool limit = true, bool denoise = true);		// audio holds the pre-volume signal; copies it to clean if tapped, denoises if nr (and the mode allows it), then applies volume
	std::unique_ptr<Denoise> den;		// made when nr goes on, dropped when it goes off
	Limiter lim;		// on clean
};

std::unique_ptr<Demod> make_demod(const std::string &mode, const float &vol);		// lsb, usb, am, wbfm; nullptr otherwise
