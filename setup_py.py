from setuptools import find_packages, setup  # noqa: D100

setup(
    name="dicom_ups_rs_client",
    version="0.1.0",
    description="DICOM UPS-RS Client",
    author="Your Name",
    author_email="your.email@example.com",
    packages=find_packages(),
    install_requires=[
        "pydicom>=2.3.0",
        "requests>=2.27.0",
        "websockets>=10.0",
    ],
    python_requires=">=3.8",
    entry_points={
        "console_scripts": [
            "ups-rs-client=ups_rs_client:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Healthcare Industry",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Topic :: Scientific/Engineering :: Medical Science Apps.",
    ],
)
