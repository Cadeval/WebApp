
# -*- coding: utf-8 -*-
import asyncio
import csv
import time

from pprint import pprint
from typing import Any, Dict, List, Optional, Set

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.express
import ifcopenshell.express.rules
import ifcopenshell.file
import ifcopenshell.geom
import ifcopenshell.util
import ifcopenshell.util.element
import ifcopenshell.util.schema
import ifcopenshell.util.selector
import ifcopenshell.util.shape
import ifcopenshell.util.unit

from celery import shared_task
from model_manager.backend import ProgressRecorder

def read_config(config_file: str) -> dict:
    config_dict: dict = {}
    with open(config_file, newline="") as csvfile:
        reader = csv.reader(csvfile, delimiter=";", quotechar="'")
        for row in reader:
            config_dict[row[0]] = row[1]
    return config_dict


def get_space_area(space):
    pset = ifcopenshell.util.element.get_pset(space, "Pset_SpaceCommon")
    if pset and "GrossFloorArea" in pset:
        return pset["GrossFloorArea"]
    elif space.Representation:
        settings = ifcopenshell.geom.settings()
        shape = ifcopenshell.geom.create_shape(settings, space)
        return ifcopenshell.util.shape.get_footprint_area(shape.geometry)
    return 0


def get_plot_area(site):
    # Try to get the area from properties
    pset = ifcopenshell.util.element.get_pset(site, "Pset_SiteCommon")
    if pset and "TotalArea" in pset:
        return pset["TotalArea"]
    elif site.Representation:
        settings = ifcopenshell.geom.settings()
        shape = ifcopenshell.geom.create_shape(settings, site)
        face = ifcopenshell.util.shape.get_largest_face(shape.geometry)
        return ifcopenshell.util.shape.get_area(face)
    else:
        spaces = ifcopenshell.util.element.get_decomposition(site)
        total_area = sum(
            get_space_area(space) for space in spaces if space.is_a("IfcSpace")
        )
        return total_area