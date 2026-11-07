#!/usr/bin/env python3
"""
fla_svg_extractor.py

Extract vector artwork from Adobe Animate / Flash ``.fla`` files and write
structured SVG assets.

How it works
------------
Modern ``.fla`` files (CS5 and later) are ZIP archives containing an
XML-based project format called XFL.  This script:

1. Finds every ``.fla`` file in the current directory (or the paths you pass).
2. Copies each one to a temporary path with a ``.zip`` extension.
3. Extracts the archive into a temporary directory.
4. Reads the symbol XML files in ``LIBRARY/``.
5. Converts every ``<DOMShape>`` (including shapes inside groups and, by
   default, symbol instances) into SVG paths.
6. Preserves the Flash layer structure as ``<g data-layer="...">`` groups.
7. Writes one SVG per library symbol, plus an optional per-layer SVG set.

Output layout::

    svg_assets/
        MyMovie/
            Symbol 1.svg
            Symbol 2.svg
            manifest.json
            layers/                 # only with --split-layers
                Symbol 1__Background.svg
                Symbol 1__Accent.svg

Usage
-----
::

    python fla_svg_extractor.py
    python fla_svg_extractor.py movie.fla other.fla --output out
    python fla_svg_extractor.py --all-frames --split-layers

The script uses only the Python standard library (Python 3.8+).

Notes
-----
* XFL edge coordinates are stored in twips (1/20 px) and are converted to
  pixels.  Hex-encoded fixed-point coordinates (``#BD9.4D``) are supported.
* Only XFL/ZIP-based ``.fla`` files are supported.  The older OLE2/binary
  format (Flash 8 and earlier) cannot be parsed this way and is reported and
  skipped.
* Bitmap fills, text, and audio are not vectorised.  Unsupported elements are
  counted in ``manifest.json``.
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import math
import re
import shutil
import struct
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
import xml.etree.ElementTree as ET


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SVG_NS = "http://www.w3.org/2000/svg"

# Flash gradient space is -819.2 .. +819.2 in shape pixels (16384 twips).
GRADIENT_HALF_SPAN = 16384.0 / 20.0

# XFL edge data is expressed in twips.
TWIPS_PER_PIXEL = 20.0

# Windows reserved file names (case-insensitive).
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


# ---------------------------------------------------------------------------
# Small XML helpers (XFL uses a default namespace, so strip it everywhere)
# ---------------------------------------------------------------------------

def local_name(tag: str) -> str:
    """Return the local part of an ElementTree tag."""
    return tag.rsplit("}", 1)[-1]


def child(element: Optional[ET.Element], name: str) -> Optional[ET.Element]:
    """Return the first direct child with the given local tag name."""
    if element is None:
        return None
    for item in element:
        if local_name(item.tag) == name:
            return item
    return None


def children(element: Optional[ET.Element], name: str) -> List[ET.Element]:
    """Return all direct children with the given local tag name."""
    if element is None:
        return []
    return [item for item in element if local_name(item.tag) == name]


def first_descendant(element: Optional[ET.Element], name: str) -> Optional[ET.Element]:
    """Return the first descendant (including ``element``) with a tag name."""
    if element is None:
        return None
    for item in element.iter():
        if local_name(item.tag) == name:
            return item
    return None


# ---------------------------------------------------------------------------
# Number formatting
# ---------------------------------------------------------------------------

def fmt(value: float, precision: int = 4) -> str:
    """Format a float compactly for SVG attributes."""
    if value is None:
        return "0"
    value = float(value)
    if abs(value) < 10 ** (-precision) / 2.0:
        value = 0.0
    text = f"{value:.{precision}f}".rstrip("0").rstrip(".")
    if text in ("", "-0"):
        return "0"
    return text


def make_point(x: float, y: float) -> Tuple[float, float]:
    """Round a point to the precision used for edge stitching."""
    return (round(float(x), 3), round(float(y), 3))


def point_str(point: Tuple[float, float]) -> str:
    return f"{fmt(point[0], 3)} {fmt(point[1], 3)}"


# ---------------------------------------------------------------------------
# 2D affine matrices
# ---------------------------------------------------------------------------

class Matrix:
    """A 2D affine matrix in the same layout as SVG.

    ::

        | a c tx |
        | b d ty |
        | 0 0  1 |
    """

    __slots__ = ("a", "b", "c", "d", "tx", "ty")

    def __init__(self, a=1.0, b=0.0, c=0.0, d=1.0, tx=0.0, ty=0.0):
        self.a = float(a)
        self.b = float(b)
        self.c = float(c)
        self.d = float(d)
        self.tx = float(tx)
        self.ty = float(ty)

    @classmethod
    def identity(cls) -> "Matrix":
        return cls()

    @classmethod
    def from_element(cls, element: Optional[ET.Element]) -> "Matrix":
        """Read a matrix from an XFL element containing ``<matrix><Matrix/></matrix>``."""
        if element is None:
            return cls()
        matrix_wrapper = child(element, "matrix")
        if matrix_wrapper is None:
            # Allow passing a <Matrix> element directly.
            if local_name(element.tag) == "Matrix":
                matrix_wrapper = element
            else:
                return cls()
        inner = child(matrix_wrapper, "Matrix") or matrix_wrapper

        def get(name: str, default: float) -> float:
            raw = inner.get(name)
            if raw is None or raw == "":
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        return cls(
            get("a", 1.0),
            get("b", 0.0),
            get("c", 0.0),
            get("d", 1.0),
            get("tx", 0.0),
            get("ty", 0.0),
        )

    def __mul__(self, other: "Matrix") -> "Matrix":
        """Compose matrices: ``self * other`` applies ``other`` first."""
        return Matrix(
            self.a * other.a + self.c * other.b,
            self.b * other.a + self.d * other.b,
            self.a * other.c + self.c * other.d,
            self.b * other.c + self.d * other.d,
            self.a * other.tx + self.c * other.ty + self.tx,
            self.b * other.tx + self.d * other.ty + self.ty,
        )

    def apply(self, x: float, y: float) -> Tuple[float, float]:
        return (self.a * x + self.c * y + self.tx,
                self.b * x + self.d * y + self.ty)

    def is_identity(self) -> bool:
        return (
            abs(self.a - 1.0) < 1e-9
            and abs(self.b) < 1e-9
            and abs(self.c) < 1e-9
            and abs(self.d - 1.0) < 1e-9
            and abs(self.tx) < 1e-9
            and abs(self.ty) < 1e-9
        )

    def scale(self) -> float:
        """Approximate uniform scale factor (square root of |determinant|)."""
        return math.sqrt(abs(self.a * self.d - self.b * self.c))

    def to_svg(self) -> str:
        return "matrix({} {} {} {} {} {})".format(
            fmt(self.a, 6), fmt(self.b, 6), fmt(self.c, 6),
            fmt(self.d, 6), fmt(self.tx, 6), fmt(self.ty, 6),
        )


# ---------------------------------------------------------------------------
# Bounding box
# ---------------------------------------------------------------------------

class BBox:
    def __init__(self):
        self.min_x = math.inf
        self.min_y = math.inf
        self.max_x = -math.inf
        self.max_y = -math.inf

    def add(self, x: float, y: float) -> None:
        if x < self.min_x:
            self.min_x = x
        if y < self.min_y:
            self.min_y = y
        if x > self.max_x:
            self.max_x = x
        if y > self.max_y:
            self.max_y = y

    def add_point_list(self, point_list: Sequence, matrix: Matrix) -> None:
        for item in point_list:
            point = item[1] if isinstance(item, tuple) else item
            x, y = matrix.apply(point[0], point[1])
            self.add(x, y)

    def is_empty(self) -> bool:
        return not math.isfinite(self.min_x)

    def expanded(self, pad: float) -> Tuple[float, float, float, float]:
        return (
            self.min_x - pad,
            self.min_y - pad,
            self.max_x + pad,
            self.max_y + pad,
        )


# ---------------------------------------------------------------------------
# XFL edge-data parsing
# ---------------------------------------------------------------------------

# Commands: ! = move, | and / = line, [ and ] = quadratic curve.
# Numbers: decimal twips or signed 32-bit fixed-point hex (#BD9.4D).
# "S" style-selection tokens are ignored.  The negative lookbehind prevents
# the digit in "S4" from being parsed as a coordinate.
EDGE_TOKENIZER = re.compile(
    r"""
    [!|/\[\]]                            |
    (?<!S)-?(?:\d+(?:\.\d*)?|\.\d+)      |
    \#[0-9A-Fa-f]+\.[0-9A-Fa-f]+
    """,
    re.VERBOSE,
)


def parse_edge_number(token: str) -> float:
    """Parse one XFL edge number and convert twips to pixels."""
    if token.startswith("#"):
        integer, _, fraction = token[1:].partition(".")
        # Pad the integer part to 6 hex digits and the fraction to 2, giving
        # a signed 32-bit fixed-point value scaled by 256.
        hex_digits = f"{integer:>06}{fraction:<02}"
        raw = int(hex_digits, 16)
        if raw >= 0x80000000:
            raw -= 0x100000000
        return (raw / 256.0) / TWIPS_PER_PIXEL
    return float(token) / TWIPS_PER_PIXEL


def edge_to_point_lists(data: str) -> List[List]:
    """Convert an XFL ``edges`` attribute into point lists.

    A point list is a list of ``(x, y)`` tuples.  Quadratic control points are
    stored as ``("Q", (x, y))`` immediately before their destination point.
    Reversing such a list (``list(reversed(...))``) correctly reverses the
    path, which is what fill-style stitching relies on.
    """
    tokens = EDGE_TOKENIZER.findall(data)
    if not tokens:
        return []

    try:
        index = tokens.index("!")
    except ValueError:
        return []

    def read_point(i: int) -> Tuple[Tuple[float, float], int]:
        x = parse_edge_number(tokens[i])
        y = parse_edge_number(tokens[i + 1])
        return make_point(x, y), i + 2

    try:
        current, index = read_point(index + 1)
    except (IndexError, ValueError):
        return []

    point_list: List = [current]
    result: List[List] = []

    try:
        while index < len(tokens):
            command = tokens[index]
            index += 1

            if command == "!":
                point, index = read_point(index)
                if point != current:
                    result.append(point_list)
                    point_list = [point]
                    current = point
            elif command in ("|", "/"):
                point, index = read_point(index)
                point_list.append(point)
                current = point
            elif command in ("[", "]"):
                control, index = read_point(index)
                destination, index = read_point(index)
                point_list.append(("Q", control))
                point_list.append(destination)
                current = destination
            else:
                # Unknown token: stop rather than desynchronise the stream.
                break
    except (IndexError, ValueError):
        pass

    result.append(point_list)
    return [points for points in result if len(points) >= 2]


def point_list_to_d(point_list: Sequence) -> str:
    """Convert one point list into an SVG path ``d`` string."""
    if not point_list:
        return ""

    parts = ["M", point_str(point_list[0])]
    last_command = "M"
    index = 1

    while index < len(point_list):
        item = point_list[index]
        if isinstance(item, tuple):
            # Quadratic control point, followed by the destination.
            if index + 1 >= len(point_list):
                break
            control = item[1]
            destination = point_list[index + 1]
            if last_command != "Q":
                parts.append("Q")
            parts.append(point_str(control))
            parts.append(point_str(destination))
            last_command = "Q"
            index += 2
        else:
            if last_command != "L":
                parts.append("L")
            parts.append(point_str(item))
            last_command = "L"
            index += 1

    if len(point_list) > 1 and point_list[0] == point_list[-1]:
        parts.append("Z")

    return " ".join(parts)


def stitch_point_lists(point_lists: List[List]) -> List[List]:
    """Join open point lists into closed contours for one fill style.

    Flash stores each shared edge once, tagged with the fill on either side.
    To draw a filled region we collect all of its directed edges and connect
    them end-to-start.  If a contour cannot be closed, it is returned as an
    open path (SVG fills it as if closed).
    """
    closed: List[List] = []
    graph: Dict[Tuple[float, float], List[List]] = defaultdict(list)

    for points in point_lists:
        if len(points) < 2:
            continue
        if points[0] == points[-1]:
            closed.append(points)
        else:
            graph[points[0]].append(points)

    def walk(
        current: Tuple[float, float],
        used: set,
        origin: Tuple[float, float],
    ) -> Optional[List]:
        available = graph[current]
        for i in range(len(available)):
            candidate = available[i]
            next_point = candidate[-1]

            if next_point == origin:
                del available[i]
                return candidate

            if next_point not in used:
                used.add(next_point)
                tail = walk(next_point, used, origin)
                if tail is None:
                    used.remove(next_point)
                else:
                    del available[i]
                    return candidate + tail[1:]

        return None

    for origin in list(graph.keys()):
        while graph[origin]:
            first = graph[origin].pop()
            tail = walk(first[-1], {origin, first[-1]}, origin)
            if tail is None:
                closed.append(first)
            else:
                closed.append(first + tail[1:])

    return closed


# ---------------------------------------------------------------------------
# Fill and stroke styles
# ---------------------------------------------------------------------------

def _solid_color(element: ET.Element) -> Tuple[str, Optional[float]]:
    """Return ``(color, alpha)`` from an XFL ``<SolidColor>`` element."""
    color = element.get("color", "#000000")
    alpha: Optional[float] = None

    raw_alpha = element.get("alpha")
    if raw_alpha is not None and raw_alpha != "":
        try:
            alpha = float(raw_alpha)
        except ValueError:
            alpha = None

    # Some exporters write #RRGGBBAA instead of a separate alpha attribute.
    if len(color) == 9 and color.startswith("#"):
        try:
            alpha = int(color[7:9], 16) / 255.0
        except ValueError:
            pass
        color = color[:7]

    return color, alpha


def _gradient_stops(element: ET.Element) -> List[ET.Element]:
    entries = children(element, "GradientEntry")
    stops: List[ET.Element] = []
    for entry in entries:
        try:
            offset = float(entry.get("ratio", "0")) * 100.0
        except ValueError:
            offset = 0.0
        attributes = {
            "offset": f"{fmt(offset, 3)}%",
            "stop-color": entry.get("color", "#000000"),
        }
        alpha = entry.get("alpha")
        if alpha is not None and alpha != "":
            try:
                attributes["stop-opacity"] = fmt(float(alpha), 4)
            except ValueError:
                pass
        stops.append(ET.Element("stop", attributes))

    if not stops:
        stops.append(ET.Element("stop", {"offset": "0%", "stop-color": "#000000"}))
        stops.append(ET.Element("stop", {"offset": "100%", "stop-color": "#000000"}))
    return stops


def build_gradient(element: ET.Element, kind: str, renderer: "SymbolRenderer") -> str:
    """Add a gradient to the renderer's ``<defs>`` and return its id."""
    gradient_id = f"gradient-{renderer.gradient_counter}"
    renderer.gradient_counter += 1

    matrix = Matrix.from_element(element)
    stops = _gradient_stops(element)

    if kind == "LinearGradient":
        start_x, start_y = matrix.apply(-GRADIENT_HALF_SPAN, 0.0)
        end_x, end_y = matrix.apply(GRADIENT_HALF_SPAN, 0.0)
        attributes = {
            "id": gradient_id,
            "gradientUnits": "userSpaceOnUse",
            "x1": fmt(start_x, 4),
            "y1": fmt(start_y, 4),
            "x2": fmt(end_x, 4),
            "y2": fmt(end_y, 4),
        }
        spread = element.get("spreadMethod")
        if spread:
            attributes["spreadMethod"] = spread
        gradient = ET.Element("linearGradient", attributes)
    else:
        # RadialGradient: the gradient space is a circle of radius 819.2.
        center_x, center_y = matrix.apply(0.0, 0.0)
        edge_x, edge_y = matrix.apply(GRADIENT_HALF_SPAN, 0.0)
        radius = math.hypot(edge_x - center_x, edge_y - center_y)
        try:
            focal_ratio = float(element.get("focalPointRatio", "0"))
        except ValueError:
            focal_ratio = 0.0
        focal_x, focal_y = matrix.apply(focal_ratio * GRADIENT_HALF_SPAN, 0.0)
        attributes = {
            "id": gradient_id,
            "gradientUnits": "userSpaceOnUse",
            "cx": fmt(center_x, 4),
            "cy": fmt(center_y, 4),
            "r": fmt(radius, 4),
            "fx": fmt(focal_x, 4),
            "fy": fmt(focal_y, 4),
        }
        spread = element.get("spreadMethod")
        if spread:
            attributes["spreadMethod"] = spread
        gradient = ET.Element("radialGradient", attributes)

    gradient.extend(stops)
    renderer.defs.append(gradient)
    return gradient_id


