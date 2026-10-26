#include "tui.h"
#include "control.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <fcntl.h>
#include <cstdlib>
#include <iomanip>
#include <sstream>
#include <unistd.h>

namespace {
constexpr int left_percent = 50;
constexpr int min_left_width = 51;
constexpr int bottom_height = 1;
constexpr int item_rows[] = {1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14};
constexpr int item_count = sizeof(item_rows) / sizeof(item_rows[0]);

const char* bandwidth_names[] = {"Narrow voice", "Normal voice", "Wide voice", "Digital"};
const int bandwidth_low[] = {300, 300, 200, 200};
const int bandwidth_high[] = {2100, 3100, 3300, 3000};
constexpr int bandwidth_count = 4;
constexpr double channel_min_db = -100.0;
constexpr double channel_max_db = -40.0;

constexpr int button_x = 2;		// [ Bookmarks ] sits in the left panel's top border
const char* button = "[ Bookmarks ]";
constexpr int button_w = 13;
constexpr int list_w = 40;		// popup width
const char* help_button = "[ Help ]";
constexpr int help_x = button_x + button_w + 1, help_w = 8;
const char* help_lines[] = {
	"Main screen",
	"  j k  Up Down        select a row",
	"  h l  Left Right     adjust it (frequency: 1 kHz SSB, 100 kHz AM/FM)",
	"  Shift-Left/Right    bigger step (10 kHz SSB, 1 MHz AM/FM)",
	"  Enter  Space        edit the value, or toggle / cycle it",
	"  s                   save freq+mode, settings and waterfall on/off",
	"  b  bookmarks        w  waterfall on/off       :  command line",
	"  d  digital out on/off (48 kHz s16 mono, UDP 127.0.0.1:7355)",
	"  n  noise blanker on/off; h/l on its row: trigger level (lower dB catches more)",
	"     /s = pulses blanked per second: ~1/s for a fence; dozens on plain noise = too low"
	"  ?  this help        q  quit",
	"",
	"Mouse",
	"  wheel on a row      adjust it",
	"  click a toggle row  toggle it (Bandwidth, HDR, notches, Waterfall, Digital, Blanker)",
	"  click bottom line   command line",
	"  [ Bookmarks ]  [ Help ]   buttons in the top border",
	"",
	"Bookmarks list",
	"  j k  wheel  PgUp PgDn   move",
	"  Enter  click            tune there, settings + waterfall (count +1)",
	"  n  right-click          rename ('' or empty removes the name)",
	"  d  Delete               press twice to delete",
	"  Esc  b  q               close",
	"",
	"Command line (:)    Esc cancels, ^U clears",
	"  f MHz              m lsb|usb|am|wbfm     v 0-2",
	"  if 20-59           lna N                 g IF LNA",
	"  ant a|b|c          hdr 0|1  notch 0|1  dab 0|1",
	"  ref -20..20        zoom 0-2 (0 = widest)  digi 0|1",
	"  nb 0|1|8-20 (dB: on at that trigger level)",
	"  s [name]           status   help   q",
};
constexpr int help_count = sizeof(help_lines) / sizeof(help_lines[0]);

bool is_ssb(const std::string& mode) { return mode == "usb" || mode == "lsb"; }

int item_row(int i, bool ssb) {
	if (ssb && i == 2) return 3;
	int old = i - (ssb && i > 2 ? 1 : 0);
	return item_rows[old] + (ssb && old >= 2 ? 1 : 0);
}

int old_item(int i, bool ssb) {
	return i - (ssb && i > 2 ? 1 : 0);
}


int lna_max(double mhz) {
	if (mhz < 12.0) return 18;
	if (mhz < 50.0) return 19;
	if (mhz < 60.0) return 24;
	if (mhz < 250.0) return 26;
	if (mhz < 420.0) return 27;
	if (mhz < 1000.0) return 20;
	return 18;
}
}

int ssb_bw_index = 1;
int ssb_bw_low = 300;
int ssb_bw_high = 3100;
bool wf_on = false;
bool digi_on = false;
bool nb_on = false;
int nb_db = 12;

void set_ssb_bw(int index) {
	ssb_bw_index = (index % bandwidth_count + bandwidth_count) % bandwidth_count;
	ssb_bw_low = bandwidth_low[ssb_bw_index];
	ssb_bw_high = bandwidth_high[ssb_bw_index];
}

