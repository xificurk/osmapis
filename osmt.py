# -*- coding: utf-8 -*-
"""
Set of tools for accessing and manipulating OSM data via (X)API.

Constants:
    API_VERSION --- Version of used OSM API.
    API_PATH    --- Path to OSM API on server.

Classes:
    Api         --- Main instance for accessing OSM data via (X)API.
    WebApi      --- Access to the HTTP (X)API.
    DummyCache  --- Dummy cache - defines basic interface for cache classes.
    FileCache   --- Cache that stores data as files in designated directory.
    Node        --- Wrapper for Node element.
    Way         --- Wrapper for Way element.
    Relation    --- Wrapper for Relation element.
    Changeset   --- Wrapper for Changeset element.
    OSM         --- OSM XML document wrapper.
    OSC         --- OSC XML document wrapper.
    ApiError    --- OSM API exception.

"""

__author__ = "Petr Morávek (xificurk@gmail.com)"
__copyright__ = "Copyright (C) 2010 Petr Morávek"
__license__ = "LGPL 3.0"

__version__ = "0.6.0"

from base64 import encodestring as base64encode
try:
    from http.client import HTTPConnection
except ImportError:
    from httplib import HTTPConnection
from logging import getLogger
import os.path
from time import sleep
try:
    from urllib.parse import unquote
except ImportError:
    from urllib import unquote
import xml.etree.cElementTree as ET

__all__ = ["API_VERSION",
           "API_PATH",
           "Api",
           "WebApi",
           "DummyCache",
           "FileCache",
           "Node",
           "Way",
           "Relation",
           "Changeset",
           "OSM",
           "OSC",
           "ApiError"]


API_VERSION = 0.6
API_PATH = "/api/{0}/".format(API_VERSION)


