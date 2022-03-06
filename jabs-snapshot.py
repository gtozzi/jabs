#!/usr/bin/env python3

""" @package docstring
JABS - Just Another Backup Script - Snapshotter tool

Monitors a folder for JABS backups and takes a snapshot according to
configuration. Actually only works on BTRFS.

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

import re
import os
import sys
import logging
import datetime
import dateutil.parser
import traceback
import configparser
import subprocess

# Should stay in sunc with jabs
VERSION = "jabs-snapshot v.1.6"

# Default values for configuration
CONFIG_DEFAULTS = {
	'linklast': True,
	'linklastname': 'lastsnap',
}


class BackupFolder:
	TIMESTAMP_FILE = 'backup-timestamp'

	''' Represents a backup folder '''
	def __init__(self, root, name):
		self.root = root
		self.name = name
		self.path = os.path.join(self.root, self.name)
		assert os.path.isdir(self.path)
		self.tpath = os.path.join(self.path, self.TIMESTAMP_FILE)
		if not os.path.isfile(self.tpath):
			raise ValueError('"{}" is not a timestamp file'.format(self.tpath))
		self.readTimestamp()

	def __str__(self):
		return "{} of {}".format(self.name, self.timestamp.strftime(r'%Y-%m-%d'))

	def readTimestamp(self):
		''' Reads timestamp from backup-timestamp file '''
		with open(self.tpath, 'rt') as f:
			traw = f.read()

		self.timestamp = dateutil.parser.parse(traw)


class Snapshotter:
	''' Monitors a path for new backups and snapshot them '''

	def __init__(self, id, root, cur, hanoiDay, sets,
			lnLast=CONFIG_DEFAULTS['linklast'], lnLastName=CONFIG_DEFAULTS['linklastname']):
		''' Inits the thread
		@param id string: This threads's unique ID
		@param root string: The root backup path
		@param cur string: Name of the current (last) backup fodler
		@param hanoiDay date: First day for hanoi-based rotation calculations
		@param sets int: Number of hanoi sets to use
		@param lnLast boolean: Wether to create a symlink to most recent snapshot
		@param lnLastName string: Name for the created symlink
		'''
		self.id = id
		self.root = os.path.abspath(root)
		self.curName = cur
		self.hanoiDay = hanoiDay
		self.sets = sets
		self.lnLast = lnLast
		self.lnLastName = lnLastName
		self._log = logging.getLogger('snap-{}'.format(id))

		self._log.debug('Initing checker for root "{}" cur "{}"'.format(self.root, self.curName))

		if not isinstance(self.hanoiDay, datetime.date):
			raise ValueError('Hanoiday must be a date')

		if sets <= 1:
			raise ValueError('Sets must be at least 1')

		if not os.path.isdir(self.root):
			raise ValueError('Invalid root dir {}'.format(self.root))

	def run(self):
		''' Scans the folders and takes a snapshot if needed '''

		# Scans the folders
		backupFolders = {}
		curFolder = None
		for f in os.listdir(self.root):
			if not os.path.isdir(os.path.join(self.root, f)):
				continue
			if os.path.islink(os.path.join(self.root, f)):
				continue

			bf = BackupFolder(self.root, f)
			if f == self.curName:
				curFolder = bf
				self._log.debug('Found cur folder "%s"', bf)
			else:
				backupFolders[f] = bf
				self._log.debug('Found folder "%s"', bf)

		if not curFolder:
			raise RuntimeError('Cur folder "{}" not found'.format(self.curName))

		# Look for a snapshot of cur
		for folder in backupFolders.values():
			if folder.timestamp == curFolder.timestamp:
				self._log.info('Cur snapshot is folder "%s", nothing to do', folder)
				return

		hday, hsuf = self.calcHanoi(self.sets, self.hanoiDay, curFolder.timestamp.date())

		self._log.info('No cur snapshot, taking it. Today is hanoi day {}, using letter {}'.format(hday, hsuf))
		snapPath = os.path.join(self.root, hsuf)

		# Delete snapshot if existing
		if os.path.exists(snapPath):
			self._log.info('Snapshot "{}" already exists, deleting it'.format(snapPath))

			cmd = ('btrfs', 'subvolume', 'delete', '-c', '-v', snapPath)
			self.btrfsSub(cmd)

		assert not os.path.exists(snapPath)

		# Create new snapshot
		cmd = ('btrfs', 'subvolume', 'snapshot', '-r', curFolder.path, snapPath)
		self.btrfsSub(cmd)

		if self.lnLast:
			self.symlinkSnapshot(snapPath)

	def symlinkSnapshot(self, snapPath):
		''' Create a symlink for the last snapshot '''

		lnPath = os.path.join(self.root, self.lnLastName)

		# Delete symlink if exists
		if os.path.exists(lnPath):
			if not os.path.islink(lnPath):
				# This is not a real symlink! Not messing with it
				self._log.error('Not overwriting "%s" because it is not a symlink. Unable to create symlink.', lnPath)
				return False

			os.remove(lnPath)

		# Create symlink to last snaposhot
		os.symlink(snapPath, lnPath)
		self._log.info('Updated symlink "%s" to last snapshot', lnPath)
		return True

	def calcHanoi(self, sets, firstDay, today):
		''' Calculate hanoi day and suffix to use
		@param sets int: Number of sets
		@param firstDay date: First hanoi day
		@param today date: Hanoi today's date
		@todo Move to a shared library
		@return ( int: day num, str: one letter suffix )
		'''
		if sets < 1:
			raise ValueError('Sets must be at least 1')
		if not isinstance(firstDay, datetime.date):
			raise ValueError('firstDay must be a date')
		if not isinstance(today, datetime.date):
			raise ValueError('today must be a date')

		day = (today - firstDay).days + 1
		assert day >= 1
		suf = None
		i = sets
		while i >= 0:
			if day % 2 ** i == 0:
				suf = chr(i+65)
				break
			i -= 1
		assert suf is not None

		return ( day, suf )

	def btrfsSub(self, cmd):
		''' Execute a btrfs subprocess command '''
		ret = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
		if ret.stderr:
			self._log.info('BTRFS subprocess stderr:\n%s', ret.stderr.decode())
		if ret.stdout:
			self._log.debug('BTRFS subprocess stdout:\n%s', ret.stdout.decode())
		if ret.returncode != 0:
			raise RuntimeError('BTRFS subprocess returned non-zero')


class Main:
	''' The main snapshotter program '''

	def __init__(self, configPath):
		''' Reads the config '''
		self._log = logging.getLogger('main')

		if not os.path.exists(configPath) or not os.path.isfile(configPath):
			raise ValueError('configPath must be a file')

		self.config = configparser.ConfigParser(defaults=CONFIG_DEFAULTS)
		self.config.read(configPath)

	def run(self):
		''' Runs the snapshotter '''
		dateRe = re.compile(r'^([0-9]{4})-([0-9]{2})-([0-9]{2})$')

		# Checks sets and snapshots them
		for section in self.config:
			if section == 'DEFAULT':
				continue

			self._log.debug('Creating snapshotter for set "{}"'.format(section))

			root = self.config.get(section, 'path')
			cur = self.config.get(section, 'curfolder')
			rawhd = self.config.get(section, 'hanoiDay')
			sets = self.config.getint(section, 'hanoi')
			linkLast = self.config.getboolean(section, 'linklast')
			linkLastName = self.config.get(section, 'linklastname')
			m = dateRe.match(rawhd)
			if not m:
				raise ValueError('Invalid date "{}"'.format(rawhd))
			hanoiDay = datetime.date(year=int(m.group(1)), month=int(m.group(2)), day=int(m.group(3)))
			if sets < 1:
				raise ValueError('Invalid hanoi sets number')

			Snapshotter(section, root, cur, hanoiDay, sets, linkLast, linkLastName).run()


if __name__ == '__main__':
	import argparse

	parser = argparse.ArgumentParser()
	parser.add_argument('configFile', help="configuration file path")
	parser.add_argument('--version', action='version', version=VERSION)
	parser.add_argument('-v', '--verbose', action='store_true', help="more verbose output")
	parser.add_argument('-q', '--quiet', action='store_true', help="suppress non-essential output")
	args = parser.parse_args()

	if args.verbose:
		level = logging.DEBUG
	elif args.quiet:
		level = logging.WARNING
	else:
		level = logging.INFO
	format = r"%(name)s: %(message)s"
	logging.basicConfig(level=level, format=format)

	try:
		main = Main(args.configFile).run()
	except Exception as e:
		logging.critical(traceback.format_exc())
		print('ERROR: {}'.format(e))
		sys.exit(1)

	sys.exit(0)
