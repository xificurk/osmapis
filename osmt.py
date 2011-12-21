# -*- coding: utf-8 -*-
"""
Osmt is a set of tools for accessing and manipulating OSM data via (Overpass) API.

Classes:
    QHS             --- Interface for Quick History Service.
    OverpassAPI     --- OSM Overpass API interface.
    API             --- OSM API interface.
    HTTPClient      --- Interface for accessing data over HTTP.
    NullCache       --- Null dummy cache.
    FileCache       --- Cache that stores data as files in a designated directory.
    Node            --- Node wrapper.
    Way             --- Way wrapper.
    Relation        --- Relation wrapper.
    Changeset       --- Changeset wrapper.
    OSM             --- OSM XML document wrapper.
    OSC             --- OSC XML document wrapper.
    APIError        --- OSM API exception.

"""

__author__ = "Petr Morávek (xificurk@gmail.com)"
__copyright__ = "Copyright (C) 2010 Petr Morávek"
__license__ = "LGPL 3.0"

__version__ = "0.7.2"

from abc import ABCMeta, abstractmethod, abstractproperty
from base64 import b64encode
from collections import MutableSet, MutableMapping
from hashlib import sha1
from itertools import chain
import logging
import os
import os.path
from time import sleep
import xml.etree.cElementTree as ET
try:
    from http.client import HTTPConnection
except ImportError:
    from httplib import HTTPConnection
try:
    from urllib.parse import unquote, urlencode
except ImportError:
    from urllib import unquote, urlencode


__all__ = ["QHS",
           "OverpassAPI",
           "API",
           "HTTPClient",
           "NullCache",
           "FileCache",
           "Node",
           "Way",
           "Relation",
           "Changeset",
           "OSM",
           "OSC",
           "APIError"]


logging.getLogger('osmt').addHandler(logging.NullHandler())


############################################################
### HTTPClient class.                                    ###
############################################################

class HTTPClient(object):
    """
    Interface for accessing data over HTTP.

    Attributes:
        headers         --- Default headers for HTTP request.

    Methods:
        request     --- HTTP request.

    """

    def __new__(cls, *p, **k):
        raise TypeError("This class cannot be instantionalized.")

    headers = {}
    headers["User-agent"] = "osmt/{0}".format(__version__)
    log = logging.getLogger("osmt.http")

    @classmethod
    def request(cls, server, path, method="GET", headers={}, payload=None, retry=10):
        """
        Perform HTTP request and handle possible redirection, on error retry.

        Raise ValueError on invalid credentials and auth=True.
        Return downloaded body as string or raise APIError.

        Arguments:
            server      --- Domain name of HTTP server.
            path        --- Path on server.

        Keyworded arguments:
            method      --- HTTP request method.
            headers     --- Additional HTTP headers.
            payload     --- Dictionary containing data to send with request.
            retry       --- Number of re-attempts on error.

        """
        cls.log.debug("{}({}) {}{} << {}".format(method, retry, server, path, payload is not None))
        req_headers = dict(cls.headers)
        req_headers.update(headers)
        if payload is not None and not isinstance(payload, bytes):
            payload = payload.encode("utf-8")
        connection = HTTPConnection(server)
        connection.connect()
        connection.request(method, path, payload, req_headers)
        response = connection.getresponse()
        if response.status == 200:
            body = response.read()
            connection.close()
            if server == OverpassAPI.server and response.getheader("Content-Type") != "application/osm3s+xml":
                # Overpass API returns always status 200, grr!
                raise APIError(response.status, "Unexpected Content-type {}".format(response.getheader("Content-Type")), body.decode("utf-8", "replace").strip())
            return body
        elif response.status in (301, 302, 303, 307):
            # Try to redirect
            connection.close()
            url = response.getheader("Location")
            if url is None:
                cls.log.error("Got code {}, but no location header.".format(response.status))
                raise APIError(response.status, response.reason, "")
            url = unquote(url)
            cls.log.debug("Redirecting to {}".format(url))
            url = url.split("/", 3)
            server = url[2]
            path = "/" + url[3]
            return cls.request(server, path, method=method, headers=headers, payload=payload, retry=retry)
        elif 400 <= response.status < 500:
            connection.close()
            cls.log.error("Could not find {}{}".format(server, path))
            raise APIError(response.status, response.reason, "")
        else:
            body = response.read().decode("utf-8", "replace").strip()
            connection.close()
            if retry <= 0:
                cls.log.error("Could not download {}{}".format(server, path))
                raise APIError(response.status, response.reason, body)
            else:
                wait = 30
                cls.log.warn("Got error {} ({})... will retry in {} seconds.".format(response.status, response.reason, wait))
                cls.log.debug(body)
                sleep(wait)
                return cls.request(server, path, method=method, headers=headers, payload=payload, retry=retry-1)



############################################################
### API classes                                          ###
############################################################

