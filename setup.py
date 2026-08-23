from setuptools import setup, find_packages

setup(
    name="claudegate",
    version="1.0.0",
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        "fastapi>=0.115.0",
        "uvicorn[standard]>=0.32.0",
        "pydantic>=2.10.0",
        "openai>=1.60.0",
        "python-dotenv>=1.0.0",
        "httpx>=0.28.0",
    ],
    entry_points={
        "console_scripts": [
            "claudegate=src.cli:cli_main",
        ],
    },
)
