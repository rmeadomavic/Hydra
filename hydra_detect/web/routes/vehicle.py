from fastapi import APIRouter

from hydra_detect.web import server

router = APIRouter()
for path, fn, methods in [
    ('/api/vehicle/loiter', server.api_command_loiter, ['POST']),
    ('/api/vehicle/mode', server.api_set_vehicle_mode, ['POST']),
    ('/api/vehicle/beep', server.api_vehicle_beep, ['POST']),
    ('/api/target', server.api_target_status, ['GET']),
    ('/api/target/lock', server.api_target_lock, ['POST']),
    ('/api/target/unlock', server.api_target_unlock, ['POST']),
    ('/api/target/strike', server.api_strike_command, ['POST']),
    ('/api/abort', server.api_abort, ['POST']),
]:
    router.add_api_route(path, fn, methods=methods)
