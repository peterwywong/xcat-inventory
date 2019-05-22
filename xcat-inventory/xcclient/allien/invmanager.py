###############################################################################
# IBM(c) 2019 EPL license http://www.eclipse.org/legal/epl-v10.html
###############################################################################

# -*- coding: utf-8 -*-

import os
import collections
from flask import g, current_app

from xcclient.xcatd import XCATClient, XCATClientParams

from .app import dbi, dbsession, cache
from .noderange import NodeRange
from ..inventory.manager import InventoryFactory
from ..inventory.exceptions import *
import time

OPT_QUERY_THRESHHOLD = 18


def get_nodes_list(ids=None):
    """Fetches node name list and their belonged groups.

    Returns:
        A dict mapping keys to the corresponding node
        fetched. Each row is represented as a tuple of strings. For
        example:

            {
             'node1':{'groups':'all,my_group'},
             'node2':{'groups':'all,my_group'}
            }

    Raises:
        DBException: An error occurred accessing the database.
    """
    wants = []
    if ids:
        if type(ids) is list:
            wants.extend(ids)
        else:
            wants.append(ids)

    return dbi.getcolumns('nodelist', cols=['groups','comments'], keys=wants)


def get_nodes_status(ids=None):
    """Fetches node status with given nodes or all.

    Returns:
        A dict mapping keys to the corresponding node
        fetched. Each row is represented as a tuple of strings. For
        example:

            {
             'node1':{'status':'installing', 'statustime': 'XXXX', ...},
             'node2':{'status':'booted', 'statustime': 'XXXX', ...}
            }

    Raises:
        DBException: An error occurred accessing the database.
    """
    wants = []
    if ids:
        if type(ids) is list:
            wants.extend(ids)
        else:
            wants.append(ids)

    return dbi.getcolumns('nodelist',
                        cols=['status', 'statustime', 'updatestatus', 'updatestatustime', 'appstatus', 'appstatustime'],
                        keys=wants)


def transform_to_status(status_d):
    """transform the status object model to a REST style

        Args:
            status_d: dict of status, {'status':'installing', 'statustime': 'XXXX', ...}.

        Returns:
            A dict mapping keys to the corresponding node
            fetched. Each row is represented as a tuple of strings. For
            example:

                {
                 'boot':{'state':'installing', 'updated_at': 'XXXX'},
                 'sync':{'state':'installing', 'updated_at': 'XXXX'},
                 'app':{'state':'installing', 'updated_at': 'XXXX'},
                }
    """
    spec = dict()
    if status_d.get('status'):
        spec['boot'] = dict(state=status_d.get('status'), updated_at=status_d.get('statustime'))
    if status_d.get('updatestatus'):
        spec['sync'] = dict(state=status_d.get('updatestatus'), updated_at=status_d.get('updatestatustime'))
    if status_d.get('appstatus'):
        spec['sync'] = dict(state=status_d.get('appstatus'), updated_at=status_d.get('appstatustime'))

    return spec


@cache.cached(timeout=50, key_prefix='get_node_basic')
def get_node_basic(id):

    return dbi.gettab(['nodelist'], [id])


def get_nodes_by_range(noderange=None):

    # parse the node range in literal to a list objects (might be node, group, or non existence)
    nr = NodeRange(noderange)

    # Get attributes from nodelist
    if nr.all or nr.size > OPT_QUERY_THRESHHOLD:
        # query whole if the range size larger than 255
        dataset = dbi.gettab(['nodelist', 'nodegroup'])
    else:
        dataset = dbi.gettab(['nodelist', 'nodegroup'], nr.nodes)

    g.nodeset = dataset
    if nr.all:
        return dataset.keys(), None

    nodelist = dict()
    nonexistence = list()
    for name in nr.nodes:
        if name in dataset:
            nodelist[name] = dataset[name]
        else:
            nonexistence.append(name)

    # For nonexistence, need to check if it is a group or tag
    return nodelist.keys(), nonexistence


