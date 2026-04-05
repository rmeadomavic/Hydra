from fastapi import APIRouter

from hydra_detect.web import server

router = APIRouter()
for path, fn, methods in [
    ('/setup', server.setup_page, ['GET']),
    ('/api/setup/devices', server.api_setup_devices, ['GET']),
    ('/api/setup/save', server.api_setup_save, ['POST']),
]:
    router.add_api_route(path, fn, methods=methods)
