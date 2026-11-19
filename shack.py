#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, base64, email.header, json, logging, os, re, shutil, signal, socket, sqlite3, struct, subprocess, sys, tempfile, time, urllib.request, wave
from pathlib import Path

import serial
from PyQt6.QtCore import pyqtSignal, QEvent, QPointF, QProcess, QProcessEnvironment, QRect, QSize, Qt, QTimer, QUrl
from PyQt6.QtNetwork import QAbstractSocket, QNetworkAccessManager, QNetworkReply, QNetworkRequest, QTcpSocket
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QImage, QKeySequence, QPainter, QPalette, QPen, QPolygonF, QShortcut, QTextCursor
from PyQt6.QtWidgets import (QApplication, QComboBox, QDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout, QHeaderView, QFileDialog, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
	QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QSlider, QSpinBox, QSplitter, QStatusBar, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)
from prodict import Prodict

import utils
import cat, civ, d75, styles


try:		# decoders need numpy; without it shack still runs, the Decoder row says why
	import numpy as np
	import decode
	NODECODE = None
except ImportError as e:
	decode, NODECODE = None, str(e)

HERE = Path(__file__).resolve().parent
log = logging.getLogger('shack')		# set up in main by utils.setup_logging

HELP = '''
<h3>Radio</h3>
<p>Pick IC-705, RSPdx, Xiegu x6200, FTdx10 or TH-D75 to open it, none to close it. Only one radio is open at a time; only its own controls are shown. A greyed-out one doesn't apply in the current mode (Bandwidth outside SSB, for example).</p>

<h3>Frequency</h3>
<p>Like the waterfall page: the mouse wheel over any digit tunes that digit (the dimmed leading zeros too, for big jumps); clicking a digit's top half steps it up, its bottom half down. Arrow keys: left/right picks a digit, up/down tunes it. Or just type a frequency in MHz (7.074) and press Enter; Esc cancels. The radio's own changes don't overwrite what you're typing.</p>

<h3>RSPdx</h3>
<table cellspacing="4">
<tr><td><b>Mode</b></td><td>LSB, USB, AM, WBFM (broadcast FM)</td></tr>
<tr><td><b>Volume</b></td><td>0 to 1</td></tr>
<tr><td><b>AGC</b></td><td>SSB only (AM levels itself on the carrier). Fast: back up in 100 ms; Mid: holds 0.5 s, back up in 0.5 s; Slow: holds 1 s, back up in 2 s (steadier for FT8, JS8, WSPR); Off: the gain stays where it was when you switched it off, so set the level with IF reduction. Kept in bookmarks</td></tr>
<tr><td><b>IF reduction</b></td><td>20-59 dB; more = less gain</td></tr>
<tr><td><b>LNA reduction</b></td><td>0 = most gain; the top end depends on the band</td></tr>
<tr><td><b>Antenna</b></td><td>A, B or C</td></tr>
<tr><td><b>Bandwidth</b></td><td>SSB only. Narrow 300-2100 Hz, Normal 300-3100, Wide 200-3300, Digital 200-3000 (for decoders)</td></tr>
<tr><td><b>Overload</b></td><td>red while the RSPdx reports RF overload; the count is since it started</td></tr>
<tr><td><b>ADC</b></td><td>everything coming into the RSPdx. Red above -6 dBFS: raise IF or LNA reduction</td></tr>
<tr><td><b>Signal</b></td><td>level inside the passband, dBFS</td></tr>
</table>

<h3>Buttons (green = on)</h3>
<table cellspacing="4">
<tr><td><b>RF notch, DAB notch</b></td><td>the RSPdx's MW/FM broadcast and DAB band filters</td></tr>
<tr><td><b>HDR</b></td><td>high dynamic range mode, below 2 MHz only</td></tr>
<tr><td><b>Digital out</b></td><td>48 kHz 16-bit mono audio as UDP to 127.0.0.1:7355, for decode.py, xara-monitor, direwolf</td></tr>
<tr><td><b>Waterfall</b></td><td>web page at http://localhost:8700/ (opens a browser if none connects)</td></tr>
<tr><td><b>Noise blanker</b></td><td>SSB and AM. The dB box is the trigger level: lower catches more, and changing it turns the blanker on. /s is pulses blanked per second: about 1/s for the electric fence; dozens on plain noise means the level is too low</td></tr>
<tr><td><b>Noise reduction</b></td><td>RNNoise, a neural network trained on speech: takes the hiss and noise floor out from under voice. Like SDRconnect's NR it won't dig very weak signals out of heavy noise, and it can chop up music and CW, so turn it off for those (it's off in WBFM). Digital out (the decoders) gets the audio before it</td></tr>
<tr><td><b>Save</b></td><td>bookmarks frequency, mode and settings (or updates the bookmark already there)</td></tr>
</table>

<h3>Decoder</h3>
<p>Pick RTTY, CW, PSK31, Olivia, VARA, ARDOP, PACTOR, Packet, JS8, FT4, FT8 or WSPR. With the RSPdx it turns Digital out on and decodes that; with the IC-705 or Xiegu it decodes the radio's USB sound card (the radio's receive audio, read with arecord; use a -D mode or USB with a filter wide enough for the signal). It decodes it (decode.py's decoders; VARA by xara-monitor, ARDOP by ardopcf, packet by direwolf, JS8 by JS8Call), text scrolling below. Use SSB with the Digital bandwidth. It tunes itself to the strongest signal in the passband and follows it; a new station starts a new line. The strip under the row is the audio spectrum the decoder sees (300-2900 Hz): click a signal to lock onto it (RTTY: either tone), the wheel nudges 10 Hz, right-click for Auto tune (back to following the strongest). To keep auto tune off a loud neighbor, right-click the strip beside it: Ignore above (or below) that frequency; the ignored part is dimmed, auto tune stays out of it (a click still tunes there), Search the whole strip undoes it. Remembered for each of RTTY, CW, PSK31 and Olivia. The dashed line is the squelch; the marker on the signal (two for RTTY) is green while decoding. The status line says auto or locked. The dBFS figure before the status is the loudest the radio's audio got in the last second (0 = full scale): orange within 3 dB of the top, red when it clips; with AGC off, keep it out of the red with RF gain. The dB box is the squelch: decoding starts when the signal is that far above the noise and stops 3 dB lower. The line next to it shows the tone frequency, signal to noise, open/closed, and for RTTY the alternation (near -1 = real RTTY), for CW the speed, for PSK31 the quality (how clean the phase is: near 1 = a good signal, about 0.64 = noise; it decodes above 0.8). PSK31: live keyboard chat around 14.070, 7.070, 3.580 (USB); the strip shows each signal as a narrow hump about 60 Hz wide: click one to read it (it fine-tunes itself), or leave Auto tune on for the strongest. Olivia: the broad, steady, warbling signals (often 250 to 1000 Hz wide, around 14.072-14.076 and 7.070-7.075 USB) that sound like a choir of beeps; slow (1.5 to 2.5 characters a second) but it reads through noise and fading far below where PSK31 gives up. Pick the mode that matches the signal's width on the strip, right-click > Olivia: 8/250 and 16/500 are the common ones, 32/1000 the widest (the first number is tones, the second the width in Hz); the shaded part of the strip is the width it decodes and should sit over the signal. A wrong mode decodes nothing. It finds the signal (auto: the strongest stretch that wide, not a narrow carrier) and the timing itself, and searches about 125 Hz either side of the shading for the exact frequency, so a click near the middle of the signal is enough. It takes a few blocks to lock (8/250: about 8 s) and then prints those too; text comes a block at a time. The status line says the mode, the frequency, S/N (the code's own measure: above 4 it decodes, noise reads about 3) and searching or decoding. Contestia (same menu) is the faster sibling, upper case only. Right-click the row, the strip or the text for Auto tune, RTTY settings (presets, baud, shift, mark low) and Clear. Scroll up to read back; it stops following until you scroll to the bottom again. Double-click a callsign (or right-click it > Look up) to see its QRZ page. Lookups go to one ungoogled Chromium window of shack's own (its own profile, shack-qrz under ~/.var/app/io.github.ungoogled_software.ungoogled_chromium/config: log in to QRZ there once), reused for every lookup and opened again if you close it. VARA: USB, Digital bandwidth, signal centered (VARA sits at 1500 Hz audio; the shaded part of the strip is its bandwidth, right-click > VARA to change it: 500, 2300 or 2750 Hz). Every packet xara-monitor hears is a line: calls, frame types, SN, KISS text. Log (on the Decoder line, remembered) appends everything the text box shows to a file next to shack.py named for the decoder: vara.log, ardop.log, rtty.log, cw.log, packet.log, js8.log, with a dated header each time logging starts or the decoder changes. A station it can't name shows as #hash (a code made from the callsign); calls.txt next to shack.py lists the callsigns it should name, read each time VARA starts. shack adds every callsign xara-monitor decodes (CQ, header and link lines, and 'de CALL' in VarAC text); add your own and your regulars by hand, one per line. It needs xara-monitor on the PATH or in ~/installed/builds/xara/target/release. Right-click > VARA for more: Full payloads shows each frame's whole data instead of its first 100 characters; Unconfirmed frames too adds frames whose calls it detected but couldn't confirm (more lines, some of them wrong). ARDOP (Winlink's other HF mode, often heard near the VARA frequencies; tune it to 1500 Hz audio like VARA): by ardopcf in its receive-only mode, which decodes every frame it hears, whoever's session it is. Like JS8, shack plays the audio into a sound device of its own (shack-ARDOP) and ardopcf listens to it (through ALSA's pulse device: if ardopcf stops at once, apt install libasound2-plugins). Each frame is a line: UTC, [session ID] (FF = not in a session: calls, pings, ID frames), frame type (its name says the modulation and bandwidth: 4PSK.500.100.E), Q (decode quality, out of 100; on DataACK / DataNAK the other side's), then the callers (ConReq: who > whom, Ping), the station's call and grid on ID frames (with distance and bearing from your grid), or the data as text under it (Winlink mail itself is compressed: shown as binary). The shaded part of the strip follows the bandwidth of the last frame. An idle link trades DataACK and IDLE frames every few seconds: hidden unless you untick right-click > ARDOP > Hide. It needs ardopcf (github.com/pflarue/ardop: make) on the PATH or in ~/installed/builds/ardop. PACTOR (Winlink's and SailMail's older mode: chirping bursts back and forth, 7.10x and up): by SCS's pmon, which only runs on a Raspberry Pi (spi, Decoders.PMON): shack sends the audio there over ssh (a key, no password) into its ALSA loopback (snd-aloop) and shows what pmon prints. PACTOR-1, 2 and 3, Winlink mail unpacked; tune the signal to 1500 Hz audio (the shaded part: PACTOR-1's two tones sit inside it), pmon works out the sideband. Packet: AX.25, FX.25 and IL2P, by direwolf (on the PATH or in ~/installed/builds/direwolf/build/src); right-click > Packet: 300 baud (HF: tones 1600/1800 Hz, either sideband, tune so they sit in the shaded part) or 1200 baud (VHF FM, needs an FM radio). Each packet is a line: who to whom, via, text; APRS ones get a second line decoding it. direwolf complains about packets that aren't APRS (BBS and node IDs: 'Unknown APRS Data Type', 'Use of ID is obsolete'); those lines are hidden unless you untick right-click > Packet > Hide complaints. Right-click > Packet > One line per packet (remembered) shows them the old way instead, Larry's own Direwolf parser: each packet one numbered line, from -> to (and via), text, a text's lines under each other; direwolf's audio level lines go (the Packet tab still gets them), everything else shows as it comes. Right-click > Packet > Transmit (a transceiver, with the PTT port on) lets direwolf transmit too, as N7RTS (Modem.CALL): shack puts the radio in the data version of its sideband (LSB-D or USB-D: only there does the radio send the USB audio), split off, and starts direwolf again with an output to the radio's sound card, PTT through the PTT port and its AGW port 8000 open for connected mode. The TX level line appears: Audio level is the card's PCM level for packet, separate from VARA's (direwolf plays much louder), remembered per radio. Send a test packet (same menu) sends one frame to BEACON, about 3 s at 300 baud: the PTT line's meters show its peaks afterwards; set Audio level so the watts reach what the radio is set to while ALC barely moves. Its own frames show in the text but not in Heard. With Transmit on, a Connect line appears under the text: type a call (with its -SSID: K5DDT-1) and Connect, or right-click a station in the Packet tab > Connect to (it tunes there first). direwolf does the AX.25 part (it tries 10 times, 10 s apart, then gives up: DISCONNECTED RETRYOUT); while it tries, the frames it sends and hears show as usual. Once connected, the text box shows only the session: what the station sends, and the lines you type in the box beside Disconnect (Enter sends; the BBS's prompt, then your line, like a terminal); direwolf's frame lines still go to Heard and packet.log, and right-click > Packet > Monitor while connected shows them too. Disconnect ends the session (or gives up while still trying). A station that connects to N7RTS shows up the same way. Settings for slow HF: frames of up to 64 characters, one at a time, 400 ms of lead-in for the radio to key, sent as FX.25 (forward error correction: the other end can repair a frame with a few bad bytes; plain AX.25 stations read it as usual). Transmit ends when the decoder stops (another decoder, Off, Modem, the radio closed) and starts off again every time. JS8: through JS8Call (started if it isn't running; if shack started it, Off, another decoder, closing the radio or shack close it again; one you started yourself is left running): shack plays Digital out into a sound device of its own, shack-JS8, and shows what JS8Call decodes (UTC, SNR, offset, text), a message's frames joined on one line as in JS8Call's Band Activity. Once, in JS8Call: Settings > Audio, Input = Monitor of shack-JS8; Settings > Reporting, Enable UDP Server API, 127.0.0.1 port 2242. The status line says when JS8Call is connected (it pings every 15 s). JS8 needs the clock right to a second or so. FT4 and FT8: by jt9, WSJT-X's decoder (apt install wsjtx; WSJT-X itself isn't run): at the end of each period on the UTC clock (FT4 7.5 s, FT8 15 s) shack gives it that period's audio and shows what it decodes, the period's time, SNR, DT (how far the signal is off the clock: a few tenths is normal, all of them off by the same amount = your clock), offset and message, like WSJT-X's Band Activity. The whole passband is decoded; use USB or USB-D with a wide filter (Digital bandwidth on the RSPdx). FT8 at 14.074, 7.074...; FT4 at 14.080, 7.0475... WSPR (by wsprd, also WSJT-X's): beacons, every even minute, 2-minute transmissions within 200 Hz of 1500 Hz audio; each line is a station heard: time, SNR, DT, offset, drift (Hz/min), callsign, grid and the power it says it sends (dBm and watts). FT4, FT8 and WSPR lines with a grid end with its distance (miles) and bearing from your grid: right-click > My grid (EM85RP to start). A good way to see where a band is open. Dials (USB): 3.5686, 7.0386, 10.1387, 14.0956, 18.1046, 21.0946, 28.1246 MHz. Needs the clock right. Settings are shared with decode.py (decode.json). Only one program can listen on UDP 7355: close decode.py, or an xara-monitor fed by nc, first.</p>

<h3>Bookmarks (right)</h3>
<p>One list for all radios, kept in bookmarks.db next to shack.py. Double-click: tune there, with the settings this radio had when it was saved. Grey ones are out of this radio's range or modes. Yellow ones are at the current frequency (any mode). Click a column heading to sort by it, again for the other way (Name: A-Z, unnamed ones last; Hits: most picked first); remembered. Save asks for a name for a new bookmark (blank is fine; Cancel doesn't save); saving one that's already there just updates its settings. Right-click: rename or delete. Most used are at the top. Each radio starts where it was when you last closed it.</p>

<h3>Packet (right)</h3>
<p>Stations the Packet decoder has heard, kept in bookmarks.db: one row for each callsign at each dial frequency. The tab shows every frequency on every band and fills in as packets arrive (click MHz to group them by frequency). Hits counts its packets; Last is when the latest came (today: the time, otherwise the date too); FEC says FX.25 or IL2P (blank: plain AX.25); Svc is what the station has said it offers, gathered from its packets: bbs (a mailbox: 'Mail for' beacons, a BBS greeting), node, em (Winlink / email), chat, aprs, digi (it relayed someone else's packet); blank = nothing seen yet; Miles is how far it is from your grid (decoder right-click > My grid): from the grid in its beacon if it sends one, otherwise US callsigns are looked up once at callook.info (the FCC's address for the licence; other countries stay blank); Clicked counts your double-clicks on it; Via is the digipeater when you didn't hear the station itself (the digipeater gets its own row: it's the one you heard); Text is its last beacon. The station the latest packet came from is green (with its digipeater, if it came through one), until the next packet. Rest the mouse on a row for when it was first heard, direwolf's audio level, the signal (the transceiver's S-meter at its highest during the station's latest packet; heard direct only, not through a digipeater; none on the RSPdx) and the whole text. Click a column heading to sort by it, again for the other way; remembered. Double-click the callsign: its QRZ page (the same window the decoders use). Double-click anywhere else on the row: go to that frequency and mode with the decoder that heard it. Right-click: Connect to it (packet Transmit on), look up on QRZ, Highlight it (light gold, every row with that callsign and SSID on every band, remembered: say for the stations you've connected to; the same item takes it off), forget one station, or clear the whole list.</p>

<h3>IC-705, Xiegu x6200</h3>
<p>Frequency, VFO A/B, mode, filter, volume, RF gain, AGC, Preamp, ATT, bookmarks (they keep filter, volume, RF gain, AGC, Preamp, ATT, NB and NR and their levels; never TX power; and the decoder that was picked when you saved, Off included: double-click starts it. Older bookmarks leave the decoder alone until you Save them again. A bookmark also keeps the VFO it was saved on and double-click goes back to that VFO first, so VFO B can stay on one thing, broadcast FM say, while A does the rest), S-meter, transmit power and PTT. The PTT button keys the radio (click again to stop; it unkeys itself after 30 s), and TX/RX next to it shows what the radio reports, whoever keyed it. SWR is the radio's meter, read while it transmits (it keeps the last reading). Power is % of the maximum on the 705 and watts on the Xiegu (0-10 W, in tenths); drag the slider (it's sent when you let go), or for one step at a time (1 %, 0.1 W on the Xiegu, 1 W on the FTdx10) turn the wheel over it, or click it and use the arrow keys. Noise blanker and Noise reduction switch the radio's own NB and NR on and off; the % box next to each is its level, usable while it's on (not on the Xiegu: its firmware has no NB / NR level or AGC over CI-V, so those are hidden). The PTT and Power lines are greyed outside the ham bands, and the PTT port refuses to key there too. Filter picks the radio's FIL1, FIL2 or FIL3 for the current mode: three presets per mode, each with its own width (set in the radio's menu); the width shown is the radio's report for the one in use (none in FM). Changing mode from here lands on FIL1. A red SPLIT button appears next to VFO A / VFO B whenever the radio is in split (it would transmit on the other VFO's frequency); click it to turn split off. VFO A and B pick the radio's VFO. The IC-705 can't say over CI-V which one it's on, so opening it puts it on the VFO shack last used with it (A the first time) and lights that button; switching with the radio's own button doesn't show here. The Xiegu (OpenX6X00 firmware) says which: the button follows its own A/B key. TX shows red while transmitting. Modem: xara, the VARA modem, on this radio's sound card. Listen starts it (callsign: Modem.CALL in shack.py; starting bandwidth: the VARA decoder's, right-click > VARA; the radio goes to VFO A, split off, USB-D) so Pat and OpenSlot can connect to it on ports 8100 and 8300; it hears but can't transmit. Transmit also lets it play into the radio and key it through the PTT port: shack first sets the sound card's level for this radio (and the IC-705's RF power to 100 %), and won't start if the radio is still in split or didn't go to USB-D on VFO A. The levels per radio are the vara= entries in civ.py and cat.py (IC-705, FTdx10). Its lines scroll in the text box and go to xara.log next to shack.py. The decoders and the Spectrum strip stop while it runs (one program at a time can have the radio's sound card) and come back with Off. Gateways (the tab next to Bookmarks): the Winlink VARA HF gateways on the band the radio is on, from rms.db (right-click > Download a fresh list runs rms.py), ones heard from in the last 6 hours first, then nearest first. To send mail: right-click > Compose a message (To, Cc, Subject, text; Put in the outbox), then double-click a gateway (or right-click it > Connect): shack sets Modem to Transmit if it isn't, Pat connects to it (it moves the dial and puts it back afterwards), sends what's in the outbox and fetches your mail; what Pat prints scrolls in the text box. When Pat is done (or you stop it) shack sets Modem back to Off. The menu says how many messages are waiting in the outbox. Right-click > Stop Pat gives up on a connect. Right-click a gateway > Add as a Pat alias puts it in Pat's config (connect_aliases, named like ko4hjo-7100.5) for pat connect and Pat's own Connect list; it shows with a ★ here, and the same menu item removes it. Right-click > Mail opens Pat's mailbox here (no browser): Inbox, Outbox, Sent, Archive; unread messages are bold and are marked read when you open them; Reply (to the sender), Reply all, Save attachments, Archive, Delete, Compose. After a connect the bottom line says how many are unread. BUSY in the bottom left corner of the window (there while the modem runs) is red while xara (the modem) hears a signal on the frequency, grey otherwise. While the radio transmits, the PTT line shows its own meters: watts, ALC (0 to 1; for VARA keep it near 0) and SWR, live; after it unkeys, the highest of each during that transmission (marked peak), until the next. With Modem on Transmit a TX level line appears: Tune makes xara send a steady tone (click again to stop; 30 s at most; it needs xara's command port, so not while Pat or OpenSlot is connected), and Audio level is the sound card's PCM level into the radio: turn it while tuning until the power is where you want it and ALC barely moves. It's remembered for each radio. PTT port 4532 on the status bar answers like Hamlib's rigctld for the radio that's open here: frequency, mode, VFO, split and PTT (xara, Pat, rigctl -m 2, and JS8Call / WSJT-X / fldigi set to Hamlib NET rigctl); the radio is unkeyed whenever a program that keyed it goes away, and whenever it is closed. Screensaver (Xiegu with the OpenX6X00 firmware): On turns the radio's screensaver on or off, the box is how many minutes without use before it comes. It goes over WiFi to the radio's web page (x6200.local, the RIGS web entry in civ.py), not over the USB cable, so it's greyed until the radio answers there; it's read when the radio is opened.</p>
<p>Scope (IC-705): Waterfall shows the radio's own band scope, sent over CI-V about four times a second: spectrum on top, waterfall under it, red line at the dial. Click anywhere on it to tune there; Shift-click a signal to tune so it lands at 1500 Hz audio, the middle of the passband where VARA, ARDOP and the decoders listen (USB and LSB; other modes tune straight there). Pause freezes it and the audio spectrum strip together (either Pause button; both light up) for a closer look at a signal; the radio keeps tuning. Turning it on puts the radio's scope in center mode; the span is how far it reaches either side of the dial, Ref the reference level (lower = weak signals brighter). Changing the span starts the waterfall over. The x6200 doesn't send its scope over CI-V (firmware as of 2026-11), so it has no Scope line.</p>

<h3>FTdx10</h3>
<p>Yaesu CAT on the radio's USB port (the first of its two serial ports, 38400 baud: the radio's CAT-1 RATE). Like the IC-705 without Filter, SWR and Scope: frequency, VFO A/B (read from the radio, so the one it's on is lit; switching here, shack puts back that VFO's Preamp, ATT, Roofing, AGC, NB and NR as they were when you left it, which the radio's own A/B key does by itself but a switch over CAT doesn't; the first switch to a VFO after opening keeps the current settings), mode, volume, RF gain, AGC, Preamp (IPO, AMP1, AMP2), ATT (off, 6, 12, 18 dB), Roofing (the radio's R FIL: 12 kHz, 3 kHz, 500 Hz), Display (the radio's screen contrast and brightness and its LED brightness, 0-20), NB and NR with their levels, S-meter, power and PTT; bookmarks keep Preamp, ATT and Roofing too. Scope sets the radio's own scope (the picture stays on the radio's screen): display mode (3DSS or waterfall, L/N/S size; Center, Cursor or Fix, the Fix edges set on the radio), span, Ref level (-30 to +30 dB) and speed; they follow the radio when you change them there. Right-click the Scope line (its label or between the boxes) for Peak, Color, RBW, Marker and Hold. Power goes from 5 W up to what the radio's own menu allows (TX GENERAL > HF / 50M MAX POWER, and AM MAX POWER in AM); the slider stops there, and its tooltip says the limit. Raise it in the radio's menu. Decoders read its USB sound card (CODEC, the same name as the 705's: plug in one of the two at a time).</p>

<h3>TH-D75</h3>
<p>Kenwood handheld on its USB-C cable (receive only from here, so far): frequency, mode, volume, squelch, backlight, signal and bookmarks. Volume is the radio's own (its VOL ring shows up here too; 1.00 = 200 on the radio's scale); Squelch is 0-5 for the VFO that's lit (0 = always open); Backlight: On keeps the display lit. VFO A and VFO B are the radio's band A and band B; the one its keys work on is lit, and tuning from here goes to that one. Opening it puts the radio in single-band mode, so you hear only the VFO that's lit (in the radio's dual-band mode both play at once; [F], [A/B] on the radio switches). Band A covers 136-174, 216-260 and 410-470 MHz in FM and DV only; band B is the wide receiver, 0.1-524 MHz in every mode (broadcast FM: VFO B, WBFM). The radio tunes in steps: a frequency that isn't on the band's step gets the widest step it sits on (25 kHz for 162.475, say), else the nearest 5 kHz; what the radio ended up on is what shows. Signal is the radio's own 0-5 bars. If band B is silent, check the radio's volume balance (menu 910: MENU, 9, 1, 0). The radio's TNC must not be in KISS mode: its port carries packets then and stops answering.</p>

<h3>Record</h3>
<p>Records the radio's receive audio, what the decoder and the spectrum strip hear, to /tmp/x.wav (48 kHz, mono); click again to stop. The button counts the seconds. No decoder running: the audio is read just for the recording. Stops by itself after 10 minutes, when the radio is closed, or when the modem takes the sound card. When it stops you're asked for a name: x.wav becomes NAME.wav in /tmp (the frequency and mode are offered; blank or Cancel leaves it as x.wav, which the next recording replaces). Record I/Q (RSPdx only) records the same audio and, next to it, the RSPdx's I/Q stream as rspdx gets it, before any demodulating: /tmp/x.cs16, then NAME.cs16: 16-bit I, Q pairs, 500 kS/s in SSB and AM (2 MB a second), 1 MS/s in WBFM. The text box says its length, sample rate and centre frequency (the file's 0 Hz: a little off the dial, where the RSPdx is really tuned); inspectrum, SigDigger and GNU Radio read it given the rate. It stops by itself at 1 GB (about 4 minutes) or when the mode changes. Needs an rspdx built after 2026-11-15 (./build.sh).</p>

<h3>Spectrum</h3>
<p>The button next to Help keeps the audio spectrum strip running whenever a radio is open, with or without a decoder: voice, music, whatever is in the passband (300-2900 Hz). It's remembered. Pause on the Decoder line freezes the strip, and the IC-705 waterfall with it, until you click it again; decoding carries on. The decoder area's colors are in styles.py (decoder).</p>

<p><i>F1 opens this help.</i></p>
'''

'''-------------------------------------------------------------------
-------------------------------------------------------------------'''

def sunits(db):		# S9-relative dB -> S7 / S9+20
	return f'S{max(0, (db + 54) // 6)}' if db <= 0 else f'S9+{db}'

'''-------------------------------------------------------------------
	Maidenhead grid (EM85 or EM85rp) -> (lat, lon) of its middle, None if it isn't one; distance (miles) and bearing between two
-------------------------------------------------------------------'''

GRID = re.compile(r'\b([A-R]{2}[0-9]{2}(?:[A-Xa-x]{2})?)\b')

def grid_ll(g):
	g = g.strip()
	if not re.fullmatch(r'[A-Ra-r]{2}[0-9]{2}([A-Xa-x]{2})?', g):
		return None
	g = g.upper()
	lon = (ord(g[0]) - 65) * 20 - 180 + int(g[2]) * 2
	lat = (ord(g[1]) - 65) * 10 - 90 + int(g[3])
	if len(g) == 6:
		return lat + (ord(g[5]) - 65 + 0.5) / 24, lon + (ord(g[4]) - 65 + 0.5) / 12
	return lat + 0.5, lon + 1

def path(a, b):		# great circle: miles, initial bearing (degrees from north)
	(la1, lo1), (la2, lo2) = (map(np.radians, p) for p in (a, b))
	d = 2 * np.arcsin(np.sqrt(np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2))
	brg = np.degrees(np.arctan2(np.sin(lo2 - lo1) * np.cos(la2), np.cos(la1) * np.sin(la2) - np.sin(la1) * np.cos(la2) * np.cos(lo2 - lo1))) % 360
	return float(d * 3958.8), float(brg)

'''-------------------------------------------------------------------
-------------------------------------------------------------------'''

def lna_max(mhz):		# RSPdx LNA states by frequency, as tui.cpp has them
	for top, n in ((12, 18), (50, 19), (60, 24), (250, 26), (420, 27), (1000, 20)):
		if mhz < top:
			return n
	return 18

'''-------------------------------------------------------------------
-------------------------------------------------------------------'''

def quiet(w, f, *a):		# change a widget from the radio's side without echoing it back as a command
	w.blockSignals(True)
	f(*a)
	w.blockSignals(False)

'''-------------------------------------------------------------------
	plughw:N of the sound card inside the same radio as serial port dev: on the same USB device, else on the same hub (the hub inside the radio, never the computer's own)
	None if there isn't one. By port, not by name: the IC-705's and the FTdx10's cards are both called CODEC
-------------------------------------------------------------------'''

def card_of(dev, sys='/sys'):
	usb = re.compile(r'\d+-[\d.]+')		# 1-3.1 is a USB device; 1-3.1:1.0 is one of its interfaces, usb1 the computer's root hub
	d = Path(sys, 'class/tty', Path(dev).resolve().name, 'device').resolve()
	while d != d.parent and not usb.fullmatch(d.name):
		d = d.parent
	if d == d.parent:
		return None
	for top in (d, d.parent) if usb.fullmatch(d.parent.name) else (d,):
		for c in sorted(Path(sys, 'class/sound').glob('card[0-9]*')):
			if top in (c / 'device').resolve().parents:
				return 'plughw:' + c.name[4:]
	return None
# card_of

'''===================================================================================================================================================

==================================================================================================================================================='''

class Freq(QWidget):		# frequency readout that works like the waterfall page's: wheel or click on any digit tunes that digit
	changed = pyqtSignal(float)		# MHz, on every step and on Enter

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def __init__(self):
		super().__init__()
		self.setFont(QFont('DejaVu Sans Mono', 30, QFont.Weight.DemiBold))
		self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
		self.setMouseTracking(True)		# hover highlight without a button down
		self.setToolTip('Wheel over a digit tunes it; click its top half for up, bottom half for down.\nArrow keys: left/right picks a digit, up/down tunes it.\nOr type a frequency in MHz and press Enter (Esc cancels)')
		self.hz, self.lo, self.hi, self.ndig = 0, 30000, 2000000000, 10
		self.hover = None		# digit under the mouse
		self.cur = None		# digit the arrow keys tune
		self.typed = None		# MHz being typed; None = showing the frequency
		self.moved = 0		# when we last tuned: the radio's older reports mustn't pull the display back
		self.notches = 0		# wheel angle not yet used (touchpads send fractions of a notch)
		return
	# __init__

	'''-------------------------------------------------------------------
		top of the band in MHz: also how many digits there are
	-------------------------------------------------------------------'''

	def set_top(self, mhz):
		self.hi = round(mhz * 1e6)
		self.ndig = len(str(self.hi))
		self.cur, self.moved = None, 0		# a new radio: take its first report
		self.updateGeometry()
		self.update()
		return
	# set_top

	'''-------------------------------------------------------------------
		the radio's frequency; ignored for a second after we tune, so a slow report doesn't rubber-band the display
	-------------------------------------------------------------------'''

	def set_mhz(self, mhz):
		if time.monotonic() - self.moved < 1:
			return
		self.hz = round(mhz * 1e6)
		self.update()
		return
	# set_mhz

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def tune(self, hz):
		self.hz, self.moved = hz, time.monotonic()
		self.update()
		self.changed.emit(hz / 1e6)
		return
	# tune

	'''-------------------------------------------------------------------
		one step of digit i; a step off either end is ignored, as on the waterfall page
	-------------------------------------------------------------------'''

	def step(self, i, up):
		n = self.hz + (1 if up else -1) * 10 ** (self.ndig - 1 - i)
		if self.lo <= n <= self.hi:
			self.tune(n)
		return
	# step

	'''-------------------------------------------------------------------
		(x, width, digit index or None, text) for each character, left to right; dots between groups of three
	-------------------------------------------------------------------'''

	def cells(self):
		cw = self.fontMetrics().horizontalAdvance('0')
		out, x = [], 4
		for i, c in enumerate(str(self.hz).zfill(self.ndig)[-self.ndig:]):
			out.append((x, cw, i, c))
			x += cw
			if i < self.ndig - 1 and (self.ndig - 1 - i) % 3 == 0:
				out.append((x, cw * 2 // 3, None, '.'))
				x += cw * 2 // 3
		return out
	# cells

	def digit_at(self, px):
		return next((i for x, w, i, t in self.cells() if i is not None and x <= px < x + w), None)

	def unit_font(self):
		f = QFont(self.font())
		f.setPointSize(self.font().pointSize() * 2 // 5)
		return f

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def sizeHint(self):
		x, w, i, t = self.cells()[-1]
		return QSize(x + w + QFontMetrics(self.unit_font()).horizontalAdvance('  MHz') + 8, self.fontMetrics().height() + 8)

	def minimumSizeHint(self):
		return self.sizeHint()

	'''-------------------------------------------------------------------
		digits, leading zeros and dots dimmed, the hovered digit shaded, the arrow keys' digit underlined
	-------------------------------------------------------------------'''

	def paintEvent(self, e):
		p = QPainter(self)
		ink = self.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText)		# not the current group: some themes grey the inactive one (window without focus)
		dim = QColor('#a0a0a0')
		shade = QColor(self.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight))
		shade.setAlpha(50)
		h = self.height()
		if self.typed is not None:		# typing: show what's typed, nothing else
			p.setPen(ink)
			p.drawText(QRect(4, 0, self.width() - 4, h), Qt.AlignmentFlag.AlignVCenter, self.typed + '_')
			x = 4 + self.fontMetrics().horizontalAdvance(self.typed + '_')
			unit = 'MHz'
		else:
			first = next((i for i, c in enumerate(str(self.hz).zfill(self.ndig)) if c != '0'), self.ndig)
			for x, w, i, t in self.cells():
				if i is not None and i == self.hover:
					p.fillRect(QRect(x, 2, w, h - 4), shade)
				if i is not None and i == self.cur and self.hasFocus():
					p.fillRect(QRect(x + 2, h - 5, w - 4, 3), ink)
				p.setPen(ink if i is not None and i >= first and self.isEnabled() else dim)
				p.drawText(QRect(x, 0, w, h), Qt.AlignmentFlag.AlignCenter, t)
			x += w
			unit = 'Hz'
		p.setPen(dim)
		p.setFont(self.unit_font())
		p.drawText(QRect(x + 6, 0, self.width() - x, h), Qt.AlignmentFlag.AlignVCenter, unit)
		return
	# paintEvent

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def wheelEvent(self, e):
		i = self.digit_at(e.position().x())
		if i is None:
			return e.ignore()
		self.notches += e.angleDelta().y()
		while abs(self.notches) >= 120:
			self.step(i, self.notches > 0)
			self.notches -= 120 if self.notches > 0 else -120
		e.accept()
		return
	# wheelEvent

	def mousePressEvent(self, e):		# top half up, bottom half down
		i = self.digit_at(e.position().x())
		if i is not None and e.button() == Qt.MouseButton.LeftButton:
			self.cur = i
			self.step(i, e.position().y() < self.height() / 2)
		return

	def mouseMoveEvent(self, e):
		i = self.digit_at(e.position().x())
		self.setCursor(Qt.CursorShape.SizeVerCursor if i is not None else Qt.CursorShape.ArrowCursor)
		if i != self.hover:
			self.hover = i
			self.update()
		return

	def leaveEvent(self, e):
		self.hover = None
		self.update()
		return

	def focusOutEvent(self, e):		# leaving drops a half-typed frequency
		self.typed = None
		self.update()
		return super().focusOutEvent(e)

	'''-------------------------------------------------------------------
		typing a frequency in MHz (digits and a point), or arrows on a digit
	-------------------------------------------------------------------'''

	def keyPressEvent(self, e):
		k, t = e.key(), e.text()
		if self.typed is not None:
			if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
				return self.enter()
			elif k == Qt.Key.Key_Escape:
				self.typed = None
			elif k == Qt.Key.Key_Backspace:
				self.typed = self.typed[:-1] or None
			elif t and t in '0123456789.':
				self.typed += t
			else:
				return super().keyPressEvent(e)
		elif t and t in '0123456789.':
			self.typed = t
		elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right):
			c = self.ndig - 4 if self.cur is None else self.cur		# starts on the kHz digit
			self.cur = max(0, min(self.ndig - 1, c + (1 if k == Qt.Key.Key_Right else -1)))
		elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
			if self.cur is None:
				self.cur = self.ndig - 4
			self.step(self.cur, k == Qt.Key.Key_Up)
		else:
			return super().keyPressEvent(e)
		self.update()
		return
	# keyPressEvent

	def enter(self):		# out of range or not a number: beep and leave it to be fixed
		try:
			n = round(float(self.typed) * 1e6)
		except ValueError:
			n = -1
		if not self.lo <= n <= self.hi:
			return QApplication.beep()
		self.typed = None
		self.tune(n)
		return
	# enter

