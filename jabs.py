#!/usr/bin/env python3
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

import os, sys, socket, subprocess, threading, gzip, tempfile, getpass, shutil
from stat import S_ISDIR, S_ISLNK, ST_MODE
import argparse
import configparser
from string import Template
from time import sleep, mktime
from datetime import datetime, date, timedelta, time
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart

# Default configuration
CONFIGFILE = "/etc/jabs/jabs.cfg"
VERSION = "jabs v.1.7.2"
CACHEDIR = "/var/cache/jabs"
MINPYTHON = (3, 5)

# Useful regexp
rpat = re.compile('{setname}')
rdir = re.compile('{dirname}')
risremote = re.compile(r'(.*@.*):{1,2}(.*)')
rlsparser = re.compile(r'^([^\s]+)\s+([0-9]+)\s+([^\s]+)\s+([^\s]+)\s+([0-9]+)\s+([0-9]{4}-[0-9]{2}-[0-9]{2}\s[0-9]{2}:[0-9]{2})\s+(.+)$')

# ------------ FUNCTIONS AND CLASSES ----------------------


def wrapper(func, args):
	"""
		Takes a list of arguments and passes them as positional arguments to
		func
	"""
	return func(*args)


class MyLogger:
	""" Custom logger class

		Assumed debug levels:
		-2: ERROR message
		-1: WARNING message
		0: NORMAL message
		1: SUPERFLUOUS message
		2: DEBUG message
	"""
	def __init__(self):
		#List of lists: 0: the string, 1=debug level
		self.logs = []
		#With print only messages with this debug level or lower
		self.debuglvl = 0

	def setdebuglvl(self, lvl):
		self.debuglvl = lvl

	def add(self,*args,**kwargs):
		"""
			Adds a line to self.logs and eventually prints it
			Arguments are passed like in the print() builtin
			function. An optional 'lvl' named argument may be
			specified to set debug level (default: 0)
			Also, an optional 'noprint' parameter may be set
			to true to avoid printing that message regardless
			of debug level
		"""
		if 'lvl' in kwargs:
			lvl = kwargs['lvl']
		else:
			lvl = 0
		outstr = ''
		for arg in args:
			outstr += str(arg) + " "
		if len(outstr):
			outstr = outstr[:-1]
		if lvl <= self.debuglvl and not ( 'noprint' in kwargs and kwargs['noprint'] ):
			print(outstr)
		self.logs.append([outstr, lvl])

	def getstr(self,lvl=0):
		""" Returns the buffered log as string """
		retstr = ''
		for l in self.logs:
			if l[1] <= lvl:
				retstr += l[0] + "\n"
		return retstr