TUI::TUI(Control& control) : control_(control) {}

TUI::~TUI() {
	close();
}

bool TUI::open() {
	if (!isatty(STDIN_FILENO) || !isatty(STDOUT_FILENO)) return false;
	const char* term = std::getenv("TERM");
	if (!term || std::string(term) == "dumb") return false;
	if (!initscr()) return false;

	int log_fd = ::open("rsp.log", O_WRONLY | O_CREAT | O_APPEND, 0644);
	int saved = ::dup(STDERR_FILENO);
	if (log_fd < 0 || saved < 0 || ::dup2(log_fd, STDERR_FILENO) < 0) {
		if (log_fd >= 0) ::close(log_fd);
		if (saved >= 0) ::close(saved);
		endwin();
		fprintf(stderr, "TUI: cannot redirect stderr to rsp.log\n");
		return false;
	}
	::close(log_fd);
	saved_stderr_ = saved;
	open_ = true;
	control_.set_ui_active(true);
	cbreak();
	noecho();
	keypad(stdscr, TRUE);
	set_escdelay(25);		// Esc closes the popup without the default 1 s wait
	nodelay(stdscr, TRUE);
	curs_set(0);
	mousemask(ALL_MOUSE_EVENTS, nullptr);
	mouseinterval(0);
	if (has_colors()) {
		start_color();
		use_default_colors();
		init_pair(1, COLOR_GREEN, -1);
		init_pair(2, COLOR_RED, -1);
		init_pair(3, COLOR_YELLOW, -1);
	}
	layout();
	draw();
	return true;
}

void TUI::close() {
	if (!open_) return;
	if (left_) delwin(left_);
	if (right_) delwin(right_);
	if (bottom_) delwin(bottom_);
	if (popup_) delwin(popup_);
	if (help_) delwin(help_);
	left_ = right_ = bottom_ = popup_ = help_ = nullptr;
	endwin();
	if (saved_stderr_ >= 0) {
		fflush(stderr);
		::dup2(saved_stderr_, STDERR_FILENO);
		::close(saved_stderr_);
		saved_stderr_ = -1;
	}
	control_.set_ui_active(false);
	open_ = false;
}

void TUI::layout() {
	if (left_) delwin(left_);
	if (right_) delwin(right_);
	if (bottom_) delwin(bottom_);
	if (popup_) delwin(popup_);
	if (help_) delwin(help_);
	left_ = right_ = bottom_ = popup_ = help_ = nullptr;

	getmaxyx(stdscr, screen_height_, screen_width_);
	if (screen_width_ < 80 || screen_height_ < 18) return;		// SSB puts the last row (Noise blanker) on row 15

	left_width_ = std::max(min_left_width, screen_width_ * left_percent / 100);
	left_width_ = std::min(left_width_, screen_width_ - 20);
	int top_height = screen_height_ - bottom_height;
	left_ = newwin(top_height, left_width_, 0, 0);
	right_ = newwin(top_height, screen_width_ - left_width_, 0, left_width_);
	bottom_ = newwin(bottom_height, screen_width_, top_height, 0);
}

