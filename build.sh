#!/bin/sh
rm *.o
touch *.cpp *.h *.make *.patch
chmod 664 *.cpp *.h *.make
make -f rspdx.make
sudo make -f rspdx.make install			# prefix is /usr/local
