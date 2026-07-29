import os
import urllib.request
from urllib.parse import urlsplit


def check_liveness(
    public_url: str,
    *,
    backend_url: str = "http://127.0.0.1:8000/health/live",
) -> None:
    public_host = urlsplit(public_url).netloc
    if not public_host:
        raise ValueError("CALOGRAPH_PUBLIC_URL does not contain a host")
    request = urllib.request.Request(
        backend_url,
        headers={"Host": public_host},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        if response.status != 200:
            raise RuntimeError(f"Backend liveness check returned HTTP {response.status}")


if __name__ == "__main__":
    check_liveness(os.environ["CALOGRAPH_PUBLIC_URL"])
