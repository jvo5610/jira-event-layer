"""Bound provider response allocation, including decompressed response bytes."""
import httpx


def bounded_request(client, method, url, *, max_bytes=256000, **kwargs):
    with client.stream(method, url, **kwargs) as response:
        chunks = []
        size = 0
        for chunk in response.iter_bytes(chunk_size=16384):
            size += len(chunk)
            if size > max_bytes:
                # A write may already have happened; callers must classify this as ambiguous.
                raise httpx.ReadError("Provider response exceeded limit", request=response.request)
            chunks.append(chunk)
        headers = [(key, value) for key, value in response.headers.multi_items()
                   if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}]
        return httpx.Response(response.status_code, headers=headers, content=b"".join(chunks), request=response.request)