def parse_fill_style(style_element: ET.Element, renderer: "SymbolRenderer") -> Dict[str, str]:
    """Convert a ``<FillStyle>`` element into SVG path attributes."""
    inner = style_element[0] if len(style_element) else None
    if inner is None:
        return {"fill": "#000000", "stroke": "none"}

    kind = local_name(inner.tag)

    if kind == "SolidColor":
        color, alpha = _solid_color(inner)
        attributes = {"fill": color, "stroke": "none"}
        if alpha is not None and alpha < 1.0:
            attributes["fill-opacity"] = fmt(alpha, 4)
        return attributes

    if kind in ("LinearGradient", "RadialGradient"):
        gradient_id = build_gradient(inner, kind, renderer)
        return {"fill": f"url(#{gradient_id})", "stroke": "none"}

    # Bitmap fills and anything unknown: use a neutral grey so the geometry is
    # still visible and mark the limitation for downstream tooling.
    return {
        "fill": "#808080",
        "stroke": "none",
        "data-unsupported-fill": kind,
    }


def parse_stroke_style(style_element: ET.Element, renderer: "SymbolRenderer") -> Dict[str, str]:
    """Convert a ``<StrokeStyle>`` element into SVG path attributes."""
    inner = style_element[0] if len(style_element) else None
    if inner is None:
        return {"fill": "none"}

    attributes: Dict[str, str] = {
        "fill": "none",
        "stroke": "#000000",
        "stroke-width": "1",
    }

    fill_element = child(inner, "fill")
    if fill_element is not None and len(fill_element):
        paint = fill_element[0]
        paint_kind = local_name(paint.tag)
        if paint_kind == "SolidColor":
            color, alpha = _solid_color(paint)
            attributes["stroke"] = color
            if alpha is not None and alpha < 1.0:
                attributes["stroke-opacity"] = fmt(alpha, 4)
        elif paint_kind in ("LinearGradient", "RadialGradient"):
            gradient_id = build_gradient(paint, paint_kind, renderer)
            attributes["stroke"] = f"url(#{gradient_id})"

    weight = inner.get("weight")
    if weight:
        try:
            numeric_weight = float(weight)
            if numeric_weight <= 0.0:
                numeric_weight = 0.05
            attributes["stroke-width"] = fmt(numeric_weight, 4)
        except ValueError:
            pass

    if inner.get("solidStyle") == "hairline":
        attributes["stroke-width"] = "0.05"

    caps = inner.get("caps", "round")
    if caps == "none":
        caps = "butt"
    attributes["stroke-linecap"] = caps

    joints = inner.get("joints", "round")
    attributes["stroke-linejoin"] = joints

    if joints == "miter":
        attributes["stroke-miterlimit"] = inner.get("miterLimit", "5")

    return attributes


