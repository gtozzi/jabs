#!/usr/bin/env python3

""" @package docstring
JABS - Just Another Backup Script

This file contains unit tests useful for developing purposes

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

import os
import unittest


class TestRun(unittest.TestCase):

	def test_import(self):
		import jabs
		jsnap = __import__('jabs-snapshot')

	def test_versions(self):
		import jabs
		jsnap = __import__('jabs-snapshot')

		self.assertTrue(jabs.VERSION.split(' ')[1] == jsnap.VERSION.split(' ')[1], "{} != {}".format(jabs.VERSION, jsnap.VERSION))

	def test_run_jabs(self):
		''' Run a test backup '''
		DEST = '/tmp/jabs-backup'

		if not os.path.exists(DEST):
			os.mkdir(DEST)

		import jabs
		j = jabs.Jabs()
		j.debug = 1
		res = j.run('jabs.cfg', '/tmp/', pidFilePath='/tmp/jabs.pid', onlySets=['Test'], force=True)
		self.assertTrue(res == 0, res)

if __name__ == '__main__':
	unittest.main()
