# -*- coding: utf-8 -*-
"""
Osmapis is a set of tools for accessing and manipulating OSM data via OSM API,
Overpass API, Quick History Service.

Variables:
    wrappers        --- Dictionary containing the classes to use for OSM element wrappers.

Classes:
    QHS             --- Quick History Service interface.
    OverpassAPI     --- OSM Overpass API interface.
    API             --- OSM API interface.
    HTTPClient      --- Interface for accessing data over HTTP.
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

__version__ = "0.8.0"

from abc import ABCMeta, abstractmethod
from base64 import b64encode
from collections import MutableSet, MutableMapping
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


__all__ = ["wrappers",
           "QHS",
           "OverpassAPI",
           "API",
           "HTTPClient",
           "Node",
           "Way",
           "Relation",
           "Changeset",
           "OSM",
           "OSC",
           "APIError"]


logging.getLogger('osmapis').addHandler(logging.NullHandler())


############################################################
### HTTPClient class.                                    ###
############################################################

class HTTPClient(object):
    """
    Interface for accessing data over HTTP.

    Attributes:
        headers     --- Default headers for HTTP request.

    Methods:
        request     --- Perform HTTP request and handle possible redirection, on error retry.

    """

    def __new__(cls, *p, **k):
        raise TypeError("This class cannot be instantionalized.")

    headers = {}
    headers["User-agent"] = "osmapis/{0}".format(__version__)
    log = logging.getLogger("osmapis.http")

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
        cls.log.debug("{}({}) {}{} << payload {}".format(method, retry, server, path, payload is not None))
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
                raise APIError("Unexpected Content-type {}".format(response.getheader("Content-Type")), payload)
            return body
        elif response.status in (301, 302, 303, 307):
            # Try to redirect
            connection.close()
            url = response.getheader("Location")
            if url is None:
                cls.log.error("Got code {}, but no location header.".format(response.status))
                raise APIError("Unable to redirect the request.", payload)
            url = unquote(url)
            cls.log.debug("Redirecting to {}".format(url))
            url = url.split("/", 3)
            server = url[2]
            path = "/" + url[3]
            return cls.request(server, path, method=method, headers=headers, payload=payload, retry=retry)
        elif 400 <= response.status < 500:
            body = response.read().decode("utf-8", "replace").strip()
            connection.close()
            cls.log.error("Got error {} ({}).".format(response.reason, response.status))
            raise APIError(body, payload)
        else:
            body = response.read().decode("utf-8", "replace").strip()
            connection.close()
            if retry <= 0:
                cls.log.error("Could not download {}{}".format(server, path))
                raise APIError("Got error {} ({}).".format(response.reason, response.status), payload)
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
        get_bbox            --- Download OSM data inside the specified bbox.
        get_element         --- Download node/way/relation by id and optionally version.
        get_element_full    --- Download way/relation by id and all elements that references.
        get_elements        --- Download nodes/ways/relations by ids.
        get_element_rels    --- Download relations that reference the node/way/relation by id.
        get_node_ways       --- Download ways that reference the node by id or wrapper.

    Methods:
        get_node            --- Download node by id and optionally version.
        get_way             --- Download way by id and optionally version.
        get_relation        --- Download relation by id and optionally version.
        get_history         --- Download complete history of Node/Way/Relation wrapper.
        get_way_full        --- Download way by id and all referenced nodes.
        get_relation_full   --- Download relation by id and all referenced members.
        get_full            --- Download way/relation and all elements that references.
        get_nodes           --- Download nodes by ids.
        get_ways            --- Download ways by ids.
        get_relations       --- Download relations by ids.
        get_node_rels       --- Download relations that reference the node by id or wrapper.
        get_way_rels        --- Download relations that reference the way by id or wrapper.
        get_relation_rels   --- Download relations that reference the relation by id or wrapper.
        get_rels            --- Download relations that reference the Node/Way/Relation wrapper.

    """
    __metaclass_ = ABCMeta

    @abstractmethod
    def get_bbox(self, left, bottom, right, top):
        """
        Download OSM data inside the specified bbox.

        Return OSM wrapper.

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
        Download node/way/relation by id and optionally version.

        Return Node/Way/Relation wrapper.

        Arguments:
            type_       --- Element type (node/way/relation).
            id_         --- Element id.

        Keyworded arguments:
            version     --- Element version number, None (latest), or '*' (complete history).

        """
        raise NotImplementedError

    def get_node(self, id_, version=None):
        """
        Download node by id and optionally version.

        Return Node wrapper.

        Arguments:
            id_         --- Node id.

        Keyworded arguments:
            version     --- Node version number, None (latest), or '*' (complete history).

        """
        return self.get_element("node", id_, version=version)

    def get_way(self, id_, version=None):
        """
        Download way by id and optionally version.

        Return Way wrapper.

        Arguments:
            id_         --- Way id.

        Keyworded arguments:
            version     --- Way version number, None (latest), or '*' (complete history).

        """
        return self.get_element("way", id_, version=version)

    def get_relation(self, id_, version=None):
        """
        Download relation by id and optionally version.

        Return Relation wrapper.

        Arguments:
            id_         --- Relation id.

        Keyworded arguments:
            version     --- Relation version number, None (latest), or '*' (complete history).

        """
        return self.get_element("relation", id_, version=version)

    def get_history(self, element):
        """
        Download complete history of Node/Way/Relation wrapper.

        Return Node/Way/Relation wrapper.

        Arguments:
            element     --- Node/Way/Relation wrapper.

        """
        if not isinstance(element, (Node, Way, Relation)):
            raise TypeError("Element must be a Node, Way or Relation instance.")
        return self.get_element(element.xml_tag, element.id, version="*")

    @abstractmethod
    def get_element_full(self, type_, id_):
        """
        Download way/relation by id and all elements that references.

        Return OSM wrapper.

        Arguments:
            type_       --- Element type (way/relation).
            id_         --- Element id.

        """
        raise NotImplementedError

    def get_way_full(self, id_):
        """
        Download way by id and all referenced nodes.

        Return OSM wrapper.

        Arguments:
            id_         --- Way id.

        """
        return self.get_element_full("way", id_)

    def get_relation_full(self, id_):
        """
        Download way by id and all referenced members.

        Return OSM wrapper.

        Arguments:
            id_         --- Relation id.

        """
        return self.get_element_full("relation", id_)

    def get_full(self, element):
        """
        Download way/relation and all elements that references.

        Return OSM wrapper.

        Arguments:
            element     --- Way/Relation wrapper.

        """
        if not isinstance(element, (Way, Relation)):
            raise TypeError("Element must be a Way or Relation instance.")
        return self.get_element_full(element.xml_tag, element.id)

    @abstractmethod
    def get_elements(self, type_, ids):
        """
        Download nodes/ways/relations by ids.

        Return OSM wrapper.

        Arguments:
            type_       --- Elements type (node/way/relation).
            ids         --- Iterable with ids.

        """
        raise NotImplementedError

    def get_nodes(self, ids):
        """
        Download nodes by ids.

        Return OSM wrapper.

        Arguments:
            ids         --- Iterable with ids.

        """
        return self.get_elements("node", ids)

    def get_ways(self, ids):
        """
        Download ways by ids.

        Return OSM wrapper.

        Arguments:
            ids         --- Iterable with ids.

        """
        return self.get_elements("way", ids)

    def get_relations(self, ids):
        """
        Download relations by ids.

        Return OSM wrapper.

        Arguments:
            ids         --- Iterable with ids.

        """
        return self.get_elements("relation", ids)

    @abstractmethod
    def get_element_rels(self, type_, id_):
        """
        Download relations that reference the node/way/relation by id.

        Return OSM wrapper.

        Arguments:
            type_       --- Element type (node/way/relation).
            id_         --- Element id.

        """
        raise NotImplementedError

    def get_node_rels(self, element):
        """
        Download relations that reference the node by id or wrapper.

        Return OSM wrapper.

        Arguments:
            element     --- Node wrapper or id.

        """
        if isinstance(element, Node):
            element = element.id
        return self.get_element_rels("node", element)

    def get_way_rels(self, element):
        """
        Download relations that reference the way by id or wrapper.

        Return OSM wrapper.

        Arguments:
            element     --- Way wrapper or id.

        """
        if isinstance(element, Way):
            element = element.id
        return self.get_element_rels("way", element)

    def get_relation_rels(self, element):
        """
        Download relations that reference the relation by id or wrapper.

        Return OSM wrapper.

        Arguments:
            element     --- Relation wrapper or id.

        """
        if isinstance(element, Relation):
            element = element.id
        return self.get_element_rels("relation", element)

    def get_rels(self, element):
        """
        Download relations that reference the Node/Way/Relation wrapper.

        Return OSM wrapper.

        Arguments:
            element     --- Node/Way/Relation wrapper.

        """
        if not isinstance(element, (Node, Way, Relation)):
            raise TypeError("Element must be a Node, Way or Relation instance.")
        return self.get_element_rels(element.xml_tag, element.id)

    @abstractmethod
    def get_node_ways(self, element):
        """
        Download ways that reference the node by id or wrapper.

        Return OSM wrapper.

        Arguments:
            element     --- Node wrapper or id.

        """
        raise NotImplementedError