# ---------------------------------------------------------------------------
# ZIP handling (including Animate's occasionally broken EOCD record)
# ---------------------------------------------------------------------------

def open_fla_zip(path: Path) -> zipfile.ZipFile:
    """Open a copied ``.fla``/``.zip`` archive.

    Animate sometimes writes an end-of-central-directory record whose
    "size of central directory" field is 54 bytes too large.  ``zipfile``
    rejects those archives, so patch the field in memory before opening.
    """
    data = bytearray(path.read_bytes())

    if data[:4] == b"\xd0\xcf\x11\xe0":
        raise zipfile.BadZipFile(
            "this is a binary OLE2 FLA (Flash 8 or earlier), not an XFL/ZIP file"
        )

    eocd_signature = b"PK\x05\x06"
    eocd_index = data.rfind(eocd_signature)
    if eocd_index != -1 and eocd_index + 22 <= len(data):
        cd_size, cd_offset = struct.unpack_from("<II", data, eocd_index + 12)
        comment_length = struct.unpack_from("<H", data, eocd_index + 20)[0]
        actual_cd_size = len(data) - cd_offset - 22 - comment_length
        if cd_size != actual_cd_size and cd_size - actual_cd_size == 54:
            struct.pack_into("<I", data, eocd_index + 12, actual_cd_size)

    return zipfile.ZipFile(io.BytesIO(bytes(data)))


def safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    """Extract an archive, rejecting absolute paths and ``..`` traversal."""
    destination = destination.resolve()
    for info in archive.infolist():
        raw_name = info.filename.replace("\\", "/")
        parts = [part for part in raw_name.split("/") if part not in ("", ".")]
        if not parts:
            continue

        if (
            raw_name.startswith("/")
            or re.match(r"^[A-Za-z]:", raw_name)
            or any(part == ".." for part in parts)
        ):
            raise ValueError(f"unsafe path in archive: {info.filename}")

        target = destination.joinpath(*parts)
        if info.is_dir() or raw_name.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, open(target, "wb") as output:
            shutil.copyfileobj(source, output)


def find_xfl_root(extract_dir: Path) -> Path:
    """Find the directory containing ``DOMDocument.xml``."""
    candidates = sorted(
        extract_dir.rglob("DOMDocument.xml"),
        key=lambda item: len(item.parts),
    )
    return candidates[0].parent if candidates else extract_dir


def find_library_dir(root: Path) -> Optional[Path]:
    """Find the ``LIBRARY/`` directory (case-insensitive)."""
    for item in root.iterdir():
        if item.is_dir() and item.name.lower() == "library":
            return item
    for item in root.rglob("*"):
        if item.is_dir() and item.name.lower() == "library":
            return item
    return None


