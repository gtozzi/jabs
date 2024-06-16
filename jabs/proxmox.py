#!/usr/bin/env python3

""" @package docstring
JABS - Just Another Backup Script - Proxmox backup tool

Takes proxmox vm snapshots and syncs them locally

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
import sys
import logging
import argparse
import traceback
import proxmoxer
import configparser

from . import consts
from . import config as jabs_config


VERSION = "jabs-proxmox v." + consts.version_str()


class ProxMoxBackupper:
	''' Handles a remote host '''

	def __init__(self, host:str, user:str) -> None:
		self.log = logging.getLogger('jabspx')
		self.px = proxmoxer.ProxmoxAPI(host, user=user)
		print(self.px.nodes.get())


class Main:
	''' The main backup program '''

	def __init__(self, configPath:str) -> None:
		''' Reads the config '''
		self._log = logging.getLogger('main')

		if not os.path.exists(configPath) or not os.path.isfile(configPath):
			raise ValueError('configPath must be a file')

		self.config = jabs_config.JabsConfig()
		self.config.read(configPath)

	def run(self) -> None:
		''' Runs the backup '''
		for section in self.config.sections():
			src = self.config.getuserhost('src', section)

			bck = ProxMoxBackupper(src.host, src.user)


def runFromCommandLine() -> int:
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
		return 1

	return 0

if __name__ == '__main__':
	sys.exit(runFromCommandLine())
