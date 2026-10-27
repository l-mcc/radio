#include "demod.h"
#include <liquid/liquid.h>
#include <algorithm>
#include <array>
#include <cmath>

/* ==================================================================================
	demodulaors implemented here
================================================================================== */


extern int ssb_bw_low, ssb_bw_high;		// control.cpp: the bandwidth preset (bw command)

namespace {
constexpr float pi = 3.14159265358979323846f;

double dc_gain(unsigned int n, float fc) {		// liquid's kaiser taps aren't normalized
	std::vector<float> h(n);
	liquid_firdes_kaiser(n, fc, 60.0f, 0.0f, h.data());
	double sum = 0.0;
	for (float v : h) sum += v;
	return sum;
}

firdecim_crcf decimator(unsigned int m) {		// kaiser, unity DC gain
	firdecim_crcf f = firdecim_crcf_create_kaiser(m, 8, 60.0f);
	firdecim_crcf_set_scale(f, static_cast<float>(1.0 / dc_gain(2 * m * 8 + 1, 0.5f / m)));
	return f;
}

void decimate(firdecim_crcf f, unsigned int m, IQ &pending, IQ &out) {		// whole groups of m; the remainder waits
	size_t groups = pending.size() / m;
	out.resize(groups);
	if (!groups) return;
	firdecim_crcf_execute_block(f, reinterpret_cast<liquid_float_complex*>(pending.data()), groups,
		reinterpret_cast<liquid_float_complex*>(out.data()));
	pending.erase(pending.begin(), pending.begin() + groups * m);
}

class Narrow : public Demod {		// shared front end for SSB and AM: 500 kS/s -> 100 kS/s, dial to DC, -> final rate
public:
	Narrow(unsigned int decim2, const float &vol) : vol(vol), decim2(decim2) {
		decimation = 4;
		offset = 10000;
		audio_rate = 100000.0 / decim2;
		zooms = {500e3, 100e3, audio_rate};
		fir1 = decimator(5);
		fir2 = decimator(decim2);
		nco = nco_crcf_create(LIQUID_NCO);
		nco_crcf_set_frequency(nco, 2.0f * pi * offset / 500000.0f);
	}
	~Narrow() override {
		firdecim_crcf_destroy(fir1);
		firdecim_crcf_destroy(fir2);
		nco_crcf_destroy(nco);
	}
	void tune(double if_hz) override { nco_crcf_set_frequency(nco, 2.0f * pi * if_hz / 500000.0f); }
protected:
	void front(const IQ &iq) {		// leaves this block's 100k and final-rate streams in iq1, iq2
		pending.insert(pending.end(), iq.begin(), iq.end());
		auto *added = reinterpret_cast<liquid_float_complex*>(pending.data() + pending.size() - iq.size());
		nco_crcf_mix_block_up(nco, added, added, iq.size());
		decimate(fir1, 5, pending, iq1);
		if (nb) blank(iq1);		// before the channel filter and AGC, so a click can't pull the gain down
		pending2.insert(pending2.end(), iq1.begin(), iq1.end());
		decimate(fir2, decim2, pending2, iq2);
	}
	const float &vol;
	unsigned int decim2;
	firdecim_crcf fir1, fir2;
	nco_crcf nco;
	IQ pending, pending2, iq1, iq2;
private:
	void blank(IQ &x) {		// impulse blanker: a sample nb_db over the running power zeroes 160 us before it to 200 us after
		float threshold = std::pow(10.0f, nb_db / 10);
		for (auto &v : x) {
			float p = std::norm(v);
			if (nb_avg <= 0) nb_avg = p;
			if (p > threshold * nb_avg) {
				if (!nb_zero) ++nb_pulses;		// a hit inside the current blank is the same pulse
				nb_zero = nb_delay + nb_post + 1;
				++nb_run;
				if (nb_run > nb_max) nb_avg = p, nb_run = 0;		// too long for a click: a signal arrived, take its level
			} else {
				nb_run = 0;
				nb_avg += nb_alpha * (p - nb_avg);
			}
			std::complex<float> out = nb_line[nb_pos];		// nb_delay samples late, so the blank can start before the hit
			nb_line[nb_pos] = v;
			nb_pos = (nb_pos + 1) % nb_delay;
			v = nb_zero > 0 ? std::complex<float>() : out;
			if (nb_zero > 0) --nb_zero;
		}
	}
	static constexpr int nb_delay = 16, nb_post = 20, nb_max = 300;		// 100 kS/s: 160 us, 200 us, 3 ms
	const float nb_alpha = 1.0f - std::exp(-1.0f / (100000.0f * 0.01f));		// 10 ms running power
	std::array<std::complex<float>, nb_delay> nb_line{};
	int nb_pos = 0, nb_zero = 0, nb_run = 0;
	float nb_avg = 0;
};

class Ssb : public Narrow {
public:
	Ssb(bool usb, const float &vol) : Narrow(10, vol), usb(usb) {
		name = "SSB Radio";
		design();
	}
	~Ssb() override { firfilt_cccf_destroy(voice); }
	bool set_mode(const std::string &m) override {
		if (m != "lsb" && m != "usb") return false;
		usb = m == "usb";		// sideband flip: same filter, mirrored
		design();
		return true;
	}
	void process(const IQ &iq, std::vector<float> &audio) override {
		if (ssb_bw_low != low || ssb_bw_high != high) design();
		front(iq);
		audio.resize(iq2.size());
		for (size_t i = 0; i < iq2.size(); ++i) {
			firfilt_cccf_push(voice, static_cast<liquid_float_complex>(iq2[i]));
			liquid_float_complex y;
			firfilt_cccf_execute(voice, &y);
			audio[i] = std::real(y);
			channel_power += 0.5 * std::norm(y);
		}
		channel_count += iq2.size();
		for (float &sample : audio) {
			float hp = hp_alpha * (hp_output + sample - hp_previous);
			hp_previous = sample;
			hp_output = hp;
			float mag = std::fabs(hp);
			if (mag > agc_env) {
				agc_env += agc_attack * (mag - agc_env);
				agc_hang = agc_hang_samples;
			} else if (agc_hang > 0) {
				--agc_hang;
			} else {
				agc_env += agc_release * (mag - agc_env);
			}
			float gain = std::clamp(agc_target / std::max(agc_env, 1e-9f), agc_min_gain, agc_max_gain);
			sample = hp * gain;
		}
		finish(audio, vol);
	}
private:
	void design() {		// one-sided bandpass, low..high Hz, on the chosen side of DC
		low = ssb_bw_low;
		high = ssb_bw_high;
		std::vector<float> lp(taps);
		liquid_firdes_kaiser(taps, 0.5f * (high - low) / audio_rate, 60.0f, 0.0f, lp.data());
		float lp_sum = 0.0f;
		for (float v : lp) lp_sum += v;
		std::vector<std::complex<float>> bp(taps);
		float f0 = (usb ? 0.5f : -0.5f) * (high + low) / audio_rate;
		for (size_t n = 0; n < bp.size(); ++n)
			bp[n] = lp[n] / lp_sum * std::polar(1.0f, 2.0f * pi * f0 * (n - (taps - 1) / 2.0f));
		auto *h = reinterpret_cast<liquid_float_complex*>(bp.data());
		voice = voice ? firfilt_cccf_recreate(voice, h, bp.size()) : firfilt_cccf_create(h, bp.size());
	}