def _check_groups_in_noderange(nodelist, noderange):
    unique_groups = set()  # unique group or tag name

    # get all group names
    for node, values in nodelist.iteritems():
        groups = values.get('nodelist.groups', '')
        if groups:
            unique_groups.update(groups.split(','))

    return list(unique_groups)


def get_nodes_by_list(nodelist=None):
    return dbi.gettab(['nodelist'], nodelist)


def get_hmi_by_list(nodelist=None):
    result = {}
    for node, values in dbi.gettab(['openbmc'], nodelist).iteritems():
        result[node] = {'bmcip': values.get('openbmc.bmc'), 'username': 'root', 'password': '0penBmc'}

    return result


def dict_merge(dct, merge_dct):
    """Recursive dict merge

    Recurse down into dicts nested to an arbitrary depth, updating keys. The merge_dct is merged into dct.

    Args:
        dct: dict onto which the merge is executed
        merge_dct: dct merged into dct

    Returns:
        None
    """
    for k, v in merge_dct.iteritems():
        if (k in dct and isinstance(dct[k], dict)
                and isinstance(merge_dct[k], collections.Mapping)):
            dict_merge(dct[k], merge_dct[k])
        else:
            dct[k] = merge_dct[k]
    return dct


def get_node_attributes(node):
    """Get node attbributes.

    Args:
        node: one node name

    Returns:
        example:

           {
               'meta':{"name":"node1"},
               'spec':{"obj_type": "node",
                      ... ... }
           }
    """
    # get hierarchicalattrs from site table
    hierarchicalattrs = dbi.getsitetabentries(['hierarchicalattrs'])
    target_node = get_nodes_list(node)
    if not target_node:
        return None
    groups = target_node.values()[0].get('groups')
    # combine the attribute from groups
    needs = [node]
    groupslist=groups.split(',')
    needs.extend(['xcatdefaults'])
    needs.extend(groupslist)
    inv_data=get_nodes_inventory(ids=needs)
    result={}
    if hierarchicalattrs:
        result=groups_data_overwrite_node(hierarchicalattrs,inv_data)
    else:
        # default
        result=fetch_data_from_group(inv_data,node,groupslist)
    return result


def groups_data_overwrite_node(hierarchicalattrs,inv_data={}):
    """TODO"""
    result={}
    result['meta']='groups_data_overwrite_node'
    return result


def merge_groups_data(inv_data,nodelist):
    """merge different groups data into dict

    Args:
        inv_data: all groups inventary data
        nodelist: ordered group list

    Returns:
        final group data dict

        example:

            {'obj_type': 'group', 'engines': {'netboot_engine': {'engine_info': {'postbootscripts': 'confignetwork'}}}, 'obj_info': {'grouptype': 'static'}, 'role': 'compute', 'device_type': 'server', 'network_info': 
          ... ...
            }}

    """
    mergeddict={}
    for k in list(set(nodelist).intersection(set(inv_data.keys()))):
        if not mergeddict:
            mergeddict=inv_data[k]
        else:
            mergeddict=dict_merge(mergeddict,inv_data[k])
    #delete group specific attribute
    exclude_list=['grouptype','members','wherevals']
    if 'obj_info' in mergeddict.keys():
        for exl in exclude_list:
            if exl in mergeddict['obj_info'].keys():
                del mergeddict['obj_info'][exl]
    return mergeddict


