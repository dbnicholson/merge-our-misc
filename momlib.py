#!/usr/bin/env python
# -*- coding: utf-8 -*-
# momlib.py - common utility functions
#
# Copyright © 2008 Canonical Ltd.
# Author: Scott James Remnant <scott@ubuntu.com>.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of version 3 of the GNU General Public License as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import with_statement

import os
import re
import sys
try:
    from hashlib import md5
except ImportError:
    import md5 as _mod_md5
    md5 = _mod_md5.new
import time
import fcntl
import gzip
import errno
import logging
import datetime
import stat

from cgi import escape
from optparse import OptionParser

from deb.controlfile import ControlFile
from deb.version import Version
from util import shell, tree

try:
    from xml.etree import ElementTree
except ImportError:
    from elementtree import ElementTree


MOM_CONFIG_PATH = "/etc/merge-o-matic"
sys.path.insert(1, MOM_CONFIG_PATH)
from momsettings import *
sys.path.remove(MOM_CONFIG_PATH)
    

# Cache of parsed sources files
SOURCES_CACHE = {}
# Cache of mappings of Debian package names to OBS package names
OBS_CACHE = {}

# --------------------------------------------------------------------------- #
# Command-line tool functions
# --------------------------------------------------------------------------- #

def run(main_func, options_func=None, usage=None, description=None):
    """Run the given main function after initialising options."""
    logging.basicConfig()
    logging.getLogger().setLevel(logging.DEBUG)

    parser = OptionParser(usage=usage, description=description)
    parser.add_option("-q", "--quiet", action="callback",
                      callback=quiet_callback, help="Be less chatty")
    if options_func is not None:
        options_func(parser)

    (options, args) = parser.parse_args()
    sys.exit(main_func(options, args))

def quiet_callback(opt, value, parser, *args, **kwds):
    logging.getLogger().setLevel(logging.WARNING)


# --------------------------------------------------------------------------- #
# Utility functions
# --------------------------------------------------------------------------- #

def ensure(path):
    """Ensure that the parent directories for path exist."""
    dirname = os.path.dirname(path)
    if not os.path.isdir(dirname):
        os.makedirs(dirname)

def pathhash(path):
    """Return the path hash component for path."""
    if path.startswith("lib"):
        return path[:4]
    else:
        return path[:1]

def cleanup(path):
    """Remove the path and any empty directories up to ROOT."""
    tree.remove(path)

    (dirname, basename) = os.path.split(path)
    while dirname != ROOT:
        try:
            os.rmdir(dirname)
        except OSError, e:
            if e.errno == errno.ENOTEMPTY or e.errno == errno.ENOENT:
                break
            raise

        (dirname, basename) = os.path.split(dirname)

def md5sum(filename):
    """Return an md5sum."""
    return md5(open(filename).read()).hexdigest()


# --------------------------------------------------------------------------- #
# Location functions
# --------------------------------------------------------------------------- #

def uncompressed_sources_file(distro, dist, component):
    """Return the location of a local Sources file."""
    path = "%s/dists/%s" % (ROOT, distro)
    if dist is not None:
        path = "%s-%s" % (path, dist)
    if component is not None:
        path = "%s/%s" % (path, component)
    return path + "/source/Sources"

def sources_file(distro, dist, component):
    return uncompressed_sources_file(distro, dist, component) + ".gz"

def pool_directory(distro, package):
    """Return the pool directory for a source"""
    return "pool/%s/%s/%s" % (pool_name(distro), pathhash(package), package)

def pool_sources_file(distro, package):
    """Return the location of a pool Sources file for a source."""
    pooldir = pool_directory(distro, package)
    return "%s/%s/Sources" % (ROOT, pooldir)

def unpack_directory(source):
    """Return the location of a local unpacked source."""
    return "%s/unpacked/%s/%s/%s" % (ROOT, pathhash(source["Package"]),
                                     source["Package"], source["Version"])

