"""
Constants used across the AI tools discovery system
"""

# Invalid serial number values that should be ignored
INVALID_SERIAL_VALUES = [
    "TO BE FILLED BY O.E.M.",
    "DEFAULT STRING",
    "SERIALNUMBER",
    "SYSTEM SERIAL NUMBER",
    "NOT APPLICABLE",
    "N/A",
    "NONE",
    "NOT SPECIFIED",
    "OEM",
    "O.E.M.",
    "DEFAULT",
    "SYSTEM MANUFACTURER",
    "CHASSIS SERIAL NUMBER",
    "0",
    "00000000",
    "000000000000",
    "123456789",
    "XXXXXXXXXXXXXX",
    ""
]

# Command execution timeouts
COMMAND_TIMEOUT = 20
VERSION_TIMEOUT = 10

# Cursor rules extraction settings
MAX_CONFIG_FILE_SIZE = 50 * 1024  # 50KB in bytes
MAX_SEARCH_DEPTH = 10  # Maximum directory depth to search recursively
SKIP_DIRS = {'.git', 'node_modules', 'venv', '__pycache__', '.venv', 'vendor', '.idea', '.vscode'}

# System directories to skip when searching from root (macOS/Unix)
SKIP_SYSTEM_DIRS = {
    '/System', '/Library', '/private', '/usr', '/bin', '/sbin', '/opt',
    '/var', '/etc', '/tmp', '/cores', '/dev', '/home', '/net', '/Volumes',
    '/.fseventsd', '/.Spotlight-V100', '/.Trashes', '/.vol'
}

