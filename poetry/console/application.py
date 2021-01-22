import logging

from typing import Any
from typing import List
from typing import Optional
from typing import cast

from cleo import Application as BaseApplication
from cleo import Command as BaseCommand
from cleo.events.console_command_event import ConsoleCommandEvent
from cleo.events.console_events import COMMAND
from cleo.events.event_dispatcher import EventDispatcher
from cleo.formatters.style import Style
from cleo.io.inputs.input import Input
from cleo.io.io import IO
from cleo.io.outputs.output import Output

from poetry.__version__ import __version__

from .commands.about import AboutCommand
from .commands.add import AddCommand
from .commands.build import BuildCommand
from .commands.cache.clear import CacheClearCommand
from .commands.cache.list import CacheListCommand
from .commands.check import CheckCommand
from .commands.command import Command
from .commands.config import ConfigCommand
from .commands.debug.info import DebugInfoCommand
from .commands.debug.resolve import DebugResolveCommand
from .commands.env.info import EnvInfoCommand
from .commands.env.list import EnvListCommand
from .commands.env.remove import EnvRemoveCommand
from .commands.env.use import EnvUseCommand
from .commands.env_command import EnvCommand
from .commands.export import ExportCommand
from .commands.init import InitCommand
from .commands.install import InstallCommand
from .commands.installer_command import InstallerCommand
from .commands.lock import LockCommand
from .commands.new import NewCommand
from .commands.publish import PublishCommand
from .commands.remove import RemoveCommand
from .commands.run import RunCommand
from .commands.search import SearchCommand
from .commands.self.update import SelfUpdateCommand
from .commands.shell import ShellCommand
from .commands.show import ShowCommand
from .commands.update import UpdateCommand
from .commands.version import VersionCommand
from .logging.io_formatter import IOFormatter
from .logging.io_handler import IOHandler


class Application(BaseApplication):
    def __init__(self):
        super(Application, self).__init__("poetry", __version__)

        self._poetry = None

        dispatcher = EventDispatcher()
        dispatcher.add_listener(COMMAND, self.register_command_loggers)
        dispatcher.add_listener(COMMAND, self.set_env)
        dispatcher.add_listener(COMMAND, self.set_installer)
        self.set_event_dispatcher(dispatcher)

    @property
    def default_commands(self) -> List[BaseCommand]:
        default_commands = super().default_commands

        commands = [
            AboutCommand(),
            AddCommand(),
            BuildCommand(),
            CheckCommand(),
            ConfigCommand(),
            ExportCommand(),
            InitCommand(),
            InstallCommand(),
            LockCommand(),
            NewCommand(),
            PublishCommand(),
            RemoveCommand(),
            RunCommand(),
            SearchCommand(),
            ShellCommand(),
            ShowCommand(),
            UpdateCommand(),
            VersionCommand(),
        ]

        # Cache commands
        commands += [
            CacheClearCommand(),
            CacheListCommand(),
        ]

        # Debug commands
        commands += [DebugInfoCommand(), DebugResolveCommand()]

        # Env commands
        commands += [
            EnvInfoCommand(),
            EnvListCommand(),
            EnvRemoveCommand(),
            EnvUseCommand(),
        ]

        # Self commands
        commands += [SelfUpdateCommand()]

        return default_commands + commands

    @property
    def poetry(self):
        from pathlib import Path

        from poetry.factory import Factory

        if self._poetry is not None:
            return self._poetry

        self._poetry = Factory().create_poetry(Path.cwd())

        return self._poetry

    def reset_poetry(self) -> None:
        self._poetry = None

    def create_io(
        self,
        input: Optional[Input] = None,
        output: Optional[Output] = None,
        error_output: Optional[Output] = None,
    ) -> IO:
        io = super(Application, self).create_io(input, output, error_output)

        # Set our own CLI styles
        formatter = io.output.formatter
        formatter.set_style("c1", Style("cyan"))
        formatter.set_style("c2", Style("default", options=["bold"]))
        formatter.set_style("info", Style("blue"))
        formatter.set_style("comment", Style("green"))
        formatter.set_style("warning", Style("yellow"))
        formatter.set_style("debug", Style("default", options=["dark"]))
        formatter.set_style("success", Style("green"))

        # Dark variants
        formatter.set_style("c1_dark", Style("cyan", options=["dark"]))
        formatter.set_style("c2_dark", Style("default", options=["bold", "dark"]))
        formatter.set_style("success_dark", Style("green", options=["dark"]))

        io.output.set_formatter(formatter)
        io.error_output.set_formatter(formatter)

        return io

    def register_command_loggers(
        self, event: ConsoleCommandEvent, event_name: str, _: Any
    ) -> None:
        command = event.command
        if not isinstance(command, Command):
            return

        io = event.io

        loggers = [
            "poetry.packages.locker",
            "poetry.packages.package",
            "poetry.utils.password_manager",
        ]

        loggers += command.loggers

        handler = IOHandler(io)
        handler.setFormatter(IOFormatter())

        for logger in loggers:
            logger = logging.getLogger(logger)

            logger.handlers = [handler]

            level = logging.WARNING
            # The builders loggers are special and we can actually
            # start at the INFO level.
            if logger.name.startswith("poetry.core.masonry.builders"):
                level = logging.INFO

            if io.is_debug():
                level = logging.DEBUG
            elif io.is_very_verbose() or io.is_verbose():
                level = logging.INFO

            logger.setLevel(level)

    def set_env(self, event: ConsoleCommandEvent, event_name: str, _: Any):
        from poetry.utils.env import EnvManager

        command: EnvCommand = cast(EnvCommand, event.command)
        if not isinstance(command, EnvCommand):
            return

        if command.env is not None:
            return

        io = event.io
        poetry = command.poetry

        env_manager = EnvManager(poetry)
        env = env_manager.create_venv(io)

        if env.is_venv() and io.is_verbose():
            io.write_line("Using virtualenv: <comment>{}</>".format(env.path))

        command.set_env(env)

    def set_installer(
        self, event: ConsoleCommandEvent, event_name: str, _: Any
    ) -> None:
        command: InstallerCommand = cast(InstallerCommand, event.command)
        if not isinstance(command, InstallerCommand):
            return

        # If the command already has an installer
        # we skip this step
        if command.installer is not None:
            return

        from poetry.installation.installer import Installer

        poetry = command.poetry
        installer = Installer(
            event.io,
            command.env,
            poetry.package,
            poetry.locker,
            poetry.pool,
            poetry.config,
        )
        installer.use_executor(poetry.config.get("experimental.new-installer", False))
        command.set_installer(installer)


if __name__ == "__main__":
    Application().run()
