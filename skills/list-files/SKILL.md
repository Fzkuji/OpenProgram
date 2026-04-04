---
name: list-files
description: "List files and folders in a directory with sizes. Use when: user asks to see directory contents, list files, show folder structure. Triggers: 'list files', 'show directory', 'what files are in', 'ls'."
---

# List Files

List files and folders in a directory.


## Usage

```python
from agentic.functions.list_files import list_files

result = list_files(path="/some/directory")
print(result)
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | `str` | `"."` | Directory path to list |

## Output

```
Contents of '/some/directory':
[DIR]  src/
[DIR]  tests/
[FILE] README.md  (8.3 KB)
[FILE] setup.py  (1.2 KB)
```
