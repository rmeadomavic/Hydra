"""Tests for review_export.py — XSS escaping and HTML generation."""

from __future__ import annotations

from hydra_detect.review_export import generate_html


def _make_records(labels: list[str]) -> list[dict]:
    """Build minimal detection records with the given labels."""
    return [
        {
            "label": label,
            "track_id": i,
            "confidence": 0.9,
            "timestamp": "2026-03-30T12:00:00",
            "lat": 35.05 + i * 0.001,
            "lon": -79.49,
            "image": f"img_{i}.jpg",
        }
        for i, label in enumerate(labels)
    ]


def _make_summary(labels: list[str]) -> dict:
    classes = {}
    for label in labels:
        classes[label] = classes.get(label, 0) + 1
    return {
        "total": len(labels),
        "tracks": len(set(range(len(labels)))),
        "with_gps": len(labels),
        "time_start": "2026-03-30T12:00:00",
        "time_end": "2026-03-30T12:30:00",
        "classes": classes,
    }


class TestXSSEscaping:
    """Verify that malicious detection data cannot inject JavaScript."""

    def test_script_tag_breakout(self):
        """Label containing </script> must not break out of the inline script."""
        records = _make_records(['</script><script>alert(1)//'])
        summary = _make_summary(['</script><script>alert(1)//'])
        html = generate_html(records, summary)
        # The raw </script> must NOT appear — it should be escaped to <\/script>
        assert '</script><script>' not in html
        # The escaped version should be present in the JSON data
        assert '<\\/script>' in html

    def test_html_injection_in_label(self):
        """Label with HTML tags must be escaped in the generated JS."""
        records = _make_records(['<img src=x onerror=alert(1)>'])
        summary = _make_summary(['<img src=x onerror=alert(1)>'])
        html = generate_html(records, summary)
        # The esc() function is defined in the generated JS
        assert 'function esc(' in html
        # Raw HTML must not appear in innerHTML assignments unescaped
        # (the esc() function handles this at runtime, but the summary
        # section uses esc() for class names)
        assert 'esc(c)' in html
        assert 'esc(d.label)' in html

    def test_title_html_escaped(self):
        """Title with HTML must be entity-escaped."""
        records = _make_records(['person'])
        summary = _make_summary(['person'])
        html = generate_html(records, summary, title='<script>alert("xss")</script>')
        assert '<script>alert' not in html.split('</title>')[0]
        assert '&lt;script&gt;' in html

    def test_image_data_escaped_in_popup(self):
        """image_data field must be escaped in Leaflet popup HTML."""
        records = _make_records(['person'])
        records[0]['image_data'] = 'javascript:alert(1)'
        summary = _make_summary(['person'])
        html = generate_html(records, summary)
        # The popup uses esc() for image_data
        assert 'esc(d.image_data)' in html

    def test_track_id_escaped_in_popup(self):
        """track_id must be escaped in popup HTML."""
        records = _make_records(['person'])
        summary = _make_summary(['person'])
        html = generate_html(records, summary)
        assert 'esc(d.track_id)' in html

    def test_clean_labels_render_normally(self):
        """Normal labels should produce valid HTML without issues."""
        records = _make_records(['person', 'vehicle', 'drone'])
        summary = _make_summary(['person', 'vehicle', 'drone'])
        html = generate_html(records, summary)
        assert '<title>Hydra Mission Report</title>' in html
        assert 'const D=' in html
        assert 'const S=' in html
        assert '"person"' in html


class TestHTMLStructure:
    """Basic structural checks for generated HTML."""

    def test_valid_html_structure(self):
        records = _make_records(['person'])
        summary = _make_summary(['person'])
        html = generate_html(records, summary)
        assert html.startswith('<!DOCTYPE html>')
        assert '</html>' in html
        assert '<script>' in html
        assert 'L.map' in html  # Leaflet map init

    def test_empty_records(self):
        html = generate_html([], {"total": 0, "tracks": 0, "with_gps": 0,
                                   "time_start": "", "time_end": "", "classes": {}})
        assert '<title>Hydra Mission Report</title>' in html
        assert 'const D=[]' in html
