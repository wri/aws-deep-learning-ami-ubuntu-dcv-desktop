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


def run_cmd_capture(cmd: list[str]) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        return 1, "", str(exc)

    return result.returncode, result.stdout or "", result.stderr or ""


def run_cmd_capture_logged(logger: logging.Logger, cmd: list[str]) -> tuple[int, str, str]:
    logger.info("Running: %s", " ".join(cmd))
    rc, stdout, stderr = run_cmd_capture(cmd)
    if stdout:
        logger.info("stdout:\n%s", stdout.strip())
    if stderr:
        logger.info("stderr:\n%s", stderr.strip())
    if rc != 0:
        logger.error("Exit code: %s", rc)
    return rc, stdout, stderr


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


def choco_install_result(logger: logging.Logger, package: str) -> str:
    rc, stdout, stderr = run_cmd_capture_logged(
        logger, ["choco", "install", package, "-y", "--skip-when-installed"]
    )
    output = f"{stdout}\n{stderr}".lower()
    if "already installed" in output:
        return "already-installed"
    if rc == 0:
        return "installed"
    return "failed"


def choco_install_result_with_params(logger: logging.Logger, package: str, params: str) -> str:
    cmd = [
        "choco",
        "install",
        package,
        "-y",
        "--skip-when-installed",
        "--params",
        params,
        "--params-global",
    ]
    redacted = params.replace("/Password:", "/Password:***")
    logger.info(
        "Running: choco install %s -y --skip-when-installed --params %s --params-global",
        package,
        redacted,
    )
    rc, stdout, stderr = run_cmd_capture(cmd)
    if stdout:
        logger.info("stdout:\n%s", stdout.strip())
    if stderr:
        logger.info("stderr:\n%s", stderr.strip())
    if rc != 0:
        logger.error("Exit code: %s", rc)

    output = f"{stdout}\n{stderr}".lower()
    if "already installed" in output:
        return "already-installed"
    if rc == 0:
        return "installed"
    return "failed"


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


def find_postgres_bin() -> Path | None:
    base = Path(r"C:\Program Files\PostgreSQL")
    if not base.exists():
        return None

    candidates: list[tuple[int, Path]] = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        try:
            version = int(child.name)
        except ValueError:
            continue
        bin_dir = child / "bin"
        if bin_dir.exists():
            candidates.append((version, bin_dir))

    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0], reverse=True)[0][1]


def ensure_path_contains(logger: logging.Logger, directory: Path) -> None:
    script = (
        "$path=[Environment]::GetEnvironmentVariable('Path','Machine');"
        f"$dir='{directory}';"
        "if ($path -notlike ('*' + $dir + '*')) {"
        "[Environment]::SetEnvironmentVariable('Path', $path + ';' + $dir, 'Machine');"
        "Write-Output 'PATH updated';"
        "} else { Write-Output 'PATH already contains directory'; }"
    )
    run_cmd(logger, ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script])


def prompt_postgres_password() -> str | None:
    while True:
        password = questionary.password(
            "Set PostgreSQL postgres user password",
            validate=lambda text: bool(text.strip()) or "Password cannot be empty.",
        ).ask()
        if password is None:
            return None
        confirm = questionary.password("Confirm password").ask()
        if confirm is None:
            return None
        if password == confirm:
            return password
        print("Passwords do not match. Please try again.")


def has_dotnet_desktop_runtime_8() -> bool:
    rc, stdout, _stderr = run_cmd_capture(["dotnet", "--list-runtimes"])
    if rc != 0:
        return False
    return any("microsoft.windowsdesktop.app 8." in line.lower() for line in stdout.splitlines())


def ensure_dotnet_desktop_runtime_8(logger: logging.Logger) -> bool:
    if has_dotnet_desktop_runtime_8():
        logger.info(".NET Desktop Runtime 8 detected.")
        return True

    logger.info(".NET Desktop Runtime 8 missing; installing.")
    installer = SCRIPT_DIR / "windowsdesktop-runtime-8.0.23-win-x64.exe"
    url = "https://builds.dotnet.microsoft.com/dotnet/WindowsDesktop/8.0.23/windowsdesktop-runtime-8.0.23-win-x64.exe"
    if not download_file(logger, url, installer):
        return False
    rc = run_installer(logger, installer, ["/install", "/quiet", "/norestart"])
    return rc == 0


