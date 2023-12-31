#!/usr/bin/env python3

""" @package docstring
JABS - Just Another Backup Script

Helper script for creating a debian package

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
import math
import shutil
import pathlib
import logging
import zipfile
import tempfile
import subprocess

import jabs.consts


PACKAGE_NAME = 'jabs'

# Template for debian control file
CONTROL_TEMPLATE = {
	'Package': PACKAGE_NAME,
	'Section': 'custom',
	'Priority': 'optional',
	'Architecture': 'all',
	'Essential': 'no',
	'Maintainer': 'gabriele@tozzi.eu',
	'Depends': 'python3, python3-dateutil, python3-paramiko',
	'Recommends': 'rsync | rclone',
	'Description': """JABS - Just Another Backup Script
        This is a simple and powerful rsync-based backup script.
 .
        Main features:
        - Rsync-based: Bandwidth is optimized during transfers
        - Automatic "Hanoi" backup set rotation
        - Incremental "complete" backups using hard links
        - E-Mail notifications on completion
 .
        The script will end silently when has nothing to do.
        Where there is a "soft" error or when a backup is completed, you'll receive
        an email from the script
        Where there is an "hard" error, you'll receive an email from Cron Daemon (so
        make sure cron is able to send emails)""",
}


class Packager:
	''' Utility class for creating a JABS debian package '''

	TEMP_PREFIX = 'jabs_build_'
	DEB_VER = 1

	def __init__(self):
		self.path = pathlib.Path(__file__).parent.absolute()
		self._log = logging.getLogger()

	def build(self, clean=True):
		''' Builds the debian package
		@param clean bool: When True, cleans the temporary directory
		'''
		whl_path = self.buildPy()
		tpl = self.checkAndGatherInfo()

		if clean:
			dir = tempfile.TemporaryDirectory(prefix=self.TEMP_PREFIX)
			self._log.debug('Building into "%s"', dir.name)
			with dir as rootDir:
				self.__build(rootDir, whl_path, tpl)
		else:
			rootDir = tempfile.mkdtemp(prefix=self.TEMP_PREFIX)
			self._log.debug('Building into "%s"', rootDir)
			self.__build(rootDir, whl_path, tpl)

	def __build(self, rootDir:pathlib.Path|str, whl_path: pathlib.Path|str, tpl:str):
		# Create the base dir
		rootDir = pathlib.Path(rootDir)
		baseDir = '{}_{}_{}_{}'.format(PACKAGE_NAME, jabs.consts.version_str(), self.DEB_VER, tpl['Architecture'])
		basePath = rootDir / baseDir
		basePath.mkdir()
		debPath = basePath / 'DEBIAN'
		debPath.mkdir()
		pythonPath = basePath / 'usr' / 'lib' / 'python3' / 'dist-packages' / 'jabs'
		pythonPath.mkdir(parents=True)

		roughSize = 0

		# Unpack the whl and account for size
		with zipfile.ZipFile(whl_path, 'r') as whl:
			whl.extractall(pythonPath)

		# Copy the files (read sizes and create conffile meanwhile)
		tocopy = {
			('usr', 'bin'): ( ('jabs.py', 0o755), ('jabs-snapshot.py', 0o755) ),
			('etc', 'jabs'): ( ('jabs.cfg', 0o600), ('jabs-snapshot.cfg', 0o600) ),
			('etc', 'cron.d'): ( ('example.crontab', 0o644, 'jabs'), ),
		}
		with open(debPath / 'conffiles', 'wt') as cf:
			for path, files in tocopy.items():
				fullPath = basePath
				for pel in path:
					fullPath /= pel
				fullPath.mkdir(parents=True)
				for finfo in files:
					src = self.path / finfo[0]
					dstname = finfo[2] if len(finfo) > 2 else finfo[0]
					dst = fullPath / dstname
					self._log.debug('Copying "%s" as "%s"', src, dst)
					roughSize += src.stat().st_size
					shutil.copyfile(src, dst)
					os.chmod(dst, finfo[1])

					# Append to conffiles if needed
					if path[0] == 'etc':
						cf.write(os.path.join(os.sep, *path, dstname) + "\n")

		# Update the installed size
		tpl['Installed-Size'] = math.ceil(roughSize / 1024)

		# Create the control file
		with open(os.path.join(basePath, 'DEBIAN', 'control'), 'wt') as f:
			for key, value in tpl.items():
				f.write("{}: {}\n".format(key, value))

		# Finally build the debian package
		cmd = ['dpkg-deb', '--build', '--root-owner-group', '-Zgzip', basePath]
		self._log.debug('Running %s', cmd)
		subprocess.check_call(cmd)
		pkgName = baseDir + '.deb'
		pkgPath = os.path.join(rootDir, pkgName)
		if not os.path.exists(pkgPath):
			raise RuntimeError('Package file "{}" not found'.format(pkgPath))

		# Move the generated file here
		shutil.copyfile(pkgPath, os.path.join(self.path, pkgName))
		self._log.info('Package %s generated', pkgName)

	def checkAndGatherInfo(self):
		''' Run some consistency checks and gather info
		@return dict Debian control template '''
		#TODO: Run tests

		tpl = CONTROL_TEMPLATE

		# Detect version
		tpl['Version'] = "{}+{}".format(jabs.consts.version_str(), self.DEB_VER)

		# Read description
		#with open('jabs.py', 'rt') as f:
		#	jabs = ast.parse(f.read())

		#descr = ast.get_docstring(jabs)
		#tpl['Description'] = descr

		self._log.debug('INFO: %s', tpl)
		return tpl

	def buildPy(self) -> pathlib.Path:
		''' Runs the python build
		@returns built whl path
		'''
		cmd = ('python3', '-m', 'build', '--no-isolation')
		subprocess.check_call(cmd)
		whl_path = pathlib.Path('dist/jabs-{}-py3-none-any.whl'.format(jabs.consts.version_str()))
		assert whl_path.exists() and whl_path.is_file(), whl_path
		return whl_path


if __name__ == '__main__':
	import argparse

	parser = argparse.ArgumentParser()
	parser.add_argument('-v', '--verbose', action='store_true', help="more verbose output")
	parser.add_argument('-q', '--quiet', action='store_true', help="suppress non-essential output")
	parser.add_argument('--noclean', action='store_true', help="do not clean temporary directory")
	args = parser.parse_args()

	if args.verbose:
		level = logging.DEBUG
	elif args.quiet:
		level = logging.WARNING
	else:
		level = logging.INFO
	logging.basicConfig(level=level)

	Packager().build(clean=not args.noclean)