class BaseReadAPI(object):
    """
    Abstract class for read-only API operations.

    Abstract methods:
        get_bbox            --- OSM bbox download.
        get_element         --- Download OSM primitive by type, id and optionally version.
        get_element_full    --- Download OSM primitive by type, id and all primitives that references.
        get_elements        --- Download OSM primitives by type, ids.
        get_element_rels    --- Download Relations that reference OSM primitive.
        get_node_ways       --- Download Ways that reference Node by id or wrapper.

    Methods:
        get_node            --- Download Node by id and optionally version.
        get_way             --- Download Way by id and optionally version.
        get_relation        --- Download Relation by id and optionally version.
        get_history         --- Download complete history of OSM primitive wrapper.
        get_way_full        --- Download Way by id and all referenced nodes.
        get_relation_full   --- Download Relation by id and all referenced members.
        get_full            --- Download OSM primitive and all primitives that references.
        get_nodes           --- Download Nodes by ids.
        get_ways            --- Download Ways by ids.
        get_relations       --- Download Relations by ids.
        get_node_rels       --- Download Relations that reference Node.
        get_way_rels        --- Download Relations that reference Way.
        get_relation_rels   --- Download Relations that reference Relation.
        get_rels            --- Download Relations that reference OSM primitive wrapper.

    """
    __metaclass_ = ABCMeta

    @abstractmethod
    def get_bbox(self, left, bottom, right, top):
        """
        OSM bbox download.

        Return OSM instance.

        Arguments:
            left        --- Left boundary.
            bottom      --- Bottom boundary.
            right       --- Right boundary.
            top         --- Top boundary.

        """
        raise NotImplementedError

    @abstractmethod
    def get_element(self, type_, id_, version=None):
        """
        Download OSM primitive by type, id and optionally version.

        Return Node/Way/Relation instance.

        Arguments:
            type_       --- OSM primitive type.
            id_         --- OSM primitive id.

        Keyworded arguments:
            version     --- OSM primitive version number, None (latest), or
                            '*' (complete history).

        """
        raise NotImplementedError

    def get_node(self, id_, version=None):
        """
        Download Node by id and optionally version.

        Return Node instance.

        Arguments:
            id_         --- Node id.

        Keyworded arguments:
            version     --- Node version number, None (latest), or
                            '*' (complete history).

        """
        return self.get_element("node", id_, version=version)

    def get_way(self, id_, version=None):
        """
        Download Way by id and optionally version.

        Return Way instance.

        Arguments:
            id_         --- Way id.

        Keyworded arguments:
            version     --- Way version number, None (latest), or
                            '*' (complete history).

        """
        return self.get_element("way", id_, version=version)

    def get_relation(self, id_, version=None):
        """
        Download Relation by id.

        Return Relation instance.

        Arguments:
            id_         --- Relation id.

        Keyworded arguments:
            version     --- Relation version number, None (latest), or
                            '*' (complete history).

        """
        return self.get_element("relation", id_, version=version)

    def get_history(self, element):
        """
        Download complete history of OSM primitive.

        Return Node/Way/Relation instance.

        Arguments:
            element     --- Node/Way/Relation instance.

        """
        if not isinstance(element, (Node, Way, Relation)):
            raise TypeError("Element must be a Node, Way or Relation instance.")
        return self.get_element(element.TAG_NAME(), element.id, version="*")

    @abstractmethod
    def get_element_full(self, type_, id_):
        """
        Download OSM primitive by type, id and all primitives that references.

        Return OSM instance.

        Arguments:
            type_       --- OSM primitive type.
            id_         --- OSM primitive id.

        """
        raise NotImplementedError

    def get_way_full(self, id_):
        """
        Download Way by id and all referenced nodes.

        Return OSM instance.

        Arguments:
            id_         --- Way id.

        """
        return self.get_element_full("way", id_)

    def get_relation_full(self, id_):
        """
        Download Way by id and all referenced members.

        Return OSM instance.

        Arguments:
            id_         --- Relation id.

        """
        return self.get_element_full("relation", id_)

    def get_full(self, element):
        """
        Download OSM primitive and all primitives that references.

        Return OSM instance.

        Arguments:
            element     --- Node/Way/Relation instance.

        """
        if not isinstance(element, (Node, Way, Relation)):
            raise TypeError("Element must be a Node, Way or Relation instance.")
        return self.get_element_full(element.TAG_NAME(), element.id)

    @abstractmethod
    def get_elements(self, type_, ids):
        """
        Download OSM primitives by type, ids.

        Return OSM instance.

        Arguments:
            type_       --- OSM primitive type.
            ids         --- Iterable with ids.

        """
        raise NotImplementedError

    def get_nodes(self, ids):
        """
        Download Nodes by ids.

        Return OSM instance.

        Arguments:
            ids         --- Iterable with ids.

        """
        return self.get_elements("node", ids)

    def get_ways(self, ids):
        """
        Download Ways by ids.

        Return OSM instance.

        Arguments:
            ids         --- Iterable with ids.

        """
        return self.get_elements("way", ids)

    def get_relations(self, ids):
        """
        Download Relations by ids.

        Return OSM instance.

        Arguments:
            ids         --- Iterable with ids.

        """
        return self.get_elements("relation", ids)

    @abstractmethod
    def get_element_rels(self, type_, id_):
        """
        Download Relations that reference OSM primitive.

        Return OSM instance.

        Arguments:
            type_       --- OSM primitive type.
            id_         --- OSM primitive id.

        """
        raise NotImplementedError

    def get_node_rels(self, id_):
        """
        Download Relations that reference Node.

        Return OSM instance.

        Arguments:
            id_         --- Node id.

        """
        return self.get_element_rels("node", id_)

    def get_way_rels(self, ids):
        """
        Download Relations that reference Way.

        Return OSM instance.

        Arguments:
            id_         --- Way id.

        """
        return self.get_element_rels("way", ids)

    def get_relation_rels(self, id_):
        """
        Download Relations that reference Relation.

        Return OSM instance.

        Arguments:
            id_         --- Relation id.

        """
        return self.get_element_rels("relation", ids)

    def get_rels(self, element):
        """
        Download Relations that reference OSM primitive wrapper.

        Return OSM instance.

        Arguments:
            element     --- Node/Way/Relation instance.

        """
        if not isinstance(element, (Node, Way, Relation)):
            raise TypeError("Element must be a Node, Way or Relation instance.")
        return self.get_element_rels(element.TAG_NAME(), element.id)

    @abstractmethod
    def get_node_ways(self, element):
        """
        Download Ways that reference Node by id or wrapper.

        Return OSM instance.

        Arguments:
            element     --- Node wrapper or id.

        """
        raise NotImplementedError