def merge_xcatdefaults(xcatdefaults_dict,nodedict):
    """merge xcatdefaults group into nodedict.

    Args:
        xcatdefaults_dict: xcatdefaults inventory data dict
        nodedict: node object data dict
    Returns:
        new node dict
        example:
            {'obj_type': 'node', 
             'engines': {'netboot_engine': {'engine_info': {'postbootscripts': 'confignetwork'}}}, 'obj_info': {'grouptype': 'static'}, 'role': 'compute', 'device_type': 'server', 'network_info':
          ... ...
            }
    """
    nodepostscripts=''
    scripts=['postbootscripts','postscripts']
    if 'engines' not in nodedict:
        nodedict['engines'] = dict(netboot_engine=dict(engine_info=dict()))
    for scp in scripts:
        if scp in xcatdefaults_dict['engines']['netboot_engine']['engine_info'].keys():
            if scp in nodedict['engines']['netboot_engine']['engine_info'].keys():
                nodepostscripts=nodedict['engines']['netboot_engine']['engine_info'][scp]
        nodedict['engines']['netboot_engine']['engine_info'][scp]="%s,%s" % (xcatdefaults_dict['engines']['netboot_engine']['engine_info'][scp],nodepostscripts)
    return nodedict


def fetch_data_from_group(inv_data,node,nodelist):
    """when site.hierarchicalattrs is empty

    Args:
        inv_data: node and its groups inventary data
        node: node name
        nodelist: ordered node and groups list

    Returns:
        node object details
        example:
            {'meta': {'name':'node1'},
             'spec': {
              ... ...
                }
            }
    """
    result={}
    mergeddict={}
    mergeddict=merge_groups_data(inv_data,nodelist)
    #merge node and groups data
    mergeddict=dict_merge(mergeddict,inv_data[node])
    if inv_data['xcatdefaults']:
        mergeddict=merge_xcatdefaults(inv_data['xcatdefaults'],mergeddict)
    metadict={}
    metadict['name']=node
    result['meta']=metadict
    result['spec']=mergeddict
    return result


def get_nodes_inventory(objtypes=[], ids=None):

    result = get_inventory_by_type('node', ids)
    if objtypes:
        not_wants = list()
        for name, obj_d in result.items():
            obj_type = obj_d.get('obj_type')
            if obj_type not in objtypes:
                not_wants.append(name)
        for name in not_wants:
            del result[name]

    return result


def get_inventory_by_type(objtype, ids=None):
    hdl = InventoryFactory.createHandler(objtype, dbsession, None)

    wants = None
    if ids:
        if type(ids) is list:
            wants = ids
        else:
            wants = [ids]

    return hdl.exportObjs(wants, None, fmt='json').get(objtype)


def upd_inventory_by_type(objtype, obj_attr_dict, clean=False):
    hdl = InventoryFactory.createHandler(objtype, dbsession, None)

    hdl.importObjs(obj_attr_dict.keys(), obj_attr_dict, update=not clean, envar={})
    dbsession.commit()


def del_inventory_by_type(objtype, obj_list):
    """delete objects from data store"""
    # hdl = InventoryFactory.createHandler(objtype, dbsession, None)

    #return hdl.importObjs(obj_list, {}, update=False, envar={})

    param = XCATClientParams(xcatmaster=os.environ.get('XCAT_SERVER'))
    cl = XCATClient()
    cl.init(current_app.logger, param)
    result = cl.rmdef(args=['-t', objtype, '-o', ','.join(obj_list)])
    return result.output_msgs


def patch_inventory_by_type(objtype, obj_name, obj_d):
    """modify object attribute"""
    if not obj_d:
        raise InvalidValueException("Input data should not be None.")
    if not type(obj_d) in [dict, list]:
        raise InvalidValueException("Input data format should be dict or list.")
    if not obj_d.keys()[0] == "modify":
        raise InvalidValueException("Input data key should be modify.")
    if not obj_d.get('modify'):
        raise InvalidValueException("Input data value should be null.")
    kv_pair=''
    for key,value in obj_d.get('modify').items():
        if kv_pair is None:
            kv_pair=key+"="+value
        else:
            kv_pair=kv_pair+" "+key+"="+value
    param = XCATClientParams(xcatmaster=os.environ.get('XCAT_SERVER'))
    cl = XCATClient()
    cl.init(current_app.logger, param)
    result = cl.chdef(args=['-t', objtype, '-o', obj_name, kv_pair])

    return dict(outputs=result.output_msgs)