# class Freq


'''===================================================================================================================================================

==================================================================================================================================================='''

class Bookmarks:		# bookmarks.db next to shack.py: one list for every radio; a row keeps settings per radio, {radio: {key: value}}

	'''-------------------------------------------------------------------
		opens (or makes) the database; a new one gets rspdx's settings.db rows copied in
	-------------------------------------------------------------------'''

	def __init__(self, path, old):
		new = not Path(path).exists()
		self.db = sqlite3.connect(path)
		self.db.execute("CREATE TABLE IF NOT EXISTS bookmarks (hz INTEGER NOT NULL, mode TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', hits INTEGER NOT NULL DEFAULT 1,"
			" settings TEXT NOT NULL DEFAULT '{}', updated TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (hz, mode))")
		self.db.execute("CREATE TABLE IF NOT EXISTS last (radio TEXT PRIMARY KEY, hz INTEGER NOT NULL, mode TEXT NOT NULL, settings TEXT NOT NULL DEFAULT '{}')")		# where each radio was left
		self.db.execute("CREATE TABLE IF NOT EXISTS prefs (k TEXT PRIMARY KEY, v TEXT NOT NULL)")		# the table's sort
		self.db.execute("CREATE TABLE IF NOT EXISTS heard (call TEXT NOT NULL, hz INTEGER NOT NULL, mode TEXT NOT NULL, dec TEXT NOT NULL, hits INTEGER NOT NULL DEFAULT 1, first TEXT NOT NULL, last TEXT NOT NULL,"
			" level INTEGER, fec TEXT NOT NULL DEFAULT '', via TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '', PRIMARY KEY (call, hz, mode, dec))")		# stations the decoders heard
		have = {r[1] for r in self.db.execute('PRAGMA table_info(heard)')}
		for col, kind in (('clicks', 'INTEGER NOT NULL DEFAULT 0'), ('svc', "TEXT NOT NULL DEFAULT ''"), ('sig', 'INTEGER')):		# added 2026-11-15; sig 2026-11-18
			if col not in have:
				self.db.execute(f'ALTER TABLE heard ADD COLUMN {col} {kind}')
		self.db.execute("CREATE TABLE IF NOT EXISTS marked (call TEXT PRIMARY KEY)")		# Packet tab rows shown gold (right-click > Highlight, say the stations connected to): the callsign with its SSID, every band
		self.db.execute("CREATE TABLE IF NOT EXISTS grids (call TEXT PRIMARY KEY, grid TEXT NOT NULL)")		# where a callsign (no SSID) is: from its beacon or callook.info; '' = looked up, not found
		if new and Path(old).exists():
			self.copy_rspdx(old)
		self.db.commit()
		return
	# __init__

	'''-------------------------------------------------------------------
		one-time copy of rspdx's settings.db (its -1 = never saved, left out)
	-------------------------------------------------------------------'''

	def copy_rspdx(self, old):
		src = sqlite3.connect(old)
		cols = ('v', 'if', 'lna', 'ant', 'notch', 'dab', 'hdr', 'bw', 'nb', 'nr')
		for hz, mode, name, hits, *vals in src.execute('SELECT frequency_hz, mode, name, hits, volume, if_reduction, lna, antenna, rf_notch, dab_notch, hdr, bw, nb, nr FROM settings'):
			s = {k: v for k, v in zip(cols, vals) if not (k in ('bw', 'nb', 'nr') and v < 0)}
			self.db.execute('INSERT OR IGNORE INTO bookmarks (hz, mode, name, hits, settings) VALUES (?, ?, ?, ?, ?)', (hz, mode.upper(), name, hits, json.dumps({'RSPdx': s})))
		row = src.execute('SELECT frequency_hz, mode FROM last_frequency ORDER BY saved_at DESC LIMIT 1').fetchone()
		if row:
			b = self.find(row[0], row[1].upper())
			self.set_last('RSPdx', row[0], row[1].upper(), b['settings'].get('RSPdx', {}) if b else {})
		src.close()
		log.info(f'bookmarks: copied {self.db.execute("SELECT count(*) FROM bookmarks").fetchone()[0]} from {old}')
		return
	# copy_rspdx

	'''-------------------------------------------------------------------
		all bookmarks sorted by a column of the table (SORTS), down = Z-A / high to low; ties by frequency; unnamed ones last when sorted by name
	-------------------------------------------------------------------'''

	SORTS = ('name', 'hz', 'mode', 'hits')

	def list(self, col=3, down=True):
		out = []
		for hz, mode, name, hits, s in self.db.execute('SELECT hz, mode, name, hits, settings FROM bookmarks ORDER BY hz'):
			out.append(dict(hz=hz, mode=mode, name=name, hits=hits, settings=json.loads(s)))
		k = self.SORTS[col]
		if k == 'name':
			named = sorted((b for b in out if b['name']), key=lambda b: b['name'].casefold(), reverse=down)
			return named + [b for b in out if not b['name']]
		return sorted(out, key=lambda b: b[k], reverse=down)		# stable: ties keep frequency order
	# list

	def pref(self, k, default=None):
		row = self.db.execute('SELECT v FROM prefs WHERE k = ?', (k,)).fetchone()
		return json.loads(row[0]) if row else default

	def set_pref(self, k, v):
		self.db.execute('INSERT OR REPLACE INTO prefs (k, v) VALUES (?, ?)', (k, json.dumps(v)))
		self.db.commit()
		return

	def find(self, hz, mode):
		return next((b for b in self.list() if b['hz'] == hz and b['mode'] == mode), None)

	'''-------------------------------------------------------------------
		Save: adds the bookmark with its name, or updates this radio's settings in it (other radios' settings, name and hits stay)
		dec: the decoder to start with it, kept beside the radios as settings['decoder'] (one for every radio); None leaves it as it was
	-------------------------------------------------------------------'''

	def save(self, radio, hz, mode, settings, name='', dec=None):
		b = self.find(hz, mode)
		s = b['settings'] if b else {}
		s[radio] = settings
		if dec:
			s['decoder'] = dec
		self.db.execute('INSERT INTO bookmarks (hz, mode, name, settings) VALUES (?, ?, ?, ?) ON CONFLICT (hz, mode) DO UPDATE SET settings = excluded.settings, updated = CURRENT_TIMESTAMP',
			(hz, mode, name, json.dumps(s)))
		self.db.commit()
		return
	# save

	def picked(self, b):
		self.db.execute('UPDATE bookmarks SET hits = hits + 1 WHERE hz = ? AND mode = ?', (b['hz'], b['mode']))
		self.db.commit()

	def rename(self, b, name):
		self.db.execute('UPDATE bookmarks SET name = ? WHERE hz = ? AND mode = ?', (name, b['hz'], b['mode']))
		self.db.commit()

	def delete(self, b):
		self.db.execute('DELETE FROM bookmarks WHERE hz = ? AND mode = ?', (b['hz'], b['mode']))
		self.db.commit()

	'''-------------------------------------------------------------------
		where a radio was left: (hz, mode, settings) or None
	-------------------------------------------------------------------'''

	def last(self, radio):
		row = self.db.execute('SELECT hz, mode, settings FROM last WHERE radio = ?', (radio,)).fetchone()
		return (row[0], row[1], json.loads(row[2])) if row else None

	def set_last(self, radio, hz, mode, settings):
		self.db.execute('INSERT OR REPLACE INTO last (radio, hz, mode, settings) VALUES (?, ?, ?, ?)', (radio, hz, mode, json.dumps(settings)))
		self.db.commit()
		return
	# set_last

	'''-------------------------------------------------------------------
		a station a decoder heard: one row per callsign, dial frequency, radio mode and decoder; hits counts up, first and last are local time
		level: the decoder's own reading of the last frame (direwolf: its audio level); fec: FX.25 / IL2P; via: the digipeater it came through; text: its last beacon
		svc: what it offers ('bbs', 'node'...), added to what the row already says
	-------------------------------------------------------------------'''

	def heard(self, call, hz, mode, dec, level=None, fec='', via='', text='', svc=(), sig=None):		# sig: the radio's S-meter, dB from S9 (S7 = -12), None = no reading
		now = time.strftime('%Y-%m-%d %H:%M:%S')
		key = (call, hz, mode, dec)
		row = self.db.execute('SELECT svc FROM heard WHERE call = ? AND hz = ? AND mode = ? AND dec = ?', key).fetchone()
		svc = ', '.join(sorted(set(svc) | set(row[0].split(', ') if row and row[0] else ())))
		self.db.execute('INSERT INTO heard (call, hz, mode, dec, first, last, level, fec, via, text, svc, sig) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
			' ON CONFLICT (call, hz, mode, dec) DO UPDATE SET hits = hits + 1, last = excluded.last, level = excluded.level, fec = excluded.fec, via = excluded.via, svc = excluded.svc,'
			" text = CASE WHEN excluded.text != '' THEN excluded.text ELSE heard.text END, sig = coalesce(excluded.sig, heard.sig)", (*key, now, now, level, fec, via, text, svc, sig))
		self.db.commit()
		return
	# heard

	def clicked(self, h):		# a row of the heard list was double-clicked
		self.db.execute('UPDATE heard SET clicks = clicks + 1 WHERE call = ? AND hz = ? AND mode = ? AND dec = ?', (h['call'], h['hz'], h['mode'], h['dec']))
		self.db.commit()
		return

	def grid(self, call):		# a callsign's grid: None = never looked for, '' = not found
		row = self.db.execute('SELECT grid FROM grids WHERE call = ?', (call,)).fetchone()
		return row[0] if row else None

	def set_grid(self, call, grid):
		self.db.execute('INSERT OR REPLACE INTO grids (call, grid) VALUES (?, ?)', (call, grid))
		self.db.commit()
		return

	'''-------------------------------------------------------------------
		the heard list, sorted by a column of the table (HEARD), down = Z-A / high to low / newest first; ties newest first
		unheard: forgets one row (h), or the whole list
	-------------------------------------------------------------------'''

	HEARD = ('call', 'hz', 'hits', 'last', 'fec', 'svc', 'miles', 'clicks', 'via', 'text')

	def heards(self, col=3, down=True, home=None):		# home: (lat, lon), for miles
		cols = ('call', 'hz', 'mode', 'dec', 'hits', 'first', 'last', 'level', 'fec', 'via', 'text', 'clicks', 'svc', 'sig')
		out = [dict(zip(cols, row)) for row in self.db.execute(f"SELECT {', '.join(cols)} FROM heard ORDER BY last DESC")]
		grids = dict(self.db.execute('SELECT call, grid FROM grids'))
		marked = {r[0] for r in self.db.execute('SELECT call FROM marked')}
		for h in out:
			h['mark'] = h['call'] in marked
			h['grid'] = grids.get(h['call'].split('-')[0])
			ll = grid_ll(h['grid']) if h['grid'] else None
			h['miles'] = round(path(home, ll)[0]) if home and ll else None
		k = self.HEARD[min(col, len(self.HEARD) - 1)]
		return sorted(out, key=lambda h: h[k].casefold() if isinstance(h[k], str) else -1 if h[k] is None else h[k], reverse=down)		# stable: ties stay newest first
	# heards

	def mark(self, call, on):
		self.db.execute('INSERT OR IGNORE INTO marked (call) VALUES (?)' if on else 'DELETE FROM marked WHERE call = ?', (call,))
		self.db.commit()
		return

	def unheard(self, h=None):
		if h:
			self.db.execute('DELETE FROM heard WHERE call = ? AND hz = ? AND mode = ? AND dec = ?', (h['call'], h['hz'], h['mode'], h['dec']))
		else:
			self.db.execute('DELETE FROM heard')
		self.db.commit()
		return
	# unheard

# class Bookmarks


'''===================================================================================================================================================

==================================================================================================================================================='''

class Rspdx:		# rspdx -n (RSPdx) as a child process: commands on its stdin, JSON lines on its stdout; same face as civ.Rig
	MODES = ['LSB', 'USB', 'AM', 'WBFM']
	AGCS = {'Fast': 'fast', 'Mid': 'mid', 'Slow': 'slow', 'Off': 'off'}		# AGC box: rspdx's agc command
	BANDWIDTHS = ['Narrow voice  1.8 kHz', 'Normal voice  2.8 kHz', 'Wide voice  3.1 kHz', 'Digital  2.8 kHz']		# bw 0-3, SSB only
	SWITCHES = {'notch': 'RF notch', 'dab': 'DAB notch', 'hdr': 'HDR', 'digi': 'Digital out', 'wf': 'Waterfall', 'nb': 'Noise blanker', 'nr': 'Noise reduction'}		# rspdx command: button

	'''-------------------------------------------------------------------
		starts the rspdx program (C++ demodulators) as a child process
	-------------------------------------------------------------------'''

	def __init__(self, path, emit, last=None):		# last: (hz, mode, settings) to start from, as Bookmarks.last gives it
		self.emit, self.ok, self.tx = emit, False, 0		# ok once rspdx has sent its first state
		self.start = last		# not self.last: that's rspdx's last stderr line
		self.state = {}		# rspdx's last state line
		self.last = ''		# last stderr line, for the box if rspdx dies
		self.p = QProcess()
		self.p.setWorkingDirectory(str(Path(path).parent))		# index.html, app.js if the waterfall comes back
		self.p.readyReadStandardOutput.connect(self.out)
		self.p.readyReadStandardError.connect(self.err)
		self.p.finished.connect(self.ended)
		self.p.errorOccurred.connect(lambda e: e == QProcess.ProcessError.FailedToStart and self.emit('gone', f"Couldn't run {path}.\n\n{self.p.errorString()}"))
		self.p.start(str(path), ['-n', *((f'{last[0] / 1e6:.6f}', last[1].lower()) if last else ())])		# no frequency: rspdx starts at its default
		return
	# __init__

	'''-------------------------------------------------------------------
		lines coming in on rspdx's stdout: state and notices every 50 ms, meter every 250 ms
		emits signals with new rspdx values to class Shack()
	-------------------------------------------------------------------'''

	def out(self):
		while self.p.canReadLine():
			line = bytes(self.p.readLine()).decode(errors='replace')
			try:
				m = json.loads(line)
				if m['type'] == 'state':
					if not self.ok and self.start:		# first state: rspdx is up, put back the settings it was left with
						self.apply(self.start[1], self.start[2])
					self.ok, self.state = True, m['state']
					self.show()
				elif m['type'] == 'notice':		# every command gets one; a refused command changes no state, so put the controls back
					self.emit('error', m['msg'])
					self.show()
				elif m['type'] == 'meter':		# every 250 ms
					self.emit('meter', (m['adc'], m['sig'], m['nbr']))
				elif m['type'] == 'iq':		# an I/Q recording ended: its file, sample rate, centre (Hz), samples, why (if not asked to)
					self.emit('iq', m)
				elif m['type'] == 'bookmarks':		# rspdx's own list, empty under shack (bookmarks.db is shack's)
					pass
			except (ValueError, KeyError, TypeError) as e:		# not JSON, or an rspdx older than this file (pull without ./build.sh): log it, keep going
				log.error(f'rspdx: bad line ({e!r}): {line.strip()}')
				self.emit('error', 'rspdx sent something unexpected (rebuild it after the last pull?), see shack.log')
		# while data coming in from rspdx.cpp
	# out

	'''-------------------------------------------------------------------
		emits signals to Shack
		mhz and other real time values, including overload
	-------------------------------------------------------------------'''

	def show(self):		# rspdx's last state -> the window
		st = self.state
		if not st:
			return
		self.emit('mhz', st['mhz'])
		for k in ('mode', 'v', 'if', 'lna', 'ant', 'bw', *self.SWITCHES):
			self.emit(k, st[k])
		if 'agc' in st:		# an rspdx from before 2026-11-15 has none
			self.emit('agc', st['agc'])
		self.emit('ovl', (st['ovl'], st['ovln']))
	# show

	'''-------------------------------------------------------------------
		what a bookmark keeps for the RSPdx, and putting it back
	-------------------------------------------------------------------'''

	KEEP = ('v', 'if', 'lna', 'ant', 'notch', 'dab', 'hdr', 'bw', 'nb', 'nr', 'ref', 'agc')		# ref: the waterfall's reference level, dB

	def settings(self):
		return {k: self.state[k] for k in self.KEEP if k in self.state}

	def apply(self, mode, s):
		for k, v in s.items():
			if k == 'nb':		# 0 = off, else its trigger level (which turns it on)
				self.set('nbdb', v) if v else self.set('nb', 0)
			elif k == 'bw':
				if v >= 0 and mode in ('LSB', 'USB'):		# SSB only
					self.set('bw', v)
			elif k in self.KEEP:
				self.set(k, v)
		return
	# apply

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def err(self):		# rspdx's stderr goes to shack.log
		for line in bytes(self.p.readAllStandardError()).decode(errors='replace').splitlines():
			if line.strip():
				self.last = line.strip()
				log.info(f'rspdx: {self.last}')
	# err

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def ended(self, code, status):
		if self.ok:
			how = 'crashed' if status == QProcess.ExitStatus.CrashExit else f'stopped (exit {code})'
			self.emit('gone', f'rspdx {how}.\n\nLast from rspdx: {self.last}\n(all of it is in shack.log)')
		else:
			self.emit('gone', f"Couldn't start the RSPdx.\n\n{self.last or 'rspdx exited with ' + str(code)}\n\nIs it plugged in? Is another program using it?")
	# ended

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def set(self, k, v):
		if k == 'mhz':
			self.send(f'f {v:.6f}')
		elif k == 'mode':
			self.send(f'm {v.lower()}')
		elif k == 'v':
			self.send(f'v {v:.3f}')
		elif k in ('if', 'lna'):
			self.send(f'{k} {v}')
		elif k == 'ant':
			self.send(f'ant {v.lower()}')
		elif k == 'bw':
			self.send(f'bw {v}')
		elif k == 'agc':
			self.send(f'agc {v}')
		elif k == 'ref':
			self.send(f'ref {v:g}')
		elif k in self.SWITCHES:		# on/off
			self.send(f'{k} {int(v)}')
		elif k == 'nbdb':		# blanker level; rspdx turns the blanker on at it
			self.send(f'nb {v}')
	# set

	'''-------------------------------------------------------------------
		writes a command line to rspdx's stdin; rspdx reads it in Control::poll(), every 50 ms
	-------------------------------------------------------------------'''

	def send(self, line):
		self.p.write((line + '\n').encode())
	# send

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def close(self):		# EOF on stdin is rspdx's cue to shut down cleanly
		self.p.finished.disconnect(self.ended)
		self.p.closeWriteChannel()
		if not self.p.waitForFinished(3000):
			self.p.kill()
			self.p.waitForFinished(1000)
	# close

# class Rspdx


'''===================================================================================================================================================

==================================================================================================================================================='''

class Spectrum(QWidget):		# the decoder's averaged audio spectrum, 300-2900 Hz: click a signal to lock onto it
	TOP = 40		# dB above the noise at the top of the strip

	'''-------------------------------------------------------------------
		owner: the Decoders it shows and tunes
	-------------------------------------------------------------------'''

	def __init__(self, owner):
		super().__init__()
		self.owner = owner
		self.setFixedHeight(80)
		self.setCursor(Qt.CursorShape.CrossCursor)
		self.setToolTip('Click a signal to decode it (RTTY: either tone). Wheel: nudge 10 Hz. Right-click: Auto tune, settings.\nDashed line: squelch. Marker green while decoding')
		self.shot = None		# Pause: the picture as it was, drawn instead of the live one
		return
	# __init__

	def pause(self, on):		# grab() paints the live picture before shot is set
		self.shot = self.grab() if on else None
		self.update()
		return

	def hz(self, x):
		return decode.F0 + x / self.width() * (decode.F1 - decode.F0)

	def px(self, hz):
		return round((hz - decode.F0) / (decode.F1 - decode.F0) * self.width())

	'''-------------------------------------------------------------------
		spectrum filled, 500 Hz grid and labels, squelch dashed, the tone(s) being decoded
	-------------------------------------------------------------------'''

	def paintEvent(self, e):
		p = QPainter(self)
		if self.shot:
			p.drawPixmap(self.rect(), self.shot)
			return
		w, h = self.width(), self.height() - 14		# bottom 14 px: Hz labels
		p.fillRect(self.rect(), QColor(styles.decoder['spectrum']))		# colors: styles.py
		if not decode:
			return
		p.setFont(QFont('DejaVu Sans', 7))
		for f in range(500, decode.F1, 500):
			x = self.px(f)
			p.setPen(QColor(styles.decoder['grid']))
			p.drawLine(x, 0, x, h)
			p.setPen(QColor(styles.decoder['labels']))
			p.drawText(QRect(x - 25, h, 50, 14), Qt.AlignmentFlag.AlignCenter, f'{f}')
		d = self.owner.dec
		if not d or d.tu.avg is None:
			return
		tu = d.tu
		y = lambda db: h - max(0.0, min(1.0, db / self.TOP)) * h
		edges = np.linspace(decode.F0, decode.F1, w + 1) / tu.df		# bins under each pixel column
		db = tu.db(tu.avg[:int(edges[-1]) + 1])
		col = np.maximum.reduceat(db, edges[:-1].astype(int))		# loudest bin in each column
		band = getattr(d, 'band', None)		# VARA, packet: the audio they decode, shaded
		if band:
			p.fillRect(QRect(self.px(band[0]), 0, self.px(band[1]) - self.px(band[0]), h), QColor(32, 160, 32, 60))
		win = getattr(tu, 'look', None)		# RTTY, CW, PSK31: where auto tune looks; the rest dimmed
		if win:
			p.fillRect(QRect(0, 0, self.px(win[0]), h), QColor(0, 0, 0, 120))
			p.fillRect(QRect(self.px(win[1]), 0, w - self.px(win[1]), h), QColor(0, 0, 0, 120))
		pts = [QPointF(0, h)] + [QPointF(i, y(v)) for i, v in enumerate(col)] + [QPointF(w, h)]
		p.setPen(Qt.PenStyle.NoPen)
		p.setBrush(QColor(styles.decoder['fill']))
		p.drawPolygon(QPolygonF(pts))
		if tu.sq <= self.TOP:
			p.setPen(QPen(QColor(styles.decoder['squelch']), 1, Qt.PenStyle.DashLine))
			p.drawLine(0, round(y(tu.sq)), w, round(y(tu.sq)))
		if tu.center:
			sh = getattr(d, 'shift', 0)
			p.setPen(QPen(QColor('#20a020' if d.op else '#e08000'), 2))
			for f in ((tu.center - sh / 2, tu.center + sh / 2) if sh else (tu.center,)):
				p.drawLine(self.px(f), 0, self.px(f), h)
		return
	# paintEvent

	'''-------------------------------------------------------------------
		click: lock onto the strongest tone (RTTY: tone pair) near the click
	-------------------------------------------------------------------'''

	def mousePressEvent(self, e):
		d = self.owner.dec
		if d and e.button() == Qt.MouseButton.LeftButton:
			f = self.hz(e.position().x())
			d.tune(f if isinstance(d, decode.Olivia) else d.tu.snap(f, getattr(d, 'shift', 0) / 2 + 40))		# Olivia: click its middle
			self.update()
		return
	# mousePressEvent

	def wheelEvent(self, e):		# nudge 10 Hz, locks
		d = self.owner.dec
		if d and d.tu.center and e.angleDelta().y():
			d.tune(min(max(d.tu.center + (10 if e.angleDelta().y() > 0 else -10), decode.F0), decode.F1))
			self.update()
		e.accept()
		return
	# wheelEvent

# class Spectrum


'''===================================================================================================================================================

==================================================================================================================================================='''