def changes_file(distro, source):
    """Return the location of a local changes file."""
    return "%s/changes/%s/%s/%s/%s_%s_source.changes" \
           % (ROOT, distro, pathhash(source["Package"]),
              source["Package"], source["Package"], source["Version"])

def dpatch_directory(distro, source):
    """Return the directory where we put dpatches."""
    return "%s/dpatches/%s/%s/%s/%s" \
           % (ROOT, distro, pathhash(source["Package"]), source["Package"],
              source["Version"])

def diff_directory(distro, source):
    """Return the directory where we can find diffs."""
    return "%s/diffs/%s/%s/%s" \
           % (ROOT, distro, pathhash(source["Package"]), source["Package"])

def diff_file(distro, source):
    """Return the location of a local diff file."""
    return "%s/%s_%s.patch" % (diff_directory(distro, source),
                               source["Package"], source["Version"])

def patch_directory(distro, source):
    """Return the directory where we can find local patch files."""
    return "%s/patches/%s/%s/%s" \
           % (ROOT, distro, pathhash(source["Package"]), source["Package"])

def patch_file(distro, source, slipped=False):
    """Return the location of a local patch file."""
    path = "%s/%s_%s" % (patch_directory(distro, source),
                         source["Package"], source["Version"])
    if slipped:
        return path + ".slipped-patch"
    else:
        return path + ".patch"

def published_file(distro, source):
    """Return the location where published patches should be placed."""
    return "%s/published/%s/%s/%s_%s.patch" \
           % (ROOT, pathhash(source["Package"]), source["Package"],
              source["Package"], source["Version"])

def patch_list_file():
    """Return the location of the patch list."""
    return "%s/published/PATCHES" % ROOT

def patch_rss_file(distro=None, source=None):
    """Return the location of the patch rss feed."""
    if distro is None or source is None:
        return "%s/published/patches.xml" % ROOT
    else:
        return "%s/patches.xml" % patch_directory(distro, source)

def diff_rss_file(distro=None, source=None):
    """Return the location of the diff rss feed."""
    if distro is None or source is None:
        return "%s/diffs/patches.xml" % ROOT
    else:
        return "%s/patches.xml" % diff_directory(distro, source)

def work_dir(package, version):
    """Return the directory to produce the merge result."""
    return "%s/work/%s/%s/%s" % (ROOT, pathhash(package), package, version)

def result_dir(package):
    """Return the directory to store the result in."""
    return "%s/merges/%s/%s" % (ROOT, pathhash(package), package)

# --------------------------------------------------------------------------- #
# OBS handling
# --------------------------------------------------------------------------- #

def obs_package_cache(distro):
    """Cache the mapping of Debian package names to OBS package names"""
    global OBS_CACHE

    if distro in OBS_CACHE:
        return

    OBS_CACHE[distro] = {}
    d = obs_directory(distro)
    for package_elt in ElementTree.parse(d + "/.osc/_packages").findall(".//package"):
        obs_package = package_elt.get("name")
        files = []
        for file_elt in ElementTree.parse("%s/%s/.osc/_files" % (d, obs_package)).findall(".//entry"):
            filename = file_elt.get("name")
            files.append(filename)
            if filename[-4:] == ".dsc":
                source = ControlFile("%s/%s/.osc/%s" % (d, obs_package, filename), multi_para=False, signed=True)
        assert source is not None
        OBS_CACHE[distro][source.para["Source"]] = {
            "name": source.para["Source"],
            "obs-name": obs_package,
            "files": files
        }

def obs_directory(distro=None, package=None):
    """Return the directory where the *Debian* package is checked out"""
    if distro is None:
        return "%s/osc" % ROOT
    d = "%s/osc/%s" % (ROOT, DISTROS[distro]["obs"]["project"])
    if package is None:
        return d
    obs_package_cache(distro)
    return d + "/" + OBS_CACHE[distro][package]["obs-name"]

def obs_packages(distro):
    """Return the list of *Debian* package names in an osc checkout"""
    if distro not in OBS_CACHE:
        obs_package_cache(distro)

    return OBS_CACHE[distro].keys()
    
