#!/usr/bin/env python3

""" @package docstring
JABS - Just Another Backup Script

This is a simple and powerful rsync-based backup script (also supports rclone sync).

Main features:
- Rsync-based: Bandwidth is optimized during transfers (same for rclone)
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

from __future__ import annotations

import abc
import types
import typing
import os, sys, socket, subprocess, threading, gzip, tempfile, getpass, shutil
from stat import S_ISDIR, S_ISLNK, ST_MODE
psutil:types.ModuleType|None = None
try:
	import psutil
except ModuleNotFoundError:
	pass
import json
import pathlib
import argparse
from string import Template
from time import sleep, mktime
from datetime import datetime, date, timedelta, time
import re
import smtplib
import email.utils
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart

from . import consts
from . import config as jabs_config

# Default configuration
CONFIGFILE = "/etc/jabs/jabs.cfg"
VERSION = "jabs v." + consts.version_str()
CACHEDIR = "/var/cache/jabs"

# Useful regexp
risremote = re.compile(r'(.*@.*):{1,2}(.*)')
rlsparser = re.compile(r'^([^\s]+)\s+([0-9]+)\s+([^\s]+)\s+([^\s]+)\s+([0-9]+)\s+([0-9]{4}-[0-9]{2}-[0-9]{2}\s[0-9]{2}:[0-9]{2})\s+(.+)$')

# ------------ FUNCTIONS AND CLASSES ----------------------


class Program(metaclass=abc.ABCMeta):
	''' Base class for a supported program '''

	SETNAME_PHOLDER = '{setname}'
	DIRNAME_PHOLDER = '{dirname}'

	def __init__(self, name:str) -> None:
		self.name = name
		self.hardlink_support = False
		self.exit_codes:dict[int,str] = {}

	def __str__(self) -> str:
		return self.name

	def _get_base_cmd(self) -> list[str]:
		return [ self.name ]

	def _get_cmd_options(self, bs:BackupSet, plink:list[str]) -> list[str]:
		opts = bs.program_opts[self.name]
		cmd = list(map(lambda x: x.replace(self.SETNAME_PHOLDER, bs.name.lower()), opts))
		if self.hardlink_support:
			for pl in plink:
				cmd.append("--link-dest=" + pl )
		return cmd

	def _get_cmd_srcdst(self, bs:BackupSet, dst:str|pathlib.Path, hanoisuf:str) -> list[str]:
		if isinstance(dst, pathlib.Path):
			# If source specification is a full local path (backup file), use it as-is
			src_str = str(dst)
		else:
			src_str = bs.src.replace(self.DIRNAME_PHOLDER, dst)

		dst_str = bs.dst.replace(self.DIRNAME_PHOLDER, str(dst))
		if len(hanoisuf) > 0:
			dst_str += bs.sep + hanoisuf

		return [ src_str, dst_str ]

	def get_cmd(self, bs:BackupSet, dst:str|pathlib.Path, hanoisuf:str, plink:list[str]) -> list[str]:
		''' Returns command line for given set '''
		cmd = self._get_base_cmd()
		cmd.extend(self._get_cmd_options(bs, plink))
		cmd.extend(self._get_cmd_srcdst(bs, dst, hanoisuf))
		return cmd

	def get_good_exit_codes(self, bs:BackupSet) -> set[int]:
		return { 0 }

	def get_exit_code_descr(self, code:int) -> str:
		return self.exit_codes[code] if code in self.exit_codes else ''

	def get_version(self) -> str:
		cmd = [ self.name, '--version' ]
		res = subprocess.run(cmd, capture_output=True, check=False, universal_newlines=True, encoding='utf8')
		if res.returncode:
			return f"ERROR: " + res.stderr.replace('\n',' - ')
		else:
			return res.stdout.splitlines()[0]

	def is_error_output_line(self, stream:str, line:bytes, bs:BackupSet) -> str|None:
		''' Given an stdout/stderr line, tells if it signals an error
		@return error reason or None on success '''
		if stream.lower() == 'stdout':
			return self.is_error_stdout_line(line, bs)
		elif stream.lower() == 'stderr':
			return self.is_error_stderr_line(line, bs)
		else:
			raise NotImplementedError(stream)

	def is_error_stdout_line(self, line:bytes, bs:BackupSet) -> str|None:
		''' Given an stdout line, tells if it signals an error '''
		return None

	def is_error_stderr_line(self, line:bytes, bs:BackupSet) -> str|None:
		''' Given an stderr line, tells if it signals an error '''
		return None

class ProgramRsync(Program):
	''' Rsync program definition '''

	def __init__(self) -> None:
		super().__init__('rsync')
		self.hardlink_support = True

		# Rsync exit codes from https://lxadm.com/rsync-exit-codes/
		self.exit_codes = {
			0: 'Success. The rsync command completed successfully without any errors.',
			1: 'Syntax or usage error. There was a problem with the syntax of the rsync command or with the options specified.',
			2: 'Protocol incompatibility. There was a problem with the protocol version or negotiation between the rsync client and server.',
			3: 'Errors selecting input/output files, dirs. There was a problem with the source or destination file or directory specified in the rsync command.',
			4: 'Requested action not supported: An attempt was made to use an unsupported action or option.',
			5: 'Error starting client-server protocol. There was an error starting the client-server protocol.',
			6: 'Daemon unable to append to log-file. The rsync daemon was unable to write to its log file.',
			10: 'Error in socket I/O. There was an error with the socket input/output.',
			11: 'Error in file I/O. There was an error reading or writing to a file.',
			12: 'Error in rsync protocol data stream. There was an error in the rsync protocol data stream.',
			13: 'Errors with program diagnostics. There was an error generating program diagnostics.',
			14: 'Error in IPC code. There was an error in the inter-process communication (IPC) code.',
			20: 'Received SIGUSR1 or SIGINT. The rsync process was interrupted by a signal.',
			21: 'Some error returned by waitpid(). An error occurred while waiting for a child process to complete.',
			22: 'Error allocating core memory buffers. There was an error allocating memory buffers.',
			23: 'Partial transfer due to error. The rsync command completed with an error, but some files may have been transferred successfully.',
			24: 'Partial transfer due to vanished source files. Some source files disappeared before they could be transferred.',
			25: 'The --max-delete limit stopped deletions.',
			30: 'Timeout in data send/receive.',
			35: 'Timeout waiting for daemon connection',
		}

	def get_good_exit_codes(self, bs:BackupSet) -> set[int]:
		goodrets = super().get_good_exit_codes(bs)
		if bs.ignorevanished:
			goodrets.add(24)
		return goodrets

	def is_error_stderr_line(self, line:bytes, bs:BackupSet) -> str|None:
		vanish_starts = [
			b'file has vanished: ',
			b'rsync warning: some files vanished before they could be transferred',
		]

		if b'(will try again)' in line:
			return None

		if bs.ignorevanished and any([line.startswith(s) for s in vanish_starts]):
			return None

		return 'not empty'

class ProgramRclone(Program):
	''' Rsync program definition '''

	def __init__(self) -> None:
		super().__init__('rclone')
		# See https://rclone.org/docs/
		self.exit_codes = {
			0: 'Success',
			1: 'Error not otherwise categorised',
			2: 'Syntax or usage error',
			3: 'Directory not found',
			4: 'File not found',
			5: 'Temporary error (one that more retries might fix) (Retry errors)',
			6: 'Less serious errors (like 461 errors from dropbox) (NoRetry errors)',
			7: 'Fatal error (one that more retries won\'t fix, like account suspended) (Fatal errors)',
			8: 'Transfer exceeded - limit set by --max-transfer reached',
			9: 'Operation successful, but no files transferred (Requires --error-on-no-transfer)',
			10: 'Duration exceeded - limit set by --max-duration reached',
		}

	def _get_base_cmd(self) -> list[str]:
		return [ self.name, 'sync' ]

	def get_cmd(self, bs:BackupSet, dst:str|pathlib.Path, hanoisuf:str, plink:list[str]) -> list[str]:
		cmd = self._get_base_cmd()
		cmd.extend(self._get_cmd_srcdst(bs, dst, hanoisuf))
		cmd.extend(self._get_cmd_options(bs, plink))
		cmd.append('--use-json-log')
		return cmd

	def is_error_stdout_line(self, line:bytes, bs:BackupSet) -> str|None:
		return 'not empty'

	def is_error_stderr_line(self, line:bytes, bs:BackupSet) -> str|None:
		good_levels = { 'debug', 'info', 'warning' }

		try:
			decoded = json.loads(line)
		except json.JSONDecodeError:
			return 'no json'

		if type(decoded) != dict:
			return 'no dict'

		if 'level' not in decoded:
			return 'no level'

		if decoded['level'] not in good_levels:
			return 'bad level'

		return None


# Supported programs
PROGRAMS = {
	'rsync': ProgramRsync(),
	'rclone': ProgramRclone(),
}


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


class SubProcessCommThread(threading.Thread):
	""" Base subprocess communication thread class """

	def __init__(self, name:str, stream:typing.IO[bytes], logh:typing.IO[bytes]|gzip.GzipFile|None, sl:MyLogger, record:bool, bs:BackupSet, check:bool) -> None:
		"""
			@param name: The stream name (STDOUT|STDERR)
			@param stream: The stream to write to
			@param logh: The file to write to
			@param sl: Debug logger
			@param record: Whether to record data in self.output
			@param bs: The backup set
			@param check: Whether to check lines for errors
		"""
		super().__init__(daemon=True)
		self.name = name
		self._stream = stream
		self._logh = logh
		self._sl = sl
		self._record = record
		self._bs = bs
		self._check = check
		self.output:list[bytes] = []
		self.errors:list[tuple[str,bytes]] = []

	def run(self):
		while True:
			text = self._stream.readline()
			if text == b'':
				break
			self._processLine(text)

	def _processLine(self, text:bytes):
		self._sl.add(self.name + ': ' + text.decode(errors='replace').rstrip('\n'), lvl=1)
		if self._logh:
			self._logh.write(text)
			self._logh.flush()
		if self._record:
			self.output.append(text)
		if self._check:
			err_reason = self._bs.program.is_error_output_line(self.name, text, self._bs)
			if err_reason is not None:
				self.errors.append((err_reason, text))


class BackupSet:
	"""
		Backup set class
	"""

	def __init__(self, name, config:jabs_config.JabsConfig):
		"""
			Creates a new Backup Set object

			@param name string: the name of this set
			@param object config: the JabsConfig object
		"""
		self.name = name
		# Used to filter backup sets
		self.run_now = True

		self.program = PROGRAMS[config.getstr('PROGRAM', self.name, 'rsync', choices=PROGRAMS.keys())]
		self.backuplist:list[str|pathlib.Path] = config.getlist('BACKUPLIST', self.name)
		self.deletelist = config.getlist('DELETELIST', self.name, [])
		self.ionice = config.getint('IONICE', self.name, 0)
		self.ionice_level = config.getint('IONICE_LEVEL', self.name, None)
		self.nice = config.getint('NICE', self.name, 0)
		self.program_opts = {
			'rsync': config.getlist('RSYNC_OPTS', self.name, []),
			'rclone': config.getlist('RCLONE_OPTS', self.name, []),
		}
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
		self.skiponpreerror = config.getboolean('SKIPONPREERROR', self.name, False)
		self.ignorevanished = config.getboolean('IGNOREVANISHED', self.name, False)
		self.smtphost = config.getstr('SMTPHOST', self.name, None)
		self.smtpuser = config.getstr('SMTPUSER', self.name, None)
		self.smtppass = config.getstr('SMTPPASS', self.name, None)
		self.smtpport = config.getint('SMTPPORT', self.name, None)
		self.smtpssl = config.getboolean('SMTPSSL', self.name, True)
		self.smtptimeout = config.getint('SMTPTIMEOUT', self.name, 300)
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

	def run(self, cfgPath:str, cacheDir:str, pidFilePath:str|None=None, onlySets:list[str]|None=None, force:bool=False, batch:bool=False, safe:bool=False) -> int:
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
		config = jabs_config.JabsConfig()
		try:
			with open(cfgPath, 'rt') as f:
				config.read_file(f)
		except IOError:
			print("ERROR: Couldn't open config file", cfgPath)
			return 1

		# Reads settings from the config file
		sets_str = config.sections()
		if sets_str.count("Global") < 1:
			print("ERROR: Global section on config file not found")
			return 1
		sets_str.remove("Global")

		# If specified at command line, remove unwanted sets
		if onlySets:
			sets_str = [s for s in sets_str if s.lower() in map(lambda i: i.lower(), onlySets)]

		if self.debug > 0:
			print("Will run these backup sets:", sets_str)

		#Init backup sets
		sets:list[BackupSet] = []
		for ss in sets_str:
			sets.append(BackupSet(ss, config))

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
		sets = [ s for s in sets if not s.disabled ]

		# Check for sets to run based on current time
		if not force:
			for s in sets:
				if s.runtime[0] > starttime.time() or s.runtime[1] < starttime.time():
					if self.debug > 0:
						print("Skipping set", s.name, "because out of runtime (", s.runtime[0].isoformat(), "-", s.runtime[1].isoformat(), ")")
					s.run_now = False
		sets = [ s for s in sets if s.run_now ]

		# Check for sets to run based on interval
		if not force:
			for s in sets:
				s.run_now = False
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
						s.run_now = True
		sets = [ s for s in sets if s.run_now ]

		# Ping hosts if required
		for s in sets:
			s.run_now = False
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
					s.run_now = True
				elif self.debug > 0:
					print("Skipping backup of", host, "because it's down.")
			else:
				s.run_now = True
		sets = [ s for s in sets if s.run_now ]

		# Check if some set is still remaining after checks
		if not len(sets):
			return 0

		# Print the backup header
		backupheader_tpl = Template("""
