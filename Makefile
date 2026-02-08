#
# Makefile for the maudec tool
#

# Python command to be included in the shebang
PYTHON ?= /usr/bin/env python3

# Bundle the all the Python and data file into single executable zip file
# (based on Stack Overflow's question 17486578)

RESOURCES = maudec/data/*.json
CODE      = maudec/*.py maudec/*/*.py

dist/maudec: dist $(RESOURCES) $(CODE)
	# Create temporary directory and copy the package into it
	mkdir -p zip
	cp -r maudec zip
	# Create a __main__ file for the package that invokes the maudec one
	echo -e 'import sys\nfrom maudec.__main__ import main\nsys.exit(main())' > zip/__main__.py
	touch -ma zip/* zip/*/*
	# Compress that directory into a zip file
	cd zip ; zip -q ../maudec.zip $(RESOURCES) $(CODE) __main__.py
	rm -rf zip
	# Put the shebang and then the zip file into the executable bundle
	echo '#!$(PYTHON)' > $@
	cat maudec.zip >> $@
	rm maudec.zip
	chmod a+x $@

wheel:
	pip wheel --no-deps -w dist .
	$(RM) -r build maudec.egg-info

dist:
	mkdir -p dist
