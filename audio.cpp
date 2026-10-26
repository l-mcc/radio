#include "audio.h"
#include <chrono>
#include <cstdio>
#include <pulse/error.h>

Audio::Audio(unsigned int rate, const char* name, unsigned int latency_ms, unsigned int batch_ms)
	: rate_(rate), name_(name), latency_ms_(latency_ms), batch_(rate * batch_ms / 1000) {}

Audio::~Audio() { close(); }

bool Audio::open() {
	if (speaker_) return true;
	pa_sample_spec spec = {};
	spec.format = PA_SAMPLE_FLOAT32NE;
	spec.rate = rate_;
	spec.channels = 1;

	pa_buffer_attr attr;
	attr.maxlength = static_cast<uint32_t>(-1);
	attr.tlength = pa_usec_to_bytes(latency_ms_ * 1000ull, &spec);	// target latency
	attr.prebuf = static_cast<uint32_t>(-1);
	attr.minreq = static_cast<uint32_t>(-1);
	attr.fragsize = static_cast<uint32_t>(-1);

	speaker_ = pa_simple_new(
		nullptr, "RSPdx",
		PA_STREAM_PLAYBACK,
		nullptr, name_.c_str(),
		&spec, nullptr, &attr, &error_
	);
	if (!speaker_) {
		fprintf(stderr, "PulseAudio: %s\n", pa_strerror(error_));
		return false;
	}
	buffer_.reserve(batch_ * 2);
	return true;
}

void Audio::close() {
	if (!speaker_) return;
	pa_simple_free(speaker_);
	speaker_ = nullptr;
}

bool Audio::write(const float* samples, size_t count) {
	buffer_.insert(buffer_.end(), samples, samples + count);
	if (buffer_.size() < batch_) return true;
	return flush();
}

bool Audio::flush() {
	if (!speaker_ || buffer_.empty()) return true;
	auto t0 = std::chrono::steady_clock::now();
	int rc = pa_simple_write(speaker_, buffer_.data(), buffer_.size() * sizeof(float), &error_);
	auto ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
	buffer_.clear();
	if (rc < 0) {
		if (!quiet_) fprintf(stderr, "Audio: %s\n", pa_strerror(error_));
		return false;
	}
	if (ms > 50.0 && !quiet_)
		fprintf(stderr, "PulseAudio blocked %.2f ms\n", ms);
	return true;
}

double Audio::latency_ms() {
	if (!speaker_) return 0.0;
	return pa_simple_get_latency(speaker_, &error_) / 1000.0;
}
