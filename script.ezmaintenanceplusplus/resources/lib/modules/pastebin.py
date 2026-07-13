# -*- coding: utf-8 -*-
import requests
import base64

from resources.lib.modules.backtothefuture import PY2

if PY2:
    from urlparse import urljoin
else:
    # Explicit submodule import: `import urllib` alone does not expose
    # urllib.parse - it only works here while `import requests` above
    # happens to load it transitively (same landmine hit live in default.py).
    from urllib.parse import urljoin


class api:
    def __init__(self):
        self.base_link = "https://pastebin.com"
        self.paste_link = "/api/api_post.php"
        self.apiKey = base64.b64decode("MjNkNTNhMGMyMTdlZWY2OGM5ZWE3NDY0NDIwZTMzNmU=")

    def paste(self, text):
        url = urljoin(self.base_link, self.paste_link)
        payload = {
            "api_dev_key": self.apiKey,
            "api_option": "paste",
            "api_paste_code": text,
        }
        result = requests.post(url, data=payload, timeout=10).content
        if not PY2:
            result = result.decode("UTF-8")
        if self.base_link not in result:
            return "Error: " + result
        else:
            return result