def obs_files(distro, package):
    """Return paths to the source files checked out from osc"""
    if distro not in OBS_CACHE:
        obs_package_cache(distro)

    d = obs_directory(distro, package)
    return [ "%s/.osc/%s" % (d, f) for f in OBS_CACHE[distro][package]["files"] ]

def obs_is_checked_out(distro):
    """Return True if the distro has been checked out via osc"""
    return os.path.exists(obs_directory(distro) + "/.osc/_packages")

def obs_checkout_or_update(distro):
    """If the distro is not checked out, checkout via osc, else update"""
    if not os.path.isdir(obs_directory()):
        os.makedirs(obs_directory())

    if obs_is_checked_out(distro):
        shell.run(("osc", "-A", DISTROS[distro]["obs"]["url"], "update", DISTROS[distro]["obs"]["project"]),
                  chdir=obs_directory())
    else:
        shell.run(("osc", "-A", DISTROS[distro]["obs"]["url"], "checkout", DISTROS[distro]["obs"]["project"]),
                  chdir=obs_directory())

def obs_update_pool(distro):
    """Symlink sources checked out from osc into pool, update Sources, and clear stale symlinks"""
    if distro not in OBS_CACHE:
        obs_package_cache(distro)

    for package, cache in OBS_CACHE[distro].items():
        pooldir = "%s/%s" % (ROOT, pool_directory(distro, package))
        obsdir = obs_directory(distro, cache["name"])
        if not os.path.isdir(pooldir):
            os.makedirs(pooldir)
        for f in cache["files"]:
            target = "%s/%s" % (pooldir, f)
            if os.path.lexists(target):
                os.unlink(target)
            os.symlink("%s/.osc/%s" % (obsdir, f), target)

    def walker(arg, dirname, filenames):
        is_pooldir = False
        for filename in filenames:
            if filename[-4:] == ".dsc" or filename == "Sources" or filename == "Sources.gz":
                is_pooldir = True
            fullname = "%s/%s" % (dirname, filename)
            if not os.path.exists(fullname):
                logging.info("Unlinking stale %s", tree.subdir(ROOT, fullname))
                os.unlink(fullname)

    os.path.walk("%s/pool/%s" % (ROOT, pool_name(distro)), walker, None)

    sources_filename = sources_file(distro, None, None)
    logging.info("Updating %s", tree.subdir(ROOT, sources_filename))
    if not os.path.isdir(os.path.dirname(sources_filename)):
        os.makedirs(os.path.dirname(sources_filename))

    # For some reason, if we try to write directly to the gzipped stream,
    # it gets corrupted at the end
    with open(uncompressed_sources_file(distro, None, None), "w") as f:
        shell.run(("apt-ftparchive", "sources", "%s/pool/%s" % (ROOT, pool_name(distro))), chdir=ROOT, stdout=f)
    with open(uncompressed_sources_file(distro, None, None)) as f:
        with gzip.open(sources_filename, "wb") as gzf:
            gzf.write(f.read())
    
# --------------------------------------------------------------------------- #
# Sources file handling
# --------------------------------------------------------------------------- #

def get_sources(distro, dist, component):
    """Parse a cached Sources file."""
    global SOURCES_CACHE

    filename = sources_file(distro, dist, component)
    if filename not in SOURCES_CACHE:
        SOURCES_CACHE[filename] = ControlFile(filename, multi_para=True,
                                              signed=False)

    return SOURCES_CACHE[filename].paras

def get_source(distro, dist, component, package):
    """Return the source for a package in a distro."""
    sources = get_sources(distro, dist, component)
    matches = []
    for source in sources:
        if source["Package"] == package:
            matches.append(source)
    if matches:
        version_sort(matches)
        return matches.pop()
    else:
        raise IndexError


# --------------------------------------------------------------------------- #
# Pool handling
# --------------------------------------------------------------------------- #

def pool_name(distro):
    """Return the name of the pool for the given distro."""
    if "pool" in DISTROS[distro]:
        return DISTROS[distro]["pool"]
    else:
        return distro