# ---------------------------------------------------------------------------
# Library symbol index
# ---------------------------------------------------------------------------

class Library:
    """Index of every ``DOMSymbolItem`` XML file in ``LIBRARY/``."""

    def __init__(self, library_dir: Path, log):
        self.library_dir = library_dir
        self.log = log
        self.entries: List[Tuple[str, Path, ET.Element]] = []
        self._by_key: Dict[str, Path] = {}
        self._cache: Dict[Path, ET.Element] = {}

        for path in sorted(library_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() != ".xml":
                continue
            try:
                root = ET.parse(path).getroot()
            except (ET.ParseError, OSError) as exc:
                log(f"  ! could not parse {path.name}: {exc}")
                continue
            if local_name(root.tag) != "DOMSymbolItem":
                continue

            name = root.get("name") or path.stem
            self.entries.append((name, path, root))
            self._cache[path] = root

            self._register(name, path)
            self._register(path.stem, path)
            self._register(name.replace("&", "_"), path)
            self._register(path.stem.replace("&", "_"), path)

            try:
                relative = path.relative_to(library_dir).with_suffix("").as_posix()
            except ValueError:
                relative = path.stem
            self._register(relative, path)
            self._register(relative.replace("&", "_"), path)

    def _register(self, key: str, path: Path) -> None:
        if key and key not in self._by_key:
            self._by_key[key] = path

    def resolve(self, name: Optional[str]) -> Optional[ET.Element]:
        """Resolve a ``libraryItemName`` to its parsed symbol element."""
        if not name:
            return None

        candidates: List[str] = [name]
        if name.lower().endswith(".xml"):
            candidates.append(name[:-4])
        candidates.extend(candidate.replace("&", "_") for candidate in list(candidates))

        base = candidates[0].rsplit("/", 1)[-1]
        candidates.append(base)
        candidates.append(base.replace("&", "_"))

        for key in candidates:
            path = self._by_key.get(key)
            if path is not None:
                return self._load(path)
        return None

    def _load(self, path: Path) -> ET.Element:
        if path not in self._cache:
            self._cache[path] = ET.parse(path).getroot()
        return self._cache[path]


# ---------------------------------------------------------------------------
# Symbol renderer
# ---------------------------------------------------------------------------

class Options:
    def __init__(
        self,
        all_frames: bool = False,
        split_layers: bool = False,
        inline_instances: bool = True,
        max_depth: int = 8,
        include_guides: bool = False,
    ):
        self.all_frames = all_frames
        self.split_layers = split_layers
        self.inline_instances = inline_instances
        self.max_depth = max_depth
        self.include_guides = include_guides


class SymbolRenderer:
    """Render one library symbol (and nested instances) to an SVG element."""

    def __init__(self, library: Library, options: Options, source_name: str):
        self.library = library
        self.options = options
        self.source_name = source_name

        self.defs = ET.Element("defs")
        self.bbox = BBox()
        self.layer_counter = 0
        self.gradient_counter = 0
        self.max_stroke_scaled = 0.0
        self.layer_groups: List[Tuple[str, ET.Element]] = []
        self.warnings: List[str] = []
        self.skipped = defaultdict(int)

    # -- public entry point -------------------------------------------------

    def render(self, symbol_element: ET.Element, symbol_name: str) -> ET.Element:
        self.defs = ET.Element("defs")
        self.bbox = BBox()
        self.layer_counter = 0
        self.gradient_counter = 0
        self.max_stroke_scaled = 0.0
        self.layer_groups = []
        self.warnings = []
        self.skipped = defaultdict(int)

        svg = ET.Element("svg", {"xmlns": SVG_NS, "version": "1.1"})
        ET.SubElement(svg, "title").text = symbol_name
        description = ET.SubElement(svg, "desc")
        description.text = (
            f"Vector layers extracted from {self.source_name} (LIBRARY/{symbol_name})"
        )

        root_group = ET.SubElement(
            svg, "g", {"id": "symbol", "data-symbol": symbol_name}
        )

        layers_element = self._layers_of(symbol_element)
        if layers_element is not None:
            self._render_layers(
                layers_element,
                root_group,
                Matrix.identity(),
                first_frame_only=not self.options.all_frames,
                start_frame=0,
                depth=0,
                stack=frozenset([symbol_name]),
            )

        if len(self.defs) > 0:
            svg.insert(2, self.defs)  # after <title> and <desc>

        if self.bbox.is_empty():
            view_box = "0 0 1 1"
            width = height = 1.0
        else:
            pad = max(1.0, self.max_stroke_scaled / 2.0 + 1.0)
            min_x, min_y, max_x, max_y = self.bbox.expanded(pad)
            width = max(max_x - min_x, 1.0)
            height = max(max_y - min_y, 1.0)
            view_box = "{} {} {} {}".format(
                fmt(min_x, 2), fmt(min_y, 2), fmt(width, 2), fmt(height, 2)
            )

        svg.set("viewBox", view_box)
        svg.set("width", fmt(width, 2))
        svg.set("height", fmt(height, 2))

        return svg

    # -- timeline / layers --------------------------------------------------

    @staticmethod
    def _layers_of(symbol_element: ET.Element) -> Optional[ET.Element]:
        timeline = first_descendant(symbol_element, "DOMTimeline")
        if timeline is None:
            return None
        return child(timeline, "layers")

    def _select_frames(
        self, frames_element: ET.Element, first_frame_only: bool, start_frame: int
    ) -> List[ET.Element]:
        frames = children(frames_element, "DOMFrame")
        if not frames:
            return []
        if not first_frame_only:
            return frames

        for frame in frames:
            try:
                index = int(frame.get("index", "0"))
            except ValueError:
                index = 0
            try:
                duration = int(frame.get("duration", "1"))
            except ValueError:
                duration = 1
            if index <= start_frame < index + duration:
                return [frame]
        return [frames[0]]

    def _render_layers(
        self,
        layers_element: ET.Element,
        parent_group: ET.Element,
        current_matrix: Matrix,
        first_frame_only: bool,
        start_frame: int,
        depth: int,
        stack: frozenset,
    ) -> None:
        # XFL stores layers top-to-bottom; SVG paints back-to-front.
        for layer in reversed(children(layers_element, "DOMLayer")):
            layer_type = layer.get("layerType")
            if layer_type == "guide" and not self.options.include_guides:
                continue

            self.layer_counter += 1
            layer_id = f"layer-{self.layer_counter}"
            layer_name = layer.get("name", f"Layer {self.layer_counter}")

            attributes = {"id": layer_id, "data-layer": layer_name}
            if layer.get("color"):
                attributes["data-color"] = layer.get("color")
            if layer_type:
                attributes["data-layer-type"] = layer_type
            if layer.get("visible", "true").lower() == "false":
                attributes["data-visible"] = "false"
                attributes["style"] = "display:none"

            layer_group = ET.SubElement(parent_group, "g", attributes)
            self.layer_groups.append((layer_name, layer_group))

            frames_element = child(layer, "frames")
            if frames_element is None:
                continue

            for frame in self._select_frames(
                frames_element, first_frame_only, start_frame
            ):
                frame_attributes = {"data-frame": frame.get("index", "0")}
                if frame.get("duration"):
                    frame_attributes["data-duration"] = frame.get("duration")
                frame_group = ET.SubElement(layer_group, "g", frame_attributes)

                elements = child(frame, "elements")
                if elements is not None:
                    for element in elements:
                        self._render_element(
                            element, frame_group, current_matrix, depth, stack
                        )

    # -- elements -----------------------------------------------------------

    def _render_element(
        self,
        element: ET.Element,
        parent_group: ET.Element,
        current_matrix: Matrix,
        depth: int,
        stack: frozenset,
    ) -> None:
        tag = local_name(element.tag)

        if tag == "DOMShape":
            self._render_shape(element, parent_group, current_matrix)
        elif tag == "DOMGroup":
            members = child(element, "members")
            if members is None and len(element) and local_name(element[-1].tag) == "members":
                members = element[-1]
            if members is None:
                return
            group = ET.SubElement(parent_group, "g", {"data-group": "true"})
            # Animate's DOMGroup matrices are redundant; ignoring them matches
            # Animate's own SVG export.
            for member in members:
                self._render_element(member, group, current_matrix, depth, stack)
        elif tag == "DOMSymbolInstance":
            self._render_instance(element, parent_group, current_matrix, depth, stack)
        else:
            self.skipped[tag] += 1

    def _render_instance(
        self,
        instance: ET.Element,
        parent_group: ET.Element,
        current_matrix: Matrix,
        depth: int,
        stack: frozenset,
    ) -> None:
        name = instance.get("libraryItemName")
        matrix = Matrix.from_element(instance)
        local_matrix = current_matrix * matrix

        attributes = {"data-instance": name or ""}
        if not matrix.is_identity():
            attributes["transform"] = matrix.to_svg()

        color_wrapper = child(instance, "color")
        if color_wrapper is not None:
            color = child(color_wrapper, "Color")
            if color is not None:
                alpha = color.get("alphaMultiplier")
                if alpha is not None:
                    try:
                        alpha_value = float(alpha)
                        if alpha_value < 1.0:
                            attributes["opacity"] = fmt(alpha_value, 4)
                    except ValueError:
                        pass

        group = ET.SubElement(parent_group, "g", attributes)

        if (
            not self.options.inline_instances
            or not name
            or depth >= self.options.max_depth
            or name in stack
        ):
            if name and name in stack:
                self.warnings.append(f"cycle detected at symbol instance '{name}'")
            return

        symbol = self.library.resolve(name)
        if symbol is None:
            self.warnings.append(f"could not resolve symbol instance '{name}'")
            return

        symbol_name = symbol.get("name") or name
        if symbol_name in stack:
            self.warnings.append(f"cycle detected at symbol instance '{symbol_name}'")
            return

        layers_element = self._layers_of(symbol)
        if layers_element is None:
            return

        try:
            first_frame = int(instance.get("firstFrame", "0"))
        except ValueError:
            first_frame = 0

        self._render_layers(
            layers_element,
            group,
            local_matrix,
            first_frame_only=True,
            start_frame=first_frame,
            depth=depth + 1,
            stack=stack | {name, symbol_name},
        )

    def _render_shape(
        self,
        shape: ET.Element,
        parent_group: ET.Element,
        current_matrix: Matrix,
    ) -> None:
        matrix = Matrix.from_element(shape)
        local_matrix = current_matrix * matrix

        fill_styles: Dict[str, Dict[str, str]] = {}
        fills_element = child(shape, "fills")
        for style in children(fills_element, "FillStyle"):
            index = style.get("index")
            if index is not None:
                fill_styles[index] = parse_fill_style(style, self)

        stroke_styles: Dict[str, Dict[str, str]] = {}
        strokes_element = child(shape, "strokes")
        for style in children(strokes_element, "StrokeStyle"):
            index = style.get("index")
            if index is not None:
                stroke_styles[index] = parse_stroke_style(style, self)

        edges_element = child(shape, "edges")
        if edges_element is None:
            return

        fill_contributions: Dict[str, List[List]] = defaultdict(list)
        stroke_contributions: Dict[str, List[List]] = defaultdict(list)

        for edge in children(edges_element, "Edge"):
            edge_data = edge.get("edges")
            if not edge_data:
                continue

            point_lists = edge_to_point_lists(edge_data)
            if not point_lists:
                continue

            fill_style_0 = edge.get("fillStyle0")
            fill_style_1 = edge.get("fillStyle1")
            stroke_style = edge.get("strokeStyle")

            for points in point_lists:
                self.bbox.add_point_list(points, local_matrix)

                # fillStyle0 is one side of the edge and fillStyle1 the other.
                # Reversing one side makes the contours wind consistently.
                if fill_style_0 is not None:
                    fill_contributions[fill_style_0].append(points)
                if fill_style_1 is not None:
                    fill_contributions[fill_style_1].append(list(reversed(points)))
                if stroke_style is not None:
                    stroke_contributions[stroke_style].append(points)

            if stroke_style in stroke_styles:
                try:
                    width = float(stroke_styles[stroke_style].get("stroke-width", "1"))
                except ValueError:
                    width = 1.0
                self.max_stroke_scaled = max(
                    self.max_stroke_scaled, width * local_matrix.scale()
                )

        shape_group = parent_group
        if not matrix.is_identity():
            shape_group = ET.SubElement(
                parent_group, "g", {"transform": matrix.to_svg()}
            )

        for style_id, point_lists in fill_contributions.items():
            style = fill_styles.get(style_id)
            if style is None:
                continue
            contours = stitch_point_lists(point_lists)
            path_data = " ".join(
                point_list_to_d(contour) for contour in contours
            ).strip()
            if not path_data:
                continue
            attributes = dict(style)
            attributes["d"] = path_data
            ET.SubElement(shape_group, "path", attributes)

        for style_id, point_lists in stroke_contributions.items():
            style = stroke_styles.get(style_id)
            if style is None:
                continue
            path_data = " ".join(
                point_list_to_d(points) for points in point_lists
            ).strip()
            if not path_data:
                continue
            attributes = dict(style)
            attributes["d"] = path_data
            ET.SubElement(shape_group, "path", attributes)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str, max_length: int = 120) -> str:
    name = INVALID_FILENAME_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(".")
    if not name:
        name = "symbol"
    if name.upper() in RESERVED_NAMES:
        name = "_" + name
    return name[:max_length].rstrip(" .") or "symbol"


