###############################################################################
# IBM(c) 2019 EPL license http://www.eclipse.org/legal/epl-v10.html
###############################################################################

# -*- coding: utf-8 -*-

import os
import random
import uuid

from flask import g, current_app
from pyparsing import Word, Combine

from xcclient.xcatd import XCATClient, XCATClientParams

from .invmanager import get_nodes_list, ParseException

MOCK_FREE_POOL = get_nodes_list()
_applied = dict()


SELECTOR_OP_MAP = {
    "disksize": [">", ">=", "<", "<="],
    "memory": [">", ">=", "<", "<="],
    "cpucount": [">", ">=", "<", "<="],
    "cputype": ["!=", "!~", "=~"],
    "machinetype": None,
    "name": None,
    "rack": None,
    "unit": None,
    "room": None,
    "arch": None,
}

SELECTOR_ATTR_MAP = {
    "machinetype": 'mtm',
}


class NotEnoughResourceError(Exception):
    pass


def apply_resource(count, criteria=None, instance=None):

    # Need to lock first

    if not instance:
        instance = str(uuid.uuid1())

    #free_nodes = get_free_resource()
    #if len(free_nodes) < count:
    #    raise NotEnoughResourceError("Not enough free resource.")

    # Make the selection
    selected = filter_resource(count, criteria)
    occupy_nodes(selected, g.user)

    return {instance : ','.join(selected)}


def occupy_nodes(selected, user):

    param = XCATClientParams(xcatmaster=os.environ.get('XCAT_SERVER'))
    cl = XCATClient()
    cl.init(current_app.logger, param)
    args = ['-t', 'node', '-o', ','.join(selected), '-m', 'groups=__TFPOOL-FREE']
    cl.chdef(args)

    args = ['-t', 'node', '-o', ','.join(selected), '-p', 'groups=__TFPOOL-%s' % user]
    cl.chdef(args)


def release_nodes(selected, user):

    param = XCATClientParams(xcatmaster=os.environ.get('XCAT_SERVER'))
    cl = XCATClient()
    cl.init(current_app.logger, param)
    args = ['-t', 'node', '-o', ','.join(selected), '-m', 'groups=__TFPOOL-%s' % user]
    cl.chdef(args)

    args = ['-t', 'node', '-o', ','.join(selected), '-p', 'groups=__TFPOOL-FREE']
    cl.chdef(args)


def _build_query_args(criteria):

    args = list()
    for key, val in criteria.items:
        if key == 'tags':
            for tag in val.split(','):
                op = '=~'
                if tag[0] == '-':
                    tag = tag[1:]
                    op = '!~'
                args.append('-w')
                args.append("usercomment%s%s" % (op, tag))

        elif key not in SELECTOR_OP_MAP:
            raise NotEnoughResourceError("Not supported criteria type: %s." % key)

        args.append('-w')
        args.append("%s==%s" % (key, val))

    return args


def filter_resource(count=1, criteria=None):

    if criteria:
        args = _build_query_args(criteria)
    else:
        args =[]

    selecting = get_free_resource(args)
    if len(selecting) < count:
        raise NotEnoughResourceError("Not enough free resource matched with the specified criteria.")

    rv = list()
    for i in range(count):
        index = random.randint(0, len(selecting)-1)
        rv.append(selecting[index])

    return rv


def free_resource(names=None):
    if names:
        selected = names.split(',')
    else:
        # TODO: get the whole occupied node by this user
        raise NotImplementedError("You must specify some nodes to be free.")

    release_nodes(selected, g.user)


def _parse_lsdef_output(output):
    nodelist = list()
    for item in output.output_msgs:
        if item.endswith("(node)"):
            nodelist.append(item.split()[0])
    return nodelist


def get_free_resource(selector=None):

    param = XCATClientParams(xcatmaster=os.environ.get('XCAT_SERVER'))
    cl = XCATClient()
    cl.init(current_app.logger, param)
    args = ['-t', 'node', '__TFPOOL-FREE' % g.user, '-s']
    if selector:
        args.extend(selector)

    result = cl.lsdef(args)
    if not result.succeeded():
        return []

    return _parse_lsdef_output(result)


def get_occupied_resource():

    param = XCATClientParams(xcatmaster=os.environ.get('XCAT_SERVER'))
    cl = XCATClient()
    cl.init(current_app.logger, param)
    args = ['-t', 'node', '__TFPOOL-%s' % g.user, '-s']

    result = cl.lsdef(args)
    if not result.succeeded():
        return []

    return _parse_lsdef_output(result)
