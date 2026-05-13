from interstate75 import Interstate75, DISPLAY_INTERSTATE75_64X32

# Import the matching constant above (eg. DISPLAY_INTERSTATE75_64X32) and assign it here - the import and the DISPLAY_TYPE must match.
# Note that some 64x32 panels with 1/32 scan need DISPLAY_INTERSTATE75_64X64 in order to not cut off the bottom half of the display; i75.height then reports 64 but only the top 32 rows are physically visible.
DISPLAY_TYPE = DISPLAY_INTERSTATE75_64X32
# Color order: try alternative ordering (eg. COLOR_ORDER_GRB) if colors look wrong on your panel
COLOR_ORDER  = Interstate75.COLOR_ORDER_RGB

API_URL                 = "https://wherever-the-flight-finder-service-is-deployed"
BRIGHT_MODE             = False # Set to True for brighter (higher intensity) colours
DISTANCE_UNIT           = "km" # km or mi, for display purposes only
SHOW_ALTITUDE           = False # Set to True to cycle between distance and altitude on line 2
ALTITUDE_UNIT           = "ft" # ft or m, for display purposes only (FR24 reports altitude in feet)
VALUE_SWAP_INTERVAL     = 5 # seconds between distance/altitude swaps when SHOW_ALTITUDE is True
SCROLL_ENABLED          = True # Set to False to disable marquee scrolling; long text on line 2 or line 3 will simply be cut off at the right edge
SCROLL_PAUSE_MS         = 2500 # ms to pause at start/end of each scroll cycle (line 2 and line 3)
SCROLL_SPEED_PX_PER_SEC = 10 # marquee speed when a line overflows the display
LATITUDE                = 51.5274575 # lat of display location
LONGITUDE               = -0.2595316 # lon of display location
RADIUS                  = 10 # km, for finding flights (from lat/lon)
REFRESH_INTERVAL        = 60 # seconds, best to keep this at 30s or more
USER_AGENT_ID           = "Flight Tracker 1" # ID used as part of user-agent header in requests to API, eg. "I75 Matrix Display {USER_AGENT_ID}" (useful for identifying the devices making requests)

# "quiet time" config (ie. show nothing on the display between these times)
UTC_OFFSET         = 0 # offset of your timezone from UTC (eg. for UTC+2 set to 2, for UTC-5 set to -5)
QUIET_START_HOUR   = 22
QUIET_START_MINUTE = 0
QUIET_END_HOUR     = 7
QUIET_END_MINUTE   = 0