def unique_filename(base: str, used: set) -> str:
    stem = sanitize_filename(base)
    candidate = f"{stem}.svg"
    counter = 2
    while candidate.lower() in used:
        candidate = f"{stem}_{counter}.svg"
        counter += 1
    used.add(candidate.lower())
    return candidate


def unique_output_dir(root: Path, stem: str) -> Path:
    candidate = root / sanitize_filename(stem)
    counter = 2
    while candidate.exists():
        candidate = root / f"{sanitize_filename(stem)}_{counter}"
        counter += 1
    return candidate


def write_svg(svg: ET.Element, path: Path) -> None:
    tree = ET.ElementTree(svg)
    if hasattr(ET, "indent"):
        ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def build_layer_svg(
    full_svg: ET.Element,
    layer_element: ET.Element,
    defs_element: ET.Element,
    symbol_name: str,
    layer_name: str,
) -> ET.Element:
    svg = ET.Element("svg", {"xmlns": SVG_NS, "version": "1.1"})
    for attribute in ("viewBox", "width", "height", "preserveAspectRatio"):
        if attribute in full_svg.attrib:
            svg.set(attribute, full_svg.attrib[attribute])

    ET.SubElement(svg, "title").text = f"{symbol_name} — {layer_name}"
    description = ET.SubElement(svg, "desc")
    description.text = f"Layer '{layer_name}' of library symbol '{symbol_name}'"

    if len(defs_element) > 0:
        svg.append(copy.deepcopy(defs_element))
    svg.append(copy.deepcopy(layer_element))
    return svg