void TUI::draw() {
	if (!open_) return;
	if (!left_ || !right_ || !bottom_) {
		erase();
		mvaddstr(0, 0, "Terminal too small (minimum 80 x 18). Resize or press q.");
		refresh();
		return;
	}

	auto s = control_.state();
	bool ssb = is_ssb(s.mode);
	werase(left_);
	int border_attr = has_colors() ? COLOR_PAIR(3) : A_BOLD;
	wattron(left_, border_attr);
	box(left_, 0, 0);
	wattroff(left_, border_attr);
	wattron(left_, list_open_ ? A_REVERSE : A_BOLD);
	mvwaddstr(left_, 0, button_x, button);
	wattroff(left_, list_open_ ? A_REVERSE : A_BOLD);
	wattron(left_, help_open_ ? A_REVERSE : A_BOLD);
	mvwaddstr(left_, 0, help_x, help_button);
	wattroff(left_, help_open_ ? A_REVERSE : A_BOLD);

	char frequency[32], volume[32], gain[32], lna[32], antenna[8];
	std::snprintf(frequency, sizeof(frequency), "%.6f MHz", s.mhz);
	std::snprintf(volume, sizeof(volume), "%.3f", s.volume);
	std::snprintf(gain, sizeof(gain), "%d dB", s.if_reduction);
	std::snprintf(lna, sizeof(lna), "%d", s.lna);
	std::snprintf(antenna, sizeof(antenna), "%c", s.antenna);

	char lna_range[24];
	std::snprintf(lna_range, sizeof(lna_range), "(0-%d)", lna_max(s.mhz));

	const char* names[] = {"Frequency", "Mode", "Volume", "IF reduction", "LNA reduction", "Antenna"};
	const char* values[] = {frequency, s.mode.c_str(), volume, gain, lna, antenna};
	const char* ranges[] = {"(0-2000 MHz)", "(lsb,usb,am,wbfm)", "(0-2)", "(20-59 dB)", lna_range, "(a,b,c)"};

	for (int i = 0; i < 6; ++i) {
		int idx = i + (ssb && i >= 2 ? 1 : 0);
		int attr = idx == selected_ ? (A_BOLD | (has_colors() ? COLOR_PAIR(1) : A_REVERSE)) : A_NORMAL;
		wattron(left_, attr);
		char line[128];
		std::snprintf(line, sizeof(line), "%-13.13s %-14.14s %s", names[i], values[i], ranges[i]);
		mvwaddnstr(left_, item_row(idx, ssb), 2, line, left_width_ - 4);
		wattroff(left_, attr);
	}

	if (ssb) {
		int attr = selected_ == 2 ? (A_BOLD | (has_colors() ? COLOR_PAIR(1) : A_REVERSE)) : A_NORMAL;
		wattron(left_, attr);
		char line[128];
		std::snprintf(line, sizeof(line), "%-13s %s (%.1f kHz)", "Bandwidth", bandwidth_names[ssb_bw_index], (ssb_bw_high - ssb_bw_low) / 1000.0);
		mvwaddnstr(left_, item_row(2, true), 2, line, left_width_ - 4);
		wattroff(left_, attr);
	}

	const char* hdr_value = (s.mhz >= 2.0) ? "--" : (s.hdr ? "ON" : "OFF");
	int hdr_attr = has_colors() ? COLOR_PAIR(s.hdr ? 1 : 2) : A_NORMAL;
	if (s.mhz >= 2.0) hdr_attr = A_DIM;
	if (selected_ == 6 + ssb) hdr_attr |= A_BOLD | A_REVERSE;
	wattron(left_, hdr_attr);
	char hdr_line[96];
	std::snprintf(hdr_line, sizeof(hdr_line), "%-13s %-14s (below 2 MHz)", "HDR", hdr_value);
	mvwaddnstr(left_, item_row(6 + ssb, ssb), 2, hdr_line, left_width_ - 4);
	wattroff(left_, hdr_attr);

	const char* notch_names[] = {"RF notch", "DAB notch"};
	const bool notch_values[] = {s.rf_notch, s.dab_notch};
	for (int i = 0; i < 2; ++i) {
		int attr = (has_colors() ? COLOR_PAIR(notch_values[i] ? 1 : 2) : A_NORMAL);
		if (selected_ == 7 + i + ssb) attr |= A_BOLD | A_REVERSE;
		wattron(left_, attr);
		char line[96];
		std::snprintf(line, sizeof(line), "%-13s %-14s (off/on)", notch_names[i], notch_values[i] ? "ON" : "OFF");
		mvwaddnstr(left_, item_row(7 + i + ssb, ssb), 2, line, left_width_ - 4);
		wattroff(left_, attr);
	}

	int wf_attr = has_colors() ? COLOR_PAIR(wf_on ? 1 : 2) : A_NORMAL;
	if (selected_ == 9 + ssb) wf_attr |= A_BOLD | A_REVERSE;
	wattron(left_, wf_attr);
	char wf_line[96];
	std::snprintf(wf_line, sizeof(wf_line), "%-13s %-14s (w, port 8700)", "Waterfall", wf_on ? "ON" : "OFF");
	mvwaddnstr(left_, item_row(9 + ssb, ssb), 2, wf_line, left_width_ - 4);
	wattroff(left_, wf_attr);

	int digi_attr = has_colors() ? COLOR_PAIR(digi_on ? 1 : 2) : A_NORMAL;
	if (selected_ == 11 + ssb) digi_attr |= A_BOLD | A_REVERSE;
	wattron(left_, digi_attr);
	char digi_line[96];
	std::snprintf(digi_line, sizeof(digi_line), "%-13s %-14s (d)", "Digital out", digi_on ? "udp 7355 48k" : "OFF");
	mvwaddnstr(left_, item_row(11 + ssb, ssb), 2, digi_line, left_width_ - 4);
	wattroff(left_, digi_attr);

	bool fm = s.mode == "wbfm";		// the blanker sits in the SSB/AM front end
	int nb_attr = fm ? A_DIM : has_colors() ? COLOR_PAIR(nb_on ? 1 : 2) : A_NORMAL;
	if (selected_ == 12 + ssb) nb_attr |= A_BOLD | A_REVERSE;
	wattron(left_, nb_attr);
	char nb_value[24], nb_line[96];
	if (fm) std::snprintf(nb_value, sizeof nb_value, "--");
	else if (nb_on) std::snprintf(nb_value, sizeof nb_value, "%d dB %.0f/s", nb_db, nb_rate_);
	else std::snprintf(nb_value, sizeof nb_value, "OFF");
	std::snprintf(nb_line, sizeof(nb_line), "%-13s %-14s (n, h/l 8-20 dB)", "Noise blanker", nb_value);
	mvwaddnstr(left_, item_row(12 + ssb, ssb), 2, nb_line, left_width_ - 4);
	wattroff(left_, nb_attr);

	int ref_attr = selected_ == 10 + ssb ? (A_BOLD | (has_colors() ? COLOR_PAIR(1) : A_REVERSE)) : A_NORMAL;
	wattron(left_, ref_attr);
	char ref_line[96];
	std::snprintf(ref_line, sizeof(ref_line), "%-13s %+.1f dB%-6s (-20..+20 dB)", "Waterfall ref", s.ref, "");
	mvwaddnstr(left_, item_row(10 + ssb, ssb), 2, ref_line, left_width_ - 4);
	wattroff(left_, ref_attr);

	werase(right_);
	box(right_, 0, 0);
	int right_width = screen_width_ - left_width_;
	char adc_line[128];
	if (signal_valid_) {
		if (right_width >= 40)
			std::snprintf(adc_line, sizeof(adc_line), "ADC: %.1f dBFS  Scale: -60..0 dBFS", signal_db_);
		else
			std::snprintf(adc_line, sizeof(adc_line), "ADC %.1f  Scale:-60..0", signal_db_);
	} else {
		std::snprintf(adc_line, sizeof(adc_line),
			right_width >= 40 ? "ADC: waiting  Scale: -60..0 dBFS" : "ADC: waiting  -60..0 dBFS");
	}
	mvwaddnstr(right_, 1, 2, adc_line, right_width - 4);

	int bar_width = std::max(0, right_width - 6);
	int filled = signal_valid_ ? static_cast<int>(std::round(
		std::clamp((signal_db_ + 60.0) / 60.0, 0.0, 1.0) * bar_width)) : 0;
	int red_start = static_cast<int>(std::ceil(bar_width * 54.0 / 60.0));
	for (int i = 0; i < bar_width; ++i) {
		if (i < filled) {
			int attr = has_colors() ? COLOR_PAIR(i >= red_start ? 2 : 1) : A_BOLD;
			wattron(right_, attr);
			mvwaddch(right_, 2, 2 + i, '#');
			wattroff(right_, attr);
		} else {
			mvwaddch(right_, 2, 2 + i, '-');
		}
	}
	if (channel_valid_) {
		char line[128];
		std::snprintf(line, sizeof(line), "Signal: %.1f dBFS  Scale: %.0f..%.0f",
			channel_db_, channel_min_db, channel_max_db);
		mvwaddnstr(right_, 3, 2, line, right_width - 4);
		int lit = static_cast<int>(std::round(std::clamp(
			(channel_db_ - channel_min_db) / (channel_max_db - channel_min_db), 0.0, 1.0) * bar_width));
		for (int i = 0; i < bar_width; ++i) {
			if (i < lit) {
				int attr = has_colors() ? COLOR_PAIR(1) : A_BOLD;
				wattron(right_, attr);
				mvwaddch(right_, 4, 2 + i, '#');
				wattroff(right_, attr);
			} else {
				mvwaddch(right_, 4, 2 + i, '-');
			}
		}
	} else {
		mvwaddnstr(right_, 3, 2, "Red: above -6 dBFS", right_width - 4);
		mvwaddnstr(right_, 4, 2, "Broadband ADC; not S-units", right_width - 4);
	}
	const std::string& msg = hint_.empty() ? control_.notice() : hint_;
	mvwaddnstr(right_, 6, 2, msg.c_str(), right_width - 4);

	if (s.overload || s.overload_count) {
		char line[96];
		std::snprintf(line, sizeof(line), "RF overload: %s (%llu)",
			s.overload ? "DETECTED" : "clear",
			static_cast<unsigned long long>(s.overload_count));
		int attr = has_colors() ? COLOR_PAIR(2) | A_BOLD : A_BOLD;
		wattron(right_, attr);
		mvwaddnstr(right_, 8, 2, line, right_width - 4);
		wattroff(right_, attr);
	}

	werase(bottom_);
	std::string prompt = renaming_ ? "Name (Enter saves, empty or '' removes, ^U clears, Esc cancels): " + input_ : entering_ ? ":" + input_ : ":";
	mvwaddnstr(bottom_, 0, 0, prompt.c_str(), screen_width_ - 1);
	curs_set(entering_ || renaming_ ? 1 : 0);
	if (entering_ || renaming_) wmove(bottom_, 0, std::min(screen_width_ - 2, static_cast<int>(prompt.size())));

	wnoutrefresh(left_);
	wnoutrefresh(right_);
	wnoutrefresh(bottom_);
	if (list_open_) {
		int rows = std::max(1, std::min<int>(list_.size(), screen_height_ - 4));
		if (!popup_) popup_ = newwin(rows + 2, list_w, 1, button_x);
		top_ = std::clamp(top_, std::max(0, sel_ - rows + 1), sel_);		// keep the highlight in view
		werase(popup_);
		wattron(popup_, border_attr);
		box(popup_, 0, 0);
		wattroff(popup_, border_attr);
		mvwaddstr(popup_, 0, 2, armed_ ? " d again deletes " : renaming_ ? " Renaming " : " Bookmarks  n name  d del ");
		if (list_.empty()) mvwaddstr(popup_, 1, 2, "none yet: s saves this frequency");
		for (int r = 0; r < rows && top_ + r < static_cast<int>(list_.size()); ++r) {
			const auto& b = list_[top_ + r];
			char line[64];
			std::snprintf(line, sizeof(line), " %-14.14s %11.6f %-4s %4d ", b.name.empty() ? band_name(b.hz / 1e6) : b.name.c_str(), b.hz / 1e6, b.mode.c_str(), b.hits);
			int attr = top_ + r == sel_ ? (A_BOLD | (has_colors() ? COLOR_PAIR(armed_ ? 2 : 1) | A_REVERSE : A_REVERSE)) : A_NORMAL;
			wattron(popup_, attr);
			mvwaddnstr(popup_, 1 + r, 1, line, list_w - 2);
			wattroff(popup_, attr);
		}
		touchwin(popup_);		// the panels under it were just redrawn
		wnoutrefresh(popup_);
		if (renaming_) wnoutrefresh(bottom_);		// the terminal cursor follows the last window refreshed
	}
	if (help_open_) {
		int h = std::min(help_count + 2, screen_height_ - 1), w = std::min(74, screen_width_ - 2);
		if (!help_) help_ = newwin(h, w, 0, (screen_width_ - w) / 2);
		help_top_ = std::clamp(help_top_, 0, std::max(0, help_count - (h - 2)));
		werase(help_);
		wattron(help_, border_attr);
		box(help_, 0, 0);
		wattroff(help_, border_attr);
		mvwaddstr(help_, 0, 2, h - 2 < help_count ? " Help  j/k scroll  any other key closes " : " Help  any key closes ");
		for (int r = 0; r < h - 2 && help_top_ + r < help_count; ++r) {
			const char* line = help_lines[help_top_ + r];
			bool heading = line[0] && line[0] != ' ';
			if (heading) wattron(help_, A_BOLD | (has_colors() ? COLOR_PAIR(3) : 0));
			mvwaddnstr(help_, 1 + r, 2, line, w - 4);
			if (heading) wattroff(help_, A_BOLD | (has_colors() ? COLOR_PAIR(3) : 0));
		}
		touchwin(help_);
		wnoutrefresh(help_);
	}
	doupdate();
	last_draw_ = std::chrono::steady_clock::now();
}

