# Document API

Generate comprehensive API documentation for Python code.

## What this does

Creates well-structured documentation for:
- Functions and methods
- Classes and modules
- API endpoints
- Type signatures
- Usage examples

## Documentation Formats

### Google Style Docstring
```python
def extract_cursor_skills(user_home: Path) -> Dict[str, List]:
    """Extract Cursor skills from user directory.

    This function scans the user's home directory for Cursor skills
    in both .cursor/skills/ and .agents/skills/ directories.

    Args:
        user_home: Path to user's home directory.

    Returns:
        Dictionary with two keys:
            - 'user_skills': List of user-level skill dictionaries
            - 'project_skills': List of project dictionaries

    Raises:
        PermissionError: If directory access is denied.
        ValueError: If user_home is not a valid directory.

    Example:
        >>> skills = extract_cursor_skills(Path.home())
        >>> print(len(skills['user_skills']))
        5
    """
    pass
```

### NumPy Style Docstring
```python
def process_data(data, threshold=0.5):
    """
    Process input data with specified threshold.

    Parameters
    ----------
    data : list of float
        Input data values to process
    threshold : float, optional
        Cutoff value for filtering (default is 0.5)

    Returns
    -------
    list of float
        Processed data values

    See Also
    --------
    filter_data : Related filtering function
    normalize_data : Data normalization utility
    """
    pass
```

### Sphinx/reStructuredText
```python
def connect_database(host, port, user, password):
    """
    Connect to database server.

    :param str host: Database hostname
    :param int port: Database port number
    :param str user: Username for authentication
    :param str password: Password for authentication
    :return: Database connection object
    :rtype: Connection
    :raises ConnectionError: If connection fails
    """
    pass
```

## Auto-generation Tools

```bash
# Generate Sphinx documentation
sphinx-quickstart
sphinx-apidoc -o docs/ scripts/

# Generate with pdoc
pdoc --html scripts/ -o docs/

# Use pydoc
python -m pydoc -w scripts.coding_discovery_tools
```

## Documentation Checklist

- [ ] Function purpose clearly stated
- [ ] All parameters documented with types
- [ ] Return value documented
- [ ] Exceptions/errors listed
- [ ] Usage example provided
- [ ] Type hints included in code
- [ ] Edge cases mentioned

## Usage

Say "document this function" or "/document-api" to generate documentation.
