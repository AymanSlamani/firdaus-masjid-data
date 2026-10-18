"""Assign missing state codes from US Census public cartographic boundaries."""

import io
from pathlib import Path
import zipfile
import shapefile


def inside_ring(x, y, ring):
    inside = False
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[i - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def inside_polygon(x, y, rings):
    return inside_ring(x, y, rings[0]) and not any(
        inside_ring(x, y, hole) for hole in rings[1:]
    )


class CensusStates:
    def __init__(self, path: Path):
        z = zipfile.ZipFile(path)
        reader = shapefile.Reader(
            shp=io.BytesIO(z.read(next(n for n in z.namelist() if n.endswith(".shp")))),
            dbf=io.BytesIO(z.read(next(n for n in z.namelist() if n.endswith(".dbf")))),
        )
        self.shapes = []
        for record in reader.iterShapeRecords():
            geometry = record.shape.__geo_interface__
            polygons = (
                geometry["coordinates"]
                if geometry["type"] == "MultiPolygon"
                else [geometry["coordinates"]]
            )
            self.shapes.append((record.record["STUSPS"], polygons, record.shape.bbox))

    def state(self, lng, lat):
        for state, polygons, bbox in self.shapes:
            if not bbox[0] <= lng <= bbox[2] or not bbox[1] <= lat <= bbox[3]:
                continue
            if any(inside_polygon(lng, lat, p) for p in polygons):
                return state
        return None
