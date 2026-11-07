#!/bin/sh
sqlite3 -column -header rms.db "select call, band, dial_khz, bw, miles, bearing from channels where kind = 'VARA HF' order by miles limit 20"
sqlite3 -column -header rms.db "select band, bw, count(*) n from channels where kind = 'VARA HF' group by band, bw"
