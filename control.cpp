#include "control.h"
#include "rsp.h"

#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <poll.h>
#include <sstream>
#include <sqlite3.h>
#include <unistd.h>
#include <utility>
#include <initializer_list>

namespace {
const int bandwidth_low[] = {300, 300, 200, 200};		// SSB presets: narrow voice, normal voice, wide voice, digital
const int bandwidth_high[] = {2100, 3100, 3300, 3000};
constexpr int bandwidth_count = 4;
}

int ssb_bw_index = 1;		// receiver switches; were in tui.cpp
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

namespace {
std::string clean_name(std::string s) {		// trims; '' or "" (or quotes around a name) come off
	s.erase(0, s.find_first_not_of(" \t"));
	s.erase(s.find_last_not_of(" \t") + 1);
	if (s.size() >= 2 && (s[0] == '\'' || s[0] == '"') && s.back() == s[0]) s = s.substr(1, s.size() - 2);
	return s;
}

double fine_limit(const std::string &mode) {
	return mode == "wbfm" ? 350000.0 : 210000.0;
}
}

Control::Control(RSP& rsp, float& volume, std::string mode, double frequency_hz,
	double offset_hz, int if_reduction, int lna, char antenna)
	: rsp_(rsp), volume_(volume), mode_(std::move(mode)),
	  hz_(std::llround(frequency_hz)), rf_hz_(frequency_hz + offset_hz), offset_hz_(offset_hz), if_reduction_(if_reduction),
	  lna_(lna), antenna_(antenna) {}

Control::State Control::state() const {
	return {mode_, hz_ / 1e6, rf_hz_ / 1e6, volume_, if_reduction_, lna_, antenna_,
		rsp_.rf_notch(), rsp_.dab_notch(), rsp_.hdr(), rsp_.overload(), rsp_.overload_count(), ref_, zoom_};
}

Control::~Control() {
	if (db_) sqlite3_close(db_);
}

