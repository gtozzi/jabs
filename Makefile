# JABS linux based makefile

tests:
	./dockertests.py

oldtests:
	python3 -m unittest tests

# Generates a pypy package
py:
	python3 -m build --no-isolation

# Generates a debian package
deb: debian.py
	./debian.py

disttest:
	python3 -m twine upload --repository testpypi dist/*