class BaseWriteAPI(object):
    """
    Abstract class for write API operations.

    Abstract methods:
        upload_diff         --- Diff upload.
        create_element      --- Create OSM primitive.
        update_element      --- Update OSM primitive.
        delete_element      --- Delete OSM primitive.
        delete_elements     --- Delete OSM primitives by type and ids.

    Methods:
        create_node         --- Create Node.
        create_way          --- Create Way.
        create_relation     --- Create Relation.
        update_node         --- Update Node.
        update_way          --- Update Way.
        update_relation     --- Update Relation.
        delete_node         --- Delete Node.
        delete_way          --- Delete Way.
        delete_relation     --- Delete Relation.
        delete_nodes        --- Delete Nodes by ids.
        delete_ways         --- Delete Ways by ids.
        delete_relations    --- Delete Relations by ids.

    """
    __metaclass_ = ABCMeta

    @abstractmethod
    def upload_diff(self, osc, changeset=None):
        """
        Diff upload.

        Return {type: {old_id: returned_data} }

        Arguments:
            osc         --- OSC instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        raise NotImplementedError

    @abstractmethod
    def create_element(self, element, changeset=None):
        """
        Create OSM primitive.

        Return Node/Way/Relation instance.

        Arguments:
            element     --- Node/Way/Relation instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        raise NotImplementedError

    def create_node(self, element, changeset=None):
        """
        Create Node.

        Return Node instance.

        Arguments:
            element     --- Node instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(element, Node):
            raise TypeError("Element must be Node instance.")
        return self.create_element(element, changeset)

    def create_way(self, element, changeset=None):
        """
        Create Way.

        Return Way instance.

        Arguments:
            element     --- Way instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(element, Way):
            raise TypeError("Element must be Way instance.")
        return self.create_element(element, changeset)

    def create_relation(self, element, changeset=None):
        """
        Create Relation.

        Return Relation instance.

        Arguments:
            element     --- Relation instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(element, Relation):
            raise TypeError("Element must be Relation instance.")
        return self.create_element(element, changeset)

    @abstractmethod
    def update_element(self, element, changeset=None):
        """
        Update OSM primitive.

        Return Node/Way/Relation instance.

        Arguments:
            element     --- Node/Way/Relation instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        raise NotImplementedError

    def update_node(self, element, changeset=None):
        """
        Update Node.

        Return Node instance.

        Arguments:
            element     --- Node instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(element, Node):
            raise TypeError("Element must be Node instance.")
        return self.update_element(element, changeset)

    def update_way(self, element, changeset=None):
        """
        Update Way.

        Return Way instance.

        Arguments:
            element     --- Way instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(element, Way):
            raise TypeError("Element must be Way instance.")
        return self.update_element(element, changeset)

    def update_relation(self, element, changeset=None):
        """
        Update Relation.

        Return Relation instance.

        Arguments:
            element     --- Relation instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(element, Relation):
            raise TypeError("Element must be Relation instance.")
        return self.update_element(element, changeset)

    @abstractmethod
    def delete_element(self, element, changeset=None):
        """
        Delete OSM primitive.

        Return Node/Way/Relation instance.

        Arguments:
            element     --- Node/Way/Relation instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        raise NotImplementedError

    def delete_node(self, element, changeset=None):
        """
        Delete Node.

        Return Node instance.

        Arguments:
            element     --- Node instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(element, Node):
            raise TypeError("Element must be Node instance.")
        return self.delete_element(element, changeset)

    def delete_way(self, element, changeset=None):
        """
        Delete Way.

        Return Way instance.

        Arguments:
            element     --- Way instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(element, Way):
            raise TypeError("Element must be Way instance.")
        return self.delete_element(element, changeset)

    def delete_relation(self, element, changeset=None):
        """
        Delete Relation.

        Return Relation instance.

        Arguments:
            element     --- Relation instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(relation, Relation):
            raise TypeError("Relation must be Relation instance or integer.")
        return self.delete_element(element, changeset)

    def delete_elements(self, type_, ids, changeset=None):
        """
        Delete OSM primitives by type and ids.

        Return OSC instance.

        Arguments:
            type_       --- OSM primitive type.
            ids         --- Iterable of ids.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        raise NotImplementedError

    def delete_nodes(self, ids, changeset=None):
        """
        Delete Nodes by ids.

        Return OSC instance.

        Arguments:
            ids         --- Iterable of ids.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        return self.delete_elements("node", ids, changeset)

    def delete_ways(self, ids, changeset=None):
        """
        Delete Ways by ids.

        Return OSC instance.

        Arguments:
            ids         --- Iterable of ids.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        return self.delete_elements("way", ids, changeset)

    def delete_relations(self, ids, changeset=None):
        """
        Delete Relations by ids.

        Return OSC instance.

        Arguments:
            ids         --- Iterable of ids.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        return self.delete_elements("relation", ids, changeset)



class QHS(object):
    """
    Interface for Quick History Service.

    Attributes:
        http        --- Interface for accessing data over HTTP.
        server      --- Domain name of QHS.
        basepath    --- Path to the API on the server.
        version         --- Version of OSM API.

    Methods:
        request     --- Low-level method to retrieve data from server.
        problems    --- Check licensing problems.

    """

    http = HTTPClient
    version = 0.6
    server = "wtfe.gryph.de"
    basepath = "/api/{}/".format(version)

    def request(self, path, data):
        """
        Low-level method to retrieve data from server.

        Arguments:
            path        --- Currently only 'problems'.
            data        --- Data to send with the request.

        """
        path = "{}{}".format(self.basepath, path)
        payload = urlencode(data)
        return self.http.request(self.server, path, method="POST", payload=payload)

    def problems(self, element):
        """
        Check licensing problems.

        Returns the same type of object as passed in element argument. To each
        Node, Way and Relation instance will be added 'license' argument
        containing found problems, or None.

        Arguments:
            element     --- Data to check - Node, Way, Relation, or OSM instance.

        """
        if isinstance(element, (Node, Way, Relation)):
            query = {"{}s".format(element.TAG_NAME()): element.id}
        elif isinstance(element, OSM):
            query = {}
            for type_ in ("node", "way", "relation"):
                query["{}s".format(type_)] = ",".join(str(id_) for id_ in getattr(element, type_).keys())
        else:
            raise TypeError("Element must be a Node, Way, Relation or OSM instance.")

        result = self.request("problems", query)
        result = self._parse_problems(result)
        if isinstance(element, (Node, Way, Relation)):
            try:
                element.license = result[element.TAG_NAME()][element.id]
            except KeyError:
                element.license = None
        elif isinstance(element, OSM):
            for child in element:
                try:
                    child.license = result[child.TAG_NAME()][child.id]
                except KeyError:
                    child.license = None
        return element

    def _parse_problems(self, data):
        data = ET.XML(data)
        result = {"node": {}, "way": {}, "relation": {}}
        for type_, container in result.items():
            for element in data.findall(type_):
                problems = []
                for user in element.findall("user"):
                    problems.append(dict(user.attrib))
                container[int(element.attrib["id"])] = problems
        return result


class OverpassAPI(BaseReadAPI):
    """
    OSM Overpass API interface.

    Read calls are mostly compatible with standard API.

    Attributes:
        http        --- Interface for accessing data over HTTP.
        server      --- Domain name of OSM Overpass API.
        basepath    --- Path to the API on the server.

    Methods:
        request     --- Low-level method to retrieve data from server.
        interpreter --- Send request to interpreter and return OSM instance.

    Methods (required by BaseReadAPI):
        get_bbox            --- OSM bbox download.
        get_element         --- Download OSM primitive by type, id.
        get_element_full    --- Download OSM primitive by type, id and all primitives that references.
        get_elements        --- Download OSM primitives by type, ids.
        get_element_rels    --- Download Relations that reference OSM primitive.
        get_node_ways       --- Download Ways that reference Node by id or wrapper.

    """

    http = HTTPClient
    server = "www.overpass-api.de"
    basepath = "/api/"

    def __init__(self, directory=None):
        try:
            self.cache = FileCache(directory)
        except IOError:
            self.cache = NullCache()

    def request(self, path, data):
        """
        Low-level method to retrieve data from server.

        Arguments:
            path        --- One of 'interpreter', 'get_rule', 'add_rule', 'update_rule'.
            data        --- Data to send with the request.

        """
        path = "{}{}".format(self.basepath, path)
        payload = urlencode({"data": data})
        return self.http.request(self.server, path, method="POST", payload=payload)

    def interpreter(self, query):
        """
        Send request to interpreter and return OSM instance.

        Arguments:
            query       --- ET.Element or string.

        """
        if ET.iselement(query):
            query = ET.tostring(query)
        try:
            data = self.cache[query]
        except KeyError:
            data = self.request("interpreter", query)
            self.cache[query] = data
        return OSM.from_xml(data)

    ##################################################
    # READ API                                       #
    ##################################################
    def get_bbox(self, left, bottom, right, top):
        """
        OSM bbox download.

        Return OSM instance.

        Arguments:
            left        --- Left boundary.
            bottom      --- Bottom boundary.
            right       --- Right boundary.
            top         --- Top boundary.

        """
        query = """<union>
                        <bbox-query w="{}" s="{}" e="{}" n="{}"/>
                        <recurse type="node-relation"/>
                        <recurse type="node-way"/>
                        <recurse type="way-relation"/>
                        <recurse type="way-node"/>
                        <recurse type="node-relation"/>
                   </union>
                   <print mode="meta" order="quadtile"/>""".format(left, bottom, right, top)
        return self.interpreter(query)

    def get_element(self, type_, id_, version=None):
        """
        Download OSM primitive by type, id.

        Version and history calls are not supported.

        Return Node/Way/Relation instance.

        Arguments:
            type_       --- OSM primitive type.
            id_         --- OSM primitive id.

        Keyworded arguments:
            version     --- For compatibility only, must be None.

        """
        if version is not None:
            raise NotImplementedError("Version calls are not supported.")
        if type_ not in ("node", "way", "relation"):
            raise ValueError("Type must be 'node', 'way' or 'relation'.")
        query = '<id-query type="{}" ref="{}"/>'.format(type_, id_)
        query += '<print mode="meta"/>'
        osm = self.interpreter(query)
        return getattr(osm, type_)[id_]

    def get_element_full(self, type_, id_):
        """
        Download OSM primitive by type, id and all primitives that references.

        Return OSM instance.

        Arguments:
            type_       --- OSM primitive type.
            id_         --- OSM primitive id.

        """
        if type_ not in ("way", "relation"):
            raise ValueError("Type must be from {}.".format(", ".join(("way", "relation"))))
        query = '<id-query type="{}" ref="{}"/>'.format(type_, id_)
        if type_ == "way":
            query += '<recurse type="way-node"/>'
        else:
            query += '<recurse type="relation-way"/>'
            query += '<recurse type="relation-node"/>'
            query += '<recurse type="relation-relation"/>'
            query += '<recurse type="way-node"/>'
        query += '<print mode="meta" order="quadtile"/>'
        return self.interpreter(query)

    def get_elements(self, type_, ids):
        """
        Download OSM primitives by type, ids.

        Return OSM instance.

        Arguments:
            type_       --- OSM primitive type.
            ids         --- Iterable with ids.

        """
        if type_ not in ("node", "way", "relation"):
            raise ValueError("Type must be 'node', 'way' or 'relation'.")
        query = ''
        for id_ in ids:
            query += '<id-query type="{}" ref="{}"/>'.format(type_, id_)
        query += '<print mode="meta" order="quadtile"/>'
        return self.interpreter(query)

    def get_element_rels(self, type_, id_):
        """
        Download Relations that reference OSM primitive.

        Return OSM instance.

        Arguments:
            type_       --- OSM primitive type.
            id_         --- OSM primitive id.

        """
        if type_ not in ("node", "way", "relation"):
            raise ValueError("Type must be 'node', 'way' or 'relation'.")
        query = '<id-query type="{}" ref="{}" into="child"/>'.format(type_, id_)
        if type_ == "relation":
            query += '<recurse type="relation-backwards" from="child"/>'
        else:
            query += '<recurse type="{}-relation" from="child"/>'.format(type_)
        query += '<print mode="meta" order="quadtile"/>'
        return self.interpreter(query)

    def get_node_ways(self, element):
        """
        Download Ways that reference Node by id or wrapper.

        Return OSM instance.

        Arguments:
            element     --- Node wrapper or id.

        """
        if isinstance(element, Node):
            element = element.id
        query = '<id-query type="node" ref="{}" into="parent"/>'.format(element)
        query += '<recurse type="node-way" from="parent"/>'
        query += '<print mode="meta" order="quadtile"/>'
        return self.interpreter(query)


class API(BaseReadAPI, BaseWriteAPI):
    """
    OSM API interface.

    Properties:
        capabilities    --- OSM API capabilities.

    Attributes:
        http            --- Interface for accessing data over HTTP.
        server          --- Domain name of OSM Overpass API.
        basepath        --- Path to the API on the server.
        version         --- Version of OSM API.
        username        --- Username for API authentication
        password        --- Password for API authentication.
        auto_changeset  --- Dictionary with configuration of automatic changeset creation.

    Methods:
        request             --- Low-level method to retrieve data from server.
        get                 --- Low-level method for GET request.
        put                 --- Low-level method for PUT request.
        delete              --- Low-level method for DELETE request.
        post                --- Low-level method for POST request.

        get_changeset       --- Download Changeset by id.
        get_changeset_full  --- Download Changeset contents by id.
        search_changeset    --- Search for Changeset by given parameters.
        create_changeset    --- Create Changeset.
        update_changeset    --- Update Changeset.
        close_changeset     --- Close Changeset.

    Methods (required by BaseReadAPI):
        get_capabilities    --- Download and return dictionary with OSM API capabilities.
        get_bbox            --- OSM bbox download.
        get_element         --- Download OSM primitive by type, id and optionally version.
        get_element_full    --- Download OSM primitive by type, id and all primitives that references.
        get_elements        --- Download OSM primitives by type, ids.
        get_element_rels    --- Download Relations that reference OSM primitive.
        get_node_ways       --- Download Ways that reference Node by id or wrapper.

    Methods (required by BaseWriteAPI):
        upload_diff         --- Diff upload.
        create_element      --- Create OSM primitive.
        update_element      --- Update OSM primitive.
        delete_element      --- Delete OSM primitive.
        delete_elements     --- Delete OSM primitives by type and ids.

    """

    http = HTTPClient
    log = logging.getLogger("osmt.api")
    _capabilities = None
    _changeset = None
    version = 0.6
    server = "api.openstreetmap.org"
    basepath = "/api/{}/".format(version)

    def __init__(self, username="", password="", auto_changeset=None):
        """
        Keyworded arguments:
            username        --- Username for API authentication
            password        --- Password for API authentication
            auto_changeset  --- Dictionary with configuration of automatic
                                changeset creation:
                                    'enabled' - Enable auto_changeset (boolean).
                                    'size' - Maximum size of changeset (integer).
                                    'tag' - Default tags (dictionary).

        """
        self.username = username
        self.password = password
        if not isinstance(auto_changeset, MutableMapping):
            auto_changeset = {}
        auto_changeset.setdefault("enabled", True)
        auto_changeset.setdefault("size", 200)
        auto_changeset.setdefault("tag", {}).setdefault("created_by", "osmt/{0}".format(__version__))
        self.auto_changeset = auto_changeset

    def __del__(self):
        self._auto_changeset_clear(force=True)
        return None

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
        payload = element.to_xml(strip=("user", "uid", "visible", "timestamp", "changeset"))
        for key in ("node", "way", "relation"):
            for element in payload.iter(key):
                element.attrib["changeset"] = changeset_id
                if main_tag_only:
                    for child in element:
                        element.remove(child)
        return ET.tostring(payload)

    ##################################################
    # HTTP methods                                   #
    ##################################################
    def _get_auth_header(self):
        """ Get value of Authorization header. """
        return "Basic " + b64encode("{}:{}".format(self.username, self.password).encode("utf-8")).decode().strip()

    def request(self, path, payload=None, method="GET", auth=False):
        """
        Low-level method to retrieve data from server.

        Arguments:
            path        --- One of 'interpreter', 'get_rule', 'add_rule', 'update_rule'.

        Keyworded arguments:
            payload     --- Data to send with the request.
            method      --- HTTP method to use for request.
            auth        --- Add Authorization header.

        """
        path = "{}{}".format(self.basepath, path)
        headers = {}
        if auth:
            headers["Authorization"] = self._get_auth_header()
        return self.http.request(self.server, path, method=method, headers=headers, payload=payload)

    def get(self, path):
        """
        Low-level method for GET request.

        Arguments:
            path        --- Path to download.

        """
        return self.request(path)

    def put(self, path, payload=None):
        """
        Low-level method for PUT request.

        Arguments:
            path        --- Path to download.

        Keyworded arguments:
            payload     --- Data to send with the request.

        """
        return self.request(path, payload=payload, method="PUT", auth=True)

    def delete(self, path, payload=None):
        """
        Low-level method for DELETE request.

        Arguments:
            path        --- Path to download.

        Keyworded arguments:
            payload     --- Data to send with the request.

        """
        return self.request(path, payload=payload, method="DELETE", auth=True)

    def post(self, path, payload=None):
        """
        Low-level method for POST request.

        Arguments:
            path        --- Path to download.

        Keyworded arguments:
            payload     --- Data to send with the request.

        """
        return self.request(path, payload=payload, method="POST", auth=True)

    ##################################################
    # capabilities                                   #
    ##################################################
    @property
    def capabilities(self):
        """ OSM API capabilities """
        if self._capabilities is None:
            self._capabilities = self.get_capabilities()
        return self._capabilities

    def get_capabilities(self):
        """ Download and return dictionary with OSM API capabilities. """
        capabilities = {}
        data = ET.XML(self.http.request(self.server, "/api/capabilities"))
        for element in data.find("api"):
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
    # Changesets                                     #
    ##################################################
    def get_changeset(self, id_):
        """
        Download Changeset by id.

        Return Changeset instance.

        Arguments:
            id_         --- Changeset id.

        """
        path = "changeset/{}".format(id_)
        return Changeset.from_xml(ET.XML(self.get(path)).find("changeset"))

    def get_changeset_full(self, id_):
        """
        Download Changeset contents by id.

        Return OSC instance.

        Arguments:
            id_         --- Changeset id.

        """
        path = "changeset/{}/download".format(id_)
        return OSC.from_xml(self.get(path))

    def search_changeset(self, params):
        """
        Search for Changeset by given parameters.

        Return list of Changesets.

        Arguments:
            params          --- Dictionary of parameters: bbox, user or
                                display_name, time, open, closed.

        """
        params = "&".join(("{}={}".format(key, value) for key, value in params.items()))
        path = "changesets?{}".format(params)
        data = self.get(path)
        result = []
        for element in ET.XML(self.get(path)).findall("changeset"):
            result.append(Changeset.from_xml(element))
        return result

    def create_changeset(self, changeset=None, comment=None):
        """
        Create Changeset.

        Return Changeset instance.

        Keyworded arguments:
            changeset   --- Changeset instance or None (create new).
            comment     --- Comment tag.

        """
        if changeset is None:
            # No Changset instance provided => create new one
            tag = dict(self.auto_changeset["tag"])
            if comment is not None:
                tag["comment"] = comment
            changeset = Changeset(tag=tag)
        elif not isinstance(changeset, Changeset):
            raise TypeError("Changeset must be Changeset instance or None.")
        payload = "<osm>{}</osm>".format(ET.tostring(changeset.to_xml()))
        path = "changeset/create"
        changeset.attrib["id"] = int(self.put(path, payload))
        return changeset

    def update_changeset(self, changeset):
        """
        Update Changeset.

        Return updated Changeset instance.

        Arguments:
            changeset   --- Changeset instance.

        """
        if not isinstance(changeset, Changeset):
            raise TypeError("Changeset must be Changeset instance.")
        payload = "<osm>{}</osm>".format(ET.tostring(changeset.to_xml()))
        path = "changeset/{}".format(changeset.id)
        return Changeset.from_xml(ET.XML(self.put(path, payload)).find("changeset"))

    def close_changeset(self, changeset):
        """
        Close Changeset.

        Arguments:
            changeset   --- Changeset instance or changeset id.

        """
        # Temporarily disable auto_changeset
        old = self.auto_changeset["enabled"]
        self.auto_changeset["enabled"] = False
        changeset_id = self._changeset_id(changeset)
        self.auto_changeset["enabled"] = old
        path = "changeset/{}/close".format(changeset_id)
        self.put(path)

    ##################################################
    # READ API                                       #
    ##################################################
    def get_bbox(self, left, bottom, right, top):
        """
        OSM bbox download.

        Return OSM instance.

        Arguments:
            left        --- Left boundary.
            bottom      --- Bottom boundary.
            right       --- Right boundary.
            top         --- Top boundary.

        """
        path = "map?bbox={},{},{},{}".format(left, bottom, right, top)
        return OSM.from_xml(self.get(path))

    def get_element(self, type_, id_, version=None):
        """
        Download OSM primitive by type, id and optionally version.

        Return Node/Way/Relation instance.

        Arguments:
            type_       --- OSM primitive type.
            id_         --- OSM primitive id.

        Keyworded arguments:
            version     --- OSM primitive version number, None (latest), or
                            '*' (complete history).

        """
        if type_ not in ("node", "way", "relation"):
            raise ValueError("Type must be 'node', 'way' or 'relation'.")
        path = "{}/{}".format(type_, id_)
        if isinstance(version, int):
            path += "/{}".format(version)
        elif version == "*":
            path += "/history"
        elif version is not None:
            raise TypeError("Version must be integer, '*' or None.")
        osm = OSM.from_xml(self.get(path))
        return getattr(osm, type_)[id_]

    def get_element_full(self, type_, id_):
        """
        Download OSM primitive by type, id and all primitives that references.

        Return OSM instance.

        Arguments:
            type_       --- OSM primitive type.
            id_         --- OSM primitive id.

        """
        if type_ not in ("way", "relation"):
            raise ValueError("Type must be from {}.".format(", ".join(("way", "relation"))))
        path = "{}/{}/full".format(type_, id_)
        return OSM.from_xml(self.get(path))

    def get_elements(self, type_, ids):
        """
        Download OSM primitives by type, ids.

        Return OSM instance.

        Arguments:
            type_       --- OSM primitive type.
            ids         --- Iterable with ids.

        """
        if type_ not in ("node", "way", "relation"):
            raise ValueError("Type must be 'node', 'way' or 'relation'.")
        path = "{0}s?{0}s={1}".format(type_, ",".join((str(id) for id in ids)))
        return OSM.from_xml(self.get(path))

    def get_element_rels(self, type_, id_):
        """
        Download Relations that reference OSM primitive.

        Return OSM instance.

        Arguments:
            type_       --- OSM primitive type.
            id_         --- OSM primitive id.

        """
        if type_ not in ("node", "way", "relation"):
            raise ValueError("Type must be 'node', 'way' or 'relation'.")
        path = "{}/{}/relations".format(type_, id_)
        return OSM.from_xml(self.get(path))

    def get_node_ways(self, element):
        """
        Download Ways that reference Node by id or wrapper.

        Return OSM instance.

        Arguments:
            element     --- Node wrapper or id.

        """
        if isinstance(element, Node):
            element = element.id
        path = "node/{}/ways".format(element)
        return OSM.from_xml(self.get(path))


    ##################################################
    # WRITE API                                      #
    ##################################################
    def upload_diff(self, osc, changeset=None):
        """
        Diff upload.

        Return {type: {old_id: returned_data} }

        Arguments:
            osc         --- OSC instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        changeset_id = self._changeset_id(changeset)
        if not isinstance(osc, OSC):
            raise TypeError("Osc must be OSC instance.")
        payload = self._format_payload(osc, changeset_id)
        path = "changeset/{}/upload".format(changeset_id)
        data = self.post(path, payload)
        if not self._auto_changeset_clear(force=True):
            self.close_changeset(int(changeset_id))
        data = ET.XML(data)
        result = {"node":{}, "way":{}, "relation":{}}
        for key, value in result.items():
            for element in data.findall(key):
                old_id = int(element.attrib["old_id"])
                value[old_id] = {"old_id": old_id}
                if "new_id" in element.attrib:
                    value["new_id"] = int(element.attrib["new_id"])
                if "new_version" in element.attrib:
                    value[old_id]["new_version"] = int(element.attrib["new_version"])
        return result

    def create_element(self, element, changeset=None):
        """
        Create OSM primitive.

        Return Node/Way/Relation instance.

        Arguments:
            element     --- Node/Way/Relation instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(element, (Node, Way, Relation)):
            raise TypeError("Element must be a Node, Way or Relation instance.")
        changeset_id = self._changeset_id(changeset)
        path = "{}/{}/create".format(element.TAG_NAME(), element.id)
        payload = "<osm>{}</osm>".format(self._format_payload(element, changeset_id))
        data = self.put(path, payload)
        self._auto_changeset_clear()
        element.attrib["version"] = int(data)
        element.attrib["changeset"] = int(changeset_id)
        element.history = {element.version: element}
        return element

    def update_element(self, element, changeset=None):
        """
        Update OSM primitive.

        Return Node/Way/Relation instance.

        Arguments:
            element     --- Node/Way/Relation instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(element, (Node, Way, Relation)):
            raise TypeError("Element must be a Node, Way or Relation instance.")
        changeset_id = self._changeset_id(changeset)
        path = "{}/{}".format(element.TAG_NAME(), element.id)
        payload = "<osm>{}</osm>".format(self._format_payload(element, changeset_id))
        data = self.put(path, payload)
        self._auto_changeset_clear()
        element.attrib["version"] = int(data)
        element.attrib["changeset"] = int(changeset_id)
        element.history = {element.version: element}
        return element

    def delete_element(self, element, changeset=None):
        """
        Delete OSM primitive.

        Return Node/Way/Relation instance.

        Arguments:
            element     --- Node/Way/Relation instance.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if not isinstance(element, (Node, Way, Relation)):
            raise TypeError("Element must be a Node, Way or Relation instance.")
        changeset_id = self._changeset_id(changeset)
        path = "{}/{}".format(element.TAG_NAME(), element.id)
        payload = "<osm>{}</osm>".format(self._format_payload(element, changeset_id, main_tag_only=True))
        data = self.delete(path, payload)
        self._auto_changeset_clear()
        element.attrib["visible"] = False
        element.attrib["version"] = int(data)
        element.attrib["changeset"] = int(changeset_id)
        element.history = {element.version: element}
        return element

    def delete_elements(self, type_, ids, changeset=None):
        """
        Delete OSM primitives by type and ids.

        Return OSC instance.

        Arguments:
            type_       --- OSM primitive type.
            ids         --- Iterable of ids.

        Keyworded arguments:
            changeset   --- Changeset instance, changeset id or None (create new).

        """
        if type_ not in ("node", "way", "relation"):
            raise ValueError("Type must be 'node', 'way' or 'relation'.")
        delete = []
        for element in self.get_elements(type_, ids):
            delete.append(self.delete_element(element, changeset))
        return OSC(delete=delete)



############################################################
### Cache for Overpass API requests.                     ###
############################################################

class NullCache(object):
    """
    Null dummy cache.

    """

    def __getitem__(self, key):
        raise KeyError

    def __setitem__(self, key, value):
        pass

    def __delitem__(self, key):
        pass


class FileCache(NullCache):
    """
    Cache that stores data as files in a designated directory.

    Attributes:
        directory       --- Directory for storing cache files.
        suffix          --- Suffix of cache files.

    """

    suffix = ".osm.cache"

    def __init__(self, directory):
        """
        Raise IOError if the passed directory is invalid.

        Arguments:
            directory   --- Directory for storing cache files.

        """
        if directory is None or not os.path.isdir(directory):
            raise IOError((1, "Directory not found.", directory))
        self.directory = directory

    def get_filename(self, key):
        if not isinstance(key, bytes):
            key = key.encode("utf-8")
        return sha1(key).hexdigest() + self.suffix

    def get_filepath(self, key):
        return os.path.join(self.directory, self.get_filename(key))

    def __getitem__(self, key):
        path = self.get_filepath(key)
        if not os.path(path):
            raise KeyError
        with open(path) as fp:
            return fp.read()

    def __setitem__(self, key, value):
        path = self.get_filepath(key)
        with open(path, "w") as fp:
            fp.write(value)

    def __delitem__(self, key):
        path = self.get_filepath(key)
        if not os.path(path):
            raise KeyError
        os.remove(path)



############################################################
### Wrappers for OSM Elements and documents.             ###
############################################################

class XMLElement(object):
    """
    Abstract wrapper for XML Elements.

    Abstract methods:
        to_xml          --- Get ET.Element representation of wrapper.

    Class methods:
        parse_attribs   --- Extract attributes of ET.element and convert them
                            to appropriate types.
        unparse_attribs --- Convert attribute values to strings, optionally
                            filtering out some attributes.

    """

    __metaclass__ = ABCMeta

    @abstractmethod
    def to_xml(self, strip=()):
        """
        Get ET.Element representation of wrapper.

        Keyworded arguments:
            strip       --- Attributes that should be filtered out.

        """
        raise NotImplementedError

    @classmethod
    def parse_attribs(cls, element):
        """
        Extract attributes of ET.element and convert them to appropriate types.

        Arguments:
            element     --- ET.Element instance.

        """
        attribs = dict(element.attrib)
        for key, value in attribs.items():
            if key in ("uid", "changeset", "version", "id", "ref"):
                attribs[key] = int(value)
            elif key in ("lat", "lon", "min_lon", "max_lon", "min_lat", "max_lat"):
                attribs[key] = float(value)
            elif key in ("open", "visible"):
                attribs[key] = value=="true"
        return attribs

    @classmethod
    def unparse_attribs(cls, data, strip=()):
        """
        Convert attribute values to strings, optionally filtering out some attributes.

        Arguments:
            data        --- Dictionary of attributes.

        Keyworded arguments:
            strip       --- Container of attribute names that should be filtered out
                            from the returned dictionary.

        """
        attribs = {}
        for key, value in data.items():
            if key in strip:
                continue
            attribs[key] = str(value)
            if isinstance(value, bool):
                attribs[key] = attribs[key].lower()
        return attribs


class OSMElement(XMLElement):
    """
    Abstract wrapper for OSM primitives and changeset.

    Class methods:
        TAG_NAME    --- Tag name of the OSMElement.
        parse_tags  --- Extract tags of OSM primitive or changeset from ET.Element.

    Properties:
        id          --- Id of OSM primitive or changeset, read-only.

    Attributes:
        attrib      --- Attributes of OSM primitive or changeset.
        tag         --- Tags of OSM primitive or changeset.

    Methods:
        to_xml      --- Get ET.Element representation of wrapper.

    """

    @classmethod
    def TAG_NAME(cls):
        """ get tag name of the element """
        return cls.__name__.lower()

    @classmethod
    def parse_tags(cls, element):
        """
        Extract tags of OSM primitive or changeset from ET.Element.

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
        """ id of OSM primitive or changeset """
        return self.attrib.get("id")

    def __init__(self, attrib={}, tag={}):
        self.attrib = dict(attrib)
        self.tag = dict(tag)

    def to_xml(self, strip=()):
        """
        Get ET.Element representation of wrapper.

        Keyworded arguments:
            strip       --- Attributes that should be filtered out.

        """
        attrib = self.unparse_attribs(self.attrib, strip=strip)
        element = ET.Element(self.TAG_NAME(), attrib)
        for key in sorted(self.tag.keys()):
            ET.SubElement(element, "tag", {"k": key, "v": self.tag[key]})
        return element