class JabsConfig(configparser.ConfigParser):
	"""
		Custom configuration parser
	"""
	BASE_SECTION = 'Global'
	LIST_SEP = ','

	def __getInType(self, name, section, vtype):
		""" Internal function called by __get """
		if vtype == 'str':
			return configparser.ConfigParser.get(self, section, name).strip()
		elif vtype == 'int':
			return configparser.ConfigParser.getint(self, section, name)
		elif vtype == 'bool':
			return configparser.ConfigParser.getboolean(self, section, name)
		elif vtype == 'list':
			return [ x.strip() for x in configparser.ConfigParser.get(self, section, name).strip().split(self.LIST_SEP) ]
		elif vtype == 'date':
			return wrapper(date, map(int, configparser.ConfigParser.get(self, section, name).strip().split('-',3)))
		elif vtype == 'interval':
			string = configparser.ConfigParser.get(self, section, name).strip()
			d, h, m, s = (0 for x in range(4))
			if len(string):
				for i in string.split():
					if i[-1] == 's':
						s = int(i[:-1])
					elif i[-1] == 'm':
						m = int(i[:-1])
					elif i[-1] == 'h':
						h = int(i[:-1])
					elif i[-1] == 'd':
						d = int(i[:-1])
			return timedelta(days=d,hours=h,minutes=m,seconds=s)
		elif vtype == 'timerange':
			return tuple(map(lambda s: wrapper(time,map(int,s.split(':'))), configparser.ConfigParser.get(self, section, name).strip().split('-')))
		else:
			raise RuntimeError("Unvalid vtype %s" % vtype)

	def __get(self, name, section=NotImplemented, default=NotImplemented, vtype='str', multi=False):
		"""
			Get an option value for the named section.
			If section is missing, uses the Global section.
			If value is missing, and a default value is passed, return default.
			NotImplemented is used in place of None to allow passing None as default value.
			All values are stripped before bein' returned.
			Converts the value to given vtype: string, int, bool, list, date

			If multi is set to true, looks for multiple names in the format
			name_XX and returns a list of items of the requested vtype merging
			all names together
		"""
		if section is NotImplemented:
			section = self.BASE_SECTION

		# Look for all the keys named like the one specified
		if multi:
			retlist = []
			multi_keys = {}
			for sec in (self.BASE_SECTION, section):
				for opt in self.options(sec):
					if opt[:len(name)] == name.lower():
						multi_keys[opt.upper()] = sec

			def sort_key(i):
				s = i.split('_')
				try:
					return int(s[1])
				except IndexError:
					return 0

			for k in sorted(multi_keys.keys(), key=sort_key):
				retlist.append(self.__getInType(k, multi_keys[k], vtype))

			return retlist

		# Standard lookup
		try:
			return self.__getInType(name, section, vtype)
		except(configparser.NoSectionError, configparser.NoOptionError):
			try:
				return self.__getInType(name, self.BASE_SECTION, vtype)
			except configparser.NoOptionError as e:
				if default is not NotImplemented:
					return default
				else:
					raise ValueError("Error parsing config file: option %s not found." % name)

	def getstr(self, name, section=NotImplemented, default=NotImplemented, multi=False):
		""" Same as __get, returning a string """
		return self.__get(name, section, default, 'str', multi)

	def getint(self, name, section=NotImplemented, default=NotImplemented, multi=False):
		""" Same as __get, but also formats value as int """
		return self.__get(name, section, default, 'int', multi)

	def getlist(self, name, section=NotImplemented, default=NotImplemented, multi=False):
		"""
			Same as __get, but returns a list from a comma-separated string.
			Also strips every element of the list.
		"""
		return self.__get(name, section, default, 'list', multi)

	def getfloat(self, name, section=NotImplemented, default=NotImplemented, multi=False):
		raise NotImplementedError('Method not implemented')

	def getboolean(self, name, section=NotImplemented, default=NotImplemented, multi=False):
		""" Same as __get, but also formats value as boolean """
		return self.__get(name, section, default, 'bool', multi)

	def getdate(self, name, section=NotImplemented, default=NotImplemented, multi=False):
		""" Same as __get, but returns a date from the format YYYY-MM-DD """
		return self.__get(name, section, default, 'date', multi)

	def getinterval(self, name, section=NotImplemented, default=NotImplemented, multi=False):
		""" Same as __get, but returns a timedelta from a string interval """
		return self.__get(name, section, default, 'interval', multi)

	def gettimerange(self, name, section=NotImplemented, default=NotImplemented, multi=False):
		"""
			Same as __get, but returns a list with two time objects representing
			a time range form a string in the format hh:mm:ss-hh:mm:ss
		"""
		return self.__get(name, section, default, 'timerange', multi)


class SubProcessCommThread(threading.Thread):
	""" Base subprocess communication thread class """

	def __init__(self, sp, stream):
		"""
			@param sp: The POpen subproces object
			@param stream: The sp's stream to read from
		"""
		assert stream == sp.stderr or stream == sp.stdout
		super(SubProcessCommThread, self).__init__()
		self.daemon = True
		self.sp = sp
		self._stream = stream

	def run(self):
		while True:
			text = self._stream.readline()
			if text == b'':
				break
			self._processText(text)