class Waterfall(QWidget):		# a CI-V radio's own band scope (27 00 data): spectrum on top, waterfall under it, the dial marked; click tunes there
	ROWS = 200		# sweeps of history (about 50 s at the 705's 4 a second)
	SPEC = 70		# px of spectrum above the waterfall
	TOP = 160		# the 705's scope level at full scale (0xA0)
	STOPS = ((0, '#000000'), (30, '#000050'), (60, '#0000ff'), (85, '#00e0ff'), (110, '#ffff00'), (135, '#ff0000'), (160, '#ffffff'))		# level: color

	'''-------------------------------------------------------------------
		tune(hz, center): what a click does; center = Shift held (Shack.wf_click)
	-------------------------------------------------------------------'''

	def __init__(self, tune):
		super().__init__()
		self.tune = tune
		self.setFixedHeight(self.SPEC + self.ROWS + 14)		# bottom 14 px: kHz labels
		self.setCursor(Qt.CursorShape.CrossCursor)
		self.setToolTip('The radio\'s band scope. Click: tune there.  Shift-click: tune so that signal lands at 1500 Hz audio (VARA, ARDOP, the decoders\' middle).  Red line: the dial')
		self.lo, self.hi, self.dial = 0, 0, 0		# Hz across the width, Hz of the dial
		self.n = 0		# points per sweep
		self.rows = bytearray()		# newest sweep first, ROWS * n levels
		self.paused = False		# Pause: sweeps are dropped, the picture stays
		self.colors = self.table()
		return
	# __init__

	'''-------------------------------------------------------------------
		256 colors for Indexed8: STOPS blended, white above the top
	-------------------------------------------------------------------'''

	def table(self):
		out = []
		for v in range(256):
			(a, ca), (b, cb) = next(((s, t) for s, t in zip(self.STOPS, self.STOPS[1:]) if v <= t[0]), (self.STOPS[-1], self.STOPS[-1]))
			f = (v - a) / (b - a) if b > a else 1
			ca, cb = QColor(ca), QColor(cb)
			out.append(QColor(round(ca.red() + f * (cb.red() - ca.red())), round(ca.green() + f * (cb.green() - ca.green())), round(ca.blue() + f * (cb.blue() - ca.blue()))).rgb())
		return out
	# table

	'''-------------------------------------------------------------------
		one sweep from civ.Rig: <II lo hi Hz, then a level byte per point; a new width (span, points) starts the waterfall over
	-------------------------------------------------------------------'''

	def add(self, v):
		lo, hi = struct.unpack('<II', v[:8])
		d = v[8:]
		if len(d) < 2 or self.paused:
			return
		if len(d) != self.n or hi - lo != self.hi - self.lo:
			self.n, self.rows = len(d), bytearray(len(d) * self.ROWS)
		self.lo, self.hi = lo, hi
		self.rows[len(d):] = self.rows[:-len(d)]
		self.rows[:len(d)] = d
		self.update()
		return
	# add

	def clear(self):
		self.n, self.rows = 0, bytearray()
		self.update()

	def pause(self, on):
		self.paused = on
		self.update()

	def px(self, hz):
		return round((hz - self.lo) / (self.hi - self.lo) * self.width())

	'''-------------------------------------------------------------------
		spectrum (newest sweep), grid and kHz labels, waterfall, dial line
	-------------------------------------------------------------------'''

	def paintEvent(self, e):
		p = QPainter(self)
		w, s, h = self.width(), self.SPEC, self.SPEC + self.ROWS
		p.fillRect(self.rect(), QColor(styles.decoder['spectrum']))
		if not self.n or self.hi <= self.lo:
			p.setPen(QColor(styles.decoder['labels']))
			p.drawText(QRect(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, 'waiting for the radio\'s scope…')
			return
		raw = bytes(self.rows)		# QImage doesn't copy: keep it alive while drawing
		img = QImage(raw, self.n, self.ROWS, self.n, QImage.Format.Format_Indexed8)
		img.setColorTable(self.colors)
		p.drawImage(QRect(0, s, w, self.ROWS), img)
		span = self.hi - self.lo
		step = next((x for x in (500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000) if span / x <= 10), 500000)
		p.setFont(QFont('DejaVu Sans', 7))
		for f in range((self.lo // step + 1) * step, self.hi, step):
			x = self.px(f)
			p.setPen(QColor(styles.decoder['grid']))
			p.drawLine(x, 0, x, s)
			p.setPen(QColor(styles.decoder['labels']))
			if 30 <= x <= w - 30:		# no half labels at the edges
				p.drawText(QRect(x - 30, h, 60, 14), Qt.AlignmentFlag.AlignCenter, f'{f / 1e6:.4f}'.rstrip('0').rstrip('.') if step < 10000 else f'{f / 1e6:.3f}')
		pts = [QPointF(0, s)] + [QPointF(i * w / (self.n - 1), s - min(c, self.TOP) / self.TOP * s) for i, c in enumerate(self.rows[:self.n])] + [QPointF(w, s)]
		p.setPen(Qt.PenStyle.NoPen)
		p.setBrush(QColor(styles.decoder['fill']))
		p.drawPolygon(QPolygonF(pts))
		if self.lo <= self.dial <= self.hi:
			p.setPen(QPen(QColor(255, 60, 60, 200), 1))
			p.drawLine(self.px(self.dial), 0, self.px(self.dial), h)
		return
	# paintEvent

	'''-------------------------------------------------------------------
		click: the frequency under the mouse, to 10 Hz on narrow spans, 100 Hz on wide ones
	-------------------------------------------------------------------'''

	def mousePressEvent(self, e):
		if self.n and e.button() == Qt.MouseButton.LeftButton:
			hz = self.lo + e.position().x() / self.width() * (self.hi - self.lo)
			snap = 10 if self.hi - self.lo <= 20000 else 100
			self.tune(round(hz / snap) * snap, bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
		return
	# mousePressEvent

# class Waterfall


'''===================================================================================================================================================

==================================================================================================================================================='''

class Helper:		# a decoder program run as a child: Digital out goes to its stdin, its lines to the text box (xara-monitor for VARA, direwolf for packet). Same face as decode.Rtty / Cw
	PROGRAMS = {'VARA': ('xara-monitor', '~/installed/builds/xara/target/release/xara-monitor'), 'Packet': ('direwolf', '~/installed/builds/direwolf/build/src/direwolf'),
		'JS8': ('pacat', '/usr/bin/pacat'), 'ARDOP': ('pacat', '/usr/bin/pacat'), 'PACTOR': ('ssh', '/usr/bin/ssh')}		# kind: (name on the PATH, where else to look). JS8, ARDOP: pacat plays the audio into a sound device JS8Call / ardopcf listens to. PACTOR: ssh carries it to pmon on another machine
	CHATTER = ('Dire Wolf', 'Includes optional', 'Reading config', 'Audio input', 'Audio out', 'Channel 0:', 'Note: PTT', 'Disabled', 'Running this as root', 'End of file')		# direwolf's start-up and exit lines: shack.log only

	'''-------------------------------------------------------------------
		args: the program's options; band: (Hz, Hz) of audio it decodes, shaded on the strip
		add(text): lines to the box; gone(why): it stopped by itself
	-------------------------------------------------------------------'''

	def __init__(self, kind, args, band, add, gone):
		path = self.find(kind)
		self.name, self.band, self.add, self.gone = self.PROGRAMS[kind][0], band, add, gone
		self.tu = decode.Tuner(None, 99, 0, 4)		# spectrum only; a 99 dB squelch never picks a signal
		self.op, self.last = False, ''		# last: its last stderr line
		self.hush = kind == 'Packet'		# direwolf talks a lot before its first packet (VOX advice...): shack.log until then
		self.drop = ()		# line starts not to show at all
		self.clean = kind == 'PACTOR'		# pmon sends stray non-text bytes around its lines: dropped
		self.seen = None		# seen(line): every decoded line, before it's shown (VARA: callsigns for calls.txt)
		self.gain = 1.0		# on the audio it gets
		self.clock = kind == 'VARA'		# xara-monitor's times = its start + samples/48000: they fall behind when audio is lost (or the card runs slow)
		self.fed = 0		# samples written to it
		self.p = QProcess()
		self.p.readyReadStandardOutput.connect(self.out)
		self.p.readyReadStandardError.connect(self.err)
		self.p.finished.connect(self.ended)
		self.p.errorOccurred.connect(lambda e: e == QProcess.ProcessError.FailedToStart and self.gone(f"couldn't run {path}: {self.p.errorString()}"))
		self.p.start(path, args)
		self.t0 = time.time()		# ~ xara-monitor's own start reading
		return
	# __init__

	@classmethod
	def find(cls, kind):		# path of the program, None if it isn't there
		name, alt = cls.PROGRAMS[kind]
		return next((p for p in (shutil.which(name), str(Path(alt).expanduser())) if p and Path(p).is_file()), None)

	'''-------------------------------------------------------------------
		audio in: to the spectrum and to the program's stdin as s16le
	-------------------------------------------------------------------'''

	def __call__(self, x):
		self.tu(x)
		if self.p.state() == QProcess.ProcessState.Running:
			self.p.write((np.clip(x * self.gain, -1, 1) * 32767).astype('<i2').tobytes())
			self.fed += len(x)
		return '', False
	# __call__

	def info(self):
		return ''

	'''-------------------------------------------------------------------
		VARA: lag() = seconds xara-monitor's clock is behind (audio it never got); fix_time() moves its 'HH:MM:SS.d' line starts up by that
	-------------------------------------------------------------------'''

	def lag(self):
		return time.time() - self.t0 - self.fed / decode.FS

	STAMP = re.compile(r'^(\s*)(\d\d):(\d\d):(\d\d)\.(\d)')

	def fix_time(self, line):
		m = self.STAMP.match(line)
		if not m:
			return line
		t = ((int(m[2]) * 60 + int(m[3])) * 60 + int(m[4])) * 10 + int(m[5]) + round(self.lag() * 10)		# tenths of a second
		h, t = divmod(t % 864000, 36000)
		mi, t = divmod(t, 600)
		return f'{m[1]}{h:02d}:{mi:02d}:{t // 10:02d}.{t % 10}' + line[m.end():]
	# fix_time

	def tune(self, f):		# the program finds the signal itself
		return

	'''-------------------------------------------------------------------
		stdout: decoded lines (start-up and exit chatter to shack.log); stderr: to shack.log
		eg, vara decode writes to vara.log
	-------------------------------------------------------------------'''

	def out(self):
		while self.p.canReadLine():
			line = bytes(self.p.readLine()).decode(errors='replace').rstrip()
			if self.clean:
				line = re.sub(r'[\ufffd\x00-\x08\x0b-\x1f\x7f]', '', line)
			if self.hush and ('audio level' in line or re.match(r'\[\d[LH]\] ', line)):		# a packet's first line, or one we sent
				self.hush = False
			if line.startswith(self.drop):
				continue
			if self.hush and ('Hamlib' in line or 'rror' in line):		# PTT or AGW port trouble at start: show it
				self.add(line + '\n')
			elif self.hush or line.startswith(self.CHATTER) or not line.strip():
				log.info(f'{self.name}: {line}') if line.strip() else None
			else:
				if self.clock:
					line = self.fix_time(line)
				if self.seen:
					self.seen(line)
				self.add(line + '\n')			# <<<<<<<<<< decoder output
		return
	# out

	def err(self):
		for line in bytes(self.p.readAllStandardError()).decode(errors='replace').splitlines():
			if line.strip():
				self.last = line.strip()
				log.info(f'{self.name}: {self.last}')
		return
	# err

	def ended(self, code, status):
		self.gone(f'{self.name} stopped: {self.last or "exit " + str(code)} (see shack.log)')
		return

	'''-------------------------------------------------------------------
		EOF on its stdin is the program's cue to finish
	-------------------------------------------------------------------'''

	def close(self):
		self.p.finished.disconnect(self.ended)
		self.p.closeWriteChannel()
		if not self.p.waitForFinished(2000):
			self.p.kill()
			self.p.waitForFinished(1000)
		return
	# close

# class Helper


'''===================================================================================================================================================

==================================================================================================================================================='''

class Link:		# a connected-mode packet session through direwolf's AGW port (direwolf does AX.25 itself): our call registered, connect, text both ways, disconnect
	HDR = struct.Struct('<B3xBxBx10s10sII')		# AGW header: port, kind, PID, call from, call to, data length, user

	'''-------------------------------------------------------------------
		said(text): what the other station sent; event(text, peer): direwolf's news, peer = who we're now connected to, None = not connected
		peer: who we're connected or connecting to; up: connected
	-------------------------------------------------------------------'''

	def __init__(self, port, call, said, event):
		self.port, self.call, self.said, self.event = port, call, said, event
		self.peer, self.up, self.buf, self.tries = None, False, b'', 0
		self.s = QTcpSocket()
		self.s.connected.connect(lambda: self.send('X', pid=0))		# register our call: incoming connects come to us too
		self.s.readyRead.connect(self.read)
		self.s.errorOccurred.connect(self.err)
		self.s.connectToHost('127.0.0.1', port)
		return
	# __init__

	def err(self, e):		# direwolf takes a moment to open its port: try for 10 s
		if self.s.state() == QAbstractSocket.SocketState.ConnectedState or self.tries < 0:
			return
		self.tries += 1
		if self.tries > 20:
			return self.event(f"can't reach direwolf's AGW port {self.port}: {self.s.errorString()}", None)
		QTimer.singleShot(500, lambda: self.tries >= 0 and self.s.state() == QAbstractSocket.SocketState.UnconnectedState and self.s.connectToHost('127.0.0.1', self.port))
		return
	# err

	def send(self, kind, to='', data=b'', pid=0xF0):		# False: direwolf's port isn't open (yet)
		if self.s.state() != QAbstractSocket.SocketState.ConnectedState:
			self.event(f"direwolf isn't ready yet (its AGW port {self.port}): try again in a moment", self.peer if self.up else None)
			return False
		self.s.write(self.HDR.pack(0, ord(kind), pid, self.call.encode(), to.encode(), len(data), 0) + data)
		return True
	# send

	def connect(self, to):
		if not self.send('C', to):
			return False
		self.peer = to
		return True

	def write(self, text):		# a line we typed; the BBS wants CR
		self.send('D', self.peer, text.encode('utf-8', 'replace') + b'\r')
		return

	def bye(self):		# connected: DISC; still connecting: gives up
		self.send('d', self.peer)
		return

	'''-------------------------------------------------------------------
		direwolf's replies: X registered (data 1 = ok), C connected (either way), D their text, d disconnected (by them, us or retries)
	-------------------------------------------------------------------'''

	def read(self):
		self.buf += bytes(self.s.readAll())
		while len(self.buf) >= self.HDR.size:
			port, kind, pid, frm, to, n, user = self.HDR.unpack_from(self.buf)
			if len(self.buf) < self.HDR.size + n:
				break
			data, self.buf = self.buf[self.HDR.size:self.HDR.size + n], self.buf[self.HDR.size + n:]
			kind, frm = chr(kind), frm.split(b'\0')[0].decode(errors='replace')
			text = data.decode('utf-8', 'replace').replace('\0', '').replace('\r\n', '\n').replace('\r', '\n')		# direwolf's own notices end in a NUL
			if kind == 'X' and data[:1] != b'\x01':
				self.event(f'direwolf refused to register {self.call}', None)
			elif kind == 'C':
				self.peer, self.up = frm, True
				self.event(text, frm)
			elif kind == 'D':
				self.said(text)
			elif kind == 'd':
				self.peer, self.up = None, False
				self.event(text, None)
		return
	# read

	def close(self):
		self.tries = -1		# no more reconnecting
		self.s.abort()
		return

# class Link


'''===================================================================================================================================================

==================================================================================================================================================='''

class Direwolf:		# Larry's raw direwolf parser (2024's Direwolf.proc), for right-click > Packet > One line per packet: numbered, 'SRC -> DST   payload', a payload's <0x0d> lines under each other
	pat = re.compile(
		r"""
		^
		(?:\[(?P<timestamp>[^\]]+)\]\s*)?	# Optional [timestamp] prefix
		(?P<sender>[A-Z0-9\-]+)			  # Sender (e.g., W9GM-7)
		>
		(?P<destination>[A-Z0-9,\-\*]+)	  # Destination (now handles commas and asterisks)
		:
		(?P<payload>.*)					   # Payload (rest of the line)
		$
		""",
		re.VERBOSE
	)
	# cnt = 0
	lines = []
	state = 0
	packets = 0
	fxs = 0
	axs = 0

	# nodes = ['PNDBBQ', 'PNDBBS', 'BPQ Packet Switch', 'BBS', 'PLWS', 'JTBBS', 'PVHBBS']
	# cs = Prodict.from_dict({})
	ncs = []				# non callsigns
	# cmds = []
	# apps = ['ARDOP', 'BPQ Node', 'LinBPQ', 'VARA5', 'LinBPQ Network Node', 'BBS', 'NODE', 'CHAT', 'RMS']
	last_lookup_time = time.time()

	STARTSWITHS =	(
							# 'Station address,',
		     				# 'Digi2 Address "" is empty.',
							# 'Audio input level',
	     			 	)
	SKIPS =	[
					'"" is empty',
					# 'audio level',
					'UNKNOWN vendor',
					'is obsolete',
					'(Not AX.25)',		# noise direwolf took for a frame
					'must begin with "T#',
					'just heard',
					'scrutiny. ***',
				]
	W_DST, W_VIA = 9, 12		# column widths: destination, the digipeater path after it (cut with ~ when longer)
	LEVEL = re.compile(r'^(?P<digi>Digipeater )?(?P<who>\S+)(?: \(probably (?P<prob>\S+)\))? audio level = (?P<level>\d+)\((?P<ms>[^)]*)\)\s+(?:(?P<fec>FX\.25|IL2P)\s+)?(?P<bars>\S*)')		# eg  Digipeater KB9PVH audio level = 100(28/28)   FX.25   00000____
	W_TEXT = 70		# text lines longer than this are wrapped at a space (shorter ones are left alone: ASCII art keeps its spacing)

	def __init__(self, rf=None):
		self.packets = 0
		self.rf = rf		# rf(): the transceiver's S-meter for this packet (dB from S9, None = no meter)
		self.sigline, self.sigt = '', 0		# the signal info from the latest 'audio level', shown on its packet's first line; when (monotonic): stale after a second (its packet was skipped)
		self.indent = ' ' * len(self.head(0, '', '', ''))		# where the text column starts: every line under a packet starts there
		self.text = ''		# the last packet's first line of text
		self.after = False		# the last line was a packet: what direwolf prints next about it (APRS decode, errors) goes in the text column
		return

	def wrap(self, text):		# one line of text -> lines no longer than W_TEXT
		if len(text) <= self.W_TEXT:
			return [text]
		return utils.split_long_string(text, self.W_TEXT)

	def signal(self, m):		# direwolf's 'audio level' line -> the signal line: RF, audio level (mark/space), error correction, which decoders got it, digipeater
		sig = self.rf() if self.rf else None
		rf = '-' if sig is None else sunits(sig)
		ms = '(' + m['ms'] + ')'
		fec = m['fec'] or 'AX.25'
		digi = 'digi ' + (m['prob'] or m['who']) if m['digi'] else ''
		return f'rf {rf:7s} audio {m["level"]:>3s} {ms:9s} {fec:5s}  {m["bars"]:10s} {digi}'.rstrip()

	def head(self, n, src, dst, via):		# the fixed columns before the text
		if len(via) > self.W_VIA:
			via = via[:self.W_VIA - 1] + '~'
		return f'{n:4d}  {src:9s} -> {dst:{self.W_DST}s} {via:{self.W_VIA}s}  '

	'''----------------------------------------------------------------------------------------------------
		a direwolf line -> what the text box shows: '' = nothing; a packet's 'audio level' line is dropped
		(the Packet tab still gets it), lines that aren't frames go through as they are
	-------------------------------------------------------------------------------------------------------'''

	def __call__(self, line) -> str:

		if True:
			try:
				s = line.strip()
				if s == '':
					return line

				lv = self.LEVEL.match(s)
				if lv:		# a new packet's first line: what follows isn't about the last one
					self.after = False
					self.sigline, self.sigt = self.signal(lv), time.monotonic()
					return ''
				if any(x in s for x in self.SKIPS):
					return ''
				if s.startswith(self.STARTSWITHS):
					return ''

				m = self.pat.match(s)
				if not m:
					if self.after:		# direwolf about the packet just shown (its APRS decode, an Error:): under its text
						if s in (self.text, self.text[1:].strip()):		# the decode only repeats the text (a status report: '>' and the same words)
							return ''
						return self.indent + ('\n' + self.indent).join(self.wrap(s)) + '\n'
					return line

				self.packets += 1
				self.after = True
				src = m['sender']
				path = m['destination'].split(',')		# TO,DIGI1,DIGI2*
				dst = path[0]
				via = ','.join(path[1:])
				head = self.head(self.packets, src, dst, via)
				parts = re.split(r'<0x0[ad]>', m['payload'])		# the payload's own line breaks
				lines = []
				for p in parts:
					p = p.strip()
					if p:
						lines += self.wrap(p)
				self.text = next((p.strip() for p in parts if p.strip()), '')		# the first line before wrapping, for spotting direwolf's echo of it
				sigline = self.sigline if time.monotonic() - self.sigt < 1 else ''		# direwolf prints the frame right after its level line; our own sent frames have none
				self.sigline = ''
				first = (head + sigline).rstrip()		# number, from, to, via, then the signal info in the text column
				body = ''
				for p in lines:		# the text from the second line on, in the text column
					body += self.indent + p + '\n'
				return '\n' + first + '\n' + body		# a blank line before each packet
			except Exception as e:
				raise
		else:
			try:
				line = line.strip()

				if line == '':
					if Direwolf.state == 1:
						Direwolf.packets += 1			# for easier debugging
						if Direwolf.packets == 42:
							bp += 1
						cnt = 0
						for line in Direwolf.lines:
							cnt += 1
							if cnt == 1:
								if 'FX.25' in line:
									Direwolf.fxs += 1
								else:
									Direwolf.axs += 1
								continue
							if line.startswith('Station address,'):
								continue
							elif line.startswith('Digi2 Address "" is empty.'):
								continue
							elif line.startswith('Audio input level'):
								continue
							match = re.match(Direwolf.pat, line)
							if not match:
								raise ValueError(f"Malformed line: {line}")

							src = match.group("sender")
							a = src.split('-')
							cs = a[0]
							if utils.is_callsign(cs):
								now = time.time()
								dt = now - Direwolf.last_lookup_time
								# info = utils.lookup(cs, db, dt)
								Direwolf.last_lookup_time = time.time()
								# if not cs in Direwolf.cs:
								# 	Direwolf.cs[cs] = info
							else:
								if not src in Direwolf.ncs:
									# Direwolf.ncs.append(src)
									Direwolf.ncs = '1'

							if Direwolf.packets == 1593:
								pass

							dst = match.group("destination")
							if dst == 'MBX':
								pass
							a = dst.split('-')
							cs = a[0]
							if utils.is_callsign(cs):
								now = time.time()
								dt = now - Direwolf.last_lookup_time
								# info = utils.lookup(cs, db, dt)
								Direwolf.last_lookup_time = time.time()
								if not cs in Direwolf.cs:
									# Direwolf.cs[cs] = info
									pass
							else:
								if not dst in Direwolf.ncs:
									Direwolf.ncs.append(dst)

							payload = match.group("payload")

							if dst == "ID" or "<0x0d>" in payload or "ARDOP" in payload or "VARA" in payload:
								style = 'data'
								color = consts.KMAG
								# print()
							else:
								style = 'aprs'
								color = consts.KCYN
							pl = None
							if Direwolf.packets == 2:
								pass
							payload = payload.strip('<0x0d>')
							payload = payload.strip()

							if not 'ARDOP' in Direwolf.apps:
								Direwolf.apps.append('ARDOP')

							if '<0x0d>' in payload:
								a = payload.split('<0x0d>')
								pl = ''
								for s in a:
									if s == '':
										continue
									pl += '\n' + ' '*35 + s.strip()
								payload = pl.strip()

								bp += 1
							s = f'{Direwolf.packets:4d}  {src:9s} ->  {dst:14s}  {payload}'
							# if pl:
							# 	s += pl
							# print(f'{color}{s}{consts.COLOR_RESET}')
							# print(s)
							if style == 'data':
								# print()
								pass

							# print(line[6:])
						# hamutils.parse_raw_direwolf(lines, db, packets)
						Direwolf.lines.clear()
						Direwolf.state = 0
					return s
				# if line == ''


				if '<0x' in line:
					pass

				if ('|' in line or 'FX.25' in line) and Direwolf.state == 0:
					# assert Direwolf.state==0
					Direwolf.lines.append(line)
					Direwolf.state = 1
				elif Direwolf.state == 1:
					Direwolf.lines.append(line)
					pass
				else:
					if 'some scrutiny' in line:
						Direwolf.state = 0
						return ''
					elif line.startswith('QRT'):
						Direwolf.state = 0
						return ''
					elif 'is empty!' in line:
						Direwolf.state = 0
						return ''
					elif 'Attached to AGW client' in line:
						Direwolf.state = 0
						return ''
					else:
						pass



				# print(f'{consts.KMAG}{line}{consts.COLOR_RESET}')
				# parse the line and reformat:
				# hamutils.parse_raw_direwolf(line)
			except Exception as e:
				s = repr(e)
				exc_type, exc_obj, exc_tb = sys.exc_info()
				fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
				s = '%s, %s, %s' % (exc_type, fname, exc_tb.tb_lineno)
				raise Exception(s)
			finally:
				pass
		# if False


	# __call__

# class Direwolf


'''===================================================================================================================================================

==================================================================================================================================================='''

class Ardop(Helper):		# ARDOP by ardopcf in receive-only mode (RXO: every frame it hears, any session): pacat plays the audio into shack-ARDOP, ardopcf records its monitor (it only takes a sound card). Same face as Helper
	PROG = ('ardopcf', '~/installed/builds/ardop/ardopcf')		# name on the PATH, where else to look
	PORT = 8525		# its host port (+1 data), not the usual 8515: a Pat with its own ardopcf keeps that one
	SINK = 'shack_ardop'
	HIDE = ('DataACK', 'IDLE')		# an idle ARQ link trades these every few seconds
	BW = re.compile(r'(200|500|1000|2000)')		# a frame name's bandwidth: ConReq500M, 4PSK.2000.100.E
	FRAME = re.compile(r'\[DecodeFrame\] Frame: (\S+)\s*(?:Decode (PASS|FAIL),\s+Quality= (\d+))?')
	ID = re.compile(r'\[DecodeFrame\] IDFrame: (\S+) \[(\w*)\]')
	RXO = re.compile(r'\s*\[RXO (\w\w)\] (.*)')
	HEX = re.compile(r'\s*([0-9A-F]{2}\s+)*[0-9A-F]{2}\s*')

	'''-------------------------------------------------------------------
		quiet: DataACK and IDLE frames not shown; home: (lat, lon) for an ID frame's distance and bearing, None = no grid
	-------------------------------------------------------------------'''

	def __init__(self, add, gone, quiet, home):
		super().__init__('ARDOP', ['--playback', '--raw', '--format=s16le', '--rate=48000', '--channels=1', f'--device={self.SINK}', '--latency-msec=200'], (500, 2500), add, gone)
		self.quiet, self.home = quiet, home
		self.gain = 0.5		# 6 dB below what the radio sends: ardopcf warns of clipping at 32000 of 32767 (static crashes, the fence)
		self.fr, self.sid, self.id = None, '--', None		# frame being read: [name, quality, extra]; its session ID; (call, grid) of the last ID frame
		self.skip, self.need, self.body = 0, 0, []		# lines to skip (its hex dump); bytes of payload text still to come, and its lines
		self.said, self.got = '', ''		# ardopcf's last line that wasn't a frame; the last frame shown, for the status line
		self.m = QProcess()
		env = QProcessEnvironment.systemEnvironment()
		env.insert('PULSE_SOURCE', f'{self.SINK}.monitor')		# ALSA's pulse device records from here
		self.m.setProcessEnvironment(env)
		self.m.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)		# ALSA's complaints come on stderr
		self.m.readyReadStandardOutput.connect(self.line)
		self.m.finished.connect(self.quit)
		self.m.errorOccurred.connect(lambda e: e == QProcess.ProcessError.FailedToStart and self.gone(f"couldn't run ardopcf: {self.m.errorString()}"))
		self.m.start(self.find_ardop(), ['-m', '-H', 'PROTOCOLMODE RXO', str(self.PORT), 'pulse', 'null'])		# playback null: RXO never sends (NOSOUND would stop capture too)
		return
	# __init__

	@classmethod
	def find_ardop(cls):
		name, alt = cls.PROG
		return next((p for p in (shutil.which(name), str(Path(alt).expanduser())) if p and Path(p).is_file()), None)

	'''-------------------------------------------------------------------
		ardopcf's console, one line at a time. Each frame: '[DecodeFrame] Frame: NAME Decode PASS, Quality= Q' (FAIL: shown at once),
		then '[RXO SID] ...' lines: callsigns (Ping), quality (DataACK/NAK), 'NAME frame received OK.  frameLen = N', the payload as hex
		(skipped) and as text (N bytes, CR made LF, then the line end). Shown once the payload is in
	-------------------------------------------------------------------'''

	def line(self):
		while self.m.canReadLine():
			raw = bytes(self.m.readLine())
			if self.need > 0:		# payload text: counted in bytes, it may hold anything
				self.need -= len(raw)
				self.body.append(raw.decode(errors='replace').rstrip('\r\n'))
				if self.need <= 0:
					self.show()
				continue
			if self.skip:
				self.skip -= 1
				continue
			line = raw.decode(errors='replace').rstrip()
			m = self.FRAME.search(line)
			if m:
				name, how, q = m.groups()
				self.flush()
				if how == 'FAIL':
					self.fr, self.sid = [name, q, 'decode FAILED'], '??'
					self.show()
				else:
					self.fr = [name, q, '']
				continue
			m = self.ID.search(line)
			if m:
				self.id = m.groups()
				continue
			m = self.RXO.match(line)
			if m and self.fr:
				self.sid, what = m.groups()
				if (p := re.search(r"callsigns: '(\S+) (\S+)'.*S:N=(\d+)", what)):		# Ping
					self.fr[2] = f'{p[1]} > {p[2]}  S:N {p[3]} dB'
				elif (p := re.search(r'S:N=(\d+) and Quality=(\d+)', what)):		# PingAck
					self.fr[2] = f'S:N {p[1]} dB'
				elif (p := re.search(r'quality \((\d+)/100\)', what)):		# DataACK / DataNAK: how well the other side decoded
					self.fr[1] = p[1]
				elif (p := re.search(r'frame received OK\.\s+frameLen = (\d+)', what)):
					if p[1] == '0':
						self.show()
				elif 'as hex values' in what:
					self.skip = 1
				elif (p := re.match(r'(\d+) bytes of data as UTF-8 text:', what)):
					self.need, self.body = int(p[1]) + 1, []		# + the line end
				elif 'not appear to be valid UTF-8' in what:
					self.fr[2] = 'binary (compressed mail?)'
					self.show()
				continue
			if line.startswith(('[Frame Type Decode', '[RXO DecodeSessionID', 'bytFrameData1')) or self.HEX.fullmatch(line) or not line.strip() or m:		# the frame's type before its body, session IDs, raw bytes of FEC / failed frames; ConAck's leader lengths
				continue
			self.said = line.strip()
			log.info(f'ardopcf: {self.said}')
		return
	# line

	def flush(self):		# a frame still waiting (its payload line never came): shown as it is
		if self.fr:
			self.show()
		return

	'''-------------------------------------------------------------------
		one frame: 'UTC  [session]  name  Q  extra', a text payload under it (ConReq: the calls, ID: call, grid, distance and bearing)
	-------------------------------------------------------------------'''

	def show(self):
		name, q, extra = self.fr
		body, self.fr, self.need, self.body = self.body, None, 0, []
		if name.startswith('ConReq') and body:		# 'N7RTS KB1ABC': caller, called
			extra, body = ' > '.join(body[0].split()[:2]), []
		elif name == 'IDFrame' and self.id:
			call, grid = self.id
			extra, body = f'{call}  {grid}', []
			ll = grid_ll(grid) if grid else None
			if ll and self.home:
				mi, brg = path(self.home, ll)
				extra += f'  {mi:,.0f} mi  {brg:.0f}°'
		if (b := self.BW.search(name)):
			self.band = (1500 - int(b[1]) / 2, 1500 + int(b[1]) / 2)
		t = time.strftime('%H:%M:%S', time.gmtime())
		self.got = f'{t} {name}'
		if self.quiet and name in self.HIDE:
			return
		head = f"{t}  [{self.sid}]  {name:<16} {'Q' + q if q else '':<5} {extra}".rstrip()
		while body and not body[-1]:
			body.pop()
		self.add(head + '\n' + ''.join(f'    {b}\n' for b in body))
		return
	# show

	def info(self):
		return 'ardopcf listening' + (f'   last frame {self.got}' if self.got else '')

	'''-------------------------------------------------------------------
		ardopcf ended by itself: usually the sound device (no ALSA pulse device: apt install libasound2-plugins)
	-------------------------------------------------------------------'''

	def quit(self, code, status):
		self.line()
		hint = '. No ALSA pulse device? (apt install libasound2-plugins)' if 'pulse' in self.said.lower() or 'InitSound' in self.said else ''
		self.gone(f'ardopcf stopped: {self.said or "exit " + str(code)}{hint} (see shack.log)')
		return

	def close(self):
		self.m.finished.disconnect(self.quit)
		self.m.terminate()		# SIGTERM: it shuts down cleanly
		if not self.m.waitForFinished(2000):
			self.m.kill()
			self.m.waitForFinished(1000)
		super().close()
		return
	# close

# class Ardop


'''===================================================================================================================================================

==================================================================================================================================================='''

class Wsjt:		# FT4 / FT8 by WSJT-X's jt9, WSPR by its wsprd: each period's audio (on the UTC clock) to a 12 kHz WAV, the program decodes it. Same face as decode.Rtty / Cw
	RATE, OUT = 48000, 12000		# audio in, the programs' rate
	PERIOD = {'FT4': 7.5, 'FT8': 15.0, 'WSPR': 120.0}		# WSPR: even minutes
	PROG = {'FT4': ['jt9', '-5', '-d', '3'], 'FT8': ['jt9', '-8', '-d', '3'], 'WSPR': ['wsprd', '-f', '0']}		# wsprd -f 0: its frequencies come out as the audio offset
	LINE = re.compile(r'^\d{6}\s+(-?\d+)\s+(-?[\d.]+)\s+(\d+)\s+[~+]\s+(.*?)\s*$')		# jt9: hhmmss snr dt freq ~ message
	WLINE = re.compile(r'^\d{4}\s+(-?\d+)\s+(-?[\d.]+)\s+([\d.]+)\s+(-?\d+)\s+(.*?)\s*$')		# wsprd: hhmm snr dt MHz drift message
	TAPS = np.sinc(2 * 4800 / RATE * (np.arange(101) - 50)) * np.hamming(101) * 2 * 4800 / RATE		# low-pass before taking every 4th sample

	'''-------------------------------------------------------------------
		add(text): decoded lines to the box; gone(why): jt9 can't run
	-------------------------------------------------------------------'''

	def __init__(self, kind, add, gone, home=None):		# home: (lat, lon) of my grid, for distance and bearing
		self.kind, self.add, self.gone, self.home = kind, add, gone, home
		self.period = self.PERIOD[kind]
		self.band = (200, 3000)		# jt9 looks across the whole passband
		self.tu = decode.Tuner(None, 99, 0, 4)		# spectrum only
		self.op = False
		self.name = self.PROG[kind][0]
		self.dir = tempfile.mkdtemp(prefix=f'shack-{self.name}-')		# its WAVs, decoded.txt, FFTW wisdom, wsprd's hash table of callsigns
		self.chunks, self.t0, self.n = [], None, 0		# audio kept: (stream index of its first sample, samples); UTC of sample 0 of the stream; samples so far
		self.next = None		# start (UTC) of the next period to decode
		self.p, self.got, self.when, self.skipped = None, 0, '', 0		# program running; decodes in its last period, that period's time; periods skipped (still busy)
		return
	# __init__

	'''-------------------------------------------------------------------
		audio in. The stream's clock is pinned to UTC by when the audio arrives: the earliest arrival seen
		(least delay) sets it, a later one pulls it slowly (the sound card's clock drifts against the computer's)
	-------------------------------------------------------------------'''

	def __call__(self, x):
		self.tu(x)
		self.n += len(x)
		a = time.time() - self.n / self.RATE
		self.t0 = a if self.t0 is None or a < self.t0 else self.t0 + (a - self.t0) * 0.01
		self.chunks.append((self.n - len(x), x.astype(np.float32)))
		if self.next is None:		# the first whole period after starting
			self.next = (int((self.t0 + self.chunks[0][0] / self.RATE) / self.period) + 1) * self.period
		end = self.t0 + self.n / self.RATE
		if end >= self.next + self.period:
			self.decode(self.next)
			self.next += self.period
			if end >= self.next + self.period:		# fell behind (window stalled): skip to the current period
				self.next = (int(end / self.period) - 1) * self.period
		first = round((self.next - self.t0 - 1) * self.RATE)		# keep from a second before the next period
		while self.chunks and self.chunks[0][0] + len(self.chunks[0][1]) < first:
			self.chunks.pop(0)
		return '', False
	# __call__

	'''-------------------------------------------------------------------
		the period starting at t (UTC) to a WAV named for its time (jt9 prints that time), and jt9 on it
	-------------------------------------------------------------------'''

	def decode(self, t):
		if self.p:		# the last one isn't done: skip this period
			self.skipped += 1
			log.info(f'{self.name} still busy, skipped the {time.strftime("%H:%M:%S", time.gmtime(t))} period')
			return
		i0 = round((t - self.t0) * self.RATE)
		i1 = i0 + round(self.period * self.RATE)
		x = np.zeros(i1 - i0, np.float32)		# silence where there's no audio (started late, dropouts)
		for j, c in self.chunks:
			a, b = max(i0, j), min(i1, j + len(c))
			if a < b:
				x[a - i0:b - i0] = c[a - j:b - j]
		y = np.convolve(x, self.TAPS, 'same')[::self.RATE // self.OUT]
		self.stamp = time.strftime('%H:%M:%S' if self.period < 60 else '%H:%M', time.gmtime(t)) + (('.5' if t % 1 else '.0') if self.period % 1 else '')		# FT4 periods start on the half second
		name = str(Path(self.dir) / (time.strftime('%y%m%d_%H%M%S' if self.period < 60 else '%y%m%d_%H%M', time.gmtime(t)) + '.wav'))		# the programs print this time
		with wave.open(name, 'wb') as w:
			w.setnchannels(1)
			w.setsampwidth(2)
			w.setframerate(self.OUT)
			w.writeframes((np.clip(y, -1, 1) * 32767).astype('<i2').tobytes())
		self.wav = name
		self.p = QProcess()
		self.p.setWorkingDirectory(self.dir)
		self.p.finished.connect(self.done)
		self.p.errorOccurred.connect(lambda e: e == QProcess.ProcessError.FailedToStart and self.gone(f"couldn't run {self.name}: {self.p.errorString()}"))
		prog, *args = self.PROG[self.kind]
		self.p.start(prog, [*args, '-a', self.dir, *(['-t', self.dir] if prog == 'jt9' else []), name])
		return
	# decode

	'''-------------------------------------------------------------------
		jt9's lines -> 'HH:MM:SS  SNR dB  DT s  offset Hz  message' (DT: the signal's time against the clock);
		wsprd's -> 'HH:MM  SNR dB  DT s  offset Hz  drift Hz/min  call grid dBm (W)'
	-------------------------------------------------------------------'''

	def done(self, code, status):
		out = bytes(self.p.readAllStandardOutput()).decode(errors='replace').splitlines()
		err = bytes(self.p.readAllStandardError()).decode(errors='replace').strip()
		self.p = None
		Path(self.wav).unlink(missing_ok=True)
		if err:
			log.info(f'{self.name}: {err}')
		self.got, self.when = 0, self.stamp
		for line in out:
			m = self.WLINE.match(line) if self.kind == 'WSPR' else self.LINE.match(line)
			if not m:
				continue
			if self.kind == 'WSPR':
				snr, dt, mhz, drift, msg = m.groups()
				w = msg.split()
				if w and w[-1].lstrip('-').isdigit():		# power, dBm
					msg += f' dBm ({self.watts(int(w[-1]))})'
				self.add(f'{self.stamp}  {int(snr):+3d} dB  {float(dt):+.1f} s  {round(float(mhz) * 1e6):4d} Hz  {int(drift):+d}  {self.pad(msg, 30)}\n')
			else:
				snr, dt, hz, msg = m.groups()
				self.add(f'{self.stamp}  {int(snr):+3d} dB  {float(dt):+.1f} s  {int(hz):4d} Hz  {self.pad(msg, 24)}\n')
			self.got += 1
		return
	# done

	def far(self, msg):		# '   560 mi  352°' for the grid in a message (RR73 looks like one but isn't)
		g = [x for x in GRID.findall(msg) if x != 'RR73']
		ll = grid_ll(g[-1]) if g and self.home else None
		if not ll:
			return ''
		mi, brg = path(self.home, ll)
		return f'{mi:6,.0f} mi  {brg:3.0f}°'

	def pad(self, msg, n):		# the message, then its distance and bearing in a column (n: usual message width)
		f = self.far(msg)
		return f'{msg:<{n}}  {f}' if f else msg

	def watts(self, dbm):
		w = 10 ** (dbm / 10) / 1000
		return f'{w * 1000:.3g} mW' if w < 1 else f'{w:.3g} W'

	def info(self):
		if self.when:
			t = f'{self.kind}  {self.name}: {self.got} at {self.when}'
		elif self.next is not None:
			t = f"{self.kind}  {self.name}: first decode after {time.strftime('%H:%M:%S', time.gmtime(self.next + self.period))} UTC"
		else:
			t = f'{self.kind}  {self.name}: waiting for audio'
		return t + (f'  ({self.skipped} skipped: {self.name} too slow)' if self.skipped else '')

	def tune(self, f):		# jt9 looks at the whole passband
		return

	def close(self):
		if self.p:
			self.p.finished.disconnect(self.done)
			self.p.kill()
			self.p.waitForFinished(1000)
			self.p = None
		shutil.rmtree(self.dir, ignore_errors=True)
		return
	# close

# class Wsjt


'''===================================================================================================================================================

==================================================================================================================================================='''

class Scope:		# Spectrum button with no decoder running: the strip alone. Same face as decode.Rtty / Cw
	def __init__(self):
		self.tu = decode.Tuner(None, 99, 0, 4)		# a 99 dB squelch never picks a signal: no marker, no squelch line
		self.op = False

	def __call__(self, x):
		self.tu(x)
		return '', False

	def info(self):
		return ''

	def tune(self, f):
		return

# class Scope


'''===================================================================================================================================================

==================================================================================================================================================='''

class Qrz:		# QRZ lookups in one browser window that's reused: its own Chromium profile with the DevTools port open (Chromium 136+ ignores the port on the everyday profile)
	FLATPAK = 'io.github.ungoogled_software.ungoogled_chromium'
	BROWSER = ['flatpak', 'run', FLATPAK]		# Debian's instead: ['chromium'], with PROFILE = Path.home() / '.config' / 'shack-qrz'
	PORT = 9224		# not 9223: Debian's Chromium had that one, and a window of it still open would be reused for every lookup
	PROFILE = Path.home() / '.var' / 'app' / FLATPAK / 'config' / 'shack-qrz'		# logged in to QRZ here once, remembered; inside the flatpak's own folder, the one place its sandbox can always write

	'''-------------------------------------------------------------------
		the QRZ window's tab to this callsign's page; no window (closed, or never opened): start it on the page
	-------------------------------------------------------------------'''

	def show(self, call):
		url = f'https://www.qrz.com/db/{call}'
		try:
			pages = [t for t in self.get('/json/list') if t.get('type') == 'page']
		except OSError:		# not running
			return self.start(url)
		t = next((t for t in pages if 'qrz.com' in t.get('url', '')), pages[0] if pages else None)
		try:
			if not t:		# running, no tab left
				return self.get('/json/new?' + url, 'PUT')
			self.send(t['webSocketDebuggerUrl'], {'id': 1, 'method': 'Page.navigate', 'params': {'url': url}})
			self.get('/json/activate/' + t['id'])		# to the front
		except (OSError, KeyError, ValueError) as e:
			log.info(f'QRZ window: {e!r}')
			return self.start(url)
		return
	# show

	def start(self, url):
		self.PROFILE.mkdir(parents=True, exist_ok=True)
		ok, pid = QProcess.startDetached(self.BROWSER[0], [*self.BROWSER[1:], f'--user-data-dir={self.PROFILE}', f'--remote-debugging-port={self.PORT}', '--no-first-run', '--new-window', url])
		log.info(f'QRZ window: started {self.BROWSER[0]}, pid {pid}' if ok else f"QRZ window: couldn't start {self.BROWSER[0]}")
		if not ok:		# fall back on the desktop's browser (a new tab)
			QDesktopServices.openUrl(QUrl(url))
		return

	def get(self, path, method='GET'):		# the DevTools HTTP endpoints: JSON (activate answers plain text)
		with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{self.PORT}{path}', method=method), timeout=1) as r:
			b = r.read()
		try:
			return json.loads(b)
		except ValueError:
			return b.decode(errors='replace')

	'''-------------------------------------------------------------------
		one DevTools command over the tab's websocket (no Origin header, so Chromium takes it), its reply ignored
	-------------------------------------------------------------------'''

	def send(self, ws, msg):
		m = re.match(r'ws://([^:/]+):(\d+)(/.*)', ws)
		host, port, path = m.group(1), int(m.group(2)), m.group(3)
		with socket.create_connection((host, port), timeout=1) as s:
			key = base64.b64encode(os.urandom(16)).decode()
			s.sendall(f'GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'.encode())
			head = b''
			while b'\r\n\r\n' not in head:
				c = s.recv(1024)
				if not c:
					raise OSError('websocket closed during the handshake')
				head += c
			if b' 101 ' not in head.split(b'\r\n')[0]:
				raise OSError(head.split(b'\r\n')[0].decode(errors='replace'))
			data, mask = json.dumps(msg).encode(), os.urandom(4)
			n = len(data)
			hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + n.to_bytes(2, 'big'))		# one text frame, masked (client to server)
			s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
			try:
				s.recv(4096)		# the reply: the navigation has been taken
			except socket.timeout:
				pass
		return
	# send

# class Qrz


'''===================================================================================================================================================

==================================================================================================================================================='''

class Text(QPlainTextEdit):		# the decoded text: read-only, a double-click hands the text cursor there to double(cursor)
	double = None

	def mouseDoubleClickEvent(self, e):
		super().mouseDoubleClickEvent(e)
		if self.double:
			self.double(self.cursorForPosition(e.position().toPoint()))
		return

# class Text


'''===================================================================================================================================================

==================================================================================================================================================='''

class Decoders:		# decode.py's RTTY and CW, VARA (xara-monitor), ARDOP (ardopcf), packet (direwolf) and JS8 (JS8Call), on Digital out (UDP 7355): a row of controls (self.row), the spectrum (self.spec) and the text they decode (self.text)
	KINDS = ['Off', 'RTTY', 'CW', 'PSK31', 'Olivia', 'VARA', 'ARDOP', 'PACTOR', 'Packet', 'JS8', 'FT4', 'FT8', 'WSPR']
	PMON = 'spi'		# PACTOR: the Pi running SCS's pmon (armhf only); shack's audio goes there over ssh into its ALSA loopback, pmon's lines come back
	PMONCMD = "script -qfc 'pmon -a plughw:Loopback,1,0' /dev/null </dev/null & p=$!; aplay -q -D plughw:Loopback,0,0 -t raw -f S16_LE -r 48000 -c 1; kill $p"		# script: a terminal for pmon, else its lines sit in a 4 KB buffer (stdbuf can't: 64-bit, pmon 32). Subdevice 0 both ends: a stray pmon = busy, not silence. EOF from shack ends aplay, then pmon
	WSJT = ('FT4', 'FT8', 'WSPR')		# by jt9 and wsprd (WSJT-X's)
	SINK, JS8PORT = 'shack_js8', 2242		# JS8: null sink JS8Call's input listens to (its monitor); JS8Call's UDP API port
	PACKET = {300: (1550, 1850), 1200: (1150, 2250)}		# baud: audio band, mark and space tones inside it
	AGW = 8000		# direwolf's AGW port while packet can transmit: connects go through it
	NAGS = ('ERROR!!!  Unknown APRS Data Type', 'Use of "ID" in the destination', 'Tell the sender')		# direwolf on packets that aren't APRS (BBS and node IDs)
	CALLS = HERE / 'calls.txt'		# callsigns xara-monitor shows by name instead of #hash (--calls); shack adds the ones it hears, you can add your own
	NAMED = re.compile(r'CQFRAME (\S+)|HEADER (\S+) → (\S+):|link (\S+) → (\S+) ended')		# xara-monitor lines that carry callsigns
	DE = re.compile(r'\bde ([A-Z0-9]+(?:-\d{1,2})?)\b')		# VarAC text: '15 de N5IMC <R+08>'
	CALL = re.compile(r'(?=.*\d)(?=.*[A-Z])[A-Z0-9]{3,7}(-\d{1,2})?')		# a callsign, not a #hash
	LEVEL = re.compile(r'^(Digipeater )?(\S+)(?: \(probably (\S+)\))? audio level = (\d+)\(\S*\)\s+(FX\.25|IL2P)?')		# direwolf's first line for a packet: who was on the air, how loud, error correction
	FRAME = re.compile(r'^\[\d[^\]]*\] ([A-Z0-9-]+)>([A-Z0-9-]+)(?:,[A-Z0-9-]+\*?)*:(.*)')		# its second: '[0.2] FROM>TO,DIGI*:' and what the packet holds
	LINK = re.compile(r'^\((I|RR|RNR|REJ|SREJ|SABME?|DISC|DM|UA|FRMR|XID|TEST) ')		# a connected-mode frame; anything else is a beacon's text
	SVC = (('bbs', re.compile(r'mail for|\bbbs\b|\bpbbs\b|\[[A-Za-z0-9]+-[\w.]+(-[\w$#]+)?\]', re.I)),		# what a packet's text says its station offers; [BPQ-6.0.22-...] is a BBS greeting
		('node', re.compile(r'\bnode\b|net/?rom', re.I)), ('em', re.compile(r'winlink|\brms\b|e-?mail', re.I)), ('chat', re.compile(r'\bchat\b', re.I)))

	'''-------------------------------------------------------------------
		digi(): turns the radio's Digital out on
	-------------------------------------------------------------------'''

	def __init__(self, digi, port=7355):
		self.digi, self.port = digi, port
		self.alsa = None		# the open radio's USB sound card (ALSA name); None = the RSPdx's Digital out on UDP
		self.rec, self.carry, self.last = None, b'', ''		# arecord reading it; half a sample left over; arecord's last stderr line
		self.sk, self.dec, self.heard = None, None, 0
		self.kind, self.baud, self.shift, self.rev = 'RTTY', 1000 / 22, 170, False
		self.sq = {'RTTY': 6, 'CW': 10, 'PSK31': 2}		# squelch dB per decoder (PSK31: low, its quality gate does most of the work)
		self.hf = 2300		# VARA bandwidth
		self.vdump, self.vverb = False, False		# xara-monitor --dump (whole payloads, not 100-character previews), --verbose (unconfirmed frames too)
		self.pk = 300		# packet baud
		self.om, self.ocont = '8/250', False		# Olivia tones/bandwidth; Contestia instead
		self.hide = True		# hide direwolf's complaints about non-APRS packets
		self.aquiet = True		# ARDOP: don't show DataACK and IDLE frames
		self.grid = 'EM85RP'		# mine, for FT4/FT8/WSPR distance and bearing (right-click > My grid)
		self.win = {}		# decoder: [Hz, Hz] auto tune looks in (right-click the strip > Ignore above / below)
		self.lookup = Qrz()		# double-click a callsign: its QRZ page, in one window
		self.api, self.js8 = None, None		# JS8: socket JS8Call's API sends to; (when it last pinged, its version)
		self.js8pid = None		# JS8Call when shack started it: closed when JS8 stops (one started by hand is left running)
		self.scope, self.ready = False, False		# Spectrum button: strip on with no decoder; a radio is open (shack sets it)
		self.wav, self.wavn = None, 0		# Record button: the open /tmp/x.wav, samples written to it
		self.oniq, self.iq = None, False		# oniq(file or None): the RSPdx starts / stops recording I/Q (shack sets it); iq: it is, alongside the audio
		self.lines = {}		# JS8: offset Hz: (cursor at the start of that message's line, when its last frame came, the line's start); a message's frames join one line
		self.logging, self.logged = False, None		# Log button; logged: the decoder the file's last header was for
		self.rf = None		# rf(): the transceiver's S-meter at its highest for the last packet, dB from S9, None = no meter (shack sets it)
		self.onheard, self.pkh = None, None		# onheard(call, decoder, level, fec, via, text, svc): a station was heard (shack sets it); pkh: direwolf's 'audio level' line, waiting for its packet line
		self.ptx = None		# packet transmit: (card, PTT port) while it's on, None = receive only
		self.ontx, self.txoff = None, None		# ontx(on): right-click > Packet > Transmit; txoff(): transmit ended with the decoder (shack sets both)
		self.link = None		# the AGW Link while packet can transmit
		self.mon = False		# right-click > Packet > Monitor while connected: direwolf's frame lines show during a session too
		self.compact, self.dw = False, None		# right-click > Packet > One line per packet: direwolf's lines through Direwolf (self.dw, new with each direwolf)
		self.load()
		self.pick = QComboBox(toolTip='Decode Digital out: RTTY, CW, PSK31, Olivia, VARA, ARDOP (ardopcf), PACTOR (pmon on spi), packet, JS8 (through JS8Call), FT4, FT8 (jt9) or WSPR (wsprd). Right-click for settings')
		self.pick.addItems(self.KINDS)
		self.pick.activated.connect(lambda i: self.start(self.pick.itemText(i)))
		self.sql = QSpinBox(minimum=0, maximum=30, suffix=' dB', keyboardTracking=False, enabled=False, toolTip='Squelch: decodes when the signal is this far above the noise, stops 3 dB lower')
		self.sql.valueChanged.connect(self.set_sq)
		self.status = QLabel(NODECODE and f"can't decode: {NODECODE}" or '')
		self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)		# changing text mustn't resize the window
		self.row = QWidget(toolTip='Right-click: Auto tune, RTTY, VARA and packet settings, Clear', objectName='decoderRow')
		self.row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)		# a plain QWidget paints a style sheet background only with this
		self.row.setStyleSheet(f"QWidget#decoderRow {{ background-color: {styles.decoder['row']}; border-radius: 4px; }}")		# colors: styles.py
		h = QHBoxLayout(self.row)
		h.setContentsMargins(4, 2, 4, 2)
		h.addWidget(self.pick)
		h.addWidget(self.sql)
		self.pauseb = QPushButton('Pause', checkable=True, toolTip='Freeze the spectrum strip and the waterfall (for a closer look); click again to go on. Decoding carries on')		# Shack.freeze
		h.addWidget(self.pauseb)
		self.logb = QPushButton('Log', checkable=True, checked=self.logging, toolTip='Keep what the decoder shows in a file next to shack.py: rtty.log, cw.log, psk31.log, olivia.log, vara.log, ardop.log, packet.log, js8.log...')
		self.logb.clicked.connect(self.set_log)
		h.addWidget(self.logb)
		self.level = QLabel(toolTip='Peak audio level coming from the radio over the last second, dB below full scale. Orange: close to the top; red: clipping (turn RF gain, or the radio\'s USB audio level, down)')
		self.level.setFont(QFont('DejaVu Sans Mono', 10))
		self.level.setFixedWidth(self.level.fontMetrics().horizontalAdvance(' -99 dBFS '))		# changing text mustn't move the status
		self.peaks = []		# (time, peak) of the last second's audio
		h.addWidget(self.level)
		h.addWidget(self.status, 1)
		self.text = Text(readOnly=True, toolTip='Decoded text. Double-click a callsign to look it up on QRZ. Scroll up to read back; right-click for Clear and settings')
		self.text.double = lambda c: (call := self.call_at(c)) and self.qrz(call)
		self.text.setMaximumBlockCount(500)
		self.text.setFont(QFont('DejaVu Sans Mono', 11))
		self.text.setStyleSheet(f"QPlainTextEdit {{ background-color: {styles.decoder['text']}; }}")
		self.scopeb = QPushButton('Spectrum', checkable=True, checked=self.scope, toolTip='The audio spectrum strip, also with no decoder running (voice, music...)')
		self.scopeb.clicked.connect(self.set_scope)
		self.recb = QPushButton('Record', checkable=True, toolTip=f'Record the radio\'s receive audio to {self.WAV} (replaces the last one); click again to stop')
		self.recb.clicked.connect(self.set_rec)
		self.iqb = QPushButton('Record I/Q', checkable=True, toolTip=f'RSPdx: record its I/Q stream to {self.IQ} and the audio to {self.WAV}; click again to stop and name them')
		self.iqb.clicked.connect(lambda on: self.set_rec(on, True))
		self.callbox = QLineEdit(maxLength=9, placeholderText='call', toolTip='The station to connect to (with its -SSID if it has one): a BBS, node or anyone. Enter or Connect')
		self.callbox.setFixedWidth(self.callbox.fontMetrics().horizontalAdvance('W' * 11))
		self.callbox.returnPressed.connect(self.con_click)
		self.conb = QPushButton('Connect', toolTip='Connect to that station through direwolf (it retries on its own); Disconnect ends the session, or gives up while still trying')
		self.conb.clicked.connect(self.con_click)
		self.line = QLineEdit(enabled=False, placeholderText='type here, Enter sends', toolTip='A line to the connected station (sent with a CR at the end)')
		self.line.returnPressed.connect(self.type_send)
		self.conrow = QWidget(visible=False)		# packet's Connect line: there while packet can transmit
		h = QHBoxLayout(self.conrow)
		h.setContentsMargins(0, 0, 0, 0)
		h.addWidget(QLabel('Connect to'))
		h.addWidget(self.callbox)
		h.addWidget(self.conb)
		h.addWidget(self.line, 1)
		self.spec = Spectrum(self)
		for w in (self.row, self.spec, self.text):
			w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
			w.customContextMenuRequested.connect(lambda pos, w=w: self.menu(w, pos))
		self.timer = QTimer(timeout=self.poll, interval=100)
		return
	# __init__

	'''-------------------------------------------------------------------
		Off, RTTY, CW, VARA, Packet or JS8 from the combo; RSPdx: listens on the port and turns Digital out on; CI-V radio: records its USB sound card
	-------------------------------------------------------------------'''

	def start(self, kind):
		self.stop(keep=kind == 'JS8')		# JS8 again: keep its JS8Call
		if kind == 'Off':
			if not ((self.scope or self.wav) and self.ready):
				return
			kind = 'Scope'		# no decoder, Spectrum or Record on: the audio and the strip alone
		if not decode:
			quiet(self.pick, self.pick.setCurrentIndex, 0)
			return self.status.setText(f"can't decode: {NODECODE}")
		if kind in self.WSJT and not shutil.which(Wsjt.PROG[kind][0]):
			quiet(self.pick, self.pick.setCurrentIndex, 0)
			return self.status.setText(f"can't find {Wsjt.PROG[kind][0]} (it comes with WSJT-X: apt install wsjtx)")
		if kind in Helper.PROGRAMS and not Helper.find(kind) or kind == 'ARDOP' and not Ardop.find_ardop():
			quiet(self.pick, self.pick.setCurrentIndex, 0)
			name, alt = Ardop.PROG if kind == 'ARDOP' and Helper.find(kind) else Helper.PROGRAMS[kind]
			return self.status.setText(f"can't find {name} (on the PATH or at {alt})")
		why = self.record() if self.alsa else self.bind()
		if why:
			self.off()
			return self.status.setText(why)
		if kind in ('JS8', 'ARDOP'):
			why = self.js8open() if kind == 'JS8' else self.sink(Ardop.SINK, 'shack-ARDOP')
			if why:
				self.off()		# closes what's open
				return self.status.setText(why)
		self.kind, self.heard = kind, time.monotonic()
		self.make()
		self.sql.setEnabled(kind in self.sq)		# the programs and Olivia have no squelch here
		self.digi()
		self.timer.start()
		if kind != 'Scope':
			self.save()
		return
	# start

	'''-------------------------------------------------------------------
		the audio: the RSPdx's Digital out on UDP, or arecord on a CI-V radio's USB sound card. return why not, or None
	-------------------------------------------------------------------'''

	def bind(self):
		self.sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		try:
			self.sk.bind(('127.0.0.1', self.port))
		except OSError as e:
			return f'UDP {self.port}: {e.strerror}. Is decode.py, xara-monitor or nc listening?'
		self.sk.setblocking(False)
		self.sk.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)		# room for a few seconds of audio if the window stalls (the kernel may cap it)
		return None
	# bind

	def record(self):
		self.rec, self.carry, self.last = QProcess(), b'', ''
		self.rec.readyReadStandardError.connect(self.recerr)
		self.rec.finished.connect(self.recended)
		self.rec.start('arecord', ['-q', '-D', self.alsa, '-f', 'S16_LE', '-r', '48000', '-c', '1', '-t', 'raw'])
		if not self.rec.waitForStarted(2000):
			return f"couldn't run arecord: {self.rec.errorString()}"
		return None
	# record

	def recerr(self):
		for line in bytes(self.rec.readAllStandardError()).decode(errors='replace').splitlines():
			if line.strip():
				self.last = line.strip()
				log.info(f'arecord: {self.last}')
		return

	def recended(self, code, status):		# by itself: the card is busy or gone
		self.recerr()
		self.rec = None		# already gone
		self.off()		# and whatever decoder program was running
		self.status.setText(f"arecord stopped: {self.last or 'exit ' + str(code)}. Radio's USB audio {self.alsa} plugged in? Another program using it?")
		return

	'''-------------------------------------------------------------------
		calls.txt: made the first time with a comment saying what it is; read again at each xara-monitor start, so hand edits count
	-------------------------------------------------------------------'''

	def calls(self):
		if not self.CALLS.exists():
			self.CALLS.write_text('# callsigns xara-monitor shows by name instead of #hash; one per line, # starts a comment.\n# shack adds the ones it hears (CQ, header, link lines); add yours and the stations you work.\n')
		self.known = {l.split('#')[0].split(',')[0].strip().upper() for l in self.CALLS.read_text().splitlines()} - {''}
		return self.CALLS
	# calls

	def named(self, line):		# a callsign xara-monitor decoded and calls.txt doesn't have yet: add it
		m = self.NAMED.search(line)
		for c in (*(m.groups() if m else ()), *self.DE.findall(line)):
			if c and self.CALL.fullmatch(c) and c not in self.known:
				self.known.add(c)
				with open(self.CALLS, 'a') as f:
					f.write(c + '\n')
				log.info(f'calls.txt: added {c}')
		return
	# heard

	'''-------------------------------------------------------------------
		direwolf's two lines per packet -> onheard: the station that sent it; through a digipeater, the digipeater (it's the one on the air here) and the sender with via
		eg  N3VEM-1 audio level = 35(8/8)   FX.25   00000____
		    [0.2] N3VEM-1>MAIL:Mail For:N3VEM
	-------------------------------------------------------------------'''

	def pkseen(self, line):
		m = self.LEVEL.match(line)
		if m:
			self.pkh = (m[3] or m[2], int(m[4]), m[5] or '', bool(m[1]))
			return
		m = self.FRAME.match(line)
		if m and self.onheard and m[1].split('-')[0] != Modem.CALL:		# not what we sent
			src, dst, info = m[1], m[2], m[3]
			who, level, fec, digi = self.pkh or (src, None, '', False)
			self.pkh = None
			text = '' if self.LINK.match(info) else re.sub(r'(<0x0[ad]>)+', ' ', info).strip()[:200]
			svc = {k for k, rx in self.SVC if rx.search(info)}
			svc |= {'bbs'} if dst == 'MAIL' else {'node'} if dst == 'NODES' else {'aprs'} if dst.startswith('AP') and not self.LINK.match(info) else set()
			if digi and who != src:
				if not who.startswith(('WIDE', 'RELAY', 'TRACE')):		# an alias, not a station
					self.onheard(who, 'Packet', level, fec, svc=('digi',))
				self.onheard(src, 'Packet', level, fec, who, text, svc)
			else:
				self.onheard(src, 'Packet', level, fec, '', text, svc)
		return
	# pkseen

	'''-------------------------------------------------------------------
		a new decoder with the current settings
	-------------------------------------------------------------------'''

	def make(self, center=None):		# center: stay locked there; None = tune itself
		self.logged = None		# a new header in the log (VARA: its bandwidth may have changed)
		if self.kind == 'Scope':
			self.dec = Scope()
			return
		if self.kind in self.WSJT:
			self.dec = Wsjt(self.kind, self.add, self.died, grid_ll(self.grid))
			return self.newline()
		if self.kind in Helper.PROGRAMS:
			if self.kind == 'VARA':
				h = Helper('VARA', ['--stdin', '48000', '--hf', str(self.hf), '--calls', str(self.calls()), *(['--dump'] if self.vdump else []), *(['--verbose'] if self.vverb else [])], (1500 - self.hf / 2, 1500 + self.hf / 2), self.add, self.died)		# VARA sits at 1500 Hz
				h.seen = self.named
			elif self.kind == 'ARDOP':
				h = Ardop(self.add, self.died, self.aquiet, grid_ll(self.grid))
			elif self.kind == 'PACTOR':
				h = Helper('PACTOR', ['-T', '-o', 'BatchMode=yes', self.PMON, self.PMONCMD], (1300, 1700), self.add, self.died)		# PACTOR-1 tones 1400 / 1600 Hz
				h.drop = ('INFO: could not find a config file',)
			elif self.kind == 'JS8':
				h = Helper('JS8', ['--playback', '--raw', '--format=s16le', '--rate=48000', '--channels=1', f'--device={self.SINK}', '--latency-msec=200'], (500, 2500), self.add, self.died)		# JS8 signals sit in 500-2500 Hz
			else:
				h = Helper('Packet', ['-c', self.dwconf(), '-t', '0'], self.PACKET[self.pk], self.pkadd, self.died)
				h.drop = self.NAGS if self.hide else ()
				h.seen = self.pkseen
				h.gain = 0.5		# Digital out arrives at direwolf's 'audio level' 70-100; it wants about 50 (above that it warns of distortion)
				self.dw = None		# numbering starts again
			if self.sk or self.rec:		# still on: a failed start has already turned it off
				self.dec = h
			return self.newline()
		if self.kind == 'RTTY':
			self.dec = decode.Rtty(self.baud, self.shift, self.rev, center, self.sq['RTTY'])
		elif self.kind == 'PSK31':
			self.dec = decode.Psk(center, self.sq['PSK31'])
		elif self.kind == 'Olivia':
			self.dec = decode.Olivia(self.om, center, self.ocont)
		else:
			self.dec = decode.Cw(center, self.sq['CW'])
		w = self.win.get(self.kind)
		self.dec.tu.look = tuple(w) if w else None
		if self.kind in self.sq:
			quiet(self.sql, self.sql.setValue, round(self.sq[self.kind]))
		self.newline()
		return
	# make

	'''-------------------------------------------------------------------
		stop() keeps the combo as it is (start uses it); off() also sets it to Off, for closing the radio
	-------------------------------------------------------------------'''

	def stop(self, keep=False):
		self.timer.stop()
		if self.ptx:
			self.ptx = None
			self.unlink()
			self.txoff and self.txoff()
		if not keep:
			self.js8quit()
		if self.rec:
			self.rec.finished.disconnect(self.recended)
			self.rec.kill()
			self.rec.waitForFinished(1000)
			self.rec = None
		if isinstance(self.dec, (Helper, Wsjt)):
			self.dec.close()
		if self.sk:
			self.sk.close()
		if self.api:
			self.api.close()
		self.sk, self.dec, self.api, self.js8, self.lines = None, None, None, None, {}
		self.sql.setEnabled(False)
		self.status.setText('')
		self.peaks = []
		self.show_level()
		self.spec.update()
		return
	# stop

	def off(self):		# the audio is gone (radio closed, card lost, program died): a recording ends too
		self.stop()
		quiet(self.pick, self.pick.setCurrentIndex, 0)
		self.rec_end()
		return

	'''-------------------------------------------------------------------
		Record button: the audio poll() reads goes to /tmp/x.wav too; with no decoder the audio is started for it (and stopped after, unless Spectrum wants it)
	-------------------------------------------------------------------'''

	WAV = Path('/tmp/x.wav')
	IQ = Path('/tmp/x.cs16')		# the RSPdx's I/Q as rspdx gets it: 16-bit I, Q pairs (500 kS/s in SSB and AM = 2 MB/s); rspdx stops at 1 GB
	WAVMAX = 600		# s: auto stop (~58 MB)

	def set_rec(self, on, iq=False):
		if not on:
			return self.rec_end()
		if self.wav:		# the other button, mid-recording
			return quiet(self.iqb if iq else self.recb, (self.iqb if iq else self.recb).setChecked, False)
		why = None if self.ready else 'no radio open'
		if not why:
			try:
				self.wav = wave.open(str(self.WAV), 'wb')
				self.wav.setnchannels(1)
				self.wav.setsampwidth(2)
				self.wav.setframerate(48000)		# arecord and the RSPdx's Digital out alike
				self.wavn = 0
			except OSError as e:
				self.wav, why = None, f'{self.WAV}: {e.strerror}'
		if not why and not self.dec:
			self.start('Off')
			if not self.dec:
				self.rec_end()
				why = self.status.text() or 'no audio'
		if why:
			quiet(self.recb, self.recb.setChecked, False)
			quiet(self.iqb, self.iqb.setChecked, False)
			return self.status.setText(f'Record: {why}')
		self.iq = iq and bool(self.oniq)
		if self.iq:
			self.IQ.unlink(missing_ok=True)		# so a file there afterwards is this recording's
			self.oniq(self.IQ)
		(self.recb if self.iq else self.iqb).setEnabled(False)
		log.info(f'recording to {self.WAV}' + (f' and {self.IQ}' if self.iq else ''))
		return
	# set_rec

	def rec_end(self):
		if not self.wav:
			return
		self.wav.close()
		self.wav = None
		for b, t in ((self.recb, 'Record'), (self.iqb, 'Record I/Q')):
			quiet(b, b.setChecked, False)
			b.setText(t)
			b.setEnabled(True)
		iq, self.iq = self.iq, False
		if iq:
			self.oniq(None)
		self.newline()
		self.add(f'recorded {self.wavn / 48000:.1f} s to {self.WAV}\n')
		log.info(f'recorded {self.wavn / 48000:.1f} s to {self.WAV}')
		if self.kind == 'Scope' and self.dec and not self.scope:		# the audio was running only for the recording
			self.stop()
		QTimer.singleShot(300, lambda: self.rec_name(iq))		# not from in here (a radio being closed ends a recording too); rspdx has closed its file by then
		return
	# rec_end

	'''-------------------------------------------------------------------
		a recording ended: asks for a name; /tmp/x.wav (and x.cs16) become /tmp/NAME.wav (.cs16). Cancel or blank: they stay x
	-------------------------------------------------------------------'''

	def rec_name(self, iq):
		files = [f for f in ((self.WAV, self.IQ) if iq else (self.WAV,)) if f.exists()]
		if iq and self.IQ not in files:
			self.add(f"no {self.IQ}: rspdx didn't record (an older rspdx? ./build.sh)\n")
		name, ok = QInputDialog.getText(self.text, 'Recording', f"Name for {' and '.join(f.name for f in files)} in {self.WAV.parent} (blank: leave as x):", text=self.recname() if self.recname else '')
		name = re.sub(r'[/\x00]', '-', name.strip())
		if not ok or not name:
			return
		try:
			for f in files:
				f.replace(f.with_name(name + f.suffix))
			self.add('saved ' + ', '.join(str(f.with_name(name + f.suffix)) for f in files) + '\n')
			log.info(f'recording named {name}')
		except OSError as e:
			self.status.setText(f'Record: {e.filename}: {e.strerror}')
		return
	# rec_name

	recname = None		# shack sets it: a name to offer (frequency and mode)

	def iq_done(self, m):		# rspdx's report on an I/Q recording: what the file holds; why, when rspdx ended it itself
		if m['why']:
			self.add(f"I/Q recording stopped: {m['why']}\n")
			if self.iq:
				self.rec_end()
		if m['n']:
			line = f"I/Q: {m['n'] / m['rate']:.1f} s, {m['rate'] / 1e3:g} kS/s, centre {m['hz'] / 1e6:.6f} MHz, 16-bit I/Q ({m['n'] * 4 / 1e6:.0f} MB)"
			self.add(line + '\n')
			log.info(line)
		return
	# iq_done

	'''-------------------------------------------------------------------
		Spectrum button: on with no decoder starts the strip alone; off stops it (a decoder keeps its strip)
	-------------------------------------------------------------------'''

	def set_scope(self, on):
		self.scope = on
		if on and self.ready and not self.dec:
			self.start('Off')
		elif not on and self.kind == 'Scope' and self.dec and not self.wav:
			self.off()
		self.save()
		return
	# set_scope

	def opened(self, alsa):		# a radio was opened: where its audio comes from (None = RSPdx Digital out); the strip starts if Spectrum is on
		self.alsa, self.ready = alsa, True
		if self.scope:
			self.start('Off')
		return

	def closed(self):		# the radio is being closed
		self.ready = False
		self.off()
		return

	def died(self, why):		# the decoder program stopped by itself
		self.dec = None		# already gone, nothing to close
		self.off()
		self.status.setText(why)
		return

	'''-------------------------------------------------------------------
		every 100 ms: decode whatever audio has arrived, then the status line
	-------------------------------------------------------------------'''

	def poll(self):
		if self.rec:		# arecord's output, whole samples
			b = self.carry + bytes(self.rec.readAllStandardOutput())
			n = len(b) & ~1
			raw, self.carry = b[:n], b[n:]
		else:
			data = []
			while True:
				try:
					data.append(self.sk.recv(4096))
				except BlockingIOError:
					break
			raw = b''.join(data)
		if raw:
			self.heard = time.monotonic()
			if self.wav:
				self.wav.writeframes(raw)
				self.wavn += len(raw) // 2
				(self.iqb if self.iq else self.recb).setText(f"{'Record I/Q' if self.iq else 'Record'} {self.wavn // 48000} s")
				if self.wavn >= self.WAVMAX * 48000:
					QTimer.singleShot(0, self.rec_end)		# not from inside poll: it may stop the audio poll is still using
			x = np.frombuffer(raw, '<i2') / 32768.0
			self.peaks.append((self.heard, float(np.abs(x).max()) if len(x) else 0.0))
			txt, new = self.dec(x)
			if new:
				self.newline()
			if txt:
				self.add(txt)
		if self.api:
			self.js8in()
		tu = self.dec.tu
		if time.monotonic() - self.heard > 1:
			t = f'no audio from {self.alsa}' if self.alsa else 'no audio: is Digital out on?'
		elif self.kind == 'Scope':
			t = 'spectrum only'
		elif self.kind == 'VARA':
			lag = self.dec.lag()
			t = f'{self.hf} Hz   xara-monitor listening' + (f'   its clock {lag:.0f} s behind (lost audio), times corrected' if lag >= 1 else '')
		elif self.kind == 'Packet':
			t = f'{self.pk} baud   direwolf ' + (f'TRANSMIT enabled as {Modem.CALL}, AGW port {self.AGW}' if self.ptx else 'listening')
		elif self.kind in self.WSJT or self.kind == 'ARDOP':
			t = self.dec.info()
		elif self.kind == 'PACTOR':
			t = f'pmon on {self.PMON} listening (signal centered at 1500 Hz)'
		elif self.kind == 'Olivia':
			t = ('locked  ' if tu.fixed else 'auto  ') + self.dec.info() + ('  decoding' if self.dec.op else '  searching')
		elif self.kind == 'JS8':
			ok = self.js8 and time.monotonic() - self.js8[0] < 40		# it pings every 15 s
			t = f'JS8Call {self.js8[1]} connected' if ok else f'waiting for JS8Call: running? Settings > Reporting > UDP Server API on, port {self.JS8PORT}? Input: Monitor of shack-JS8'
		else:
			t = ('locked  ' if tu.fixed else 'auto  ') + (f'{tu.center:.0f} Hz  {tu.snr:.0f} dB' if tu.center else '-- Hz') + ('  open  ' if self.dec.op else '  closed  ') + self.dec.info()
		if self.kind == 'RTTY':
			t = f'{self.bd(self.baud)} Bd  {self.shift} Hz{"  mark low" if self.rev else ""}    ' + t
		self.status.setText(t)
		self.show_level()
		self.spec.update()
		return
	# poll

	'''-------------------------------------------------------------------
		peak audio level over the last second: dBFS, orange from -3 dB, red when it reaches full scale
	-------------------------------------------------------------------'''

	def show_level(self):
		now = time.monotonic()
		self.peaks = [(t, p) for t, p in self.peaks if now - t < 1]
		if not self.peaks:
			self.level.setText('')
			self.level.setStyleSheet('')
			return
		p = max(p for t, p in self.peaks)
		db = 20 * np.log10(max(p, 1e-5))
		self.level.setText(f'{db:4.0f} dBFS')
		self.level.setStyleSheet('color: white; background: red; font-weight: bold' if p >= 0.99 else 'color: black; background: orange' if db > -3 else '')
		return
	# show_level

	def bd(self, b):
		return '45.45' if b == 1000 / 22 else f'{b:g}'

	'''-------------------------------------------------------------------
		text at the end; follows it only if already at the bottom,
		so reading back isn't yanked away
	-------------------------------------------------------------------'''

	def add(self, txt):
		if self.logging and self.dec and self.kind in self.KINDS[1:]:
			self.write_log(txt)
		sb = self.text.verticalScrollBar()
		end = sb.value() >= sb.maximum() - 2
		c = QTextCursor(self.text.document())
		c.movePosition(QTextCursor.MoveOperation.End)
		c.insertText(txt)
		if end:
			sb.setValue(sb.maximum())
		return
	# add

	'''-------------------------------------------------------------------
		Log: decoded text appended to <decoder>.log, a dated header whenever logging starts or the decoder changes (xara's lines carry only the UTC time)
	-------------------------------------------------------------------'''

	def set_log(self, on):
		self.logging, self.logged = on, None
		self.save()
		return

	def write_log(self, txt):
		try:
			with open(HERE / f'{self.kind.lower()}.log', 'a') as f:
				if self.logged != self.kind:
					f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC  {self.kind}{self.vlabel()} ---\n")
					self.logged = self.kind
				f.write(txt)
		except OSError as e:
			self.logging = False
			self.logb.setChecked(False)
			self.status.setText(f'log: {e.strerror}')
		return
	# write_log

	def newline(self):
		if self.text.document().lastBlock().text():
			self.add('\n')
		return

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def set_sq(self, v):
		self.sq[self.kind] = v
		if self.dec:
			self.dec.tu.sq = v
		self.save()
		return
	# set_sq

	'''-------------------------------------------------------------------
		VARA bandwidth changed: a new xara-monitor if VARA is running
	-------------------------------------------------------------------'''

	def vlabel(self):		# the log header's VARA part: bandwidth and xara-monitor's extras
		return f" {self.hf} Hz{' dump' if self.vdump else ''}{' verbose' if self.vverb else ''}" if self.kind == 'VARA' else ''

	def vara_opts(self, dump, verb):		# xara-monitor's --dump / --verbose: a new one now if VARA is running
		self.vdump, self.vverb = dump, verb
		if self.kind == 'VARA' and isinstance(self.dec, Helper):
			self.dec.close()
			self.make()
		self.save()
		return
	# vara_opts

	def vara(self, hf):
		self.hf = hf
		if self.kind == 'VARA' and isinstance(self.dec, Helper):
			self.dec.close()
			self.make()
		self.save()
		return
	# vara

	'''-------------------------------------------------------------------
		packet speed changed: a new direwolf if packet is running
	-------------------------------------------------------------------'''

	def olivia(self, om, cont):		# Olivia mode or Contestia changed: a new decoder if it's running
		self.om, self.ocont = om, cont
		if self.kind == 'Olivia' and self.dec:
			self.make()
		self.save()
		return
	# olivia

	'''-------------------------------------------------------------------
		packet speed changed: a new direwolf if packet is running
	-------------------------------------------------------------------'''

	def packet(self, baud):
		self.pk = baud
		if self.kind == 'Packet' and isinstance(self.dec, Helper):
			self.dec.close()
			self.make()
		self.save()
		return
	# packet

	'''-------------------------------------------------------------------
		packet transmit on (card, PTT port) or off (None): direwolf again with the new config
	-------------------------------------------------------------------'''

	def transmit(self, tx):
		self.unlink()
		self.ptx = tx
		if self.kind == 'Packet' and isinstance(self.dec, Helper):
			self.dec.close()
			self.make()
		if tx and isinstance(self.dec, Helper):
			self.link = Link(self.AGW, Modem.CALL, self.said, self.linked)
			self.conrow.setVisible(True)
		return
	# transmit

	def unlink(self):		# the session goes with direwolf: a connected station times out on its own
		if self.link:
			if self.link.peer:
				self.link.bye()		# DISC, if direwolf gets to send it
				self.add(f"\n*** {'session with' if self.link.up else 'connect to'} {self.link.peer} dropped: transmit off\n")
			self.link.close()
			self.link = None
		self.conrow.setVisible(False)
		self.connected(None)
		return
	# unlink

	'''-------------------------------------------------------------------
		Connect line: Connect / Disconnect, a typed line; direwolf's news and the other station's text into the text box
	-------------------------------------------------------------------'''

	def con_click(self):
		if not self.link:
			return
		if self.link.peer:
			return self.link.bye()
		call = self.callbox.text().strip().upper()
		if not re.fullmatch(r'[A-Z0-9]{3,6}(-\d{1,2})?', call) or not re.search(r'\d', call):
			return self.status.setText(f'not a callsign: {call!r}')
		self.callbox.setText(call)
		if self.link.connect(call):
			self.connected(call, False)
		return
	# con_click

	def type_send(self):
		t = self.line.text()
		if self.link and self.link.up:
			self.link.write(t)
			self.add(t + '\n')		# echoed after the BBS's prompt, like a terminal
			self.line.clear()
		return

	def said(self, text):
		self.add(text)
		return

	def linked(self, text, peer):
		self.add(('' if self.text.document().lastBlock().text() == '' else '\n') + text.rstrip('\n') + '\n')
		self.connected(peer, bool(peer))
		return

	def connected(self, peer, up=False):		# the Connect line's state: nobody, trying (peer, not up), connected
		self.conb.setText('Connect' if not peer else 'Disconnect')
		self.callbox.setEnabled(not peer)
		self.line.setEnabled(up)
		if up:
			self.line.setFocus()
		return
	# connected

	'''-------------------------------------------------------------------
		direwolf's lines: while connected only its 'Stream' lines (link trouble, disconnects) show, unless Monitor is ticked; packet.log gets them all
	-------------------------------------------------------------------'''

	def pkadd(self, txt):
		if self.link and self.link.up and not self.mon and not txt.startswith('Stream '):
			if self.logging:
				self.write_log(txt)
			return
		if self.compact:
			self.dw = self.dw or Direwolf(self.rf)
			txt = self.dw(txt)
			if not txt:
				return
		self.add(txt)
		return
	# pkadd

	'''-------------------------------------------------------------------
		one UI frame to BEACON through direwolf's AGW port ('M': unproto, no path), long enough to set the audio level by the meters
		AGW header: port, 3 x 0, kind, 0, PID, 0, call from (10), call to (10), data length, user (0)
	-------------------------------------------------------------------'''

	def test_packet(self):
		data = (f'{Modem.CALL} test ' * 9 + '\r').encode()		# ~100 bytes: ~3 s at 300 baud, time for the meters to settle
		try:
			with socket.create_connection(('127.0.0.1', self.AGW), 1) as s:
				s.sendall(struct.pack('<B3xBxBx10s10sII', 0, ord('M'), 0xF0, Modem.CALL.encode(), b'BEACON', len(data), 0) + data)
		except OSError as e:
			self.add(f"can't reach direwolf's AGW port {self.AGW}: {e.strerror or e}\n")
		return
	# test_packet

	'''-------------------------------------------------------------------
		direwolf's "Unknown APRS Data Type" and "Use of ID is obsolete" lines on BBS and node IDs: hidden or shown, from now on
	-------------------------------------------------------------------'''

	def nags(self, hide):
		self.hide = hide
		if self.kind == 'Packet' and isinstance(self.dec, Helper):
			self.dec.drop = self.NAGS if hide else ()
		self.save()
		return
	# nags

	def set_compact(self, on):		# right-click > Packet > One line per packet, from now on
		self.compact = on
		self.save()
		return
	# set_compact

	'''-------------------------------------------------------------------
		JS8: the null sink pacat plays into (made once, left in place so JS8Call keeps its input), JS8Call itself if it isn't running,
		and the port JS8Call's API sends to. returns why it can't, or None
	-------------------------------------------------------------------'''

	def js8open(self):
		why = self.sink(self.SINK, 'shack-JS8')
		if why:
			return why
		if subprocess.run(['pgrep', '-x', 'js8call'], capture_output=True).returncode != 0:		# not running: start it, on its own (Off or closing shack leaves it running)
			if not shutil.which('js8call'):
				return "can't find js8call on the PATH"
			ok, pid = QProcess.startDetached('js8call', [])
			log.info(f'started js8call, pid {pid}' if ok else "couldn't start js8call")
			if not ok:
				return "couldn't start js8call (see shack.log)"
			self.js8pid = pid
		self.api = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		try:
			self.api.bind(('127.0.0.1', self.JS8PORT))
		except OSError as e:
			self.api.close()
			self.api = None
			return f'UDP {self.JS8PORT}: {e.strerror}. Another program listening to JS8Call?'
		self.api.setblocking(False)
		return None
	# js8open

	'''-------------------------------------------------------------------
		a null sink (pulse): pacat plays Digital out into it, JS8Call / ardopcf record its monitor. Made once, left in place. returns why not, or None
	-------------------------------------------------------------------'''

	def sink(self, name, desc):
		try:
			if name not in subprocess.run(['pactl', 'list', 'short', 'sinks'], capture_output=True, text=True, timeout=5).stdout:
				subprocess.run(['pactl', 'load-module', 'module-null-sink', f'sink_name={name}', f'sink_properties=device.description={desc}', 'rate=48000', 'channels=1'],
					check=True, capture_output=True, timeout=5)
				log.info(f'made the {name} sink')
		except (OSError, subprocess.SubprocessError) as e:
			log.error(f'{name} sink: {e!r}')
			return f"couldn't make the {desc} sound device (pactl: {e})"
		return None
	# sink

	'''-------------------------------------------------------------------
		close the JS8Call shack started (if it's still that program). SIGTERM: its Settings are saved when you press OK there, not at exit
	-------------------------------------------------------------------'''

	def js8quit(self):
		pid, self.js8pid = self.js8pid, None
		if not pid:
			return
		try:
			if Path(f'/proc/{pid}/comm').read_text().strip() == 'js8call':
				os.kill(pid, signal.SIGTERM)
				log.info(f'closed js8call, pid {pid}')
		except (OSError, ValueError):		# already gone
			pass
		return
	# js8quit

	'''-------------------------------------------------------------------
		JS8Call's API messages: PING every 15 s, CLOSE when it quits, RX.ACTIVITY for each decoded frame (shown)
	-------------------------------------------------------------------'''

	def js8in(self):
		while True:
			try:
				m = json.loads(self.api.recv(65536))
			except BlockingIOError:
				break
			except ValueError:
				continue
			kind, p = m.get('type'), m.get('params', {})
			if kind == 'PING':
				self.js8 = (time.monotonic(), p.get('VERSION', ''))
			elif kind == 'CLOSE':
				self.js8 = None
			elif kind == 'RX.ACTIVITY':
				self.frame(p.get('OFFSET', 0), p.get('SNR', 0), p.get('UTC', 0), m.get('value', '').strip())
		return
	# js8in

	'''-------------------------------------------------------------------
		one JS8 frame: joins the open line at its offset (within 10 Hz, its last frame less than a minute ago) like JS8Call's
		Band Activity, or starts a line 'UTC  SNR  offset  text'. The end-of-message mark closes the line
	-------------------------------------------------------------------'''

	def frame(self, off, snr, utc, text):
		now = time.monotonic()
		self.lines = {o: v for o, v in self.lines.items() if now - v[1] < 60}
		hit = next((o for o in self.lines if abs(o - off) <= 10), None)
		c, head = self.lines.pop(hit)[0::2] if hit is not None else (None, None)		# c: start of the line; text added at the end of the box doesn't move it
		if c and c.block().text().startswith(head):		# still that line (not dropped off the top of the box)
			e = QTextCursor(c)
			e.movePosition(QTextCursor.MoveOperation.EndOfBlock)
			e.insertText(' ' + text)
		else:
			self.newline()
			head = f"{time.strftime('%H:%M:%S', time.gmtime(utc / 1000))}  {snr:+3d} dB  {off:4d} Hz  "
			self.add(head + text)
			c = QTextCursor(self.text.document())
			c.movePosition(QTextCursor.MoveOperation.End)
			c.movePosition(QTextCursor.MoveOperation.StartOfBlock)
		if not text.endswith('\u2662'):		# ♢: the message is complete
			self.lines[off] = (c, now, head)
		return
	# frame

	'''-------------------------------------------------------------------
		direwolf's config, written fresh each start: audio from stdin, nothing out, no network ports or announcing (nothing to clash with)
		transmit: out to the radio's card, our call, PTT through shack's PTT port (Hamlib's NET rigctl, model 2), the AGW port for connects,
		slow-HF link settings: 400 ms of flags for CAT keying, 10 s before a retry, short frames one at a time, v2.0 straight away (no SABME tries)
	-------------------------------------------------------------------'''

	def dwconf(self):
		path = Path(tempfile.gettempdir()) / 'shack-direwolf.conf'
		card, port = self.ptx or ('null', None)
		c = [f'ADEVICE stdin {card}', 'ARATE 48000', 'ACHANNELS 1', 'CHANNEL 0', f'MODEM {self.pk}']
		if port:
			c += [f'MYCALL {Modem.CALL}', f'PTT RIG 2 localhost:{port}', 'TXDELAY 40', 'TXTAIL 10', 'FRACK 10', 'RETRY 10', 'PACLEN 64', 'MAXFRAME 1', 'MAXV22 0', 'FX25TX 16']		# FX25TX 16: frames go out as FX.25 (16 check bytes repair 8 bad bytes a frame); plain AX.25 stations still read them
		c += [f'AGWPORT {self.AGW if port else 0}', 'KISSPORT 0', 'DNSSD 0']
		path.write_text('\n'.join(c) + '\n')
		return str(path)
	# dwconf

	'''-------------------------------------------------------------------
		RTTY settings changed: a new decoder if RTTY is running
	-------------------------------------------------------------------'''

	def rtty(self, baud, shift, rev):
		self.baud, self.shift, self.rev = baud, shift, rev
		if self.dec and self.kind == 'RTTY':
			self.make(self.dec.tu.center if self.dec.tu.fixed else None)
		self.save()
		return
	# rtty

	'''-------------------------------------------------------------------
		right-click on the row, the strip or the text: Auto tune, RTTY, VARA and packet settings, Clear (the text also gets Copy, Select all)
	-------------------------------------------------------------------'''

	def menu(self, w, pos):
		m = self.text.createStandardContextMenu() if w is self.text else QMenu(w)
		if w is self.text:
			call = self.call_at(self.text.cursorForPosition(pos))
			if call:
				a = m.addAction(f'Look up {call} on QRZ')
				a.triggered.connect(lambda: self.qrz(call))
			m.addSeparator()
		d = self.dec
		if d and not isinstance(d, (Helper, Scope)):		# ticked = following the strongest signal; unticking locks where it is
			self.check(m, 'Auto tune', not d.tu.fixed, lambda on: d.tune(None if on else d.tu.center))
		if w is self.spec and d and not isinstance(d, (Helper, Scope, Wsjt)):		# where auto tune looks, from where the strip was clicked
			f = round(min(max(self.spec.hz(pos.x()), decode.F0), decode.F1) / 10) * 10
			lo, hi = d.tu.look or (decode.F0, decode.F1)
			if f > lo:
				a = m.addAction(f'Ignore above {f} Hz')
				a.triggered.connect(lambda: self.set_win((lo, f)))
			if f < hi:
				a = m.addAction(f'Ignore below {f} Hz')
				a.triggered.connect(lambda: self.set_win((f, hi)))
			if d.tu.look:
				a = m.addAction('Search the whole strip')
				a.triggered.connect(lambda: self.set_win(None))
		r = m.addMenu('RTTY')
		if decode:
			for name, b, s, rev in decode.PRESETS:
				self.check(r, f'{name}: {self.bd(b)} Bd, {s} Hz' + (', mark low' if rev else ''), (b, s, rev) == (self.baud, self.shift, self.rev), lambda on, p=(b, s, rev): self.rtty(*p))
			r.addSeparator()
			bm, sm = r.addMenu('Baud'), r.addMenu('Shift')
			for b in decode.BAUDS:
				self.check(bm, self.bd(b), b == self.baud, lambda on, b=b: self.rtty(b, self.shift, self.rev))
			for s in decode.SHIFTS:
				self.check(sm, f'{s} Hz', s == self.shift, lambda on, s=s: self.rtty(self.baud, s, self.rev))
			self.check(r, 'Mark low (reverse)', self.rev, lambda on: self.rtty(self.baud, self.shift, on))
		o = m.addMenu('Olivia')
		if decode:
			for om in decode.OLIVIA:
				t, bw = om.split('/')
				self.check(o, f'{om}: {t} tones, {bw} Hz wide', om == self.om, lambda on, om=om: self.olivia(om, self.ocont))
			o.addSeparator()
			self.check(o, 'Contestia (same family, 6-bit text)', self.ocont, lambda on: self.olivia(self.om, on))
		v = m.addMenu('VARA')
		for hf in (500, 2300, 2750):
			self.check(v, f'{hf} Hz', hf == self.hf, lambda on, hf=hf: self.vara(hf))
		v.addSeparator()
		self.check(v, 'Full payloads (not cut at 100 characters)', self.vdump, lambda on: self.vara_opts(on, self.vverb))
		self.check(v, 'Unconfirmed frames too (calls it couldn\'t confirm)', self.vverb, lambda on: self.vara_opts(self.vdump, on))
		ad = m.addMenu('ARDOP')
		self.check(ad, 'Hide DataACK and IDLE frames (an idle link trades them every few seconds)', self.aquiet, self.ardop_quiet)
		k = m.addMenu('Packet')
		for b, what in ((300, '300 baud (HF)'), (1200, '1200 baud (VHF FM)')):
			self.check(k, what, b == self.pk, lambda on, b=b: self.packet(b))
		k.addSeparator()
		self.check(k, 'Hide complaints about non-APRS packets', self.hide, self.nags)
		self.check(k, 'One line per packet (numbered: from -> to, text)', self.compact, self.set_compact)
		k.addSeparator()
		a = self.check(k, f'Transmit (as {Modem.CALL})', bool(self.ptx), lambda on: self.ontx and self.ontx(on))
		a.setEnabled(self.kind == 'Packet' and isinstance(self.dec, Helper))
		a = k.addAction('Send a test packet')
		a.setEnabled(bool(self.ptx))
		a.triggered.connect(self.test_packet)
		self.check(k, 'Monitor while connected (all frames heard, not just the session)', self.mon, lambda on: setattr(self, 'mon', on))
		a = m.addAction(f'My grid ({self.grid or "none"})…')		# FT4, FT8, WSPR: distance and bearing from here
		a.triggered.connect(self.my_grid)
		a = m.addAction('Clear')
		a.triggered.connect(self.text.clear)
		m.exec(w.mapToGlobal(pos))
		return
	# menu

	def my_grid(self):
		g, ok = QInputDialog.getText(self.text, 'My grid', 'Your Maidenhead grid (EM85 or EM85rp), for distance and bearing on FT4, FT8 and WSPR lines; blank = none:', text=self.grid)
		if not ok:
			return
		g = g.strip()
		if g and not grid_ll(g):
			return self.status.setText(f'{g}: not a grid (two letters A-R, two digits, maybe two letters a-x)')
		self.grid = g[:4].upper() + g[4:].lower()
		if isinstance(self.dec, (Wsjt, Ardop)):
			self.dec.home = grid_ll(self.grid)
		self.save()
		return
	# my_grid

	def ardop_quiet(self, on):		# ARDOP: DataACK and IDLE frames hidden or shown, from now on
		self.aquiet = on
		if isinstance(self.dec, Ardop):
			self.dec.quiet = on
		self.save()
		return

	'''-------------------------------------------------------------------
		the callsign at a text cursor (its word: PJ4/K1ABC -> K1ABC, <K1ABC> -> K1ABC, KG5RPO-7>APRS,WIDE2-1: -> KG5RPO), None if it isn't one; QRZ's page for it
	-------------------------------------------------------------------'''

	def call_at(self, c):
		line, col = c.block().text(), c.positionInBlock()
		w = next((m.group() for m in re.finditer(r'\S+', line) if m.start() <= col <= m.end()), '')
		parts = [re.sub(r'-\d{1,2}$', '', x) for x in re.split(r'[/>,:*<]', w.strip('<>:,;.()[]'))]
		calls = [x for x in parts if self.CALL.fullmatch(x) and not GRID.fullmatch(x) and x != 'RR73']
		return max(calls, key=len) if calls else None
	# call_at

	def qrz(self, call):
		self.lookup.show(call)
		return

	'''-------------------------------------------------------------------
		auto tune's window (Hz, Hz) for this decoder, None = all of it; remembered per decoder
	-------------------------------------------------------------------'''

	def set_win(self, win):
		if win:
			self.win[self.kind] = list(win)
		else:
			self.win.pop(self.kind, None)
		if self.dec and hasattr(self.dec, 'tu'):
			self.dec.tu.look = win
		self.spec.update()
		self.save()
		return
	# set_win

	def check(self, menu, text, on, f):		# a ticked or unticked menu item
		a = menu.addAction(text)
		a.setCheckable(True)
		a.setChecked(on)
		a.triggered.connect(f)
		return a

	'''-------------------------------------------------------------------
		settings shared with decode.py's own UI (decode.json)
	-------------------------------------------------------------------'''

	def load(self):
		if not decode:
			return
		try:
			c = json.load(open(decode.CONF))
			self.kind = c['kind'] if c['kind'] in self.sq else self.kind
			self.baud, self.shift, self.rev = float(c['baud']), int(c['shift']), bool(c['rev'])
			self.sq.update({k: float(v) for k, v in c['sq'].items() if k in self.sq})
			self.hf = int(c.get('hf', self.hf))
			self.vdump, self.vverb = bool(c.get('vara_dump', self.vdump)), bool(c.get('vara_verbose', self.vverb))
			self.pk = int(c.get('pk', self.pk))
			self.hide = bool(c.get('hide', self.hide))
			self.compact = bool(c.get('compact', self.compact))
			self.aquiet = bool(c.get('ardop_quiet', self.aquiet))
			self.scope = bool(c.get('scope', self.scope))
			self.logging = bool(c.get('log', self.logging))
			self.grid = str(c.get('grid', self.grid))
			self.win = {k: [float(a), float(b)] for k, (a, b) in c.get('win', {}).items() if k in self.KINDS}
			self.om = c.get('olivia', self.om) if c.get('olivia') in decode.OLIVIA else self.om
			self.ocont = bool(c.get('contestia', self.ocont))
		except (OSError, ValueError, KeyError, TypeError, AttributeError):
			pass
		return
	# load

	def save(self):
		if not decode:
			return
		try:
			with open(decode.CONF, 'w') as f:
				json.dump({'kind': self.kind if self.kind in self.sq else 'RTTY', 'scope': self.scope, 'baud': self.baud, 'shift': self.shift, 'rev': self.rev, 'sq': self.sq, 'hf': self.hf, 'vara_dump': self.vdump, 'vara_verbose': self.vverb, 'pk': self.pk, 'hide': self.hide, 'compact': self.compact, 'ardop_quiet': self.aquiet, 'log': self.logging, 'grid': self.grid, 'win': self.win, 'olivia': self.om, 'contestia': self.ocont}, f)
		except OSError as e:
			log.error(f'decoder settings: {e}')
		return
	# save

