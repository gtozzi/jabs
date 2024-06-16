
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

import typing
import datetime
import configparser


def wrapper(func, args):
	"""
		Takes a list of arguments and passes them as positional arguments to
		func
	"""
	return func(*args)


class UserHost(typing.NamedTuple):
	user: str
	host: str


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
			return [ x.strip() for x in configparser.ConfigParser.get(self, section, name).strip().split(self.LIST_SEP) if x != '' ]
		elif vtype == 'date':
			return wrapper(datetime.date, map(int, configparser.ConfigParser.get(self, section, name).strip().split('-',3)))
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
			return datetime.timedelta(days=d,hours=h,minutes=m,seconds=s)
		elif vtype == 'timerange':
			return tuple(map(lambda s: wrapper(datetime.time,map(int,s.split(':'))), configparser.ConfigParser.get(self, section, name).strip().split('-')))
		elif vtype == 'userhost':
			return UserHost(*configparser.ConfigParser.get(self, section, name).strip().split('@'))
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

	def getuserhost(self, name, section=NotImplemented, default=NotImplemented, multi=False) -> UserHost:
		""" Same as __get, but returns a UserHost named tuple """
		return self.__get(name, section, default, 'userhost', multi)
