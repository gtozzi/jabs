
""" @package docstring
JABS - Just Another Backup Script

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

MINPYTHON = (3, 5)

if sys.version_info[0] < MINPYTHON[0] or \
	sys.version_info[0] == MINPYTHON[0] and sys.version_info[1] < MINPYTHON[1]:
	raise RuntimeError('At least python {}.{} is required to run this script'.format(*MINPYTHON))
