import json
import platform
import shutil
import socket
from pathlib import Path

import pytest

from charter.enumerate import enumerate_stdio_server
from charter.models import Server, Transport
from charter.sandbox import require_bubblewrap

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "sandbox_probe_mcp_server.py"
).resolve()


pytestmark = pytest.mark.skipif(
    platform.system() != "Linux" or shutil.which("bwrap") is None,
    reason="the real isolation acceptance test requires Linux and Bubblewrap",
)


def test_hostile_server_is_confined_by_the_real_bubblewrap_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    fixture = project / "sandbox_probe_mcp_server.py"
    fixture.write_bytes(FIXTURE.read_bytes())
    host_home = project / "host-home"
    host_home.mkdir()
    (host_home / ".charter-host-marker").write_text("host-only\n")
    monkeypatch.setenv("HOME", str(host_home))
    monkeypatch.setenv("CHARTER_HOST_SECRET", "must-not-cross-the-sandbox-boundary")

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        config = {
            "mcpServers": {
                "hostile": {
                    "command": "/usr/bin/python3",
                    "args": [str(fixture), "--repo", str(project), "--host-port", str(port)],
                    "env": {"CONFIG_SECRET": "must-not-cross-the-sandbox-boundary-either"},
                }
            }
        }
        config_path = project / ".mcp.json"
        config_path.write_text(json.dumps(config))
        server = Server(
            name="hostile",
            transport=Transport.STDIO,
            command="/usr/bin/python3",
            args=tuple(config["mcpServers"]["hostile"]["args"]),
            env_var_names=("CONFIG_SECRET",),
            url=None,
            header_names=(),
            source_file=config_path,
            source_line=3,
        )

        require_bubblewrap.cache_clear()
        result = enumerate_stdio_server(server, timeout=5.0)

    assert result.error is None
    assert {tool.name for tool in result.tools} == {
        "sandbox_repo_read_only",
        "sandbox_home_isolated",
        "sandbox_environment_sanitized",
        "sandbox_network_blocked",
    }
    assert not (project / "sandbox-write-probe").exists()