class OSMPrimitive(OSMElement):
    """
    Abstract wrapper for Node, Way and Relation.

    Properties:
        version     --- Version of OSM primitive, read-only.

    Attributes:
        history     --- Dictionary containing old versions of OSM primitive.

    Methods:
        merge_history   --- Merge history of other wrapper into self.

    """

    @property
    def version(self):
        """ version of OSM primitive """
        return self.attrib.get("version")

    def __init__(self, attrib={}, tag={}):
        OSMElement.__init__(self, attrib, tag)
        self.history = {}
        if self.version is not None:
            self.history[self.version] = self

    def merge_history(self, other):
        if self.__class__.__name__ != other.__class__.__name__:
            raise ValueError("Cannot merge history of {} into {} wrapper.".format(other.__class__.__name__, self.__class__.__name__))
        elif self.id != other.id:
            raise ValueError("Cannot merge history of wrappers with distinct ids.")
        elif None in (self.version, other.version):
            raise ValueError("Cannot merge history of wrappers without version numbers.")
        history = dict(other.history)
        history.update(self.history)
        for element in history.values():
            element.history = history
        max_id = max(self.history.keys())
        return self.history[max_id]


class Node(OSMPrimitive):
    """
    Node wrapper.

    Implements methods for operators:
        Node == Node
        Node != Node

    Attributes:
        lat         --- Latitude of the node.
        lon         --- Longitude of the node.

    Class methods:
        from_xml    --- Create Node wrapper from XML representation.

    """

    _counter = 0

    @classmethod
    def from_xml(cls, data):
        """
        Create Node wrapper from XML representation.

        Arguments:
            data    --- ET.Element or XML string.

        """
        if not ET.iselement(data):
            data = XML(data)
        attrib = cls.parse_attribs(data)
        tag = cls.parse_tags(data)
        return cls(attrib, tag)

    def __init__(self, attrib={}, tag={}):
        OSMPrimitive.__init__(self, attrib, tag)
        if self.id is None:
            # Automatically asign id
            Node._counter -= 1
            self.attrib["id"] = Node._counter

    def __eq__(self, other):
        if not isinstance(other, Node):
            return NotImplemented
        return self.id == other.id and self.version == other.version and self.tag == other.tag and self.lat == other.lat and self.lon == other.lon

    def __ne__(self, other):
        if not isinstance(other, Node):
            return NotImplemented
        return not self.__eq__(other)

    @property
    def lat(self):
        return self.attrib.get("lat")

    @lat.setter
    def lat(self, value):
        self.attrib["lat"] = float(value)

    @property
    def lon(self):
        return self.attrib.get("lon")

    @lon.setter
    def lon(self, value):
        self.attrib["lon"] = float(value)


