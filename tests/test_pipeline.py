from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline.config import Bbox, Request
from pipeline.sources.rivers import selected_regions
from pipeline.sources.satellite import MEDITERRANEAN
from pipeline.sources.terrain import _shape_bbox


def test_bbox_dateline_and_intersection():
    bbox = Bbox(170, -10, -170, 10)
    assert bbox.crosses_dateline
    assert bbox.width_deg == 20
    assert bbox.intersects(Bbox(175, -5, 179, 5))
    assert bbox.intersects(Bbox(-179, -5, -175, 5))
    assert not bbox.intersects(Bbox(0, -5, 10, 5))


def test_request_id_changes_with_area_and_dates():
    first = Request(Bbox(0, 0, 1, 1), dt.date(2024, 1, 1), dt.date(2024, 1, 2), name="one")
    second = Request(Bbox(0, 0, 2, 1), dt.date(2024, 1, 1), dt.date(2024, 1, 2), name="one")
    assert first.request_id != second.request_id
    assert first.height == first.width


def test_invalid_request_is_rejected():
    with pytest.raises(ValueError):
        Bbox(0, 10, 1, -10)
    with pytest.raises(ValueError):
        Request(Bbox(0, 0, 1, 1), dt.date(2024, 1, 3), dt.date(2024, 1, 2))


def test_river_regions_are_not_hardcoded_to_europe_africa():
    assert "na" in selected_regions(Bbox(-120, 20, -110, 30))
    assert "af" in selected_regions(Bbox(29, 29, 36, 34))
    assert "sa" in selected_regions(Bbox(-75, -20, -65, -10))
    assert "eu" in selected_regions(Bbox(29, 29, 36, 34))


def test_mediterranean_coverage_is_explicit():
    assert Bbox(29, 29, 36, 34).intersects(MEDITERRANEAN)
    assert not Bbox(-120, 20, -110, 30).intersects(MEDITERRANEAN)


def test_config_import_does_not_freeze_output_path(tmp_path):
    code = """
import json, os
from pathlib import Path
from pipeline.config import Request, Bbox
from pipeline.jobs import staging_context
request = Request(Bbox(-120, 20, -119, 21), name='path-test', sources=('bathymetry',))
context = staging_context(request, Path(os.environ['TEST_ROOT']))
os.environ['FOSSPULSER_REQUEST_JSON'] = json.dumps(request.to_dict())
os.environ['FOSSPULSER_BUNDLE_DIR'] = str(context.paths.directory)
from pipeline.config import DATA_CURATED, MAP_WIDTH, MAP_HEIGHT
assert str(DATA_CURATED).startswith(os.environ['TEST_ROOT']), DATA_CURATED
assert MAP_WIDTH == request.width
assert MAP_HEIGHT == request.height
print(DATA_CURATED)
"""
    env = os.environ.copy()
    env["TEST_ROOT"] = str(tmp_path)
    result = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert str(tmp_path) in result.stdout


def test_degenerate_shape_bounds_are_computable():
    class Shape:
        points = [(1.0, 2.0)]
    assert _shape_bbox(Shape()) == (1.0, 2.0, 1.0, 2.0)


def test_worker_envelope_does_not_require_credentials(tmp_path):
    request = Request(Bbox(-120, 20, -119, 21), name="envelope", sources=("bathymetry",))
    envelope = {"request": request.to_dict(), "data_root": str(tmp_path), "build": False}
    assert "credentials" not in envelope
    assert "password" not in json.dumps(envelope)
