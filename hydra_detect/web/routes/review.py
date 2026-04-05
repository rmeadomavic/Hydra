from fastapi import APIRouter

from hydra_detect.web import server

router = APIRouter()
for path, fn, methods in [
    ('/review', server.review_page, ['GET']),
    ('/api/review/logs', server.api_review_logs, ['GET']),
    ('/api/review/log/{filename}', server.api_review_log, ['GET']),
    ('/api/review/events/{filename}', server.api_review_events, ['GET']),
    ('/api/review/images/{filename}', server.api_review_image, ['GET']),
    ('/api/review/waypoints/{filename}', server.api_review_waypoints, ['GET']),
    ('/api/export', server.api_export_logs, ['GET']),
    ('/api/export/waypoints', server.api_export_waypoints, ['GET']),
    ('/api/logs', server.api_app_logs, ['GET']),
]:
    router.add_api_route(path, fn, methods=methods)
