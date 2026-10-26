#ifndef CONTROL_H
#define CONTROL_H

#include <chrono>
#include <cstdint>
#include <string>
#include <vector>

class RSP;
struct sqlite3;

class Control {
public:
	Control(RSP& rsp, float& volume, std::string mode, double frequency_hz,
		double offset_hz = 0.0, int if_reduction = 50, int lna = 5, char antenna = 'a');
	~Control();

	Control(const Control&) = delete;
	Control& operator=(const Control&) = delete;

	bool open(const std::string& database = "", bool restore_last = true);
	bool poll();                          // false means quit requested
	bool command(const std::string& line);
	void set_offset(double offset_hz);		// LO above the dial; retunes
	void status() const;

	struct State {
		std::string mode;
		double mhz;
		double rf_mhz;		// hardware LO, independent of dial
		float volume;
		int if_reduction;
		int lna;
		char antenna;
		bool rf_notch;
		bool dab_notch;
		bool hdr;
		bool overload;
		uint64_t overload_count;
		double ref;		// waterfall floor shift, dB
		int zoom;		// waterfall span: 0 widest
	};
	State state() const;
	void set_ui_active(bool active) { ui_active_ = active; }
	const std::string& notice() const { return notice_; }
	std::string take_notice() { std::string n; n.swap(notice_); return n; }		// rx -n: each notice goes out once
	bool stdin_closed() const { return stdin_closed_; }
	bool take_list_request() { bool r = list_wanted_; list_wanted_ = false; return r; }		// rx -n: bookmark list changed or asked for

	struct Bookmark { int64_t hz; std::string mode, name; int hits; };		// one settings row
	std::vector<Bookmark> bookmarks();		// most picked first, then by frequency
	void pick(const Bookmark& b);		// counts the pick, then tunes there in its mode
	bool forget(const Bookmark& b);		// deletes the row
	bool rename(const Bookmark& b, const std::string& name);		// "" goes back to the band label

private:
	bool save(const std::string& name = "");		// name: "" keeps an existing one
	bool load(bool startup = false);		// startup: also restore the waterfall on/off
	bool remember_frequency();
	std::string saved_mode(int64_t hz);		// mode of the newest save at hz, or ""
	std::string mode_for(int64_t hz);		// saved mode at hz, else the band plan, else ""

	RSP& rsp_;
	float& volume_;
	std::string mode_;
	int64_t hz_;                    // displayed frequency, excluding tuner offset
	double rf_hz_;		// actual hardware LO
	double offset_hz_;
	int if_reduction_;
	int lna_;
	char antenna_;
	double ref_ = 0.0;
	int zoom_ = 0;
	sqlite3* db_ = nullptr;
	std::string input_;
	bool stdin_closed_ = false;
	bool ui_active_ = false;
	bool list_wanted_ = false;
	bool picking_ = false;		// load() during pick(): the row's waterfall on/off applies too
	std::string notice_;
	std::chrono::steady_clock::time_point last_poll_{};
};

const char* band_mode(double mhz);		// band-plan default mode, nullptr outside the table
const char* band_name(double mhz);		// "40m", "31m SW", ...; "" outside the table

#endif