def get_pool_distros():
    """Return the list of distros with pools."""
    distros = []
    for distro in DISTROS.keys():
        pool = pool_name(distro)
        if pool not in distros:
            distros.append(pool)

    return distros

def update_pool_sources(distro, package):
    """Update the Sources files in the pool."""
    pooldir = pool_directory(distro, package)
    filename = pool_sources_file(distro, package)

    logging.info("Updating %s", tree.subdir(ROOT, filename))
    with open(filename, "w") as sources:
        shell.run(("apt-ftparchive", "sources", pooldir), chdir=ROOT,
                  stdout=sources)

def get_pool_sources(distro, package):
    """Parse the Sources file for a package in the pool."""
    filename = pool_sources_file(distro, package)
    sources = ControlFile(filename, multi_para=True, signed=False)
    return sources.paras

def get_pool_source(distro, package, version=None):
    """Return the source for a particular version of a package."""
    sources = get_pool_sources(distro, package)
    if version is None:
        version_sort(sources)
        return sources.pop()

    for source in sources:
        if version == source["Version"]:
            return source
    else:
        raise IndexError

def get_nearest_source(our_distro, package, base):
    """Return the base source or nearest to it."""
    try:
        sources = get_pool_sources(SRC_DISTRO, package)
    except IOError:
        sources = []

    bases = []
    for source in sources:
        if base == source["Version"]:
            return source
        elif base > source["Version"]:
            bases.append(source)
    else:
        try:
            return get_pool_source(our_distro, package, base)
        except (IOError, IndexError):
            version_sort(bases)
            return bases.pop()

def get_same_source(distro, dist, package):
    """Find the same source in another distribution."""
    for component in DISTROS[distro]["components"]:
        try:
            source = get_source(distro, dist, component, package)
            version = Version(source["Version"])
            pool_source = get_pool_source(distro, package, version)

            return (source, version, pool_source)
        except IndexError:
            pass
    else:
        raise IndexError, "%s not found in %s %s" % (package, distro, dist)


# --------------------------------------------------------------------------- #
# Source meta-data handling
# --------------------------------------------------------------------------- #

def get_base(source, slip=False):
    """Get the base version from the given source."""
    def strip_suffix(text, suffix):
        try:
            idx = text.rindex(suffix)
        except ValueError:
            return text

        for char in text[idx+len(suffix):]:
            if not (char.isdigit() or char == '.'):
                return text

        return text[:idx]

    version = source["Version"]
    version = strip_suffix(version, "build")
    version = strip_suffix(version, "co")

    if version.endswith("-"):
        version += "0"

    if slip and version.endswith("-0"):
        version = version[:-2] + "-1"

    return Version(version)

def version_sort(sources):
    """Sort the source list by version number."""
    sources.sort(key=lambda x: Version(x["Version"]))

def files(source):
    """Return (md5sum, size, name) for each file."""
    files = source["Files"].strip("\n").split("\n")
    return [ f.split(None, 2) for f in files ]

def read_basis(filename):
    """Read the basis version of a patch from a file."""
    basis_file = filename + "-basis"
    if not os.path.isfile(basis_file):
        return None

    with open(basis_file) as basis:
        return Version(basis.read().strip())

def save_basis(filename, version):
    """Save the basis version of a patch to a file."""
    basis_file = filename + "-basis"
    with open(basis_file, "w") as basis:
        print >>basis, "%s" % version


# --------------------------------------------------------------------------- #
# Unpacked source handling
# --------------------------------------------------------------------------- #

def unpack_source(source):
    """Unpack the given source and return location."""
    destdir = unpack_directory(source)
    if os.path.isdir(destdir):
        return destdir

    srcdir = "%s/%s" % (ROOT, source["Directory"])
    for md5sum, size, name in files(source):
        if name.endswith(".dsc"):
            dsc_file = name
            break
    else:
        raise ValueError, "Missing dsc file"

    ensure(destdir)
    try:
        shell.run(("dpkg-source", "-x", dsc_file, destdir), chdir=srcdir)
        # Make sure we can at least read everything under .pc, which isn't
        # automatically true with dpkg-dev 1.15.4.
        pc_dir = os.path.join(destdir, ".pc")
        for filename in tree.walk(pc_dir):
            pc_filename = os.path.join(pc_dir, filename)
            pc_stat = os.lstat(pc_filename)
            if pc_stat is not None and stat.S_IMODE(pc_stat.st_mode) == 0:
                os.chmod(pc_filename, 0400)
    except:
        cleanup(destdir)
        raise

    return destdir

