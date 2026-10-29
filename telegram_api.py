"""Minimal Telegram Bot API client, standard library only."""

import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
import uuid

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(Exception):
    """Telegram rejected the call or could not be reached."""


def call(token, method, fields=None, files=None):
    """Calls a Bot API method and returns its result. `files` maps a field name to a Path to upload."""
    fields = fields or {}
    if files:
        boundary = uuid.uuid4().hex
        body = b""
        for name, value in fields.items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, path in files.items():
            kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'
                     f"Content-Type: {kind}\r\n\r\n").encode() + path.read_bytes() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        content_type = f"multipart/form-data; boundary={boundary}"
    else:
        body = urllib.parse.urlencode(fields).encode()
        content_type = "application/x-www-form-urlencoded"
    request = urllib.request.Request(API.format(token=token, method=method), data=body,
                                     headers={"Content-Type": content_type})
    # Errors never include the URL: it contains the token.
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            answer = json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            answer = json.load(e)  # Telegram explains the error in the body
        except ValueError:
            answer = {"description": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        raise TelegramError(f"{method}: {e.reason}") from None
    if not answer.get("ok"):
        raise TelegramError(f"{method}: {answer.get('description', 'unknown error')}")
    return answer["result"]
