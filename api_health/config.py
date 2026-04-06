"""Default configuration constants.

Override these by passing custom values to check constructors.
"""

# Trust Score thresholds
HEALTHY_THRESHOLD = 80
DEGRADED_THRESHOLD = 50

# Latency thresholds (milliseconds)
LATENCY_WARN_MS = 2000
LATENCY_CRIT_MS = 5000

# Default check interval (seconds)
CHECK_INTERVAL = 300  # 5 minutes

# Latency tracking
LATENCY_MAX_SAMPLES = 100
