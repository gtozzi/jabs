#!/usr/bin/env python3

""" @package docstring
JABS - Just Another Backup Script - Binary entry point

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

import sys

# Prevent name clash with current directory
sys.path = sys.path[1:]
import jabs.sync


if __name__ == '__main__':
	sys.exit(jabs.sync.runFromCommandLine())