class Way(OSMPrimitive):
    """
    Way wrapper.

    Implements methods for operators:
        Way == Way
        Way != Way
        Node in Way

    Class methods:
        from_xml    --- Create Way wrapper from XML representation.
        parse_nds   --- Extract list of node ids of the way from ET.Element.

    Attributes:
        nd          --- List of node ids of the way.

    Methods:
        to_xml      --- Get ET.Element representation of wrapper.

    """

    _counter = 0

    @classmethod
    def from_xml(cls, data):
        """
        Create Way wrapper from XML representation.

        Arguments:
            data    --- ET.Element or XML string.

        """
        if not ET.iselement(data):
            data = XML(data)
        attrib = cls.parse_attribs(data)
        tag = cls.parse_tags(data)
        nd = cls.parse_nds(data)
        return cls(attrib, tag, nd)

    @classmethod
    def parse_nds(cls, element):
        """
        Extract list of node ids of the way from ET.Element.

        Arguments:
            element     --- ET.Element instance.

        """
        nds = []
        for nd in element.findall("nd"):
            nds.append(int(nd.attrib["ref"]))
        return nds

    def __init__(self, attrib={}, tag={}, nd=()):
        OSMPrimitive.__init__(self, attrib, tag)
        self.nd = list(nd)
        if self.id is None:
            # Automatically asign id
            Way._counter -= 1
            self.attrib["id"] = Way._counter

    def __eq__(self, other):
        if not isinstance(other, Way):
            return NotImplemented
        return self.id == other.id and self.version == other.version and self.tag == other.tag and self.nd == other.nd

    def __ne__(self, other):
        if not isinstance(other, Way):
            return NotImplemented
        return not self.__eq__(other)

    def __contains__(self, item):
        if not isinstance(item, Node):
            raise NotImplementedError
        return item.id in self.nd

    def to_xml(self, strip=()):
        """
        Get ET.Element representation of wrapper.

        Keyworded arguments:
            strip       --- Attributes that should be filtered out.

        """
        element = OSMPrimitive.to_xml(self, strip=strip)
        for nd in self.nd:
            ET.SubElement(element, "nd", {"ref": str(nd)})
        return element


