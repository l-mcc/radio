# radio

A Linux ham shack in Python: one window (`shack.py`) that runs the radio, a set of digital-mode decoders, and a few tools for VARA / Winlink logs.

Developed on Debian 12 (bookworm). Other recent Debian/Ubuntu/Mint should work.

| file | what it is |
|---|---|
| `shack.py` | the program: radio control, waterfall, decoders, bookmarks (PyQt6) |
| `civ.py` | Icom CI-V engine (IC-705, Xiegu X6200), PTT port for xara |
| `cat.py` | Yaesu CAT engine (FTdx10) |
| `thd.py` | Kenwood TH-D75 engine (receive only so far) |
| `decode.py` | RTTY, CW, PSK31, Olivia/Contestia decoders (used by shack; also runs on its own) |
| `rspdx` (`*.cpp`) | C++ receiver for the SDRplay RSPdx, run by shack. Only needed if you have one |
| `pvara.py` | VARA log (`vara.log`) -> one readable line per transmission |
| `rms.py` | Winlink RMS gateway list -> `rms.db` (SQLite), with distance/bearing |
| `styles.py`, `consts.py`, `utils.py` | shared bits |

## Radios

Pick the radio in shack's **Radio** list. shack finds it by its USB name under `/dev/serial/by-id/`, so the cable just has to be plugged in.

- **Icom IC-705**: USB cable. CI-V USB baud 115200. Band scope / waterfall in shack.
- **Xiegu X6200**: USB cable (CI-V on the second serial port; the first is the radio's own console).
- **Yaesu FTdx10**: USB cable, CAT rate 38400.
- **Kenwood TH-D75**: USB-C cable. Dial, mode, bands A/B and bookmarks so far; TNC not in KISS mode.
- **SDRplay RSPdx**: needs the C++ part built (see the end).

Receive audio for the decoders comes from the radio's USB sound card (ALSA). Nothing to set up if the radio is plugged in.

## Install

1. System packages:
   ```
   sudo apt install git python3-venv python3-pip
   ```
2. Your user needs the serial ports (log out and back in afterwards):
   ```
   sudo usermod -aG dialout $USER
   ```
3. Clone:
   ```
   git clone https://github.com/l-mcc/radio
   cd radio
   ```
4. Python packages, in a venv so they don't touch the system's:
   ```
   python3 -m venv ~/radio-venv
   ~/radio-venv/bin/pip install PyQt6 pyserial numpy aiohttp
   ```

## Run

```
~/radio-venv/bin/python shack.py
```

Pick the radio. **Help** (or F1) explains every control. Right-click the decoder line, the spectrum strip and the decoded text for more settings.

Options: `python shack.py -h`. `--ptt 0` turns off the PTT port (4532) that xara uses.

### Decoders

Pick one in the **Decoder** list. The first four are built in, the others run an outside program that shack looks for on your PATH (or in `~/installed/builds/...`):

| decoder | needs |
|---|---|
| RTTY, CW, PSK31, Olivia | nothing (numpy) |
| VARA | `xara-monitor` from [xara](https://github.com/x0rro/xara) (Rust): `sudo apt install libasound2-dev libpulse-dev pkg-config cargo`, then `cargo build --release --locked` in its folder |
| ARDOP | `ardopcf` from [pflarue/ardop](https://github.com/pflarue/ardop) (`make`; needs `libasound2-dev`), plus `sudo apt install libasound2-plugins` |
| Packet | `direwolf` (`sudo apt install direwolf`, or build from source) |
| JS8 | `js8call` (`sudo apt install js8call`). Setup is in shack's Help |
| FT4, FT8, WSPR | `jt9` and `wsprd` from `sudo apt install wsjtx` |

The **Log** button writes what's decoded to `vara.log`, `psk31.log`, ... next to shack.py.

### VARA / Winlink tools

```
python3 pvara.py -q vara.log       # read a VARA log, one line per transmission (-h for more examples)
python3 rms.py                     # needs Pat (getpat.io): fetches the RMS list into rms.db
```

## Updating

```
git pull
```

If you changed files yourself, `git pull` may refuse. `git stash`, `git pull`, `git stash pop` puts your changes back on top.

## Files shack makes

Next to shack.py, not in git (you may want a `.gitignore` for them): `shack.log`, `bookmarks.db`, `decode.json`, `calls.txt`, `*.log`, `rms.db`, `__pycache__/`.

## Transmitting

shack's PTT only keys inside the ham bands, and unkeys after 30 s. Start into a dummy load at low power.

## RSPdx (optional)

Only if you have an SDRplay RSPdx.

1. SDRplay API 3.x from sdrplay.com (its installer starts the `sdrplay` service).
2. Build tools and libraries:
   ```
   sudo apt install build-essential libpulse-dev libsqlite3-dev autoconf automake libtool wget
   ```
3. liquid-dsp 1.8.3 from source (github.com/jgaeddert/liquid-dsp), installed to `/usr/local`.
4. RNNoise from source:
   ```
   git clone https://gitlab.xiph.org/xiph/rnnoise.git && cd rnnoise
   ./autogen.sh
   ./configure CFLAGS="-O3 -march=native" --prefix=/usr/local --disable-doc
   make && sudo make install && sudo ldconfig
   ```
5. In the radio folder: `./build.sh` (builds `rspdx`, then installs a copy to `/usr/local/bin` with sudo).