# class Decoders


'''===================================================================================================================================================
	xara, the VARA modem, run as a child on the open radio's USB sound card: Pat and OpenSlot connect to it (KISS 8100, commands 8300)
	tx None: listen only, it plays to ALSA's null device and has no PTT
	tx (port, drive dB): it plays into the radio's card and keys the radio through shack's rigctld port
	everything it prints goes to xara.log next to shack.py (a new file each start) and to add(text); gone(why) if it stops by itself
==================================================================================================================================================='''

PATCONF = Path.home() / '.config/pat/config.json'		# Pat's config: mycall, http_addr, connect_aliases

def pat_conf():		# Pat's config as a dict, {} if it can't be read
	try:
		return json.loads(PATCONF.read_text())
	except (OSError, ValueError):
		return {}

def mbox(box):		# one of Pat's mailbox folders: in, out, sent, archive (~/.local/share/pat/mailbox/<mycall>/<box>); None without a mycall
	call = pat_conf().get('mycall', '')
	return Path.home() / '.local/share/pat/mailbox' / call.upper() / box if call else None

def outbox():		# messages waiting to go
	d = mbox('out')
	return len(list(d.glob('*.b2f'))) if d else 0

def unread():		# inbox messages not opened yet (X-Unread: true, as Pat marks them)
	d = mbox('in')
	return sum(1 for f in d.glob('*.b2f') if b'\nX-Unread: true' in f.read_bytes()[:4000]) if d else 0