def count_paths(element: ET.Element) -> int:
    return sum(1 for item in element.iter() if local_name(item.tag) == "path")


# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------

def process_fla(
    fla_path: Path,
    output_root: Path,
    options: Options,
    log,
) -> Optional[dict]:
    log(f"Processing {fla_path}")

    temporary_context = None
    if options_keep_temp_global:
        temporary_dir = Path(tempfile.mkdtemp(prefix="fla_svg_"))
    else:
        temporary_context = tempfile.TemporaryDirectory(prefix="fla_svg_")
        temporary_dir = Path(temporary_context.name)

    try:
        # 1. Copy to a temporary path with a .zip extension.
        zip_path = temporary_dir / f"{fla_path.stem}.zip"
        shutil.copy2(fla_path, zip_path)

        # 2. Extract.
        extract_dir = temporary_dir / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            archive = open_fla_zip(zip_path)
        except (zipfile.BadZipFile, OSError) as exc:
            log(f"  ! skipped: {exc}")
            return None

        with archive:
            safe_extract(archive, extract_dir)

        xfl_root = find_xfl_root(extract_dir)
        library_dir = find_library_dir(xfl_root)
        if library_dir is None:
            log("  ! skipped: no LIBRARY/ folder in the archive")
            return None

        library = Library(library_dir, log)
        if not library.entries:
            log("  ! skipped: no DOMSymbolItem XML files in LIBRARY/")
            return None

        output_dir = unique_output_dir(output_root, fla_path.stem)
        output_dir.mkdir(parents=True, exist_ok=True)
        layers_dir = output_dir / "layers"

        used_symbol_names: set = set()
        used_layer_names: set = set()
        manifest: dict = {
            "fla": str(fla_path.resolve()),
            "output": str(output_dir.resolve()),
            "symbols": [],
            "warnings": [],
        }

        log(f"  found {len(library.entries)} library symbol(s)")

        for symbol_name, symbol_path, symbol_element in library.entries:
            renderer = SymbolRenderer(library, options, fla_path.name)
            svg = renderer.render(symbol_element, symbol_name)
            filename = unique_filename(symbol_name, used_symbol_names)
            write_svg(svg, output_dir / filename)

            path_count = count_paths(svg)
            layer_names = [name for name, _ in renderer.layer_groups]

            manifest["symbols"].append(
                {
                    "name": symbol_name,
                    "file": filename,
                    "source": symbol_path.relative_to(library_dir).as_posix(),
                    "layers": layer_names,
                    "paths": path_count,
                    "skipped": dict(renderer.skipped),
                }
            )
            manifest["warnings"].extend(renderer.warnings)

            # Optional: write each layer as its own SVG, using the symbol's
            # overall viewBox so the layers stay aligned.
            if options.split_layers and renderer.layer_groups:
                layers_dir.mkdir(parents=True, exist_ok=True)
                for layer_name, layer_element in renderer.layer_groups:
                    layer_svg = build_layer_svg(
                        svg,
                        layer_element,
                        renderer.defs,
                        symbol_name,
                        layer_name,
                    )
                    layer_filename = unique_filename(
                        f"{symbol_name}__{layer_name}", used_layer_names
                    )
                    write_svg(layer_svg, layers_dir / layer_filename)

            log(f"  - {symbol_name}: {path_count} path(s), {len(layer_names)} layer(s)")

        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        log(f"  wrote {len(manifest['symbols'])} SVG asset(s) to {output_dir}")
        return manifest

    finally:
        if options_keep_temp_global:
            log(f"  temporary files kept at {temporary_dir}")
        elif temporary_context is not None:
            temporary_context.cleanup()


