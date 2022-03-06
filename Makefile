# JABS linux based makefile

tests:
	python3 -m unittest tests
	python2 -m unittest tests

# Generates a debian package
deb: debian.py
	./debian.py
