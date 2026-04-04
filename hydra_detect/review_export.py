"""CLI tool to export detection logs as standalone HTML map reports.

Usage::

    python -m hydra_detect.review_export \\
        /data/logs/detections_20260315_120000.jsonl -o report.html
    python -m hydra_detect.review_export \\
        /data/logs/detections.csv --images-dir /data/images -o report.html
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_jsonl(path: Path) -> list[dict]:
    """Parse a JSONL detection log file."""
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def parse_csv_log(path: Path) -> list[dict]:
    """Parse a CSV detection log file."""
    records = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            for key in ("confidence", "x1", "y1", "x2", "y2", "lat", "lon", "alt"):
                if key in row and row[key]:
                    try:
                        row[key] = float(row[key])
                    except ValueError:
                        pass
            for key in ("frame", "track_id", "class_id", "fix"):
                if key in row and row[key]:
                    try:
                        row[key] = int(row[key])
                    except ValueError:
                        pass
            records.append(row)
    return records


def parse_log(path: Path) -> list[dict]:
    """Auto-detect format and parse a detection log file."""
    if path.suffix == ".csv":
        return parse_csv_log(path)
    return parse_jsonl(path)


def build_summary(records: list[dict]) -> dict:
    """Build summary statistics from detection records."""
    if not records:
        return {"total": 0, "classes": {}, "tracks": 0, "with_gps": 0}
    classes: dict[str, int] = {}
    track_ids: set = set()
    with_gps = 0
    for r in records:
        label = r.get("label", "unknown")
        classes[label] = classes.get(label, 0) + 1
        if r.get("track_id") is not None:
            track_ids.add(r["track_id"])
        if r.get("lat") is not None and r.get("lon") is not None:
            with_gps += 1
    return {
        "total": len(records),
        "classes": classes,
        "tracks": len(track_ids),
        "with_gps": with_gps,
        "time_start": records[0].get("timestamp", ""),
        "time_end": records[-1].get("timestamp", ""),
    }


def embed_images(records: list[dict], images_dir: Path, max_images: int = 100) -> list[dict]:
    """Replace image filenames with base64 data URIs for inline viewing.

    Args:
        records: Detection records list (modified in-place).
        images_dir: Directory containing detection images.
        max_images: Maximum number of images to embed (prevents GB-scale output).

    Returns:
        The same records list with ``image_data`` keys added where applicable.
    """
    images_dir_resolved = images_dir.resolve()
    embedded = 0
    for r in records:
        img = r.get("image")
        if not img:
            continue
        img_path = (images_dir / img).resolve()
        # Guard against path traversal (e.g. img = "../../etc/passwd")
        try:
            img_path.relative_to(images_dir_resolved)
        except ValueError:
            logger.warning("Skipping image with path traversal attempt: %s", img)
            continue
        if not img_path.exists():
            continue
        if embedded >= max_images:
            logger.warning(
                "embed_images: reached max_images=%d — remaining images skipped",
                max_images,
            )
            break
        data = img_path.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        r["image_data"] = f"data:image/jpeg;base64,{b64}"
        embedded += 1
    return records


def generate_html(records: list[dict], summary: dict, title: str = "Hydra Mission Report") -> str:
    """Generate a self-contained HTML file with Leaflet map."""
    # Escape </script> sequences to prevent script-tag breakout (XSS)
    detections_json = json.dumps(records).replace("</", "<\\/")
    summary_json = json.dumps(summary).replace("</", "<\\/")
    safe_title = html.escape(title)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_title}</title>
<link rel="stylesheet"
  href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
  integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
  crossorigin="anonymous"/>
<script
  src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
  integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
  crossorigin="anonymous"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Courier New',monospace;background:#0a0a0a;color:#e0e0e0}}
.hdr{{background:#111;padding:10px 20px;border-bottom:1px solid #333}}
.hdr h1{{font-size:16px;color:#00ff88;letter-spacing:2px;display:inline}}
.hdr .stats{{float:right;font-size:12px;color:#888}}
.wrap{{display:flex;height:calc(100vh - 42px)}}
#map{{flex:1}}
.side{{width:300px;background:#111;border-left:1px solid #333;overflow-y:auto;padding:12px}}
.side h3{{color:#00ff88;font-size:13px;margin-bottom:6px;
  border-bottom:1px solid #333;padding-bottom:4px}}
.sec{{margin-bottom:14px}}
.st{{font-size:12px;color:#aaa;margin-bottom:3px}}
.st span{{color:#00ff88}}
select,input[type=range]{{width:100%;background:#1a1a1a;color:#e0e0e0;border:1px solid #333;padding:5px;font-family:inherit;font-size:12px;margin-bottom:6px}}
label{{font-size:11px;color:#888;display:block;margin-bottom:3px}}
.chk{{display:flex;align-items:center;gap:6px;margin-bottom:4px}}
.chk input{{accent-color:#00ff88}}
.chk label{{margin:0;font-size:12px}}
.tags{{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px}}
.tag{{padding:2px 7px;border-radius:3px;font-size:11px;cursor:pointer;border:1px solid #444;background:#1a1a1a}}
.tag.on{{background:#00ff88;color:#000;border-color:#00ff88}}
.popup-img{{max-width:260px;margin-top:4px;border:1px solid #444}}
.leaflet-popup-content-wrapper{{background:#1a1a1a;color:#e0e0e0;border-radius:4px}}
.leaflet-popup-tip{{background:#1a1a1a}}
.leaflet-popup-content{{font-family:'Courier New',monospace;font-size:12px}}
</style>
</head>
<body>
<div class="hdr">
<h1>HYDRA DETECT — MISSION REPORT</h1>
<span class="stats" id="hdrStats"></span>
</div>
<div class="wrap">
<div id="map"></div>
<div class="side">
<div class="sec"><h3>SUMMARY</h3><div id="summaryBox"></div></div>
<div class="sec"><h3>FILTERS</h3>
<label>Min confidence: <span id="cv">0.00</span></label>
<input type="range" id="cs" min="0" max="0.99" step="0.05" value="0">
<label>Classes:</label><div id="cf" class="tags"></div>
</div>
<div class="sec"><h3>DISPLAY</h3>
<div class="chk"><input type="checkbox" id="st" checked><label for="st">Track trails</label></div>
<div class="chk"><input type="checkbox" id="sm" checked><label for="sm">Markers</label></div>
</div>
</div>
</div>
<script>
const D={detections_json};
const S={summary_json};
function esc(s){{const d=document.createElement('div');d.textContent=String(s);return d.innerHTML}}
const map=L.map('map').setView([0,0],2);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{attribution:'OSM',maxZoom:19}}).addTo(map);
const mL=L.layerGroup().addTo(map),tL=L.layerGroup().addTo(map);
const CC=['#ff4444','#44ff44','#4488ff','#ffaa00','#ff44ff','#44ffff','#ff8844','#88ff44','#4444ff','#ffff44'];
let ccm={{}},ac=new Set();
function gc(l){{if(!(l in ccm))ccm[l]=CC[Object.keys(ccm).length%CC.length];return ccm[l]}}
document.getElementById('hdrStats').textContent=`${{S.total}} detections | ${{S.tracks}} tracks | ${{S.with_gps}} geotagged`;
const sb=document.getElementById('summaryBox');
sb.innerHTML=`<div class="st">Total: <span>${{esc(S.total)}}</span></div><div class="st">Tracks: <span>${{esc(S.tracks)}}</span></div><div class="st">Geotagged: <span>${{esc(S.with_gps)}}</span></div><div class="st">Time: <span>${{esc((S.time_start||'').slice(11,19))}} \u2192 ${{esc((S.time_end||'').slice(11,19))}}</span></div>`;
for(const[c,n]of Object.entries(S.classes))sb.innerHTML+=`<div class="st">${{esc(c)}}: <span>${{esc(n)}}</span></div>`;
const cls=new Set(D.map(d=>d.label).filter(Boolean));ac=new Set(cls);
const cfEl=document.getElementById('cf');
for(const c of cls){{const t=document.createElement('span');t.className='tag on';t.textContent=c;t.style.borderColor=gc(c);t.onclick=()=>{{if(ac.has(c)){{ac.delete(c);t.classList.remove('on')}}else{{ac.add(c);t.classList.add('on')}}render()}};cfEl.appendChild(t)}}
function render(){{mL.clearLayers();tL.clearLayers();const mc=parseFloat(document.getElementById('cs').value);const st=document.getElementById('st').checked;const sm=document.getElementById('sm').checked;const f=D.filter(d=>d.lat!=null&&d.lon!=null&&ac.has(d.label)&&(d.confidence||0)>=mc);if(!f.length)return;const b=[];const tp={{}};for(const d of f){{const la=parseFloat(d.lat),lo=parseFloat(d.lon);if(isNaN(la)||isNaN(lo))continue;b.push([la,lo]);const tid=d.track_id;if(!tp[tid])tp[tid]={{label:d.label,pts:[]}};tp[tid].pts.push([la,lo]);if(sm){{const m=L.circleMarker([la,lo],{{radius:6,fillColor:gc(d.label),color:'#000',weight:1,fillOpacity:.8}});let p=`<b>${{esc(d.label)}}</b> #${{esc(d.track_id)}}<br>Conf: ${{((d.confidence||0)*100).toFixed(0)}}%<br>Time: ${{esc((d.timestamp||'').slice(11,19))}}<br>Pos: ${{la.toFixed(6)}}, ${{lo.toFixed(6)}}`;if(d.image_data)p+=`<br><img class="popup-img" src="${{esc(d.image_data)}}">`;else if(d.image)p+=`<br><small>${{esc(d.image)}}</small>`;m.bindPopup(p,{{maxWidth:300}});mL.addLayer(m)}}}}if(st)for(const tid of Object.keys(tp)){{const t=tp[tid];if(t.pts.length<2)continue;L.polyline(t.pts,{{color:gc(t.label),weight:2,opacity:.5,dashArray:'4 4'}}).addTo(tL)}}if(b.length)map.fitBounds(b,{{padding:[30,30]}})}}
document.getElementById('cs').oninput=e=>{{document.getElementById('cv').textContent=parseFloat(e.target.value).toFixed(2);render()}};
document.getElementById('st').onchange=render;document.getElementById('sm').onchange=render;
render();
</script>
</body>
</html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export Hydra detection logs as standalone HTML map reports.",
    )
    parser.add_argument("logfile", help="Path to JSONL or CSV detection log file")
    parser.add_argument("-o", "--output", default="mission_report.html", help="Output HTML file")
    parser.add_argument("--images-dir", help="Directory of detection images to embed as base64")
    parser.add_argument("--max-images", type=int, default=100,
                        help="Maximum number of images to embed (default: 100)")
    parser.add_argument("--title", default="Hydra Mission Report", help="Report title")
    args = parser.parse_args(argv)

    log_path = Path(args.logfile)
    if not log_path.exists():
        print(f"Error: log file not found: {log_path}", file=sys.stderr)
        return 1

    records = parse_log(log_path)
    if not records:
        print("Warning: no detections found in log file.", file=sys.stderr)

    if args.images_dir:
        images_path = Path(args.images_dir)
        if images_path.is_dir():
            records = embed_images(records, images_path, max_images=args.max_images)
        else:
            print(f"Warning: images directory not found: {images_path}", file=sys.stderr)

    summary = build_summary(records)
    html_content = generate_html(records, summary, title=args.title)

    output_path = Path(args.output)
    output_path.write_text(html_content)
    print(f"Report written to {output_path} ({len(records)} detections)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