bool Control::open(const std::string& database, bool restore_last) {
	std::string path = database;
	if (path.empty()) {
		// Keep settings alongside am/fm so the database travels with the project.
		std::error_code ec;
		auto executable = std::filesystem::read_symlink("/proc/self/exe", ec);
		path = ec ? "settings.db" : (executable.parent_path() / "settings.db").string();
	}

	const auto parent = std::filesystem::path(path).parent_path();
	if (!parent.empty()) {
		std::error_code ec;
		std::filesystem::create_directories(parent, ec);
		if (ec) {
			fprintf(stderr, "Control: cannot create directory: %s\n", ec.message().c_str());
			return false;
		}
	}

	if (sqlite3_open(path.c_str(), &db_) != SQLITE_OK) {
		fprintf(stderr, "Control: cannot open database: %s\n", sqlite3_errmsg(db_));
		sqlite3_close(db_);
		db_ = nullptr;
		return false;
	}

	const char* sql =
		"CREATE TABLE IF NOT EXISTS settings ("
		" mode TEXT NOT NULL, frequency_hz INTEGER NOT NULL,"
		" frequency_mhz REAL GENERATED ALWAYS AS (frequency_hz / 1000000.0) VIRTUAL,"
		" volume REAL NOT NULL, if_reduction INTEGER NOT NULL,"
		" lna INTEGER NOT NULL, antenna TEXT NOT NULL,"
		" rf_notch INTEGER NOT NULL DEFAULT 0, dab_notch INTEGER NOT NULL DEFAULT 0,"
		" hdr INTEGER NOT NULL DEFAULT 0, ref REAL NOT NULL DEFAULT 0,"
		" bw INTEGER NOT NULL DEFAULT -1, wf INTEGER NOT NULL DEFAULT 0, zoom INTEGER NOT NULL DEFAULT 0, nb INTEGER NOT NULL DEFAULT -1,"
		" name TEXT NOT NULL DEFAULT '', hits INTEGER NOT NULL DEFAULT 1,"
		" updated_at TEXT DEFAULT CURRENT_TIMESTAMP,"
		" PRIMARY KEY(mode, frequency_hz))";
	if (sqlite3_exec(db_, sql, nullptr, nullptr, nullptr) != SQLITE_OK) {
		fprintf(stderr, "Control: cannot create settings table: %s\n", sqlite3_errmsg(db_));
		sqlite3_close(db_);
		db_ = nullptr;
		return false;
	}

	// Add the MHz display column to databases created by older versions.
	sqlite3_stmt* probe = nullptr;
	bool has_mhz = sqlite3_prepare_v2(db_,
		"SELECT frequency_mhz FROM settings LIMIT 0", -1, &probe, nullptr) == SQLITE_OK;
	sqlite3_finalize(probe);
	if (!has_mhz) {
		const char* upgrade = "ALTER TABLE settings ADD COLUMN frequency_mhz "
			"REAL GENERATED ALWAYS AS (frequency_hz / 1000000.0) VIRTUAL";
		if (sqlite3_exec(db_, upgrade, nullptr, nullptr, nullptr) != SQLITE_OK) {
			fprintf(stderr, "Control: cannot add MHz column: %s\n", sqlite3_errmsg(db_));
			sqlite3_close(db_);
			db_ = nullptr;
			return false;
		}
	}

	const char* last_sql =
		"CREATE TABLE IF NOT EXISTS last_frequency ("
		" mode TEXT PRIMARY KEY, frequency_hz INTEGER NOT NULL, saved_at INTEGER NOT NULL DEFAULT 0)";
	if (sqlite3_exec(db_, last_sql, nullptr, nullptr, nullptr) != SQLITE_OK) {
		fprintf(stderr, "Control: cannot create last_frequency table: %s\n", sqlite3_errmsg(db_));
		return false;
	}

	for (auto [table, column, def] : std::initializer_list<std::array<const char*, 3>>{		// columns added after the first release
			{"settings", "rf_notch", "INTEGER NOT NULL DEFAULT 0"}, {"settings", "dab_notch", "INTEGER NOT NULL DEFAULT 0"},
			{"settings", "hdr", "INTEGER NOT NULL DEFAULT 0"}, {"settings", "ref", "REAL NOT NULL DEFAULT 0"},
			{"settings", "bw", "INTEGER NOT NULL DEFAULT -1"}, {"settings", "wf", "INTEGER NOT NULL DEFAULT 0"},
			{"settings", "zoom", "INTEGER NOT NULL DEFAULT 0"}, {"settings", "name", "TEXT NOT NULL DEFAULT ''"},
			{"settings", "hits", "INTEGER NOT NULL DEFAULT 1"},		// existing rows start at 1
			{"settings", "nb", "INTEGER NOT NULL DEFAULT -1"},		// -1: saved before the blanker, leave it alone
			{"last_frequency", "saved_at", "INTEGER NOT NULL DEFAULT 0"}}) {
		sqlite3_stmt* probe = nullptr;
		std::string sql = std::string("SELECT ") + column + " FROM " + table + " LIMIT 0";
		bool exists = sqlite3_prepare_v2(db_, sql.c_str(), -1, &probe, nullptr) == SQLITE_OK;
		sqlite3_finalize(probe);
		if (exists) continue;
		sql = std::string("ALTER TABLE ") + table + " ADD COLUMN " + column + " " + def;
		if (sqlite3_exec(db_, sql.c_str(), nullptr, nullptr, nullptr) != SQLITE_OK) {
			fprintf(stderr, "Control: cannot add %s: %s\n", column, sqlite3_errmsg(db_));
			return false;
		}
	}

	if (restore_last) {		// the newest s/save, any mode; the caller follows mode_ with the right demodulator
		std::string saved_mode = mode_;
		int64_t saved_hz = 0;
		for (const char* sql : {"SELECT mode, frequency_hz FROM last_frequency ORDER BY saved_at DESC LIMIT 1",
				"SELECT mode, frequency_hz FROM settings ORDER BY updated_at DESC, rowid DESC LIMIT 1"}) {
			sqlite3_stmt* stmt = nullptr;
			if (sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr) != SQLITE_OK) return false;
			if (sqlite3_step(stmt) == SQLITE_ROW) {
				saved_mode = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
				saved_hz = sqlite3_column_int64(stmt, 1);
			}
			sqlite3_finalize(stmt);
			if (saved_hz) break;
		}
		if (saved_hz > 0 && saved_hz <= 2000000000LL &&
			rsp_.set_frequency(static_cast<double>(saved_hz) + offset_hz_)) {
			hz_ = saved_hz;
			rf_hz_ = saved_hz + offset_hz_;
			mode_ = saved_mode;
		}
	}

	if (mode_.empty()) {		// no mode given and nothing restored
		mode_ = mode_for(hz_);
		if (mode_.empty()) mode_ = hz_ < 10000000 ? "lsb" : "usb";
	}
	load(restore_last);
	return true;
}

