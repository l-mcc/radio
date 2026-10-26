#ifndef RSP_H
#define RSP_H

#include <array>
#include <atomic>
#include <complex>
#include <cstddef>
#include <cstdint>
#include <mutex>
#include <vector>

#include "sdrplay_api.h"

class RSP {
public:
	using IQCallback = void (*)(const std::complex<float>*, size_t, void*);

	RSP(double freq = 1380000, double rate = 2000000);
	~RSP();
	RSP(const RSP&) = delete;
	RSP& operator=(const RSP&) = delete;

	bool open();
	bool start();
	void stop();
	void close();
	bool set_frequency(double hz);
	bool set_antenna(char ant);
	bool set_gain(int if_reduction, int lna);
	bool set_rf_notch(bool enable);
	bool set_dab_notch(bool enable);
	bool rf_notch() const;
	bool dab_notch() const;
	bool set_hdr(bool enable);
	bool hdr() const;
	bool set_decimation(int factor);	// 1 (off), 2, 4, 8, 16, 32
	bool overload() const;
	uint64_t overload_count() const;
	void set_iq_callback(IQCallback cb, void* context = nullptr);
	void set_stdout(bool enable);
	uint64_t samples() const;
	bool running() const;

private:
	static bool check(sdrplay_api_ErrT error, const char* operation);
	static void streamcb(short* xi, short* xq, sdrplay_api_StreamCbParamsT*,
		unsigned int n, unsigned int reset, void* context);
	static void eventcb(sdrplay_api_EventT event, sdrplay_api_TunerSelectT tuner,
		sdrplay_api_EventParamsT* params, void* context);

	double freq_;
	double rate_;
	bool opened_ = false;
	bool locked_ = false;
	bool selected_ = false;
	bool running_ = false;
	std::atomic<bool> output_stdout_{false};
	std::atomic<uint64_t> samples_{0};
	std::atomic<bool> overload_{false};
	std::atomic<uint64_t> overload_count_{0};
	sdrplay_api_DeviceT devices_[6]{};
	sdrplay_api_DeviceT* device_ = nullptr;
	sdrplay_api_DeviceParamsT* params_ = nullptr;
	sdrplay_api_CallbackFnsT cb_{};
	IQCallback iq_callback_ = nullptr;
	void* iq_context_ = nullptr;
};

class IQQueue {
public:
	struct Stats {
		size_t depth;
		size_t peak;
		uint64_t pushed;
		uint64_t popped;
		uint64_t dropped;
	};

	Stats stats(bool reset_peak = false);
	uint64_t dropped();
	void push(const std::complex<float>* iq, size_t count);
	bool pop(std::vector<std::complex<float>>& iq);

private:
	static constexpr size_t Capacity = 256;
	static constexpr size_t BlockSamples = 1008;

	struct Block {
		std::array<std::complex<float>, BlockSamples> samples;
		size_t count = 0;
	};

	std::vector<Block> ring_ = std::vector<Block>(Capacity);
	std::mutex mutex_;
	size_t head_ = 0;
	size_t tail_ = 0;
	size_t depth_ = 0;
	uint64_t dropped_ = 0;
	size_t peak_ = 0;
	uint64_t pushed_ = 0;
	uint64_t popped_ = 0;
};

void iq_test(const std::complex<float>* iq, size_t count, void* context);
void iq_enqueue(const std::complex<float>* iq, size_t count, void* context);

#endif
