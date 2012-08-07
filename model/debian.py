from model.base import Distro, Package
from util import tree
import logging
import os
from os import path
import urllib
import error
from deb.version import Version
import config

class DebianDistro(Distro):
  def __init__(self, name, parent=None):
    super(DebianDistro, self).__init__(name, parent)

  def packages(self, dist, component):
    sources = self.getSources(dist, component)
    return map(lambda x:self.package(dist, component, x["Package"]), sources)

  def package(self, dist, component, name):
    source = None
    for s in self.getSources(dist, component):
      if s['Package'] == name:
        return Package(self, dist, component, name, Version(s['Version']))
    raise error.PackageNotFound(dist, component, name)

  def updatePool(self, dist, component, package=None):
    mirror = self.config("mirror")
    sources = self.getSources(dist, component)
    for source in sources:
      if package != source["Package"] and not (package is None):
        continue
      sourcedir = source["Directory"]

      pooldir = self.package(dist, component, source["Package"]).poolDirectory()

      for md5sum, size, name in files(source):
          url = "%s/%s/%s" % (mirror, sourcedir, name)
          filename = "%s/%s/%s" % (config.get('ROOT'), pooldir, name)

          if os.path.isfile(filename):
              if os.path.getsize(filename) == int(size):
                  logging.debug("Skipping %s, already downloaded.", filename)
                  continue

          logging.debug("Downloading %s", url)
          tree.ensure(filename)
          try:
              urllib.URLopener().retrieve(url, filename)
          except IOError:
              logging.error("Downloading %s failed", url)
              raise
          logging.info("Saved %s", tree.subdir(config.get('ROOT'), filename))

def files(source):
    """Return (md5sum, size, name) for each file."""
    files = source["Files"].strip("\n").split("\n")
    return [ f.split(None, 2) for f in files ]