void TUI::set_signal_db(double value) {
	if (!std::isfinite(value)) return;
	signal_db_ = value;
	signal_valid_ = true;
}

void TUI::set_channel_db(double value) {
	if (!std::isfinite(value)) return;
	channel_db_ = value;
	channel_valid_ = true;
}

void TUI::adjust(int direction, bool large) {
	hint_.clear();
	auto s = control_.state();
	bool ssb = is_ssb(s.mode);
	if (ssb && selected_ == 2) {
		set_ssb_bw(ssb_bw_index + direction);
		return;
	}
	int field = old_item(selected_, ssb);
	std::ostringstream cmd;
	if (field == 0) {
		double mhz = s.mhz + direction * (ssb ? (large ? 0.010 : 0.001) : (large ? 1.0 : 0.1));
		if (mhz <= 0.0 || mhz > 2000.0) return;
		cmd << "f " << std::fixed << std::setprecision(6) << mhz;
	} else if (field == 1) {
		static const char* modes[] = {"lsb", "usb", "am", "wbfm"};
		int i = 0;
		while (i < 4 && s.mode != modes[i]) ++i;
		cmd << "m " << modes[(i + (direction > 0 ? 1 : 3)) % 4];
	} else if (field == 2) {
		double v = std::clamp(static_cast<double>(s.volume) + direction * (large ? 0.05 : 0.005), 0.0, 2.0);
		cmd << "v " << std::fixed << std::setprecision(3) << v;
	} else if (field == 3) {
		int v = std::clamp(s.if_reduction + direction * (large ? 5 : 1), 20, 59);
		cmd << "if " << v;
	} else if (field == 4) {
		int v = std::clamp(s.lna + direction, 0, lna_max(s.mhz));
		cmd << "lna " << v;
	} else if (field == 5) {
		const char* antennas = "abc";
		int i = 0;
		while (i < 3 && antennas[i] != s.antenna) ++i;
		cmd << "ant " << antennas[(i + (direction > 0 ? 1 : 2)) % 3];
	} else if (field == 6) {
		cmd << "hdr " << (direction > 0 ? 1 : 0);
	} else if (field == 7 || field == 8) {
		cmd << (field == 7 ? "notch " : "dab ") << (direction > 0 ? 1 : 0);
	} else if (field == 9) {
		wf_on = direction > 0;
		return;
	} else if (field == 10) {
		double v = std::clamp(std::round(s.ref) + direction * (large ? 5 : 1), -20.0, 20.0);
		cmd << "ref " << v;
	} else if (field == 11) {
		cmd << "digi " << (direction > 0 ? 1 : 0);
	} else if (field == 12) {		// on: the trigger level; off: right turns it on
		if (!nb_on) cmd << "nb " << (direction > 0 ? 1 : 0);
		else cmd << "nb " << std::clamp(nb_db + direction * (large ? 3 : 1), 8, 20);
	}
	control_.command(cmd.str());
}

