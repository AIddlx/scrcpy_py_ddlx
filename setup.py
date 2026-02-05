"""
scrcpy-py-ddlx - Python scrcpy Client
Pure Python implementation of scrcpy client for mirroring and controlling Android devices.
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read version from __init__.py
version_file = Path(__file__).parent / "scrcpy_py_ddlx" / "__init__.py"
version_content = version_file.read_text()
version_line = [
    line for line in version_content.split("\n") if line.startswith("__version__")
]
if version_line:
    version = version_line[0].split("=")[1].strip().strip('"')
else:
    version = "0.1.0"

# Read README for long description
readme_file = Path(__file__).parent / "scrcpy_py_ddlx" / "README.md"
if readme_file.exists():
    long_description = readme_file.read_text()
else:
    long_description = "Pure Python scrcpy client for Android"

setup(
    name="scrcpy-py-ddlx",
    version=version,
    author="AutoGLM",
    description="Pure Python implementation of scrcpy client for Android devices",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/autoglm/py-scrcpy",
    packages=find_packages(exclude=["tests*", "examples*"]),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: OS Independent",
        "Topic :: Multimedia :: Video :: Display",
        "Topic :: System :: Hardware",
    ],
    python_requires=">=3.8",
    install_requires=[
        "av>=13.0.0",
        "numpy>=1.24.0,<2.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=8.0.0",
            "black>=24.0.0",
            "mypy>=1.8.0",
        ],
        "gui": [
            "PySide6>=6.6.0",
        ],
        "audio": [
            "pyaudio>=0.2.14",
        ],
    },
    entry_points={
        "console_scripts": [
            "scrcpy-connect=scrcpy_py_ddlx.client_v2:main",
        ],
    },
)
