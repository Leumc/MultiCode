from pathlib import Path
import re

ROOT = Path(__file__).parents[1] / "src" / "remote_dev" / "static"


def test_admin_page_has_required_management_sections():
    html = (ROOT / "admin.html").read_text(encoding="utf-8")
    for element_id in (
        "login-view", "dashboard-view", "login-form", "user-form", "users-table",
        "grant-form", "header-list", "audit-list", "toast",
    ):
        assert f'id="{element_id}"' in html
    assert "https://fonts.googleapis.com" not in html
    assert "cdnjs" not in html


def test_every_literal_dom_lookup_exists():
    html = (ROOT / "admin.html").read_text(encoding="utf-8")
    js = (ROOT / "admin.js").read_text(encoding="utf-8")
    html_ids = set(re.findall(r'id="([^"]+)"', html))
    js_ids = set(re.findall(r'\$\("([^"]+)"\)', js))
    assert js_ids - html_ids == set()


def test_frontend_avoids_inner_html_for_api_data():
    js = (ROOT / "admin.js").read_text(encoding="utf-8")
    assert ".innerHTML" not in js
    assert "textContent" in js


def test_user_lifecycle_controls_are_wired_to_admin_api():
    html = (ROOT / "admin.html").read_text(encoding="utf-8")
    js = (ROOT / "admin.js").read_text(encoding="utf-8")
    assert "操作" in html
    for label in ("改名", "禁用", "强制退出", "轮换 Token"):
        assert label in js
    assert "/revoke-sessions" in js
    assert "/rotate-token" in js
    assert 'method: "PATCH"' in js


def test_admin_layout_has_tablet_breakpoint_and_touch_targets():
    css = (ROOT / "admin.css").read_text(encoding="utf-8")
    assert "@media (max-width: 900px)" in css
    assert re.search(r"min-height\s*:\s*44px", css)
