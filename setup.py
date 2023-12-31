#!/usr/bin/env python3

""" @package docstring
JABS - Just Another Backup Script

@author Gabriele Tozzi <gabriele@tozzi.eu>
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.
You should have received a copy of the GNU General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.
"""

import m2r2
import distutils.core

import jabs.consts

with open('README.md', 'rt') as f:
	readme = f.read()
readme = m2r2.convert(readme)

# See https://docs.python.org/3.8/distutils/setupscript.html
distutils.core.setup(
	name = 'jabs',
	version = jabs.consts.version_str(),
	description = 'Just Another Backup Script',
	long_description = readme,
	author = jabs.consts.__author__,
	author_email = jabs.consts.__author_email__,
	url = 'https://github.com/gtozzi/jabs',
	packages = ['jabs'],
	classifiers = [
		"Programming Language :: Python :: 3",
		"License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
		"Operating System :: OS Independent",
	],
)
