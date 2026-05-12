from collections.abc import Awaitable, Callable
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.api.errors import ErrorCode, build_error_response

MAX_REQUEST_ID_LENGTH = 128


def new_request_id() -> str:
    return str(uuid4())


class RequestIdMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, header_name: str = "X-Request-Id") -> None:
        super().__init__(app)
        self.header_name = header_name

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming_request_id = request.headers.get(self.header_name)
        if incoming_request_id is None or incoming_request_id.strip() == "":
            request_id = new_request_id()
        elif len(incoming_request_id) > MAX_REQUEST_ID_LENGTH:
            request_id = new_request_id()
            request.state.request_id = request_id
            return build_error_response(
                request=request,
                status_code=400,
                code=ErrorCode.VALIDATION_ERROR,
                message="請求資料不合法",
                details={"field": self.header_name, "reason": "request id length must be 1..128"},
            )
        else:
            request_id = incoming_request_id

        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[self.header_name] = request_id
        return response
