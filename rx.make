CXX = g++
MODE ?= O3
CXXFLAGS = -std=c++17 -Wall -Wextra -pthread -march=native -$(MODE)
LDFLAGS = -L/usr/local/lib -Wl,-rpath,/usr/local/lib -Wl,--disable-new-dtags
LDLIBS = -lsdrplay_api -lliquid -lpulse-simple -lpulse -lsqlite3
OBJS = rsp.o control.o audio.o waterfall.o demod.o

rx: rx.cpp demod.h $(OBJS)
	$(CXX) $(CXXFLAGS) -o $@ rx.cpp $(OBJS) $(LDFLAGS) $(LDLIBS)

rsp.o: rsp.cpp rsp.h
	$(CXX) $(CXXFLAGS) -c rsp.cpp -o rsp.o

control.o: control.cpp control.h rsp.h
	$(CXX) $(CXXFLAGS) -c control.cpp -o control.o

audio.o: audio.cpp audio.h
	$(CXX) $(CXXFLAGS) -c audio.cpp -o audio.o

waterfall.o: waterfall.cpp waterfall.h control.h
	$(CXX) $(CXXFLAGS) -c waterfall.cpp -o waterfall.o

demod.o: demod.cpp demod.h
	$(CXX) $(CXXFLAGS) -c demod.cpp -o demod.o

.PHONY: clean
clean:
	rm -f rx $(OBJS)
