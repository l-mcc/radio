#include "rsp.h"
#include "sdrplay_api_rspDx.h"
#include <algorithm>
#include <cstdio>
#include <unistd.h>
#include <utility>

RSP::RSP(double freq, double rate)
		: freq_(freq), rate_(rate) {}

RSP::~RSP() { close(); }

bool RSP::open() {
		if (opened_) return true;
		if (!check(sdrplay_api_Open(), "Open")) return false;
		opened_ = true;

		if (!check(sdrplay_api_LockDeviceApi(), "LockDeviceApi")) {
			close();
			return false;
		}
		locked_ = true;

		unsigned int count = 0;
		if (!check(sdrplay_api_GetDevices(devices_, &count, 6), "GetDevices") || count == 0) {
			if (count == 0) fprintf(stderr, "No SDRplay device found\n");
			close();
			return false;
		}

		device_ = nullptr;
		for (unsigned int i = 0; i < count; ++i) {
			if (devices_[i].hwVer == SDRPLAY_RSPdx_ID) {
				device_ = &devices_[i];
				break;
			}
		}
		if (!device_) {
			fprintf(stderr, "No RSPdx found\n");
			close();
			return false;
		}

		if (!check(sdrplay_api_SelectDevice(device_), "SelectDevice")) {
			close();
			return false;
		}
		selected_ = true;
		sdrplay_api_UnlockDeviceApi();
		locked_ = false;

		if (!check(sdrplay_api_GetDeviceParams(device_->dev, &params_), "GetDeviceParams")) {
			close();
			return false;
		}

		auto* dp = params_->devParams;
		auto* rx = params_->rxChannelA;
		if (!dp || !rx) {
			fprintf(stderr, "Device parameters unavailable\n");
			close();
			return false;
		}

		dp->fsFreq.fsHz = rate_;
		dp->ppm = 0;
		dp->rspDxParams.antennaSel = sdrplay_api_RspDx_ANTENNA_A;
		dp->rspDxParams.hdrEnable = 0;
		dp->rspDxParams.rfNotchEnable = 0;
		dp->rspDxParams.rfDabNotchEnable = 0;
		dp->rspDxParams.biasTEnable = 0;

		rx->tunerParams.rfFreq.rfHz = freq_;
		rx->tunerParams.bwType = sdrplay_api_BW_Undefined;
		rx->tunerParams.ifType = sdrplay_api_IF_Zero;
		rx->tunerParams.gain.gRdB = 40;
		rx->tunerParams.gain.LNAstate = 10;
		/* rx->ctrlParams.agc.enable = sdrplay_api_AGC_50HZ; */
		rx->ctrlParams.agc.enable = sdrplay_api_AGC_DISABLE;
		rx->ctrlParams.agc.setPoint_dBfs = -30;
		rx->ctrlParams.dcOffset.DCenable = 0;
		rx->ctrlParams.dcOffset.IQenable = 0;

		return true;
	}

bool RSP::start() {
		if (!device_ || !params_) return false;
		if (running_) return true;
		cb_.StreamACbFn = streamcb;
		cb_.EventCbFn = eventcb;
		if (!check(sdrplay_api_Init(device_->dev, &cb_, this), "Init"))
			return false;
		running_ = true;
		return true;
	}

void RSP::stop() {
		if (!running_) return;
		fprintf(stderr, "RSP: Uninit starting\n");
		if (check(sdrplay_api_Uninit(device_->dev), "Uninit"))
			running_ = false;
	}

void RSP::close() {
		if (running_) stop();
		if (locked_) {
			sdrplay_api_UnlockDeviceApi();
			locked_ = false;
		}
		if (selected_) {
			sdrplay_api_LockDeviceApi();
			sdrplay_api_ReleaseDevice(device_);
			sdrplay_api_UnlockDeviceApi();
			selected_ = false;
		}
		device_ = nullptr;
		params_ = nullptr;
		if (opened_) {
			sdrplay_api_Close();
			opened_ = false;
		}
	}

bool RSP::set_frequency(double hz) {
		if (!params_) return false;
		auto& rf = params_->rxChannelA->tunerParams.rfFreq.rfHz;
		double old = rf;
		rf = hz;
		if (running_ && !check(sdrplay_api_Update(device_->dev, device_->tuner,
				sdrplay_api_Update_Tuner_Frf, sdrplay_api_Update_Ext1_None), "Set frequency")) {
			rf = old;
			return false;
		}
		freq_ = hz;
		return true;
	}

