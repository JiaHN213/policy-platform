from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_handler


class Conflict(APIException):
    status_code = 409
    default_detail = "数据已变化，请刷新后重试。"
    default_code = "version_conflict"


def exception_handler(exc, context):
    response = drf_handler(exc, context)
    if response is not None:
        def messages(value):
            if isinstance(value, dict):
                return [item for child in value.values() for item in messages(child)]
            if isinstance(value, (list, tuple)):
                return [item for child in value for item in messages(child)]
            return [str(value)] if value else []

        readable = messages(response.data)
        response.data = {
            "code": getattr(exc, "default_code", "invalid_request"),
            "message": "；".join(dict.fromkeys(readable)) or "请求未完成，请检查填写内容。",
            "details": response.data,
            "request_id": getattr(context.get("request"), "request_id", ""),
        }
    return response