bool Control::load(bool startup) {
	const char* sql = "SELECT volume, if_reduction, lna, antenna, rf_notch, dab_notch, hdr, ref, bw, wf, zoom, nb FROM settings "
		"WHERE mode = ? AND frequency_hz = ?";
	sqlite3_stmt* stmt = nullptr;
	if (sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr) != SQLITE_OK) return false;

	sqlite3_bind_text(stmt, 1, mode_.c_str(), -1, SQLITE_TRANSIENT);
	sqlite3_bind_int64(stmt, 2, hz_);
	bool found = sqlite3_step(stmt) == SQLITE_ROW;
	if (found) {
		float v = static_cast<float>(sqlite3_column_double(stmt, 0));
		int gr = sqlite3_column_int(stmt, 1);
		int lna = sqlite3_column_int(stmt, 2);
		const unsigned char* ant = sqlite3_column_text(stmt, 3);
		char a = ant && ant[0] ? static_cast<char>(ant[0]) : 'a';

		if (std::isfinite(v) && v >= 0.0f && v <= 2.0f) volume_ = v;
		if (rsp_.set_gain(gr, lna)) {
			if_reduction_ = gr;
			lna_ = lna;
		}
		if (rsp_.set_antenna(a)) antenna_ = a;
		rsp_.set_rf_notch(sqlite3_column_int(stmt, 4) != 0);
		rsp_.set_dab_notch(sqlite3_column_int(stmt, 5) != 0);
		rsp_.set_hdr(sqlite3_column_int(stmt, 6) != 0);
		double r = sqlite3_column_double(stmt, 7);
		if (std::isfinite(r) && r >= -20.0 && r <= 20.0) ref_ = r;
		if (sqlite3_column_int(stmt, 8) >= 0) set_ssb_bw(sqlite3_column_int(stmt, 8));		// -1: saved before bw was stored
		if (startup || picking_) wf_on = sqlite3_column_int(stmt, 9) != 0;		// not when tuning merely passes a saved frequency
		int z = sqlite3_column_int(stmt, 10);
		if (startup && z >= 0 && z <= 2) zoom_ = z;		// tuning never changes the view; only the zoom buttons do
		int nb = sqlite3_column_int(stmt, 11);		// -1: saved before the blanker; 0 off; else its trigger dB
		if (nb >= 0) nb_on = nb > 0;
		if (nb >= 8 && nb <= 20) nb_db = nb;
		if (!ui_active_) fprintf(stderr, "Control: restored %s %.6f MHz\n", mode_.c_str(), hz_ / 1e6);
	}
	sqlite3_finalize(stmt);
	return found;
}

std::string Control::saved_mode(int64_t hz) {
	std::string m;
	sqlite3_stmt* stmt = nullptr;
	if (db_ && sqlite3_prepare_v2(db_, "SELECT mode FROM settings WHERE frequency_hz = ? "
			"ORDER BY updated_at DESC, rowid DESC LIMIT 1", -1, &stmt, nullptr) == SQLITE_OK) {
		sqlite3_bind_int64(stmt, 1, hz);
		if (sqlite3_step(stmt) == SQLITE_ROW) m = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
		sqlite3_finalize(stmt);
	}
	return m;
}

std::string Control::mode_for(int64_t hz) {
	std::string m = saved_mode(hz);
	if (m.empty() && band_mode(hz / 1e6)) m = band_mode(hz / 1e6);
	return m;
}

bool Control::remember_frequency() {
	if (!db_) return false;
	const char* sql =
		"INSERT INTO last_frequency (mode, frequency_hz, saved_at) "
		"VALUES (?, ?, (SELECT IFNULL(MAX(saved_at), 0) + 1 FROM last_frequency)) "
		"ON CONFLICT(mode) DO UPDATE SET frequency_hz = excluded.frequency_hz, saved_at = excluded.saved_at";
	sqlite3_stmt* stmt = nullptr;
	if (sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr) != SQLITE_OK) return false;
	sqlite3_bind_text(stmt, 1, mode_.c_str(), -1, SQLITE_TRANSIENT);
	sqlite3_bind_int64(stmt, 2, hz_);
	bool ok = sqlite3_step(stmt) == SQLITE_DONE;
	sqlite3_finalize(stmt);
	return ok;
}

