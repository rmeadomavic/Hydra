from fastapi import APIRouter

from hydra_detect.web import server

router = APIRouter()
for path, fn, methods in [
    ('/api/health', server.api_health, ['GET']),
    ('/api/preflight', server.api_preflight, ['GET']),
    ('/api/stats', server.api_stats, ['GET']),
    ('/api/tracks', server.api_active_tracks, ['GET']),
    ('/api/detections', server.api_recent_detections, ['GET']),
    ('/api/events', server.api_events, ['GET']),
    ('/api/events/status', server.api_events_status, ['GET']),
    ('/stream.mjpeg', server.mjpeg_stream, ['GET']),
    ('/stream.jpg', server.snapshot_frame, ['GET']),
    ('/api/stream/quality', server.get_stream_quality, ['GET']),
    ('/api/stream/quality', server.set_stream_quality, ['POST']),
]:
    router.add_api_route(path, fn, methods=methods)