'''-------------------------------------------------------------------
	a Pat message file (B2F): header lines, a blank line, then Body: N bytes of text and each File: N name in turn, each ended by CRLF
	-> dict(path, head {name: [values]}, body text, files [(name, bytes)]); None if it can't be read
-------------------------------------------------------------------'''

def b2f(path):
	try:
		data = path.read_bytes().lstrip()
	except OSError:
		return None
	end = data.find(b'\r\n\r\n')
	if end < 0:
		return None
	head = {}
	for line in data[:end].decode('latin-1').split('\r\n'):
		k, _, v = line.partition(':')
		head.setdefault(k.strip().lower(), []).append(v.strip())
	i = end + 4
	n = int((head.get('body') or ['0'])[0] or 0)
	cs = re.search(r'charset="?([\w.-]+)', (head.get('content-type') or [''])[0])
	try:
		body = data[i:i + n].decode(cs[1] if cs else 'latin-1', errors='replace')
	except LookupError:
		body = data[i:i + n].decode('latin-1')
	i += n + 2
	files = []
	for f in head.get('file', []):
		size, _, name = f.partition(' ')
		size = int(size) if size.isdigit() else 0
		files.append((decoded(name), data[i:i + size]))
		i += size + 2
	return dict(path=path, head=head, body=body.replace('\r\n', '\n'), files=files)
# b2f

def decoded(s):		# an RFC 2047 header value (=?utf-8?q?...?=) as plain text
	try:
		return str(email.header.make_header(email.header.decode_header(s)))
	except (ValueError, LookupError):
		return s

def mark_read(path):		# drops Pat's X-Unread line from the header, the rest of the file byte for byte
	data = path.read_bytes()
	end = data.find(b'\r\n\r\n')
	head = data[:end]
	new = re.sub(rb'\r\nX-Unread: true|^X-Unread: true\r\n', b'', head)
	if new != head:
		path.write_bytes(new + data[end:])
	return


'''===================================================================================================================================================

==================================================================================================================================================='''

class Compose(QDialog):		# a Winlink message into Pat's outbox: pat compose with the body on stdin (not a terminal: no prompts); the next connect sends it
	def __init__(self, parent, to='', cc='', subj='', body=''):
		super().__init__(parent, windowTitle='Winlink message')
		self.to = QLineEdit(to, placeholderText='call or email address; several: comma or space between')
		self.cc = QLineEdit(cc, placeholderText='optional')
		self.subj = QLineEdit(subj)
		self.body = QPlainTextEdit(body)
		self.body.setFont(QFont('DejaVu Sans Mono', 11))
		self.body.setMinimumSize(560, 260)
		send = QPushButton('Put in the outbox', default=True)
		send.clicked.connect(self.post)
		cancel = QPushButton('Cancel')
		cancel.clicked.connect(self.reject)
		g = QGridLayout(self)
		for row, (name, w) in enumerate((('To', self.to), ('Cc', self.cc), ('Subject', self.subj))):
			g.addWidget(QLabel(name), row, 0)
			g.addWidget(w, row, 1)
		g.addWidget(self.body, 3, 0, 1, 2)
		h = QHBoxLayout()
		h.addStretch()
		h.addWidget(cancel)
		h.addWidget(send)
		g.addLayout(h, 4, 0, 1, 2)
		(self.body if subj else self.subj if to else self.to).setFocus()
		return
	# __init__

	@staticmethod
	def addrs(s):
		return [a for a in re.split(r'[,;\s]+', s) if a]

	def post(self):
		to = self.addrs(self.to.text())
		if not to:
			return QMessageBox.warning(self, 'Winlink message', 'Who is it to?')
		args = ['compose', '-s', self.subj.text().strip() or '<No subject>', *(x for c in self.addrs(self.cc.text()) for x in ('-c', c)), *to]
		p = QProcess()
		p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
		p.start('pat', args)
		if not p.waitForStarted(5000):
			return QMessageBox.warning(self, 'Winlink message', f"Couldn't run pat: {p.errorString()}")
		p.write(self.body.toPlainText().encode())
		p.closeWriteChannel()
		p.waitForFinished(15000)
		said = bytes(p.readAll()).decode(errors='replace').strip()
		log.info(f'pat compose: {said}')
		if 'Message posted' not in said:
			return QMessageBox.warning(self, 'Winlink message', f'pat compose said:\n\n{said or "nothing"}')
		self.accept()
		return
	# post
# class Compose


'''===================================================================================================================================================

==================================================================================================================================================='''

class Mail(QDialog):		# Pat's mailbox read straight from its files: folders, the message list (unread bold), the message; Compose, Reply, Save attachments, Archive, Delete
	BOXES = {'Inbox': 'in', 'Outbox': 'out', 'Sent': 'sent', 'Archive': 'archive'}

	def __init__(self, shack):
		super().__init__(shack, windowTitle='Winlink mail')
		self.shack, self.msgs, self.cur = shack, [], None
		self.box = QComboBox(sizeAdjustPolicy=QComboBox.SizeAdjustPolicy.AdjustToContents)
		self.box.addItems(list(self.BOXES))
		self.box.activated.connect(lambda i: self.load())
		self.list = QTableWidget(0, 4, selectionBehavior=QTableWidget.SelectionBehavior.SelectRows, selectionMode=QTableWidget.SelectionMode.SingleSelection,
			editTriggers=QTableWidget.EditTrigger.NoEditTriggers)
		self.list.setHorizontalHeaderLabels(['Date (UTC)', 'From', 'Subject', ''])
		self.list.verticalHeader().hide()
		hh = self.list.horizontalHeader()
		hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
		for c in (0, 1, 3):
			hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
		self.list.itemSelectionChanged.connect(self.show_msg)
		self.view = QPlainTextEdit(readOnly=True)
		self.view.setFont(QFont('DejaVu Sans Mono', 11))
		row = QHBoxLayout()
		row.addWidget(self.box)
		self.btn = {}
		for name, f in (('Compose', self.compose), ('Reply', lambda: self.reply(False)), ('Reply all', lambda: self.reply(True)), ('Save attachments', self.save),
				('Archive', self.archive), ('Delete', self.delete)):
			b = self.btn[name] = QPushButton(name)
			b.clicked.connect(f)
			row.addWidget(b)
		row.addStretch()
		v = QVBoxLayout(self)
		v.addLayout(row)
		v.addWidget(self.list, 2)
		v.addWidget(self.view, 3)
		self.resize(820, 640)
		self.load()
		return
	# __init__

	'''-------------------------------------------------------------------
		the folder's messages, newest first; the counts in the folder names
	-------------------------------------------------------------------'''

	def load(self):
		d = mbox(self.which())
		if not d:
			return self.view.setPlainText(f"No mycall in {PATCONF}: can't find Pat's mailbox")
		for i, (name, b) in enumerate(self.BOXES.items()):
			n = len(list(mbox(b).glob('*.b2f')))
			self.box.setItemText(i, f'{name} ({n})' if n else name)
		mid = self.cur and self.cur['path'].name
		self.msgs = sorted(filter(None, (b2f(f) for f in d.glob('*.b2f'))), key=lambda m: (m['head'].get('date') or [''])[0], reverse=True)
		out = self.which() in ('out', 'sent')
		self.list.setHorizontalHeaderItem(1, QTableWidgetItem('To' if out else 'From'))
		quiet(self.list, self.list.clearSelection)		# so selecting the same row again below counts as a change
		quiet(self.list, self.list.setRowCount, len(self.msgs))
		for r, m in enumerate(self.msgs):
			h = m['head']
			who = ', '.join(h.get('to', [])) if out else (h.get('from') or [''])[0]
			bold = 'true' in h.get('x-unread', [])
			for c, x in enumerate(((h.get('date') or [''])[0], who, decoded((h.get('subject') or [''])[0]), f"📎{len(m['files'])}" if m['files'] else '')):
				it = QTableWidgetItem(x)
				if bold:
					f = it.font()
					f.setBold(True)
					it.setFont(f)
				self.list.setItem(r, c, it)
		self.cur = None
		self.view.clear()
		r = next((r for r, m in enumerate(self.msgs) if m['path'].name == mid), None)
		if r is not None:
			self.list.selectRow(r)
		self.buttons()
		return
	# load

	def which(self):		# the folder shown: in, out, sent, archive (the combo's text carries a count)
		return list(self.BOXES.values())[self.box.currentIndex()]

	def buttons(self):
		m = self.cur
		box = self.which()
		self.btn['Reply'].setEnabled(bool(m) and box in ('in', 'archive'))
		self.btn['Reply all'].setEnabled(bool(m) and box in ('in', 'archive'))
		self.btn['Save attachments'].setEnabled(bool(m and m['files']))
		self.btn['Archive'].setEnabled(bool(m) and box != 'archive' and box != 'out')
		self.btn['Delete'].setEnabled(bool(m))
		return

	'''-------------------------------------------------------------------
		a message picked: its header and text below; an unread one is marked read (as Pat does when you open it)
	-------------------------------------------------------------------'''

	def show_msg(self):
		rows = self.list.selectionModel().selectedRows()
		self.cur = self.msgs[rows[0].row()] if rows else None
		self.buttons()
		if not self.cur:
			return self.view.clear()
		m, h = self.cur, self.cur['head']
		lines = [f'From:     {(h.get("from") or [""])[0]}', f'To:       {", ".join(h.get("to", []))}']
		if h.get('cc'):
			lines.append(f'Cc:       {", ".join(h["cc"])}')
		lines += [f'Date:     {(h.get("date") or [""])[0]} UTC', f'Subject:  {decoded((h.get("subject") or [""])[0])}']
		if m['files']:
			lines.append('Attached: ' + ', '.join(f'{n} ({len(d):,} bytes)' for n, d in m['files']))
		self.view.setPlainText('\n'.join(lines) + '\n' + '─' * 60 + '\n' + m['body'])
		if 'true' in h.get('x-unread', []):
			try:
				mark_read(m['path'])
				h.pop('x-unread')
				for c in range(4):
					it = self.list.item(rows[0].row(), c)
					f = it.font()
					f.setBold(False)
					it.setFont(f)
			except OSError as e:
				log.error(f'mark read {m["path"]}: {e}')
		return
	# show_msg

	'''-------------------------------------------------------------------
		Compose / Reply (To = the sender; all: Cc = everyone else but me), Save attachments, Archive, Delete
	-------------------------------------------------------------------'''

	def compose(self):
		self.shack.compose()
		self.load()
		return

	def reply(self, everyone):
		m = self.cur
		h = m['head']
		me = pat_conf().get('mycall', '').upper()
		frm = (h.get('from') or [''])[0]
		cc = [a for a in h.get('to', []) + h.get('cc', []) if a.upper() not in (me, frm.upper())] if everyone else []
		subj = decoded((h.get('subject') or [''])[0])
		subj = subj if subj.lower().startswith('re:') else f'Re: {subj}'
		quoted = '\n'.join('> ' + x for x in m['body'].rstrip().split('\n'))
		self.shack.compose(frm, ', '.join(cc), subj, f'\n\n{frm} wrote on {(h.get("date") or [""])[0]} UTC:\n{quoted}\n')
		self.load()
		return
	# reply

	def save(self):
		d = QFileDialog.getExistingDirectory(self, 'Save the attachments in', str(Path.home() / 'Downloads'))
		if not d:
			return
		for name, data in self.cur['files']:
			(Path(d) / Path(name).name).write_bytes(data)
		self.shack.statusBar().showMessage(f"Saved {len(self.cur['files'])} attachment(s) in {d}", 8000)
		return

	def archive(self):
		dest = mbox('archive')
		dest.mkdir(parents=True, exist_ok=True)
		os.replace(self.cur['path'], dest / self.cur['path'].name)
		self.cur = None
		self.load()
		return

	def delete(self):
		if QMessageBox.question(self, 'Winlink mail', f"Delete '{decoded((self.cur['head'].get('subject') or [''])[0])}'?") != QMessageBox.StandardButton.Yes:
			return
		self.cur['path'].unlink(missing_ok=True)
		self.cur = None
		self.load()
		return
	# delete
