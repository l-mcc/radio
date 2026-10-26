#ifndef TUI_H
#define TUI_H

#include <chrono>
#include <string>
#include <vector>
#include <ncurses.h>
#include "control.h"

extern int ssb_bw_index;		// bandwidth preset 0-3 (bw command)
extern int ssb_bw_low;
extern int ssb_bw_high;
extern bool wf_on;		// waterfall wanted; the receiver creates or drops it
extern bool digi_on;		// decoder feed wanted: 48 kHz s16 mono, UDP 127.0.0.1:7355
extern bool nb_on;		// noise blanker wanted (SSB, AM)
extern int nb_db;		// its trigger, dB over the running power

class TUI {
public:
	explicit TUI(Control& control);
	~TUI();

	bool open();
	void close();
	bool poll();
	void set_signal_db(double value);
	void set_channel_db(double value);
	void clear_channel() { channel_valid_ = false; }		// mode without a passband meter
	void set_nb_rate(double per_second) { nb_rate_ = per_second; }		// pulses blanked, shown on the row

private:
	void layout();
	void draw();
	void adjust(int direction, bool large = false);
	void start_command(bool prefill);
	bool handle(int key);
	void open_list();
	bool list_key(int key);		// keys while the bookmarks popup is open
	void pick(int i);
	bool help_key(int key);		// keys while the help popup is open

	Control& control_;
	WINDOW* left_ = nullptr;
	WINDOW* right_ = nullptr;
	WINDOW* bottom_ = nullptr;
	WINDOW* popup_ = nullptr;		// bookmarks list, null when closed
	WINDOW* help_ = nullptr;
	bool help_open_ = false;
	int help_top_ = 0;
	bool list_open_ = false;
	bool armed_ = false;		// d pressed once; a second d deletes
	bool renaming_ = false;		// editing the highlighted row's name in the bottom line (input_)
	int sel_ = 0, top_ = 0;		// highlighted row, first row shown
	std::vector<Control::Bookmark> list_;
	bool open_ = false;
	int saved_stderr_ = -1;
	bool entering_ = false;
	bool signal_valid_ = false;
	bool channel_valid_ = false;
	int selected_ = 0;
	int left_width_ = 0;
	int screen_height_ = 0;
	int screen_width_ = 0;
	double signal_db_ = -120.0;
	double channel_db_ = -120.0;
	double nb_rate_ = 0;
	std::string input_;
	std::string hint_;
	std::chrono::steady_clock::time_point last_draw_{};
};

#endif
