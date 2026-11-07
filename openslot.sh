#!/bin/bash
# openslot.sh: rigctld (FTdx10 CAT) -> xara (VARA modem) -> OpenSlot. Closing OpenSlot stops xara and rigctld.
#	openslot.sh		listen only: xara can't transmit (plays to ALSA null, no PTT)
#	openslot.sh tx		transmit: xara plays into the radio and keys it through rigctld
# logs: rigctld.log, xara.log next to this script (tail -f xara.log to watch the modem)
# shack must be closed: rigctld needs the radio's CAT port, xara its USB audio

CALL=N7RTS
GRID=EM85RP
BW=2300
LEAD=150		# ms PTT is held before the audio starts: CAT keying through rigctld + the radio's T/R relay
DRIVE=-11		# xara TX drive, dB: FTdx10 at 25 W, RPORT GAIN 5 -> 18.7 W with ALC 0 on TUNE (2026-11-07)
CAT=/dev/serial/by-id/usb-Silicon_Labs_CP2105_Dual_USB_to_UART_Bridge_Controller_016AE87B-if00-port0
CARD=plughw:CARD=CODEC
XARA=~/installed/builds/xara/target/release/xara
OPENSLOT=~/installed/builds/OpenSlot/target/release/openslot
HERE=$(dirname "$(readlink -f "$0")")

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

for p in 4532 8300; do
	if up $p; then
		echo "port $p is already in use (shack? an old rigctld or xara?): close it first"
		exit 1
	fi
done
trap stop EXIT
trap 'exit 1' INT TERM

rigctld -m 1042 -r $CAT -s 38400 -t 4532 -C serial_handshake=None,rts_state=ON > "$HERE/rigctld.log" 2>&1 &
RPID=$!
wait_for 4532 $RPID || { echo "rigctld didn't start: see rigctld.log"; exit 1; }
echo "rigctld: radio on $(printf 'f\n' | nc -q 1 127.0.0.1 4532) Hz"

if [ "$1" = tx ]; then
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