# class Mail


'''===================================================================================================================================================

==================================================================================================================================================='''

class Status(QStatusBar):		# the window's bottom line: BUSY at the left corner while the modem runs; messages in a label beside it (QStatusBar's own message would hide BUSY while it shows)
	def __init__(self):
		super().__init__()
		self.busy = QLabel(' BUSY ', toolTip='xara hears a signal on the frequency (the modem): red while it does, grey otherwise')
		self.msg = QLabel()
		self.msg.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)		# a long message doesn't widen the window
		self.addWidget(self.busy)
		self.addWidget(self.msg, 1)
		self.t = QTimer(singleShot=True, timeout=self.msg.clear)
		self.set_busy(False, False)
		return
	# __init__

	def showMessage(self, text, timeout=0):		# same call as QStatusBar's
		self.msg.setText(text)
		self.t.start(timeout) if timeout else self.t.stop()
		return

	def clearMessage(self):
		self.msg.clear()

	def set_busy(self, on, show=None):		# show: True with the modem, False without it
		self.busy.setStyleSheet('color: white; background: red; font-weight: bold' if on else 'color: #b0b0b0')
		if show is not None:
			self.busy.setVisible(show)
		return
# class Status


'''===================================================================================================================================================

==================================================================================================================================================='''

class Modem:
	PROG = ('xara', '~/installed/builds/xara/target/release/xara')		# name on the PATH, where else to look
	CALL = 'N7RTS'
	PORTS = (8100, 8300)
	LEAD = 150		# ms PTT is held before the audio starts: CAT keying + the radio's T/R relay

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def __init__(self, card, hf, add, gone, tx=None, chan=None):		# chan(busy): xara's BUSY ON / BUSY OFF (a signal in the channel), instead of lines in the text
		self.chan = chan
		self.add, self.gone, self.last = add, gone, ''		# last: its last line, the reason when it stops by itself
		self.log = open(HERE / 'xara.log', 'w')
		self.p = QProcess()
		self.p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
		self.p.readyReadStandardOutput.connect(self.out)
		self.p.finished.connect(self.ended)
		self.p.errorOccurred.connect(lambda e: e == QProcess.ProcessError.FailedToStart and self.gone(f"couldn't run xara: {self.p.errorString()}"))
		out = ['--playback', card, '--radio', '--ptt', f'rigctld:127.0.0.1:{tx[0]}', '--drive-db', str(tx[1]), '--ptt-lead-ms', str(self.LEAD)] if tx else ['--playback', 'null']
		self.p.start(self.find(), ['--call', self.CALL, '--audio', 'alsa', '--capture', card, *out, '--hf', str(hf)])
		return
	# __init__

	@classmethod
	def find(cls):		# path of xara, None if it isn't there
		return next((p for p in (shutil.which(cls.PROG[0]), str(Path(cls.PROG[1]).expanduser())) if p and Path(p).is_file()), None)

	@classmethod
	def busy(cls):		# one of xara's ports that something already listens on (an xara started by openslot.sh?), None if both are free
		for port in cls.PORTS:
			with socket.socket() as s:
				s.settimeout(0.2)
				if s.connect_ex(('127.0.0.1', port)) == 0:
					return port
		return None
	# busy

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def out(self):
		while self.p.canReadLine():
			line = bytes(self.p.readLine()).decode(errors='replace').rstrip()
			if line:
				self.last = line
				self.log.write(line + '\n')
				self.log.flush()
				m = self.chan and re.search(r'< BUSY (ON|OFF)$', line)
				if m:
					self.chan(m[1] == 'ON')
				else:
					self.add(line + '\n')
		return
	# out

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def ended(self, code, status):
		self.out()
		self.log.close()
		self.gone(f'xara stopped: {self.last}' if self.last else f'xara stopped (exit {code})')
		return
	# ended

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def close(self):
		self.p.finished.disconnect(self.ended)
		self.p.terminate()
		if not self.p.waitForFinished(2000):
			self.p.kill()
			self.p.waitForFinished(1000)
		self.out()
		self.log.close()
		return
	# close
# class Modem


'''-------------------------------------------------------------------
-------------------------------------------------------------------'''