-------------------------------------------------
$version

Backup of $hostname
Backup date: $starttime
Backup sets: $backupsets
Backup list: $backuplist
Program versions:
$pversions
-------------------------------------------------

		""")

		pversions:dict[str,str] = {}
		for s in sets:
			if s.program.name not in pversions:
				pversions[s.program.name] = f'- {s.program.name}: ' + s.program.get_version()
		pversions_str = '\n'.join(pversions.values())

		backupheader = backupheader_tpl.substitute(
			version = VERSION,
			hostname = hostname,
			starttime = starttime.ctime(),
			backupsets = ', '.join(s.name for s in sets),
			pversions = pversions_str,
			backuplist = '',
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
				backupsets = s.name,
				pversions = pversions_str,
				backuplist = ', '.join(map(str, s.backuplist)),
			))

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
			tmpdir = tempfile.TemporaryDirectory(prefix='jabs_')
			tmpfile:pathlib.Path|None = None
			if s.datefile:
				if safe:
					sl.add("Skipping creation of datefile", s.datefile)
				else:
					tmpfile = pathlib.Path(tmpdir.name) / s.datefile
					assert tmpfile is not None
					sl.add("Generating datefile", str(tmpfile))
					with open(tmpfile, "wt") as tmpfile_h:
						tmpfile_h.write(str(datetime.now())+"\n")
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

			if s.hardlink and not s.program.hardlink_support:
				sl.add("Will NOT use hark linking (not supported in {})".format(s.program), lvl=-1)

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
					files = stdout.split(b'\n')
					psets:list[tuple[str,datetime]] = []
					for file in files:
						m = rlsparser.match(file.decode())
						# If file matched regexp and is a directory
						if m and m.group(1)[0] == "d":
							btime = datetime.strptime(m.group(6),"%Y-%m-%d %H:%M")
							psets.append((m.group(7),btime))
				else:
					(path, base) = os.path.split(s.dst)
					dirs = os.listdir(path)
					psets = []
					for d in dirs:
						if ( d == base or d[:len(base)] + s.sep == base + s.sep ) and S_ISDIR(os.lstat(path+"/"+d)[ST_MODE]):
							btime = datetime.fromtimestamp(os.stat(path+"/"+d).st_mtime)
							psets.append((d,btime))
					psets = sorted(psets, key=lambda pset: pset[1], reverse=True) #Sort by age

				for pset in psets:
					sl.add("Found previous backup:", pset[0], "(", pset[1], ")", lvl=1)
					if pset[0] != base + s.sep + hanoisuf:
						plink.append(path + "/" + pset[0])

				if len(plink):
					sl.add("Will hard link against", plink)
				else:
					sl.add("Will NOT use hard linking (no suitable set found)")

			else:
				sl.add("Will NOT use hark linking (disabled)")

			tarlogs:list[pathlib.Path] = []
			setsuccess = True

			if s.pre:
				# Pre-backup tasks
				goon = False
				for pre_cmd in s.pre:
					sl.add("Running pre-backup task: %s" % pre_cmd)
					ret = subprocess.call(pre_cmd, shell=True)
					if ret != 0:
						sl.add("ERROR: %s failed with return code %i" % (pre_cmd, ret), lvl=-2)
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

			for bel in s.backuplist:
				sl.add("Backing up", str(bel), "on", s.name, "...")
				tarlogfile = None
				if s.mailto:
					tarlogfile_ext = '.log'
					if s.compresslog:
						tarlogfile_ext += '.gz'
					tarlogfile = pathlib.Path(tmpdir.name) / (re.sub(r'(\/|\.)', '_', s.name + '-' + str(bel)) + tarlogfile_ext)
					tarlogs.append(tarlogfile)

				#Build command line
				cmd = s.program.get_cmd(s, bel, hanoisuf, plink)

				if safe:
					nlvl = 0
				else:
					nlvl = 1
				sl.add("Commandline:", cmd, lvl=nlvl)
				if tarlogfile:
					sl.add("Will write STDOUT and STDERR to", str(tarlogfile), lvl=1)

				if not safe:
					# Execute the backup
					sys.stdout.flush()

					if not tarlogfile:
						tarlogfile_handle:typing.IO[bytes]|gzip.GzipFile|None = None
					elif s.compresslog:
						tarlogfile_handle = gzip.open(tarlogfile, 'wb')
					else:
						tarlogfile_handle = open(tarlogfile, 'wb')

					try:
						p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=-1)
					except OSError as e:
						print("ERROR: Unable to locate file", e.filename)
						print("Path: ", os.environ['PATH'])
						return 1

					if psutil is None:
						sl.add("WARNING: psutil library not installed while trying to set niceness", lvl=-1)
					else:
						try:
							ps_p = psutil.Process(p.pid)

							if s.nice != 0:
								sl.add("Setting process niceness to", str(s.nice), lvl=1)
								ps_p.nice(s.nice)

							if s.ionice != 0:
								sl.add("Setting process i/o class to", str(s.ionice), 'level', s.ionice_level, lvl=1)
								ps_p.ionice(s.ionice, s.ionice_level)
						except psutil.NoSuchProcess:
							sl.add("WARNING: process not found while trying to set niceness", lvl=-1)

					if p.stdout is None or p.stderr is None:
						raise RuntimeError("Unable to open streams")
					elif isinstance(s.program, ProgramRsync):
						# Rsync writes error to stderr and normal/operational info to stderr
						spoct = SubProcessCommThread('STDOUT', p.stdout, tarlogfile_handle, sl, False, s, False)
						spect = SubProcessCommThread('STDERR', p.stderr, None, sl, True, s, True)
					elif isinstance(s.program, ProgramRclone):
						# Rclone writes all output to stderr, in JSON
						spoct = SubProcessCommThread('STDOUT', p.stdout, None, sl, True, s, False)
						spect = SubProcessCommThread('STDERR', p.stderr, tarlogfile_handle, sl, False, s, True)
					else:
						raise NotImplementedError(s.program.__class__.__name__)

					spoct.start()
					spect.start()

					ret = p.wait()

					spoct.join()
					spect.join()

					if tarlogfile_handle:
						tarlogfile_handle.close()

					goodrets = s.program.get_good_exit_codes(s)

					if ret in goodrets:
						retmessage = 'Good'
					else:
						retmessage = 'Bad'
						setsuccess = False
					retdescr = s.program.get_exit_code_descr(ret)
					sl.add(f"Done. {retmessage} exit status:", ret, retdescr)

					# Error if had bad output
					for spct in spoct, spect:
						quotedstderrlines = []

						for reason, line in spct.errors:
							quotedstderrlines.append(reason + '!> ' + line.decode('utf-8', errors='replace').rstrip('\n'))

						if not quotedstderrlines:
							continue

						setsuccess = False
						sl.add("ERROR: " + spct.name + " had errors:")
						for ql in quotedstderrlines:
							sl.add(ql)

					# Show full recorded output anyway
					for spct in spoct, spect:
						if not spct.output:
							continue

						sl.add("INFO: full " + spct.name + ":")
						for line in spct.output:
							sl.add(line.decode('utf-8', errors='replace').rstrip('\n'))

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
			if setsuccess:
				how = "succesfully"
			else:
				how = "with errors"
			sl.add(f"Set {s.name} completed {how}. Took: {stooktime}")

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
						sl.add("Sending detailed logs to", s.mailto, "via", s.smtphost, "port", s.smtpport, "ssl", s.smtpssl)
					else:
						sl.add("Sending detailed logs to", s.mailto, "using local smtp")

					# Create main message
					msg = MIMEMultipart()
					msg["Message-ID"] = email.utils.make_msgid()
					msg["X-Jabs-Version"] = consts.version_str()
					msg["X-Jabs-Host"] = hostname
					msg["Date"] = email.utils.formatdate(localtime=True)
					if setsuccess:
						i = "OK"
					else:
						i = "FAILED"
					msg['Subject'] = "Backup of " + s.name + " " + i
					msg["X-Jabs-SetSuccess"] = str(setsuccess).lower()
					msg["X-Jabs-SetName"] = s.name
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
						if not tl.exists():
							continue

						with open(tl, 'rb') as f:
							if s.compresslog:
								att:MIMEApplication|MIMEText = MIMEApplication(f.read(),'gzip')
							else:
								att = MIMEText(f.read().decode(errors='replace'),'plain','utf-8')
						att.add_header(
							'Content-Disposition',
							'attachment',
							filename=os.path.basename(tl)
						)
						msg.attach(att)

					# Send the message
					if s.smtphost:
						smtp_port = 0 if s.smtpport is None else s.smtpport
						if s.smtpssl:
							smtp:smtplib.SMTP_SSL|smtplib.SMTP = smtplib.SMTP_SSL(s.smtphost, smtp_port, timeout=s.smtptimeout)
						else:
							smtp = smtplib.SMTP(s.smtphost, smtp_port, timeout=s.smtptimeout)
					else:
						smtp = smtplib.SMTP(timeout=s.smtptimeout)
						smtp.connect()
					#smtp.set_debuglevel(1)
					if s.smtpuser or s.smtppass:
						smtp.login(s.smtpuser, s.smtppass)
					smtp.sendmail(m_from, s.mailto, msg.as_string())
					smtp.quit()

			# Delete temporary logs, if any
			for tl in tarlogs:
				if tl.exists():
					sl.add("Deleting log file", str(tl), lvl=1)
					tl.unlink()
			tarlogs.clear()

			# Delete tmpfile, if created
			if tmpfile and tmpfile.exists():
				sl.add("Deleting temporary files")
				tmpfile.unlink()
			if tmpdir:
				tmpdir.cleanup()

		took = datetime.now() - starttime
		if self.debug > -1:
			print("Backup completed. Took", took)

		return 0


def runFromCommandLine() -> int:
	''' Parses the command line and runs JABS
	@return int exit status, 0 on success
	'''

	parser = argparse.ArgumentParser(description=VERSION)
	parser.add_argument('--version', action='version', version=VERSION)
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

	jabs = Jabs()

	# Set debug level according to -v/-d switches
	if args.verbose:
		jabs.debug = 1
	elif args.quiet:
		jabs.debug = -1

	return jabs.run(
		cfgPath = args.configfile,
		cacheDir = args.cachedir,
		pidFilePath = args.pidfile,
		onlySets = args.sets,
		force = args.force,
		batch = args.batch,
		safe = args.safe
	)


if __name__ == '__main__':
	sys.exit(runFromCommandLine())
