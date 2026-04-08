"""Constants for the Miner Stack integration."""

DOMAIN = "stack_miners"

# Config keys — grid sensor
CONF_GRID_SENSOR = "grid_sensor_entity_id"
CONF_HYSTERESIS_W = "hysteresis_w"
CONF_ROLLING_SAMPLES = "rolling_samples"
CONF_MIN_ON_TIME = "min_on_time_s"
CONF_MIN_OFF_TIME = "min_off_time_s"

# Config keys — miner list entry
CONF_MINERS = "miners"
CONF_MINER_NAME = "name"
CONF_MINER_ENTITY_ID = "entity_id"
CONF_MINER_POWER_W = "power_w"

# Config key — simulation
CONF_SIMULATION = "simulation_enabled"

# Internal config flow key for miner priority (stripped before saving)
CONF_MINER_PRIORITY = "priority"
CONF_MINER_PRIORITY_INTERNAL = "_priority"

# Defaults
DEFAULT_HYSTERESIS_W = 100
DEFAULT_ROLLING_SAMPLES = 5
DEFAULT_MIN_ON_TIME = 60
DEFAULT_MIN_OFF_TIME = 60

# Mode strings (exposed via sensor)
MODE_IDLE = "idle"
MODE_RUNNING = "running"
