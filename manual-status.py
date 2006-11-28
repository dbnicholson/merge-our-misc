#!/usr/bin/env python
# -*- coding: UTF-8 -*-
#
# output status of manual merges

import os

from momlib import *


# Order of priorities
PRIORITY = [ "unknown", "required", "important", "standard", "optional",
             "extra" ]
COLOURS =  [ "#ff8080", "#ffb580", "#ffea80", "#dfff80", "#abff80", "#80ff8b" ]

# Sections
SECTIONS = [ "new", "updated" ]


def options(parser):
    parser.add_option("-D", "--source-distro", type="string", metavar="DISTRO",
                      default=SRC_DISTRO,
                      help="Source distribution")
    parser.add_option("-S", "--source-suite", type="string", metavar="SUITE",
                      default=SRC_DIST,
                      help="Source suite (aka distrorelease)")

    parser.add_option("-d", "--dest-distro", type="string", metavar="DISTRO",
                      default=OUR_DISTRO,
                      help="Destination distribution")
    parser.add_option("-s", "--dest-suite", type="string", metavar="SUITE",
                      default=OUR_DIST,
                      help="Destination suite (aka distrorelease)")

    parser.add_option("-c", "--component", type="string", metavar="COMPONENT",
                      action="append",
                      help="Process only these destination components")

def main(options, args):
    src_distro = options.source_distro
    src_dist = options.source_suite

    our_distro = options.dest_distro
    our_dist = options.dest_suite

    # For each package in the destination distribution, find out whether
    # there's an open merge, and if so add an entry to the table for it.
    for our_component in DISTROS[our_distro]["components"]:
        if options.component is not None \
               and our_component not in options.component:
            continue

        merges = []

        for our_source in get_sources(our_distro, our_dist, our_component):
            try:
                package = our_source["Package"]
                our_version = Version(our_source["Version"])
                our_pool_source = get_pool_source(our_distro, package,
                                                  our_version)
                logging.debug("%s: %s is %s", package, our_distro, our_version)
            except IndexError:
                continue

            try:
                (src_source, src_version, src_pool_source) \
                             = get_same_source(src_distro, src_dist, package)
                logging.debug("%s: %s is %s", package, src_distro, src_version)
            except IndexError:
                continue

            try:
                base = get_base(our_pool_source)
                base_source = get_nearest_source(package, base)
                base_version = Version(base_source["Version"])
                logging.debug("%s: base is %s (%s wanted)",
                              package, base_version, base)
                continue
            except IndexError:
                pass

            try:
                priority_idx = PRIORITY.index(our_source["Priority"])
            except KeyError:
                priority_idx = 0

            filename = changes_file(our_distro, our_source)
            if os.path.isfile(filename):
                changes = ControlFile(filename,
                                      multi_para=False, signed=False).para

                user = changes["Changed-By"]
                uploaded = changes["Distribution"] == OUR_DIST
            else:
                user = None
                uploaded = False

            if uploaded:
                section = "updated"
            else:
                section = "new"

            merges.append((section, priority_idx, package, user,
                           our_source, our_version, src_version))

        write_status_page(our_component, merges, our_distro, src_distro)


def write_status_page(component, merges, left_distro, right_distro):
    """Write out the manual merge status page."""
    merges.sort()

    status_file = "%s/merges/%s-manual.html" % (ROOT, component)
    status = open(status_file + ".new", "w")
    try:
        print >>status, "<html>"
        print >>status
        print >>status, "<head>"
        print >>status, "<title>Ubuntu Merge-o-Matic: %s manual</title>" \
              % component
        print >>status, "<style>"
        print >>status, "img#ubuntu {"
        print >>status, "    border: 0;"
        print >>status, "}"
        print >>status, "h1 {"
        print >>status, "    padding-top: 0.5em;"
        print >>status, "    font-family: sans-serif;"
        print >>status, "    font-size: 2.0em;"
        print >>status, "    font-weight: bold;"
        print >>status, "}"
        print >>status, "h2 {"
        print >>status, "    padding-top: 0.5em;"
        print >>status, "    font-family: sans-serif;"
        print >>status, "    font-size: 1.5em;"
        print >>status, "    font-weight: bold;"
        print >>status, "}"
        print >>status, "p, td {"
        print >>status, "    font-family: sans-serif;"
        print >>status, "    margin-bottom: 0;"
        print >>status, "}"
        print >>status, "li {"
        print >>status, "    font-family: sans-serif;"
        print >>status, "    margin-bottom: 1em;"
        print >>status, "}"
        print >>status, "tr.first td {"
        print >>status, "    border-top: 2px solid white;"
        print >>status, "}"
        print >>status, "</style>"
        print >>status, "<body>"
        print >>status, "<img src=\".img/ubuntulogo-100.png\" id=\"ubuntu\">"
        print >>status, "<h1>Ubuntu Merge-o-Matic: %s manual</h1>" % component

        for section in SECTIONS:
            section_merges = [ m for m in merges if m[0] == section ]
            print >>status, ("<p><a href=\"#%s\">%s %s merges</a></p>"
                             % (section, len(section_merges), section))

        for section in SECTIONS:
            section_merges = [ m for m in merges if m[0] == section ]

            print >>status, ("<h2 id=\"%s\">%s Merges</h2>"
                             % (section, section.title()))

            do_table(status, section_merges, left_distro, right_distro)

        print >>status, "</body>"
        print >>status, "</html>"
    finally:
        status.close()

    os.rename(status_file + ".new", status_file)

def do_table(status, merges, left_distro, right_distro):
    """Output a table."""
    print >>status, "<table cellspacing=0>"
    print >>status, "<tr bgcolor=#d0d0d0>"
    print >>status, "<td rowspan=2><b>Package</b></td>"
    print >>status, "<td colspan=2><b>Last Uploader</b></td>"
    print >>status, "</tr>"
    print >>status, "<tr bgcolor=#d0d0d0>"
    print >>status, "<td><b>%s Version</b></td>" % left_distro.title()
    print >>status, "<td><b>%s Version</b></td>" % right_distro.title()
    print >>status, "</tr>"

    for uploaded, priority, package, user, source, \
            left_version, right_version in merges:
        if user is not None:
            user = user.replace("&", "&amp;")
            user = user.replace("<", "&lt;")
            user = user.replace(">", "&gt;")
        else:
            user = "&nbsp;"

        print >>status, "<tr bgcolor=%s class=first>" % COLOURS[priority]
        print >>status, "<td><tt><a href=\"https://patches.ubuntu.com/" \
              "%s/%s/%s_%s.patch\">%s</a></tt>" \
              % (pathhash(package), package, package, left_version, package)
        print >>status, " <a href=\"https://launchpad.net/distros/ubuntu/" \
              "+source/%s\">(lp)</a></td>" % package
        print >>status, "<td colspan=2>%s</td>" % user
        print >>status, "</tr>"
        print >>status, "<tr bgcolor=%s>" % COLOURS[priority]
        print >>status, "<td><small>%s</small></td>" % source["Binary"]
        print >>status, "<td>%s</td>" % left_version
        print >>status, "<td>%s</td>" % right_version
        print >>status, "</tr>"

    print >>status, "</table>"

if __name__ == "__main__":
    run(main, options, usage="%prog",
        description="output status of manual merges")
