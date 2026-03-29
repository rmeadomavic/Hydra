---
title: "Post-mission review"
sidebarTitle: "Mission review"
icon: "map"
description: "Visualize detection data on a map after missions"
---

After a mission, Hydra provides two ways to review detection data on a map: a CLI export tool that generates standalone HTML reports, and a built-in review page on the web dashboard.

## CLI export tool

Generate a self-contained HTML report with an embedded OpenStreetMap from the command line:

```bash
python -m hydra_detect.review_export /data/logs/detections.jsonl -o report.html
```

This produces a single HTML file you can open in any browser. No server required. The map, detection markers, and all styling are embedded in the file.

The export tool reads detection log files in JSONL or CSV format (configured by `log_format` in `config.ini`).

## Web dashboard review

The [web dashboard](/features/dashboard) includes a dedicated review page at `/review`:

```
http://<jetson-ip>:8080/review
```

This provides the same map visualization with interactive controls: browse and load available log files, switch between log files without reloading, and filter data in real time.

### Review API endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/review` | Post-mission review map page |
| `GET` | `/api/review/logs` | List available detection log files |
| `GET` | `/api/review/log/{filename}` | Parse and return detection data from a log file |

## Map features

Both the CLI export and the web review page share the same visualization:

<CardGroup cols={2}>
  <Card title="Detection markers" icon="location-dot">
    Each detection is plotted on the map at its GPS coordinates with a marker colored by object class.
  </Card>
  <Card title="Track trails" icon="route">
    Detections sharing the same ByteTrack ID are connected with lines showing the target's movement path over time.
  </Card>
  <Card title="Confidence filters" icon="sliders">
    Filter detections by minimum confidence score to focus on high-quality detections.
  </Card>
  <Card title="Class filtering" icon="filter">
    Show or hide specific object classes to isolate the detections you care about.
  </Card>
</CardGroup>

The map tiles come from [OpenStreetMap](https://www.openstreetmap.org/). An internet connection is required to load tiles, but detection data is rendered client-side.

## Detection log format

The review tools read from the detection logs written during missions. Log output is configured in the `[logging]` section of `config.ini`:

```ini
[logging]
log_dir = /data/logs
log_format = jsonl
save_images = true
image_dir = /data/images
image_quality = 90
save_crops = false
crop_dir = /data/crops
```

Each log entry contains: timestamp, object class and confidence score, GPS coordinates (latitude, longitude), track ID (ByteTrack persistent ID), and vehicle position and heading at time of detection.