void TUI::open_list() {
	list_ = control_.bookmarks();
	list_open_ = true;
	armed_ = false;
	sel_ = top_ = 0;
	if (popup_) { delwin(popup_); popup_ = nullptr; }		// height follows the list
}

void TUI::pick(int i) {
	list_open_ = false;
	if (i < 0 || i >= static_cast<int>(list_.size())) return;
	control_.pick(list_[i]);
	char where[32];
	std::snprintf(where, sizeof where, "%.6f %s", list_[i].hz / 1e6, list_[i].mode.c_str());
	hint_ = "Bookmark: " + (list_[i].name.empty() ? std::string(band_name(list_[i].hz / 1e6)) + " " + where : list_[i].name);
}

bool TUI::help_key(int key) {
	int page = std::max(1, screen_height_ - 3);
	switch (key) {
	case 'j': case KEY_DOWN: help_top_++; break;
	case 'k': case KEY_UP: help_top_--; break;
	case KEY_NPAGE: help_top_ += page; break;
	case KEY_PPAGE: help_top_ -= page; break;
	case KEY_RESIZE: layout(); break;
	case KEY_MOUSE: {
		MEVENT event{};
		if (getmouse(&event) != OK) break;
		if (event.bstate & BUTTON4_PRESSED) help_top_--;
		else if (event.bstate & BUTTON5_PRESSED) help_top_++;
		else if (event.bstate & (BUTTON1_CLICKED | BUTTON1_PRESSED)) help_open_ = false;
		break;
	}
	default: help_open_ = false;
	}
	help_top_ = std::max(0, help_top_);		// draw() clamps the bottom
	return true;
}

