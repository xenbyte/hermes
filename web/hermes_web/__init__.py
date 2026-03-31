"""hermes_web package.

Note: don't re-export the Flask `app` object here.
Doing so breaks `import hermes_web.app` because dotted imports resolve attributes
on the package first (and `hermes_web.app` would become the Flask instance).

Gunicorn should target `hermes_web.app:app`.
"""

__all__ = []
