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

import io
import os
import types
import unittest
import unittest.mock

import jabs.sync
import jabs.snapshot


class TestMyLogger(unittest.TestCase):

	def _make_sets(self, names:list) -> list:
		''' Build minimal fake set objects with just a .name attribute '''
		return [types.SimpleNamespace(name=n) for n in names]

	def test_prefix_in_print(self):
		''' prefix is prepended to every printed line '''
		sl = jabs.sync.MyLogger(prefix='[X] ')
		sl.setdebuglvl(0)
		with unittest.mock.patch('builtins.print') as mock_print:
			sl.add('hello')
			mock_print.assert_called_once_with('[X] hello')

	def test_no_prefix_by_default(self):
		''' default MyLogger has no prefix '''
		sl = jabs.sync.MyLogger()
		sl.setdebuglvl(0)
		with unittest.mock.patch('builtins.print') as mock_print:
			sl.add('hello')
			mock_print.assert_called_once_with('hello')

	def test_prefix_not_in_getstr(self):
		''' prefix must never appear in the buffered log (email safety) '''
		sl = jabs.sync.MyLogger(prefix='[PREFIX] ')
		sl.setdebuglvl(0)
		with unittest.mock.patch('builtins.print'):
			sl.add('line one')
			sl.add('line two')
		log = sl.getstr()
		self.assertNotIn('[PREFIX]', log)
		self.assertIn('line one', log)
		self.assertIn('line two', log)

	def test_color_prefixes_count(self):
		''' color_prefixes returns one entry per set '''
		sets = self._make_sets(['Alpha', 'Beta', 'Gamma'])
		prefixes = jabs.sync.MyLogger.color_prefixes(sets)
		self.assertEqual(set(prefixes.keys()), {'Alpha', 'Beta', 'Gamma'})

	def test_color_prefixes_padding(self):
		''' all prefixes are padded to the length of the longest name '''
		sets = self._make_sets(['A', 'LongName', 'Mid'])
		prefixes = jabs.sync.MyLogger.color_prefixes(sets)
		max_len = len('LongName')
		for name, prefix in prefixes.items():
			padded = name.ljust(max_len)
			self.assertIn(padded, prefix)

	def test_color_prefixes_all_different(self):
		''' every set gets a unique ANSI colour (up to the colour cycle length) '''
		n = len(jabs.sync._ANSI_COLORS)
		sets = self._make_sets([f'Set{i}' for i in range(n)])
		prefixes = jabs.sync.MyLogger.color_prefixes(sets)
		colours_used = set()
		for prefix in prefixes.values():
			for colour in jabs.sync._ANSI_COLORS:
				if colour in prefix:
					colours_used.add(colour)
		self.assertEqual(len(colours_used), n)

	def test_color_prefixes_cycle(self):
		''' colours wrap around when there are more sets than colours '''
		n = len(jabs.sync._ANSI_COLORS)
		sets = self._make_sets([f'Set{i}' for i in range(n + 2)])
		prefixes = jabs.sync.MyLogger.color_prefixes(sets)
		first_colour = jabs.sync._ANSI_COLORS[0]
		self.assertIn(first_colour, prefixes['Set0'])
		self.assertIn(first_colour, prefixes[f'Set{n}'])

	def test_noprint_suppresses_output(self):
		''' noprint=True prevents printing regardless of debug level '''
		sl = jabs.sync.MyLogger(prefix='[X] ')
		sl.setdebuglvl(0)
		with unittest.mock.patch('builtins.print') as mock_print:
			sl.add('silent', noprint=True)
			mock_print.assert_not_called()
		self.assertIn('silent', sl.getstr())

	def test_level_filtering(self):
		''' messages above debuglvl are not printed but are still buffered '''
		sl = jabs.sync.MyLogger()
		sl.setdebuglvl(0)
		with unittest.mock.patch('builtins.print') as mock_print:
			sl.add('debug msg', lvl=1)
			mock_print.assert_not_called()
		self.assertIn('debug msg', sl.getstr(lvl=1))
		self.assertNotIn('debug msg', sl.getstr(lvl=0))


class TestRun(unittest.TestCase):

	def test_run_jabs(self):
		''' Run a test backup '''
		DEST = '/tmp/jabs-backup'

		if not os.path.exists(DEST):
			os.mkdir(DEST)

		j = jabs.sync.Jabs()
		j.debug = 1
		res = j.run('jabs.cfg', '/tmp/', pidFilePath='/tmp/jabs.pid', onlySets=['Test'], force=True)
		self.assertTrue(res == 0, res)

	def test_run_jabs_parallel(self):
		''' Run the same test backup with parallel=True; result must be identical '''
		DEST = '/tmp/jabs-backup'

		if not os.path.exists(DEST):
			os.mkdir(DEST)

		j = jabs.sync.Jabs()
		j.debug = 1
		res = j.run('jabs.cfg', '/tmp/', pidFilePath='/tmp/jabs.pid', onlySets=['Test'], force=True, parallel=True)
		self.assertTrue(res == 0, res)


if __name__ == '__main__':
	unittest.main()
