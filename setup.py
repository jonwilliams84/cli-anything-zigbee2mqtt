from setuptools import setup, find_namespace_packages

with open("cli_anything/zigbee2mqtt/README.md") as f:
    long_description = f.read()

setup(
    name="cli-anything-zigbee2mqtt",
    version="0.1.0",
    description="CLI harness for Zigbee2MQTT — bridge control, device management, OTA, external converters from the command line",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    install_requires=[
        "click>=8.0.0",
        "prompt-toolkit>=3.0.0",
        "paho-mqtt>=1.6.0",
        "requests>=2.28.0",
    ],
    entry_points={
        "console_scripts": [
            "cli-anything-zigbee2mqtt=cli_anything.zigbee2mqtt.zigbee2mqtt_cli:main",
        ],
    },
    package_data={
        "cli_anything.zigbee2mqtt": ["skills/*.md", "README.md"],
    },
    include_package_data=True,
    python_requires=">=3.10",
)