def install_chrome(logger: logging.Logger) -> None:
    choco_install(logger, "googlechrome")


def install_vscode(logger: logging.Logger) -> None:
    choco_install(logger, "vscode")


def install_r(logger: logging.Logger) -> None:
    choco_install(logger, "r")


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
    if not ensure_dotnet_desktop_runtime_8(logger):
        logger.error("Failed to install .NET Desktop Runtime 8. Skipping ArcGIS Pro.")
        return

    downloads_dir = Path(os.environ.get("USERPROFILE", str(SCRIPT_DIR))) / "Downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)

    installer_name = None
    if s3_uri:
        installer_name = Path(s3_uri).name
    elif url:
        installer_name = Path(url).name

    if not installer_name:
        logger.error("ArcGIS Pro requires an installer URL or S3 URI. Skipping.")
        return

    installer = downloads_dir / installer_name

    if not installer.exists():
        if s3_uri:
            rc = run_cmd(logger, ["aws", "s3", "cp", s3_uri, str(installer)])
            if rc != 0:
                return
        else:
            if not download_file(logger, url, installer):
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


def is_arcgis_pro_installed() -> bool:
    return Path(r"C:\Program Files\ArcGIS\Pro\bin\ArcGISPro.exe").exists()


def is_office_installed() -> bool:
    return Path(r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE").exists()


APP_CATALOG = [
    "Chrome",
    "ArcGIS Pro",
    "Microsoft Office",
    "VS Code",
    "RStudio",
    "PostgreSQL",
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
    results: dict[str, str] = {}

    for app in apps:
        logger.info("Starting install: %s", app)
        try:
            if app == "Chrome":
                results[app] = choco_install_result(logger, "googlechrome")
            elif app == "ArcGIS Pro":
                if is_arcgis_pro_installed():
                    results[app] = "already-installed"
                    logger.info("Already installed: %s", app)
                else:
                    install_arcgis_pro(logger, arcgis_pro_url, arcgis_pro_s3_uri)
                    results[app] = "installed" if is_arcgis_pro_installed() else "failed"
            elif app == "Microsoft Office":
                if is_office_installed():
                    results[app] = "already-installed"
                    logger.info("Already installed: %s", app)
                else:
                    install_office(logger, office_odt_url, office_config)
                    results[app] = "installed" if is_office_installed() else "failed"
            elif app == "VS Code":
                results[app] = choco_install_result(logger, "vscode")
            elif app == "RStudio":
                rc_r = choco_install_result(logger, "r")
                rc_rstudio = choco_install_result(logger, "r.studio")
                if "failed" in (rc_r, rc_rstudio):
                    results[app] = "failed"
                elif rc_r == "already-installed" and rc_rstudio == "already-installed":
                    results[app] = "already-installed"
                else:
                    results[app] = "installed"
            elif app == "PostgreSQL":
                password = prompt_postgres_password()
                if not password:
                    logger.error("No PostgreSQL password provided. Skipping install.")
                    results[app] = "failed"
                else:
                    params = f"/Password:{password}"
                    results[app] = choco_install_result_with_params(logger, "postgresql", params)
                    if results[app] != "failed":
                        bin_dir = find_postgres_bin()
                        if bin_dir:
                            ensure_path_contains(logger, bin_dir)
            elif app == "QGIS":
                results[app] = choco_install_result(logger, "qgis")
            elif app == "Conda":
                results[app] = choco_install_result(logger, "miniforge3")
            elif app == "Cyberduck":
                results[app] = choco_install_result(logger, "cyberduck")
            elif app == "WinDirStat":
                results[app] = choco_install_result(logger, "windirstat")
            else:
                logger.error("Unknown app: %s", app)
                results[app] = "unknown"
        except Exception:
            logger.exception("Install failed for %s", app)
            results[app] = "failed"
        logger.info("Finished install: %s", app)

    logger.info("Install summary:")
    for app in apps:
        status = results.get(app, "unknown")
        logger.info("  %s: %s", app, status)

if __name__ == "__main__":
    main()
