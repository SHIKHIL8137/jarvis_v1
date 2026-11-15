import threading

import pytest

pytest.importorskip("playwright")

from openjarvis.tools import browser  # noqa: E402


def test_playwright_page_usable_from_different_threads():
    s = browser._BrowserSession()
    try:
        results = []

        def work(i):
            page = s.page  # each call from a different thread
            page.set_content(f"<h1>hello {i}</h1>")
            results.append(page.inner_text("h1"))

        for i in range(3):
            t = threading.Thread(target=work, args=(i,))
            t.start()
            t.join()
        assert results == ["hello 0", "hello 1", "hello 2"]
    finally:
        s.close()