class SubProcessCommStdoutThread(SubProcessCommThread):
	""" Handles stdout communication with the rsync/tar subprocess """

	def __init__(self, sp, logh):
		"""
			@param logh: The logfile handle where to send stdout
		"""
		super(SubProcessCommStdoutThread, self).__init__(sp, sp.stdout)
		self.logh = logh

	def _processText(self, text):
		self.logh.write(text)
		self.logh.flush()


class SubProcessCommStderrThread(SubProcessCommThread):
	""" Handles stderr communication with the rsync/tar subprocess """

	def __init__(self, sp):
		super(SubProcessCommStderrThread, self).__init__(sp, sp.stderr)
		self.output = b''

	def _processText(self, text):
		self.output += text


class BackupSet:
	"""
		Backup set class
	"""

	def __init__(self, name, config):
		"""
			Creates a new Backup Set object

			@param name string: the name of this set
			@param object config: the JabsConfig object
		"""
		self.name = name

		self.program = config.getstr('PROGRAM', self.name, 'rsync')
		self.backuplist = config.getlist('BACKUPLIST', self.name)
		self.deletelist = config.getlist('DELETELIST', self.name, [])
		self.ionice = config.getint('IONICE', self.name, 0)
		self.nice = config.getint('NICE', self.name, 0)
		self.rsync_opts = config.getlist('RSYNC_OPTS', self.name, [])
		self.rclone_opts = config.getlist('RCLONE_OPTS', self.name, [])
		self.src = config.getstr('SRC', self.name)
		self.dst = config.getstr('DST', self.name)
		self.sleep = config.getint('SLEEP', self.name, 0)
		self.hanoi = config.getint('HANOI', self.name, 0)
		self.hanoiday = config.getdate('HANOIDAY', self.name, date(1970,1,1))
		self.hardlink = config.getboolean('HARDLINK', self.name, False)
		self.checkdst = config.getboolean('CHECKDST', self.name, False)
		self.sep = config.getstr('SEP', self.name, '.')
		self.pri = config.getint('PRI', self.name, 0)
		self.datefile = config.getstr('DATEFILE', self.name, None)
		self.interval = config.getinterval('INTERVAL', self.name, None)
		self.ping = config.getboolean('PING', self.name, False)
		self.runtime = config.gettimerange('RUNTIME', self.name, [time(0,0,0),time(23,59,59)])
		self.mailto = config.getlist('MAILTO', self.name, None)
		self.mailfrom = config.getstr('MAILFROM', self.name, getpass.getuser() + '@' + socket.getfqdn())
		self.mount = config.getstr('MOUNT', self.name, None)
		self.umount = config.getstr('UMOUNT', self.name, None)
		self.disabled = config.getboolean('DISABLED', self.name, False)
		self.pre = config.getstr('PRE', self.name, None, True)
		self.skiponpreerror = config.getboolean('SKIPONPREERROR', self.name, None, False)
		self.smtphost = config.getstr('SMTPHOST', self.name, None)
		self.smtpuser = config.getstr('SMTPUSER', self.name, None)
		self.smtppass = config.getstr('SMTPPASS', self.name, None)
		self.compresslog = config.getstr('COMPRESSLOG', self.name, True)

		self.remsrc = risremote.match(self.src)
		self.remdst = risremote.match(self.dst)


# ----------------------------------------------------------

# ------------ INIT ---------------------------------------

