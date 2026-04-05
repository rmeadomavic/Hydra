from fastapi import APIRouter

from hydra_detect.web import server

router = APIRouter()
for path, fn, methods in [
    ('/api/config', server.api_get_config, ['GET']),
    ('/api/config/prompts', server.api_set_prompts, ['POST']),
    ('/api/config/threshold', server.api_set_threshold, ['POST']),
    ('/api/config/alert-classes', server.api_get_alert_classes, ['GET']),
    ('/api/config/alert-classes', server.api_set_alert_classes, ['POST']),
    ('/api/config/full', server.api_get_full_config, ['GET']),
    ('/api/config/schema', server.api_get_config_schema, ['GET']),
    ('/api/config/full', server.api_set_full_config, ['POST']),
    ('/api/config/restore-backup', server.api_restore_config_backup, ['POST']),
    ('/api/config/factory-reset', server.api_factory_reset, ['POST']),
    ('/api/config/export', server.api_config_export, ['GET']),
    ('/api/config/import', server.api_config_import, ['POST']),
]:
    router.add_api_route(path, fn, methods=methods)
