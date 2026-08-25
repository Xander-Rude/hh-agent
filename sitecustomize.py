"""Project-wide Python startup customization.

Python imports ``sitecustomize`` automatically during interpreter startup when
this repository is on ``sys.path`` (which is true for project scripts). Keep
this module dependency-light because it runs before application imports.
"""

from app.runtime_io import configure_utf8_stdio


configure_utf8_stdio()
