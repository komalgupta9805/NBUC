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
    "ipoe-bind": {"status": "implemented", "module": "recipes.ipoe_bind"},
    "ipoe-scale": {"status": "implementation_required", "module": "recipes.ipoe_scale"},
    "ipoe-flap": {"status": "implementation_required", "module": "recipes.ipoe_flap"},
    "radius-acct": {"status": "implementation_required", "module": "recipes.radius_acct"},
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
        "start_sessions": {"default": 50, "minimum": 10, "maximum": 700, "required": False},
        "max_sessions": {"default": 200, "minimum": 10, "maximum": 700, "required": False},
        "cpu_limit": {"default": 80, "minimum": 50, "maximum": 95, "required": False},
    },
    "ipoe-flap": {
        "sessions": {"default": 20, "minimum": 10, "maximum": 700, "required": False},
        "cycles": {"default": 1, "minimum": 1, "maximum": 5, "required": False},
    },
    "radius-acct": {
        "sessions": {"default": 20, "minimum": 10, "maximum": 700, "required": False},
        "capture_duration": {"default": 120, "minimum": 30, "maximum": 600, "required": False},
    },
}
