import time
import urllib.error
import urllib.request

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"


class PipelineError(Exception):
    pass


def http_get(url: str, attempts: int = 3) -> bytes | None:
    """GET with retries. Returns None on 404 (holiday / not published); raises PipelineError otherwise."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err = e
        except OSError as e:  # URLError, timeouts, resets
            err = e
        if i < attempts - 1:
            time.sleep(5 * 2**i)
    raise PipelineError(f"download failed after {attempts} attempts: {url}: {err}")
