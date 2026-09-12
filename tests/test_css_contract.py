"""CSS rules the JavaScript silently depends on.

The UI tests drive a real DOM but jsdom does not run the cascade — it reports
`el.hidden` as a property and never computes `display`. So a stylesheet rule
that defeats an attribute the JS toggles is invisible to every other test in
this suite, and shows up only as a window that looks wrong.

That is not hypothetical: `.setup-veil { display: grid }` outranked the
user-agent `[hidden] { display: none }`, so the project picker sat permanently
over the editor while every test passed.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSS = (ROOT / "web/styles.css").read_text()
JS = (ROOT / "web/app.js").read_text()
HTML = (ROOT / "web/index.html").read_text()


def rules():
    """(selector, body) for every rule in the stylesheet."""
    for match in re.finditer(r"([^{}]+)\{([^}]*)\}", CSS, re.S):
        selector = match.group(1).strip().splitlines()[-1].strip()
        if selector.startswith("@") or not selector:
            continue
        yield selector, match.group(2)


def test_hidden_beats_any_display_rule():
    """Without this, `el.hidden = true` is a no-op on anything with display."""
    enforced = [sel for sel, body in rules()
                if sel == "[hidden]" and "display: none !important" in body]
    assert enforced, (
        "styles.css must force [hidden] { display: none !important }, or any "
        "rule setting `display` on a class silently outranks the attribute")


def elements_toggled_via_hidden():
    """Element ids the JS shows/hides with the `hidden` property."""
    return set(re.findall(r"\$\('([A-Za-z0-9_]+)'\)\.hidden\s*=", JS))


def test_the_elements_the_js_hides_actually_exist():
    missing = [i for i in elements_toggled_via_hidden()
               if f'id="{i}"' not in HTML]
    assert not missing, f"JS toggles hidden on ids not in the markup: {missing}"


def test_something_is_toggled_by_hidden_at_all():
    """Guards the two tests above from passing vacuously."""
    assert elements_toggled_via_hidden(), \
        "no element is toggled via .hidden — has the mechanism changed?"


@pytest.mark.parametrize("selector", [".setup-veil", ".problems-drawer"])
def test_overlays_still_declare_a_display(selector):
    """These are the overlays the [hidden] guard exists to protect.

    If one stops declaring `display`, the guard is no longer load-bearing for
    it — which is fine, but this test should be updated rather than silently
    protecting nothing.
    """
    bodies = [body for sel, body in rules() if sel == selector]
    assert bodies, f"{selector} no longer exists"
    assert any("display:" in body for body in bodies), \
        f"{selector} no longer sets display; the [hidden] guard may be moot here"
