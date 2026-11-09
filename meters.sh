#!/bin/bash
# meters.sh [port]: PTT, power, ALC, SWR from rigctld once a second (run next to openslot.sh; Ctrl-C to stop)

PORT=${1:-4532}
trap '' PIPE		# rigctld gone: write fails instead of killing the script

while true; do
	if ! exec 3<>/dev/tcp/127.0.0.1/$PORT; then
		echo "$(date +%T)  no rigctld on $PORT"; sleep 2; continue
	fi 2>/dev/null
	while printf 't\nl RFPOWER_METER_WATTS\nl ALC\nl SWR\n' >&3 2>/dev/null; do
		read -t 2 -u 3 ptt && read -t 2 -u 3 w && read -t 2 -u 3 alc && read -t 2 -u 3 swr || break
		[ "$ptt" = 1 ] && tx=TX || tx=rx
		printf '%s  %s  %6.2f W   ALC %5.2f   SWR %5.2f\n' "$(date +%T)" $tx "$w" "$alc" "$swr" 2>/dev/null \
			|| echo "$(date +%T)  $tx  $w  $alc  $swr"		# not numbers (RPRT error): show raw
		sleep 1
	done
	exec 3<&-
	echo "$(date +%T)  lost rigctld"; sleep 2
done
