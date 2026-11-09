#!/bin/bash
# meters.sh [port]: power, ALC, SWR from rigctld while the radio transmits (run next to openslot.sh; Ctrl-C to stop)

PORT=${1:-4532}
EVERY=2			# seconds between lines during TX
trap '' PIPE		# rigctld gone: write fails instead of killing the script

while true; do
	if ! exec 3<>/dev/tcp/127.0.0.1/$PORT; then
		echo "$(date +%T)  no rigctld on $PORT"; sleep 2; continue
	fi 2>/dev/null
	echo "$(date +%T)  connected, waiting for TX"
	tx=0
	while printf 't\n' >&3 2>/dev/null && read -t 2 -u 3 ptt; do
		if [ "$ptt" != 1 ]; then
			[ $tx = 1 ] && echo "$(date +%T)  rx"
			tx=0; sleep 0.5; continue
		fi
		tx=1
		printf 'l RFPOWER_METER_WATTS\nl ALC\nl SWR\n' >&3 2>/dev/null || break
		read -t 2 -u 3 w && read -t 2 -u 3 alc && read -t 2 -u 3 swr || break
		printf '%s  TX  %6.2f W   ALC %5.2f   SWR %5.2f\n' "$(date +%T)" "$w" "$alc" "$swr" 2>/dev/null \
			|| echo "$(date +%T)  TX  $w  $alc  $swr"		# not numbers (RPRT error): show raw
		sleep $EVERY
	done
	exec 3<&-
	echo "$(date +%T)  lost rigctld"; sleep 2
done
