import re
from pathlib import Path

from setuptools import setup, find_packages

with open("requirements.txt") as f:
	install_requires = f.read().strip().split("\n")

# Read __version__ statically from helpdesk/__init__.py. Importing the
# package here breaks PEP-517 isolated builds (`pip install -e .`) because
# the source isn't on sys.path yet — that's the ModuleNotFoundError the
# uat deploy was hitting.
_init = Path(__file__).parent / "helpdesk" / "__init__.py"
_match = re.search(
	r'^__version__\s*=\s*["\']([^"\']+)["\']', _init.read_text(), re.MULTILINE
)
version = _match.group(1) if _match else "0.0.0"

setup(
	name="helpdesk",
	version=version,
	description="Customer Service Software",
	author="Frappe Technologies",
	author_email="hello@frappe.io",
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires,
)