def del_table_entry_by_key(objtype, obj_attr_dict):
    hdl = InventoryFactory.createHandler(objtype, dbsession, None)

    hdl.deleteTabEntrybykey(objtype, obj_attr_dict)
    dbsession.commit()


def add_table_entry_by_key(objtype, obj_attr_dict):
    hdl = InventoryFactory.createHandler(objtype, dbsession, None)
    hdl.addTabEntrybykey(objtype, obj_attr_dict)
    dbsession.commit()


def update_table_entry_by_key(objtype, obj_attr_dict):
    hdl = InventoryFactory.createHandler(objtype, dbsession, None)
    hdl.updateTabEntrybykey(objtype, obj_attr_dict)
    dbsession.commit()


def transform_from_inv(obj_d):
    """transform the inventory object model(dict for collection) to a list"""
    assert obj_d is not None
    assert type(obj_d) is dict

    results = list()
    while len(obj_d) > 0:
        name, spec = obj_d.popitem()
        rd = dict(meta=dict(name=name), spec=spec)
        results.append(rd)

    return results


def validate_resource_input_data(obj_d, obj_name=None):
    """input object data should have meta and spec"""
    if not obj_d:
        raise InvalidValueException("Input data should not be None.")
    if not type(obj_d) in [dict, list]:
        raise InvalidValueException("Input data format should be dict or list.")
    if not len(obj_d.keys())==2:
        raise InvalidValueException("Input data keys number is wrong.")
    if not obj_d.keys()[0]=="meta":
        raise InvalidValueException("Input data first key "+obj_d.keys()[0]+" should be meta.")
    if not obj_d.keys()[1]=="spec":
        raise InvalidValueException("Input data second key "+obj_d.keys()[1]+" is spec.")
    if not type(obj_d['spec']) in [dict]:
        raise InvalidValueException("spec type shoule be dict.")
    if not type(obj_d['meta']) in [dict]:
        raise InvalidValueException("meta type shoule be dict.")
    if not len(obj_d['meta'])==1:
        raise InvalidValueException("meta key number should be 1.")
    if not obj_d['meta'].keys()[0]=="name":
        raise InvalidValueException("meta key should be name.")
    if not obj_d['meta'].get('name'):
        raise InvalidValueException("meta name should not be null");
    if obj_name:
        if not obj_d['meta'].get('name')==obj_name:
            raise InvalidValueException("meta name "+obj_d['meta'].get('name')+" is not the same with resource name "+obj_name+".")


def transform_to_inv(obj_d):
    """transform the REST object(list or dict) to inventory object model(dict for collection)"""
    assert obj_d is not None
    assert type(obj_d) in [dict, list]

    def _dict_to_inv(src):
        assert 'meta' in src
        name = obj_d['meta'].get('name')
        # TODO: name = name or random_name()
        val = obj_d.get('spec')
        return name, val

    result = dict()
    if type(obj_d) is dict:
        n, v = _dict_to_inv(obj_d)
        result[n] = v
    else:
        # Then it could be a list
        for ob in obj_d:
            n, v = _dict_to_inv(ob)
            result[n] = v
    return result


def split_inventory_types(types):

    include = list()
    exclude = ['credential']

    # get the include and exclude
    for rt in types:
        rt = rt.strip()
        if not rt:
            raise InvalidValueException("Invalid inventory type name: (%s)" % rt)

        if rt.startswith('-'):
            ert = rt[1:]
            if ert not in InventoryFactory.getvalidobjtypes():
                raise InvalidValueException("Invalid inventory type name: (%s)" % rt)
            exclude.append(ert)
        else:
            if rt not in InventoryFactory.getvalidobjtypes():
                raise InvalidValueException("Invalid inventory type name: (%s)" % rt)
            include.append(rt)

    return include, exclude
