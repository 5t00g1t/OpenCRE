from pprint import pprint
import logging
import os
import re
import warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, NewType, Optional, Tuple, Union
from application.defs import cre_defs as defs

import semver
import yaml
from dacite import (
    Config,
    ForwardReferenceError,
    StrictUnionMatchError,
    UnexpectedDataError,
    from_dict,
)

logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# used for serialising and deserialising yaml OSIB documents

# default language is 'en'
default_lang = "en"

# Osib_id is an int or a string
Osib_id = NewType("Osib_id", str)

# Lang is a string, e.g. 'en', 'pt_BR')
Lang = NewType("Lang", str)


@dataclass
class _Osib_base:
    def to_dict(self) -> Dict[str, Any]:
        return asdict(
            self,
            dict_factory=lambda x: {
                k: v for (k, v) in x if v not in ["", {}, [], None]
            },
        )


@dataclass
class _Status(_Osib_base):
    """Status attributes decribing OSIB attributes and list items"""

    status: str = field(compare=False, default="")
    reviewed: Optional[int] = field(compare=False, default=None)
    change: str = field(compare=False, default="")


@dataclass
class _Link(_Status):
    """Basic attributes used by `link`s-list items"""

    link: str = field(default="")  # osib id (=osib path to an object)
    type: Optional[str] = field(default=None)


@dataclass
class _Source(_Status):
    """Basic attributes used by i18n sources-directory items"""

    source: Optional[str] = field(default=None)  # url
    name: str = field(compare=False, default="")
    description: Optional[str] = field(compare=False, default=None)


@dataclass
class Node_attributes(_Status):
    """Attributes decribing an OSIB object"""

    source_id: Optional[str] = field(
        default=""
    )  # Unique id name by source, e.g. document
    links: List[_Link] = field(compare=False, default_factory=list)
    categories: Optional[List[str]] = field(compare=False, default=None)
    maturity: Optional[str] = field(compare=False, default=None)
    sources_i18n: Dict[Lang, Optional[_Source]] = field(
        compare=False, default_factory=dict
    )


@dataclass
class Osib_node(_Osib_base):

    """Object-Node for building the OSIB tree"""

    aliases: Optional[List[Osib_id]] = field(compare=False, default=None)
    attributes: Optional[Node_attributes] = field(compare=False, default=None)
    children: Optional[Dict[Union[int, str], "Osib_node"]] = field(
        compare=False, default=None
    )


@dataclass
class Osib_tree(Osib_node):
    """Root-Object for building the OSIB tree"""

    doctype: str = field(compare=False, default="OSIB")
    schema: Optional[str] = field(
        compare=False, default=semver.VersionInfo.parse("0.0.0")
    )
    # Date, when the tree has been comiled as int: YYYYMMDD
    date: Optional[str] = field(compare=False, default=None)


def read_osib_yaml(yaml_file: str = "") -> List[Dict[str, Any]]:
    with open(yaml_file, "r") as fin:
        osib_yaml = yaml.safe_load_all(fin)
        return [y for y in osib_yaml]


def try_from_file(data: List[Dict[str, Any]] = []) -> List[Osib_tree]:
    result = []
    for dat in data:
        result.append(from_dict(data_class=Osib_tree, data=dat))
    return result


def resolve_path(osib_link: _Link = None) -> Tuple[Optional[str], Optional[str]]:
    if osib_link and osib_link.link:
        reg = r"\w+\.\w+\.(?P<name>\w+)\.(?P<section>.+$)"
        match = re.search(reg, osib_link.link)
        if match:
            return (match["name"], match["section"])
    return None, None


def parse_node(
    orgname: str = None,
    root: str = "OSIB",
    name: str = None,
    osib: Osib_node = None,
    current_path: str = None,
    node_type: defs.Credoctypes = None,
):
    result = []
    if (
        osib
        and osib.attributes
        and not osib.children
        and current_path
        and orgname
        and root
    ):
        "register the standard with the current path as subsection"
        p = current_path.replace(f"{root}.{orgname}.", "")
        english_attrs = osib.attributes.sources_i18n.get("en")
        if english_attrs:
            res = defs.Document(
                doctype=node_type,
                name=name,
                section=p,
                hyperlink=english_attrs.get("source"),
            )
            res.metadata = defs.Metadata()
            if osib.aliases:
                res.metadata.labels["alias"] = [x for x in osib.aliases]
            if osib.attributes.source_id:
                res.metadata.labels["source_id"] = osib.attributes.source_id
            if osib.attributes.maturity:
                res.metadata.labels["maturity"] = osib.attributes.maturity
            if osib.attributes.categories:
                res.metadata.labels["categories"] = [
                    x for x in osib.attributes.categories
                ]

            for olink in osib.attributes.links:
                docname, docsection = resolve_path(olink)
                if olink.type:
                    if olink.type.lower() == "parent":
                        res.add_link(
                            link=defs.Link(document=res, ltype=defs.LinkTypes.PartOf)
                        )
                    elif olink.type.lower() == "child":
                        res.add_link(
                            link=defs.Link(document=res, ltype=defs.LinkTypes.Contains)
                        )
                    elif olink.type.lower() == "related":
                        res.add_link(
                            link=defs.Link(document=res, ltype=defs.LinkTypes.Related)
                        )
        return res

    for section, child in osib.children.items():
        if child.children:
            result.extend(
                parse_node(
                    orgname=orgname,
                    root=root,
                    name=name,
                    osib=child,
                    current_path=f"{current_path}.{section}",
                    node_type=node_type,
                )
            )
    return result


def osib2cre(tree: Osib_tree) -> Optional[Tuple[List[defs.CRE], List[defs.Document]]]:
    tree_aliases = tree.aliases
    attrs = tree.attributes
    standards: List[defs.Document] = []
    cres: List[defs.CRE] = []
    root = tree.doctype
    if not tree.children:
        return None
    for orgname, org in tree.children.items():
        if org.children:
            for pname, project in org.children.items():
                if str(pname).lower() != "cre":
                    standards.extend(
                        parse_node(
                            root=root,
                            orgname=str(orgname),
                            name=str(pname),
                            osib=project,
                            node_type=defs.Credoctypes.Standard,
                        )
                    )
                else:
                    cres.extend(
                        parse_node(
                            root=root,
                            orgname=str(orgname),
                            osib=project,
                            node_type=defs.Credoctypes.CRE,
                        )
                    )
    return (cres, standards)


def cre2osib(docs: List[defs.Document]) -> List[Osib_tree]:
    pass