def cleanup_source(source):
    """Cleanup the given source's unpack location."""
    cleanup(unpack_directory(source))

def save_changes_file(filename, source, previous=None):
    """Save a changes file for the given source."""
    srcdir = unpack_directory(source)

    filesdir = "%s/%s" % (ROOT, source["Directory"])

    ensure(filename)
    with open(filename, "w") as changes:
        cmd = ("dpkg-genchanges", "-S", "-u%s" % filesdir)
        orig_cmd = cmd
        if previous is not None:
            cmd += ("-v%s" % previous["Version"],)

        try:
            shell.run(cmd, chdir=srcdir, stdout=changes)
        except (ValueError, OSError):
            shell.run(orig_cmd, chdir=srcdir, stdout=changes)

    return filename

def save_patch_file(filename, last, this):
    """Save a diff or patch file for the difference between two versions."""
    lastdir = unpack_directory(last)
    thisdir = unpack_directory(this)

    diffdir = os.path.commonprefix((lastdir, thisdir))
    diffdir = diffdir[:diffdir.rindex("/")]

    lastdir = tree.subdir(diffdir, lastdir)
    thisdir = tree.subdir(diffdir, thisdir)

    ensure(filename)
    with open(filename, "w") as diff:
        shell.run(("diff", "-pruN", lastdir, thisdir),
                  chdir=diffdir, stdout=diff, okstatus=(0, 1, 2))


# --------------------------------------------------------------------------- #
# Merge data handling
# --------------------------------------------------------------------------- #

def read_report(output_dir, left_distro, right_distro):
    """Read the report to determine the versions that went into it."""
    filename = "%s/REPORT" % output_dir
    if not os.path.isfile(filename):
        raise ValueError, "No report exists"

    base_version = None
    left_version = None
    right_version = None

    with open(filename) as report:
        for line in report:
            if line.startswith("base:"):
                base_version = Version(line[5:].strip())
            elif line.startswith("%s:" % left_distro):
                left_version = Version(line[len(left_distro)+1:].strip())
            elif line.startswith("%s:" % right_distro):
                right_version = Version(line[len(right_distro)+1:].strip())

    if base_version is None or left_version is None or right_version is None:
        raise AttributeError, "Insufficient detail in report"

    return (base_version, left_version, right_version)

# --------------------------------------------------------------------------- #
# Blacklist handling
# --------------------------------------------------------------------------- #

def read_blacklist():
    """Read the blacklist file."""
    filename = "%s/sync-blacklist.txt" % ROOT
    if not os.path.isfile(filename):
        return []

    bl = []
    with open(filename) as blacklist:
        for line in blacklist:
            try:
                line = line[:line.index("#")]
            except ValueError:
                pass

            line = line.strip()
            if not line:
                continue

            bl.append(line)

    return bl


# --------------------------------------------------------------------------- #
# RSS feed handling
# --------------------------------------------------------------------------- #

def read_rss(filename, title, link, description):
    """Read an RSS feed, or generate a new one."""
    rss = ElementTree.Element("rss", version="2.0")

    channel = ElementTree.SubElement(rss, "channel")

    e = ElementTree.SubElement(channel, "title")
    e.text = title

    e = ElementTree.SubElement(channel, "link")
    e.text = link

    e = ElementTree.SubElement(channel, "description")
    e.text = description

    now = time.gmtime()

    e = ElementTree.SubElement(channel, "pubDate")
    e.text = time.strftime(RSS_TIME_FORMAT, now)

    e = ElementTree.SubElement(channel, "lastBuildDate")
    e.text = time.strftime(RSS_TIME_FORMAT, now)

    e = ElementTree.SubElement(channel, "generator")
    e.text = "Merge-o-Matic"


    if os.path.isfile(filename):
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=7)

        tree = ElementTree.parse(filename)
        for i, item in enumerate(tree.find("channel").findall("item")):
            dt = datetime.datetime(*time.strptime(item.findtext("pubDate"),
                                                  RSS_TIME_FORMAT)[:6])
            if dt > cutoff or i < 10:
                channel.append(item)

    return rss

