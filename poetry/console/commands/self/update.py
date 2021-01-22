from __future__ import unicode_literals

import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile

from functools import cmp_to_key
from gzip import GzipFile

from cleo import argument
from cleo import option

from poetry.console.exceptions import PoetrySimpleConsoleException
from poetry.core.packages import Dependency

from ..command import Command


try:
    from urllib.error import HTTPError
    from urllib.request import urlopen
except ImportError:
    from urllib2 import HTTPError
    from urllib2 import urlopen


BIN = """# -*- coding: utf-8 -*-
import glob
import sys
import os

lib = os.path.normpath(os.path.join(os.path.realpath(__file__), "../..", "lib"))
vendors = os.path.join(lib, "poetry", "_vendor")
current_vendors = os.path.join(
    vendors, "py{}".format(".".join(str(v) for v in sys.version_info[:2]))
)
sys.path.insert(0, lib)
sys.path.insert(0, current_vendors)

if __name__ == "__main__":
    from poetry.console import main
    main()
"""

BAT = '@echo off\r\n{python_executable} "{poetry_bin}" %*\r\n'


class SelfUpdateCommand(Command):

    name = "self update"
    description = "Updates Poetry to the latest version."

    arguments = [argument("version", "The version to update to.", optional=True)]
    options = [option("preview", None, "Install prereleases.")]

    REPOSITORY_URL = "https://github.com/python-poetry/poetry"
    BASE_URL = REPOSITORY_URL + "/releases/download"

    @property
    def home(self):
        from pathlib import Path

        return Path(os.environ.get("POETRY_HOME", "~/.poetry")).expanduser()

    @property
    def bin(self):
        return self.home / "bin"

    @property
    def lib(self):
        return self.home / "lib"

    @property
    def lib_backup(self):
        return self.home / "lib-backup"

    def handle(self):
        from poetry.__version__ import __version__
        from poetry.core.semver import Version
        from poetry.repositories.pypi_repository import PyPiRepository

        self._check_recommended_installation()

        version = self.argument("version")
        if not version:
            version = ">=" + __version__

        repo = PyPiRepository(fallback=False)
        packages = repo.find_packages(
            Dependency("poetry", version, allows_prereleases=self.option("preview"))
        )
        if not packages:
            self.line("No release found for the specified version")
            return

        packages.sort(
            key=cmp_to_key(
                lambda x, y: 0
                if x.version == y.version
                else int(x.version < y.version or -1)
            )
        )

        release = None
        for package in packages:
            if package.is_prerelease():
                if self.option("preview"):
                    release = package

                    break

                continue

            release = package

            break

        if release is None:
            self.line("No new release found")
            return

        if release.version == Version.parse(__version__):
            self.line("You are using the latest version")
            return

        self.update(release)

    def update(self, release):
        version = release.version
        self.line("Updating to <info>{}</info>".format(version))

        if self.lib_backup.exists():
            shutil.rmtree(str(self.lib_backup))

        # Backup the current installation
        if self.lib.exists():
            shutil.copytree(str(self.lib), str(self.lib_backup))
            shutil.rmtree(str(self.lib))

        try:
            self._update(version)
        except Exception:
            if not self.lib_backup.exists():
                raise

            shutil.copytree(str(self.lib_backup), str(self.lib))
            shutil.rmtree(str(self.lib_backup))

            raise
        finally:
            if self.lib_backup.exists():
                shutil.rmtree(str(self.lib_backup))

        self.make_bin()

        self.line("")
        self.line("")
        self.line(
            "<info>Poetry</info> (<comment>{}</comment>) is installed now. Great!".format(
                version
            )
        )

    def _update(self, version):
        from poetry.utils.helpers import temporary_directory

        release_name = self._get_release_name(version)

        checksum = "{}.sha256sum".format(release_name)

        base_url = self.BASE_URL

        try:
            r = urlopen(base_url + "/{}/{}".format(version, checksum))
        except HTTPError as e:
            if e.code == 404:
                raise RuntimeError("Could not find {} file".format(checksum))

            raise

        checksum = r.read().decode().strip()

        # We get the payload from the remote host
        name = "{}.tar.gz".format(release_name)
        try:
            r = urlopen(base_url + "/{}/{}".format(version, name))
        except HTTPError as e:
            if e.code == 404:
                raise RuntimeError("Could not find {} file".format(name))

            raise

        meta = r.info()
        size = int(meta["Content-Length"])
        current = 0
        block_size = 8192

        bar = self.progress_bar(max=size)
        bar.set_format(" - Downloading <info>{}</> <comment>%percent%%</>".format(name))
        bar.start()

        sha = hashlib.sha256()
        with temporary_directory(prefix="poetry-updater-") as dir_:
            tar = os.path.join(dir_, name)
            with open(tar, "wb") as f:
                while True:
                    buffer = r.read(block_size)
                    if not buffer:
                        break

                    current += len(buffer)
                    f.write(buffer)
                    sha.update(buffer)

                    bar.set_progress(current)

            bar.finish()

            # Checking hashes
            if checksum != sha.hexdigest():
                raise RuntimeError(
                    "Hashes for {} do not match: {} != {}".format(
                        name, checksum, sha.hexdigest()
                    )
                )

            gz = GzipFile(tar, mode="rb")
            try:
                with tarfile.TarFile(tar, fileobj=gz, format=tarfile.PAX_FORMAT) as f:
                    f.extractall(str(self.lib))
            finally:
                gz.close()

    def process(self, *args):
        return subprocess.check_output(list(args), stderr=subprocess.STDOUT)

    def _check_recommended_installation(self):
        from pathlib import Path

        current = Path(__file__)
        try:
            current.relative_to(self.home)
        except ValueError:
            raise PoetrySimpleConsoleException(
                "Poetry was not installed with the recommended installer, "
                "so it cannot be updated automatically."
            )

    def _get_release_name(self, version):
        platform = sys.platform
        if platform == "linux2":
            platform = "linux"

        return "poetry-{}-{}".format(version, platform)

    def make_bin(self):
        from poetry.utils._compat import WINDOWS

        self.bin.mkdir(0o755, parents=True, exist_ok=True)

        python_executable = self._which_python()

        if WINDOWS:
            with self.bin.joinpath("poetry.bat").open("w", newline="") as f:
                f.write(
                    BAT.format(
                        python_executable=python_executable,
                        poetry_bin=str(self.bin / "poetry").replace(
                            os.environ["USERPROFILE"], "%USERPROFILE%"
                        ),
                    )
                )

        bin_content = BIN
        if not WINDOWS:
            bin_content = "#!/usr/bin/env {}\n".format(python_executable) + bin_content

        self.bin.joinpath("poetry").write_text(bin_content, encoding="utf-8")

        if not WINDOWS:
            # Making the file executable
            st = os.stat(str(self.bin.joinpath("poetry")))
            os.chmod(str(self.bin.joinpath("poetry")), st.st_mode | stat.S_IEXEC)

    def _which_python(self):
        """
        Decides which python executable we'll embed in the launcher script.
        """
        from poetry.utils._compat import WINDOWS

        allowed_executables = ["python", "python3"]
        if WINDOWS:
            allowed_executables += ["py.exe -3", "py.exe -2"]

        # \d in regex ensures we can convert to int later
        version_matcher = re.compile(r"^Python (?P<major>\d+)\.(?P<minor>\d+)\..+$")
        fallback = None
        for executable in allowed_executables:
            try:
                raw_version = subprocess.check_output(
                    executable + " --version", stderr=subprocess.STDOUT, shell=True
                ).decode("utf-8")
            except subprocess.CalledProcessError:
                continue

            match = version_matcher.match(raw_version.strip())
            if match and tuple(map(int, match.groups())) >= (3, 0):
                # favor the first py3 executable we can find.
                return executable

            if fallback is None:
                # keep this one as the fallback; it was the first valid executable we found.
                fallback = executable

        if fallback is None:
            # Avoid breaking existing scripts
            fallback = "python"

        return fallback
