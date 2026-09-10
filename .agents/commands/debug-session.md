# Debug Session

Start an interactive debugging session for Python code.

## What this does

Provides step-by-step guidance for debugging Python code issues:

1. **Reproduce the Issue**: Verify the bug exists
2. **Add Breakpoints**: Set strategic breakpoints
3. **Inspect Variables**: Check variable states
4. **Step Through Code**: Execute line by line
5. **Identify Root Cause**: Find the actual problem
6. **Fix and Verify**: Implement solution and test

## Debugging Tools

### Python Debugger (pdb)
```python
import pdb

def buggy_function(data):
    pdb.set_trace()  # Breakpoint here
    result = process_data(data)
    return result
```

### IPython Debugger (ipdb)
```python
import ipdb

ipdb.set_trace()  # More user-friendly than pdb
```

### VS Code / Cursor Debugger
Use built-in debugger with launch.json:
```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Python: Current File",
            "type": "python",
            "request": "launch",
            "program": "${file}",
            "console": "integratedTerminal"
        }
    ]
}
```

## Common Commands

- `n` (next): Execute next line
- `s` (step): Step into function
- `c` (continue): Continue execution
- `p variable`: Print variable value
- `l` (list): Show source code
- `q` (quit): Exit debugger

## Best Practices

1. **Use logging** before reaching for debugger
2. **Add unit tests** to isolate issues
3. **Check assumptions** about data and state
4. **Simplify** the problem by removing complexity
5. **Document** the fix and root cause

## Usage

Say "start debugging" or "/debug-session" to begin guided debugging.