	static constexpr size_t taps = 121;
	static constexpr float agc_target = 0.5f;		// output peak level before volume
	static constexpr float agc_min_gain = 0.1f;
	static constexpr float agc_max_gain = 500.0f;		// caps how far noise is raised between signals
	static constexpr float hp_alpha = 0.95213f;		// ~80 Hz, below the lowest filter edge
	const float agc_attack = 1.0f - std::exp(-1.0f / (audio_rate * 0.002f));		// 2 ms
	const float agc_release = 1.0f - std::exp(-1.0f / (audio_rate * 0.5f));		// 500 ms
	const int agc_hang_samples = static_cast<int>(audio_rate * 0.5f);		// hold 500 ms before releasing
	bool usb;
	int low = 0, high = 0;
	firfilt_cccf voice = nullptr;
	float hp_previous = 0, hp_output = 0, agc_env = agc_target / agc_max_gain;
	int agc_hang = 0;
};

class Am : public Narrow {
public:
	explicit Am(const float &vol) : Narrow(5, vol) {
		name = "AM Radio";
		filter = firfilt_crcf_create_kaiser(taps, cutoff, 60.0f, 0.0f);
		firfilt_crcf_set_scale(filter, static_cast<float>(1.0 / dc_gain(taps, cutoff)));
	}
	~Am() override { firfilt_crcf_destroy(filter); }
	void process(const IQ &iq, std::vector<float> &audio) override {
		front(iq);
		audio.resize(iq2.size());
		for (size_t i = 0; i < iq2.size(); ++i) {
			firfilt_crcf_push(filter, static_cast<liquid_float_complex>(iq2[i]));
			liquid_float_complex y;
			firfilt_crcf_execute(filter, &y);
			audio[i] = std::abs(static_cast<std::complex<float>>(y));
			channel_power += 0.5 * std::norm(y);		// signal meter, as SSB
		}
		channel_count += iq2.size();
		for (float &sample : audio) {
			dc += 0.0025f * (sample - dc);		// carrier level, ~20 ms
			sample = (sample - dc) / std::max(dc, carrier_floor) * am_gain;
		}
		finish(audio, vol);
	}
private:
	static constexpr unsigned int taps = 81;
	static constexpr float cutoff = 5000.0f / 20000.0f;
	static constexpr float am_gain = 4.0f;
	static constexpr float carrier_floor = 1e-3f;		// limits gain on weak signals and empty channels
	firfilt_crcf filter;
	float dc = 0;
};

class Wbfm : public Demod {		// 1 MS/s, dial to DC, 401-tap 100 kHz LPF -> 250 kS/s, discriminator, 75 us de-emphasis, 24 kHz
public:
	explicit Wbfm(const float &vol) : vol(vol) {
		decimation = 2;
		offset = 150000;
		audio_rate = 24000;
		zooms = {1e6, quad_rate};
		name = "FM Radio";
		std::vector<float> taps(401);
		liquid_firdes_kaiser(taps.size(), 100000.0f / 1e6f, 60.0f, 0.0f, taps.data());
		float sum = 0.0f;
		for (float v : taps) sum += v;
		for (float &v : taps) v /= sum;
		lpf = firdecim_crcf_create(decim, taps.data(), taps.size());
		dem = freqdem_create(75000.0f / quad_rate);
		resampler = msresamp_rrrf_create(audio_rate / quad_rate, 60.0f);
		nco = nco_crcf_create(LIQUID_NCO);
		nco_crcf_set_frequency(nco, 2.0f * pi * offset / 1e6f);
	}
	~Wbfm() override {
		msresamp_rrrf_destroy(resampler);
		freqdem_destroy(dem);
		firdecim_crcf_destroy(lpf);
		nco_crcf_destroy(nco);
	}
	void tune(double if_hz) override { nco_crcf_set_frequency(nco, 2.0f * pi * if_hz / 1e6f); }
	void process(const IQ &iq, std::vector<float> &audio) override {
		pending.insert(pending.end(), iq.begin(), iq.end());
		auto *added = reinterpret_cast<liquid_float_complex*>(pending.data() + pending.size() - iq.size());
		nco_crcf_mix_block_up(nco, added, added, iq.size());
		decimate(lpf, decim, pending, channel);
		for (auto &x : channel) channel_power += 0.5 * std::norm(x);		// signal meter: the 200 kHz channel before the discriminator
		channel_count += channel.size();
		demodulated.resize(channel.size());
		if (!channel.empty())
			freqdem_demodulate_block(dem, reinterpret_cast<liquid_float_complex*>(channel.data()), channel.size(), demodulated.data());
		for (float &sample : demodulated) {
			deemph += alpha * (sample - deemph);
			sample = deemph;
		}
		audio.resize(static_cast<size_t>(std::ceil(1.0f + 2.0f * (audio_rate / quad_rate) * channel.size())) + 64);		// resampler output varies per block
		unsigned int produced = 0;
		msresamp_rrrf_execute(resampler, demodulated.data(), demodulated.size(), audio.data(), &produced);
		audio.resize(produced);
		for (float &sample : audio) {
			dc += 0.001f * (sample - dc);
			sample = sample - dc;
		}
		finish(audio, vol, false);
	}
private:
	static constexpr unsigned int decim = 4;
	static constexpr float quad_rate = 250000.0f;
	const float alpha = 1.0f - std::exp(-1.0f / (quad_rate * 75e-6f));
	const float &vol;
	firdecim_crcf lpf;
	freqdem dem;
	msresamp_rrrf resampler;
	nco_crcf nco;
	IQ pending, channel;
	std::vector<float> demodulated;
	float deemph = 0, dc = 0;
};
}

void Demod::finish(std::vector<float> &audio, float vol, bool limit) {
	if (tap) {
		clean.resize(audio.size());
		for (size_t i = 0; i < audio.size(); ++i) clean[i] = std::clamp(audio[i], -0.9f, 0.9f);
	}
	for (float &s : audio) s = limit ? std::clamp(s * vol, -0.9f, 0.9f) : s * vol;
}

std::unique_ptr<Demod> make_demod(const std::string &mode, const float &vol) {
	if (mode == "lsb" || mode == "usb") return std::make_unique<Ssb>(mode == "usb", vol);
	if (mode == "am") return std::make_unique<Am>(vol);
	if (mode == "wbfm") return std::make_unique<Wbfm>(vol);
	return nullptr;
}