bool RSP::set_antenna(char ant) {
		if (!params_) return false;
		sdrplay_api_RspDx_AntennaSelectT value;
		switch (ant) {
		case 'a': case 'A': value = sdrplay_api_RspDx_ANTENNA_A; break;
		case 'b': case 'B': value = sdrplay_api_RspDx_ANTENNA_B; break;
		case 'c': case 'C': value = sdrplay_api_RspDx_ANTENNA_C; break;
		default: return false;
		}
		auto& setting = params_->devParams->rspDxParams.antennaSel;
		auto old = setting;
		setting = value;
		if (running_ && !check(sdrplay_api_Update(device_->dev, device_->tuner,
				sdrplay_api_Update_None, sdrplay_api_Update_RspDx_AntennaControl), "Set antenna")) {
			setting = old;
			return false;
		}
		return true;
	}

bool RSP::set_gain(int if_reduction, int lna) {
		if (!params_ || if_reduction < 20 || if_reduction > 59 || lna < 0 || lna > 27)
			return false;
		auto& gain = params_->rxChannelA->tunerParams.gain;
		auto old = gain;
		gain.gRdB = if_reduction;
		gain.LNAstate = lna;
		if (running_ && !check(sdrplay_api_Update(device_->dev, device_->tuner,
				sdrplay_api_Update_Tuner_Gr, sdrplay_api_Update_Ext1_None), "Set gain")) {
			gain = old;
			return false;
		}
		return true;
	}

bool RSP::set_rf_notch(bool enable) {
	if (!params_ || !params_->devParams) return false;
	auto& flag = params_->devParams->rspDxParams.rfNotchEnable;
	auto old = flag;
	flag = enable ? 1 : 0;
	if (running_ && !check(sdrplay_api_Update(device_->dev, device_->tuner,
			sdrplay_api_Update_None, sdrplay_api_Update_RspDx_RfNotchControl), "Set MW/FM notch")) {
		flag = old;
		return false;
	}
	return true;
}

bool RSP::set_dab_notch(bool enable) {
	if (!params_ || !params_->devParams) return false;
	auto& flag = params_->devParams->rspDxParams.rfDabNotchEnable;
	auto old = flag;
	flag = enable ? 1 : 0;
	if (running_ && !check(sdrplay_api_Update(device_->dev, device_->tuner,
			sdrplay_api_Update_None, sdrplay_api_Update_RspDx_RfDabNotchControl), "Set DAB notch")) {
		flag = old;
		return false;
	}
	return true;
}

bool RSP::rf_notch() const {
	return params_ && params_->devParams && params_->devParams->rspDxParams.rfNotchEnable != 0;
}

bool RSP::dab_notch() const {
	return params_ && params_->devParams && params_->devParams->rspDxParams.rfDabNotchEnable != 0;
}

bool RSP::set_hdr(bool enable) {
	if (!params_ || !params_->devParams || (enable && freq_ >= 2e6)) return false;
	auto& flag = params_->devParams->rspDxParams.hdrEnable;
	auto old = flag;
	if (old == (enable ? 1 : 0)) return true;
	flag = enable ? 1 : 0;
	if (running_ && !check(sdrplay_api_Update(device_->dev, device_->tuner,
			sdrplay_api_Update_None, sdrplay_api_Update_RspDx_HdrEnable), "Set HDR")) {
		flag = old;
		return false;
	}
	return true;
}

bool RSP::hdr() const {
	return params_ && params_->devParams && params_->devParams->rspDxParams.hdrEnable != 0;
}

bool RSP::set_decimation(int factor) {
	if (!params_ || !params_->rxChannelA) return false;
	if (factor != 1 && factor != 2 && factor != 4 && factor != 8 && factor != 16 && factor != 32)
		return false;
	auto& d = params_->rxChannelA->ctrlParams.decimation;
	auto old = d;
	d.enable = factor > 1 ? 1 : 0;
	d.decimationFactor = static_cast<unsigned char>(factor);
	d.wideBandSignal = 1;	// half-band filters rather than block averaging
	if (running_ && !check(sdrplay_api_Update(device_->dev, device_->tuner,
			sdrplay_api_Update_Ctrl_Decimation, sdrplay_api_Update_Ext1_None), "Set decimation")) {
		d = old;
		return false;
	}
	return true;
}

void RSP::set_iq_callback(IQCallback cb, void* context) {
		iq_callback_ = cb;
		iq_context_ = context;
	}

void RSP::set_stdout(bool enable) { output_stdout_ = enable; }

uint64_t RSP::samples() const { return samples_.load(); }

bool RSP::running() const { return running_; }

bool RSP::overload() const { return overload_.load(std::memory_order_relaxed); }
uint64_t RSP::overload_count() const { return overload_count_.load(std::memory_order_relaxed); }

