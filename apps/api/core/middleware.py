import json
import logging
import uuid


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {"level": record.levelname, "logger": record.name, "message": record.getMessage()},
            ensure_ascii=False,
        )


class RequestIDMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = str(uuid.uuid4())
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        return response
