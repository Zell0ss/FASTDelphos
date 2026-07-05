import threading
import urllib.request

from cc.serve import make_server


def test_make_server_serves_files(tmp_path):
    (tmp_path / "index.html").write_text("<h1>hello</h1>", encoding="utf-8")

    httpd = make_server(tmp_path, 0)  # port 0 -> OS picks a free port, avoids collisions
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=2) as resp:
            body = resp.read().decode("utf-8")
        assert body == "<h1>hello</h1>"
    finally:
        httpd.shutdown()
        thread.join(timeout=2)
        httpd.server_close()