class BaseWriteAPI(object):
    """
    Abstract class for write API operations.

    Abstract methods:
        upload_diff         --- OSC diff upload.
        create_element      --- Create node/way/relation.
        update_element      --- Update node/way/relation.
        delete_element      --- Delete node/way/relation.
        delete_elements     --- Delete nodes/ways/relations by ids.

    Methods:
        create_node         --- Create node.
        create_way          --- Create way.
        create_relation     --- Create relation.
        update_node         --- Update node.
        update_way          --- Update way.
        update_relation     --- Update relation.
        delete_node         --- Delete node.
        delete_way          --- Delete way.
        delete_relation     --- Delete relation.
        delete_nodes        --- Delete nodes by ids.
        delete_ways         --- Delete ways by ids.
        delete_relations    --- Delete relations by ids.

    """
    __metaclass_ = ABCMeta

    @abstractmethod
    def upload_diff(self, osc, changeset=None):
        """
        OSC diff upload.

        Return {type: {old_id: returned_data} }

        Arguments:
            osc         --- OSC wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        raise NotImplementedError

    @abstractmethod
    def create_element(self, element, changeset=None):
        """
        Create node/way/relation.

        Return Node/Way/Relation wrapper.

        Arguments:
            element     --- Node/Way/Relation wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        raise NotImplementedError

    def create_node(self, element, changeset=None):
        """
        Create node.

        Return Node wrapper.

        Arguments:
            element     --- Node wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        if not isinstance(element, Node):
            raise TypeError("Element must be Node instance.")
        return self.create_element(element, changeset)

    def create_way(self, element, changeset=None):
        """
        Create way.

        Return Way wrapper.

        Arguments:
            element     --- Way wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        if not isinstance(element, Way):
            raise TypeError("Element must be Way instance.")
        return self.create_element(element, changeset)

    def create_relation(self, element, changeset=None):
        """
        Create relation.

        Return Relation wrapper.

        Arguments:
            element     --- Relation wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        if not isinstance(element, Relation):
            raise TypeError("Element must be Relation instance.")
        return self.create_element(element, changeset)

    @abstractmethod
    def update_element(self, element, changeset=None):
        """
        Update node/way/relation.

        Return Node/Way/Relation wrapper.

        Arguments:
            element     --- Node/Way/Relation wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        raise NotImplementedError

    def update_node(self, element, changeset=None):
        """
        Update node.

        Return Node wrapper.

        Arguments:
            element     --- Node wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        if not isinstance(element, Node):
            raise TypeError("Element must be Node instance.")
        return self.update_element(element, changeset)

    def update_way(self, element, changeset=None):
        """
        Update way.

        Return Way wrapper.

        Arguments:
            element     --- Way wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        if not isinstance(element, Way):
            raise TypeError("Element must be Way instance.")
        return self.update_element(element, changeset)

    def update_relation(self, element, changeset=None):
        """
        Update relation.

        Return Relation wrapper.

        Arguments:
            element     --- Relation wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        if not isinstance(element, Relation):
            raise TypeError("Element must be Relation instance.")
        return self.update_element(element, changeset)

    @abstractmethod
    def delete_element(self, element, changeset=None):
        """
        Delete node/way/relation.

        Return Node/Way/Relation wrapper.

        Arguments:
            element     --- Node/Way/Relation wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        raise NotImplementedError

    def delete_node(self, element, changeset=None):
        """
        Delete node.

        Return Node wrapper.

        Arguments:
            element     --- Node wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        if not isinstance(element, Node):
            raise TypeError("Element must be Node instance.")
        return self.delete_element(element, changeset)

    def delete_way(self, element, changeset=None):
        """
        Delete way.

        Return Way wrapper.

        Arguments:
            element     --- Way wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        if not isinstance(element, Way):
            raise TypeError("Element must be Way instance.")
        return self.delete_element(element, changeset)

    def delete_relation(self, element, changeset=None):
        """
        Delete relation.

        Return Relation wrapper.

        Arguments:
            element     --- Relation wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        if not isinstance(relation, Relation):
            raise TypeError("Element must be Relation instance.")
        return self.delete_element(element, changeset)

    def delete_elements(self, type_, ids, changeset=None):
        """
        Delete nodes/ways/relations by ids.

        Return OSC instance.

        Arguments:
            type_       --- Element type (node/way/relation).
            ids         --- Iterable of ids.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        raise NotImplementedError

    def delete_nodes(self, ids, changeset=None):
        """
        Delete nodes by ids.

        Return OSC instance.

        Arguments:
            ids         --- Iterable of ids.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        return self.delete_elements("node", ids, changeset)

    def delete_ways(self, ids, changeset=None):
        """
        Delete ways by ids.

        Return OSC instance.

        Arguments:
            ids         --- Iterable of ids.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        return self.delete_elements("way", ids, changeset)

    def delete_relations(self, ids, changeset=None):
        """
        Delete relations by ids.

        Return OSC instance.

        Arguments:
            ids         --- Iterable of ids.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        return self.delete_elements("relation", ids, changeset)



class QHS(object):
    """
    Quick History Service interface.

    Attributes:
        http        --- Interface for accessing data over HTTP.
        server      --- Domain name of QHS.
        basepath    --- Path to the API on the server.
        version     --- Version of OSM API.

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

        Returns the same type of object as passed in element argument. Adds
        odbl_problems attribute to each Node, Way and Relation wrapper that
        contains found problems, or None.

        Arguments:
            element     --- Data to check - Node, Way, Relation, or OSM wrapper.

        """
        if isinstance(element, (Node, Way, Relation)):
            query = {"{}s".format(element.xml_tag): element.id}
        elif isinstance(element, OSM):
            query = {}
            for type_ in ("nodes", "ways", "relations"):
                query[type_] = ",".join(str(id_) for id_ in getattr(element, type_).keys())
        else:
            raise TypeError("Element must be a Node, Way, Relation or OSM instance.")

        result = self.request("problems", query)
        result = self._parse_problems(result)
        if isinstance(element, (Node, Way, Relation)):
            try:
                element.odbl_problems = result[element.xml_tag][element.id]
            except KeyError:
                element.odbl_problems = None
        else:
            for child in element:
                try:
                    child.odbl_problems = result[child.xml_tag][child.id]
                except KeyError:
                    child.odbl_problems = None
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
        interpreter --- Send request to interpreter and return OSM wrapper.

    Methods (required by BaseReadAPI):
        get_bbox            --- Download OSM data inside the specified bbox.
        get_element         --- Download node/way/relation by id and optionally version.
        get_element_full    --- Download way/relation by id and all elements that references.
        get_elements        --- Download nodes/ways/relations by ids.
        get_element_rels    --- Download relations that reference the node/way/relation by id.
        get_node_ways       --- Download ways that reference the node by id or wrapper.

    """

    http = HTTPClient
    server = "www.overpass-api.de"
    basepath = "/api/"

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
        Send request to interpreter and return OSM wrapper.

        Arguments:
            query       --- ET.Element or string.

        """
        if ET.iselement(query):
            query = ET.tostring(query, encoding="utf-8")
        return wrappers["osm"].from_xml(self.request("interpreter", query))

    ##################################################
    # READ API                                       #
    ##################################################
    def get_bbox(self, left, bottom, right, top):
        """
        Download OSM data inside the specified bbox.

        Return OSM wrapper.

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
        Download node/way/relation by id and optionally version.

        Version and history calls are not supported.

        Return Node/Way/Relation wrapper.

        Arguments:
            type_       --- Element type (node/way/relation).
            id_         --- Element id.

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
        return getattr(osm, type_ + "s")[id_]

    def get_element_full(self, type_, id_):
        """
        Download way/relation by id and all elements that references.

        Return OSM wrapper.

        Arguments:
            type_       --- Element type (way/relation).
            id_         --- Element id.

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
        Download nodes/ways/relations by ids.

        Return OSM wrapper.

        Arguments:
            type_       --- Elements type (node/way/relation).
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
        Download relations that reference the node/way/relation by id.

        Return OSM wrapper.

        Arguments:
            type_       --- Element type (node/way/relation).
            id_         --- Element id.

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
        Download ways that reference the node by id or wrapper.

        Return OSM wrapper.

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
        server          --- Domain name of OSM API.
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

        get_changeset       --- Download changeset by id.
        get_changeset_full  --- Download changeset contents by id.
        search_changeset    --- Search for changeset by given parameters.
        create_changeset    --- Create changeset.
        update_changeset    --- Update changeset.
        close_changeset     --- Close changeset.

    Methods (required by BaseReadAPI):
        get_capabilities    --- Download and return dictionary with OSM API capabilities.
        get_bbox            --- Download OSM data inside the specified bbox.
        get_element         --- Download node/way/relation by id and optionally version.
        get_element_full    --- Download way/relation by id and all elements that references.
        get_elements        --- Download nodes/ways/relations by ids.
        get_element_rels    --- Download relations that reference the node/way/relation by id.
        get_node_ways       --- Download ways that reference the node by id or wrapper.

    Methods (required by BaseWriteAPI):
        upload_diff         --- OSC diff upload.
        create_element      --- Create node/way/relation.
        update_element      --- Update node/way/relation.
        delete_element      --- Delete node/way/relation.
        delete_elements     --- Delete nodes/ways/relations by ids.

    """

    http = HTTPClient
    log = logging.getLogger("osmapis.api")
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
                                    'tags' - Default tags (dictionary).

        """
        self.username = username
        self.password = password
        if not isinstance(auto_changeset, MutableMapping):
            auto_changeset = {}
        auto_changeset.setdefault("enabled", True)
        auto_changeset.setdefault("size", 200)
        auto_changeset.setdefault("tags", {}).setdefault("created_by", "osmapis/{0}".format(__version__))
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
            changeset   --- Changeset wrapper or changeset id (integer).

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
        return ET.tostring(payload, encoding="utf-8")

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
            path        --- Path to download from server.

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
        Download changeset by id.

        Return Changeset wrapper.

        Arguments:
            id_         --- Changeset id.

        """
        path = "changeset/{}".format(id_)
        return wrappers["changeset"].from_xml(ET.XML(self.get(path)).find("changeset"))

    def get_changeset_full(self, id_):
        """
        Download changeset contents by id.

        Return OSC wrapper.

        Arguments:
            id_         --- Changeset id.

        """
        path = "changeset/{}/download".format(id_)
        return wrappers["osc"].from_xml(self.get(path))

    def search_changeset(self, params):
        """
        Search for changeset by given parameters.

        Return list of changesets.

        Arguments:
            params          --- Dictionary of parameters: bbox, user or
                                display_name, time, open, closed.

        """
        params = "&".join(("{}={}".format(key, value) for key, value in params.items()))
        path = "changesets?{}".format(params)
        data = self.get(path)
        result = []
        for element in ET.XML(self.get(path)).findall("changeset"):
            result.append(wrappers["changeset"].from_xml(element))
        return result

    def create_changeset(self, changeset=None, comment=None):
        """
        Create changeset.

        Return Changeset wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper or None (create new).
            comment     --- Comment tag.

        """
        if changeset is None:
            # No Changset instance provided => create new one
            tags = dict(self.auto_changeset["tags"])
            if comment is not None:
                tags["comment"] = comment
            changeset = wrappers["changeset"](tags=tags)
        elif not isinstance(changeset, Changeset):
            raise TypeError("Changeset must be Changeset instance or None.")
        payload = "<osm>{}</osm>".format(ET.tostring(changeset.to_xml(), encoding="utf-8"))
        path = "changeset/create"
        changeset.attribs["id"] = int(self.put(path, payload))
        return changeset

    def update_changeset(self, changeset):
        """
        Update changeset.

        Return updated Changeset wrapper.

        Arguments:
            changeset   --- Changeset wrapper.

        """
        if not isinstance(changeset, Changeset):
            raise TypeError("Changeset must be Changeset instance.")
        payload = "<osm>{}</osm>".format(ET.tostring(changeset.to_xml(), encoding="utf-8"))
        path = "changeset/{}".format(changeset.id)
        return wrappers["changeset"].from_xml(ET.XML(self.put(path, payload)).find("changeset"))

    def close_changeset(self, changeset):
        """
        Close changeset.

        Arguments:
            changeset   --- Changeset wrapper or changeset id.

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
        Download OSM data inside the specified bbox.

        Return OSM wrapper.

        Arguments:
            left        --- Left boundary.
            bottom      --- Bottom boundary.
            right       --- Right boundary.
            top         --- Top boundary.

        """
        path = "map?bbox={},{},{},{}".format(left, bottom, right, top)
        return wrappers["osm"].from_xml(self.get(path))

    def get_element(self, type_, id_, version=None):
        """
        Download node/way/relation by id and optionally version.

        Return Node/Way/Relation wrapper.

        Arguments:
            type_       --- Element type (node/way/relation).
            id_         --- Element id.

        Keyworded arguments:
            version     --- Element version number, None (latest), or '*' (complete history).

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
        osm = wrappers["osm"].from_xml(self.get(path))
        return getattr(osm, type_ + "s")[id_]

    def get_element_full(self, type_, id_):
        """
        Download way/relation by id and all elements that references.

        Return OSM wrapper.

        Arguments:
            type_       --- Element type (way/relation).
            id_         --- Element id.

        """
        if type_ not in ("way", "relation"):
            raise ValueError("Type must be from {}.".format(", ".join(("way", "relation"))))
        path = "{}/{}/full".format(type_, id_)
        return wrappers["osm"].from_xml(self.get(path))

    def get_elements(self, type_, ids):
        """
        Download nodes/ways/relations by ids.

        Return OSM wrapper.

        Arguments:
            type_       --- Elements type (node/way/relation).
            ids         --- Iterable with ids.

        """
        if type_ not in ("node", "way", "relation"):
            raise ValueError("Type must be 'node', 'way' or 'relation'.")
        path = "{0}s?{0}s={1}".format(type_, ",".join((str(id) for id in ids)))
        return wrappers["osm"].from_xml(self.get(path))

    def get_element_rels(self, type_, id_):
        """
        Download relations that reference the node/way/relation by id.

        Return OSM wrapper.

        Arguments:
            type_       --- Element type (node/way/relation).
            id_         --- Element id.

        """
        if type_ not in ("node", "way", "relation"):
            raise ValueError("Type must be 'node', 'way' or 'relation'.")
        path = "{}/{}/relations".format(type_, id_)
        return wrappers["osm"].from_xml(self.get(path))

    def get_node_ways(self, element):
        """
        Download ways that reference the node by id or wrapper.

        Return OSM wrapper.

        Arguments:
            element     --- Node wrapper or id.

        """
        if isinstance(element, Node):
            element = element.id
        path = "node/{}/ways".format(element)
        return wrappers["osm"].from_xml(self.get(path))


    ##################################################
    # WRITE API                                      #
    ##################################################
    def upload_diff(self, osc, changeset=None):
        """
        OSC diff upload.

        Return {type: {old_id: returned_data} }

        Arguments:
            osc         --- OSC wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

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
        Create node/way/relation.

        Return Node/Way/Relation wrapper.

        Arguments:
            element     --- Node/Way/Relation wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        if not isinstance(element, (Node, Way, Relation)):
            raise TypeError("Element must be a Node, Way or Relation instance.")
        changeset_id = self._changeset_id(changeset)
        path = "{}/{}/create".format(element.xml_tag, element.id)
        payload = "<osm>{}</osm>".format(self._format_payload(element, changeset_id))
        data = self.put(path, payload)
        self._auto_changeset_clear()
        element.attribs["version"] = int(data)
        element.attribs["changeset"] = int(changeset_id)
        element.history = {element.version: element}
        return element

    def update_element(self, element, changeset=None):
        """
        Update node/way/relation.

        Return Node/Way/Relation wrapper.

        Arguments:
            element     --- Node/Way/Relation wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        if not isinstance(element, (Node, Way, Relation)):
            raise TypeError("Element must be a Node, Way or Relation instance.")
        changeset_id = self._changeset_id(changeset)
        path = "{}/{}".format(element.xml_tag, element.id)
        payload = "<osm>{}</osm>".format(self._format_payload(element, changeset_id))
        data = self.put(path, payload)
        self._auto_changeset_clear()
        element.attribs["version"] = int(data)
        element.attribs["changeset"] = int(changeset_id)
        element.history = {element.version: element}
        return element

    def delete_element(self, element, changeset=None):
        """
        Delete node/way/relation.

        Return Node/Way/Relation wrapper.

        Arguments:
            element     --- Node/Way/Relation wrapper.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        if not isinstance(element, (Node, Way, Relation)):
            raise TypeError("Element must be a Node, Way or Relation instance.")
        changeset_id = self._changeset_id(changeset)
        path = "{}/{}".format(element.xml_tag, element.id)
        payload = "<osm>{}</osm>".format(self._format_payload(element, changeset_id, main_tag_only=True))
        data = self.delete(path, payload)
        self._auto_changeset_clear()
        element.attribs["visible"] = False
        element.attribs["version"] = int(data)
        element.attribs["changeset"] = int(changeset_id)
        element.history = {element.version: element}
        return element

    def delete_elements(self, type_, ids, changeset=None):
        """
        Delete nodes/ways/relations by ids.

        Return OSC instance.

        Arguments:
            type_       --- Element type (node/way/relation).
            ids         --- Iterable of ids.

        Keyworded arguments:
            changeset   --- Changeset wrapper, changeset id or None (create new).

        """
        if type_ not in ("node", "way", "relation"):
            raise ValueError("Type must be 'node', 'way' or 'relation'.")
        delete = []
        for element in self.get_elements(type_, ids):
            delete.append(self.delete_element(element, changeset))
        return wrappers["osc"](delete=delete)



