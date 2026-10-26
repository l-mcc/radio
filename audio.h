#ifndef AUDIO_H
#define AUDIO_H

#include <cstddef>
#include <string>
#include <vector>
#include <pulse/simple.h>

class Audio {
public:
	Audio(unsigned int rate, const char* name, unsigned int latency_ms = 100, unsigned int batch_ms = 20);
	~Audio();

	bool open();
	void close();
	bool write(const float* samples, size_t count);
	bool write(const std::vector<float>& samples) { return write(samples.data(), samples.size()); }
	bool flush();
	double latency_ms();
	void set_quiet(bool quiet) { quiet_ = quiet; }	// suppress stderr messages, e.g. while the TUI is up

private:
	unsigned int rate_;
	std::string name_;
	unsigned int latency_ms_;
	size_t batch_;
	pa_simple* speaker_ = nullptr;
	int error_ = 0;
	bool quiet_ = false;
	std::vector<float> buffer_;
};

#endif
