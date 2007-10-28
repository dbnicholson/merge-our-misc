#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import md5
import errno
import logging

from optparse import OptionParser

from deb.controlfile import ControlFile
from deb.version import Version
from util import shell, tree


# Output root
ROOT = "/srv/patches.ubuntu.com"

# Distribution definitions
DISTROS = {
    "ubuntu": {
        "mirror": "http://archive.ubuntu.com/ubuntu",
        "dists": [ "hardy" ],
        "components": [ "main", "restricted", "universe", "multiverse" ],
        },
    "debian": {
        "mirror": "http://ftp.uk.debian.org/debian",
        "dists": [ "testing", "unstable", "experimental" ],
        "components": [ "main", "contrib", "non-free" ],
        },
    }

# Destination distribution and release
OUR_DISTRO = "ubuntu"
OUR_DIST   = "hardy"

# Default source distrbution and release
SRC_DISTRO = "debian"
SRC_DIST   = "unstable"


# Automated bug filing
LAUNCHPAD_URL      = "https://launchpad.net/"
LAUNCHPAD_EMAIL    = "mom@ubuntu.com"
LAUNCHPAD_USERNAME = "mom-ubuntu"
LAUNCHPAD_PASSWORD = "1YFNuixv"


# Cache of parsed sources files
SOURCES_CACHE = {}


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
    return md5.new(open(filename).read()).hexdigest()


# --------------------------------------------------------------------------- #
# Location functions
# --------------------------------------------------------------------------- #

def sources_file(distro, dist, component):
    """Return the location of a local Sources file."""
    return "%s/dists/%s-%s/%s/source/Sources.gz" % (ROOT, distro, dist,
						    component)

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

def diff_directory(distro, source):
    """Return the directory where we can find diffs."""
    return "%s/diffs/%s/%s/%s" \
           % (ROOT, distro, pathhash(source["Package"]), source["Package"])

def diff_file(distro, source):
    """Return the location of a local diff file."""
    return "%s/%s_%s.patch" % (diff_directory(distro, source),
                               source["Package"], source["Version"])

def patch_file(distro, source, slipped=False):
    """Return the location of a local patch file."""
    path = "%s/patches/%s/%s/%s/%s_%s" \
           % (ROOT, distro, pathhash(source["Package"]),
              source["Package"], source["Package"], source["Version"])
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

def work_dir(package, version):
    """Return the directory to produce the merge result."""
    return "%s/work/%s/%s/%s" % (ROOT, pathhash(package), package, version)

def result_dir(package):
    """Return the directory to store the result in."""
    return "%s/merges/%s/%s" % (ROOT, pathhash(package), package)


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
    for source in sources:
        if source["Package"] == package:
            return source
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
    sources = open(filename, "w")
    try:
        shell.run(("apt-ftparchive", "sources", pooldir), chdir=ROOT,
                  stdout=sources)
    finally:
        sources.close()

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

def get_nearest_source(package, base):
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
            return get_pool_source(OUR_DISTRO, package, base)
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
    version = strip_suffix(version, "ubuntu")

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

    basis = open(basis_file)
    try:
        return Version(basis.read().strip())
    finally:
        basis.close()

def save_basis(filename, version):
    """Save the basis version of a patch to a file."""
    basis_file = filename + "-basis"
    basis = open(basis_file, "w")
    try:
        print >>basis, "%s" % version
    finally:
        basis.close()


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
    changes = open(filename, "w")
    try:
        cmd = ("dpkg-genchanges", "-S", "-u%s" % filesdir)
        orig_cmd = cmd
        if previous is not None:
            cmd += ("-v%s" % previous["Version"],)

        try:
            shell.run(cmd, chdir=srcdir, stdout=changes)
        except (ValueError, OSError):
            shell.run(orig_cmd, chdir=srcdir, stdout=changes)
    finally:
        changes.close()

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
    diff = open(filename, "w")
    try:
        shell.run(("diff", "-pruN", lastdir, thisdir),
                  chdir=diffdir, stdout=diff, okstatus=(0, 1, 2))
    finally:
        diff.close()


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

    report = open(filename)
    try:
        for line in report:
            if line.startswith("base:"):
                base_version = Version(line[5:].strip())
            elif line.startswith("%s:" % left_distro):
                left_version = Version(line[len(left_distro)+1:].strip())
            elif line.startswith("%s:" % right_distro):
                right_version = Version(line[len(right_distro)+1:].strip())
    finally:
        report.close()

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
    blacklist = open(filename)
    try:
        for line in blacklist:
            try:
                line = line[:line.index("#")]
            except ValueError:
                pass

            line = line.strip()
            if not line:
                continue

            bl.append(line)
    finally:
        blacklist.close()

    return bl
