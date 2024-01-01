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
import docker
import tarfile
import pathlib
import unittest

import jabs.consts


class DockerTests(unittest.TestCase):

	def copyFileToContainer(self, src_path:str|pathlib.Path, dest:str|pathlib.PurePath='/') -> None:
		tar_data = io.BytesIO()
		tar = tarfile.open(mode='w', fileobj=tar_data)
		tar.add(src_path)
		#tar.list(verbose=True)
		tar.close()
		tar_data.seek(0)
		#self.runCommandInContainer('ls')
		self.assertTrue(self.ubuntu.put_archive(dest, tar_data))
		#self.runCommandInContainer('ls')

	def runCommandInContainer(self, cmd:str) -> None:
		print(f'CMD: {cmd}')
		status, (out, err) = self.ubuntu.exec_run(cmd, demux=True)
		if out is not None and out != b'':
			print('STDOUT:\n' + out.decode(errors='replace'))
		if err is not None and err != b'':
			print('STDERR:\n' + err.decode(errors='replace'))
		self.assertEqual(status, 0, f'Command returned non-zero ({status})')

	def setUp(self):
		self.ubuntu = None
		print('Starting docker container…')
		self.docker = docker.from_env()
		self.ubuntu = self.docker.containers.run("debian:latest", 'tail -f /dev/null', auto_remove=True, detach=True)

		bck_dest = pathlib.PurePath('/tmp/jabs-backup')
		print(f'Creating {bck_dest}…')
		self.runCommandInContainer(f'mkdir {bck_dest}')

	def tearDown(self):
		if self.ubuntu is not None:
			print('Killing docker container…')
			self.ubuntu.kill()
		self.docker.close()

	def test_pip_run(self):
		print('Installing PIP…')
		self.runCommandInContainer('apt-get -qq update')
		self.runCommandInContainer('apt-get -qq install python3-pip')

		whl_name = 'jabs-{}-py3-none-any.whl'.format(jabs.consts.version_str())
		whl_path = pathlib.Path('dist') / whl_name
		print(f'Copying PIP package {whl_path}')
		self.copyFileToContainer(whl_path)

		print('Installing JABS as PIP…')
		self.runCommandInContainer(f'pip3 install {whl_path} --break-system-packages')

		print('Copying jabs.cfg…')
		self.copyFileToContainer('jabs.cfg')

		print('Running JABS…')
		self.runCommandInContainer(f'python3 -m jabs.jabs -v -c /jabs.cfg -f Test')

	def test_deb_run(self):
		deb_name = 'jabs_{}_1_all.deb'.format(jabs.consts.version_str())
		print(f'Copying DEB package {deb_name}')
		self.copyFileToContainer(deb_name)

		print('Installing DEB…')
		self.runCommandInContainer('apt-get -qq update')
		self.runCommandInContainer(f'apt-get -qq install ./{deb_name}')

		print('Running JABS…')
		self.runCommandInContainer(f'jabs.py -v -f Test')


if __name__ == '__main__':
	unittest.main()
