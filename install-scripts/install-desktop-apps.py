#!/usr/bin/env uv run
# /// script
# dependencies = ["click", "questionary"]
# ///
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import textwrap
import urllib.request
from pathlib import Path

import click
import questionary

SCRIPT_DIR = Path(__file__).resolve().parent
LOG_PATH = SCRIPT_DIR / "install-desktop-apps.log"


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("installer")
    logger.setLevel(logging.INFO)
    logger.handlers = []

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


def run_cmd(logger: logging.Logger, cmd: list[str], check: bool = False) -> int:
    logger.info("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            check=check,
            capture_output=True,
            text=True,
        )
    except Exception:
        logger.exception("Command failed to start.")
        return 1

    if result.stdout:
        logger.info("stdout:\n%s", result.stdout.strip())
    if result.stderr:
        logger.info("stderr:\n%s", result.stderr.strip())

    if result.returncode != 0:
        logger.error("Exit code: %s", result.returncode)
    return result.returncode


def powershell(logger: logging.Logger, script: str) -> int:
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
    return run_cmd(logger, cmd)


def ensure_choco(logger: logging.Logger) -> None:
    if shutil.which("choco"):
        logger.info("Chocolatey already installed.")
        return

    logger.info("Chocolatey not found; installing.")
    install_script = textwrap.dedent(
        r"""
        Set-ExecutionPolicy Bypass -Scope Process -Force;
        [System.Net.ServicePointManager]::SecurityProtocol = `
          [System.Net.ServicePointManager]::SecurityProtocol -bor 3072;
        iwr https://community.chocolatey.org/install.ps1 -UseBasicParsing | iex;
        """
    ).strip()
    powershell(logger, install_script)


def choco_install(logger: logging.Logger, package: str) -> int:
    return run_cmd(logger, ["choco", "install", package, "-y"])


def download_file(logger: logging.Logger, url: str, dest: Path) -> bool:
    try:
        logger.info("Downloading %s -> %s", url, dest)
        with urllib.request.urlopen(url) as response, dest.open("wb") as f:
            f.write(response.read())
        return True
    except Exception:
        logger.exception("Download failed for %s", url)
        return False


def run_installer(logger: logging.Logger, installer: Path, args: list[str]) -> int:
    if not installer.exists():
        logger.error("Installer not found: %s", installer)
        return 1
    return run_cmd(logger, [str(installer), *args])


def install_chrome(logger: logging.Logger) -> None:
    choco_install(logger, "googlechrome")


def install_vscode(logger: logging.Logger) -> None:
    choco_install(logger, "vscode")


def install_rstudio(logger: logging.Logger) -> None:
    choco_install(logger, "rstudio")


def install_qgis(logger: logging.Logger) -> None:
    choco_install(logger, "qgis")


def install_postgis(logger: logging.Logger) -> None:
    choco_install(logger, "postgresql")
    choco_install(logger, "postgis")


def install_cyberduck(logger: logging.Logger) -> None:
    choco_install(logger, "cyberduck")


def install_conda(logger: logging.Logger) -> None:
    choco_install(logger, "miniforge3")


def install_windirstat(logger: logging.Logger) -> None:
    choco_install(logger, "windirstat")


def install_arcgis_pro(logger: logging.Logger, url: str | None, s3_uri: str | None) -> None:
    # ArcGIS Pro requires .NET Desktop Runtime; install via Chocolatey.
    choco_install(logger, "dotnet-desktopruntime-8")

    installer = SCRIPT_DIR / "ArcGISProInstaller.exe"
    if s3_uri:
        rc = run_cmd(logger, ["aws", "s3", "cp", s3_uri, str(installer)])
        if rc != 0:
            return
    elif url:
        if not download_file(logger, url, installer):
            return
    else:
        logger.error("ArcGIS Pro requires an installer URL or S3 URI. Skipping.")
        return

    run_installer(logger, installer, ["/quiet", "/norestart"])


def install_office(
    logger: logging.Logger, odt_url: str | None, config_path: str | None
) -> None:
    if not odt_url or not config_path:
        logger.error("Office requires ODT URL and configuration XML path. Skipping.")
        return

    config = Path(config_path).expanduser()
    if not config.exists():
        logger.error("Office config XML not found: %s", config)
        return

    odt_zip = SCRIPT_DIR / "OfficeDeploymentTool.exe"
    if not download_file(logger, odt_url, odt_zip):
        return

    extract_dir = SCRIPT_DIR / "odt"
    extract_dir.mkdir(exist_ok=True)
    run_installer(
        logger,
        odt_zip,
        ["/quiet", f"/extract:{extract_dir}"],
    )

    setup_exe = extract_dir / "setup.exe"
    run_installer(logger, setup_exe, ["/configure", str(config)])


APP_CATALOG = [
    "Chrome",
    "ArcGIS Pro",
    "Microsoft Office",
    "VS Code",
    "RStudio",
    "PostGIS",
    "QGIS",
    "Conda",
    "Cyberduck",
    "WinDirStat",
]


def select_apps() -> list[str]:
    choices = ["ALL"] + APP_CATALOG
    selected = questionary.checkbox(
        "Select apps to install",
        choices=choices,
    ).ask()
    if not selected:
        return []
    if "ALL" in selected:
        return APP_CATALOG
    return selected


@click.command()
@click.option(
    "--arcgis-pro-url",
    default=None,
    help="ArcGIS Pro installer URL (required if ArcGIS Pro is selected).",
)
@click.option(
    "--arcgis-pro-s3-uri",
    default="s3://wri-users/crowe/installers/ArcGISPro_36_197382.exe",
    help="ArcGIS Pro installer S3 URI (used if ArcGIS Pro is selected).",
)
@click.option(
    "--office-odt-url",
    default=None,
    help="Office Deployment Tool URL (required if Office is selected).",
)
@click.option(
    "--office-config",
    default=None,
    help="Path to Office ODT configuration XML.",
)
def main(
    arcgis_pro_url: str | None,
    arcgis_pro_s3_uri: str | None,
    office_odt_url: str | None,
    office_config: str | None,
) -> None:
    logger = setup_logger()
    logger.info("Log file: %s", LOG_PATH)

    if os.name != "nt":
        logger.error("This script is intended for Windows.")
        sys.exit(1)

    ensure_choco(logger)

    apps = select_apps()
    if not apps:
        logger.info("No apps selected. Exiting.")
        return

    logger.info("Selected apps: %s", ", ".join(apps))

    for app in apps:
        logger.info("Starting install: %s", app)
        try:
            if app == "Chrome":
                install_chrome(logger)
            elif app == "ArcGIS Pro":
                install_arcgis_pro(logger, arcgis_pro_url, arcgis_pro_s3_uri)
            elif app == "Microsoft Office":
                install_office(logger, office_odt_url, office_config)
            elif app == "VS Code":
                install_vscode(logger)
            elif app == "RStudio":
                install_rstudio(logger)
            elif app == "PostGIS":
                install_postgis(logger)
            elif app == "QGIS":
                install_qgis(logger)
            elif app == "Conda":
                install_conda(logger)
            elif app == "Cyberduck":
                install_cyberduck(logger)
            elif app == "WinDirStat":
                install_windirstat(logger)
            else:
                logger.error("Unknown app: %s", app)
        except Exception:
            logger.exception("Install failed for %s", app)
        logger.info("Finished install: %s", app)


if __name__ == "__main__":
    main()
