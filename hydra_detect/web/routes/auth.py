from fastapi import APIRouter

from hydra_detect.web import server

router = APIRouter()
router.add_api_route(
    '/login',
    server.login_page,
    methods=['GET'],
    response_class=server.HTMLResponse,
)
router.add_api_route('/auth/login', server.auth_login, methods=['POST'])
router.add_api_route('/auth/logout', server.auth_logout, methods=['POST'])
router.add_api_route('/auth/status', server.auth_status, methods=['GET'])
router.add_api_route('/', server.index, methods=['GET'], response_class=server.HTMLResponse)