class Relation(OSMPrimitive):
    """
    Relation wrapper.

    Implements methods for operators:
        Relation == Relation
        Relation != Relation
        Node in Relation, Way in Relation, Relation in Relation

    Class methods:
        from_xml        --- Create Relation wrapper from XML representation.
        parse_members   --- Extract list of members of the relation from ET.Element.

    Attributes:
        member          --- List of relation members.

    Methods:
        to_xml          --- Get ET.Element representation of wrapper.

    """

    _counter = 0

    @classmethod
    def from_xml(cls, data):
        """
        Create Relation wrapper from XML representation.

        Arguments:
            data    --- ET.Element or XML string.

        """
        if not ET.iselement(data):
            data = XML(data)
        attrib = cls.parse_attribs(data)
        tag = cls.parse_tags(data)
        member = cls.parse_members(data)
        return cls(attrib, tag, member)

    @classmethod
    def parse_members(cls, element):
        """
        Extract list of members of the relation from ET.Element.

        Arguments:
            element     --- ET.Element instance.

        """
        members = []
        for member in element.findall("member"):
            members.append(cls.parse_attribs(member))
        return members

    def __init__(self, attrib={}, tag={}, member=()):
        OSMPrimitive.__init__(self, attrib, tag)
        self.member = list(member)
        if self.id is None:
            # Automatically asign id
            Relation._counter -= 1
            self.attrib["id"] = Relation._counter

    def __eq__(self, other):
        if not isinstance(other, Relation):
            return NotImplemented
        return self.id == other.id and self.version == other.version and self.tag == other.tag and self.member == other.member

    def __ne__(self, other):
        if not isinstance(other, Relation):
            return NotImplemented
        return not self.__eq__(other)

    def __contains__(self, item):
        if not isinstance(item, (Node, Way, Relation)):
            raise NotImplementedError
        for member in self.member:
            if member["type"] == item.TAG_NAME() and memeber["ref"] == item.id:
                return True
        return False

    def to_xml(self, strip=()):
        """
        Get ET.Element representation of wrapper.

        Keyworded arguments:
            strip       --- Attributes that should be filtered out.

        """
        element = OSMPrimitive.to_xml(self, strip=strip)
        for member in self.member:
            attrib = self.unparse_attribs(member, strip=strip)
            ET.SubElement(element, "member", attrib)
        return element


