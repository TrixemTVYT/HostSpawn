from .arma3 import Arma3Adapter
from .base import Adapter, Unsupported
from .reforger import ReforgerAdapter
from .reforger_server import ReforgerServer
from .rust import RustAdapter
from .rust_server import RustServer

# "rust" / "reforger": wrap an existing setup (rustctl.sh, a systemd unit). The others are servers Manager runs itself.
ADAPTERS = {"rust": RustAdapter, "reforger": ReforgerAdapter, "arma3": Arma3Adapter,
            "rust-server": RustServer, "reforger-server": ReforgerServer}


def build(servers: dict) -> dict[str, Adapter]:
    return {sid: ADAPTERS[cfg["adapter"]](sid, cfg) for sid, cfg in servers.items() if cfg.get("adapter") in ADAPTERS}
