---
name: rust
description: Rust systems programming for the Vex daemon and CLI. Use when working on the vex_fren Rust codebase (vex-cli/, vex_daemon/ Rust modules), writing or reviewing Rust for Vex, building CLI tools with clap, implementing daemons with tokio/axum, or doing systems programming in Rust. Covers the Vex Rust architecture, patterns, and conventions.
---

# Rust for Vex — Systems Programming

The Vex daemon and CLI are being rewritten in Rust (vex_fren branch).
The Rust codebase lives in `vex-cli/src/` and covers temporal engines,
serving endpoints, and cognitive modules.

## Architecture

```
vex-cli/src/
├── main.rs              # Entry point, CLI parsing (clap)
├── serve.rs             # HTTP server (axum), endpoint wiring
├── temporal_depth.rs    # Gravitational time model
└── (expanding)
```

## Core Patterns

### CLI with clap
- Use `#[derive(Parser)]` for CLI struct
- Subcommands for `serve`, `ask`, `diary`, etc.
- Environment variable fallbacks for API keys

### HTTP Server with axum
- `axum::Router` for route composition
- `axum::extract::State` for shared application state
- JSON responses with `axum::Json`
- Tower layers for logging, CORS, timeouts

### Error Handling
- Use `anyhow` for application errors, `thiserror` for library errors
- Never `unwrap()` in production code — use `?` or pattern match
- `tracing` crate for structured logging instead of `println!`

### Async Runtime
- `tokio` for all async I/O
- `tokio::spawn` for background tasks (heartbeat, reaper)
- `tokio::sync::RwLock` for shared mutable state

## Security Patterns

### Secrets
- API keys from env vars or config files, NEVER hardcoded
- `dotenvy` for `.env` file loading in development
- Zeroize sensitive strings after use

### File Operations
- Use `tempfile` crate for temporary files
- Set permissions explicitly — `std::fs::Permissions` or `OpenOptions`
- Clean up temp files with `Drop` or `defer!` macro

## Performance
- `--release` builds with LTO for production
- Profile with `cargo flamegraph` before optimizing
- Avoid `clone()` in hot paths — borrow or use `Arc`