class Changeset(OSMElement):
    """
    Changeset wrapper.

    Class methods:
        from_xml        --- Create Relation wrapper from XML representation.

    """

    @classmethod
    def from_xml(cls, data):
        """
        Create Changeset wrapper from XML representation.

        Arguments:
            data    --- ET.Element or XML string.

        """
        if not ET.iselement(data):
            data = XML(data)
        attrib = cls.parse_attribs(data)
        tag = cls.parse_tags(data)
        return cls(attrib, tag)


class OSM(XMLElement, MutableSet):
    """
    OSM XML document wrapper. Essentially a mutable set of Node, Way, Relation instances.

    Class methods:
        from_xml        --- Create OSM XML document wrapper from XML representation.

    Attributes:
        node        --- Dictionary of nodes {nodeId: Node}.
        way         --- Dictionary of ways {wayId: Way}.
        relation    --- Dictionary of relations {relationId: Relation}.

    Methods:
        to_xml          --- Get ET.Element representation of wrapper.

    """

    @classmethod
    def from_xml(cls, data):
        """
        Create OSM XML document wrapper from XML representation.

        Arguments:
            data    --- ET.Element or XML string.

        """
        if not ET.iselement(data):
            data = ET.XML(data)
        node = {}
        way = {}
        relation = {}
        for elem_cls, container in ((Node, node), (Way, way), (Relation, relation)):
            for element in data.findall(elem_cls.TAG_NAME()):
                element = elem_cls.from_xml(element)
                if element.id in container:
                    container[element.id] = container[element.id].merge_history(element)
                else:
                    container[element.id] = element
        return cls(chain(node.values(), way.values(), relation.values()))

    def __init__(self, items=()):
        self.node = {}
        self.way = {}
        self.relation = {}
        for item in items:
            self.add(item)

    def __len__(self):
        return len(self.node) + len(self.way) + len(self.relation)

    def __iter__(self):
        return chain(self.node.values(), self.way.values(), self.relation.values())

    def __contains__(self, item):
        if not isinstance(item, (Node, Way, Relation)):
            raise NotImplementedError
        for container, cls in ((self.node, Node), (self.way, Way), (self.relation, Relation)):
            if isinstance(item, cls):
                return container.get(item.id) == item
        return False

    def add(self, item):
        for container, cls in ((self.node, Node), (self.way, Way), (self.relation, Relation)):
            if isinstance(item, cls):
                container[item.id] = item
                return
        raise ValueError("Only Node, Way, Relation instances are allowed.")

    def discard(self, item):
        for container, cls in ((self.node, Node), (self.way, Way), (self.relation, Relation)):
            if isinstance(item, cls):
                container.pop(item.id, None)
                return
        raise ValueError("Only Node, Way, Relation instances are allowed.")

    def to_xml(self, strip=()):
        """
        Get ET.Element representation of wrapper.

        Keyworded arguments:
            strip       --- Attributes that should be filtered out.

        """
        element = ET.Element("osm", {"version": str(API.version), "generator": "osmt"})
        for child in self:
            element.append(child.to_xml(strip=strip))
        return element