# ``process_fla`` reads this global so it can honour --keep-temp without
# threading an extra argument through every call.  It is set in main().
options_keep_temp_global = False


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

def collect_fla_files(inputs: Sequence[str], recursive: bool) -> List[Path]:
    found: List[Path] = []
    seen: set = set()

    def add(path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        key = str(resolved).lower()
        if key not in seen:
            seen.add(key)
            found.append(resolved)

    for raw in inputs:
        path = Path(raw).expanduser()
        if path.is_file():
            if path.suffix.lower() == ".fla":
                add(path)
            else:
                print(f"! not a .fla file: {path}", file=sys.stderr)
        elif path.is_dir():
            pattern = "**/*.fla" if recursive else "*.fla"
            for candidate in sorted(path.glob(pattern)):
                if candidate.is_file():
                    add(candidate)
        else:
            print(f"! path does not exist: {path}", file=sys.stderr)

    return found


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract vector graphics from .fla (XFL/ZIP) files into layered SVG assets."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python fla_svg_extractor.py\n"
            "  python fla_svg_extractor.py movie.fla --output out\n"
            "  python fla_svg_extractor.py --recursive --all-frames --split-layers\n"
        ),
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help=".fla files or directories (default: current directory)",
    )
    parser.add_argument(
        "-o", "--output",
        default="svg_assets",
        help="output directory (default: svg_assets)",
    )
    parser.add_argument(
        "--all-frames",
        action="store_true",
        help="render every keyframe instead of only the first frame of each layer",
    )
    parser.add_argument(
        "--split-layers",
        action="store_true",
        help="also write one SVG per layer under <output>/<fla>/layers/",
    )
    parser.add_argument(
        "--no-instances",
        action="store_true",
        help="do not inline library symbols referenced by DOMSymbolInstance",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=8,
        help="maximum symbol-instance nesting depth (default: 8)",
    )
    parser.add_argument(
        "--include-guides",
        action="store_true",
        help="include guide layers (skipped by default)",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="search directories recursively for .fla files",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="keep the temporary .zip and extracted files for inspection",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="print extra detail while processing",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    global options_keep_temp_global

    parser = build_parser()
    args = parser.parse_args(argv)

    options_keep_temp_global = args.keep_temp

    inputs = args.inputs or ["."]
    fla_files = collect_fla_files(inputs, args.recursive)

    if not fla_files:
        print("No .fla files found.")
        return 0

    output_root = Path(args.output).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    options = Options(
        all_frames=args.all_frames,
        split_layers=args.split_layers,
        inline_instances=not args.no_instances,
        max_depth=max(0, args.max_depth),
        include_guides=args.include_guides,
    )

    def log(message: str) -> None:
        print(message)

    processed = 0
    failed = 0

    for fla_path in fla_files:
        try:
            manifest = process_fla(fla_path, output_root, options, log)
            if manifest is None:
                failed += 1
            else:
                processed += 1
                if args.verbose and manifest["warnings"]:
                    for warning in manifest["warnings"]:
                        print(f"  warning: {warning}")
        except Exception as exc:  # keep going with the next file
            failed += 1
            print(f"! error while processing {fla_path}: {exc}", file=sys.stderr)
            if args.verbose:
                import traceback
                traceback.print_exc()

    print(f"Done. {processed} file(s) converted, {failed} skipped/failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
