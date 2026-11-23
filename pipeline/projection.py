"""Pixel-space geographic projection used by every 2D layer in FossPulser.

We deliberately avoid trying to match forge3d's internal 3D camera projection
for the precise data map (that would require calibrating an unpublished
camera model). Instead all 2D layers -- hillshade basemap, chlorophyll glow,
rivers, country borders, city/facility pins -- are projected with the *same*
simple equirectangular affine transform, so they are pixel-perfect against
each other by construction. The forge3d-rendered oblique 3D view is a
separate, complementary "cinematic" panel (see render_3d.py) that is not
expected to align pixel-for-pixel with this map.
"""
from __future__ import annotations

from pipeline.config import LAND_BBOX, MAP_HEIGHT, MAP_WIDTH


class Projection:
    def __init__(self, bbox=LAND_BBOX, width=MAP_WIDTH, height=MAP_HEIGHT):
        self.west, self.south, self.east, self.north = bbox
        self.width = width
        self.height = height
        self.sx = width / (self.east - self.west)
        self.sy = height / (self.north - self.south)

    def lonlat_to_px(self, lon, lat):
        x = (lon - self.west) * self.sx
        y = (self.north - lat) * self.sy
        return x, y

    def px_to_lonlat(self, x, y):
        lon = self.west + x / self.sx
        lat = self.north - y / self.sy
        return lon, lat


DEFAULT = Projection()
