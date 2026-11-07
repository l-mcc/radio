CXX = g++
MODE ?= O3
PREFIX ?= /usr/local
SHARE = $(PREFIX)/share/rspdx
CXXFLAGS = -std=c++17 -Wall -Wextra -pthread -march=native -$(MODE)
LDFLAGS = -L/usr/local/lib -Wl,-rpath,/usr/local/lib -Wl,--disable-new-dtags
LDLIBS = -lsdrplay_api -lliquid -lrnnoise -lpulse-simple -lpulse -lsqlite3
OBJS = rsp.o control.o audio.o waterfall.o demod.o

rspdx: rspdx.cpp demod.h $(OBJS)
	$(CXX) $(CXXFLAGS) -DSHARE_DIR='"$(SHARE)"' -o $@ rspdx.cpp $(OBJS) $(LDFLAGS) $(LDLIBS)

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

.PHONY: clean install uninstall
clean:
	rm -f rspdx $(OBJS)

install: rspdx	# sudo make -f rspdx.make install
	install -D -m 755 rspdx $(DESTDIR)$(PREFIX)/bin/rspdx
	install -D -m 644 -t $(DESTDIR)$(SHARE) index.html app.js	# waterfall page, used when rspdx runs outside a checkout

uninstall:
	rm -f $(DESTDIR)$(PREFIX)/bin/rspdx
	rm -rf $(DESTDIR)$(SHARE)