bool Control::save(const std::string& name) {
	if (!db_) {
		fprintf(stderr, "Control: database not open\n");
		return false;
	}
	const char* sql =
		"INSERT INTO settings (mode, frequency_hz, volume, if_reduction, lna, antenna, rf_notch, dab_notch, hdr, ref, bw, wf, zoom, name, nb) "
		"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
		"ON CONFLICT(mode, frequency_hz) DO UPDATE SET "
		"volume=excluded.volume, if_reduction=excluded.if_reduction, "
		"lna=excluded.lna, antenna=excluded.antenna, "
		"rf_notch=excluded.rf_notch, dab_notch=excluded.dab_notch, hdr=excluded.hdr, ref=excluded.ref, bw=excluded.bw, wf=excluded.wf, zoom=excluded.zoom, nb=excluded.nb, "
		"name=CASE excluded.name WHEN '' THEN settings.name ELSE excluded.name END, "		// hits untouched: a re-save keeps its count
		"updated_at=CURRENT_TIMESTAMP";
	sqlite3_stmt* stmt = nullptr;
	if (sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr) != SQLITE_OK) return false;
	sqlite3_bind_text(stmt, 1, mode_.c_str(), -1, SQLITE_TRANSIENT);
	sqlite3_bind_int64(stmt, 2, hz_);
	sqlite3_bind_double(stmt, 3, volume_);
	sqlite3_bind_int(stmt, 4, if_reduction_);
	sqlite3_bind_int(stmt, 5, lna_);
	char ant[2] = {antenna_, '\0'};
	sqlite3_bind_text(stmt, 6, ant, -1, SQLITE_TRANSIENT);
	sqlite3_bind_int(stmt, 7, rsp_.rf_notch() ? 1 : 0);
	sqlite3_bind_int(stmt, 8, rsp_.dab_notch() ? 1 : 0);
	sqlite3_bind_int(stmt, 9, rsp_.hdr() ? 1 : 0);
	sqlite3_bind_double(stmt, 10, ref_);
	sqlite3_bind_int(stmt, 11, ssb_bw_index);
	sqlite3_bind_int(stmt, 12, wf_on ? 1 : 0);
	sqlite3_bind_int(stmt, 13, zoom_);
	sqlite3_bind_text(stmt, 14, name.c_str(), -1, SQLITE_TRANSIENT);
	sqlite3_bind_int(stmt, 15, nb_on ? nb_db : 0);
	bool ok = sqlite3_step(stmt) == SQLITE_DONE;
	if (!ok)
		fprintf(stderr, "Control: save failed: %s\n", sqlite3_errmsg(db_));
	sqlite3_finalize(stmt);
	if (ok) ok = remember_frequency();
	if (ok && !ui_active_) fprintf(stderr, "Control: saved %s %.6f MHz\n", mode_.c_str(), hz_ / 1e6);
	return ok;
}

std::vector<Control::Bookmark> Control::bookmarks() {
	std::vector<Bookmark> out;
	sqlite3_stmt* stmt = nullptr;
	if (!db_ || sqlite3_prepare_v2(db_, "SELECT frequency_hz, mode, name, hits FROM settings "
			"ORDER BY hits DESC, frequency_hz", -1, &stmt, nullptr) != SQLITE_OK) return out;
	while (sqlite3_step(stmt) == SQLITE_ROW)
		out.push_back({sqlite3_column_int64(stmt, 0), reinterpret_cast<const char*>(sqlite3_column_text(stmt, 1)),
			reinterpret_cast<const char*>(sqlite3_column_text(stmt, 2)), sqlite3_column_int(stmt, 3)});
	sqlite3_finalize(stmt);
	return out;
}

void Control::pick(const Bookmark& b) {
	sqlite3_stmt* stmt = nullptr;
	if (db_ && sqlite3_prepare_v2(db_, "UPDATE settings SET hits = hits + 1 WHERE mode = ? AND frequency_hz = ?",
			-1, &stmt, nullptr) == SQLITE_OK) {
		sqlite3_bind_text(stmt, 1, b.mode.c_str(), -1, SQLITE_TRANSIENT);
		sqlite3_bind_int64(stmt, 2, b.hz);
		sqlite3_step(stmt);
		sqlite3_finalize(stmt);
	}
	char f[32];
	std::snprintf(f, sizeof f, "f %.6f", b.hz / 1e6);
	command("m " + b.mode);		// mode first, so f restores the row saved in that mode
	picking_ = true;
	command(f);
	picking_ = false;
}