############################################################
### Wrappers for OSM Elements and documents.             ###
############################################################

class XMLFile(object):
    """
    Abstract wrapper for XML Elements.

    Abstract methods:
        to_xml          --- Get ET.Element representation of wrapper.
        from_xml        --- Create wrapper from XML representation.

    Class methods:
        load            --- Load the wrapper from file.

    Methods:
        save            --- Save the wrapper into file.

    """

    __metaclass__ = ABCMeta

    @abstractmethod
    def from_xml(cls, data):
        """
        Create wrapper from XML representation.

        Arguments:
            data    --- ET.Element or XML string.

        """
        raise NotImplementedError

    @abstractmethod
    def to_xml(self, strip=()):
        """
        Get ET.Element representation of wrapper.

        Keyworded arguments:
            strip       --- Attributes that should be filtered out.

        """
        raise NotImplementedError

    def save(self, filename):
        """
        Save the wrapper into file.

        Arguments:
            filename        --- Filename where to save the wrapper.

        """
        with open(filename, "w") as fp:
            fp.write('<?xml version="1.0" encoding="UTF-8"?>')
            fp.write(ET.tostring(self.to_xml(), encoding="utf-8"))

    @classmethod
    def load(cls, filename):
        """
        Load the wrapper from file.

        Arguments:
            filename        --- Filename from where to load the wrapper.

        """
        with open(filename) as fp:
            return cls.from_xml(fp.read())


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
            if isinstance(value, (int, float)):
                attribs[key] = str(value)
            elif isinstance(value, bool):
                attribs[key] = str(value).lower()
            else:
                attribs[key] = value
        return attribs