class Api:
    """
    Main instance for accessing OSM data via (X)API.

    Attributes:
        auto_changeset  --- Dictionary with configuration of automatic changeset
                            creation.
        cache           --- Cache instance for XAPI requests.
        api             --- WebAPI instance for OSM API.
        xapi            --- WebAPI instance for OSM XAPI.
        capabilities    --- OSM API capabilities.

    Methods:
        set_credentials     --- Set credentials for authentication in OSM API.
        get_capabilities    --- Download OSM API capabilities.
        get_map             --- OSM API map download.
        get_element         --- Download OSM Element via API by type and id.
        get_node            --- Download Node via API by id.
        get_way             --- Download Way via API by id.
        get_realtion        --- Download Relation via API by id.
        get_history         --- Download complete history of OSM Element.
        get_elements        --- Download OSM Elements via API by type and ids.
        get_nodes           --- Download Nodes via API by ids.
        get_ways            --- Download Ways via API by ids.
        get_relations       --- Download Relations via API by ids.
        get_element_parent_relations --- Download Relations via API that reference OSM Element.
        get_parent_relations --- Download Relations via API that reference OSM Element.
        get_node_parent_ways --- Download Ways via API that reference Node.
        get_parent_ways     --- Download Ways via API that reference Node.
        get_changeset       --- Download Changeset via API by id.
        search_changeset    --- Search for Changeset via API by bbox, user or display_name, time, status.
        create_changeset    --- Create Changeset via API.
        update_changeset    --- Update Changeset via API.
        close_changeset     --- Close Changeset via API.
        upload_diff         --- Diff upload via API.
        create_element      --- Create OSM Element via API.
        create_node         --- Create Node via API.
        create_way          --- Create Way via API.
        create_relation     --- Create Relation via API.
        update_element      --- Update OSM Element via API.
        update_node         --- Update Node via API.
        update_way          --- Update Way via API.
        update_relation     --- Update Relation via API.
        delete_element      --- Delete OSM Element via API.
        delete_node         --- Delete Node via API.
        delete_way          --- Delete Way via API.
        delete_relation     --- Delete Relation via API.
        search              --- Search for OSM Elements via XAPI.
        search_node         --- Search for Nodes via XAPI.
        search_way          --- Search for Ways via XAPI.
        search_relation     --- Search for Relations via XAPI.

    """

    def __init__(self, auto_changeset=None, cache=None, credentials=None):
        """
        Keyworded arguments:
            auto_changeset  --- Dictionary with configuration of automatic
                                changeset creation:
                                    'enabled' - Enable auto_changeset (boolean).
                                    'size' - Maximum size of changeset (integer).
                                    'tag' - Default tags (dictionary).
            cache           --- Path to the cache directory.
            credentials     --- Credentials for authentication in OSM API.

        """
        self._log = getLogger("osmt.api")
        self._capabilities = None
        self._changeset = None
        # Setup auto_changeset
        if not isinstance(auto_changeset, dict):
            auto_changeset = {}
        if "enabled" not in auto_changeset:
            auto_changeset["enabled"] = True
        if "size" not in auto_changeset:
            auto_changeset["size"] = 200
        if "tag" not in auto_changeset:
            auto_changeset["tag"] = {}
        if "created_by" not in auto_changeset["tag"]:
            auto_changeset["tag"]["created_by"] = "osmt/{0}".format(__version__)
        self.auto_changeset = auto_changeset
        # Setup api
        self.xapi = WebAPI("www.informationfreeway.org")
        self.api = WebAPI("api.openstreetmap.org", credentials=credentials)
        try:
            self.cache = FileCache(cache)
        except IOError:
            self.cache = DummyCache()

    def __del__(self):
        self._auto_changeset_clear(force=True)
        return None

    def set_credentials(self, credentials):
        """
        Set credentials for authentication in OSM API.

        Arguments:
            credentials --- Credentials for authentication in OSM API.

        """
        self.api.credentials = credentials

    def _changeset_id(self, changeset=None):
        """
        Return changeset id as string or raise Exception.

        If auto_changeset is enabled and no valid changeset or its id is passed,
        return auto_changeset id (if necessary, create new one).

        Keyworded arguments:
            changeset   --- Changeset instance or changeset id (integer).

        """
        if isinstance(changeset, Changeset):
            if changeset.id is not None:
                return str(changeset.id)
            else:
                raise ValueError("This changeset has no id.")
        elif isinstance(changeset, int):
            return str(changeset)
        elif self.auto_changeset["enabled"]:
            self._auto_changeset_clear()
            if self._changeset is None:
                self._changeset = self.create_changeset()
                self._changeset.counter = 0
            self._changeset.counter += 1
            return str(self._changeset.id)
        else:
            raise TypeError("Auto_changeset is disabled and no valid changeset or its id was passed.")

    def _auto_changeset_clear(self, force=False):
        """ Check if the auto_changeset should be closed """
        if self._changeset is None:
            return False
        if force or self._changeset.counter >= self.auto_changeset["size"]:
            self.close_changeset(self._changeset)
            self._changeset = None
            return True

    def _format_payload(self, element, changeset_id, main_tag_only=False):
        """ Format payload data """
        payload = element.xml(asstring=False, strip=("user", "uid", "visible", "timestamp", "changeset"))
        for key in OSM._elements:
            for element in payload.getiterator(key):
                element.attrib["changeset"] = changeset_id
                if main_tag_only:
                    for child in element.getchildren():
                        element.remove(child)
        return ET.tostring(payload)

    ##################################################
    # API - capabilities                             #
    ##################################################
    @property
    def capabilities(self):
        """ OSM API capabilities """
        if self._capabilities is None:
            self._capabilities = self.get_capabilities()
        return self._capabilities

    def get_capabilities(self):
        """
        Download and return dictionary with OSM API capabilities.
        """
        capabilities = {}
        data = ET.XML(self.api.get("/api/capabilities"))
        for element in data.find("api").getchildren():
            capabilities[element.tag] = {}
            for key, value in element.attrib.items():
                try:
                    if int(value) == float(value):
                        capabilities[element.tag][key] = int(value)
                    else:
                        capabilities[element.tag][key] = float(value)
                except:
                    capabilities[element.tag][key] = value
        return capabilities

    ##################################################
    # API - map                                      #
    ##################################################
    def get_map(self, bbox):
        """
        OSM API map download.

        Return OSM instance.

        Arguments:
            bbox        --- List or tuple containing bbox borders.

        """
        path = API_PATH + "map?bbox={0},{1},{2},{3}".format(*bbox)
        data = self.api.get(path)
        data = OSM(data=ET.XML(data))
        return data

    ##################################################
    # API - Element: Read, Full, Version, History    #
    ##################################################
    def get_element(self, type_, id_, version=None, full=False):
        """
        Download OSM Element via API by type and id.

        Return Node/Way/Relation instance if full is False.
        Return OSM instance if full is True ignoring version.

        Arguments:
            type_       --- OSM Element type.
            id_         --- OSM Element id.

        Keyworded arguments:
            version     --- OSM Element version number, None (latest), or
                            '*' (complete history).
            full        --- Return full OSM instance with all referenced elements.

        """
        if type_ not in OSM._elements:
            raise ValueError("Type_ must be from {0}.".format(OSM._elements))
        path = API_PATH + "{0}/{1}".format(type_, id_)
        if full:
            path += "/full"
        elif isinstance(version, int):
            path += "/{0}".format(version)
        elif version == "*":
            path += "/history"
        elif version is not None:
            raise TypeError("Version must be integer, '*' or None.")
        data = self.api.get(path)
        data = OSM(data=ET.XML(data))
        if full:
            return data
        else:
            return data[type_][id_]

    def get_node(self, id_, version=None):
        """
        Download Node via API by id.

        Return Node instance.

        Arguments:
            id_         --- Node id.

        Keyworded arguments:
            version     --- Node version number, None (latest), or
                            '*' (complete history).

        """
        return self.get_element("node", id_, version=version)

    def get_way(self, id_, version=None, full=False):
        """
        Download Way via API by id.

        Return Way instance if full is False.
        Return OSM instance if full is True ignoring version.

        Arguments:
            id_         --- Way id.

        Keyworded arguments:
            version     --- Way version number, None (latest), or
                            '*' (complete history).
            full        --- Return full OSM instance with all referenced elements.

        """
        return self.get_element("way", id_, version=version, full=full)

    def get_relation(self, id_, version=None, full=False):
        """
        Download Relation via API by id.

        Return Relation instance if full is False.
        Return OSM instance if full is True ignoring version.

        Arguments:
            id_         --- Relation id.

        Keyworded arguments:
            version     --- Relation version number, None (latest), or
                            '*' (complete history).
            full        --- Return full OSM instance with all referenced elements.

        """
        return self.get_element("relation", id_, version=version, full=full)

    def get_history(self, element):
        """
        Download complete history of OSM Element.

        Return Node/Way/Relation instance.

        Arguments:
            element     --- Node/Way/Relation instance.
        """
        if not isinstance(element, (Node, Way, Relation)):
            raise TypeError("Element must be a Node, Way or Relation instance.")
        return self.get_element(element.name, element.id, version="*")

    ##################################################
    # API - Elements: Multi-fetch                    #
    ##################################################
    def get_elements(self, type_, ids):
        """
        Download OSM Elements via API by type and ids.

        Return dictionary of type {id: data}.

        Arguments:
            type_       --- OSM Element type.
            ids         --- List OSM Element ids.

        """
        if type_ not in OSM._elements:
            raise ValueError("Type_ must be from {0}.".format(OSM._elements))
        if not isinstance(ids, (list, tuple)):
            raise TypeError("Ids must be a list/tuple.")
        path = API_PATH + "{0}s?{0}s={1}".format(type_, ",".join((str(id) for id in ids)))
        data = self.api.get(path)
        data = OSM(data=ET.XML(data))
        return data[type_]

    def get_nodes(self, ids):
        """
        Download Nodes via API by ids.

        Return dictionary of type {id: data}.

        Arguments:
            ids         --- List Node ids.

        """
        return self.get_elements("node", ids)

    def get_ways(self, ids):
        """
        Download Ways via API by ids.

        Return dictionary of type {id: data}.

        Arguments:
            ids         --- List Way ids.

        """
        return self.get_elements("way", ids)

    def get_relations(self, ids):
        """
        Download Relations via API by ids.

        Return dictionary of type {id: data}.

        Arguments:
            ids         --- List Relation ids.

        """
        return self.get_elements("relation", ids)

    ##################################################
    # API - Elements: parent relations and ways      #
    ##################################################
    def get_element_parent_relations(self, type_, id_):
        """
        Download Relations via API that reference OSM Element.

        Return dictionary of type {id: data}.

        Arguments:
            type_       --- OSM Element type.
            id_         --- OSM Element id.

        """
        if type_ not in OSM._elements:
            raise ValueError("Type_ must be from {0}.".format(OSM._elements))
        path = API_PATH + "{0}/{1}/relations".format(type_, id_)
        data = self.api.get(path)
        data = OSM(data=ET.XML(data))
        return data["relation"]

    def get_parent_relations(self, element):
        """
        Download Relations via API that reference OSM Element.

        Return dictionary of type {id: data}.

        Arguments:
            element     --- Node/Way/Relation instance.

        """
        if not isinstance(element, (Node, Way, Relation)):
            raise TypeError("Element must be a Node, Way or Relation instance.")
        return self.get_element_parent_relations(element.name, element.id)

    def get_node_parent_ways(self, id_):
        """
        Download Ways via API that reference Node.

        Return dictionary of type {id: data}.

        Arguments:
            id_         --- Node id.

        """
        path = API_PATH + "node/{0}/ways".format(id_)
        data = self.api.get(path)
        data = OSM(data=ET.XML(data))
        return data["way"]

    def get_parent_ways(self, element):
        """
        Download Ways via API that reference Node.

        Return dictionary of type {id: data}.

        Arguments:
            element     --- Node instance.

        """
        if not isinstance(element, Node):
            raise TypeError("Element must be a Node instance.")
        return self.get_node_parent_ways(element.id)

    ##################################################
    # API - Changesets                               #
    ##################################################
    def get_changeset(self, id_, full=False):
        """
        Download Changeset via API by id.

        Return Changeset instance if full is False.
        Return OSC instance if full is True.

        Arguments:
            id_         --- Changeset id.

        Keyworded arguments:
            full        --- Return full OSC instance with all changes.

        """
        path = API_PATH + "changeset/{0}".format(id_)
        if full:
            path += "/download"
        data = self.api.get(path)
        if full:
            return Osc(data=ET.XML(data))
        else:
            return Changeset(data=ET.XML(data).find("changeset"))

    def search_changeset(self, bbox=None, user=None, display_name=None, time=None, status=None):
        """
        Search for Changeset via API by bbox, user or display_name, time, status.

        Return dictionary of type {id: data}.

        Keyworded arguments:
            bbox            --- List or tuple containing bbox borders.
            user            --- OSM user id.
            display_name    --- OSM user name.
            time            --- String, list/tuple containing time.
            status          --- Open or closed changesets.

        """
        params = []
        if bbox is not None:
            params.append("bbox={0},{1},{2},{3}".format(*bbox))
        if user is not None:
            params.append("user={0}".format(user))
        elif display_name is not None:
            params.append("display_name={0}".format(display_name))
        if time is not None:
            if isinstance(time, str):
                params.append("time={0}".format(time))
            else:
                params.append("time={0}".format(",".join(time)))
        if status in ("open", "closed"):
            params.append(status)
        path = API_PATH + "changesets?{0}".format("&".join(params))
        data = self.api.get(path)
        result = {}
        for element in ET.XML(data).findall("changeset"):
            changeset = Changeset(data=element)
            result[changeset.id] = changeset
        return result

    def create_changeset(self, changeset=None, comment=None):
        """
        Create Changeset via API.

        Return Changeset instance.

        Keyworded arguments:
            changeset   --- Changeset instance or None (create new).
            comment     --- Comment tag.

        """
        if changeset is None:
            # No Changset instance provided => create new one
            changeset = Changeset()
            changeset.tag = self.auto_changeset["tag"]
            if comment is not None:
                changeset.tag["comment"] = comment
        elif not isinstance(changeset, Changeset):
            raise TypeError("Changeset must be Changeset instance or None.")
        payload = "<osm>{0}</osm>".format(changeset.xml(asstring=True))
        path = API_PATH + "changeset/create"
        changeset_id = self.api.put(path, payload)
        changeset.attrib["id"] = int(changeset_id)
        return changeset

    def update_changeset(self, changeset):
        """
        Update Changeset via API.

        Return updated Changeset instance.

        Arguments:
            changeset   --- Changeset instance.

        """
        if not isinstance(changeset, Changeset):
            raise TypeError("Changeset must be Changeset instance.")
        payload = "<osm>{0}</osm>".format(changeset.xml(asstring=True))
        path = API_PATH + "changeset/{0}".format(changeset.id)
        data = self.api.put(path, payload)
        return Changeset(data=ET.XML(data).find("changeset"))

    def close_changeset(self, changeset):
        """
        Close Changeset via API.

        Arguments:
            changeset   --- Changeset instance or changeset id.

        """
        # Temporarily disable auto_changeset
        old = self.auto_changeset["enabled"]
        self.auto_changeset["enabled"] = False
        changeset_id = self._changeset_id(changeset)
        self.auto_changeset["enabled"] = old
        path = API_PATH + "changeset/{0}/close".format(changeset_id)
        self.api.put(path)

    ##################################################
    # API - Diff upload                              #
    ##################################################
    def upload_diff(self, osc, changeset=None):
        """
        Diff upload via API.

        Return {type: {old_id: returned_data} }

        Arguments:
            osc         --- OSC instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        changeset_id = self._changeset_id(changeset)
        if not isinstance(osc, OSC):
            raise TypeError("Osc must be OSC instance.")
        payload = self._format_payload(osc, changeset)
        path = API_PATH + "changeset/{0}/upload".format(changeset_id)
        data = self.api.post(path, payload)
        if not self._auto_changeset_clear(force=True):
            self.close_changeset(int(changeset_id))
        data = ET.XML(data)
        result = {"node":{}, "way":{}, "relation":{}}
        for key in OSM._elements:
            for element in data.findall(key):
                old_id = int(element.attrib["old_id"])
                result[element.tag][old_id] = {"old_id":old_id}
                if "new_id" in element.attrib:
                    result[element.tag][old_id]["new_id"] = int(element.attrib["new_id"])
                if "new_version" in element.attrib:
                    result[element.tag][old_id]["new_version"] = int(element.attrib["new_version"])
        return result

    ##################################################
    # API - Element: Create                          #
    ##################################################
    def create_element(self, element, changeset=None):
        """
        Create OSM Element via API.

        Return Node/Way/Relation instance.

        Arguments:
            element     --- Node/Way/Relation instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(element, (Node, Way, Relation)):
            raise TypeError("Element must be Node, Way or Relation instance.")
        changeset_id = self._changeset_id(changeset)
        path = API_PATH + "{0}/{1}/create".format(element.name, element.id)
        payload = "<osm>{0}</osm>".format(self._format_payload(element, changeset_id))
        data = self.api.put(path, payload)
        self._auto_changeset_clear()
        element.clear_history()
        element.attrib["version"] = int(data)
        element.attrib["changeset"] = int(changeset_id)
        return element

    def create_node(self, node, changeset=None):
        """
        Create Node via API.

        Return Node instance.

        Arguments:
            element     --- Node instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(node, Node):
            raise TypeError("Node must be Node instance.")
        return self.create_element(node, changeset)

    def create_way(self, way, changeset=None):
        """
        Create Way via API.

        Return Way instance.

        Arguments:
            element     --- Way instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(way, Way):
            raise TypeError("Way must be Way instance.")
        return self.create_element(way, changeset)

    def create_relation(self, relation, changeset=None):
        """
        Create Relation via API.

        Return Relation instance.

        Arguments:
            element     --- Relation instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(relation, Relation):
            raise TypeError("Relation must be Relation instance.")
        return self.create_element(relation, changeset)

    ##################################################
    # API - Element: Update                          #
    ##################################################
    def update_element(self, element, changeset=None):
        """
        Update OSM Element via API.

        Return Node/Way/Relation instance.

        Arguments:
            element     --- Node/Way/Relation instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(element, (Node, Way, Relation)):
            raise TypeError("Element must be Node, Way or Relation instance.")
        changeset_id = self._changeset_id(changeset)
        path = API_PATH + "{0}/{1}".format(element.name, element.id)
        payload = "<osm>{0}</osm>".format(self._format_payload(element, changeset_id))
        data = self.api.put(path, payload)
        self._auto_changeset_clear()
        element.clear_history()
        element.attrib["version"] = int(data)
        element.attrib["changeset"] = int(changeset_id)
        return element

    def update_node(self, node, changeset=None):
        """
        Update Node via API.

        Return Node instance.

        Arguments:
            element     --- Node instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(node, Node):
            raise TypeError("Node must be Node instance.")
        return self.update_element(node, changeset)

    def update_way(self, way, changeset=None):
        """
        Update Way via API.

        Return Way instance.

        Arguments:
            element     --- Way instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(way, Way):
            raise TypeError("Way must be Way instance.")
        return self.update_element(way, changeset)

    def update_relation(self, relation, changeset=None):
        """
        Update Relation via API.

        Return Relation instance.

        Arguments:
            element     --- Relation instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(relation, Relation):
            raise TypeError("Relation must be Relation instance.")
        return self.update_element(relation, changeset)

    ##################################################
    # API - Element: Delete                          #
    ##################################################
    def delete_element(self, element, changeset=None):
        """
        Delete OSM Element via API.

        Return Node/Way/Relation instance.

        Arguments:
            element     --- Node/Way/Relation instance or list/tuple containing
                            (type, id).

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not (isinstance(element, (Node, Way, Relation)) or (isinstance(element, (list, tuple)) and len(element) == 2 and element[0] in OSM._elements and isinstance(element[1], int))):
            raise TypeError("Element must be Node, Way, Relation instance or (type, id).")
        elif isinstance(element, (list, tuple)):
            element = self.get_element(element[0], element[1])
        changeset_id = self._changeset_id(changeset)
        path = API_PATH + "{0}/{1}".format(element.name, element.id)
        payload = "<osm>{0}</osm>".format(self._format_payload(element, changeset_id, main_tag_only=True))
        data = self.api.delete(path, payload)
        self._auto_changeset_clear()
        element.clear_history()
        element.attrib["version"] = int(data)
        element.attrib["changeset"] = int(changeset_id)
        element.attrib["visible"] = False
        return element

    def delete_node(self, node, changeset=None):
        """
        Delete Node via API.

        Return Node instance.

        Arguments:
            element     --- Node instance or id.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if isinstance(node, int):
            node = ("node", node)
        elif not isinstance(node, Node):
            raise TypeError("Node must be Node instance or integer.")
        return self.delete_element(node, changeset)

    def delete_way(self, way, changeset=None):
        """
        Delete Way via API.

        Return Way instance.

        Arguments:
            element     --- Way instance or id.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if isinstance(way, int):
            way = ("way", way)
        elif not isinstance(way, Way):
            raise TypeError("Way must be Way instance or integer.")
        return self.delete_element(way, changeset)

    def delete_relation(self, relation, changeset=None):
        """
        Delete Relation via API.

        Return Relation instance.

        Arguments:
            element     --- Relation instance or id.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if isinstance(relation, int):
            relation = ("relation", relation)
        elif not isinstance(relation, Relation):
            raise TypeError("Relation must be Relation instance or integer.")
        return self.delete_element(relation, changeset)


    ##################################################
    # XAPI                                           #
    ##################################################
    def search(self, type_="*", tags=None, bbox=None, full=True):
        """
        Search for OSM Elements via XAPI.

        Return OSM instance if full is True.
        Return {id: data} or {type: {id: data}} if full is False.

        Arguments:
            type_       --- OSM Element type.

        Keyworded arguments:
            tags        --- Search for OSM Elements with given tags.
            bbox        --- Search for OSM Elements in the bbox.
            full        --- Return OSM instance instead of dictionary with
                            found OSM Elements.

        """
        if type_ not in OSM._elements and type_ != "*":
            raise ValueError("Type_ must be from {0} or '*'.".format(OSM._elements))
        path = API_PATH + "{0}".format(type_)
        if tags is not None:
            path += "[{0}]".format(tags)
        if bbox is not None:
            path += "[bbox={0},{1},{2},{3}]".format(*bbox)
        data = None
        try:
            data = self.cache.get(path)
        except IOError:
            pass
        if data is None:
            data = self.xapi.get(path)
            self.cache.put(path, data)
        data = OSM(data=ET.XML(data))
        if full:
            return data
        elif type_ == "*":
            return dict(data)
        else:
            return data[type_]

    def search_node(self, tags=None, bbox=None, full=True):
        """
        Search for Nodes via XAPI.

        Return OSM instance if full is True.
        Return {id: data} if full is False.

        Keyworded arguments:
            tags        --- Search for Nodes with given tags.
            bbox        --- Search for Nodes in the bbox.
            full        --- Return OSM instance instead of dictionary with
                            found Nodes.

        """
        return self.search("node", tags=tags, bbox=bbox, full=full)

    def search_way(self, tags=None, bbox=None, full=True):
        """
        Search for Ways via XAPI.

        Return OSM instance if full is True.
        Return {id: data} if full is False.

        Keyworded arguments:
            tags        --- Search for Ways with given tags.
            bbox        --- Search for Ways in the bbox.
            full        --- Return OSM instance instead of dictionary with
                            found Ways.

        """
        return self.search("way", tags=tags, bbox=bbox, full=full)

    def search_relation(self, tags=None, bbox=None, full=True):
        """
        Search for Relations via XAPI.

        Return OSM instance if full is True.
        Return {id: data} if full is False.

        Keyworded arguments:
            tags        --- Search for Relations with given tags.
            bbox        --- Search for Relations in the bbox.
            full        --- Return OSM instance instead of dictionary with
                            found Relations.

        """
        return self.search("relation", tags=tags, bbox=bbox, full=full)



############################################################
### HTTP (X)API class.                                   ###
############################################################

class WebAPI:
    """
    Access to the HTTP (X)API.

    Attributes:
        server      --- Domain name of the (X)API server.
        credentials --- Credentials for 'edit' requests to the server.

    Methods:
        request     --- HTTP request.
        get         --- GET HTTP request.
        put         --- PUT HTTP request.
        delete      --- DELETE HTTP request.
        post        --- POST HTTP request.
    """

    def __init__(self, server, credentials=None):
        """
        Arguments:
            server      --- Domain name of the (X)API server.

        Keyworded arguments:
            credentials --- Credentials for 'edit' requests to the server,
                            expecting None or {'user':'username', 'password':'secret'}.

        """
        self._log = getLogger("osmt.webpapi")
        self.server = server
        self.credentials = credentials
        self._headers = {}
        self._headers["User-agent"] = "osmt/{0}".format(__version__)

    def request(self, server, path, method="GET", payload=None, auth=False, retry=10):
        """
        Perform HTTP request and handle possible redirection, on error retry.

        Raise ValueError on invalid credentials and auth=True.
        Return downloaded body as string or raise ApiError.

        Arguments:
            server      --- Domain name of the server.
            path        --- Path to download from server.

        Keyworded arguments:
            method      --- HTTP request method.
            payload     --- Dictionary containing data to send with request.
            auth        --- Should we authenticate?
            retry       --- Number of retries on error.

        """
        self._log.debug("{0}({5}) {1}{2} ({4}) << {3}".format(method, server, path, payload is not None, auth, retry))
        headers = dict(self._headers)
        if auth:
            if self.credentials is None or "user" not in self.credentials or "password" not in self.credentials:
                self._log.critical("Invalid credenials.")
                raise ValueError("Invalid credentials.")
            else:
                headers["Authorization"] = "Basic " + base64encode("{user}:{password}".format(**self.credentials).encode("utf8")).decode().strip()
        if payload is not None:
            payload = payload.encode("utf8")
        connection = HTTPConnection(server)
        connection.connect()
        connection.request(method, path, payload, headers)
        response = connection.getresponse()
        if response.status == 200:
            body = response.read().decode("utf8", "replace")
            connection.close()
            return body.encode("utf8")
        elif response.status in (301, 302, 303, 307):
            # Try to redirect
            connection.close()
            url = response.getheader("Location")
            if url is None:
                self._log.error("Got code {0}, but no location header.".format(response.status))
                raise ApiError(response.status, response.reason, "")
            url = unquote(url)
            self._log.debug("Redirecting to {0}".format(url))
            url = url.split("/", 3)
            server = url[2]
            path = "/" + url[3]
            return self.request(server, path, method=method, payload=payload, auth=auth, retry=retry)
        elif response.status in (404, 410):
            connection.close()
            self._log.error("Could not find {0}{1}".format(server, path))
            raise ApiError(response.status, response.reason, "")
        else:
            body = response.read().decode("utf8", "replace").strip()
            connection.close()
            if retry <= 0:
                self._log.error("Could not download {0}{1}".format(server, path))
                raise ApiError(response.status, response.reason, body)
            else:
                wait = 30
                self._log.warn("Got error {0} ({1})... will retry in {2} seconds.".format(response.status, response.reason, wait))
                self._log.debug(body)
                sleep(wait)
                return self.request(server, path, method=method, payload=payload, auth=auth, retry=retry-1)

    def get(self, path):
        """
        GET request.

        Arguments:
            path        --- Path to download.

        """
        return self.request(self.server, path)

    def put(self, path, data=None):
        """
        PUT request.

        Arguments:
            path        --- Path to download.

        Keyworded arguments:
            data        --- Dictionary containing data to send with request.

        """
        return self.request(self.server, path, method="PUT", payload=data, auth=True)

    def delete(self, path, data):
        """
        DELETE request.

        Arguments:
            path        --- Path to download.

        Keyworded arguments:
            data        --- Dictionary containing data to send with request.

        """
        return self.request(self.server, path, method="DELETE", payload=data, auth=True)

    def post(self, path, data):
        """
        POST request.

        Arguments:
            path        --- Path to download.

        Keyworded arguments:
            data        --- Dictionary containing data to send with request.

        """
        return self.request(self.server, path, method="POST", payload=data, auth=True)



############################################################
### Cache for XAPI requests.                             ###
############################################################

class DummyCache:
    """
    Dummy cache - defines basic interface for cache classes.

    Methods:
        get         --- Retrieve data from cache.
        put         --- Store data to cache.

    """

    def get(self, path):
        """
        Retrieve data from cache.

        Raise IOError if data could not be retrieved.

        Arguments:
            path        --- Key by which the data should be identified.

        """
        raise IOError((0, "Invalid cache instance"))

    def put(self, path, data):
        """
        Save data to cache.

        Arguments:
            path        --- Key by which the data should be identified.
            data        --- Data to store.
        """
        pass


class FileCache(DummyCache):
    """
    Cache that stores data as files in designated directory.

    Methods:
        get         --- Retrieve data from cache.
        put         --- Store data to cache.

    """

    def __init__(self, directory):
        """
        Raise IOError if the passed directory is invalid.

        Arguments:
            directory   --- Directory for storing cache files.

        """
        self._log = getLogger("osmt.cache.file")
        if directory is None or not os.path.isdir(directory):
            self._log.error("Directory not found '{0}'.".format(directory))
            raise IOError((1, "Directory not found.", directory))
        self.directory = directory

    def _file(self, path):
        return os.path.join(self.directory, path.replace("/", "_"))

    def get(self, path):
        """
        Retrieve data from cache.

        Raise IOError if data could not be retrieved.

        Arguments:
            path        --- Key by which the data should be identified.

        """
        file = self._file(path)
        if not os.path.isfile(file):
            self._log.debug("Did not found {0}".format(file))
            raise IOError((2, "File not found.", file))
        else:
            self._log.debug("Loading from {0}".format(file))
            with open(file) as fp:
                return fp.read()

    def put(self, path, data):
        """
        Save data to cache.

        Arguments:
            path        --- Key by which the data should be identified.
            data        --- Data to store.
        """
        file = self._file(path)
        self._log.debug("Saving to {0}".format(file))
        with open(file, "w") as fp:
            fp.write(data)



############################################################
### Wrappers for OSM Elements and documents.             ###
############################################################

class XMLElement:
    """
    Base wrapper for XML Elements.

    Methods:
        xml         --- Return XML representation of the wrapper.

    """

    def __init__(self, data=None):
        """
        Keyworded arguments:
            data        --- Data for wrapper construction - ET.Element, dict, or
                            None.

        """
        if ET.iselement(data):
            self._from_XML(data)
        elif isinstance(data, dict):
            self._from_dict(data)
        else:
            self.clear()

    def clear(self):
        pass

    def _from_XML(self, xml):
        self.clear()

    def _from_dict(self, dictionary):
        self.clear()

    def _parse_attributes(self, element):
        """
        Return dictionary with attributes of XML element.

        Extracts attributes of XML element and converts them to appropriate
        type.

        Arguments:
            element     --- ET.Element instance.

        """
        attributes = dict(element.attrib)
        for key, value in attributes.items():
            if key in ("uid", "changeset", "version", "id", "ref"):
                attributes[key] = int(value)
            elif key in ("lat", "lon"):
                attributes[key] = float(value)
            elif key in ("open", "visible"):
                attributes[key] = value=="true"
        return attributes

    def _unparse_attributes(self, data, strip=[]):
        """
        Return dictionary with string values of attributes.

        Arguments:
            data        --- Dictionary of attributes.

        Keyworded arguments:
            strip       --- List of attribute names that should be left out
                            from the returned dictionary.

        """
        attrib = {}
        for key, value in data.items():
            if key in strip:
                continue
            attrib[key] = str(value)
            if isinstance(value, bool):
                attrib[key] = attrib[key].lower()
        return attrib

    def xml(self, asstring=False, strip=[]):
        raise NotImplemented


class OSMElement(XMLElement):
    """
    Base wrapper for Node, Way, Relation, Changeset elements.

    Attributes:
        name        --- Name of OSM Element.
        id          --- Id of OSM Element.
        version     --- Version of OSM Element.
        attrib      --- Attributes of OSM Element.
        tag         --- Tags of OSM Element.
        history     --- Dictionary containing old versions of the same element.

    Methods:
        clear_history   --- Re-initialize the history of OSM Element.
        xml             --- Return XML representation of the wrapper.

    """

    name = None

    def clear(self):
        self.attrib = {}
        self.tag = {}
        self._post_init()

    def _from_XML(self, xml):
        self.attrib = self._parse_attributes(xml)
        self.tag = self._parse_tags(xml)
        self._post_init()

    def _from_dict(self, dictionary):
        self.attrib = dictionary.get("attrib", {})
        self.tag = dictionary.get("tag", {})
        self._post_init()

    def _post_init(self):
        self.clear_history()

    def _parse_tags(self, element):
        """
        Return dictionary with tags of OSM Element.

        Arguments:
            element     --- ET.Element instance.

        """
        tags = {}
        for tag in element.findall("tag"):
            key = tag.attrib["k"]
            value = tag.attrib["v"]
            tags[key] = value
        return tags

    @property
    def id(self):
        """ Return id of OSM Element. """
        return self.attrib.get("id")

    @property
    def version(self):
        """ Return version of OSM Element. """
        return self.attrib.get("version")

    def clear_history(self):
        """
        Re-initialize the history of OSM Element.

        """
        self.history = {}
        if self.version is not None:
            self.history[self.version] = self

    def xml(self, asstring=False, strip=[]):
        """
        Return XML representation of the wrapper.

        Keyworded arguments:
            asstring    --- Return string instead of ET.Element.
            strip       --- Strip all attributes in the list.

        """
        attrib = self._unparse_attributes(self.attrib, strip=strip)
        element = ET.Element(self.name, attrib)
        for tag in self.tag:
            ET.SubElement(element, "tag", {"k":tag, "v":self.tag[tag]})
        if asstring:
            return ET.tostring(element)
        else:
            return element


class Node(OSMElement):
    """
    Wrapper for Node element.

    Attributes:
        name        --- 'node'.

    """

    name = "node"
    _counter = 0

    def _post_init(self):
        OSMElement._post_init(self)
        if self.id is None:
            # Automatically asign id
            Node._counter -= 1
            self.attrib["id"] = Node._counter


class Way(OSMElement):
    """
    Wrapper for Way element.

    Implements __contains__ method for Node instance.

    Attributes:
        name        --- 'way'.

    Methods:
        xml         --- Return XML representation of the wrapper.

    """

    name = "way"
    _counter = 0

    def clear(self):
        self.nd = []
        OSMElement.clear(self)

    def _from_XML(self, xml):
        self.nd = self._parse_nds(xml)
        OSMElement._from_XML(self, xml)

    def _from_dict(self, dictionary):
        self.nd = dictionary.get("nd", [])
        OSMElement._from_dict(self, dicitonary)

    def _post_init(self):
        OSMElement._post_init(self)
        if self.id is None:
            # Automatically asign id
            Way._counter -= 1
            self.attrib["id"] = Way._counter

    def _parse_nds(self, element):
        """
        Return list of node refs of the way.

        Arguments:
            element     --- ET.Element instance.

        """
        nds = []
        for nd in element.findall("nd"):
            nds.append(int(nd.attrib["ref"]))
        return nds

    def __contains__(self, item):
        if not isinstance(item, Node):
            raise NotImplemented
        return item.id in self.nd

    def xml(self, asstring=False, strip=[]):
        """
        Return XML representation of Way wrapper.

        Keyworded arguments:
            asstring    --- Return string instead of ET.Element.
            strip       --- Strip all attributes in the list.

        """
        element = OSMElement.xml(self, asstring=False, strip=strip)
        for nd in self.nd:
            ET.SubElement(element, "nd", {"ref":str(nd)})
        if asstring:
            return ET.tostring(element)
        else:
            return element


class Relation(OSMElement):
    """
    Wrapper for Relation element.

    Implements __contains__ method for Node/Way/Relation instance.

    Attributes:
        name        --- 'relation'.

    Methods:
        xml         --- Return XML representation of the wrapper.

    """

    name = "relation"
    _counter = 0

    def clear(self):
        self.member = []
        OSMElement.clear(self)

    def _from_XML(self, xml):
        self.member = self._parse_members(xml)
        OSMElement._from_XML(self, xml)

    def _from_dict(self, dictionary):
        self.member = dictionary.get("member", [])
        OSMElement._from_dict(self, dicitonary)

    def _post_init(self):
        OSMElement._post_init(self)
        if self.id is None:
            # Automatically asign id
            Relation._counter -= 1
            self.attrib["id"] = Relation._counter

    def _parse_members(self, element):
        """
        Return list of members of the relation.

        Arguments:
            element     --- ET.Element instance.

        """
        members = []
        for member in element.findall("member"):
            members.append(self._parse_attributes(member))
        return members

    def __contains__(self, item):
        if not isinstance(item, (Node, Way, Relation)):
            raise NotImplemented
        for member in self.member:
            if member["type"] == item.name and memeber["ref"] == item.id:
                return True
        return False

    def xml(self, asstring=False, strip=[]):
        """
        Return XML representation of Relation wrapper.

        Keyworded arguments:
            asstring    --- Return string instead of ET.Element.
            strip       --- Strip all attributes in the list.

        """
        element = OSMElement.xml(self, asstring=False, strip=strip)
        for member in self.member:
            attrib = self._unparse_attributes(member, strip=strip)
            ET.SubElement(element, "member", attrib)
        if asstring:
            return ET.tostring(element)
        else:
            return element


class Changeset(OSMElement):
    """
    Wrapper for Changeset element.

    Attributes:
        name        --- 'changeset'.

    """

    name = "changeset"

    def clear_history(self):
        """ Changesets have no history --> we don't need this """
        pass


