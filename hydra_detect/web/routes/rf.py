from fastapi import APIRouter

from hydra_detect.web import server

router = APIRouter()
for path, fn, methods in [
    ('/api/rf/status', server.api_rf_status, ['GET']),
    ('/api/rf/rssi_history', server.api_rf_rssi_history, ['GET']),
    ('/api/rf/start', server.api_rf_start, ['POST']),
    ('/api/rf/stop', server.api_rf_stop, ['POST']),
]:
    router.add_api_route(path, fn, methods=methods)