bool TUI::list_key(int key) {
	if (renaming_) {
		if (key == 27) renaming_ = false;
		else if (key == '\n' || key == '\r' || key == KEY_ENTER) {
			if (control_.rename(list_[sel_], input_)) {
				int keep = sel_;
				list_ = control_.bookmarks();		// shows the name as stored
				sel_ = std::min(keep, static_cast<int>(list_.size()) - 1);
			}
			renaming_ = false;
		} else if (key == KEY_BACKSPACE || key == 127 || key == 8) {
			if (!input_.empty()) input_.pop_back();
		} else if (key == 21) input_.clear();
		else if (key >= 32 && key <= 126 && input_.size() < 30) input_ += static_cast<char>(key);
		if (!renaming_) input_.clear();
		return true;
	}
	int n = list_.size(), last = std::max(0, n - 1);
	bool was_armed = armed_;
	armed_ = false;
	switch (key) {
	case 'q': case 'b': case 27: list_open_ = false; break;
	case 'k': case KEY_UP: sel_ = std::max(0, sel_ - 1); break;
	case 'j': case KEY_DOWN: sel_ = std::min(last, sel_ + 1); break;
	case KEY_PPAGE: sel_ = std::max(0, sel_ - (screen_height_ - 5)); break;
	case KEY_NPAGE: sel_ = std::min(last, sel_ + (screen_height_ - 5)); break;
	case '\n': case '\r': case KEY_ENTER: case ' ': pick(sel_); break;
	case 'n':
		if (n) { renaming_ = true; input_ = list_[sel_].name; }
		break;
	case 'd': case KEY_DC:
		if (!n) break;
		if (!was_armed) { armed_ = true; break; }
		control_.forget(list_[sel_]);
		list_.erase(list_.begin() + sel_);
		sel_ = std::min(sel_, std::max(0, n - 2));
		if (popup_) { delwin(popup_); popup_ = nullptr; }
		break;
	case KEY_RESIZE: layout(); break;
	case KEY_MOUSE: {
		MEVENT event{};
		if (getmouse(&event) != OK) break;
		int row = top_ + event.y - 2;		// popup top border at screen row 1
		int h = popup_ ? getmaxy(popup_) : 0;
		bool inside = event.y >= 2 && event.y < h && row < n && event.x > button_x && event.x < button_x + list_w - 1;
		if ((event.bstate & (BUTTON3_PRESSED | BUTTON3_CLICKED)) && inside) {		// right-click renames
			sel_ = row;
			renaming_ = true;
			input_ = list_[sel_].name;
		} else if (event.bstate & BUTTON4_PRESSED) sel_ = std::max(0, sel_ - 1);
		else if (event.bstate & BUTTON5_PRESSED) sel_ = std::min(last, sel_ + 1);
		else if (!(event.bstate & (BUTTON1_CLICKED | BUTTON1_PRESSED))) break;
		else if (inside) pick(row);
		else list_open_ = false;		// click elsewhere closes
		break;
	}
	}
	return true;
}

