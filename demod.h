#pragma once
#include <complex>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

using IQ = std::vector<std::complex<float>>;

class Demod {		// one receive mode: everything after the API's IQ stream
public:
	virtual ~Demod() = default;
	virtual void process(const IQ &iq, std::vector<float> &audio) = 0;
	virtual bool set_mode(const std::string &) { return false; }		// switch in place (lsb <-> usb)
	virtual void tune(double if_hz) { (void)if_hz; }		// hardware LO minus dial, Hz
	double rate() const { return 2e6 / decimation; }

	int decimation = 4;		// API decimation from 2 MS/s
	double offset = 10000, audio_rate = 10000;		// offset: LO above the dial
	std::vector<double> zooms;		// waterfall spans; [0] = rate()
	const char *name = "Radio";		// PulseAudio stream name
	double channel_power = 0;		// passband power for the TUI meter; the caller reads and resets
	uint64_t channel_count = 0;
	bool tap = false;		// fill clean: the audio before volume, for decoders
	std::vector<float> clean;
	bool nb = false;		// noise blanker on the 100 kS/s IQ (SSB, AM); rx follows nb_on, nb_db
	float nb_db = 12;		// trigger: this far over the running power
	uint64_t nb_pulses = 0;		// pulses blanked; the caller reads and resets
protected:
	void finish(std::vector<float> &audio, float vol, bool limit = true);		// audio holds the pre-volume signal; copies it to clean if tapped, then applies volume
};

std::unique_ptr<Demod> make_demod(const std::string &mode, const float &vol);		// lsb, usb, am, wbfm; nullptr otherwise
