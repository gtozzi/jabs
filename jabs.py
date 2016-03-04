#! /usr/bin/env python3
# kate: space-indent off; tab-indent on;

""" @package docstring
JABS - Just Another Backup Script

This is a simple and powerful rsync-based backup script.

Main features:
- Rsync-based: Bandwidth is optimized during transfers
- Automatic "Hanoi" backup set rotation
- Incremental "complete" backups using hard links
- E-Mail notifications on completion

Installation:
- This script is supposed to run as root
- Copy jabs.cfg in /etc/jabs/jabs.cfg and customize it

Usage:
Place a cron entry like this one:

MAILTO="your-email-address"

*/5 * * * *     root    /usr/local/bin/jabs.py -b -q

The script will end silently when has nothing to do.
Where there is a "soft" error or when a backup is completed, you'll receive
an email from the script
Where there is an "hard" error, you'll receive an email from Cron Daemon (so
make sure cron is able to send emails)

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
import re
import sys
import psutil
import socket
import getpass
import logging
import datetime
import subprocess
import collections
import configparser


NAME = 'jabs'
VERSION = '2.0-pre-aplha'
DESCRIPTION = 'Just Another Backup Script'
DEFAULT_CONFIG = {
	'configfile': '/etc/jabs/jabs.cfg',
	'pidfile': '/var/run/jabs.pid',
	'cachedir': '/var/cache/jabs',
}


class ConfigurationError(Exception):
	''' Simple exception raised when there is a configuration error '''
	pass

class CannotLockError(Exception):
	''' Exception raised when lock couldn't be acquired '''
	pass


class BackupSet:
	''' Represents a single backup set '''

	# Time interval representing whole day
	ALLDAY = ( datetime.time(0,0,0), datetime.time(23,59,59) )

	def __init__(self, name, config):
		''' Reads the config section and inits the backup set
		@throws ConfigurationError
		'''
		self.name = name

		## List of folders to be backed up
		self.backupList = config.getList('BackupList')
		## List of folders to be deleted from destination
		self.deleteList = config.getList('DELETELIST', [])
		## Niceness for IO
		self.ioNice = config.getInt('IONICE', 0)
		## Niceness for the process
		self.nice = config.getInt('NICE', 0)
		## Rsync command line options
		self.rsyncOpts = config.getList('RSYNC_OPTS')
		## Source folder/path to read from
		self.src = config.getPath('SRC')
		## Destination folder/path to backup to
		self.dst = config.getPath('DST')
		## Sleep tinterval in seconds between every dir
		self.sleep = config.getInt('SLEEP', 0)
		## Number of sets to use for Hanoi rotation (0=disabled)
		self.hanoi = config.getInt('HANOI', 0)
		## First day to base Hanoi rotation on
		self.hanoiDay = config.getDate('HANOIDAY', NoDefault if self.hanoi else None)
		## Wehther to use hard linking
		self.hardLink = config.getBool('HARDLINK', False)
		## Wehther to check if destination folder already exists before backing up
		self.checkdst = config.getBool('CHECKDST', True)
		## Prefix/suffix separator when using hanoi
		self.sep = config.getStr('SEP', '.')
		## Priotity for running this set (higher means lower priority)
		self.pri = config.getInt('PRI', 0)
		## Name of date file to include in backup dest dir
		self.dateFile = config.getStr('DATEFILE', None)
		## Minimum time interval between two backups
		self.interval = config.getInterval('INTERVAL', None)
		## Ping destination before backup, run it only if succesful
		ping = config.getBool('PING', False)
		## Valid time range for starting this backup
		self.runTime = config.getTimeRange('RUNTIME', self.ALLDAY)
		## List of email address to notify about backup status
		self.mailto = config.getList('MAILTO', None)
		## Sender address for the notification email
		self.mailfrom = config.getStr('MAILFROM', getpass.getuser()+'@'+socket.gethostname())
		## Mount the given location before executing the backup
		self.mount = config.getStr('MOUNT', None)
		## UnMount the given location after executing the backup
		self.umount = config.getStr('UMOUNT', None)
		## Completely disable this set
		self.disabled = config.getBool('DISABLED', False)
		## List of commands/scripts to execute before running this backup
		self.pre = config.getStr('PRE', [], True)
		## Whether to skip the backup if a pre-task fails
		self.skipOnPreError = config.getBool('SKIPONPREERROR', True)

		# Validate the ping setting, replace it with the hostname
		if not ping:
			self.ping = None
		else:
			shost = self.src.getHost()
			dhost = self.dst.getHost()

			if shost and dhost:
				raise ConfigurationError('Both src and dst are remote and ping enabled. '
						'This is not supported')
			elif shost:
				self.ping = shost
			elif dhost:
				self.ping = dhost
			else:
				raise ConfigurationError('Ping is enabled but both src and dst are local. '
						'I do not know what to ping')


	def __str__(self):
		desc = 'Backup Set "{}":'.format(self.name)
		exclude = ('name')
		for k, v in sorted(self.__dict__.items()):
			desc += "\n- {}: {}".format(k, v)
		return desc


class Jabs:
	''' This is the main Jabs class: it takes care of starting jobs when needed '''

	GLOBAL_CONFIG_SECTION = 'Global'

	def __init__(self, force=False, batch=False, safe=False,
			configFile=DEFAULT_CONFIG['configfile'],
			pidFile=DEFAULT_CONFIG['pidfile'],
			cacheDir=DEFAULT_CONFIG['cachedir']):

		self.pidFile = PidFile(pidFile)
		self.cacheDir = cacheDir

		# Init logger
		self.log = logging.getLogger('jabs')

		# Reads configuration
		self.log.debug('Reading config from file %s', configFile)
		if not os.path.isfile(configFile):
			raise ConfigurationError('Config file "{}" does not exist'.format(configFile))
		self.config = configparser.ConfigParser()
		if configFile not in self.config.read(configFile):
			raise ConfigurationError('Couldn\'t load config file "{}"'.format(configFile))

		# Checks for correct config version
		try:
			if self.config.getint(self.GLOBAL_CONFIG_SECTION,'ConfigVersion') != 2:
				raise KeyError()
		except KeyError:
			raise ConfigurationError("{} section must define a ConfigVersion=2 parameter".format(self.GLOBAL_CONFIG_SECTION))

		# Loads sets (except disabled ones)
		sets = collections.OrderedDict()
		for name in self.config.sections():
			if name == self.GLOBAL_CONFIG_SECTION or name == 'DEFAULT':
				continue

			if name in sets.keys():
				raise ConfigurationError('Duplicate definition for set "{}"'.format(name))

			s = BackupSet(name, ConfigSection(self.config, name))
			self.log.debug('Loaded set: {}'.format(s))
			sets[s.name] = s

		# Sort sets by priority
		self.sets = collections.OrderedDict(sorted(sets.items(), key=lambda i: i[1].pri))

		# Rough validtaion on cacheDir
		if not os.path.exists(self.cacheDir):
			self.log.warning('Cache directory "%s" does not exist, creating it', self.cacheDir)
			os.mkdir(self.cacheDir)
		if not os.path.isdir(self.cacheDir):
			raise ConfigurationError('Cache directory "%s" is not a folder', self.cacheDir)
		if not os.access(self.cacheDir, os.R_OK | os.W_OK | os.X_OK):
			raise ConfigurationError('Cache directory "{}" is not accessible'.format(self.cacheDir))

	def __enter__(self):
		''' Try to acquire the lock '''
		self.acquireLock()
		return self

	def __exit__(self, exc_type, exc_value, traceback):
		self.releaseLock()

	def run(self, force=False, sets=None):
		''' Do the backups
		@param force if true, always execute sets regardless of the time
		@param sets list of the names of the sets to be run, None means all
		'''
		self.started = datetime.datetime.now()
		with self:
			runSets = self.__listSetsToRun(force, sets)
			if not runSets:
				self.log.info('Nothing to do')
				return

	def __listSetsToRun(self, force=False, sets=None):
		''' Returns list of sets to run
		@param force if true, always execute sets regardless of the time
		@param sets list of the names of the sets to be run, None means all
		'''
		if sets:
			for s in sets:
				if s not in self.sets.keys():
					raise RuntimeError('Unknown set: "{}"'.format(s))

		# Determine which sets have to be run
		if sets is None:
			activeSets = self.sets.values()
		else:
			activeSets = filter(lambda i: i.name in sets, self.sets.values())

		self.log.info('Considering sets: ' + ', '.join(['"{}"'.format(x.name) for x in activeSets]))

		if force:
			self.log.warning('Force option active: ignoring time constraints')

		runSets = []
		for bs in activeSets:
			# Check which sets should run now

			if bs.disabled:
				# Check if set is disabled
				self.log.debug('Skipping set "%s" because it\'s disabled', bs.name)
				continue

			if not force and ( bs.runTime[0] > self.started.time() or
					bs.runTime[1] < self.started.time() ):
				self.log.debug('Skipping set "%s" because out of runtime (%s-%s)',
						bs.name, bs.runTime[0].isoformat(), bs.runTime[1].isoformat())
				continue

			if not force and bs.interval and bs.interval > datetime.timedelta(seconds=0):
				# Check if enough time has passed since last run
				self.log.debug('Set "%s" runs every %s', bs.name, bs.interval)

				cacheFile = os.path.join(self.cacheDir, bs.name.replace(os.sep,'_'))
				if not os.path.exists(cacheFile):
					lastDone = datetime.datetime.fromtimestamp(0)
				else:
					with open(cacheFile, 'rt', newline=None) as cf:
						try:
							ts = int(cf.readline().strip())
							lastDone = datetime.datetime.fromtimestamp(ts)
						except ValueError:
							self.log.warning('Last backup timestamp for "%s" '
									'is corrupted. Assuming 01-01-1970', bs.name)
							lastDone = datetime.datetime.fromtimestamp(0)

				self.log.debug('Last "%s" run: %s', bs.name, lastDone)

				if lastDone + bs.interval > self.started:
					self.log.debug('Skipping set "%s" because interval has not '
							'been reached (%s still remains )', bs.name,
							lastDone + s.interval - self.started)
					continue

			if bs.ping is not None:
				# Perform the ping check
				self.log.debug('Pinging host "%s"', bs.ping)

			# Finally append the set to the run queue if all tests passed
			runSets.append(bs)

		self.log.info('Will run sets: ' + ', '.join(['"{}"'.format(x.name) for x in runSets]))
		return runSets

	def acquireLock(self):
		''' Try to acquire the lock
		@throws CannotLockError
		'''
		self.log.debug('Acquiring lock')
		if not self.pidFile.lock():
			raise CannotLockError('Another instance is already running')

	def releaseLock(self):
		''' Release the lock, if any '''
		self.log.debug('Releasing lock')
		self.pidFile.unlock()


class NoDefault:
	''' Constant used in ConfigSection when there is no default '''
	pass


class ConfigSection:
	''' Represents a single section in config file (configparser proxy) '''

	LIST_SEP = ','

	def __init__(self, config, name):
		'''
		@param config The underlying configparser object
		@param name Name of the section/set
		'''
		self.config = config
		self.name = name

	def __get(self, name, default=NoDefault, multi=False, method='get'):
		"""
			Get an option value from this section using given method
			If value is missing, and a default value is passed, return default.
			NoDefault is used in place of None to allow passing None as default value.

			If multi is set to true, looks for multiple names in the format
			name_XX and returns a list of the requested items, sorted by XX
		"""

		if multi:
			optnames = []
			for option in self.config.options(self.name):
				if option.lower().startswith(name.lower()+'_'):
					optnames.append(option)
			if not optnames:
				if default is NoDefault:
					raise ConfigurationError('Missing option {} for set "{}"'.format(
							name, self.name))
				else:
					return default

			optnames.sort()
			ret = []
			for option in optnames:
				ret.append(self.__get(name, method=method))

			return ret

		try:
			ret = getattr(self.config, method)(self.name, name)
		except configparser.NoOptionError:
			if default is NoDefault:
				raise ConfigurationError('Missing option {} for set "{}"'.format(
						name, self.name))
			else:
				return default

		if isinstance(ret, str):
			ret = ret.strip()
		return ret

	def getStr(self, name, default=NoDefault, multi=False):
		return self.__get(name, default, multi, 'get')

	def getInt(self, name, default=NoDefault, multi=False):
		return self.__get(name, default, multi, 'getint')

	def getBool(self, name, default=NoDefault, multi=False):
		return self.__get(name, default, multi, 'getboolean')

	def getPath(self, name, default=NoDefault, multi=False):
		ret = self.__get(name, default, multi)
		if ret is default:
			return ret
		return Path(ret)

	def getList(self, name, default=NoDefault, multi=False):
		ret = self.__get(name, default, multi)
		if ret is default:
			return ret
		return [ x.strip() for x in ret.strip().split(self.LIST_SEP) ]

	def getDate(self, name, default=NoDefault, multi=False):
		ret = self.__get(name, default, multi)
		if ret is default:
			return ret
		try:
			return datetime.datetime.strptime(ret,r'%Y-%m-%d').date()
		except:
			raise ConfigurationError('Invalid date "{}" for option {} '
					'in set "{}"'.format(ret, name, self.name))

	def getTimeRange(self, name, default=NoDefault, multi=False):
		ret = self.__get(name, default, multi)
		if ret is default:
			return ret
		try:
			parts = ret.split('-')
			if len(parts) != 2:
				raise ValueError('Must have exactly two parts')
			ptime = lambda i: datetime.datetime.strptime(i,r'%H:%M:%S').time()
			start = ptime(parts[0])
			end = ptime(parts[1])
			return (start, end)
		except:
			raise ConfigurationError('Invalid time range "{}" for option {} '
					'in set "{}"'.format(ret, name, self.name))

	def getInterval(self, name, default=NoDefault, multi=False):
		ret = self.__get(name, default, multi)
		if ret is default:
			return ret
		s, m, h, d = (0, 0, 0, 0)
		try:
			for i in ret.split():
				if i[-1] == 's':
					if s:
						raise ValueError('Duplicated seconds')
					s = int(i[:-1])
				elif i[-1] == 'm':
					if m:
						raise ValueError('Duplicated minutes')
					m = int(i[:-1])
				elif i[-1] == 'h':
					if h:
						raise ValueError('Duplicated hours')
					h = int(i[:-1])
				elif i[-1] == 'd':
					if d:
						raise ValueError('Duplicated days')
					d = int(i[:-1])
				else:
					raise ValueError('Unknown specifier "{}"'.format(i[-1]))

			return datetime.timedelta(days=d,hours=h,minutes=m,seconds=s)
		except:
			raise ConfigurationError('Invalid interval "{}" for option {} '
					'in set "{}"'.format(ret, name, self.name))


class Path:
	''' Represents an rsync path '''

	REMOTE_RE = re.compile('(.*@.*):{1,2}(.*)')

	def __init__(self, path):
		self.path = path

	def __str__(self):
		return self.path

	def getHost(self):
		''' Returns the host part, or None if this is not remote '''
		match = self.REMOTE_RE.match(self.path)
		if not match:
			return None
		return match.group(1).split('@')[1]


class PidFile:
	''' Class for handling a PID file '''

	def __init__(self, path):
		self.path = path
		self.log = logging.getLogger('pid')
		self.locked = False

	def lock(self):
		''' Try to acquire the lock, returns true on success or if already locked '''
		if self.locked:
			self.log.debug('Lock already acquired')
			return True

		# Open the file for rw or create a new one if missing
		if os.path.exists(self.path):
			mode = 'r+t'
		else:
			mode = 'wt'

		with open(self.path, mode, newline=None) as pidFile:
			curPid = os.getpid()
			pid = None

			if mode.startswith('r'):
				try:
					pid = int(pidFile.readline().strip())
				except ValueError:
					pass

			if pid is not None:
				# Found a pid stored in the pid file, check if its still running
				if psutil.pid_exists(pid):
					return False

			pidFile.seek(0)
			pidFile.truncate()
			print("{}".format(curPid), file=pidFile)

		self.locked = True
		return True

	def unlock(self):
		''' Release the lock, if any '''
		if not self.locked:
			self.log.debug('Lock already released')
			return False

		os.remove(self.path)
		self.locked = False
		return True


if __name__ == '__main__':
	import argparse
	import traceback

	# Parses the command line
	parser = argparse.ArgumentParser(
		prog = NAME,
		description = DESCRIPTION,
		formatter_class = argparse.ArgumentDefaultsHelpFormatter
	)

	parser.add_argument("-c", "--config", dest="configfile",
		default=DEFAULT_CONFIG['configfile'], help="Config file name")
	parser.add_argument("-p", "--pid", dest="pidfile",
		default=DEFAULT_CONFIG['pidfile'], help="PID file name")
	parser.add_argument("-a", "--cachedir", dest="cachedir",
		default=DEFAULT_CONFIG['cachedir'], help="Cache directory")

	parser.add_argument("-f", "--force", dest="force", action="store_true",
		help="ignore time constraints: will always run sets at any time")
	parser.add_argument("-b", "--batch", dest="batch", action="store_true",
		help="batch mode: exit silently if script is already running")
	parser.add_argument("-s", "--safe", dest="safe", action="store_true",
		help="safe mode: just print what will do, don't change anything")

	group = parser.add_mutually_exclusive_group()
	group.add_argument("-v", "--verbose", dest="verbose", action='count', default=0,
		help="Increase verbosity, can be repeat multiple times")
	group.add_argument("-q", "--quiet", dest="quiet", action="store_true",
		help="Suppress all non-error output")

	parser.add_argument("set", nargs='*',
		help="Name of a set to run (all if missing)")

	args = parser.parse_args()

	if args.verbose:
		verbosity = logging.DEBUG
	elif args.quiet:
		verbosity = logging.WARNING
	else:
		verbosity = logging.INFO

	# Init logging
	logging.basicConfig(
		level = verbosity,
		format = '%(asctime)s %(name)s.%(levelname)s: %(message)s'
	)

	try:
		with Jabs(
			configFile = args.configfile,
			pidFile = args.pidfile,
			cacheDir = args.cachedir,
			force = args.force,
			batch = args.batch,
			safe = args.safe
		) as jabs:
			jabs.run(
				args.force,
				args.set if len(args.set) else None
			)
	except ConfigurationError as e:
		# Invalid configuration
		print("CONFIGURATION ERROR: {}".format(e))
		sys.exit(2)
	except CannotLockError as e:
		# Instance already running
		if not args.batch:
			print("LOCK ERROR: {}".format(e))
			sys.exit(3)
	except Exception as e:
		# A generic error
		if verbosity >= logging.DEBUG:
			traceback.print_exc()
		print("ERROR: {}".format(e))
		sys.exit(1)

	sys.exit(0)


