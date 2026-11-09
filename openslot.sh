#!/bin/bash
# openslot.sh: rigctld (the radio's CAT) -> xara (VARA modem) -> OpenSlot. Closing OpenSlot stops xara and rigctld.
#	openslot.sh		listen only: xara can't transmit (plays to ALSA null, no PTT)
#	openslot.sh tx		transmit: xara plays into the radio and keys it through rigctld
#	openslot.sh ic705 tx	which radio: ftdx10 or ic705; only needed when both are on
# logs: rigctld.log, xara.log next to this script (tail -f xara.log to watch the modem)
# shack must be closed: rigctld needs the radio's CAT port, xara its USB audio
# OpenSlot's Tune window drive (dB) overrides DRIVE below: keep it at -15 for both radios; each radio's level is set by PCM (the card's mixer)

CALL=N7RTS
GRID=EM85RP
BW=2300
LEAD=150		# ms PTT is held before the audio starts: CAT keying through rigctld + the radio's T/R relay
XARA=~/installed/builds/xara/target/release/xara
OPENSLOT=~/installed/builds/OpenSlot/target/release/openslot
HERE=$(dirname "$(readlink -f "$0")")
SYS=/sys

radio() {		# per radio: CAT port (by-id pattern), rigctld options, xara TX drive dB
	case $1 in
	ftdx10)	PORT='*CP2105_Dual_USB_to_UART*if00-port0'
		RIG='-m 1042 -s 38400 -C serial_handshake=None,rts_state=ON'		# the radio answers only with RTS high
		DRIVE=-15
		PCM=51;;		# RF POWER 10 W, RPORT GAIN 5, drive -15: ALC 0, 6.5 W on the CN-501H (52: ALC 0.12; 2026-11-09)
	ic705)	PORT='*IC-705*if00'
		RIG='-m 3085 -s 115200 -C dtr_state=OFF,rts_state=OFF'		# keys over CI-V; DTR/RTS low
		DRIVE=-15
		PCM=41;;		# NOT CHECKED: 41 is a guess at 2026-11-08's level (drive -16 at 100 % power: 10.1 W, ALC 0.008; -15: ALC 0.09)
	*)	return 1;;
	esac
}

card_of() {		# plughw:N of the sound card in the same radio as serial port $1: same USB device, else same hub
	local d=$(readlink -f $SYS/class/tty/$(basename "$(readlink -f "$1")")/device)
	while [ "$d" != / ] && ! [[ $(basename "$d") =~ ^[0-9]+-[0-9.]+$ ]]; do d=$(dirname "$d"); done		# up to the USB device (1-3.1, not 1-3.1:1.0)
	[ "$d" = / ] && return 1
	local c
	local tops=$d
	[[ $(basename "$(dirname "$d")") =~ ^[0-9]+-[0-9.]+$ ]] && tops="$d $(dirname "$d")"		# a hub inside the radio, never the computer's root hub
	for top in $tops; do
		for c in $SYS/class/sound/card*; do
			case $(readlink -f $c/device) in "$top"/*) echo plughw:${c##*card}; return 0;; esac
		done
	done
	return 1
}

up() { nc -z 127.0.0.1 $1 2>/dev/null; }		# something listening on port $1?

wait_for() {		# port $1 opens while process $2 lives, up to 10 s
	for i in $(seq 100); do
		up $1 && return 0
		kill -0 $2 2>/dev/null || return 1
		sleep 0.1
	done
	return 1
}

stop() {
	[ -n "$XPID" ] && kill $XPID 2>/dev/null
	[ -n "$RPID" ] && kill $RPID 2>/dev/null
	wait 2>/dev/null
	echo "xara and rigctld stopped"
}

NAME= TX=
for a in "$@"; do
	case $a in
	tx) TX=1;;
	ftdx10|ic705) NAME=$a;;
	*) echo "usage: openslot.sh [ftdx10|ic705] [tx]"; exit 1;;
	esac
done
if [ -z "$NAME" ]; then		# whichever radio is on
	for r in ftdx10 ic705; do
		radio $r; ls /dev/serial/by-id/$PORT >/dev/null 2>&1 && NAME="$NAME $r"
	done
	NAME=${NAME# }
	case $NAME in
	'') echo "no radio found (FTdx10 or IC-705 on and plugged in?)"; exit 1;;
	*' '*) echo "both radios are on: openslot.sh ftdx10|ic705 [tx]"; exit 1;;
	esac
fi
radio $NAME
CAT=$(ls /dev/serial/by-id/$PORT 2>/dev/null | head -1)
[ -n "$CAT" ] || { echo "$NAME not found: on and plugged in?"; exit 1; }
CARD=$(card_of "$CAT") || { echo "$NAME: no USB sound card found next to $CAT"; exit 1; }
echo "$NAME: CAT $CAT, audio $CARD"
amixer -q -c ${CARD#plughw:} sset PCM $PCM || { echo "couldn't set PCM on $CARD"; exit 1; }		# the radios' cards share one saved mixer state (both are named CODEC)
echo "$NAME: PCM $PCM"

for p in 4532 8300; do
	if up $p; then
		echo "port $p is already in use (shack? an old rigctld or xara?): close it first"
		exit 1
	fi
done
trap stop EXIT
trap 'exit 1' INT TERM

rigctld $RIG -r $CAT -t 4532 > "$HERE/rigctld.log" 2>&1 &
RPID=$!
wait_for 4532 $RPID || { echo "rigctld didn't start: see rigctld.log"; exit 1; }
echo "rigctld: radio on $(printf 'f\n' | nc -q 1 127.0.0.1 4532) Hz"

if [ -n "$TX" ]; then
	AUDIO="--playback $CARD --radio --ptt rigctld:127.0.0.1:4532 --drive-db $DRIVE --ptt-lead-ms $LEAD"
	echo "xara: TRANSMIT enabled, drive $DRIVE dB, PTT lead $LEAD ms"
else
	AUDIO="--playback null"
	echo "xara: listen only"
fi
"$XARA" --call $CALL --audio alsa --capture $CARD $AUDIO --hf $BW > "$HERE/xara.log" 2>&1 &
XPID=$!
wait_for 8300 $XPID || { echo "xara didn't start: see xara.log"; exit 1; }
echo "xara: up (tail -f xara.log)"

"$OPENSLOT" --callsign $CALL --locator $GRID
