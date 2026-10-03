from pathlib import Path


TOPOLOGY_REGISTRY = {
    "osvbng": {
        "containerlab_topology_file": str(
            Path(__file__).resolve().parents[1] / "osvbng01.clab.yml"
        ),
        "status": "supported",
    }
}

RECIPE_REGISTRY = {
    "ipoe-bind": {
        "status": "implemented",
        "module": "recipes.ipoe_bind",
        "timeout_setting": "default_job_timeout_seconds",
        "display_name": "IPoE Bind",
        "description": "Dual-stack IPoE session establishment dataset.",
        "artifacts": ["Dataset", "Access PCAP", "Blaster report", "Prometheus metrics", "Session dump"],
    },
    "ipoe-scale": {
        "status": "implemented",
        "module": "recipes.ipoe_scale",
        "timeout_setting": "scale_job_timeout_seconds",
        "display_name": "IPoE Scale",
        "description": "Session setup-rate sweep toward a target load with a CPU safety limit.",
        "artifacts": ["Time-series CSV", "CPU and setup-rate metrics"],
    },
    "ipoe-flap": {
        "status": "implemented",
        "module": "recipes.ipoe_flap",
        "timeout_setting": "default_job_timeout_seconds",
        "display_name": "IPoE Flap",
        "description": "Subscriber disconnect and reconnect cycle analysis.",
        "artifacts": ["Access PCAP", "Counters CSV", "Session timeline CSV"],
    },
    "radius-acct": {
        "status": "implementation_required",
        "module": "recipes.radius_acct",
        "timeout_setting": "default_job_timeout_seconds",
        "display_name": "RADIUS Accounting",
        "description": "RADIUS interim-accounting capture after subscriber bring-up.",
        "artifacts": ["RADIUS accounting PCAP"],
    },
}

# Public aliases are normalized before validation; the PRD's canonical ID for
# accounting is ``radius-acct``.
RECIPE_ALIASES = {
    "ipoe-acct": "radius-acct",
    "ipoe_acct": "radius-acct",
}

RECIPE_PARAMETERS = {
    "ipoe-bind": {
        "sessions": {"default": 20, "minimum": 10, "maximum": 700, "required": True},
        "offered_rate": {"default": 5, "minimum": 1, "maximum": 50, "required": False},
        "duration": {"default": 60, "minimum": 30, "maximum": 600, "required": False},
    },
    "ipoe-scale": {
        "start_sessions": {"default": 50, "minimum": 10, "maximum": 700, "required": True},
        "max_sessions": {"default": 200, "minimum": 10, "maximum": 700, "required": True},
        "cpu_limit": {"default": 80, "minimum": 50, "maximum": 95, "required": True},
    },
    "ipoe-flap": {
        "sessions": {"default": 20, "minimum": 10, "maximum": 700, "required": True},
        "cycles": {"default": 1, "minimum": 1, "maximum": 5, "required": True},
    },
    "radius-acct": {
        "sessions": {"default": 20, "minimum": 10, "maximum": 700, "required": False},
        "capture_duration": {"default": 120, "minimum": 30, "maximum": 600, "required": False},
    },
}
