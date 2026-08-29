from pathlib import Path

from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def render_fragment(name: str, **context) -> str:
    return templates.env.get_template(name).render(**context)