class BaseDocument(XMLElement):
    """
    Base document wrapper.

    Implements Iterator interface and maps its attributes as dictionary items.

    """

    _elements = ()

    def _from_XML(self, xml):
        self.clear()
        for name in self._elements:
            for element in xml.findall(name):
                self._append_XML_element(element)

    def _append_XML_element(self, element):
        pass

    def __iter__(self):
        self._iterator = self._elements.__iter__()
        return self

    def __next__(self):
        item = self._iterator.__next__()
        data = (item, getattr(self, item))
        return data

    def __getitem__(self, name):
        if name in self._elements:
            return getattr(self, name)
        else:
            raise KeyError

    def get(self, name, default={}):
        try:
            return self.__getitem__(name)
        except KeyError:
            return default


class OSM(BaseDocument):
    """
    OSM XML document wrapper.

    Attributes:
        node        --- Dictionary of nodes {nodeId: Node}.
        way         --- Dictionary of ways {wayId: Way}.
        relation    --- Dictionary of relations {relationId: Relation}.

    Methods:
        xml         --- Return XML representation of the wrapper.

    """

    _elements = ("node", "way", "relation")

    def clear(self):
        self.node = {}
        self.way = {}
        self.relation = {}

    def _from_dict(self, dictionary):
        self.node = dicionary.get("node", {})
        self.way = dicionary.get("way", {})
        self.relation = dicionary.get("relation", {})

    def _append_XML_element(self, element):
        if element.tag not in self._elements:
            return
        container = getattr(self, element.tag)
        if element.tag == "node":
            element = Node(data=element)
        elif element.tag == "way":
            element = Way(data=element)
        elif element.tag == "relation":
            element = Relation(data=element)
        if element.id in container:
            container[element.id].history[element.version] = element
            element.history = container[element.id].history
            if element.version < container[element.id].version:
                max_id = max(element.history.keys())
                element = element.history[max_id]
        container[element.id] = element

    def xml(self, asstring=False, strip=[]):
        """
        Return XML representation of OSM XML document wrapper.

        Keyworded arguments:
            asstring    --- Return string instead of ET.Element.
            strip       --- Strip all attributes in the list.

        """
        element = ET.Element("osm", {"version":str(API_VERSION), "generator":"osmt"})
        for name in self._elements:
            osm_elements = getattr(self, name)
            for osm_element in osm_elements.values():
                element.append(osm_element.xml(asstring=False, strip=strip))
        if asstring:
            return ET.tostring(element)
        else:
            return element


