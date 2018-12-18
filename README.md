jabs(8) -- just another backup script
=============================================

## SYNOPSIS

`jabs` [options] [setname]


## DESCRIPTION

This is a simple and powerful rsync-based backup script.

###Main features:
- Rsync-based: Bandwidth is optimized during transfers
- Automatic "Hanoi" backup set rotation
- Incremental "complete" backups using hard links
- E-Mail notifications on completion

###Installation:
- This script is supposed to run as root
- Copy jabs.py as /usr/local/bin/jabs.py
- Copy jabs.cfg as /usr/local/etc/jabs.cfg and customize it
  (see comments and examples inside jabs.cfg)
- Optional: once you have defined your sets, do a test run
  of all sets: /usr/local/bin/jabs.py -c /usr/local/etc/jabs.cfg -f

###Usage:
Place a cron entry like this one:

    MAILTO="your-email-address"
    
    */5 * * * *     root    /usr/local/bin/jabs.py -c /usr/local/etc/jabs.cfg -b -q

The script will end silently when has nothing to do.
Where there is a "soft" error or when a backup is completed, you'll receive
an email from the script
Where there is an "hard" error, you'll receive an email from Cron Daemon (so
make sure cron is able to send emails)


## AUTHOR

Gabriele Tozzi <gabriele@tozzi.eu>


## COPYRIGHT

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