void TUI::start_command(bool prefill) {
	hint_.clear();
	bool ssb = is_ssb(control_.state().mode);
	if (prefill && ssb && selected_ == 2) {
		adjust(1);
		return;
	}
	int field = old_item(selected_, ssb);
	if (prefill && field == 1) {
		adjust(1);
		return;
	}
	if (prefill && field == 9) {
		wf_on = !wf_on;
		return;
	}
	if (prefill && field == 11) {
		control_.command(digi_on ? "digi 0" : "digi 1");
		return;
	}
	if (prefill && field == 12) {
		control_.command(nb_on ? "nb 0" : "nb 1");
		return;
	}
	if (prefill && field >= 6 && field <= 8) {		// hdr, notch, dab toggle
		auto s = control_.state();
		bool enabled = field == 6 ? s.hdr : field == 7 ? s.rf_notch : s.dab_notch;
		const char* name = field == 6 ? "hdr " : field == 7 ? "notch " : "dab ";
		control_.command(std::string(name) + (enabled ? "0" : "1"));
		return;
	}
	entering_ = true;
	input_.clear();
	if (!prefill) return;
	auto s = control_.state();
	std::ostringstream cmd;
	switch (field) {
	case 0: cmd << "f " << std::fixed << std::setprecision(6) << s.mhz; break;
	case 2: cmd << "v " << s.volume; break;
	case 3: cmd << "if " << s.if_reduction; break;
	case 4: cmd << "lna " << s.lna; break;
	case 5: cmd << "ant " << s.antenna; break;
	case 10: cmd << "ref " << s.ref; break;
	}
	input_ = cmd.str();
}