bool Control::rename(const Bookmark& b, const std::string& name) {
	sqlite3_stmt* stmt = nullptr;
	if (!db_ || sqlite3_prepare_v2(db_, "UPDATE settings SET name = ? WHERE mode = ? AND frequency_hz = ?", -1, &stmt, nullptr) != SQLITE_OK)
		return false;
	std::string n = clean_name(name);
	sqlite3_bind_text(stmt, 1, n.c_str(), -1, SQLITE_TRANSIENT);
	sqlite3_bind_text(stmt, 2, b.mode.c_str(), -1, SQLITE_TRANSIENT);
	sqlite3_bind_int64(stmt, 3, b.hz);
	bool ok = sqlite3_step(stmt) == SQLITE_DONE;
	sqlite3_finalize(stmt);
	return ok;
}

bool Control::forget(const Bookmark& b) {
	sqlite3_stmt* stmt = nullptr;
	if (!db_ || sqlite3_prepare_v2(db_, "DELETE FROM settings WHERE mode = ? AND frequency_hz = ?", -1, &stmt, nullptr) != SQLITE_OK)
		return false;
	sqlite3_bind_text(stmt, 1, b.mode.c_str(), -1, SQLITE_TRANSIENT);
	sqlite3_bind_int64(stmt, 2, b.hz);
	bool ok = sqlite3_step(stmt) == SQLITE_DONE;
	sqlite3_finalize(stmt);
	return ok;
}

void Control::set_offset(double offset_hz) {
	offset_hz_ = offset_hz;
	double rf = static_cast<double>(hz_) + offset_hz_;
	if (rsp_.set_frequency(rf)) rf_hz_ = rf;
}

namespace {
struct Band { double lo, hi; const char* mode; const char* name; };
const Band bands[] = {		// MHz; first match wins, so ham bands come before overlapping broadcast bands
	{1.8, 2.0, "lsb", "160m"}, {3.5, 4.0, "lsb", "80m"}, {5.33, 5.41, "usb", "60m"}, {7.0, 7.3, "lsb", "40m"},
	{10.1, 10.15, "usb", "30m"}, {14.0, 14.35, "usb", "20m"}, {18.068, 18.168, "usb", "17m"}, {21.0, 21.45, "usb", "15m"},
	{24.89, 24.99, "usb", "12m"}, {28.0, 29.7, "usb", "10m"}, {50.0, 54.0, "usb", "6m"},
	{0.53, 1.71, "am", "MW"}, {2.3, 2.495, "am", "120m SW"}, {3.2, 3.4, "am", "90m SW"}, {4.75, 5.06, "am", "60m SW"},
	{5.9, 6.2, "am", "49m SW"}, {7.2, 7.6, "am", "41m SW"}, {9.4, 9.9, "am", "31m SW"}, {11.6, 12.1, "am", "25m SW"},
	{13.57, 13.87, "am", "22m SW"}, {15.1, 15.8, "am", "19m SW"}, {17.48, 17.9, "am", "16m SW"}, {18.9, 19.02, "am", "15m SW"},
	{21.45, 21.85, "am", "13m SW"}, {25.67, 26.1, "am", "11m SW"}, {26.965, 27.405, "am", "CB"}, {118.0, 137.0, "am", "Air"},
	{87.5, 108.0, "wbfm", "FM"},
};
const Band* band(double mhz) {
	for (const auto& b : bands)
		if (mhz >= b.lo && mhz < b.hi) return &b;
	return nullptr;
}
}

const char* band_mode(double mhz) { auto b = band(mhz); return b ? b->mode : nullptr; }
const char* band_name(double mhz) { auto b = band(mhz); return b ? b->name : ""; }

void Control::status() const {
	if (ui_active_) return;
	fprintf(stderr, "Control: %s %.6f MHz  volume=%.3f  IF reduction=%d dB  LNA=%d  antenna=%c\n",
		mode_.c_str(), hz_ / 1e6, volume_, if_reduction_, lna_, antenna_);
}