bool RSP::check(sdrplay_api_ErrT error, const char* operation) {
		if (error == sdrplay_api_Success) return true;
		fprintf(stderr, "%s: %s\n", operation, sdrplay_api_GetErrorString(error));
		return false;
	}

void RSP::streamcb(short* xi, short* xq, sdrplay_api_StreamCbParamsT*,
		unsigned int n, unsigned int reset, void* context) {
		auto* self = static_cast<RSP*>(context);
		if (reset) return;
		self->samples_.fetch_add(n, std::memory_order_relaxed);
		if (!self->output_stdout_ && !self->iq_callback_) return;


		float iq[2016];
		if (n > 1008) return;
		for (unsigned int i = 0; i < n; ++i) {
			iq[2*i] = static_cast<float>(xi[i]) / 32768.0f;
			iq[2*i+1] = static_cast<float>(xq[i]) / 32768.0f;
		}

		if (self->iq_callback_) {
			self->iq_callback_(
				reinterpret_cast<const std::complex<float>*>(iq),
				n,
				self->iq_context_
			);
		}
		if (!self->output_stdout_) return;

		size_t bytes = n * 2 * sizeof(float);
		size_t offset = 0;
		while (offset < bytes) {
			ssize_t written = write(STDOUT_FILENO,
				reinterpret_cast<const char*>(iq) + offset, bytes - offset);
			if (written <= 0) {
				self->output_stdout_ = false;
				return;
			}
			offset += written;
		}
	}

	void RSP::eventcb(sdrplay_api_EventT event,
			sdrplay_api_TunerSelectT tuner,
			sdrplay_api_EventParamsT* params, void* context) {

		auto* self = static_cast<RSP*>(context);

		if (event == sdrplay_api_PowerOverloadChange && self->device_) {

			if (params) {
				auto status = params->powerOverloadParams.powerOverloadChangeType;
				if (status == sdrplay_api_Overload_Detected) {
					self->overload_.store(true, std::memory_order_relaxed);
					self->overload_count_.fetch_add(1, std::memory_order_relaxed);
				} else if (status == sdrplay_api_Overload_Corrected) {
					self->overload_.store(false, std::memory_order_relaxed);
				}
			}
			sdrplay_api_Update(self->device_->dev, tuner, sdrplay_api_Update_Ctrl_OverloadMsgAck, sdrplay_api_Update_Ext1_None);
		}
	}			// eventcb()

/* void RSP::eventcb(sdrplay_api_EventT event, sdrplay_api_TunerSelectT tuner, */
/* 		sdrplay_api_EventParamsT* params, void* context) { */
/* 		auto* self = static_cast<RSP*>(context); */
/* 		if (event == sdrplay_api_PowerOverloadChange && self->device_) { */
/* 			sdrplay_api_Update(self->device_->dev, tuner, */
/* 				sdrplay_api_Update_Ctrl_OverloadMsgAck, */
/* 				sdrplay_api_Update_Ext1_None); */
/* 		} */
/* 	} */

IQQueue::Stats IQQueue::stats(bool reset_peak) {
	std::lock_guard<std::mutex> lock(mutex_);
	Stats s{depth_, peak_, pushed_, popped_, dropped_};
	if (reset_peak) peak_ = depth_;
	return s;
}

uint64_t IQQueue::dropped() {
	std::lock_guard<std::mutex> lock(mutex_);
	return dropped_;
}

void IQQueue::push(const std::complex<float>* iq, size_t count) {
	std::lock_guard<std::mutex> lock(mutex_);
	++pushed_;
	if (count > BlockSamples) {
		++dropped_;
		return;
	}

	if (depth_ == Capacity) {
		head_ = (head_ + 1) % Capacity;
		--depth_;
		++dropped_;
	}

	auto& slot = ring_[tail_];
	std::copy_n(iq, count, slot.samples.begin());
	slot.count = count;
	tail_ = (tail_ + 1) % Capacity;
	++depth_;
	if (depth_ > peak_) peak_ = depth_;
}

bool IQQueue::pop(std::vector<std::complex<float>>& iq) {
	std::lock_guard<std::mutex> lock(mutex_);
	if (depth_ == 0) return false;

	const auto& slot = ring_[head_];
	iq.assign(slot.samples.begin(), slot.samples.begin() + slot.count);
	head_ = (head_ + 1) % Capacity;
	--depth_;
	++popped_;
	return true;
}

void iq_test(const std::complex<float>* iq, size_t count, void* context) {
	auto* total = static_cast<std::atomic<uint64_t>*>(context);
	total->fetch_add(count, std::memory_order_relaxed);
}

void iq_enqueue(const std::complex<float>* iq, size_t count, void* context) {
	auto* queue = static_cast<IQQueue*>(context);
	queue->push(iq, count);
}