class OSC(BaseDocument):
    """
    OSC XML document wrapper.

    Attributes:
        create      --- OSM instance containing elements to create.
        modify      --- OSM instance containing elements to modify.
        delete      --- OSM instance containing elements to delete.

    Methods:
        xml         --- Return XML representation of the wrapper.

    """

    _elements = ("create", "modify", "delete")

    def clear(self):
        self.create = OSM()
        self.modify = OSM()
        self.delet = OSM()

    def _from_dict(self, dictionary):
        self.create = dicionary.get("create", OSM())
        self.modify = dicionary.get("modify", OSM())
        self.delete = dicionary.get("delete", OSM())

    def _append_XML_element(self, element):
        osm = getattr(self, element.name)
        for element in element.getchildren():
            osm._append_XML_element(element)

    def xml(self, asstring=False, strip=[]):
        """
        Return XML representation of OSC XML document wrapper.

        Keyworded arguments:
            asstring    --- Return string instead of ET.Element.
            strip       --- Strip all attributes in the list.

        """
        element = ET.Element("osmChange", {"version":str(API_VERSION), "generator":"osmt"})
        for action in self._elements:
            action_element = getattr(self, action).xml(asstring=False, strip=strip)
            if len(action_element.getchildren()) > 0:
                action_element.tag = action
                element.append(action_element)
        if asstring:
            return ET.tostring(element)
        else:
            return element



############################################################
### Exceptions.                                          ###
############################################################

class ApiError(Exception):
    """
    OSM API exception.

    Attributes:
        status      --- HTTP status code returned by API.
        reason      --- The reason for returned status code.
        payload     --- Data sent to API with request.

    """

    def __init__(self, status, reason, payload):
        """
        Arguments:
            status      --- HTTP status code returned by API.
            reason      --- The reason for returned status code.
            payload     --- Data sent to API with request.

        """
        self.status = status
        self.reason = reason
        self.payload = payload

    def __str__(self):
        return "Request failed: {0} ({1}) << {2}".format(self.status, self.reason, self.payload)