bool Control::command(const std::string& line) {
	std::istringstream in(line);
	std::string op, extra;
	if (!(in >> op)) return true;
	notice_.clear();

	if (op == "q" || op == "quit") return false;
	if (op == "status") { status(); notice_ = "Settings refreshed"; return true; }
	if (op == "help" || op == "h" || op == "?") {
		notice_ = "f MHz | m lsb/usb/am/wbfm | v volume | if dB | lna state | ant a/b/c | hdr 0/1 | notch 0/1 | dab 0/1 | ref dB | zoom 0-2 | bw 0-3 | digi 0/1 | nb 0/1/dB | s [name] | q";
		if (!ui_active_) fprintf(stderr, "Commands: f <MHz>, m <lsb|usb|am|wbfm>, v <volume>, g <IF-reduction> <LNA>, if <dB>, lna <state>, ant <a|b|c>, hdr <0|1>, notch <0|1>, dab <0|1>, ref <dB>, zoom <0-2>, bw <0-3>, digi <0|1>, nb <0|1|8-20>, s/save [name], status, q\n");
		return true;
	}

	if (op == "save" || op == "s") {		// s [name]
		std::string name;
		std::getline(in >> std::ws, name);
		notice_ = save(clean_name(name)) ? "Settings saved" : "Save failed";
		list_wanted_ = true;
		return true;
	}

	if (op == "bm") {		// rx -n: send the bookmark list
		list_wanted_ = true;
		return true;
	}

	if (op == "bmpick" || op == "bmname" || op == "bmdel") {		// bmpick HZ MODE, bmname HZ MODE [name], bmdel HZ MODE
		int64_t hz = 0;
		std::string m, name;
		if (!(in >> hz >> m)) { notice_ = "Usage: " + op + " HZ MODE"; return true; }
		std::getline(in >> std::ws, name);
		for (auto &b : bookmarks())
			if (b.hz == hz && b.mode == m) {
				if (op == "bmpick") pick(b);
				else if (op == "bmname") notice_ = rename(b, name) ? "Bookmark renamed" : "Rename failed";
				else notice_ = forget(b) ? "Bookmark deleted" : "Delete failed";
				list_wanted_ = true;		// hits, names or rows changed
				return true;
			}
		notice_ = "No such bookmark";
		return true;
	}

	if (op == "bw") {		// SSB bandwidth preset: 0 narrow voice, 1 normal voice, 2 wide voice, 3 digital
		int i;
		if ((in >> i) && !(in >> extra) && i >= 0 && i <= 3) {
			set_ssb_bw(i);
			notice_ = "Bandwidth updated";
		} else notice_ = "Usage: bw 0-3";
		return true;
	}

	if (op == "digi") {
		std::string value;
		if ((in >> value) && !(in >> extra) && (value == "0" || value == "1" || value == "on" || value == "off")) {
			digi_on = value == "1" || value == "on";
			notice_ = digi_on ? "Digital out on: UDP 127.0.0.1:7355" : "Digital out off";
		} else notice_ = "Usage: digi 0/1";
		return true;
	}

	if (op == "nb") {		// nb 0|1|off|on, or nb 8-20: on at that trigger level (dB over the running power)
		std::string value;
		int db = 0;
		if ((in >> value) && !(in >> extra) && (value == "0" || value == "1" || value == "on" || value == "off" ||
				(std::sscanf(value.c_str(), "%d", &db) == 1 && db >= 8 && db <= 20))) {
			nb_on = value != "0" && value != "off";
			if (db >= 8) nb_db = db;
			notice_ = nb_on ? "Noise blanker on, " + std::to_string(nb_db) + " dB" : "Noise blanker off";
		} else notice_ = "Usage: nb 0/1, or nb 8-20 (dB)";
		return true;
	}

	if (op == "notch" || op == "dab" || op == "hdr") {
		std::string value;
		if ((in >> value) && !(in >> extra) && (value == "0" || value == "1" || value == "on" || value == "off")) {
			bool enable = value == "1" || value == "on";
			bool ok = op == "notch" ? rsp_.set_rf_notch(enable) :
				op == "dab" ? rsp_.set_dab_notch(enable) : rsp_.set_hdr(enable);
			notice_ = ok ? (op == "hdr" ? "HDR updated" : "Notch updated") :
				op == "hdr" ? "HDR unavailable at this frequency" : "Notch update failed";
			return true;
		}
		notice_ = "Usage: notch 0/1, dab 0/1, or hdr 0/1";
		return true;
	}

	if (op == "f") {
		double mhz;
		if ((in >> mhz) && !(in >> extra) && std::isfinite(mhz)
			&& mhz > 0.0 && mhz <= 2000.0) {
			int64_t next_hz = std::llround(mhz * 1e6);
			double limit = fine_limit(mode_), d = next_hz - rf_hz_;
			double rf = std::abs(d) <= limit ? rf_hz_		// inside the span: only the NCO moves
				: std::abs(d) <= limit + 50000 ? next_hz - std::copysign(limit, d)		// just past the edge: drag the LO along so the view pans
				: static_cast<double>(next_hz) + offset_hz_;		// a jump: the usual offset
			bool was_hdr = rsp_.hdr();
			if (was_hdr && rf >= 2e6 && !rsp_.set_hdr(false)) {
				notice_ = "Cannot disable HDR for this frequency";
				return true;
			}
			bool retune = rf != rf_hz_;
			if (!retune || rsp_.set_frequency(rf)) {
				rf_hz_ = rf;
				hz_ = next_hz;		// mode stays; only the user changes it
				if (db_ && !load())
					if (!ui_active_) fprintf(stderr, "Control: no saved settings for %s %.6f MHz; keeping current settings\n",
						mode_.c_str(), hz_ / 1e6);
				notice_ = "Frequency updated";		// only s/save makes it the startup default
				status();
				return true;
			}
			if (was_hdr && !rsp_.hdr()) rsp_.set_hdr(true);
		}
		notice_ = "Invalid frequency or tuning failed";
		if (!ui_active_) fprintf(stderr, "Control: invalid frequency or tuning failed\n");
		return true;
	}

	if (op == "m") {		// the receiver builds the matching demodulator
		std::string m;
		if ((in >> m) && !(in >> extra) && (m == "lsb" || m == "usb" || m == "am" || m == "wbfm")) {
			mode_ = m;
			notice_ = "Mode updated";
			status();
			return true;
		}
	} else if (op == "zoom") {
		int z;
		if ((in >> z) && !(in >> extra) && z >= 0 && z <= 2) {
			zoom_ = z;
			notice_ = "Waterfall zoom updated";
			return true;
		}
	} else if (op == "ref") {
		double value;
		if ((in >> value) && !(in >> extra) && std::isfinite(value) && value >= -20.0 && value <= 20.0) {
			ref_ = value;
			notice_ = "Waterfall ref updated";
			return true;
		}
	} else if (op == "v") {
		float value;
		if ((in >> value) && !(in >> extra) && std::isfinite(value) && value >= 0.0f && value <= 2.0f) {
			volume_ = value;
			notice_ = "Volume updated";
			status();
			return true;
		}
	} else if (op == "g" || op == "if" || op == "lna") {
		int gr = if_reduction_, lna = lna_;
		bool valid = false;
		if (op == "g") valid = bool(in >> gr >> lna);
		else if (op == "if") valid = bool(in >> gr);
		else valid = bool(in >> lna);
		if (valid && !(in >> extra) && rsp_.set_gain(gr, lna)) {
			if_reduction_ = gr;
			lna_ = lna;
			notice_ = "Gain updated";
			status();
			return true;
		}
	} else if (op == "ant") {
		char a;
		if ((in >> a) && !(in >> extra) && rsp_.set_antenna(a)) {
			antenna_ = a;
			notice_ = "Antenna updated";
			status();
			return true;
		}
	}

	notice_ = "Invalid command or setting";
	if (!ui_active_) fprintf(stderr, "Control: invalid command or setting (type 'help')\n");
	return true;
}

bool Control::poll() {
	if (stdin_closed_) return true;
	const auto now = std::chrono::steady_clock::now();
	if (now - last_poll_ < std::chrono::milliseconds(50)) return true;
	last_poll_ = now;

	pollfd fd = {STDIN_FILENO, POLLIN, 0};
	if (::poll(&fd, 1, 0) <= 0 || !(fd.revents & (POLLIN | POLLHUP))) return true;

	char buf[256];
	ssize_t n = ::read(STDIN_FILENO, buf, sizeof(buf));
	if (n == 0) { stdin_closed_ = true; return true; }
	if (n < 0) return true;

	for (ssize_t i = 0; i < n; ++i) {
		if (buf[i] == '\n' || buf[i] == '\r') {
			if (!command(input_)) return false;
			input_.clear();
		} else if (input_.size() < 512) {
			input_ += buf[i];
		}
	}
	return true;
}
