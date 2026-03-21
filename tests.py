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
import shutil
import time
import types
import datetime
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


class TestStatusServer(unittest.TestCase):
	''' Tests for StatusServer and the Jabs status tracking methods '''

	# A port unlikely to conflict; tests bind briefly then release
	PORT = 19876
	PID_FILE = '/tmp/jabs-test-status.pid'

	def setUp(self):
		if os.path.exists(self.PID_FILE):
			os.unlink(self.PID_FILE)

	def tearDown(self):
		if os.path.exists(self.PID_FILE):
			os.unlink(self.PID_FILE)

	def _make_jabs_with_status(self, sets_data:list) -> jabs.sync.Jabs:
		'''
			Build a Jabs instance with _status pre-populated.
			@param sets_data list: list of dicts with keys: name, pri, running, started, completed, success, item
			@return Jabs instance ready for status queries
		'''
		j = jabs.sync.Jabs()
		j._status = {
			'pid': os.getpid(),
			'started': datetime.datetime(2026, 1, 1, 10, 0, 0),
			'sets': {d['name']: dict(d) for d in sets_data},
		}
		return j

	def test_query_returns_none_when_nothing_listening(self):
		''' query() returns None when no server is on the given port '''
		result = jabs.sync.StatusServer.query(self.PORT)
		self.assertIsNone(result)

	def test_server_serves_status(self):
		''' StatusServer starts, serves GET /status, and stops cleanly '''
		j = self._make_jabs_with_status([])
		s = jabs.sync.StatusServer(self.PORT, j._get_status)
		s.start()
		try:
			result = jabs.sync.StatusServer.query(self.PORT)
			self.assertIsNotNone(result)
			self.assertIn('sets', result)
			self.assertIn('pid', result)
			self.assertIn('now', result)
		finally:
			s.stop()

	def test_server_404_on_unknown_path(self):
		''' StatusServer returns 404 for any path other than /status '''
		import urllib.request
		import urllib.error
		j = self._make_jabs_with_status([])
		s = jabs.sync.StatusServer(self.PORT, j._get_status)
		s.start()
		try:
			with self.assertRaises(urllib.error.HTTPError) as ctx:
				urllib.request.urlopen(f'http://127.0.0.1:{self.PORT}/other', timeout=3)
			self.assertEqual(ctx.exception.code, 404)
		finally:
			s.stop()

	def test_get_status_empty(self):
		''' _get_status on an empty sets dict returns a list '''
		j = self._make_jabs_with_status([])
		result = j._get_status()
		self.assertIsInstance(result['sets'], list)
		self.assertEqual(result['sets'], [])

	def test_get_status_sorted_by_pri_then_name(self):
		''' _get_status returns sets sorted by pri ascending, then by name '''
		j = self._make_jabs_with_status([
			{'name': 'Zeta', 'pri': 10, 'running': False, 'started': None, 'completed': None, 'success': None, 'item': None},
			{'name': 'Alpha', 'pri': 20, 'running': False, 'started': None, 'completed': None, 'success': None, 'item': None},
			{'name': 'Beta', 'pri': 10, 'running': False, 'started': None, 'completed': None, 'success': None, 'item': None},
		])
		sets = j._get_status()['sets']
		names = [s['name'] for s in sets]
		self.assertEqual(names, ['Beta', 'Zeta', 'Alpha'])

	def test_get_status_serializes_datetimes(self):
		''' _get_status converts datetime fields to ISO strings '''
		t = datetime.datetime(2026, 3, 21, 12, 0, 0)
		j = self._make_jabs_with_status([
			{'name': 'A', 'pri': 10, 'running': False, 'started': t, 'completed': t, 'success': True, 'item': None},
		])
		s = j._get_status()['sets'][0]
		self.assertIsInstance(s['started'], str)
		self.assertIsInstance(s['completed'], str)
		self.assertEqual(s['started'], t.isoformat())

	def test_get_status_elapsed_only_when_running(self):
		''' elapsed is present for running sets and absent for finished/pending ones '''
		t = datetime.datetime.now() - datetime.timedelta(seconds=5)
		j = self._make_jabs_with_status([
			{'name': 'Running', 'pri': 10, 'running': True, 'started': t, 'completed': None, 'success': None, 'item': '/src'},
			{'name': 'Done', 'pri': 20, 'running': False, 'started': t, 'completed': datetime.datetime.now(), 'success': True, 'item': None},
		])
		sets = {s['name']: s for s in j._get_status()['sets']}
		self.assertIn('elapsed', sets['Running'])
		self.assertNotIn('elapsed', sets['Done'])

	def test_update_status_sets_running(self):
		''' _update_status marks a set as running and stores item and started time '''
		j = self._make_jabs_with_status([
			{'name': 'MySet', 'pri': 10, 'running': False, 'started': None, 'completed': None, 'success': None, 'item': None},
		])
		t = datetime.datetime(2026, 3, 21, 8, 0, 0)
		j._update_status('MySet', '/home/user', t)
		entry = j._status['sets']['MySet']
		self.assertTrue(entry['running'])
		self.assertEqual(entry['started'], t)
		self.assertEqual(entry['item'], '/home/user')

	def test_update_status_noop_when_disabled(self):
		''' _update_status does nothing when _status is empty (server disabled) '''
		j = jabs.sync.Jabs()
		j._update_status('NoSet', '/path', datetime.datetime.now())
		self.assertEqual(j._status, {})

	def test_clear_status_marks_finished(self):
		''' _clear_status sets running=False, success, and completed '''
		t_start = datetime.datetime(2026, 3, 21, 8, 0, 0)
		t_end = datetime.datetime(2026, 3, 21, 9, 0, 0)
		j = self._make_jabs_with_status([
			{'name': 'MySet', 'pri': 10, 'running': True, 'started': t_start, 'completed': None, 'success': None, 'item': '/x'},
		])
		j._clear_status('MySet', success=True, completed=t_end)
		entry = j._status['sets']['MySet']
		self.assertFalse(entry['running'])
		self.assertTrue(entry['success'])
		self.assertEqual(entry['completed'], t_end)

	def test_clear_status_noop_when_disabled(self):
		''' _clear_status does nothing when _status is empty (server disabled) '''
		j = jabs.sync.Jabs()
		j._clear_status('NoSet', success=True, completed=datetime.datetime.now())
		self.assertEqual(j._status, {})

	def test_clear_status_unknown_set_is_safe(self):
		''' _clear_status on a set name not in _status does not raise '''
		j = self._make_jabs_with_status([])
		j._clear_status('Ghost', success=False, completed=datetime.datetime.now())

	def test_status_port_in_run(self):
		'''
			run() starts the status server during execution and stops it on return.
			_run_set is mocked to avoid needing rsync in the test environment.
		'''
		queried_during:list = []

		def fake_run_set(s, *args, **kwargs):
			queried_during.append(jabs.sync.StatusServer.query(self.PORT))

		DEST = '/tmp/jabs-backup'
		if not os.path.exists(DEST):
			os.mkdir(DEST)

		j = jabs.sync.Jabs()
		j.debug = -1
		with unittest.mock.patch.object(j, '_run_set', side_effect=fake_run_set), \
				unittest.mock.patch('jabs.sync.Program.get_version', return_value='mock'):
			res = j.run('jabs.cfg', '/tmp/', pidFilePath=self.PID_FILE,
				onlySets=['Test'], force=True, status_port=self.PORT)

		self.assertEqual(res, 0)
		# Server was reachable while run() was executing
		self.assertTrue(len(queried_during) > 0)
		self.assertIsNotNone(queried_during[0])
		self.assertIn('sets', queried_during[0])
		# Server is stopped after run() returns
		self.assertIsNone(jabs.sync.StatusServer.query(self.PORT))


class TestRun(unittest.TestCase):

	PID_FILE = '/tmp/jabs-test-run.pid'

	def setUp(self):
		if os.path.exists(self.PID_FILE):
			os.unlink(self.PID_FILE)

	def tearDown(self):
		if os.path.exists(self.PID_FILE):
			os.unlink(self.PID_FILE)

	def test_run_jabs(self):
		''' Run a test backup '''
		DEST = '/tmp/jabs-backup'

		if not os.path.exists(DEST):
			os.mkdir(DEST)

		j = jabs.sync.Jabs()
		j.debug = 1
		res = j.run('jabs.cfg', '/tmp/', pidFilePath=self.PID_FILE, onlySets=['Test'], force=True)
		self.assertTrue(res == 0, res)

	def test_run_jabs_parallel(self):
		''' Run the same test backup with parallel=True; result must be identical '''
		DEST = '/tmp/jabs-backup'

		if not os.path.exists(DEST):
			os.mkdir(DEST)

		j = jabs.sync.Jabs()
		j.debug = 1
		res = j.run('jabs.cfg', '/tmp/', pidFilePath=self.PID_FILE, onlySets=['Test'], force=True, parallel=True)
		self.assertTrue(res == 0, res)


if __name__ == '__main__':
	unittest.main()