class Jabs:
	''' Main JABS class '''

	def __init__(self):
		#TODO: Temporary debug level setting, will use logging instead
		self.debug = 0

		if sys.version_info[0] < MINPYTHON[0] or \
				sys.version_info[0] == MINPYTHON[0] and sys.version_info[1] < MINPYTHON[1]:
			raise RuntimeError('At least python {}.{} is required to run this script'.format(*MINPYTHON))

	def runFromCommandLine(self) -> int:
		''' Parses the command line and runs JABS
		@return int exit status, 0 on success
		'''

		parser = argparse.ArgumentParser(description=VERSION)
		parser.add_argument("-c", "--config", dest="configfile", default=CONFIGFILE,
			help="Config file name")
		parser.add_argument("-a", "--cachedir", default=CACHEDIR,
			help="Cache directory")
		parser.add_argument("--pidfile",
			help="PID file path, overrides config if given")
		parser.add_argument("-v", "--verbose", action="store_true",
			help="Increase output verbosity (overrides -d)")
		parser.add_argument("-q", "--quiet", action="store_true",
			help="suppress all non-error output")
		parser.add_argument("-f", "--force", action="store_true",
			help="ignore time constraints: will always run sets at any time")
		parser.add_argument("-b", "--batch", action="store_true",
			help="batch mode: exit silently if script is already running")
		parser.add_argument("-s", "--safe", action="store_true",
			help="safe mode: just print what will do, don't change anything")
		parser.add_argument("sets", nargs="*",
			help="list of sets to run; if omited, will run all")

		args = parser.parse_args()

		# Set debug level according to -v/-d switches
		if args.verbose:
			self.debug = 1
		elif args.quiet:
			self.debug = -1

		return self.run(
			cfgPath = args.configfile,
			cacheDir = args.cachedir,
			pidFilePath = args.pidfile,
			onlySets = args.sets,
			force = args.force,
			batch = args.batch,
			safe = args.safe
		)


	def run(self, cfgPath:str, cacheDir:str, pidFilePath:str=None, onlySets:list=None, force:bool=False, batch:bool=False, safe:bool=False) -> int:
		''' Runs JABS
		@param cfgPath str: Path for the config file
		@param cacheDir str: Path for the cache directory
		@param pidFilePath str: Path of PID file (overrides config if given)
		@param onlySets list: List of sets to run
		@param force bool: When True will ignore time constraints and always run sets at any time
		@param batch bool: When true enables batch mode: exit silently if script is already running
		@param safe bool: When True enables safe mode: just print what will do, don't change anything
		@return int exit status, 0 on success
		'''
		# Init some useful variables
		hostname = socket.getfqdn()
		username = getpass.getuser()
		starttime = datetime.now()

		# Reads the config file
		config = JabsConfig()
		try:
			with open(cfgPath, 'rt') as f:
				config.read_file(f)
		except IOError:
			print("ERROR: Couldn't open config file", cfgPath)
			return 1

		# Reads settings from the config file
		sets = config.sections()
		if sets.count("Global") < 1:
			print("ERROR: Global section on config file not found")
			return 1
		sets.remove("Global")

		# If specified at command line, remove unwanted sets
		if onlySets:
			sets = [s for s in sets if s.lower() in map(lambda i: i.lower(), onlySets)]

		if self.debug > 0:
			print("Will run these backup sets:", sets)

		#Init backup sets
		newsets = []
		for s in sets:
			newsets.append(BackupSet(s, config))
		sets = newsets
		del newsets

		#Sort backup sets by priority
		sets = sorted(sets, key=lambda s: s.pri)

		#Read the PIDFILE
		pidfile = config.getstr('PIDFILE') if pidFilePath is None else pidFilePath

		# Check if another insnance of the script is already running
		if os.path.isfile(pidfile):
			PIDFILE = open(pidfile, "r")
			try:
				os.kill(int(PIDFILE.read()), 0)
			except:
				# The process is no longer running, ok
				PIDFILE.close()
			else:
				# The other process is till running
				if batch:
					return 0
				else:
					print("Error: this script is already running!")
					return 12

		# Save my PID on pidfile
		try:
			PIDFILE = open(pidfile, "w")
		except:
			print("Error: couldn't open PID file", pidfile)
			return 15
		PIDFILE.write(str(os.getpid()))
		PIDFILE.flush()

		# Remove disabled sets
		newsets = []
		for s in sets:
			if not s.disabled:
				newsets.append(s)
		sets = newsets
		del newsets

		# Check for sets to run based on current time
		if not force:
			newsets = []
			for s in sets:
				if s.runtime[0] > starttime.time() or s.runtime[1] < starttime.time():
					if self.debug > 0:
						print("Skipping set", s.name, "because out of runtime (", s.runtime[0].isoformat(), "-", s.runtime[1].isoformat(), ")")
				else:
					newsets.append(s)
			sets = newsets
			del newsets

		# Check for sets to run based on interval
		if not force:
			newsets = []
			for s in sets:
				if s.interval and s.interval > timedelta(seconds=0):
					# Check if its time to run this set
					if self.debug > 0:
						print("Will run", s.name, "every", s.interval)
					cachefile = cacheDir + "/" + s.name
					if not os.path.exists(cacheDir):
						print("WARNING: Cache directory missing, creating it")
						os.mkdir(os.path.dirname(cachefile))
					if not os.path.exists(cachefile):
						lastdone = datetime.fromtimestamp(0)
						print("WARNING: Last backup timestamp for", s.name, "is missing. Assuming 01-01-1970")
					else:
						CACHEFILE = open(cachefile,'r')
						try:
							lastdone = datetime.fromtimestamp(int(CACHEFILE.readline()))
						except ValueError:
							print("WARNING: Last backup timestamp for", s.name, "corrupted. Assuming 01-01-1970")
							lastdone = datetime.fromtimestamp(0)
						CACHEFILE.close()
					if self.debug > 0:
						print("Last", s.name, "run:", lastdone)

					if lastdone + s.interval > starttime:
						if self.debug > 0:
							print("Skipping set", s.name, "because interval not reached (", str(lastdone+s.interval-starttime), "still remains )")
					else:
						newsets.append(s)

			sets = newsets
			del newsets

		# Ping hosts if required
		newsets = []
		for s in sets:
			if s.ping and s.remsrc:
				host = s.remsrc.group(1).split('@')[1]
				if self.debug > 0:
					print("Pinging host", host)
				FNULL = open('/dev/null', 'w')
				hup = subprocess.call(['ping', '-c 3','-n','-w 60', host], stdout=FNULL, stderr=FNULL)
				FNULL.close()
				if hup == 0:
					if self.debug > -1:
						print(host, "is UP.")
					newsets.append(s)
				elif self.debug > 0:
					print("Skipping backup of", host, "because it's down.")
			else:
				newsets.append(s)

		sets = newsets
		del newsets

		# Check if some set is still remaining after checks
		if not len(sets):
			return 0

		# Print the backup header
		backupheader_tpl = Template("""
-------------------------------------------------
$version

Backup of $hostname
Backup date: $starttime
Backup sets:
$backuplist
-------------------------------------------------

		""")

		nicelist = ""
		for s in sets:
			nicelist = nicelist + "  " + s.name + "\n"
		if len(nicelist) > 0:
			nicelist = nicelist[:-1]

		backupheader = backupheader_tpl.substitute(
			version = VERSION,
			hostname = hostname,
			starttime = starttime.ctime(),
			backuplist = nicelist,
		)
		if self.debug > -1:
			print(backupheader)

		# ---------------- DO THE BACKUP ---------------------------

		for s in sets:

			sstarttime = datetime.now()

			# Write some log data in a string, to be eventually mailed later
			sl = MyLogger()
			sl.setdebuglvl(self.debug)
			sl.add(backupheader_tpl.substitute(
				version = VERSION,
				hostname = hostname,
				starttime = starttime.ctime(),
				backuplist = s.name,
			), noprint=True)

			sl.add("")

			if s.mount:
				if os.path.ismount(s.mount):
					sl.add("WARNING: Skipping mount of", s.mount, "because it's already mounted", lvl=-1)
				else:
					# Mount specified location
					cmd = ["mount", s.mount ]
					sl.add("Mounting", s.mount)
					p = subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
					stdout, stderr = p.communicate()
					ret = p.poll()
					if ret != 0:
						sl.add("WARNING: Mount of", s.mount, "failed with return code", ret, lvl=-1)

			# Put a file cointaining backup date on dest dir
			tmpdir = tempfile.mkdtemp()
			tmpfile = None
			if s.datefile:
				if safe:
					sl.add("Skipping creation of datefile", s.datefile)
				else:
					tmpfile = tmpdir + "/" + s.datefile
					sl.add("Generating datefile", tmpfile)
					TMPFILE = open(tmpfile,"w")
					TMPFILE.write(str(datetime.now())+"\n")
					TMPFILE.close()
					s.backuplist.append(tmpfile)

			# Calculate curret hanoi day and suffix to use
			hanoisuf = ""
			if s.hanoi > 0:
				today = (starttime.date() - s.hanoiday).days + 1
				i = s.hanoi
				while i >= 0:
					if today % 2 ** i == 0:
						hanoisuf = chr(i+65)
						break
					i -= 1
				sl.add("First hanoi day:", s.hanoiday, lvl=1)
				sl.add("Hanoi sets to use:", s.hanoi)
				sl.add("Today is hanoi day", today, "- using suffix:", hanoisuf)

			plink = []
			if s.hardlink and not s.program == 'rsync':
				sl.add("Will NOT use hark linking (not supported in {})".format(s.program), stderr, lvl=-1)

			elif s.hardlink:
				# Seek for most recent backup set to hard link
				if s.remdst:
					#Backing up to a remote path
					(path, base) = os.path.split(s.remdst.group(2))
					sl.add("Backing up to remote path:", s.remdst.group(1), s.remdst.group(2), lvl=1)
					cmd = ["ssh", "-o", "BatchMode=true", s.remdst.group(1), "ls -l --color=never --time-style=long-iso -t -1 \"" + path + "\"" ]
					sl.add("Issuing remote command:", cmd)
					p = subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
					stdout, stderr = p.communicate()
					sl.add("Subprocess return code:", p.poll())
					if len(stderr):
						sl.add("WARNING: stderr was not empty:", stderr, lvl=-1)
					files = stdout.split('\n')
					psets = []
					for f in files:
						m = rlsparser.match(f)
						# If file matched regexp and is a directory
						if m and m.group(1)[0] == "d":
							btime = datetime.strptime(m.group(6),"%Y-%m-%d %H:%M")
							psets.append([m.group(7),btime])
				else:
					(path, base) = os.path.split(s.dst)
					dirs = os.listdir(path)
					psets = []
					for d in dirs:
						if ( d == base or d[:len(base)] + s.sep == base + s.sep ) and S_ISDIR(os.lstat(path+"/"+d)[ST_MODE]):
							btime = datetime.fromtimestamp(os.stat(path+"/"+d).st_mtime)
							psets.append([d,btime])
					psets = sorted(psets, key=lambda pset: pset[1], reverse=True) #Sort by age

				for p in psets:
					sl.add("Found previous backup:", p[0], "(", p[1], ")", lvl=1)
					if p[0] != base + s.sep + hanoisuf:
						plink.append(path + "/" + p[0])

				if len(plink):
					sl.add("Will hard link against", plink)
				else:
					sl.add("Will NOT use hard linking (no suitable set found)")

			else:
				sl.add("Will NOT use hark linking (disabled)")

			tarlogs = []
			setsuccess = True

			if s.pre:
				# Pre-backup tasks
				goon = False
				for p in s.pre:
					sl.add("Running pre-backup task: %s" % p)
					ret = subprocess.call(p, shell=True)
					if ret != 0:
						sl.add("ERROR: %s failed with return code %i" % (p, ret), lvl=-2)
						setsuccess=False
						if s.skiponpreerror:
							sl.add("ERROR: Skipping", s.name, "set, SKIPONPREERROR is set.", lvl=-2)
							break
				else:
					goon = True
				if not goon:
					continue

			if s.checkdst:
				# Checks whether the given backup destination exists
				try:
					i = os.path.exists(s.dst)
					if not i:
						sl.add("WARNING: Skipping", s.name, "set, destination", s.dst, "not found.", lvl=-1)
						continue
				except:
					sl.add("WARNING: Skipping", s.name, "set, read error on", s.dst, ".", lvl=-1)
					continue

			for d in s.backuplist:
				sl.add("Backing up", d, "on", s.name, "...")
				tarlogfile = None
				if s.mailto:
					tarlogfile = tmpdir + '/' + re.sub(r'(\/|\.)', '_', s.name + '-' + d) + '.log'
					if s.compresslog:
						tarlogfile += '.gz'
				if not safe:
					tarlogs.append(tarlogfile)

				#Build command line
				cmd, cmdi, cmdn, cmdr = ([] for x in range(4))
				cmdi.extend(["ionice", "-c", str(s.ionice)])
				cmdn.extend(["nice", "-n", str(s.nice)])
				if s.program not in ('rsync', 'rclone'):
					raise RuntimeError('Unsupported program {}'.format(s.program))
				opts = s.rsync_opts if s.program == 'rsync' else s.rclone_opts
				cmdr.append(s.program)
				cmdr.extend(map(lambda x: rpat.sub(s.name.lower(),x), opts))
				for pl in plink:
					cmdr.append("--link-dest=" + pl )
				if tmpfile and d == tmpfile:
					cmdr.append(tmpfile)
				else:
					cmdr.append(rdir.sub(d, s.src))
				cmdr.append(rdir.sub(d, s.dst + (s.sep+hanoisuf if len(hanoisuf)>0 else "") ))

				if s.ionice != 0:
					cmd.extend(cmdi)
				if s.nice != 0:
					cmd.extend(cmdn)
				cmd.extend(cmdr)

				if safe:
					nlvl = 0
				else:
					nlvl = 1
				sl.add("Commandline:", cmd, lvl=nlvl)
				sl.add("Will write tar STDOUT to", tarlogfile, lvl=1)

				if not safe:
					# Execute the backup
					sys.stdout.flush()
					if s.compresslog:
						TARLOGFILE = gzip.open(tarlogfile, 'wb')
					else:
						TARLOGFILE = open(tarlogfile, 'wb')
					try:
						p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=-1)
					except OSError as e:
						print("ERROR: Unable to locate file", e.filename)
						print("Path: ", os.environ['PATH'])
						return 1
					spoct = SubProcessCommStdoutThread(p, TARLOGFILE)
					spect = SubProcessCommStderrThread(p)
					spoct.start()
					spect.start()
					ret = p.wait()
					spoct.join()
					spect.join()
					TARLOGFILE.close()
					if ret != 0:
						setsuccess = False
					sl.add("Done. Exit status:", ret)

					# Analyze STDERR
					if len(spect.output):
						badoutput = False
						for line in spect.output.splitlines():
							if b'(will try again)' in line:
								continue
							badoutput = True
							break
						if badoutput:
							setsuccess = False
							sl.add("ERROR: stderr was not empty:", -1)
						else:
							sl.add("WARNING: stderr was not empty (but no errors detected):", -1)
						sl.add(spect.output, -1)

				if s.sleep > 0:
					if safe:
						sl.add("Should sleep", s.sleep, "secs now, skipping.")
					else:
						sl.add("Sleeping", s.sleep, "secs.")
						sleep(s.sleep)

			# Delete dirs from deletelist
			for d in s.deletelist:
				deldest = s.dst + (s.sep+hanoisuf if len(hanoisuf)>0 else "") + os.sep + d
				if os.path.exists(deldest) and os.path.isdir(deldest):
					sl.add('DELETING folder in deletelist %s' % deldest)
					shutil.rmtree(deldest)

			# Save last backup execution time
			if s.interval and s.interval > timedelta(seconds=0):
				if safe:
					sl.add("Skipping write of last backup timestamp")
				else:
					sl.add("Writing last backup timestamp", lvl=1)

					# Create cachedir if missing
					if not os.path.exists(cacheDir):
						# 448 corresponds to octal 0700 and is both python 2 and 3 compatible
						os.makedirs(cacheDir, 448)

					cachefile = cacheDir + os.sep + s.name
					CACHEFILE = open(cachefile,'w')
					CACHEFILE.write(str(int(mktime(starttime.timetuple())))+"\n")
					CACHEFILE.close()

			# Create backup symlink, is using hanoi and not remote
			if len(hanoisuf)>0 and not s.remdst:
				if os.path.exists(s.dst) and S_ISLNK(os.lstat(s.dst)[ST_MODE]):
					if safe:
						sl.add("Skipping deletion of old symlink", s.dst)
					else:
						sl.add("Deleting old symlink", s.dst)
						os.unlink(s.dst)
				if not os.path.exists(s.dst):
					if safe:
						sl.add("Skipping creation of symlink", s.dst, "to", s.dst+s.sep+hanoisuf)
					else:
						sl.add("Creating symlink", s.dst, "to", s.dst+s.sep+hanoisuf)
						os.symlink(s.dst+s.sep+hanoisuf, s.dst)
				elif not safe:
					sl.add("WARNING: Can't create symlink", s.dst, "a file with such name exists", lvl=-1)

			stooktime = datetime.now() - sstarttime
			sl.add("Set", s.name, "completed. Took:", stooktime)

			# Umount
			if s.umount:
				if not os.path.ismount(s.umount):
					sl.add("WARNING: Skipping umount of", s.umount, "because it's not mounted", lvl=-1)
				else:
					# Umount specified location
					cmd = ["umount", s.umount ]
					sl.add("Umounting", s.umount)
					p = subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
					stdout, stderr = p.communicate()
					ret = p.poll()
					if ret != 0:
						sl.add("WARNING: Umount of", s.umount, "failed with return code", ret, lvl=-1)

			# Send email
			if s.mailto:
				if safe:
					sl.add("Skipping sending detailed logs to", s.mailto)
				else:
					if s.smtphost:
						sl.add("Sending detailed logs to", s.mailto, "via", s.smtphost)
					else:
						sl.add("Sending detailed logs to", s.mailto, "using local smtp")

					# Create main message
					msg = MIMEMultipart()
					if setsuccess:
						i = "OK"
					else:
						i = "FAILED"
					msg['Subject'] = "Backup of " + s.name + " " + i
					if s.mailfrom:
						m_from = s.mailfrom
					else:
						m_from = username + "@" + hostname
					msg['From'] = m_from
					msg['To'] = ', '.join(s.mailto)
					msg.preamble = 'This is a milti-part message in MIME format.'

					# Add base text
					txt = sl.getstr() + "\n\nDetailed logs are attached.\n"
					txt = MIMEText(txt)
					msg.attach(txt)

					# Add attachments
					for tl in tarlogs:
						if tl:
							TL = open(tl, 'rb')
							if s.compresslog:
								att = MIMEApplication(TL.read(),'gzip')
							else:
								att = MIMEText(TL.read(),'plain','utf-8')
							TL.close()
							att.add_header(
								'Content-Disposition',
								'attachment',
								filename=os.path.basename(tl)
							)
							msg.attach(att)

					# Send the message
					smtp = smtplib.SMTP(timeout=300)
					#smtp.set_debuglevel(1)
					if s.smtphost:
						smtp.connect(s.smtphost)
					else:
						smtp.connect()
					if s.smtpuser or s.smtppass:
						smtp.login(s.smtpuser, s.smtppass)
					smtp.sendmail(m_from, s.mailto, msg.as_string())
					smtp.quit()

			# Delete temporary logs, if any
			for tl in tarlogs:
				if tl:
					sl.add("Deleting log file", tl, lvl=1)
					os.unlink(tl)
			tarlogs = []

			# Delete tmpfile, if created
			if tmpfile and len(tmpfile):
				sl.add("Deleting temporary files")
				os.unlink(tmpfile)
			if tmpdir:
				os.rmdir(tmpdir)

		took = datetime.now() - starttime
		if self.debug > -1:
			print("Backup completed. Took", took)

		return 0


if __name__ == '__main__':
	sys.exit(Jabs().runFromCommandLine())