class OSC(XMLElement):
    """
    OSC XML document wrapper.

    Class methods:
        from_diff       --- Create OSC XML document wrapper by diffing two OSM instances.
        from_xml        --- Create OSC XML document wrapper from XML representation.

    Attributes:
        create      --- OSM instance containing elements to create.
        modify      --- OSM instance containing elements to modify.
        delete      --- OSM instance containing elements to delete.

    Methods:
        to_xml          --- Get ET.Element representation of wrapper.

    """

    @classmethod
    def from_diff(cls, parent, child):
        """
        Create OSC XML document wrapper by diffing two OSM instances.

        Arguments:
            parent  --- OSM instance with original data.
            child   --- OSM instance with changed data.

        """
        if not (isinstance(parent, OSM) and isinstance(child, OSM)):
            raise TypeError("Both arguments must be OSM instances.")
        create = set()
        modify = set()
        delete = set()
        for type_ in ("node", "way", "relation"):
            parent_elements = getattr(parent, type_)
            child_elements = getattr(child, type_)
            for id_ in set(child_elements.keys()) | set(parent_elements.keys()):
                if id_ not in child_elements:
                    delete.add(parent_elements[id_])
                elif id_ not in parent_elements:
                    create.add(child_elements[id_])
                elif parent_elements[id_] != child_elements[id_]:
                    modify.add(child_elements[id_])
        return cls(create, modify, delete)

    @classmethod
    def from_xml(cls, data):
        """
        Create OSM XML document wrapper from XML representation.

        Arguments:
            data    --- ET.Element or XML string.

        """
        if not ET.iselement(data):
            data = ET.XML(data)
        create = OSM()
        modify = OSM()
        delete = OSM()
        for elem_name, container in (("create", create), ("modify", modify), ("delete", delete)):
            for element in data.findall(elem_name):
                container |= OSM.from_xml(element)
        return cls(create, modify, delete)

    def __init__(self, create=(), modify=(), delete=()):
        self.create = OSM(create)
        self.modify = OSM(modify)
        self.delete = OSM(delete)

    def to_xml(self, strip=()):
        """
        Get ET.Element representation of wrapper.

        Keyworded arguments:
            strip       --- Attributes that should be filtered out.

        """
        element = ET.Element("osmChange", {"version":str(API.version), "generator":"osmt"})
        for action in ("create", "modify", "delete"):
            action_element = getattr(self, action).to_xml(strip=strip)
            if len(action_element.getchildren()) > 0:
                action_element.tag = action
                action_element.attrib = {}
                element.append(action_element)
        return element



############################################################
### Exceptions.                                          ###
############################################################

class APIError(Exception):
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
        return "Request failed: {} ({}) << {}".format(self.status, self.reason, self.payload)
