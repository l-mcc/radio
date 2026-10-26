#!/bin/sh
# g++ -shared -fPIC -I/usr/local/include -g -O0 -L/usr/local/lib -o librsp.so rsp.cpp utils.cpp -lsdrplay_api -lportaudio -lm
# g++ -std=c++17 -O2 -Wall rsp.cpp -o rsp -lsdrplay_api -pthread
# g++ -std=c++17 -O2 -Wall am.cpp -o am -lsdrplay_api -pthread
# g++ -std=c++17 -O2 -Wall am.cpp
# ./rsp 10000000 test.iq

# make -f rsp.make
# make -f am.make
# ./am

rm *.o
touch *.cpp *.h *.make *.patch
chmod 664 *.cpp *.h *.make
# make -f am.make
# make -f fm.make
# make -f ssb.make
make -f rx.make

# ./fm
# taskset -c 2 ./am

# taskset -c 2 ./ssb 7.251 lsb        # 1. no waterfall: should match yesterday
# taskset -c 2 ./ssb -w 7.251 lsb     # 2. -w, no browser open
# taskset -c 2 ./ssb -w 7.251 lsb     # 3. -w, page open the whole run
