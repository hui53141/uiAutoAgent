"""
uiAutoAgent setup configuration.
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [
        line.strip()
        for line in fh
        if line.strip() and not line.startswith("#")
    ]

setup(
    name="uiAutoAgent",
    version="1.0.0",
    author="uiAutoAgent Contributors",
    description="Android UI + Hardware + CLI automation framework with AI self-healing",
    long_description=long_description,
    long_description_content_type="text/markdown",
    python_requires=">=3.11",
    packages=find_packages(exclude=["tests*", "scripts*"]),
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "uia-central=scripts.start_central:main",
            "uia-executor=scripts.start_executor:main",
            "uia-run=scripts.run_task:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3.11",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Topic :: Software Development :: Testing",
    ],
    include_package_data=True,
    package_data={
        "": ["configs/*.yaml", "locators/**/*.yaml"],
    },
)
