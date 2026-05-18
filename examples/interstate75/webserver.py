"""Tiny HTTP server for the Interstate 75 W.

Designed to be cooperatively polled from the main display loop on a single core:
the listening socket is non-blocking so `poll()` returns immediately when nothing
is pending, and the (short-lived) per-request handler runs synchronously on the
main loop. This means the display will pause for a few ms during an upload -
acceptable given how rare pushes are.

Register handlers with `route()` from main.py; this module knows nothing about
flights, config files, or filesystem layout.
"""

import socket

_BUFFER_SIZE = 1024
_MAX_BODY_BYTES = 64 * 1024 # generous ceiling for flight_display.py uploads
_RECV_TIMEOUT_S = 5

_listener = None
_routes = {} # (method, path) -> handler(body_bytes, query_string) -> (status, content_type, body)


def route(method, path, handler):
    """Register a handler. The handler receives (body_bytes, query_string) and
    returns (status_code, content_type, body). `body` may be str or bytes.
    """
    _routes[(method.upper(), path)] = handler


def start(port=80):
    """Open the listening socket. Call once after WiFi is up."""
    global _listener
    addr = socket.getaddrinfo("0.0.0.0", port)[0][-1]
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)
    s.setblocking(False)
    _listener = s
    print(f"Webserver listening on port {port}")


def poll():
    """Accept and handle one request if pending. Returns quickly otherwise."""
    if _listener is None:
        return
    try:
        conn, addr = _listener.accept()
    except OSError:
        return # EAGAIN - nothing pending
    try:
        _handle(conn)
    except Exception as e:
        print(f"Webserver error: {e}")
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _handle(conn):
    conn.settimeout(_RECV_TIMEOUT_S)

    # Read request line + headers (everything up to \r\n\r\n). Using a bytearray
    # avoids the O(n^2) cost (and potential quirks) of repeated `bytes +=`.
    buf = bytearray()
    while buf.find(b"\r\n\r\n") < 0:
        chunk = conn.recv(_BUFFER_SIZE)
        if not chunk:
            return
        buf.extend(chunk)
        if len(buf) > _MAX_BODY_BYTES + 4096: # guard against runaway client
            _send(conn, 413, "text/plain", "Request too large")
            return

    sep = buf.find(b"\r\n\r\n")
    header_blob = bytes(buf[:sep])
    body_buf = bytearray(buf[sep + 4:])

    request_line, _, header_block = header_blob.partition(b"\r\n")

    try:
        method, target, _ = request_line.decode().split(" ", 2)
    except ValueError:
        _send(conn, 400, "text/plain", "Bad request line")
        return

    path, _, query = target.partition("?")
    headers = _parse_headers(header_block)

    content_length = int(headers.get("content-length", "0") or "0")
    if content_length > _MAX_BODY_BYTES:
        _send(conn, 413, "text/plain", "Body too large")
        return

    while len(body_buf) < content_length:
        chunk = conn.recv(min(_BUFFER_SIZE, content_length - len(body_buf)))
        if not chunk:
            break
        body_buf.extend(chunk)

    received = len(body_buf)
    print(f"{method} {path} - Content-Length={content_length}, received={received}")

    if received < content_length:
        # Refuse rather than write a truncated file. Tells push.py to retry.
        _send(conn, 400, "text/plain",
              f"Truncated body: expected {content_length} bytes, got {received}")
        return

    body = bytes(body_buf[:content_length])

    handler = _routes.get((method.upper(), path))
    if handler is None:
        _send(conn, 404, "text/plain", f"No route for {method} {path}")
        return

    try:
        result = handler(body, query)
    except Exception as e:
        print(f"Handler {method} {path} raised: {e}")
        _send(conn, 500, "text/plain", f"Handler error: {e}")
        return

    if not result:
        return

    status, content_type, response_body = result
    _send(conn, status, content_type, response_body)


def _parse_headers(block):
    headers = {}
    for line in block.split(b"\r\n"):
        if not line:
            continue
        key, _, value = line.partition(b":")
        headers[key.decode().strip().lower()] = value.decode().strip()
    return headers


def _send(conn, status, content_type, body):
    if isinstance(body, str):
        body = body.encode()
    status_text = {200: "OK", 400: "Bad Request", 404: "Not Found",
                   413: "Payload Too Large", 500: "Internal Server Error"}.get(status, "")
    header = (
        f"HTTP/1.0 {status} {status_text}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode()
    conn.sendall(header + body)