class OSMElement(XMLElement):
    """
    Abstract wrapper for node, way, relation and changeset.

    Class methods:
        parse_tags  --- Extract tags from ET.Element.

    Properties:
        id          --- Id of wrapper, read-only.

    Attributes:
        attribs     --- Attributes of wrapper.
        tags        --- Tags of wrapper.

    Methods:
        to_xml      --- Get ET.Element representation of wrapper.

    """

    @classmethod
    def parse_tags(cls, element):
        """
        Extract tags from ET.Element.

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
        """ id of wrapper """
        return self.attribs.get("id")

    def __init__(self, attribs={}, tags={}):
        self.attribs = dict(attribs)
        self.tags = dict(tags)

    def to_xml(self, strip=()):
        """
        Get ET.Element representation of wrapper.

        Keyworded arguments:
            strip       --- Attributes that should be filtered out.

        """
        attribs = self.unparse_attribs(self.attribs, strip=strip)
        element = ET.Element(self.xml_tag, attribs)
        for key in sorted(self.tags.keys()):
            ET.SubElement(element, "tag", {"k": key, "v": self.tags[key]})
        return element


class OSMPrimitive(OSMElement):
    """
    Abstract wrapper for node, way and relation.

    Properties:
        version     --- Version of node/way/relation, read-only.

    Attributes:
        history     --- Dictionary containing old versions of node/way/relation.

    Methods:
        merge_history   --- Merge history of other wrapper into self.

    """

    @property
    def version(self):
        """ version of node/way/relation """
        return self.attribs.get("version")

    def __init__(self, attribs={}, tags={}):
        OSMElement.__init__(self, attribs, tags)
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
        xml_tag     --- XML tag of the element.
        lat         --- Latitude of the node.
        lon         --- Longitude of the node.

    Class methods:
        from_xml    --- Create Node wrapper from XML representation.

    """

    xml_tag = "node"
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
        attribs = cls.parse_attribs(data)
        tags = cls.parse_tags(data)
        return cls(attribs, tags)

    def __init__(self, attribs={}, tags={}):
        OSMPrimitive.__init__(self, attribs, tags)
        if self.id is None:
            # Automatically asign id
            self.__class__._counter -= 1
            self.attribs["id"] = self.__class__._counter

    def __eq__(self, other):
        if not isinstance(other, Node):
            return NotImplemented
        return self.id == other.id and self.version == other.version and self.tags == other.tags and self.lat == other.lat and self.lon == other.lon

    def __ne__(self, other):
        if not isinstance(other, Node):
            return NotImplemented
        return not self.__eq__(other)

    @property
    def lat(self):
        return self.attribs.get("lat")

    @lat.setter
    def lat(self, value):
        self.attribs["lat"] = float(value)

    @property
    def lon(self):
        return self.attribs.get("lon")

    @lon.setter
    def lon(self, value):
        self.attribs["lon"] = float(value)


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
        xml_tag     --- XML tag of the element.
        nds         --- List of node ids of the way.

    Methods:
        to_xml      --- Get ET.Element representation of wrapper.

    """

    xml_tag = "way"
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
        attribs = cls.parse_attribs(data)
        tags = cls.parse_tags(data)
        nds = cls.parse_nds(data)
        return cls(attribs, tags, nds)

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

    def __init__(self, attribs={}, tags={}, nds=()):
        OSMPrimitive.__init__(self, attribs, tags)
        self.nds = list(nds)
        if self.id is None:
            # Automatically asign id
            self.__class__._counter -= 1
            self.attribs["id"] = self.__class__._counter

    def __eq__(self, other):
        if not isinstance(other, Way):
            return NotImplemented
        return self.id == other.id and self.version == other.version and self.tags == other.tags and self.nds == other.nds

    def __ne__(self, other):
        if not isinstance(other, Way):
            return NotImplemented
        return not self.__eq__(other)

    def __contains__(self, item):
        if not isinstance(item, Node):
            raise NotImplementedError
        return item.id in self.nds

    def to_xml(self, strip=()):
        """
        Get ET.Element representation of wrapper.

        Keyworded arguments:
            strip       --- Attributes that should be filtered out.

        """
        element = OSMPrimitive.to_xml(self, strip=strip)
        for nd in self.nds:
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
        xml_tag         --- XML tag of the element.
        members         --- List of relation members.

    Methods:
        to_xml          --- Get ET.Element representation of wrapper.

    """

    xml_tag = "relation"
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
        attribs = cls.parse_attribs(data)
        tags = cls.parse_tags(data)
        members = cls.parse_members(data)
        return cls(attribs, tags, members)

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

    def __init__(self, attribs={}, tags={}, members=()):
        OSMPrimitive.__init__(self, attribs, tags)
        self.members = list(members)
        if self.id is None:
            # Automatically asign id
            self.__class__._counter -= 1
            self.attribs["id"] = self.__class__._counter

    def __eq__(self, other):
        if not isinstance(other, Relation):
            return NotImplemented
        return self.id == other.id and self.version == other.version and self.tags == other.tags and self.members == other.members

    def __ne__(self, other):
        if not isinstance(other, Relation):
            return NotImplemented
        return not self.__eq__(other)

    def __contains__(self, item):
        if not isinstance(item, (Node, Way, Relation)):
            raise NotImplementedError
        for member in self.members:
            if member["type"] == item.xml_tag and member["ref"] == item.id:
                return True
        return False

    def to_xml(self, strip=()):
        """
        Get ET.Element representation of wrapper.

        Keyworded arguments:
            strip       --- Attributes that should be filtered out.

        """
        element = OSMPrimitive.to_xml(self, strip=strip)
        for member in self.members:
            attribs = self.unparse_attribs(member, strip=strip)
            ET.SubElement(element, "member", attribs)
        return element