class Shack(QMainWindow):
	got = pyqtSignal(str, object)		# rig reader thread -> GUI thread, receives signals from civ.Rig and Rspdx to self.on_rig

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def __init__(self, a):
		super().__init__()
		self.setWindowTitle('shack')
		self.a, self.rig, self.srv = a, None, None
		self.bm = Bookmarks(HERE / 'bookmarks.db', Path(a.rspdx).parent / 'settings.db')
		self.got.connect(self.on_rig, Qt.ConnectionType.QueuedConnection)		# always via the event loop, never inside a rig callback

		self.radio = QComboBox()
		self.radio.addItems(['none', 'IC-705', 'RSPdx', 'Xiegu x6200', 'FTdx10', 'TH-D75'])
		self.radio.activated.connect(lambda i: self.pick(self.radio.itemText(i)))
		self.freq = Freq()
		self.freq.changed.connect(lambda v: self.rig and self.rig.set('mhz', v))
		self.mode = QComboBox()
		self.mode.activated.connect(lambda i: self.rig and self.rig.set('mode', self.mode.itemText(i)))
		self.filrow = QWidget(toolTip='The radio\'s three filter presets for this mode, widest to narrowest as the radio ships; the width is what the radio reports for the one in use')
		self.fil = QComboBox()
		self.fil.addItems(['FIL1', 'FIL2', 'FIL3'])
		self.fil.activated.connect(lambda i: self.rig and self.rig.set('fil', i + 1))
		self.filw = QLabel()
		h = QHBoxLayout(self.filrow)
		h.setContentsMargins(0, 0, 0, 0)
		h.addWidget(self.fil)
		h.addWidget(self.filw)
		h.addStretch()
		self.power = QWidget(toolTip='Transmit power, % of the radio\'s maximum. Changing it doesn\'t transmit')		# slider + its % readout
		self.pwr = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=100, singleStep=1, pageStep=10, tracking=False)		# sends on release, not on every pixel of a drag
		self.pwr.valueChanged.connect(lambda v: self.rig and self.rig.set('pwr', self.pwr_raw(v)))
		self.pscale = None		# (W at 0, W at 255) for radios that show watts, None = %
		self.ng = set()		# CI-V controls the open radio refuses (civ RIGS ng): hidden, not polled or sent
		self.pwr.installEventFilter(self)		# the wheel steps it one unit a notch (eventFilter)
		self.pwrnotch = 0		# wheel delta not yet a whole notch (touchpad)
		self.pwrv = QLabel()
		self.pwrv.setFixedWidth(self.pwrv.fontMetrics().horizontalAdvance('10.0 W  '))
		self.pwr.sliderMoved.connect(lambda v: self.pwrv.setText(self.pwr_text(v)))		# readout follows the drag
		self.pwr.valueChanged.connect(lambda v: self.pwrv.setText(self.pwr_text(v)))
		self.agc = QComboBox(toolTip='The radio\'s AGC. RSPdx: SSB only; Off holds the gain where it is (set the level with IF reduction)')
		self.agc.activated.connect(lambda i: self.rig and self.rig.set('agc', self.agcs[self.agc.itemText(i)]))
		self.agcs = {}		# label: 16 12 value, per radio (civ.RIGS)
		self.pa = QComboBox(toolTip='Preamp: IPO = none (strong signals, low bands), AMP1, AMP2 = most gain')
		self.pa.addItems(cat.PREAMPS)
		self.pa.activated.connect(lambda i: self.rig and self.rig.set('pa', i))
		self.ra = QComboBox(toolTip='Attenuator')
		self.ra.addItems(cat.ATTS)
		self.ra.activated.connect(lambda i: self.rig and self.rig.set('ra', i))
		self.roof = QComboBox(toolTip='Roofing filter (R FIL): the first, widest filter. Narrower keeps strong nearby signals out; it should be at least as wide as the mode needs')
		self.roof.addItems(list(cat.ROOFS))
		self.roof.activated.connect(lambda i: self.rig and self.rig.set('roof', self.roof.itemText(i)))
		self.disp = QWidget(toolTip='The radio\'s screen: contrast and brightness, and the brightness of its LEDs, 0-20')		# FTdx10
		self.da = []
		h = QHBoxLayout(self.disp)
		h.setContentsMargins(0, 0, 0, 0)
		for name in ('Contrast', 'Brightness', 'LED'):
			w = QSpinBox(minimum=0, maximum=20, keyboardTracking=False, prefix=name + ' ')
			w.valueChanged.connect(lambda v: self.rig and self.rig.set('da', [x.value() for x in self.da]))
			self.da.append(w)
			h.addWidget(w)
		h.addStretch()
		h = QHBoxLayout(self.power)
		h.setContentsMargins(0, 0, 0, 0)
		h.addWidget(self.pwr)
		h.addWidget(self.pwrv)
		self.vol = QDoubleSpinBox(decimals=2, minimum=0, maximum=1, singleStep=0.01, keyboardTracking=False)		# 0.01: the X6200's own steps (0-100)
		self.vol.valueChanged.connect(lambda v: self.rig and self.rig.set('v', v))
		self.sq = QSpinBox(minimum=0, maximum=5, keyboardTracking=False, toolTip='The radio\'s squelch for this VFO: 0 = always open, 5 = strong signals only')		# TH-D75
		self.sq.valueChanged.connect(lambda v: self.rig and self.rig.set('sq', v))
		self.lamp = QComboBox(toolTip='The radio\'s display backlight')
		self.lamp.addItems(d75.LAMPS)
		self.lamp.activated.connect(lambda i: self.rig and self.rig.set('lamp', i))
		self.scr = QWidget(toolTip='The Xiegu\'s screensaver (OpenX6X00 firmware): on or off, and the minutes without use before it comes. Set over WiFi through the radio\'s web page, not CI-V')		# X6200
		h = QHBoxLayout(self.scr)
		h.setContentsMargins(0, 0, 0, 0)
		self.saver = QPushButton('On', checkable=True)
		self.saver.clicked.connect(lambda on: self.web_set('screen_saver', int(on)))
		self.savermin = QSpinBox(minimum=1, maximum=120, prefix='after ', suffix=' min', keyboardTracking=False)
		self.savermin.valueChanged.connect(lambda v: self.web_set('screen_saver_min', v))
		h.addWidget(self.saver)
		h.addWidget(self.savermin)
		h.addStretch()
		self.web = None		# the open radio's web configurator host (civ RIGS web), None: it has none
		self.rfg = QSpinBox(minimum=0, maximum=100, singleStep=5, suffix=' %', keyboardTracking=False, toolTip='The radio\'s RF gain; 100 % = full gain')
		self.rfg.valueChanged.connect(lambda v: self.rig and self.rig.set('rfg', round(v * 2.55)))
		self.vfo = {}		# A, B: which one the radio listens on (CI-V can't ask the radio: shack selects the one it last used when the radio is opened)
		self.vfomem, self.vfo_on, self.vfo_t = {}, None, 0.0		# FTdx10: each VFO's settings as last seen, the VFO it reports, when that last changed
		self.vforow = QWidget(toolTip='The radio\'s VFO A or B. IC-705: CI-V can\'t read which one the radio is on, so shack puts it on the one it last used when you open it; the Xiegu says which. TH-D75: its band A and band B')
		h = QHBoxLayout(self.vforow)
		h.setContentsMargins(0, 0, 0, 0)
		for k in ('A', 'B'):
			b = self.vfo[k] = QPushButton(f'VFO {k}', checkable=True)
			b.clicked.connect(lambda on, k=k: self.pick_vfo(k))
			h.addWidget(b)
		self.splitb = QPushButton('SPLIT', toolTip='The radio is in split: it transmits on the other VFO\'s frequency. Click to turn split off')		# only there while split is on
		self.splitb.setStyleSheet('color: white; background: red; font-weight: bold')
		self.splitb.clicked.connect(lambda: self.rig and self.rig.set('split', 0))
		self.splitb.hide()
		h.addWidget(self.splitb)
		h.addStretch()
		self.ifr = QSpinBox(minimum=20, maximum=59, suffix=' dB', keyboardTracking=False)		# IF gain reduction
		self.ifr.valueChanged.connect(lambda v: self.rig and self.rig.set('if', v))
		self.lna = QSpinBox(minimum=0, maximum=18, keyboardTracking=False)		# LNA state; top end follows the band
		self.lna.valueChanged.connect(lambda v: self.rig and self.rig.set('lna', v))
		self.ant = QComboBox()
		self.ant.addItems(['A', 'B', 'C'])
		self.ant.activated.connect(lambda i: self.rig and self.rig.set('ant', self.ant.itemText(i)))
		self.ovl = QLabel()		# RF overload: blank until the first one
		self.bw = QComboBox()
		self.bw.addItems(Rspdx.BANDWIDTHS)
		self.bw.activated.connect(lambda i: self.rig and self.rig.set('bw', i))
		self.sw = {}		# rspdx command: toggle button
		switches = QHBoxLayout()
		self.nbdb = QSpinBox(minimum=8, maximum=20, value=12, suffix=' dB', keyboardTracking=False, toolTip='Blanker trigger level; changing it turns the blanker on')
		self.nbdb.valueChanged.connect(lambda v: self.rig and self.rig.set('nbdb', v))
		self.nbr = QLabel(toolTip='Blanker pulses per second')
		self.lvl = {}		# CI-V NB and NR levels, %; usable while their switch is on
		for k, what in (('nb', 'noise blanker'), ('nr', 'noise reduction')):
			w = self.lvl[k] = QSpinBox(minimum=0, maximum=100, singleStep=5, suffix=' %', keyboardTracking=False, toolTip=f'The radio\'s {what} level; turn the {what} on to change it')
			w.valueChanged.connect(lambda v, k=k: self.rig and self.rig.set(k + 'l', round(v * 2.55)))
		self.nbr.setFixedWidth(self.nbr.fontMetrics().horizontalAdvance('9999.9/s'))		# changing text mustn't resize the panel
		for k, name in Rspdx.SWITCHES.items():
			b = self.sw[k] = QPushButton(name, checkable=True)
			b.clicked.connect(lambda on, k=k: self.rig and self.rig.set(k, on))		# clicked, not toggled: the radio's own updates don't echo back
			switches.addWidget(b)
			if k == 'nb':		# its level and rate sit next to it
				switches.addWidget(self.nbdb)
				switches.addWidget(self.nbr)
			if k in self.lvl:		# CI-V: the radio's level
				switches.addWidget(self.lvl[k])
		self.sw['pre'] = QPushButton('On', checkable=True)		# CI-V radios; has its own row (Preamp) above RF gain
		self.sw['att'] = QPushButton('ATT', checkable=True)
		for k in ('pre', 'att'):
			self.sw[k].clicked.connect(lambda on, k=k: self.rig and self.rig.set(k, on))
		switches.insertWidget(0, self.sw['att'])
		self.sw['nr'].setToolTip('RNNoise: removes the noise floor under voice (SSB, AM). Turn off for very weak signals, music and CW; Digital out is unaffected')
		self.save = QPushButton('Save', toolTip='Bookmark this frequency and mode with the radio\'s settings (updates it if it\'s already there)')
		self.save.clicked.connect(self.bm_save)
		switches.addWidget(self.save)
		switches.addStretch()		# buttons keep their size when others are hidden
		self.adc = QProgressBar(minimum=-60, maximum=0, value=-60, format='', toolTip='Broadband level into the RSPdx ADC; red above -6 dBFS: back off the gain')
		self.bms = QTableWidget(0, 4, toolTip='Double-click: go there.  Right-click: rename or delete.  Save adds the current station.  Click a column heading to sort by it (again: the other way).')
		self.bms.setHorizontalHeaderLabels(['Name', 'MHz', 'Mode', 'Hits'])
		self.bms.verticalHeader().hide()
		self.bms.setMinimumWidth(320)		# the button row is wide; don't let it squeeze the table away
		hh = self.bms.horizontalHeader()
		hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)		# drag a column's edge; remembered
		hh.setStretchLastSection(True)		# Hits takes what's left over, so the table fills the tab
		fm = self.bms.fontMetrics()
		widths = self.bm.pref('bm columns') or [fm.horizontalAdvance(x) + 16 for x in ('M' * 4 + 'x' * 32, '144.950000', 'USB-D', 'Hits ▲')]		# Name about 36 characters to start
		for c, wd in enumerate(widths[:3]):
			hh.resizeSection(c, wd)
		self.bmcols = QTimer(singleShot=True, interval=500, timeout=lambda: self.bm.set_pref('bm columns', [hh.sectionSize(c) for c in range(3)]))		# once the dragging stops
		hh.sectionResized.connect(lambda c, old, new: c < 3 and self.bmcols.start())
		self.bmsort = self.bm.pref('sort', [3, True])		# column, down (Z-A / high to low): most picked first to start
		hh.setSortIndicatorShown(True)
		hh.setSectionsClickable(True)
		hh.setSortIndicator(self.bmsort[0], Qt.SortOrder.DescendingOrder if self.bmsort[1] else Qt.SortOrder.AscendingOrder)
		hh.sortIndicatorChanged.connect(self.bm_sort)		# Qt flips the arrow on a click; the rows are sorted here, not by Qt (bmlist[row] must stay the row's bookmark)
		self.bms.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
		self.bms.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
		self.bms.cellDoubleClicked.connect(lambda r, c: self.bm_pick(r))
		self.bms.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
		self.bms.customContextMenuRequested.connect(self.bm_menu)
		self.bmlist = []
		self.tabs = QTabWidget()		# right side: Bookmarks | Gateways | Heard
		self.tabs.setMinimumWidth(320)
		self.gws = QTableWidget(0, 6, toolTip='Winlink gateways (VARA HF) on the band the radio is on, nearest first; grey: not heard from in 6 hours.  Double-click: Pat connects to it through the modem (Modem on Transmit).  Right-click: download a fresh list, stop Pat')
		self.gws.setHorizontalHeaderLabels(['Gateway', 'Dial kHz', 'BW', 'Miles', 'Bearing', 'Heard'])
		self.gws.verticalHeader().hide()
		self.gws.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
		self.gws.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
		self.gws.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
		self.gws.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
		self.gws.cellDoubleClicked.connect(lambda r, c: self.gw_pick(r))
		self.gws.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
		self.gws.customContextMenuRequested.connect(self.gw_menu)
		self.gwlist, self.gwband, self.pat, self.rmsp = [], None, None, None		# rows shown, their band, a running pat connect, a running rms.py
		self.gwwait, self.gwnext = QTimer(interval=500, timeout=self.gw_ready), None		# a connect waiting for the modem: (call, url, give up at)
		self.mailw = None		# the Mail window, once opened
		self.hds = QTableWidget(0, 10, toolTip='Stations the Packet decoder has heard, every frequency.  Double-click a callsign: its QRZ page; the rest of the row: go to where it was heard.  Right-click: look up, forget one, or clear the list.  Click a column heading to sort by it (again: the other way).')
		self.hds.setHorizontalHeaderLabels(['Call', 'MHz', 'Hits', 'Last', 'FEC', 'Svc', 'Miles', 'Clicked', 'Via', 'Text'])
		self.hds.verticalHeader().hide()
		hd = self.hds.horizontalHeader()
		hd.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
		hd.setSectionResizeMode(9, QHeaderView.ResizeMode.Stretch)		# Text takes the spare width
		self.hdsort = self.bm.pref('heard sort', [3, True])		# column, down: the latest heard first to start
		hd.setSortIndicatorShown(True)
		hd.setSectionsClickable(True)
		hd.setSortIndicator(self.hdsort[0], Qt.SortOrder.DescendingOrder if self.hdsort[1] else Qt.SortOrder.AscendingOrder)
		hd.sortIndicatorChanged.connect(self.hd_sort)		# as the bookmarks: the rows are sorted here, not by Qt (hdlist[row] must stay the row's station)
		self.hds.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
		self.hds.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
		self.hds.cellDoubleClicked.connect(self.hd_pick)
		self.hds.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
		self.hds.customContextMenuRequested.connect(self.hd_menu)
		self.hdlist = []		# rows shown
		self.sigs = []		# (monotonic, S-meter dB from S9) for the last SIGWIN seconds: a packet's row gets the highest
		self.hdnew, self.hdat = set(), 0		# rows the latest packet touched (sender, and its digipeater), lit until the next packet; when it came (monotonic)
		self.net, self.asked = QNetworkAccessManager(self), set()		# callook.info (where a US callsign is), the callsigns asked about since shack started
		self.dec = Decoders(lambda: isinstance(self.rig, Rspdx) and self.rig.set('digi', True))		# Digital out: the RSPdx only
		self.dec.onheard = self.heard
		self.dec.rf = lambda: self.sigpeak() if self.rig else None
		self.dec.ontx, self.dec.txoff = self.pk_tx, lambda: self.pk_ui(False)
		self.pkgen = 0		# counts packet Transmit picks: a start still waiting for the radio is dropped when another comes
		self.pcmkey = 'pcm'		# the Audio level box's setting: 'pcm' for xara, 'pkpcm' for packet (direwolf plays much louder)
		self.dec.oniq = lambda f: isinstance(self.rig, Rspdx) and self.rig.send(f'iq {f or 0}')
		self.dec.recname = lambda: f'{self.freq.hz / 1e6:.6f}-{self.mode.currentText()}'
		self.rsp = (self.dec.row, self.dec.spec, self.dec.text, self.dec.scopeb, self.dec.recb, self.dec.iqb, self.ifr, self.lna, self.ant, self.ovl, self.bw, *(self.sw[k] for k in Rspdx.SWITCHES), self.nbdb, self.nbr, self.save, self.adc, self.tabs)		# RSPdx controls
		self.smeter = QProgressBar(minimum=-54, maximum=60, value=-54, format='S0')		# 705: S-units; RSPdx: dBFS (open_rx)
		self.swr = QLabel(toolTip='The radio\'s SWR meter while transmitting; the last reading stays until the next transmission')
		self.tx = QLabel('RX')
		self.tx.setFont(QFont('monospace', 16, QFont.Weight.Bold))
		self.pttb = QPushButton('PTT', checkable=True, toolTip='Transmit: click to key, click again to stop. Drops back to receive by itself after 30 s')
		self.pttb.setFont(QFont('monospace', 14, QFont.Weight.Bold))
		self.pttb.setStyleSheet('QPushButton:checked { background-color: #c00000; }')		# transmitting: red, not the usual green
		self.pttb.clicked.connect(self.key)
		self.ptt_limit = QTimer(singleShot=True, interval=30000, timeout=lambda: self.key(False))		# safety net
		self.pttrow = QWidget()		# PTT button + TX/RX indicator
		h = QHBoxLayout(self.pttrow)
		h.setContentsMargins(0, 0, 0, 0)
		h.addWidget(self.pttb)
		h.addWidget(self.tx)
		self.meters = QLabel(toolTip='The radio\'s own meters while it transmits, whoever keyed it: power, ALC (0 = none, 1 = the top of the ALC zone; for VARA keep it near 0), SWR. Live while it transmits; afterwards the highest of each during that transmission ("peak"), until the next one')
		self.meters.setFont(QFont('monospace', 12, QFont.Weight.Bold))
		self.met, self.peak, self.txon = {}, {}, False		# watts, alc, swr as last read; their highest this transmission; transmitting
		h.addSpacing(16)
		h.addWidget(self.meters)
		h.addStretch()
		self.tune_sk = None		# the connection to xara's command port while Tune is on
		self.tuneb = QPushButton('Tune', checkable=True, toolTip='xara sends a steady tone at its transmit level so you can set Audio level by the ALC and power readings on the PTT line. Click again to stop; stops by itself after 30 s. Not while Pat or OpenSlot is connected to xara')
		self.tuneb.setStyleSheet('QPushButton:checked { background-color: #c00000; }')
		self.tuneb.clicked.connect(self.set_tune)
		self.tune_limit = QTimer(singleShot=True, interval=30000, timeout=lambda: self.set_tune(False))
		self.pcm = QSpinBox(minimum=0, maximum=64, keyboardTracking=False, toolTip='How loud xara\'s audio goes into the radio: the PCM level of the radio\'s USB sound card (ALSA mixer steps). Works while transmitting; remembered per radio')
		self.pcm.valueChanged.connect(self.set_pcm)
		self.txrow = QWidget()		# Tune + audio level: there while the modem can transmit
		h = QHBoxLayout(self.txrow)
		h.setContentsMargins(0, 0, 0, 0)
		h.addWidget(self.tuneb)
		h.addSpacing(16)
		h.addWidget(QLabel('Audio level'))
		h.addWidget(self.pcm)
		h.addStretch()
		self.clients = QLabel()
		self.setStatusBar(Status())
		self.statusBar().addPermanentWidget(self.clients)
		self.mdm = None		# the running Modem
		self.modem = QComboBox(toolTip='xara, the VARA modem, on this radio, for Pat and OpenSlot (ports 8100 / 8300). Listen: it can\'t transmit. Transmit: it plays into the radio and keys it. The decoders stop while it runs: it needs the radio\'s sound card')
		self.modem.addItems(['Off', 'Listen', 'Transmit'])
		self.mgen = 0		# counts Modem picks: a start still waiting for the radio is dropped when another pick comes
		self.modem.activated.connect(lambda i: self.set_modem(self.modem.itemText(i)))
		self.modemst = QLabel()
		self.modemrow = QWidget()		# Modem: Off / Listen + what it's doing; sits left of the Decoder controls (one or the other has the sound card)
		h = QHBoxLayout(self.modemrow)
		h.setContentsMargins(0, 0, 12, 0)
		h.addWidget(QLabel('Modem'))
		h.addWidget(self.modem)
		h.addWidget(self.modemst)
		self.wf = Waterfall(self.wf_click)		# CI-V band scope, shown while wfb is on
		self.scoperow = QWidget()		# Waterfall button, span, ref level
		self.wfb = QPushButton('Waterfall', checkable=True, toolTip='The radio\'s band scope, sent over CI-V')
		self.wfb.clicked.connect(self.set_wf)
		self.span = QComboBox(toolTip='Scope width: this much either side of the dial')
		self.span.addItems([f'± {x / 1000:g} kHz' for x in civ.SPANS])
		self.span.activated.connect(lambda i: self.rig and self.rig.set('span', civ.SPANS[i]))
		self.ref = QDoubleSpinBox(minimum=-20, maximum=20, singleStep=0.5, decimals=1, suffix=' dB', keyboardTracking=False, toolTip='Scope reference level: lower shows weak signals brighter')
		self.ref.valueChanged.connect(lambda v: self.rig and self.rig.set('ref', v))
		self.ft = {}		# FTdx10: its own scope's settings (shown on the radio), SS item: combo
		for k, items, tip in (('mode', cat.SCOPEMODES, 'The radio\'s scope display: 3DSS or waterfall (L, N, S = size); Center, Cursor or Fix (the Fix edges are set on the radio)'),
				('span', cat.SPANS, 'The radio\'s scope width'), ('speed', cat.SPEEDS, 'The radio\'s scope sweep speed')):
			c = self.ft[k] = QComboBox(toolTip=tip)
			c.addItems(items)
			c.activated.connect(lambda i, k=k: self.rig and self.rig.set('ss.' + k, i))
		self.ss = {}		# FTdx10 scope items in the right-click menu: last value the radio reported
		h = QHBoxLayout(self.scoperow)
		h.setContentsMargins(0, 0, 0, 0)
		h.addWidget(self.wfb)
		h.addWidget(self.span)
		h.addWidget(self.ft['mode'])
		h.addWidget(self.ft['span'])
		h.addWidget(QLabel('Ref'))
		h.addWidget(self.ref)
		h.addWidget(self.ft['speed'])
		self.wfp = QPushButton('Pause', checkable=True, toolTip='Freeze the waterfall and the spectrum strip (for a closer look); click again to go on')
		h.addWidget(self.wfp)
		h.addStretch()
		for b in (self.wfp, self.dec.pauseb):
			b.clicked.connect(self.freeze)

		self.labels = {}		# control: its row label
		g = QGridLayout()
		g.setColumnStretch(2, 1)		# empty column 2 takes the spare width: adjusters stay narrow in column 1, meters span 1-2
		for row, (name, w) in enumerate([('Radio', self.radio), ('Frequency', self.freq), ('VFO', self.vforow), ('Mode', self.mode), ('Filter', self.filrow), ('Volume', self.vol), ('Squelch', self.sq), ('Backlight', self.lamp), ('Screensaver', self.scr), ('Preamp', self.sw['pre']), ('RF gain', self.rfg), ('AGC', self.agc), ('Preamp', self.pa), ('ATT', self.ra), ('Roofing', self.roof), ('Display', self.disp),
				('IF reduction', self.ifr), ('LNA reduction', self.lna), ('Antenna', self.ant), ('Bandwidth', self.bw), ('Overload', self.ovl), ('ADC', self.adc), ('Signal', self.smeter), ('SWR', self.swr), ('Power', self.power), ('PTT', self.pttrow), ('TX level', self.txrow)]):
			self.labels[w] = QLabel(name)
			w.installEventFilter(self)		# the label greys out with its control (eventFilter)
			g.addWidget(self.labels[w], row, 0)
			g.addWidget(w, row, 1, 1, 2 if w in (self.freq, self.vforow, self.filrow, self.ovl, self.adc, self.smeter, self.power, self.pttrow, self.txrow) else 1)
		self.show_tx(False)
		row += 1
		g.addLayout(switches, row, 0, 1, 3)
		self.helpw = None
		more = QHBoxLayout()		# second button row
		hb = QPushButton('Help', toolTip='What everything does (F1)')
		hb.clicked.connect(self.help)
		QShortcut(QKeySequence.StandardKey.HelpContents, self, self.help)
		more.addWidget(hb)
		more.addWidget(self.dec.scopeb)
		more.addWidget(self.dec.recb)
		more.addWidget(self.dec.iqb)
		more.addStretch()
		row += 1
		g.addLayout(more, row, 0, 1, 3)
		row += 1
		self.labels[self.scoperow] = QLabel('Scope')		# under the buttons: the waterfall is tall
		for w in (self.scoperow, self.labels[self.scoperow]):		# FTdx10: more scope settings
			w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
			w.customContextMenuRequested.connect(lambda pos, w=w: self.scope_menu(w.mapToGlobal(pos)))
		g.addWidget(self.labels[self.scoperow], row, 0)
		g.addWidget(self.scoperow, row, 1, 1, 2)
		row += 1
		g.addWidget(self.wf, row, 0, 1, 3)
		row += 1
		self.labels[self.dec.row] = QLabel('Decoder')
		h = QHBoxLayout()		# one line: Modem (radios that transmit), then Decoder
		h.addWidget(self.modemrow)
		h.addWidget(self.labels[self.dec.row])
		h.addWidget(self.dec.row, 1)
		g.addLayout(h, row, 0, 1, 3)
		row += 1
		g.addWidget(self.dec.spec, row, 0, 1, 3)
		row += 1
		g.addWidget(self.dec.text, row, 0, 1, 3)
		g.setRowStretch(row, 1)		# spare height goes to the decoded text (or below the rows when it's hidden)
		g.addWidget(self.dec.conrow, row + 1, 0, 1, 3)		# packet's Connect line, under the text while packet can transmit
		box = QWidget()
		box.setLayout(g)
		self.tabs.addTab(self.bms, 'Bookmarks')
		self.tabs.addTab(self.gws, 'Gateways')
		self.tabs.addTab(self.hds, 'Packet')
		self.tabs.currentChanged.connect(self.tab_shown)
		self.split = QSplitter(childrenCollapsible=False)		# drag the divider between the radio and the tabs; where it was left is remembered
		self.split.addWidget(box)
		self.split.addWidget(self.tabs)
		self.split.setStretchFactor(0, 2)		# a wider window goes 2:1 to the decoded lines
		self.split.setStretchFactor(1, 1)
		self.splitsave = QTimer(singleShot=True, interval=500, timeout=lambda: self.tabs.isHidden() or self.bm.set_pref('split', self.split.sizes()))		# once the dragging stops; put back in only()
		self.split.splitterMoved.connect(lambda *_: self.splitsave.start())
		self.setCentralWidget(self.split)

		self.parts = (self.freq, self.vforow, self.mode, self.filrow, self.vol, self.sq, self.lamp, self.scr, self.rfg, self.agc, self.pa, self.ra, self.roof, self.disp, self.smeter, self.swr, self.power, self.pttrow, self.modemrow, self.scoperow, self.wf, self.sw['pre'], self.sw['att'], *self.lvl.values(), *self.rsp)		# everything that comes and goes with the radio
		self.n, self.polled = 0, time.monotonic()		# polled: when poll last ran
		self.timer = QTimer(timeout=self.poll, interval=250)
		self.timer.start()
		self.shut()
		self.winsave = QTimer(singleShot=True, interval=500, timeout=self.save_size)		# once the dragging stops
		was = self.bm.pref('window')
		if was:
			self.resize(*was)

	'''-------------------------------------------------------------------
		pick the radio
	-------------------------------------------------------------------'''

	def pick(self, name):
		self.shut()
		if name in civ.RIGS:
			self.open_civ(name)
		elif name in cat.RIGS:
			self.open_cat(name)
		elif name == 'RSPdx':
			self.open_rx()
		elif name == 'TH-D75':
			self.open_d75(name)
		return
	# pick

	'''-------------------------------------------------------------------
		open a CI-V radio: the IC-705 or the Xiegu x6200 (which speaks the 705's CI-V)
	-------------------------------------------------------------------'''

	def open_civ(self, name):
		dev = civ.find_dev(name)
		r = civ.RIGS[name]
		if not dev:
			return self.oops(f"Can't find the {name}.\n\nIs it plugged in and turned on?")
		try:
			self.rig = civ.Rig(dev, self.a.baud or r['baud'], self.a.addr, self.got.emit)
		except serial.SerialException as e:
			return self.oops(f"Found the {name} but couldn't open it.\n\n{e}\n\nIs another program (civ.py, engine.py) using it?")
		try:
			self.srv = civ.serve_rigctld(self.rig, self.a.ptt) if self.a.ptt else None
		except OSError as e:
			self.statusBar().showMessage(f'PTT port {self.a.ptt}: {e.strerror}', 10000)
		self.rig.att_on = r['att']
		self.rig.modes = r['modes']
		self.rig.po = r.get('po')
		self.rig.m26 = r.get('m26', False)
		self.rig.v07 = r.get('v07', False)
		self.rig.sm = r.get('sm')
		self.has_swr = r['swr']
		self.agcs = r['agc']
		quiet(self.agc, self.agc.clear)
		quiet(self.agc, self.agc.addItems, list(self.agcs))
		self.ng = set(r.get('ng', ()))
		self.pscale = r['watts']
		lo, hi = (0, 100) if not self.pscale else (self.pscale[0] * 10, self.pscale[1] * 10)		# % or tenths of a watt
		quiet(self.pwr, self.pwr.setRange, lo, hi)
		self.pwr.setSingleStep(1)		# wheel and arrow keys: 1 % or 0.1 W
		self.rig.send(0x27, 0x11, 0x00)		# no scope data until the waterfall step
		self.scope_for(False)
		self.rig.read_fast()
		if not self.rig.v07:
			self.rig.set('vfo', self.bm.pref(f'vfo {name}', 'A'))		# the 705 can't tell which VFO it's on: put it on the one shack last left it on, so the right button lights
		self.smeter.setRange(-54, 60)
		self.show_radio(name, r['modes'], r['top'], (self.freq, self.vforow, self.mode, self.filrow, self.vol, self.rfg, *([self.agc] if 'agc' not in self.ng else []), self.sw['pre'], self.sw['att'], self.sw['nb'], self.sw['nr'], *(w for k, w in self.lvl.items() if k + 'l' not in self.ng), self.smeter, *([self.swr] if r['swr'] else []), self.power, self.pttrow, self.modemrow, *([self.scoperow] if r['scope'] else []), *([self.scr] if r.get('web') else []), self.save, self.tabs,
			self.dec.row, self.dec.spec, self.dec.text, self.dec.scopeb, self.dec.recb))		# nb, nr: the radio's own, on/off
		self.card = card_of(dev) or r['audio']		# the card in this radio; by name if sysfs doesn't say
		self.dec.opened(self.card)		# decoders read the radio's USB sound card
		self.levels()
		self.tx_band(0)		# no PTT until the radio says where it is
		self.show_bms()
		self.on_rig('ptt_clients', 0)
		self.web = r.get('web')
		if self.web:
			self.web_get()
		return
	# open_civ

	'''-------------------------------------------------------------------
		the Xiegu's screensaver over WiFi: OpenX6X00's web configurator, GET / POST /api/display (JSON, no login), the same calls as its Display page
		greyed until the radio answers; read when the radio is opened (a change on the radio itself shows the next time)
	-------------------------------------------------------------------'''

	def web_get(self):
		self.scr.setEnabled(False)
		req = QNetworkRequest(QUrl(f'http://{self.web}/api/display'))
		req.setTransferTimeout(5000)
		r = self.net.get(req)
		r.finished.connect(lambda: self.web_got(r))
		return

	def web_got(self, r):
		try:
			d = json.loads(bytes(r.readAll()))
			quiet(self.saver, self.saver.setChecked, bool(d['screen_saver']))
			quiet(self.savermin, self.savermin.setValue, d['screen_saver_min'])
			self.scr.setEnabled(bool(self.web))		# closed meanwhile: stays grey
			self.savermin.setEnabled(self.saver.isChecked())
		except (ValueError, KeyError, TypeError):
			self.statusBar().showMessage(f"Screensaver: no answer from {self.web} ({r.errorString()}). Is the radio on WiFi?", 10000)
		r.deleteLater()
		return

	def web_set(self, name, val):
		self.savermin.setEnabled(self.saver.isChecked())
		req = QNetworkRequest(QUrl(f'http://{self.web}/api/display'))
		req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, 'application/json')
		req.setTransferTimeout(5000)
		r = self.net.post(req, json.dumps({'name': name, 'val': val}).encode())
		r.finished.connect(lambda: self.web_set_done(r))
		return

	def web_set_done(self, r):
		if r.error() != QNetworkReply.NetworkError.NoError:
			self.statusBar().showMessage(f"Screensaver: the radio didn't take it ({bytes(r.readAll()).decode(errors='replace') or r.errorString()})", 10000)
			if self.web:
				self.web_get()		# back to what the radio has
		r.deleteLater()
		return
	# web_set_done

	'''-------------------------------------------------------------------
		open a Yaesu CAT radio: the FTdx10 (no filter presets, SWR or scope over CAT yet)
	-------------------------------------------------------------------'''

	def open_cat(self, name):
		dev = cat.find_dev(name)
		r = cat.RIGS[name]
		if not dev:
			return self.oops(f"Can't find the {name}.\n\nIs it plugged in and turned on?")
		try:
			self.rig = cat.Rig(dev, self.a.baud or r['baud'], self.got.emit)
		except serial.SerialException as e:
			return self.oops(f"Found the {name} but couldn't open it.\n\n{e}\n\nIs another program (flrig, rigctld, WSJT-X) using it?")
		try:
			self.srv = civ.serve_rigctld(self.rig, self.a.ptt) if self.a.ptt else None
		except OSError as e:
			self.statusBar().showMessage(f'PTT port {self.a.ptt}: {e.strerror}', 10000)
		self.has_swr = False
		self.agcs = r['agc']
		quiet(self.agc, self.agc.clear)
		quiet(self.agc, self.agc.addItems, list(self.agcs))
		self.pscale = r['watts']
		quiet(self.pwr, self.pwr.setRange, self.pscale[0] * 10, self.pscale[1] * 10)
		self.pwr.setSingleStep(10)		# the FTdx10 sets whole watts: 0.1 W steps were put back by its report
		self.scope_for(True)
		self.rig.poll(0)
		self.smeter.setRange(-54, 60)
		self.show_radio(name, r['modes'], r['top'], (self.freq, self.vforow, self.mode, self.vol, self.rfg, self.agc, self.pa, self.ra, self.roof, self.disp, self.sw['nb'], self.sw['nr'], *self.lvl.values(), self.smeter, self.power, self.pttrow, self.modemrow, self.scoperow, self.save, self.tabs,
			self.dec.row, self.dec.spec, self.dec.text, self.dec.scopeb, self.dec.recb))
		self.card = card_of(dev) or r['audio']		# the card in this radio; by name if sysfs doesn't say
		self.dec.opened(self.card)
		self.levels()
		self.tx_band(0)
		self.show_bms()
		self.on_rig('ptt_clients', 0)
		return
	# open_cat

	'''-------------------------------------------------------------------
		open the Kenwood TH-D75: dial, mode, bands A/B, volume, squelch, backlight and its signal bars (no sound card, PTT or decoders yet)
	-------------------------------------------------------------------'''

	def open_d75(self, name):
		dev = d75.find_dev()
		if not dev:
			return self.oops(f"Can't find the {name}.\n\nIs it plugged in and turned on?")
		try:
			self.rig = d75.Rig(dev, self.got.emit)
		except serial.SerialException as e:
			return self.oops(f"Found the {name} but couldn't open it.\n\n{e}\n\nIs another program using it?")
		self.smeter.setRange(0, 5)
		self.show_radio(name, self.rig.modes, d75.TOP, (self.freq, self.vforow, self.mode, self.vol, self.sq, self.lamp, self.smeter, self.save, self.tabs))
		self.show_bms()
		return
	# open_d75

	'''-------------------------------------------------------------------
		the Scope line: the 705's waterfall controls, or the FTdx10's settings for its own scope
	-------------------------------------------------------------------'''

	def scope_for(self, ft):
		for w in (self.wfb, self.span, self.wfp):
			w.setVisible(not ft)
		for w in self.ft.values():
			w.setVisible(ft)
		quiet(self.ref, self.ref.setRange, *((-30, 30) if ft else (-20, 20)))
		self.ref.setToolTip('The radio\'s scope level: lower shows weak signals brighter' if ft else 'Scope reference level: lower shows weak signals brighter')
		self.ss = {}
		return
	# scope_for

	'''-------------------------------------------------------------------
		right-click the Scope line (FTdx10): peak, color, RBW, marker, hold
	-------------------------------------------------------------------'''

	def scope_menu(self, at):
		if not isinstance(self.rig, cat.Rig):
			return
		menu = QMenu(self)
		acts = {}
		for k, name, items in (('peak', 'Peak', cat.PEAKS), ('color', 'Color', cat.COLORS), ('rbw', 'RBW', cat.RBWS)):
			sub = menu.addMenu(name)
			for i, t in enumerate(items):
				a = sub.addAction(t)
				a.setCheckable(True)
				a.setChecked(self.ss.get(k) == i)
				acts[a] = (k, i)
		for k, name in (('marker', 'Marker'), ('hold', 'Hold')):
			a = menu.addAction(name)
			a.setCheckable(True)
			a.setChecked(bool(self.ss.get(k)))
			acts[a] = (k, int(not self.ss.get(k)))
		act = menu.exec(at)
		if act in acts:
			k, v = acts[act]
			self.rig.set('ss.' + k, v)
		return
	# scope_menu

	'''-------------------------------------------------------------------
		start the rspdx program, which runs the RSPdx through the sdrplay api
	-------------------------------------------------------------------'''

	def open_rx(self):
		if not Path(self.a.rspdx).is_file():
			return self.oops(f"Can't find rspdx at {self.a.rspdx}.\n\nIs it built? (./build.sh)")
		self.rig = Rspdx(self.a.rspdx, self.got.emit, self.bm.last('RSPdx'))
		self.smeter.setRange(-100, -40)		# the TUI's signal scale, dBFS in the passband
		self.agcs = Rspdx.AGCS
		quiet(self.agc, self.agc.clear)
		quiet(self.agc, self.agc.addItems, list(self.agcs))
		quiet(self.agc, self.agc.setCurrentText, 'Mid')		# rspdx's own start; its first state line says what it really is
		self.show_radio('RSPdx', Rspdx.MODES, 2000, (self.freq, self.mode, self.vol, self.agc, self.smeter, *self.rsp))		# no PTT on a receiver
		self.dec.opened(None)		# decoders read Digital out
		self.show_bms()
		return
	# open_rx

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def show_radio(self, name, modes, top, live):
		self.radio.setCurrentText(name)
		self.mode.clear()
		self.mode.addItems(modes)
		self.freq.set_top(top)
		for w in live:
			w.setEnabled(True)		# a mode may grey some out again (bandwidth outside SSB...)
		self.only(live)

	'''-------------------------------------------------------------------
		show just the controls this radio has, with their row labels
	-------------------------------------------------------------------'''

	def only(self, live):
		hidden = self.tabs.isHidden()
		for w in self.parts:
			w.setVisible(w in live)
			if w in self.labels:
				self.labels[w].setVisible(w in live)
		if hidden and not self.tabs.isHidden():		# the tabs come back (no radio = none): the divider where it was last left, 2:1 the first time
			self.split.setSizes(self.bm.pref('split', [2000, 1000]))
		return
	# only

	'''-------------------------------------------------------------------
		close the radio, unkeyed, and hide its controls
	-------------------------------------------------------------------'''

	def shut(self):
		self.web = None
		self.ng = set()
		self.ptt_limit.stop()
		self.modem_off()
		self.dec.closed()
		self.vfomem, self.vfo_on = {}, None
		if self.rig:
			if isinstance(self.rig, Rspdx) and self.rig.ok:		# start here next time
				self.bm.set_last('RSPdx', round(self.rig.state['mhz'] * 1e6), self.rig.state['mode'].upper(), self.rig.settings())
			if isinstance(self.rig, (civ.Rig, cat.Rig)) and (self.rig.tx or self.pttb.isChecked()):		# the button may be ahead of the radio's report
				self.rig.ptt(False)
			if isinstance(self.rig, civ.Rig) and self.wfb.isChecked():
				self.rig.send(0x27, 0x11, 0x00)		# scope data off
			self.rig.close()
			self.rig = None
		if self.srv:
			self.srv.stop()		# also hangs up on connected clients (xara), so none keeps talking to a closed radio
			self.srv = None
		self.radio.setCurrentText('none')
		self.clients.setText('')
		self.splitb.hide()
		self.met, self.peak, self.txon = {}, {}, False
		self.show_meters()
		self.on_rig('ovl', (0, 0))
		self.bms.setRowCount(0)
		self.adc.setValue(-60)
		self.smeter.setValue(self.smeter.minimum())
		self.smeter.setFormat('')
		self.nbr.setText('')
		self.pwrv.setText('')
		self.swr.setText('')
		self.swr.setStyleSheet('')
		self.filw.setText('')
		quiet(self.fil, self.fil.setCurrentIndex, -1)
		for b in (*self.sw.values(), *self.vfo.values(), self.wfb):
			b.setChecked(False)
		self.freeze(False)
		self.wf.clear()
		self.on_rig('ptt', 0)
		self.only(())
		return
	# shut

	'''-------------------------------------------------------------------
		Modem: Listen starts xara on the radio's sound card (the decoders give it up), the radio on VFO A, split off, USB-D; Off stops it and the decoders can run again
	-------------------------------------------------------------------'''

	def set_modem(self, kind):
		self.modem_off()
		if kind == 'Off' or not isinstance(self.rig, (civ.Rig, cat.Rig)):
			return
		v = (civ.RIGS if isinstance(self.rig, civ.Rig) else cat.RIGS)[self.radio.currentText()].get('vara')		# this radio's transmit levels
		why = None
		if not Modem.find():
			why = f"can't find xara (on the PATH or in {Path(Modem.PROG[1]).parent})"
		elif Modem.busy():
			why = f'port {Modem.busy()} is in use: another xara running (./os)? Stop it first'
		elif kind == 'Transmit' and not v:
			why = f'no VARA transmit levels for the {self.radio.currentText()} yet: Listen only'
		elif kind == 'Transmit' and not self.srv:
			why = 'the PTT port is off (--ptt 0): xara would have no way to key the radio'
		if why:
			return self.modemst.setText(why)
		self.dec.closed()		# arecord lets go of the card
		for w in (self.dec.row, self.dec.scopeb, self.dec.recb):
			w.setEnabled(False)
		moved = self.rig.vfo != 'A'
		if moved:
			self.rig.set('vfo', 'A')
		if self.rig.split:
			self.rig.set('split', 0)
		if moved or self.rig.mode != 'USB-D':		# after a VFO change its mode isn't known yet
			self.rig.set('mode', 'USB-D')
		if kind == 'Transmit' and v['pwr'] is not None:
			self.rig.set('pwr', v['pwr'])
		quiet(self.modem, self.modem.setCurrentText, kind)		# modem_off put it on Off
		self.modemst.setText('setting the radio...')
		gen = self.mgen
		QTimer.singleShot(800, lambda: gen == self.mgen and self.modem_go(kind, v))		# once the radio has reported back
		return
	# set_modem

	'''-------------------------------------------------------------------
		the radio has had time to answer: start xara. Transmit only if the radio really is on VFO A, split off, USB-D, and the card's level could be set
	-------------------------------------------------------------------'''

	def modem_go(self, kind, v):
		if not self.rig:
			return
		tx, why = None, None
		if kind == 'Transmit':
			if self.rig.split:
				why = 'the radio is still in SPLIT (it would transmit on VFO B): not started'
			elif self.rig.mode != 'USB-D' or self.rig.vfo != 'A':
				why = f"the radio didn't go to VFO A, USB-D (it says VFO {self.rig.vfo}, {self.rig.mode}): not started"
			else:
				self.drive = v['drive']
				why = self.pcm_set('pcm', v['pcm'])
			tx = (self.srv.server_address[1], v['drive'])
		if why:
			self.modem_off()
			return self.modemst.setText(why)
		what = f"TRANSMIT, drive {v['drive']} dB, PCM {self.pcm.value()}" if tx else 'listen only'
		self.dec.add(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC  xara, {what}, {self.dec.hf} Hz, {self.card} ---\n")
		self.mdm = Modem(self.card, self.dec.hf, self.dec.add, self.modem_gone, tx, self.set_busy)
		self.set_busy(False, True)
		self.show_tx(bool(tx))
		self.modemst.setText(f"xara {'TRANSMIT enabled' if tx else 'listening'}, {self.card}   (xara.log)")
		self.modemst.setStyleSheet('color: #c00000; font-weight: bold' if tx else '')
		return
	# modem_go

	'''-------------------------------------------------------------------
		the TX level line (Tune, Audio level) and its label come and go with a modem that can transmit
	-------------------------------------------------------------------'''

	def show_tx(self, on):
		for w in (self.txrow, self.labels[self.txrow]):
			w.setVisible(on)
		return

	def show_meters(self):		# live while transmitting, the last transmission's peaks after
		m = self.met if self.txon or not self.peak else self.peak
		self.meters.setText('   '.join(t for t in ('' if self.txon or not self.peak else 'peak', f"{m['watts']:5.1f} W" if 'watts' in m else '', f"ALC {m['alc']:.2f}" if 'alc' in m else '', f"SWR {m['swr']:.1f}" if 'swr' in m else '') if t))
		return

	def meter(self, k, v):		# a TX meter reading: live, and the transmission's peak
		self.met[k] = v
		self.peak[k] = max(self.peak.get(k, v), v)
		self.show_meters()
		log.debug(f'meter {k} {v:.2f}' + ('' if self.txon else ' (after unkey)'))
		return

	def amixer(self, *a):		# on the open radio's card: (why it failed or None, its output)
		try:
			r = subprocess.run(['amixer', '-c', self.card.removeprefix('plughw:').removeprefix('CARD='), *a], capture_output=True, text=True, timeout=5)
		except (OSError, subprocess.TimeoutExpired) as e:
			return f"couldn't run amixer: {e}", ''
		return (f"amixer {' '.join(a)} on {self.card}: {r.stderr.strip() or r.returncode}" if r.returncode else None), r.stdout
	# amixer

	def set_pcm(self, n):
		why = self.amixer('sset', 'PCM', str(n))[0]
		if why:
			return self.statusBar().showMessage(why, 5000)
		self.bm.set_pref(f'{self.pcmkey} {self.radio.currentText()}', n)
		return
	# set_pcm

	'''-------------------------------------------------------------------
		the card's PCM level for xara (key 'pcm') or packet ('pkpcm'), as last left in the Audio level box, else n. return why it failed, or None
	-------------------------------------------------------------------'''

	def pcm_set(self, key, n):
		self.pcmkey = key
		pcm = self.bm.pref(f'{key} {self.radio.currentText()}', n)
		lim = re.search(r'Limits:.*?(\d+) - (\d+)', self.amixer('sget', 'PCM')[1])		# the card's scale: FTdx10 0-64, IC-705 0-128
		quiet(self.pcm, self.pcm.setRange, *(map(int, lim.groups()) if lim else (0, 255)))
		quiet(self.pcm, self.pcm.setValue, pcm)
		return self.amixer('sset', 'PCM', str(pcm))[0]		# the radios' cards share one saved mixer state (both are named CODEC): set every time
	# pcm_set

	'''-------------------------------------------------------------------
		Packet > Transmit: the radio to the data version of its sideband (LSB-D / USB-D, where the rear USB audio is what it sends), split off,
		then pk_go starts direwolf with transmit. Off: direwolf receive only again
	-------------------------------------------------------------------'''

	PKPCM = 15		# packet's first Audio level where vara= has no pkpcm: this many steps below xara's (direwolf plays near full scale, xara at -15 dB)

	def pk_tx(self, on):
		self.pkgen += 1
		if not on:
			self.pk_ui(False)
			self.dec.transmit(None)
			return
		name = self.radio.currentText()
		v = isinstance(self.rig, (civ.Rig, cat.Rig)) and (civ.RIGS if isinstance(self.rig, civ.Rig) else cat.RIGS)[name].get('vara')
		mode = self.rig and self.rig.mode or ''
		want = mode if mode.endswith('-D') else mode + '-D'
		why = None
		if not isinstance(self.rig, (civ.Rig, cat.Rig)):
			why = 'packet transmit needs a transceiver (IC-705, Xiegu, FTdx10)'
		elif not self.srv:
			why = 'the PTT port is off (--ptt 0): direwolf would have no way to key the radio'
		elif not v:
			why = f'no transmit levels for the {name} yet (vara= in civ.py / cat.py)'
		elif self.mdm:
			why = 'the Modem has the sound card: set it to Off first'
		elif want not in [self.mode.itemText(i) for i in range(self.mode.count())]:
			why = f"packet transmit needs LSB or USB (it goes to LSB-D / USB-D); the radio is in {mode or 'an unknown mode'}"
		else:
			with socket.socket() as sk:
				sk.settimeout(0.2)
				if sk.connect_ex(('127.0.0.1', self.dec.AGW)) == 0:
					why = f'port {self.dec.AGW} is in use: another direwolf running? Stop it first'
		if why:
			return self.statusBar().showMessage(why, 10000)
		if self.rig.split:
			self.rig.set('split', 0)
		if mode != want:
			self.rig.set('mode', want)
		self.statusBar().showMessage('packet transmit: setting the radio...', 2000)
		gen = self.pkgen
		QTimer.singleShot(800, lambda: gen == self.pkgen and self.pk_go(want, v))		# once the radio has reported back
		return
	# pk_tx

	def pk_go(self, want, v):
		if not self.rig or self.dec.kind != 'Packet' or not isinstance(self.dec.dec, Helper):
			return
		why = None
		if self.rig.split:
			why = 'the radio is still in SPLIT (it would transmit on the other VFO): packet transmit not started'
		elif self.rig.mode != want:
			why = f"the radio didn't go to {want} (it says {self.rig.mode}): packet transmit not started"
		else:
			why = self.pcm_set('pkpcm', v.get('pkpcm', max(v['pcm'] - self.PKPCM, 0)))
		if why:
			return self.statusBar().showMessage(why, 10000)
		self.dec.transmit((self.card, self.srv.server_address[1]))
		self.dec.add(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC  direwolf TRANSMIT as {Modem.CALL}, {want}, PCM {self.pcm.value()}, {self.card} ---\n")
		self.pk_ui(True)
		return
	# pk_go

	def pk_ui(self, on):		# the TX level line without Tune (that's xara's) while packet can transmit
		self.show_tx(on)
		self.tuneb.setVisible(not on)
		return

	'''-------------------------------------------------------------------
		Tune: TUNE <drive>dB / TUNE OFF on xara's command port (8300). xara takes one client there, so shack is connected only while tuning
	-------------------------------------------------------------------'''

	def set_tune(self, on):
		self.tune_limit.stop()
		if on and self.mdm and not self.tune_sk:
			try:
				self.tune_sk = socket.create_connection(('127.0.0.1', Modem.PORTS[1]), 0.5)
				self.tune_sk.sendall(f'TUNE {self.drive}dB\r'.encode())
				self.tune_limit.start()
			except OSError as e:
				self.tune_sk = None
				self.statusBar().showMessage(f"Can't tune: xara's command port: {e.strerror or e} (Pat or OpenSlot connected?)", 8000)
		elif not on and self.tune_sk:
			sk, self.tune_sk = self.tune_sk, None
			try:
				sk.sendall(b'TUNE OFF\r')
			except OSError:
				pass
			QTimer.singleShot(300, sk.close)		# after xara has read it
		self.tuneb.setChecked(bool(self.tune_sk))
		return
	# set_tune

	def set_busy(self, on, show=None):
		self.statusBar().set_busy(on, show)
		return

	'''-------------------------------------------------------------------
		Gateways tab: rms.db (made by rms.py) -> the VARA HF gateways on the radio's band, heard-lately ones first, then by miles
	-------------------------------------------------------------------'''

	def band_of(self, mhz):		# (lo, hi) kHz of the ham band around mhz, None outside
		return next(((lo, hi) for lo, hi in civ.BANDS if lo <= mhz * 1000 <= hi), None)

	def show_gws(self):
		self.gwband = self.band_of(self.freq.hz / 1e6) if self.rig else None
		self.gwlist = []
		db = HERE / 'rms.db'
		if self.gwband and db.is_file():
			try:
				with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as c:
					self.gwlist = c.execute("select call, dial_khz, bw, miles, bearing, hours_since, connect from channels where kind = 'VARA HF' and connect != '' and khz between ? and ? "
						'order by coalesce(hours_since, 99) > 6, miles limit 60', self.gwband).fetchall()
			except sqlite3.Error as e:
				self.statusBar().showMessage(f'rms.db: {e}', 8000)
		elif not db.is_file():
			self.statusBar().showMessage('No gateway list yet: right-click the Gateways tab > Download a fresh list', 8000)
		self.gws.setRowCount(len(self.gwlist))
		names = {u: n for n, u in pat_conf().get('connect_aliases', {}).items()}		# url: alias
		for r, (call, khz, bw, miles, brg, since, url) in enumerate(self.gwlist):
			old = since is None or since > 6
			if url in names:
				call = '★ ' + call
			for c, x in enumerate((call, f'{khz:.1f}', bw, f'{miles:.0f}', f'{brg:.0f}°', '?' if since is None else f'{since} h')):
				it = QTableWidgetItem(x)
				if c:
					it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
				if old:
					it.setForeground(QColor('#b0b0b0'))
				if url in names:
					it.setToolTip(f'Pat alias: {names[url]}')
				self.gws.setItem(r, c, it)
		return
	# show_gws

	'''-------------------------------------------------------------------
		double-click or right-click > Connect: pat connect <the gateway's URL>. Pat moves the dial (through the rigctld port) and puts it back; what it prints goes to the text box.
		Modem not on Transmit: shack sets it and connects once xara's ports answer (a VFO change can reload the list, so the gateway is kept, not its row)
	-------------------------------------------------------------------'''

	def gw_pick(self, r):
		call, url = self.gwlist[r][0], self.gwlist[r][6]
		why = None
		if self.pat:
			why = 'Pat is already connecting: right-click > Stop Pat to give up on that one'
		elif not shutil.which('pat'):
			why = "Can't find pat on the PATH"
		elif not isinstance(self.rig, (civ.Rig, cat.Rig)):
			why = 'Pat needs a transceiver open here'
		if why:
			return self.statusBar().showMessage(why, 8000)
		self.gwwait.stop()
		if self.mdm and self.txrow.isVisible():
			return self.gw_connect(call, url)
		self.set_modem('Transmit')
		if self.modem.currentText() != 'Transmit':		# refused at once: modemst says why
			return self.statusBar().showMessage(f'No connect: {self.modemst.text()}', 8000)
		self.gwnext = (call, url, time.monotonic() + 15)
		self.gwwait.start()
		self.statusBar().showMessage(f'Starting the modem to call {call}…', 5000)
		return
	# gw_pick

	def gw_ready(self):		# every 0.5 s while the modem starts for a connect
		call, url, until = self.gwnext
		if self.modem.currentText() != 'Transmit':		# modem_go refused (split, mode, VFO) or xara stopped
			self.gwwait.stop()
			return self.statusBar().showMessage(f'No connect to {call}: {self.modemst.text()}', 8000)
		if self.mdm and self.txrow.isVisible() and Modem.busy():		# xara listens on its ports
			self.gwwait.stop()
			return self.gw_connect(call, url)
		if time.monotonic() > until:
			self.gwwait.stop()
			self.statusBar().showMessage(f'No connect to {call}: xara never answered on its ports', 8000)
		return
	# gw_ready

	def gw_connect(self, call, url):
		self.set_tune(False)		# Pat needs xara's command port
		self.dec.add(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC  pat connect {url} ---\n")
		self.pat = QProcess(self)		# Qt owns it: pat_done lets go of it inside its own finished signal
		self.pat.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
		self.pat.readyReadStandardOutput.connect(self.pat_out)
		self.pat.finished.connect(self.pat_done)
		self.pat.start('pat', ['connect', url])
		self.tuneb.setEnabled(False)
		self.statusBar().showMessage(f'Pat: connecting to {call}', 5000)
		return
	# gw_connect

	def pat_out(self):
		if self.pat:
			self.dec.add(bytes(self.pat.readAllStandardOutput()).decode(errors='replace'))
		return

	def pat_done(self, code, status):
		self.pat_out()
		self.pat.deleteLater()
		self.pat = None
		self.tuneb.setEnabled(True)
		self.dec.add(f"--- pat finished{'' if code == 0 else f' (exit {code})'} ---\n")
		self.modem_off()		# done with the air: transmit off, the decoders get the card back (the next Connect turns it on again)
		n = unread()
		self.statusBar().showMessage(f"Mail: {n} unread, {outbox()} still in the outbox" + ('   (right-click Gateways > Mail)' if n else ''), 15000)
		if self.mailw and self.mailw.isVisible():
			self.mailw.load()
		return
	# pat_done


	def pat_stop(self):		# give up on a connect: Pat hangs up and puts the dial back
		if self.pat:
			p = self.pat
			p.terminate()
			if not p.waitForFinished(3000):
				p.kill()
				p.waitForFinished(1000)
		return
	# pat_stop

	def gw_menu(self, pos):
		menu = QMenu(self)
		r = self.gws.rowAt(pos.y())
		g = self.gwlist[r] if 0 <= r < len(self.gwlist) else None
		aliases = pat_conf().get('connect_aliases', {})
		if g:
			conn = menu.addAction(f'Connect to {g[0]}')
			conn.setEnabled(not self.pat)
			named = [n for n, u in aliases.items() if u == g[6]]
			ali = menu.addAction(f'Remove Pat alias {named[0]}' if named else f'Add {g[0]} as a Pat alias')
			menu.addSeparator()
		n, u = outbox(), unread()
		mail = menu.addAction('Mail…' + (f'   ({u} unread)' if u else ''))
		comp = menu.addAction('Compose a message…' + (f'   ({n} in the outbox)' if n else ''))
		stop = menu.addAction('Stop Pat')
		stop.setEnabled(bool(self.pat))
		fresh = menu.addAction('Download a fresh list' if not self.rmsp else 'Downloading…')
		fresh.setEnabled(not self.rmsp)
		act = menu.exec(self.gws.viewport().mapToGlobal(pos))
		if not act:
			return
		if g and act == conn:
			self.gw_pick(r)
		elif g and act == ali:
			self.gw_alias(g, named[0] if named else None)
		elif act == comp:
			self.compose()
		elif act == stop:
			self.pat_stop()
		elif act == mail:
			self.mail()
		elif act == fresh:		# rms.py: pat rmslist -d, then rms.db again
			self.rmsp = QProcess()
			self.rmsp.setWorkingDirectory(str(HERE))
			self.rmsp.finished.connect(self.rms_done)
			self.rmsp.start(sys.executable, [str(HERE / 'rms.py')])
			self.statusBar().showMessage('Downloading the gateway list…')
		return
	# gw_menu

	'''-------------------------------------------------------------------
		a gateway as one of Pat's connect_aliases (named call-dial kHz: ko4hjo-7100.5), or that alias removed
	-------------------------------------------------------------------'''

	def gw_alias(self, g, old):
		c = pat_conf()
		if not c:
			return self.statusBar().showMessage(f"Can't read {PATCONF}", 8000)
		al = c.setdefault('connect_aliases', {})
		name = old or f'{g[0].lower()}-{g[1]:.1f}'
		if old:
			del al[old]
		else:
			al[name] = g[6]
		tmp = PATCONF.with_suffix('.shack')
		try:
			tmp.write_text(json.dumps(c, indent=2) + '\n')
			os.replace(tmp, PATCONF)
		except OSError as e:
			return self.statusBar().showMessage(f"Can't write {PATCONF}: {e.strerror}", 8000)
		log.info(f"pat alias {'removed' if old else 'added'}: {name} {g[6]}")
		self.show_gws()
		self.statusBar().showMessage(f"Pat alias {name} {'removed' if old else 'added: pat connect ' + name}", 8000)
		return
	# gw_alias

	'''-------------------------------------------------------------------
		right-click > Compose: the message goes to Pat's outbox; the next connect sends it. To is remembered (not a reply's)
	-------------------------------------------------------------------'''

	def mail(self):		# right-click > Mail: one window, raised if it's already open
		if not self.mailw:
			self.mailw = Mail(self)
		self.mailw.load()
		self.mailw.show()
		self.mailw.raise_()
		return

	def compose(self, to=None, cc='', subj='', body=''):		# to None: a new message, To as last used
		if not shutil.which('pat'):
			return self.statusBar().showMessage("Can't find pat on the PATH", 8000)
		d = Compose(self, self.bm.pref('pat to', '') if to is None else to, cc, subj, body)
		if d.exec():
			if to is None:
				self.bm.set_pref('pat to', d.to.text().strip())
			self.statusBar().showMessage(f'In the outbox ({outbox()} waiting): connect to a gateway to send', 8000)
		return
	# compose

	def rms_done(self, code, status):
		err = bytes(self.rmsp.readAllStandardError()).decode(errors='replace').strip().splitlines()
		self.rmsp = None
		self.statusBar().showMessage('Gateway list updated' if code == 0 else f"rms.py failed: {err[-1] if err else code}", 8000)
		self.show_gws()
		return
	# rms_done

	def modem_gone(self, why):		# xara stopped by itself
		self.mdm = None
		self.modem_off()
		self.modemst.setText(why)
		return

	def modem_off(self):
		self.mgen += 1
		self.gwwait.stop()
		self.modemst.setStyleSheet('')
		self.set_tune(False)
		self.show_tx(False)
		self.set_busy(False, False)
		self.pat_stop()
		if self.mdm:
			self.mdm.close()
			self.mdm = None
		quiet(self.modem, self.modem.setCurrentIndex, 0)
		self.modemst.setText('')
		if not self.dec.row.isEnabled():		# the decoders get the card back
			for w in (self.dec.row, self.dec.scopeb, self.dec.recb):
				w.setEnabled(True)
			if isinstance(self.rig, (civ.Rig, cat.Rig)):
				self.dec.opened(self.card)
		return
	# modem_off

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def help(self):		# non-modal: the radio stays usable while it's open
		if not self.helpw:
			self.helpw = QDialog(self, windowTitle='shack help')
			t = QTextBrowser(openExternalLinks=True)
			t.setHtml(HELP)
			b = QPushButton('Close')
			b.clicked.connect(self.helpw.close)
			v = QVBoxLayout(self.helpw)
			v.addWidget(t)
			v.addWidget(b, alignment=Qt.AlignmentFlag.AlignRight)
			self.helpw.resize(640, 640)
		self.helpw.show()
		self.helpw.raise_()
		self.helpw.activateWindow()
		return
	# help

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def oops(self, msg):
		self.shut()
		QMessageBox.warning(self, 'shack', msg)

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def poll(self):
		if not isinstance(self.rig, (civ.Rig, cat.Rig, d75.Rig)):		# rspdx pushes its state; the others need asking
			return
		now = time.monotonic()
		late, self.polled = now - self.polled, now		# the timer fires 4x a second: a long gap means shack itself was stopped (a breakpoint), not the radio
		if late > 2:
			self.rig.heard = now
			return
		if now - self.rig.heard > 3:		# polled 4x a second, so 3 s of silence = gone
			return self.oops('The radio stopped answering.\n\nUnplugged or turned off?' + (' Or its TNC is in KISS mode.' if isinstance(self.rig, d75.Rig) else ''))
		if isinstance(self.rig, d75.Rig):
			if time.monotonic() - self.rig.last_set > 0.5:
				self.rig.poll(self.n)
				self.n += 1
			return
		if isinstance(self.rig, cat.Rig):
			if time.monotonic() - self.rig.last_set > 0.5:
				self.rig.poll(self.n)
				self.n += 1
			if self.vfo_on and time.monotonic() - self.vfo_t > 2:		# the controls have caught up with this VFO: remember them
				self.vfomem[self.vfo_on] = self.vfo_settings()
			return
		if time.monotonic() - self.rig.last_set > 0.5:		# stay out of the way of user commands
			self.rig.send(0x15, 0x02)
			if self.has_swr:
				self.rig.send(0x15, 0x12)
			if self.rig.tx:		# power and ALC meters
				if self.rig.po:
					self.rig.send(0x15, 0x11)
				self.rig.send(0x15, 0x13)
			self.rig.send(0x1C, 0x00)		# TX/RX, so a short transmission (a tuner cycle) shows
			if self.n % 4 == 0:
				self.rig.read_fast()
				for k in ('vol', 'rfg', 'nb', 'nr', 'nbl', 'nrl', 'pwr', 'pre', 'agc'):		# so the radio's own knob and buttons show up here too
					if k not in self.ng:
						self.rig.send(*civ.CTL[k])
				self.rig.send(0x11)		# attenuator
			self.n += 1

	'''-------------------------------------------------------------------
		VFO A/B buttons. FTdx10: a CAT switch doesn't load the VFO's own settings as the radio's A/B key does,
		so shack keeps each VFO's settings and puts them back (known after the first visit to that VFO)
	-------------------------------------------------------------------'''

	VFOKEEP = ('pa', 'ra', 'roof', 'agc', 'nb', 'nr', 'nbl', 'nrl')

	def pick_vfo(self, k):
		if not self.rig:
			return
		ft = isinstance(self.rig, cat.Rig)
		if ft and self.vfo_on and k != self.vfo_on and time.monotonic() - self.vfo_t > 2:
			self.vfomem[self.vfo_on] = self.vfo_settings()
		self.rig.set('vfo', k)
		if ft and k != self.vfo_on and k in self.vfomem:
			self.apply('', self.vfomem[k])
		self.vfo_t = time.monotonic()
		return
	# pick_vfo

	def vfo_settings(self):
		return {k: v for k, v in self.settings().items() if k in self.VFOKEEP}

	'''-------------------------------------------------------------------
		PTT button: key or unkey; keying starts the 30 s limit
	-------------------------------------------------------------------'''

	def key(self, on):
		if not isinstance(self.rig, (civ.Rig, cat.Rig)):
			return
		if self.rig.ptt(on) is False:		# refused: outside the ham bands
			self.pttb.setChecked(False)
			return self.statusBar().showMessage('No transmitting outside the ham bands', 5000)
		self.rig.last_set = time.monotonic()
		if on:
			self.ptt_limit.start()
		else:
			self.ptt_limit.stop()
			self.pttb.setChecked(False)
		return
	# key

	'''-------------------------------------------------------------------
		PTT and power only inside the ham bands; tuning out while keyed unkeys
	-------------------------------------------------------------------'''

	def tx_band(self, mhz):
		ok = civ.ham(mhz)
		self.pttrow.setEnabled(ok)		# the whole line, its label too
		self.power.setEnabled(ok)
		self.pttb.setToolTip('Transmit: click to key, click again to stop. Drops back to receive by itself after 30 s' if ok else 'Outside the ham bands: no transmitting')
		if not ok and (self.pttb.isChecked() or self.rig.tx):
			self.key(False)
		return
	# tx_band

	'''-------------------------------------------------------------------
		waterfall click: the dial there; Shift: the dial 1500 Hz below it in USB (above in LSB), so the signal sits mid-passband
	-------------------------------------------------------------------'''

	AUDIO = 1500		# Hz: where VARA and ARDOP sit, middle of the decoders' range

	def wf_click(self, hz, center):
		if not self.rig:
			return
		m = self.mode.currentText()
		if center and m.startswith('USB'):
			hz -= self.AUDIO
		elif center and m.startswith('LSB'):
			hz += self.AUDIO
		self.rig.set('mhz', hz / 1e6)
		return
	# wf_click

	'''-------------------------------------------------------------------
		either Pause button: waterfall and spectrum strip freeze together, both buttons show it
	-------------------------------------------------------------------'''

	def freeze(self, on):
		for b in (self.wfp, self.dec.pauseb):
			b.setChecked(on)
		self.wf.pause(on)
		self.dec.spec.pause(on)
		return
	# freeze

	'''-------------------------------------------------------------------
		Waterfall button: the radio's scope on, centered on the dial, its data over CI-V; off stops the data
	-------------------------------------------------------------------'''

	def set_wf(self, on):
		if not isinstance(self.rig, civ.Rig):
			return
		if on:
			self.rig.send(0x27, 0x10, 0x01)		# scope on
			self.rig.send(0x27, 0x14, 0x00, 0x00)		# center mode: the span picks the width
			self.rig.send(0x27, 0x11, 0x01)		# its data out on CI-V
			self.rig.send(0x27, 0x19, 0x00)		# ref level
		else:
			self.rig.send(0x27, 0x11, 0x00)
			self.wf.clear()
			self.freeze(False)
		self.wf.setVisible(on)
		return
	# set_wf

	'''-------------------------------------------------------------------
		an NB or NR level is usable only while its switch is on (and usable)
	-------------------------------------------------------------------'''

	def levels(self):
		for k, w in self.lvl.items():
			w.setEnabled(self.sw[k].isEnabled() and self.sw[k].isChecked())
		return
	# levels

	'''-------------------------------------------------------------------
		power slider value (% or tenths of a watt) <-> CI-V level 0-255 and its readout
	-------------------------------------------------------------------'''

	def pwr_raw(self, v):
		if not self.pscale:
			return round(v * 2.55)
		lo, hi = self.pscale
		return max(0, min(255, round((v / 10 - lo) / (hi - lo) * 255)))

	def pwr_text(self, v):
		return f'{v / 10:.1f} W' if self.pscale else f'{v} %'

	'''-------------------------------------------------------------------
		a row label follows its control's enabled state
	-------------------------------------------------------------------'''

	def eventFilter(self, w, e):
		if w is self.pwr and e.type() == QEvent.Type.Wheel:		# Qt steps 3 at a time; one notch = one step (1 %, 0.1 W; 1 W on the FTdx10)
			self.pwrnotch += e.angleDelta().y()
			n, self.pwrnotch = int(self.pwrnotch / 120), self.pwrnotch % 120 if self.pwrnotch >= 0 else -(-self.pwrnotch % 120)
			if n:
				self.pwr.setValue(self.pwr.value() + n * self.pwr.singleStep())
			return True
		if e.type() == QEvent.Type.EnabledChange and w in self.labels:
			self.labels[w].setEnabled(w.isEnabled())
		return super().eventFilter(w, e)

	'''-------------------------------------------------------------------
		slot that receives signals from civ.Rig and Rspdx
	-------------------------------------------------------------------'''

	def on_rig(self, k, v):
		if v is None and k != 'pwr':		# a level the radio sent as junk
			return
		if k == 'mhz':		# Freq keeps what's being typed, and ignores reports for a second after a step
			self.freq.set_mhz(v)
			if self.wf.dial != round(v * 1e6):
				self.wf.dial = round(v * 1e6)
				self.mark()
			if isinstance(self.rig, Rspdx):
				quiet(self.lna, self.lna.setMaximum, lna_max(v))		# may clamp the value; that's rspdx's call, not a command
			else:
				self.tx_band(v)
			if self.tabs.currentWidget() is self.gws and self.band_of(v) != self.gwband:
				self.show_gws()
		elif k == 'mode' and self.mode.findText(v) >= 0:
			self.mode.setCurrentText(v)
			if v.removesuffix('-D') in civ.NOWIDTH:		# FM: the radio doesn't report a width
				self.filw.setText('')
			self.sw['nr'].setEnabled(v != 'WBFM')		# speech NR chops up broadcast music; rspdx skips it there too
			if isinstance(self.rig, Rspdx):		# as in the TUI: bandwidth presets are SSB only, the blanker isn't in WBFM
				self.bw.setEnabled(v in ('LSB', 'USB'))
				self.sw['nb'].setEnabled(v != 'WBFM')
				self.nbdb.setEnabled(v != 'WBFM')
			self.levels()
		elif k == 'bw':
			self.bw.setCurrentIndex(v)
		elif k == 'fil' and 1 <= v <= 3:		# CI-V filter preset, from the 04 reply
			quiet(self.fil, self.fil.setCurrentIndex, v - 1)
		elif k == 'width':		# CI-V passband of the filter in use, Hz; 0 = the radio sent something unreadable
			self.filw.setText('?' if not v else f'{v} Hz' if v < 1000 else f'{v / 1000:g} kHz')
		elif k in self.sw:
			self.sw[k].setChecked(bool(v))
			self.levels()
			if k == 'nb' and v and isinstance(self.rig, Rspdx):		# rspdx's nb carries the level when on, 0 when off
				quiet(self.nbdb, self.nbdb.setValue, v)
		elif k == 'v':
			quiet(self.vol, self.vol.setValue, v)
		elif k == 'sq':		# TH-D75 squelch 0-5
			quiet(self.sq, self.sq.setValue, v)
		elif k == 'lamp' and 0 <= v < self.lamp.count():
			quiet(self.lamp, self.lamp.setCurrentIndex, v)
		elif k == 'vol':		# CI-V AF level 0-255
			quiet(self.vol, self.vol.setValue, v / 255)
		elif k in ('nbl', 'nrl'):		# CI-V NB / NR level 0-255
			quiet(self.lvl[k[:2]], self.lvl[k[:2]].setValue, round(v / 2.55))
		elif k == 'rfg':		# CI-V RF gain 0-255
			quiet(self.rfg, self.rfg.setValue, round(v / 2.55))
		elif k == 'vfo':		# IC-705: what civ last set (it can't be asked); Xiegu, FTdx10: what the radio reports
			for x, b in self.vfo.items():
				b.setChecked(x == v)
			if isinstance(self.rig, civ.Rig) and v in ('A', 'B'):		# the VFO shack puts it on next time it's opened
				self.bm.set_pref(f'vfo {self.radio.currentText()}', v)
			if v != self.vfo_on:		# the controls still show the other VFO for a moment
				self.vfo_on, self.vfo_t = v, time.monotonic()
		elif k == 'pwr' and not self.pwr.isSliderDown():		# CI-V RF power 0-255, None when the radio sends junk
			if v is None:
				return self.pwrv.setText('?')
			quiet(self.pwr, self.pwr.setValue, round(v / 2.55) if not self.pscale else round((self.pscale[0] + v / 255 * (self.pscale[1] - self.pscale[0])) * 10))
			self.pwrv.setText(self.pwr_text(self.pwr.value()))
		elif k in ('pa', 'ra'):		# FTdx10: index into cat.PREAMPS / ATTS
			w = self.pa if k == 'pa' else self.ra
			if 0 <= v < w.count():
				quiet(w, w.setCurrentIndex, v)
		elif k == 'roof':
			quiet(self.roof, self.roof.setCurrentText, v)
		elif k == 'da':		# FTdx10 screen and LEDs
			for w, x in zip(self.da, v):
				quiet(w, w.setValue, x)
		elif k == 'pmax':		# FTdx10: its MAX POWER menu caps what PC can set
			quiet(self.pwr, self.pwr.setMaximum, v * 10)
			self.pwrv.setText(self.pwr_text(self.pwr.value()))
			self.power.setToolTip(f"Transmit power. The radio's MAX POWER menu (TX GENERAL) allows {v} W here. Changing it doesn't transmit")
		elif k == 'agc':
			name = next((n for n, x in self.agcs.items() if x == v), None)
			if name:
				quiet(self.agc, self.agc.setCurrentText, name)
		elif k in ('if', 'lna'):
			w = self.ifr if k == 'if' else self.lna
			quiet(w, w.setValue, v)
		elif k == 'ant':
			self.ant.setCurrentText(v.upper())
		elif k == 'ovl':		# (now, count since rspdx started); red while overloaded
			now, n = v
			self.ovl.setText(f"{'OVERLOAD' if now else 'clear'} ({n})" if now or n else '')
			self.ovl.setStyleSheet('color: white; background: red; font-weight: bold' if now else '')
		elif k == 'smeter':
			now = time.monotonic()
			self.sigs = [x for x in self.sigs if now - x[0] < self.SIGWIN] + [(now, v)]
			self.smeter.setValue(max(-54, min(60, v)))
			self.smeter.setFormat(sunits(v))
		elif k == 'bars':		# TH-D75: its 0-5 meter
			self.smeter.setValue(v)
			self.smeter.setFormat(f'{v} of 5')
		elif k == 'iq':
			self.dec.iq_done(v)
		elif k == 'meter':		# RSPdx: ADC dBFS, signal dBFS, blanker pulses/s
			adc, sig, nbr = v
			self.adc.setValue(round(max(-60, min(0, adc))))
			self.adc.setFormat(f'{adc:.1f} dBFS')
			self.adc.setStyleSheet('QProgressBar::chunk { background: red }' if adc > -6 else '')
			self.smeter.setValue(round(max(-100, min(-40, sig))))
			self.smeter.setFormat(f'{sig:.1f} dBFS')
			self.nbr.setText(f'{nbr:.1f}/s' if self.sw['nb'].isChecked() else '')
		elif k == 'swr':
			r, raw = v
			if raw or (self.rig and self.rig.tx):		# receiving reads 0: keep the last transmission's value
				self.swr.setText(f'{r:.1f}' + (' +' if raw >= 255 else ''))
				self.swr.setToolTip(f'SWR meter {raw}/255')
				self.swr.setStyleSheet('color: white; background: red' if r > 3 else 'color: black; background: orange' if r > 2 else '')
				self.meter('swr', r)
		elif k == 'ptt':
			if bool(v) != self.txon:		# keyed: new peaks; unkeyed: show them
				log.info(f'radio says {"TX " + str(v) if v else "RX"}' + ('' if v else f'; peaks {self.peak}'))
				self.txon = bool(v)
				if v:
					self.met, self.peak = {}, {}
				self.show_meters()
			self.tx.setText('TX' if v else 'RX')
			self.tx.setStyleSheet('color: white; background: red' if v else '')
			self.pttb.setChecked(bool(v))		# also when xara or the radio's tuner keys it
		elif k == 'split':
			self.splitb.setVisible(bool(v))
		elif k in ('watts', 'alc'):
			self.meter(k, v)
		elif k == 'ptt_clients':
			self.clients.setText(f'PTT port {self.srv.server_address[1]}: {v} connected' if self.srv else 'PTT port off')
		elif k == 'gone' and self.rig:
			self.oops(v)
		elif k == 'error':
			self.statusBar().showMessage(v, 5000)
		elif k == 'scope' and self.wfb.isChecked():
			self.wf.add(v)
		elif k == 'span' and v in civ.SPANS and self.span.currentIndex() != civ.SPANS.index(v):
			quiet(self.span, self.span.setCurrentIndex, civ.SPANS.index(v))
		elif k == 'ref' and isinstance(self.rig, (civ.Rig, cat.Rig)):
			quiet(self.ref, self.ref.setValue, v)
		elif k.startswith('ss.'):		# FTdx10 scope settings
			self.ss[k[3:]] = v
			c = self.ft.get(k[3:])
			if c and 0 <= v < c.count():
				quiet(c, c.setCurrentIndex, v)
		return
	# on_rig

	'''-------------------------------------------------------------------
		right-click on a bookmark: rename or delete
	-------------------------------------------------------------------'''

	def bm_menu(self, pos):
		r = self.bms.rowAt(pos.y())
		if r < 0 or not self.rig:
			return
		b = self.bmlist[r]
		menu = QMenu(self)
		ren = menu.addAction('Rename…')
		dele = menu.addAction('Delete')
		act = menu.exec(self.bms.viewport().mapToGlobal(pos))
		if act == ren:
			name, ok = QInputDialog.getText(self, 'Rename bookmark', 'Name:', text=b['name'])
			if ok:
				self.bm.rename(b, name.strip())
		elif act == dele:
			if QMessageBox.question(self, 'Delete bookmark', f"Delete {b['name']}  {b['hz'] / 1e6:.6f} {b['mode']}?") == QMessageBox.StandardButton.Yes:
				self.bm.delete(b)
		self.show_bms()
		return
	# bm_menu

	'''-------------------------------------------------------------------
		the bookmark table; ones the open radio can't tune (mode or range) are greyed and won't pick
	-------------------------------------------------------------------'''

	def show_bms(self):
		self.bmlist = self.bm.list(*self.bmsort)
		self.bms.setRowCount(len(self.bmlist))
		for r, b in enumerate(self.bmlist):
			for c, t in enumerate((b['name'], f"{b['hz'] / 1e6:.6f}", b['mode'], str(b['hits']))):
				it = QTableWidgetItem(t)
				if not self.can(b):
					it.setForeground(QColor('#b0b0b0'))
					it.setToolTip(f"The {self.radio.currentText()} can't tune this")
				self.bms.setItem(r, c, it)
		self.mark()
		return
	# show_bms

	def bm_sort(self, col, order):		# a heading clicked: Qt's new arrow. A new column starts A-Z / low to high, Hits most first
		down = order == Qt.SortOrder.DescendingOrder
		if col != self.bmsort[0] and Bookmarks.SORTS[col] == 'hits':
			down = True
			quiet(self.bms.horizontalHeader(), self.bms.horizontalHeader().setSortIndicator, col, Qt.SortOrder.DescendingOrder)
		self.bmsort = [col, down]
		self.bm.set_pref('sort', self.bmsort)
		self.show_bms()
		return
	# bm_sort

	def mark(self):		# rows at the dial's frequency, any mode, shaded
		for r, b in enumerate(self.bmlist):
			for c in range(self.bms.columnCount()):
				it = self.bms.item(r, c)
				if it:
					it.setBackground(QColor('#ffe680') if b['hz'] == self.freq.hz else QColor(0, 0, 0, 0))
		return
	# mark

	def can(self, b):
		return self.mode.findText(b['mode']) >= 0 and self.freq.lo <= b['hz'] <= self.freq.hi

	'''-------------------------------------------------------------------
		double-click: tune there, on the VFO it was saved on; this radio's settings from the bookmark, if it has any, and the decoder it was saved with
	-------------------------------------------------------------------'''

	def bm_pick(self, r):
		b = self.bmlist[r]
		if not self.rig or not self.can(b):
			return self.statusBar().showMessage(f"The {self.radio.currentText()} can't tune {b['hz'] / 1e6:.6f} {b['mode']}", 5000)
		self.bm.picked(b)
		v = (b['settings'].get(self.radio.currentText()) or {}).get('vfo')		# the VFO it was saved on
		if v in ('A', 'B') and isinstance(self.rig, (civ.Rig, cat.Rig, d75.Rig)) and v != self.rig.vfo and not self.mdm:		# the modem stays on VFO A
			self.pick_vfo(v)
			rig = self.rig
			QTimer.singleShot(400, lambda: self.rig is rig and self.bm_tune(b))		# once the radio is on that VFO (the FTdx10 tunes the VFO it last reported)
		else:
			self.bm_tune(b)
		return
	# bm_pick

	def bm_tune(self, b):
		self.rig.set('mode', b['mode'])
		self.rig.set('mhz', b['hz'] / 1e6)
		s = b['settings'].get(self.radio.currentText())
		if s:
			self.apply(b['mode'], s)
		d = b['settings'].get('decoder')		# the decoder it was saved with (Off too); bookmarks from before have none and leave the decoder alone
		if d in self.dec.KINDS and d != self.dec.pick.currentText() and self.dec.row.isEnabled():		# not while the modem has the sound card
			quiet(self.dec.pick, self.dec.pick.setCurrentText, d)
			self.dec.start(d)
		self.show_bms()
		return
	# bm_tune

	'''-------------------------------------------------------------------
		a decoder heard a station: into bookmarks.db's heard table, with where the radio is (dial frequency, mode)
	-------------------------------------------------------------------'''

	def sigpeak(self):		# the S-meter's highest in the last SIGWIN seconds, dB from S9 (None: no reading)
		now = time.monotonic()
		return max((v for t, v in self.sigs if now - t < self.SIGWIN), default=None)

	def heard(self, call, dec, level=None, fec='', via='', text='', svc=()):
		if self.rig:
			mode = self.mode.currentText().removesuffix('-D')		# LSB-D (packet transmit on) is the same dial as LSB: one row
			now = time.monotonic()
			sig = None if via else self.sigpeak()		# via a digipeater: the meter read the digipeater, not this station
			self.bm.heard(call, self.freq.hz, mode, dec, level, fec, via, text, svc, sig)
			now = time.monotonic()
			if now - self.hdat > 0.2:		# a new packet (a digipeated one calls this twice in a row: digipeater, sender)
				self.hdnew = set()
			self.hdnew.add((call, self.freq.hz, mode, dec))
			self.hdat = now
			g = re.search(r'\b[A-R]{2}[0-9]{2}[A-Xa-x]{2}\b', text)		# a beacon that says where it is (6 characters: 4 would catch too much that isn't a grid)
			if g:
				self.bm.set_grid(call.split('-')[0], g[0])
			if self.tabs.currentWidget() is self.hds:
				self.show_hds()
		return
	# heard

	'''-------------------------------------------------------------------
		where a US callsign is (for Miles): asked of callook.info (the FCC's records, free, no account) once, the answer kept in bookmarks.db
		no answer (offline): asked again the next time shack runs
	-------------------------------------------------------------------'''

	def locate(self, call):
		if call in self.asked or not re.fullmatch(r'(A[A-L]|[KNW])[A-Z]?\d[A-Z]{1,3}', call):
			return
		self.asked.add(call)
		r = self.net.get(QNetworkRequest(QUrl(f'https://callook.info/{call}/json')))
		r.finished.connect(lambda: self.located(call, r))
		return

	def located(self, call, r):
		try:
			d = json.loads(bytes(r.readAll()))
			grid = str((d.get('location') or {}).get('gridsquare') or '') if d.get('status') == 'VALID' else ''
			if d.get('status') in ('VALID', 'INVALID') and self.bm.grid(call) is None:		# anything else (UPDATING, an error page): ask another day
				self.bm.set_grid(call, grid if grid_ll(grid) else '')
				if self.tabs.currentWidget() is self.hds:
					self.show_hds()
		except (ValueError, AttributeError) as e:
			log.info(f'callook.info {call}: {r.errorString()} {e!r}')
		r.deleteLater()
		return
	# located

	def tab_shown(self, i):		# a tab on the right was picked: Gateways and Heard are filled when looked at
		w = self.tabs.widget(i)
		if w is self.gws:
			self.show_gws()
		elif w is self.hds:
			self.show_hds()
		return
	# tab_shown

	'''-------------------------------------------------------------------
		Packet tab: bookmarks.db's heard table, every frequency; the selected station stays selected when the table is filled again
	-------------------------------------------------------------------'''

	SIGWIN = 3		# s: a 300 baud packet lasts 2-3 s and direwolf reports it at its end, so the S-meter's highest in the last 3 s is its signal
	HDLIT = '#8fd48f'		# the latest packet's row(s): green (selection is yellow)
	HDMARK = '#f0dc9b'		# a highlighted station (right-click > Highlight): light gold

	def show_hds(self):
		r = self.hds.currentRow()
		was = (self.hdlist[r]['call'], self.hdlist[r]['hz']) if 0 <= r < len(self.hdlist) else None
		self.hdlist = self.bm.heards(*self.hdsort, grid_ll(self.dec.grid))
		self.hds.setRowCount(len(self.hdlist))
		today = time.strftime('%Y-%m-%d')
		for r, h in enumerate(self.hdlist):
			lit = (h['call'], h['hz'], h['mode'], h['dec']) in self.hdnew
			if h['grid'] is None:
				self.locate(h['call'].split('-')[0])
			last = h['last'][11:16] if h['last'].startswith(today) else h['last'][5:16]		# today: HH:MM, before: MM-DD HH:MM
			tip = f"{h['call']}   {h['hz'] / 1e6:.6f} {h['mode']}   {h['dec']}\nfirst heard {h['first']}\nlast heard {h['last']}\naudio level {h['level']}" + (f"\nsignal {sunits(h['sig'])}" if h['sig'] is not None else '') + (f"\ngrid {h['grid']}" if h['grid'] else '') + (f"\n{h['text']}" if h['text'] else '')
			miles, clicks = '' if h['miles'] is None else str(h['miles']), str(h['clicks'] or '')
			for c, x in enumerate((h['call'], f"{h['hz'] / 1e6:.6f}", str(h['hits']), last, h['fec'], h['svc'], miles, clicks, h['via'], h['text'])):
				it = QTableWidgetItem(x)
				it.setToolTip(tip)
				if c in (1, 2, 6, 7):
					it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
				if lit or h['mark']:
					it.setBackground(QColor(self.HDLIT if lit else self.HDMARK))
				self.hds.setItem(r, c, it)
			if was == (h['call'], h['hz']):
				self.hds.setCurrentCell(r, 0)
		return
	# show_hds

	def hd_sort(self, col, order):		# a heading clicked: Qt's new arrow. A new column starts A-Z / low to high; Hits and Clicked most first, Last newest first
		down = order == Qt.SortOrder.DescendingOrder
		if col != self.hdsort[0] and Bookmarks.HEARD[col] in ('hits', 'last', 'clicks'):
			down = True
			quiet(self.hds.horizontalHeader(), self.hds.horizontalHeader().setSortIndicator, col, Qt.SortOrder.DescendingOrder)
		self.hdsort = [col, down]
		self.bm.set_pref('heard sort', self.hdsort)
		self.show_hds()
		return
	# hd_sort

	'''-------------------------------------------------------------------
		double-click: Clicked counts it; on the callsign its QRZ page, anywhere else the frequency and mode it was heard on, with the decoder that heard it
	-------------------------------------------------------------------'''

	def hd_pick(self, r, c):
		h = self.hdlist[r]
		self.bm.clicked(h)
		self.show_hds()
		if c == 0:
			return self.dec.qrz(h['call'].split('-')[0])
		if not self.rig or not self.can(h):
			return self.statusBar().showMessage(f"The {self.radio.currentText()} can't tune {h['hz'] / 1e6:.6f} {h['mode']}", 5000)
		self.rig.set('mode', h['mode'])
		self.rig.set('mhz', h['hz'] / 1e6)
		if h['dec'] in self.dec.KINDS and h['dec'] != self.dec.pick.currentText() and self.dec.row.isEnabled():		# not while the modem has the sound card
			quiet(self.dec.pick, self.dec.pick.setCurrentText, h['dec'])
			self.dec.start(h['dec'])
		return
	# hd_pick

	'''-------------------------------------------------------------------
		right-click: QRZ's page for the station under the mouse, forget it, or forget all of them (asks first)
	-------------------------------------------------------------------'''

	def hd_menu(self, pos):
		r = self.hds.rowAt(pos.y())
		menu = QMenu(self)
		link = self.dec.link
		con = menu.addAction(f"Connect to {self.hdlist[r]['call']}") if r >= 0 else None
		if con:
			con.setEnabled(bool(link) and not link.peer and self.hdlist[r]['dec'] == 'Packet')
			con.setToolTip('Packet > Transmit on and no session: tunes there and connects' if not link else 'already in a session' if link.peer else '')
			menu.addSeparator()
		qrz = menu.addAction(f"Look up {self.hdlist[r]['call'].split('-')[0]} on QRZ") if r >= 0 else None
		gold = menu.addAction(f"{'Unhighlight' if self.hdlist[r]['mark'] else 'Highlight'} {self.hdlist[r]['call']}") if r >= 0 else None
		one = menu.addAction(f"Forget {self.hdlist[r]['call']}") if r >= 0 else None
		every = menu.addAction('Clear the whole list')
		act = menu.exec(self.hds.viewport().mapToGlobal(pos))
		if act is None:
			return
		if act == con:
			return self.hd_connect(self.hdlist[r])
		if act == qrz:
			return self.dec.qrz(self.hdlist[r]['call'].split('-')[0])
		if act == gold:
			self.bm.mark(self.hdlist[r]['call'], not self.hdlist[r]['mark'])
		elif act == one:
			self.bm.unheard(self.hdlist[r])
		elif act == every and QMessageBox.question(self, 'Packet', 'Forget every station heard, on every band?') == QMessageBox.StandardButton.Yes:
			self.bm.unheard()
		self.show_hds()
		return
	# hd_menu

	'''-------------------------------------------------------------------
		Heard > Connect to: its frequency, in the data version of its sideband (transmit stays on rear USB audio), then connect once the radio's there
	-------------------------------------------------------------------'''

	def hd_connect(self, h):
		if not self.rig or not self.can(h):
			return self.statusBar().showMessage(f"The {self.radio.currentText()} can't tune {h['hz'] / 1e6:.6f} {h['mode']}", 5000)
		want = h['mode'] if h['mode'].endswith('-D') else h['mode'] + '-D'
		if self.rig.mode != want:
			self.rig.set('mode', want)
		self.rig.set('mhz', h['hz'] / 1e6)
		self.dec.callbox.setText(h['call'])
		QTimer.singleShot(800, lambda: self.dec.link and not self.dec.link.peer and self.rig and self.rig.mode == want and self.dec.con_click())
		return
	# hd_connect

	'''-------------------------------------------------------------------
		Save: bookmark the open radio's frequency, mode and settings
	-------------------------------------------------------------------'''

	def bm_save(self):
		if not self.rig:
			return
		radio, hz, mode, s = self.radio.currentText(), self.freq.hz, self.mode.currentText(), self.settings()
		name = ''
		if not self.bm.find(hz, mode):		# a new one: ask for its name; an existing one keeps its own
			name, ok = QInputDialog.getText(self, 'Save bookmark', f'Name for {hz / 1e6:.6f} {mode}:')
			if not ok:
				return
		self.bm.save(radio, hz, mode, s, name.strip(), self.dec.pick.currentText() if self.dec.row.isEnabled() else None)		# modem running: the bookmark keeps the decoder it had
		if isinstance(self.rig, Rspdx):		# a CI-V radio remembers where it was by itself
			self.bm.set_last(radio, hz, mode, s)
		self.show_bms()
		self.statusBar().showMessage(f'Saved {hz / 1e6:.6f} {mode}', 5000)
		return
	# bm_save

	'''-------------------------------------------------------------------
		what a bookmark keeps for the open radio, and putting it back. CI-V: read off the controls (they follow the radio);
		never TX power, so a bookmark can't change what you transmit with
	-------------------------------------------------------------------'''

	def settings(self):
		if isinstance(self.rig, Rspdx):
			return self.rig.settings()
		if isinstance(self.rig, d75.Rig):		# the band it's on, nothing else yet
			return {'vfo': self.rig.vfo}
		s = {'v': self.vol.value(), 'rfg': self.rfg.value(), 'nbl': self.lvl['nb'].value(), 'nrl': self.lvl['nr'].value(), **{k: int(self.sw[k].isChecked()) for k in ('nb', 'nr')}}
		if isinstance(self.rig, cat.Rig):
			s.update(pa=self.pa.currentIndex(), ra=self.ra.currentIndex(), roof=self.roof.currentText())
		else:
			s.update(pre=int(self.sw['pre'].isChecked()), att=int(self.sw['att'].isChecked()))
		if self.fil.currentIndex() >= 0:
			s['fil'] = self.fil.currentIndex() + 1
		if self.agc.currentText() in self.agcs:
			s['agc'] = self.agcs[self.agc.currentText()]
		if self.rig.vfo in ('A', 'B'):		# CI-V: only known once a VFO button was clicked
			s['vfo'] = self.rig.vfo
		return s
	# settings

	def apply(self, mode, s):
		if isinstance(self.rig, Rspdx):
			return self.rig.apply(mode, s)
		for k, v in s.items():
			if k in self.ng:
				continue
			if k in ('rfg', 'nbl', 'nrl'):		# % -> 0-255
				self.rig.set(k, round(v * 2.55))
			elif k in ('v', 'fil', 'pre', 'att', 'nb', 'nr', 'agc', 'pa', 'ra', 'roof'):
				self.rig.set(k, v)
		return
	# apply

	'''-------------------------------------------------------------------
	-------------------------------------------------------------------'''

	def resizeEvent(self, e):		# the window's size, remembered for next time
		super().resizeEvent(e)
		if hasattr(self, 'winsave'):
			self.winsave.start()
		return

	def save_size(self):
		if not (self.isMaximized() or self.isFullScreen()):		# maximized: keep the size it comes back to
			self.bm.set_pref('window', [self.width(), self.height()])
		return

	def closeEvent(self, e):
		self.shut()
		e.accept()
		return
	# closeEvent
# class Shack



import sys
import consts

'''-------------------------------------------------------------------
-------------------------------------------------------------------'''

if __name__ == '__main__':
	import os
	import sys
	import re
	import logging
	import subprocess


	try:
		app = 'shack'
		logg = utils.setup_logging(name=app, logfile=f'{app}.log', level=logging.DEBUG, console=True)
		hn, cron, cwd, vscode, machine = utils.setup()
		if sys.stdin.isatty():
			# subprocess.run("stty erase '^?'", shell=True)
			pass
		home = os.environ.get('HOME')

		if vscode:
			def exclude(fname):				# exclude files/directories from file explorer in vscode
				import json

				with open(fname, 'r') as f:
					d = json.load(f)

				with open('.vscode/settings.json', 'r') as f:
					pass

				pass

			if os.path.exists('excludes.json'):
				exclude('excludes.json')

			caller = os.environ.get('CALLER')
			print(consts.COLOR_RESET)
			subprocess.run('clear')
			utils.set_vscode_terminal_font_size(12)
			utils.set_vscode_editor_font_size(15)
			# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
			# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
			# sys.argv.append('88.1')
			ap = argparse.ArgumentParser()
			ap.add_argument('--baud', type=int, help="override the radio's usual CI-V baud")
			ap.add_argument('--addr', type=lambda x: int(x, 0), default=0xA4)
			ap.add_argument('--ptt', type=int, default=4532, help='rigctld-style PTT port for xara, 0 = off')
			ap.add_argument('--rspdx', default=str(HERE / 'rspdx'), help='the rspdx program (run as rspdx -n)')
			a = ap.parse_args()
		else:
			ap = argparse.ArgumentParser()
			ap.add_argument('--baud', type=int, help="override the radio's usual CI-V baud")
			ap.add_argument('--addr', type=lambda x: int(x, 0), default=0xA4)
			ap.add_argument('--ptt', type=int, default=4532, help='rigctld-style PTT port for xara, 0 = off')
			ap.add_argument('--rspdx', default=str(HERE / 'rspdx'), help='the rspdx program (run as rspdx -n)')
			a = ap.parse_args()
		# if vscode


		utils.blank(logg)
		logg.info('starting...')

		ll = len(sys.argv)

		if ll > 1:
			cmd = sys.argv[1]
			if cmd == 'wp':
				pass
			pass

		# ---------------------------------------------------------------------------------------------------

		app = QApplication(sys.argv)
		app.setStyleSheet(styles.style)
		w = Shack(a)
		sys.excepthook = lambda t, v, tb: (logg.error('uncaught exception', exc_info=(t, v, tb)), w.statusBar().showMessage(f'Error: {v!r}  (details in shack.log)', 10000))		# PyQt6 aborts on an exception in a slot unless excepthook is ours
		w.show()
		sys.exit(app.exec())


		# ---------------------------------------------------------------------------------------------------

		pass
	except Exception as e:
		s = utils.mkexs(e)
		if isinstance(e.args[0], str):
			s += e.args[0]
			print('\n%s%s%s' % (consts.KRED, s, consts.COLOR_RESET))
		elif isinstance(e.args[0], list):
			t = e.args[0]		# a tuple
			s = ''
			for i in t:
				s += i + '\n'
			print('\n%s%s%s' % (consts.KRED, s, consts.COLOR_RESET))
		else:
			s = repr(e)
			s = s.replace('\\n', '\n')
			s = re.sub(r'\\', '', s)
			print('\n%s%s%s' % (consts.KRED, s, consts.COLOR_RESET))
	finally:
		logg.info('finally')
		logging.shutdown()
		pass