bool TUI::handle(int key) {
	if (entering_) {
		if (key == 27) {
			entering_ = false;
			input_.clear();
		} else if (key == '\n' || key == '\r' || key == KEY_ENTER) {
			entering_ = false;
			if (input_ == "help" || input_ == "h" || input_ == "?") { help_open_ = true; help_top_ = 0; }		// the popup, not Control's one-liner
			else if (!input_.empty() && !control_.command(input_)) return false;
			input_.clear();
		} else if (key == KEY_BACKSPACE || key == 127 || key == 8) {
			if (!input_.empty()) input_.pop_back();
		} else if (key == 21) {
			input_.clear();
		} else if (key >= 32 && key <= 126 && input_.size() < 120) {
			input_ += static_cast<char>(key);
		}
		return true;
	}

	if (help_open_) return help_key(key);
	if (list_open_) return list_key(key);
	bool ssb = is_ssb(control_.state().mode);
	int count = item_count + ssb;
	switch (key) {
	case 'q': return false;
	case 'b': open_list(); break;
	case '?': help_open_ = true; help_top_ = 0; break;
	case 'k': case KEY_UP: hint_.clear(); selected_ = (selected_ + count - 1) % count; break;
	case 'j': case KEY_DOWN: hint_.clear(); selected_ = (selected_ + 1) % count; break;
	case 'h': case KEY_LEFT: adjust(-1); break;
	case 'l': case KEY_RIGHT: adjust(1); break;
	case KEY_SLEFT: adjust(-1, true); break;
	case KEY_SRIGHT: adjust(1, true); break;
	case '\n': case '\r': case KEY_ENTER: case ' ': start_command(true); break;
	case ':': start_command(false); break;
	case 's': case 'S': control_.command("s"); break;
	case 'w': wf_on = !wf_on; break;
	case 'd': control_.command(digi_on ? "digi 0" : "digi 1"); break;
	case 'n': control_.command(nb_on ? "nb 0" : "nb 1"); break;
	case KEY_RESIZE: layout(); break;
	case KEY_MOUSE: {
		MEVENT event{};
		if (getmouse(&event) != OK) break;
		if (event.y == 0 && event.x >= button_x && event.x < button_x + button_w && (event.bstate & (BUTTON1_CLICKED | BUTTON1_PRESSED))) {
			open_list();
			break;
		}
		if (event.y == 0 && event.x >= help_x && event.x < help_x + help_w && (event.bstate & (BUTTON1_CLICKED | BUTTON1_PRESSED))) {
			help_open_ = true;
			help_top_ = 0;
			break;
		}
		if (event.y == screen_height_ - 1) {
			start_command(false);
			break;
		}
		if (event.x >= left_width_) break;
		for (int i = 0; i < count; ++i) {
			if (event.y != item_row(i, ssb)) continue;
			if (selected_ != i) hint_.clear();
			selected_ = i;
			if (event.bstate & BUTTON4_PRESSED) adjust(1);
			else if (event.bstate & BUTTON5_PRESSED) adjust(-1);
			else if (((ssb && i == 2) || old_item(i, ssb) >= 6) &&
				(event.bstate & (BUTTON1_CLICKED | BUTTON1_PRESSED))) start_command(true);
			break;
		}
		break;
	}
	}
	return true;
}

bool TUI::poll() {
	if (!open_) return true;
	int key = getch();
	if (key != ERR) {
		if (!handle(key)) return false;
		draw();
	} else if (std::chrono::steady_clock::now() - last_draw_ >= std::chrono::milliseconds(150)) {
		draw();
	}
	return true;
}
