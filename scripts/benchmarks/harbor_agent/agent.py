"""OpenProgram as a Harbor installed agent.

Load it with ``harbor run -a scripts.benchmarks.harbor_agent.agent:OpenProgramAgent``
from the repository root (or with the root on ``PYTHONPATH``). It installs
the ``openprogram`` CLI in each task container, runs ``openprogram --print``
in the task's working directory with the provider key Harbor resolved for
``-m`` in that process's environment, and reports tokens and cost from
OpenProgram's usage summary. See ``README.md`` beside this file for the commands.

Agent kwargs (``--ak``):

``bundle``
    Host path of a prebuilt install (``frontier_eval`` packs one per
    commit): uploaded and unpacked with no network in every ``x86_64``
    glibc container. Other containers fall back to ``wheel``.
``wheel``
    Host path of an ``openprogram`` wheel to upload and install.
``git_ref``
    Commit or tag of ``git_url`` to install instead of a wheel. Harbor
    reads ``--ak`` values as JSON, so quote a commit id that is all digits
    or digits and one ``e``: ``--ak 'git_ref="1234e56"'``.
``constraints``
    Host path of the constraints file exported from the same commit's
    ``uv.lock`` (``uv export --frozen --no-hashes --no-emit-project``).
    Required: it holds every dependency to the locked version, so every
    trial runs the same code.
``git_url``
    Repository for ``git_ref`` (default: the public GitHub repository).
``provider``
    OpenProgram provider id, when it differs from the first segment of
    ``-m`` (for example ``kimi_coding``).
``key_env``
    The environment variable OpenProgram reads that provider's key from,
    when it is not ``<PROVIDER>_API_KEY`` or a name the adapter knows.
``uv_version`` / ``python_version``
    Override the pinned uv release and Python version.
``cost_ledger``
    Host path of a JSON-lines file outside the job directory; every
    attempt appends its tokens and cost there, so the cost of attempts
    Harbor deletes before a retry is still counted (``frontier_eval``
    sets it).
``reasoning_effort``
    ``none``, ``minimal``, ``low``, ``medium``, ``high``, ``xhigh`` or ``max``,
    passed to ``openprogram --print --effort`` (Harbor's
    ``--reasoning-effort`` sets it for every agent of a job). Without it
    OpenProgram runs at its Agent's own effort. The adapter declares the
    capability, as Harbor's Codex adapter does.
``disable_web_search``
    ``true`` runs ``openprogram --print --no-web``: no web tool and no
    provider-side search. The adapter declares ``native_web_tools``, so a
    task whose network policy is restricted turns them off too, as for
    Harbor's Codex and Claude Code adapters.

This module imports Harbor; ``commands.py`` holds everything that does not.
"""
from __future__ import annotations

import json
import shlex
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar

from harbor.agents.agent_auth_resolution import (
    ModelConnectionSpec,
    require_model_api_key,
)
from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.base import (
    AgentAuthenticationError,
    ApiInternalServerError,
    ApiRateLimitError,
    ApiResponseStalledError,
    BaseInstalledAgent,
    ModelNotFoundError,
    NetworkConnectionError,
    NonZeroAgentExitCodeError,
    with_prompt_template,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext, ModelUsage
from harbor.models.trajectories import Trajectory

from scripts.benchmarks.harbor_agent import commands

# The Harbor error for each provider or network cause the usage summary
# names (:func:`commands.error_kind`), so retry policy
# (``--retry-include``) sees infrastructure, not a task failure.
_ERRORS: dict[str, type[NonZeroAgentExitCodeError]] = {
    "authentication": AgentAuthenticationError,
    "rate_limit": ApiRateLimitError,
    "server": ApiInternalServerError,
    "network": NetworkConnectionError,
    "model_not_found": ModelNotFoundError,
    "stalled": ApiResponseStalledError,
}


class OpenProgramAgent(BaseInstalledAgent):
    """Runs one Harbor task with ``openprogram --print``."""

    # Harbor resolves the API key for ``-m`` from its provider registry
    # (``DEEPSEEK_API_KEY`` for ``deepseek/...``), or from ``--api-key-name``.
    MODEL_CONNECTION = ModelConnectionSpec()
    # The run's transcript becomes ``trajectory.json`` for ``harbor view``
    # (``atif``); ``reasoning_effort`` reaches ``--print --effort``;
    # ``disable_web_search`` (asked for, or applied by a restricted network
    # policy) reaches ``--no-web``.
    capabilities = AgentCapabilities(**commands.CAPABILITIES)
    # uv downloads Python and OpenProgram's dependencies on a cold image.
    DEFAULT_SETUP_TIMEOUT_SEC: ClassVar[float] = 900.0

    def __init__(
        self,
        *args: Any,
        wheel: str | None = None,
        bundle: str | None = None,
        git_ref: str | None = None,
        constraints: str | None = None,
        git_url: str = commands.DEFAULT_GIT_URL,
        provider: str | None = None,
        uv_version: str = commands.UV_VERSION,
        python_version: str = commands.PYTHON_VERSION,
        cost_ledger: str | None = None,
        reasoning_effort: str | None = None,
        key_env: str | None = None,
        **kwargs: Any,
    ) -> None:
        # Passed on so Harbor checks it against the declared capabilities;
        # ``disable_web_search`` stays in ``kwargs`` so Harbor sees it was
        # given explicitly.
        super().__init__(*args, reasoning_effort=reasoning_effort, **kwargs)
        self._effort = (commands.effort_flag(reasoning_effort)
                        if reasoning_effort is not None else None)
        self._no_web = kwargs.get("disable_web_search") is True
        self._key_env = key_env
        self._cost_ledger = Path(cost_ledger).expanduser().resolve() if cost_ledger else None
        # Harbor builds a new agent for every attempt, retries included.
        self._attempt_id = uuid.uuid4().hex
        self._wheel = Path(wheel).expanduser().resolve() if wheel else None
        if self._wheel is not None and not self._wheel.is_file():
            raise commands.AdapterConfigError(f"wheel not found: {self._wheel}")
        self._bundle = Path(bundle).expanduser().resolve() if bundle else None
        if self._bundle is not None and not self._bundle.is_file():
            raise commands.AdapterConfigError(f"install bundle not found: {self._bundle}")
        if not constraints:
            raise commands.AdapterConfigError(
                "constraints=<path> is required: export it from the commit's uv.lock "
                "with `uv export --frozen --no-hashes --no-emit-project -o constraints.txt`"
            )
        self._constraints = Path(constraints).expanduser().resolve()
        if not self._constraints.is_file():
            raise commands.AdapterConfigError(f"constraints file not found: {self._constraints}")
        self._package = commands.package_spec(
            wheel_path=(commands.wheel_remote_path(self._wheel.name)
                        if self._wheel is not None else None),
            git_ref=git_ref,
            git_url=git_url,
        )
        self._provider = provider
        self._uv_version = uv_version
        self._python_version = python_version
        self._model = commands.openprogram_model(self.model_name, provider)
        commands.key_env_name(self._model.partition("/")[0], key_env)

    @staticmethod
    def name() -> str:
        return "openprogram"

    @classmethod
    def resolve_model_connection_config(cls, model_name, resolve_env, **options):
        """Report the provider's public endpoint so Harbor can allowlist it.

        OpenProgram picks its own endpoint; Harbor only needs the host to
        open the network for an allowlisted task, so the provider default
        from Harbor's registry is reported as an estimate.
        """
        connection = super().resolve_model_connection_config(
            model_name, resolve_env, **options)
        if connection.inference_base_url is None:
            spec = options.get("spec") or cls.MODEL_CONNECTION
            estimate = spec.default_base_url(connection.provider) if spec else None
            if estimate:
                connection = replace(connection, inferred_base_url=estimate)
        return connection

    @classmethod
    def check_model_credentials(cls, connection, *, kwargs, env, explicit_env=None):
        # OpenProgram's run reads the key Harbor resolved; there is no ambient login.
        require_model_api_key(connection, cls.name())

    async def set_web_tools_disabled(self, disabled: bool, environment: BaseEnvironment) -> None:
        """A restricted network policy turns the web tools off (``--no-web``)
        unless ``disable_web_search`` was given explicitly, as Harbor does
        for the agents that declare ``native_web_tools``."""
        if "disable_web_search" not in self._model_connection_kwargs:
            self._no_web = bool(disabled)

    def get_version_command(self) -> str | None:
        return f"{shlex.quote(str(commands.OPENPROGRAM))} --version"

    def parse_version(self, stdout: str) -> str:
        return stdout.strip().removeprefix("openprogram").strip()

    async def install(self, environment: BaseEnvironment) -> None:
        if self._bundle is not None:
            usable = await environment.exec(command=commands.BUNDLE_USABLE_CHECK, user="root")
            if usable.return_code == 0:
                await self.exec_as_root(
                    environment, command=f"mkdir -p {shlex.quote(str(commands.BUNDLE_PATH.parent))}")
                await environment.upload_file(self._bundle, str(commands.BUNDLE_PATH))
                await self.exec_as_root(environment, command=commands.bundle_install_command())
                return
            self.logger.warning("the install bundle cannot run in this container "
                                "(needs x86_64, glibc, tar and gzip); installing from the network")
        has_curl = await environment.exec(command="command -v curl", user="root")
        dependencies: tuple[str, ...] = ()
        if has_curl.return_code != 0:
            dependencies += ("curl", "ca_certificates")
        if self._wheel is None:
            dependencies += ("git",)
        await self.ensure_system_dependencies(environment, dependencies)
        await self.exec_as_root(
            environment,
            command=f"mkdir -p {shlex.quote(str(commands.WHEEL_DIR))}",
        )
        await environment.upload_file(self._constraints, str(commands.CONSTRAINTS_PATH))
        if self._wheel is not None:
            await environment.upload_file(self._wheel, self._package)
        await self.exec_as_root(
            environment,
            command=commands.install_command(
                self._package,
                uv_version=self._uv_version,
                python_version=self._python_version,
            ),
        )

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        api_key = self.model_connection.api_key
        if not api_key:
            raise AgentAuthenticationError(
                f"no API key for {self.model_name}; export the provider's key "
                "variable or pass --api-key-name"
            )
        await commands.run_attempt(
            turn=lambda: self._run_turn(instruction, environment, api_key),
            stop=lambda: self._stop_run(environment),
            finish=lambda: self._finish(environment),
            rebuild=lambda: self._rebuild_transcript(environment),
            warn=self.logger.warning,
        )

    async def _run_turn(self, instruction: str, environment: BaseEnvironment,
                        api_key: str) -> None:
        provider = self._model.partition("/")[0]
        try:
            await self.exec_as_agent(
                environment,
                command=commands.run_command(
                    self._model, logs_dir=self.environment_logs_dir,
                    effort=self._effort, no_web=self._no_web,
                    key_env=commands.key_env_name(provider, self._key_env),
                ),
                # The key and the instruction travel in the environment of
                # this one command, never in its text.
                env={commands.API_KEY_ENV: api_key, commands.INSTRUCTION_ENV: instruction},
            )
        except NonZeroAgentExitCodeError as exc:
            # Typed by OpenProgram's own summary, never by the run's output.
            kind = commands.error_kind(await self._read_usage(environment))
            if kind is not None:
                raise _ERRORS[kind](str(exc)) from exc
            raise
        # A zero exit is trusted only when the usage summary agrees.
        failure = commands.run_failure(await self._read_usage(environment))
        if failure is not None:
            raise NonZeroAgentExitCodeError(failure)

    def _classify_exec_error(self, command: str, result: Any) -> NonZeroAgentExitCodeError:
        """Harbor's error for a failed command: Harbor's own matcher where
        :func:`commands.harbor_matcher_applies`, else the plain error, which
        :meth:`_run_turn` types from the usage summary."""
        if commands.harbor_matcher_applies(command):
            return super()._classify_exec_error(command, result)
        return NonZeroAgentExitCodeError(
            f"openprogram --print exited {result.return_code}\n"
            f"output: {self._truncate_output(result.stdout)}")

    async def _read_usage(self, environment: BaseEnvironment) -> dict[str, Any] | None:
        read = await environment.exec(
            command=f"cat {shlex.quote(commands.usage_file(self.environment_logs_dir))}",
        )
        return commands.parse_usage(read.stdout) if read.return_code == 0 else None

    async def _finish(self, environment: BaseEnvironment) -> None:
        done = await environment.exec(
            command=commands.finish_command(logs_dir=self.environment_logs_dir),
            timeout_sec=120,
        )
        if done.return_code != 0:
            self.logger.warning("OpenProgram state not copied out, or a credential left "
                                "in the container")

    async def _rebuild_transcript(self, environment: BaseEnvironment) -> None:
        # A run killed before it wrote its transcript gets it from its state.
        await environment.exec(
            command=commands.rebuild_transcript_command(logs_dir=self.environment_logs_dir),
            timeout_sec=120,
        )

    async def _stop_run(self, environment: BaseEnvironment) -> None:
        # Harbor's agent timeout: stop OpenProgram so it writes its usage
        # summary.
        try:
            await environment.exec(
                command=commands.stop_command(logs_dir=self.environment_logs_dir),
                user="root",
                timeout_sec=commands.STOP_GRACE_SEC + 20,
            )
        except Exception:  # noqa: BLE001 - the timeout is still reported
            self.logger.warning("OpenProgram run not stopped after the agent timeout",
                                exc_info=True)

    def convert_trajectory(self, logs_dir: Path) -> Trajectory | None:
        """The run's transcript as Harbor's trajectory (ATIF)."""
        try:
            text = (logs_dir / commands.TRANSCRIPT_FILE_NAME).read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            usage = commands.parse_usage(
                (logs_dir / commands.USAGE_FILE_NAME).read_text(encoding="utf-8"))
        except OSError:
            usage = None
        data = commands.trajectory(commands.parse_transcript(text), usage,
                                   agent_version=self.version() or "unknown")
        return Trajectory.model_validate(data) if data is not None else None

    def _write_trajectory(self) -> None:
        try:
            converted = self.convert_trajectory(self.logs_dir)
        except Exception:  # noqa: BLE001 - the transcript itself is still kept
            self.logger.warning("OpenProgram transcript not converted to a trajectory",
                                exc_info=True)
            return
        if converted is None:
            return
        target = self.logs_dir / commands.TRAJECTORY_FILE_NAME
        try:
            target.write_text(json.dumps(converted.to_json_dict(), ensure_ascii=False,
                                         indent=2), encoding="utf-8")
        except OSError:
            self.logger.warning("trajectory.json not written", exc_info=True)

    def populate_context_post_run(self, context: AgentContext) -> None:
        self._write_trajectory()
        path = self.logs_dir / commands.USAGE_FILE_NAME
        try:
            usage = commands.parse_usage(path.read_text(encoding="utf-8"))
        except OSError:
            return
        if usage is None:
            return
        fields = commands.context_fields(usage)
        if self._cost_ledger is not None:
            try:
                commands.append_attempt(self._cost_ledger, trial=self.logs_dir.parent.name,
                                        attempt=self._attempt_id, fields=fields,
                                        agent=self.name(), error_kind=commands.error_kind(usage))
            except OSError:
                self.logger.warning("attempt not written to the cost ledger", exc_info=True)
        context.n_input_tokens = fields["n_input_tokens"]
        context.n_cache_tokens = fields["n_cache_tokens"]
        context.n_output_tokens = fields["n_output_tokens"]
        context.cost_usd = fields["cost_usd"]
        if fields["model_usage"]:
            context.model_usage = {
                name: ModelUsage(**values)
                for name, values in fields["model_usage"].items()
            }
        context.metadata = {**(context.metadata or {}), **fields["metadata"]}
