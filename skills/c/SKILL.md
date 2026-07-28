---
name: c
description: C programming for Town Records query engine and vproj C components. Use when working on the Town Records C search engine (tr-query, libtownrecords), writing or reviewing C code, debugging memory issues with valgrind/ASAN, or optimizing search/index code. Covers the Town Records C conventions, safety patterns, and testing.
---

# C — Town Records Query Engine

The Town Records C engine (`tr-query`, `libtownrecords.a`) provides
exact-name, subject, and date lookups.  All C code lives in the
`town-records/` repository under `src/` and `include/`.

## Architecture

```
src/
├── subjects.c     # Subject index — maps subjects to document sets
├── executor.c     # Query executor — runs DSL plans
├── dsl.c          # DSL parser — structured query language
└── ...
include/           # Public headers
tests/             # Test harness (make test)
Makefile           # Build (gcc -std=c17 -Wall -Werror -O2)
```

## Safety Patterns

### Memory
- Every `malloc`/`calloc` must have a corresponding `free` reachable from the same function or its caller.
- Initialize allocated memory immediately after allocation.
- Use `calloc` when you need zero-init — it's one line instead of two.
- No VLAs (variable-length arrays) on the stack — use heap allocation for dynamic sizes.

### Buffer Safety
- `snprintf` never `sprintf` — always pass buffer size.
- `strncpy` with explicit size, then null-terminate manually.
- Every string function gets the buffer size. Never assume `strlen`.

### Pointer Safety
- Check every pointer parameter for NULL at function entry.
- `free` sets pointer to NULL after freeing (prevents double-free).
- No pointer arithmetic without bounds checking.

### Error Handling
- Return error codes from functions that can fail. Callers MUST check.
- Use `goto cleanup` pattern for error cleanup (free + return).
- `assert()` for invariants, not for runtime error handling.

## Build & Test

```bash
make           # Build libtownrecords.a + tr-query
make test      # Build and run test suite (29 tests)
make clean     # Remove build artifacts
```

```bash
# Valgrind memory check
valgrind --leak-check=full --track-origins=yes ./tr-query <test-query>

# Address sanitizer
make CFLAGS="-fsanitize=address -g" test
```

## Conventions

- C17 standard (`-std=c17`)
- `-Wall -Werror -Wextra` — all warnings are errors
- Function names: `snake_case` with subsystem prefix (`subject_`, `dsl_`, `executor_`)
- Single `.c` file per subsystem. No header-only implementations.
- `static` for file-internal functions. Only export the public API.