class Changeset(OSMElement):
    """
    Changeset wrapper.

    Class methods:
        from_xml        --- Create Changeset wrapper from XML representation.

    Attributes:
        xml_tag         --- XML tag of the element.

    """

    xml_tag = "changeset"

    @classmethod
    def from_xml(cls, data):
        """
        Create Changeset wrapper from XML representation.

        Arguments:
            data    --- ET.Element or XML string.

        """
        if not ET.iselement(data):
            data = XML(data)
        attribs = cls.parse_attribs(data)
        tags = cls.parse_tags(data)
        return cls(attribs, tags)


class OSM(XMLElement, XMLFile, MutableSet):
    """
    OSM XML document wrapper. Essentially a mutable set of Node, Way, Relation wrappers.

    Class methods:
        from_xml    --- Create OSM XML document wrapper from XML representation.

    Attributes:
        nodes       --- Dictionary of nodes {nodeId: Node}.
        ways        --- Dictionary of ways {wayId: Way}.
        relations   --- Dictionary of relations {relationId: Relation}.

    Methods:
        to_xml      --- Get ET.Element representation of wrapper.
        node        --- Retrieve Node wrapper by id or None.
        way         --- Retrieve Way wrapper by id or None.
        relation    --- Retrieve Relation wrapper by id or None.

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
        containers = {"node": {}, "way": {}, "relation": {}}
        for elem_type in containers.keys():
            for element in data.findall(elem_type):
                element = wrappers[elem_type].from_xml(element)
                if element.id in containers[elem_type]:
                    containers[elem_type][element.id] = containers[elem_type][element.id].merge_history(element)
                else:
                    containers[elem_type][element.id] = element
        return cls(chain(containers["node"].values(), containers["way"].values(), containers["relation"].values()))

    def __init__(self, items=()):
        self.nodes = {}
        self.ways = {}
        self.relations = {}
        for item in items:
            self.add(item)

    def __len__(self):
        return len(self.nodes) + len(self.ways) + len(self.relations)

    def __iter__(self):
        return chain(self.nodes.values(), self.ways.values(), self.relations.values())

    def __contains__(self, item):
        if not isinstance(item, (Node, Way, Relation)):
            raise NotImplementedError
        for container, cls in ((self.nodes, Node), (self.ways, Way), (self.relations, Relation)):
            if isinstance(item, cls):
                return container.get(item.id) == item
        return False

    def add(self, item):
        for container, cls in ((self.nodes, Node), (self.ways, Way), (self.relations, Relation)):
            if isinstance(item, cls):
                container[item.id] = item
                return
        raise ValueError("Only Node, Way, Relation instances are allowed.")

    def discard(self, item):
        for container, cls in ((self.nodes, Node), (self.ways, Way), (self.relations, Relation)):
            if isinstance(item, cls):
                container.pop(item.id, None)
                return
        raise ValueError("Only Node, Way, Relation instances are allowed.")

    def to_xml(self, strip=()):
        """
        Get ET.Element representation of wrapper.

        Keyworded arguments:
            strip   --- Attributes that should be filtered out.

        """
        element = ET.Element("osm", {"version": str(API.version), "generator": "osmapis"})
        for child in self:
            element.append(child.to_xml(strip=strip))
        return element

    def node(self, id_):
        """
        Retrieve Node wrapper by id or None.

        Arguments:
            id_     --- Id of the Node wrapper.

        """
        return self.nodes.get(id_)

    def way(self, id_):
        """
        Retrieve Way wrapper by id or None.

        Arguments:
            id_     --- Id of the Way wrapper.

        """
        return self.ways.get(id_)

    def relation(self, id_):
        """
        Retrieve Relation wrapper by id or None.

        Arguments:
            id_     --- Id of the Relation wrapper.

        """
        return self.relations.get(id_)


class OSC(XMLElement, XMLFile):
    """
    OSC XML document wrapper.

    Class methods:
        from_diff   --- Create OSC XML document wrapper by diffing two OSM instances.
        from_xml    --- Create OSC XML document wrapper from XML representation.

    Attributes:
        create      --- OSM instance containing elements to create.
        modify      --- OSM instance containing elements to modify.
        delete      --- OSM instance containing elements to delete.

    Methods:
        to_xml      --- Get ET.Element representation of wrapper.

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
            parent_elements = getattr(parent, type_ + "s")
            child_elements = getattr(child, type_ + "s")
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
        create = wrappers["osm"]()
        modify = wrappers["osm"]()
        delete = wrappers["osm"]()
        for elem_name, container in (("create", create), ("modify", modify), ("delete", delete)):
            for element in data.findall(elem_name):
                container |= wrappers["osm"].from_xml(element)
        return cls(create, modify, delete)

    def __init__(self, create=(), modify=(), delete=()):
        self.create = wrappers["osm"](create)
        self.modify = wrappers["osm"](modify)
        self.delete = wrappers["osm"](delete)

    def to_xml(self, strip=()):
        """
        Get ET.Element representation of wrapper.

        Keyworded arguments:
            strip       --- Attributes that should be filtered out.

        """
        element = ET.Element("osmChange", {"version":str(API.version), "generator":"osmapis"})
        for action in ("create", "modify", "delete"):
            action_element = getattr(self, action).to_xml(strip=strip)
            if len(action_element.getchildren()) > 0:
                action_element.tag = action
                action_element.attrib = {}
                element.append(action_element)
        return element

"""
Dictionary containing the classes to use for OSM element wrappers.

This is the place, where you can set customized wrapper classes.
WARNING: Customized classes should always inherit from the default ones,
         otherwise BAD things will happen!
"""
wrappers = {"node": Node, "way": Way, "relation": Relation,
            "changeset": Changeset, "osm": OSM, "osc": OSC}



############################################################
### Exceptions.                                          ###
############################################################

class APIError(Exception):
    """
    OSM API exception.

    Attributes:
        reason      --- The reason of failure.
        payload     --- Data sent to API with request.

    """

    def __init__(self, reason, payload):
        """
        Arguments:
            reason      --- The reason of failure.
            payload     --- Data sent to API with request.

        """
        self.reason = reason
        self.payload = payload

    def __str__(self):
        return "Request failed: {}".format(self.reason)