def write_rss(filename, rss):
    """Write out an RSS feed."""
    ensure(filename)
    tree = ElementTree.ElementTree(rss)
    tree.write(filename + ".new")
    os.rename(filename + ".new", filename)

def append_rss(rss, title, link, author=None, filename=None):
    """Append an element to an RSS feed."""
    item = ElementTree.Element("item")

    e = ElementTree.SubElement(item, "title")
    e.text = title

    e = ElementTree.SubElement(item, "link")
    e.text = link

    if author is not None:
        e = ElementTree.SubElement(item, "author")
        e.text = author

    if filename is not None:
        e = ElementTree.SubElement(item, "pubDate")
        e.text = time.strftime(RSS_TIME_FORMAT,
                               time.gmtime(os.stat(filename).st_mtime))


    channel = rss.find("channel")
    for i, e in enumerate(channel):
        if e.tag == "item":
            channel.insert (i, item)
            break
    else:
        channel.append(item)


# --------------------------------------------------------------------------- #
# Comments handling
# --------------------------------------------------------------------------- #

def comments_file():
    """Return the location of the comments."""
    return "%s/comments.txt" % ROOT

def get_comments():
    """Extract the comments from file, and return a dictionary
        containing comments corresponding to packages"""
    comments = {}

    with open(comments_file(), "r") as file_comments:
        fcntl.flock(file_comments, fcntl.LOCK_SH)
        for line in file_comments:
            package, comment = line.rstrip("\n").split(": ", 1)
            comments[package] = comment

    return comments

def add_comment(package, comment):
    """Add a comment to the comments file"""
    with open(comments_file(), "a") as file_comments:
        fcntl.flock(file_comments, fcntl.LOCK_EX)
        the_comment = comment.replace("\n", " ")
        the_comment = escape(the_comment[:100], quote=True)
        file_comments.write("%s: %s\n" % (package, the_comment))

def remove_old_comments(status_file, merges):
    """Remove old comments from the comments file using
       component's existing status file and merges"""
    if not os.path.exists(status_file):
        return

    packages = [ m[2] for m in merges ]
    toremove = []

    with open(status_file, "r") as file_status:
        for line in file_status:
            package = line.split(" ")[0]
            if package not in packages:
                toremove.append(package)

    with open(comments_file(), "a+") as file_comments:
        fcntl.flock(file_comments, fcntl.LOCK_EX)

        new_lines = []
        for line in file_comments:
            if line.split(": ", 1)[0] not in toremove:
                new_lines.append(line)

        file_comments.truncate(0)

        for line in new_lines:
            file_comments.write(line)

def gen_buglink_from_comment(comment):
    """Return an HTML formatted Debian/Ubuntu bug link from comment"""
    debian = re.search(".*Debian bug #([0-9]{1,6}).*", comment, re.I)
    ubuntu = re.search(".*bug #([0-9]{1,6}).*", comment, re.I)

    html = ""
    if debian:
        html += "<img src=\".img/debian.png\" alt=\"Debian\" />"
        html += "<a href=\"http://bugs.debian.org/%s\">#%s</a>" \
            % (debian.group(1), debian.group(1))
    elif ubuntu:
        html += "<img src=\".img/ubuntu.png\" alt=\"Ubuntu\" />"
        html += "<a href=\"https://launchpad.net/bugs/%s\">#%s</a>" \
            % (ubuntu.group(1), ubuntu.group(1))
    else:
        html += "&nbsp;"

    return html
